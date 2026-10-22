from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from referral import emails, services
from referral.models import Referral, ReferralProfile
from referral.tests.helpers import make_user

HAS_DEPOSIT = "wallet.services.has_completed_deposit"


class BaseApiTest(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.referrer = make_user("referrer@example.com")
        self.code = ReferralProfile.objects.get(user=self.referrer).code

    def refer(self, email, full_name="John Doe", verified=True):
        user = make_user(email, full_name=full_name, verified=verified)
        services.attach_referrer(user, self.code)
        return user


class DashboardTests(BaseApiTest):
    def test_requires_auth(self):
        res = self.client.get("/api/referrals/")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()["code"], "not_authenticated")

    def test_zeros_when_no_referrals(self):
        self.client.force_authenticate(self.referrer)
        res = self.client.get("/api/referrals/")
        self.assertEqual(res.status_code, 200)
        data = res.json()["data"]
        self.assertEqual(data["code"], self.code)
        self.assertEqual(data["stats"]["total_referred"], 0)
        self.assertEqual(data["stats"]["total_earned"], "0.00")
        self.assertEqual(data["recent"], [])

    @mock.patch(HAS_DEPOSIT, return_value=False)
    def test_numbers_and_masking(self, _):
        self.refer("john@gmail.com")
        self.refer("anna@gmail.com", full_name="Anna Smith", verified=False)
        rewarded = self.refer("paid@gmail.com", full_name="Paid Person")
        Referral.objects.filter(referred=rewarded).update(status="rewarded", bonus_amount=Decimal("3.00"))

        self.client.force_authenticate(self.referrer)
        res = self.client.get("/api/referrals/")
        stats = res.json()["data"]["stats"]
        self.assertEqual(stats["total_referred"], 3)
        self.assertEqual(stats["verified"], 2)
        self.assertEqual(stats["rewarded"], 1)
        self.assertEqual(stats["pending"], 2)
        self.assertEqual(stats["total_earned"], "3.00")
        self.assertEqual(stats["potential_earnings"], "6.00")
        body = res.content.decode()
        self.assertNotIn("john@gmail.com", body)
        self.assertNotIn("Smith", body)
        self.assertIn("j***@gmail.com", body)


class ListTests(BaseApiTest):
    def test_requires_auth(self):
        self.assertEqual(self.client.get("/api/referrals/list/").status_code, 401)

    def test_invalid_stage(self):
        self.client.force_authenticate(self.referrer)
        res = self.client.get("/api/referrals/list/?stage=abc")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["code"], "validation_error")
        self.assertIn("stage", res.json()["errors"])

    def test_stage_filtering_and_pagination(self):
        funded = self.refer("f@example.com")
        self.refer("v@example.com")
        self.refer("r@example.com", verified=False)
        self.client.force_authenticate(self.referrer)
        with mock.patch(HAS_DEPOSIT, side_effect=lambda u: u.pk == funded.pk):
            for stage, expected in (("funded", 1), ("verified", 1), ("registered", 1), ("rewarded", 0)):
                res = self.client.get(f"/api/referrals/list/?stage={stage}")
                self.assertEqual(res.status_code, 200, stage)
                self.assertEqual(res.json()["data"]["count"], expected, stage)
            res = self.client.get("/api/referrals/list/?page_size=2")
            data = res.json()["data"]
            self.assertEqual(data["count"], 3)
            self.assertEqual(len(data["results"]), 2)
            self.assertIsNotNone(data["next"])

    def test_idor_other_users_referrals_invisible(self):
        self.refer("mine@example.com")
        stranger = make_user("stranger@example.com")
        self.client.force_authenticate(stranger)
        with mock.patch(HAS_DEPOSIT, return_value=False):
            res = self.client.get("/api/referrals/list/")
        self.assertEqual(res.json()["data"]["count"], 0)


class EarningsTests(BaseApiTest):
    def test_requires_auth(self):
        self.assertEqual(self.client.get("/api/referrals/earnings/").status_code, 401)

    def test_only_rewarded_with_total(self):
        a = self.refer("a@example.com", full_name="Ann Lee")
        self.refer("b@example.com")
        Referral.objects.filter(referred=a).update(status="rewarded", bonus_amount=Decimal("3.00"))
        self.client.force_authenticate(self.referrer)
        data = self.client.get("/api/referrals/earnings/").json()["data"]
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["total_earned"], "3.00")
        self.assertEqual(data["results"][0]["masked_name"], "Ann L.")
        self.assertEqual(data["results"][0]["bonus_amount"], "3.00")


class ValidateTests(BaseApiTest):
    def test_valid_public_no_auth(self):
        res = self.client.get(f"/api/referrals/validate/?code={self.code.lower()}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["data"], {"valid": True, "referrer_name": "Test U."})

    def test_missing_and_malformed(self):
        for url in ("/api/referrals/validate/", "/api/referrals/validate/?code=", "/api/referrals/validate/?code=ab", "/api/referrals/validate/?code=bad!code"):
            res = self.client.get(url)
            self.assertEqual(res.status_code, 400, url)
            self.assertEqual(res.json()["code"], "validation_error")

    def test_unknown_code(self):
        res = self.client.get("/api/referrals/validate/?code=NOPE1234")
        self.assertEqual(res.status_code, 404)
        body = res.json()
        self.assertEqual(body["code"], "invalid_referral_code")
        self.assertEqual(body["data"], {"valid": False})

    def test_throttled(self):
        last = None
        for _ in range(12):
            last = self.client.get("/api/referrals/validate/?code=NOPE1234")
        self.assertEqual(last.status_code, 429)
        self.assertEqual(last.json()["code"], "throttled")


@override_settings(
    FRONTEND_URL="http://localhost:3000", NOTICE_FROM_EMAIL="SolvexMarketInc <notice@mg.example.com>",
    MAILGUN_DOMAIN="mg.example.com", MAILGUN_API_KEY="x", DEFAULT_FROM_EMAIL="a@b.c",
)
class EmailTests(BaseApiTest):
    def test_signup_email_uses_notice_sender_and_masked_name(self):
        referred = self.refer("john@gmail.com")
        with mock.patch("referral.emails.send_email") as send:
            emails.send_referral_signup_email(self.referrer, referred)
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs["template"], "referral/referral_signup.html")
        self.assertEqual(kwargs["from_email"], "SolvexMarketInc <notice@mg.example.com>")
        self.assertEqual(kwargs["context"]["referred_name"], "John D.")

    def test_bonus_email_context(self):
        referred = self.refer("john@gmail.com")
        Referral.objects.filter(referred=referred).update(status="rewarded", bonus_amount=Decimal("3.00"))
        referral = Referral.objects.select_related("referred").get()
        with mock.patch("referral.emails.send_email") as send:
            emails.send_referral_bonus_email(self.referrer, referral)
        ctx = send.call_args.kwargs["context"]
        self.assertEqual(ctx["total_earned"], Decimal("3.00"))
        self.assertEqual(send.call_args.kwargs["template"], "referral/referral_bonus.html")

    def test_templates_render(self):
        ctx = {"first_name": "Jane", "referred_name": "John D.", "bonus_amount": "3.00",
               "total_earned": "9.00", "dashboard_url": "http://localhost:3000/referrals",
               "BRAND_NAME": "SolvexMarketInc", "FRONTEND_URL": "http://localhost:3000"}
        for tpl in ("referral/referral_signup.html", "referral/referral_bonus.html"):
            html = render_to_string(tpl, ctx)
            self.assertIn("John D.", html)
            self.assertIn("SolvexMarketInc", html)
