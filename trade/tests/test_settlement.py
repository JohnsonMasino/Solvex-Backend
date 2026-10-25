from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.exceptions import BusinessError
from trade import services
from trade.models import Trade
from wallet import services as wallet_services

User = get_user_model()
OPEN_DATA = {"pair": "BTC/USDT", "side": "buy", "amount": Decimal("100.00"), "leverage": 10, "duration_hours": 6}


def make_user(email="u@example.com", funded="1000.00"):
    user = User.objects.create_user(email=email, password="StrongPass123", full_name="Test User")
    User.objects.filter(pk=user.pk).update(is_email_verified=True, tier=2, kyc_status="approved")
    user.refresh_from_db()
    wallet_services.credit_wallet(user, Decimal(funded), "adjustment", reference=f"test:{user.pk}")
    return user


class SettlementTests(TestCase):
    def setUp(self):
        p = mock.patch("trade.services.is_weekend", return_value=False)
        p.start()
        self.addCleanup(p.stop)
        self.user = make_user()
        self.admin = User.objects.create_superuser(email="a@example.com", password="StrongPass123", full_name="Admin")

    def _open(self):
        with self.captureOnCommitCallbacks(execute=False):
            trade, created = services.open_trade(self.user, dict(OPEN_DATA))
        self.assertTrue(created)
        return trade

    def test_open_has_no_result_and_locks_funds(self):
        trade = self._open()
        self.assertEqual(trade.status, "open")
        self.assertIsNone(trade.outcome)
        self.assertIsNone(trade.pnl_amount)
        b = wallet_services.get_balances(self.user)
        self.assertEqual(b["locked_balance"], Decimal("100.00"))

    @mock.patch("trade.emails.send_admin_new_trade_email")
    @mock.patch("trade.emails.send_trade_opened_email")
    def test_open_queues_user_and_admin_emails_on_commit(self, user_mail, admin_mail):
        with self.captureOnCommitCallbacks(execute=True):
            services.open_trade(self.user, dict(OPEN_DATA))
        user_mail.assert_called_once()
        admin_mail.assert_called_once()

    @override_settings(ADMIN_NOTIFICATION_EMAIL="staff@example.com", BACKEND_URL="https://api.example.com")
    @mock.patch("trade.emails.send_email")
    def test_admin_email_link(self, send):
        from trade.emails import send_admin_new_trade_email

        trade = self._open()
        send_admin_new_trade_email(trade)
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs["to"], "staff@example.com")
        self.assertEqual(
            kwargs["context"]["admin_url"], f"https://api.example.com/admin/trade/trade/{trade.id}/change/"
        )

    @override_settings(ADMIN_NOTIFICATION_EMAIL="")
    @mock.patch("trade.emails.send_email")
    def test_no_admin_email_when_unset(self, send):
        from trade.emails import send_admin_new_trade_email

        self.assertFalse(send_admin_new_trade_email(self._open()))
        send.assert_not_called()

    def test_profit_settlement(self):
        trade = self._open()
        with mock.patch("referral.services.process_first_trade_completion"), self.captureOnCommitCallbacks(execute=True):
            result = services.settle_trade_by_admin(trade.id, self.admin, "profit", Decimal("12.50"), "ok")
        self.assertEqual(result.status, "closed")
        self.assertEqual(result.outcome, "profit")
        self.assertEqual(result.pnl_amount, Decimal("12.50"))
        self.assertEqual(result.pnl_pct, Decimal("12.50"))
        self.assertEqual(result.settled_by, self.admin)
        self.assertEqual(result.close_reason, "admin")
        b = wallet_services.get_balances(self.user)
        self.assertEqual(b["balance"], Decimal("1012.50"))
        self.assertEqual(b["locked_balance"], Decimal("0.00"))

    def test_loss_settlement(self):
        trade = self._open()
        with mock.patch("referral.services.process_first_trade_completion"), self.captureOnCommitCallbacks(execute=True):
            result = services.settle_trade_by_admin(trade.id, self.admin, "loss", Decimal("40.00"))
        self.assertEqual(result.pnl_amount, Decimal("-40.00"))
        self.assertEqual(result.pnl_pct, Decimal("-40.00"))
        b = wallet_services.get_balances(self.user)
        self.assertEqual(b["balance"], Decimal("960.00"))
        self.assertEqual(b["locked_balance"], Decimal("0.00"))

    def test_loss_larger_than_amount_rejected(self):
        trade = self._open()
        with self.assertRaises(BusinessError):
            services.settle_trade_by_admin(trade.id, self.admin, "loss", Decimal("100.01"))
        trade.refresh_from_db()
        self.assertEqual(trade.status, "open")
        self.assertEqual(wallet_services.get_balances(self.user)["locked_balance"], Decimal("100.00"))

    def test_invalid_amounts_and_profit_cap(self):
        trade = self._open()
        for bad in (Decimal("0"), Decimal("-5")):
            with self.assertRaises(BusinessError):
                services.settle_trade_by_admin(trade.id, self.admin, "profit", bad)
        with self.assertRaises(BusinessError):
            services.settle_trade_by_admin(trade.id, self.admin, "profit", Decimal("1000000.01"))
        with self.assertRaises(BusinessError):
            services.settle_trade_by_admin(trade.id, self.admin, "", Decimal("5"))

    def test_double_settle_is_noop(self):
        trade = self._open()
        with mock.patch("referral.services.process_first_trade_completion"), self.captureOnCommitCallbacks(execute=True):
            first = services.settle_trade_by_admin(trade.id, self.admin, "profit", Decimal("10.00"))
            second = services.settle_trade_by_admin(trade.id, self.admin, "profit", Decimal("10.00"))
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(wallet_services.get_balances(self.user)["balance"], Decimal("1010.00"))

    def test_referral_exception_is_swallowed(self):
        trade = self._open()
        with mock.patch("referral.services.process_first_trade_completion", side_effect=RuntimeError("x")), \
                self.captureOnCommitCallbacks(execute=True):
            result = services.settle_trade_by_admin(trade.id, self.admin, "profit", Decimal("5.00"))
        self.assertEqual(result.status, "closed")

    def test_rejected_open_does_not_consume_sequence(self):
        with self.assertRaises(BusinessError):
            services.open_trade(self.user, {**OPEN_DATA, "amount": Decimal("999999.00")})
        trade = self._open()
        self.assertEqual(trade.sequence_number, 1)

    def test_legacy_closed_trade_cannot_be_resettled(self):
        trade = self._open()
        Trade.objects.filter(pk=trade.pk).update(
            status="closed", outcome="profit", pnl_amount=Decimal("1.00"), close_reason="expiry"
        )
        self.assertIsNone(services.settle_trade_by_admin(trade.id, self.admin, "profit", Decimal("5.00")))


class ApiAndAdminTests(TestCase):
    def setUp(self):
        p = mock.patch("trade.services.is_weekend", return_value=False)
        p.start()
        self.addCleanup(p.stop)
        self.user = make_user()
        self.admin = User.objects.create_superuser(email="a@example.com", password="StrongPass123", full_name="Admin")
        self.trade, _ = services.open_trade(self.user, dict(OPEN_DATA))

    def _api_login(self):
        resp = self.client.post(
            "/api/auth/login/", {"email": self.user.email, "password": "StrongPass123"}, content_type="application/json"
        )
        return {"HTTP_AUTHORIZATION": f"Bearer {resp.json()['data']['access']}"}

    def test_open_trade_fields_hidden_and_awaiting_flag(self):
        headers = self._api_login()
        data = self.client.get(f"/api/trades/{self.trade.id}/", **headers).json()["data"]
        self.assertIsNone(data["outcome"])
        self.assertIsNone(data["pnl_amount"])
        self.assertFalse(data["awaiting_settlement"])
        for hidden in ("admin_note", "settled_by", "planned_outcome", "planned_pnl_pct", "sequence_number"):
            self.assertNotIn(hidden, data)
        Trade.objects.filter(pk=self.trade.pk).update(scheduled_close_at="2000-01-01T00:00:00Z")
        data = self.client.get(f"/api/trades/{self.trade.id}/", **headers).json()["data"]
        self.assertTrue(data["awaiting_settlement"])

    def test_old_cron_urls_are_gone(self):
        self.assertEqual(self.client.post("/api/cron/trades/close-expired/").status_code, 404)
        self.assertEqual(self.client.post("/api/cron/trades/close-weekend/").status_code, 404)

    def test_admin_settles_trade_via_form(self):
        self.client.force_login(self.admin)
        url = reverse("admin:trade_trade_change", args=[self.trade.pk])
        with mock.patch("referral.services.process_first_trade_completion"), self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(url, {"settle_outcome": "profit", "settle_amount": "25.00", "admin_note": "n"})
        self.assertEqual(resp.status_code, 302)
        self.trade.refresh_from_db()
        self.assertEqual(self.trade.status, "closed")
        self.assertEqual(self.trade.pnl_amount, Decimal("25.00"))
        self.assertEqual(wallet_services.get_balances(self.user)["balance"], Decimal("1025.00"))

    def test_admin_validation_error_saves_nothing(self):
        self.client.force_login(self.admin)
        url = reverse("admin:trade_trade_change", args=[self.trade.pk])
        resp = self.client.post(url, {"settle_outcome": "loss", "settle_amount": "500.00"})
        self.assertEqual(resp.status_code, 200)  # form re-rendered with errors
        self.trade.refresh_from_db()
        self.assertEqual(self.trade.status, "open")

    def test_admin_permissions(self):
        from trade.admin import TradeAdmin
        from django.contrib import admin as dj_admin

        ma = TradeAdmin(Trade, dj_admin.site)
        request = mock.Mock(user=self.admin)
        self.assertFalse(ma.has_add_permission(request))
        self.assertFalse(ma.has_delete_permission(request))