import inspect
from decimal import Decimal
from unittest import mock

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from referral import services
from referral.models import Referral, ReferralProfile
from referral.tests.helpers import make_user
from referral.utils import CODE_ALPHABET, mask_name

HAS_DEPOSIT = "wallet.services.has_completed_deposit"
CREDIT = "wallet.services.credit_wallet"


class CodeTests(TestCase):
    def test_profile_auto_created_with_valid_code(self):
        user = make_user("a@example.com")
        profile = ReferralProfile.objects.get(user=user)
        self.assertEqual(len(profile.code), 8)
        self.assertTrue(set(profile.code) <= set(CODE_ALPHABET))

    def test_codes_are_unique(self):
        users = [make_user(f"u{i}@example.com") for i in range(5)]
        codes = {ReferralProfile.objects.get(user=u).code for u in users}
        self.assertEqual(len(codes), 5)

    def test_get_or_create_is_idempotent(self):
        user = make_user("a@example.com")
        self.assertEqual(
            services.get_or_create_referral_profile(user).pk,
            services.get_or_create_referral_profile(user).pk,
        )

    def test_collision_retries(self):
        owner = make_user("owner@example.com")
        taken = ReferralProfile.objects.get(user=owner).code
        newbie = make_user("new@example.com")
        ReferralProfile.objects.filter(user=newbie).delete()
        with mock.patch("referral.services._random_code", side_effect=[taken, taken, "ZZZZ2222"]):
            profile = services.get_or_create_referral_profile(newbie)
        self.assertEqual(profile.code, "ZZZZ2222")

    def test_collision_gives_up_after_10_attempts(self):
        owner = make_user("owner@example.com")
        taken = ReferralProfile.objects.get(user=owner).code
        newbie = make_user("new@example.com")
        ReferralProfile.objects.filter(user=newbie).delete()
        with mock.patch("referral.services._random_code", return_value=taken) as gen:
            with self.assertRaises(RuntimeError):
                services.get_or_create_referral_profile(newbie)
        self.assertEqual(gen.call_count, 10)

    @override_settings(FRONTEND_URL="http://localhost:3000/", FRONTEND_SIGNUP_PATH="/signup")
    def test_build_link(self):
        self.assertEqual(services.build_referral_link("A8K2QZ7M"), "http://localhost:3000/signup?ref=A8K2QZ7M")


class ValidateCodeTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com")
        self.code = ReferralProfile.objects.get(user=self.owner).code

    def test_case_insensitive_and_stripped(self):
        self.assertEqual(services.validate_code(f"  {self.code.lower()} "), self.owner)

    def test_invalid_and_blank(self):
        self.assertIsNone(services.validate_code("NOPE1234"))
        self.assertIsNone(services.validate_code(""))
        self.assertIsNone(services.validate_code(None))

    def test_inactive_owner(self):
        type(self.owner).objects.filter(pk=self.owner.pk).update(is_active=False)
        self.assertIsNone(services.validate_code(self.code))


class AttachReferrerTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com")
        self.code = ReferralProfile.objects.get(user=self.owner).code
        self.new = make_user("new@example.com", verified=False)

    def test_ok(self):
        services.attach_referrer(self.new, self.code.lower())
        ref = Referral.objects.get(referred=self.new)
        self.assertEqual(ref.referrer, self.owner)
        self.assertEqual(ref.status, "pending")
        self.assertEqual(ref.code_used, self.code)

    def test_self_referral_ignored(self):
        services.attach_referrer(self.owner, self.code)
        self.assertEqual(Referral.objects.count(), 0)

    def test_duplicate_is_idempotent_and_keeps_first_referrer(self):
        other = make_user("other@example.com")
        other_code = ReferralProfile.objects.get(user=other).code
        services.attach_referrer(self.new, self.code)
        services.attach_referrer(self.new, self.code)
        services.attach_referrer(self.new, other_code)
        self.assertEqual(Referral.objects.filter(referred=self.new).count(), 1)
        self.assertEqual(Referral.objects.get(referred=self.new).referrer, self.owner)

    def test_invalid_code_ignored(self):
        services.attach_referrer(self.new, "BADCODE9")
        self.assertEqual(Referral.objects.count(), 0)

    def test_inactive_owner_ignored(self):
        type(self.owner).objects.filter(pk=self.owner.pk).update(is_active=False)
        services.attach_referrer(self.new, self.code)
        self.assertEqual(Referral.objects.count(), 0)

    def test_db_blocks_self_referral(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Referral.objects.create(referrer=self.owner, referred=self.owner, code_used="X")


class OnUserVerifiedTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com")
        self.new = make_user("new@example.com", verified=True)

    def test_sets_verified_at_once_and_emails_once(self):
        services.attach_referrer(self.new, ReferralProfile.objects.get(user=self.owner).code)
        with mock.patch("referral.emails.send_referral_signup_email") as send:
            with self.captureOnCommitCallbacks(execute=True):
                services.on_user_verified(self.new)
            first = Referral.objects.get(referred=self.new).verified_at
            self.assertIsNotNone(first)
            with self.captureOnCommitCallbacks(execute=True):
                services.on_user_verified(self.new)
        self.assertEqual(Referral.objects.get(referred=self.new).verified_at, first)
        send.assert_called_once()

    def test_noop_without_referral(self):
        with mock.patch("referral.emails.send_referral_signup_email") as send:
            with self.captureOnCommitCallbacks(execute=True):
                services.on_user_verified(self.new)
        send.assert_not_called()


class PayoutTests(TestCase):
    def setUp(self):
        self.referrer = make_user("referrer@example.com")
        self.referred = make_user("referred@example.com", full_name="John Doe")
        services.attach_referrer(self.referred, ReferralProfile.objects.get(user=self.referrer).code)

    def _run(self):
        with self.captureOnCommitCallbacks(execute=True):
            services.process_first_trade_completion(self.referred)

    def test_no_referral_is_noop(self):
        stranger = make_user("stranger@example.com")
        with mock.patch(CREDIT) as credit:
            services.process_first_trade_completion(stranger)
        credit.assert_not_called()

    @mock.patch(HAS_DEPOSIT, return_value=False)
    def test_no_deposit_no_pay(self, _):
        with mock.patch(CREDIT) as credit:
            self._run()
        credit.assert_not_called()
        self.assertEqual(Referral.objects.get().status, "pending")

    @mock.patch(HAS_DEPOSIT, return_value=True)
    def test_pays_exactly_once_with_ledger_args(self, _):
        with mock.patch(CREDIT) as credit, mock.patch("referral.emails.send_referral_bonus_email") as send:
            self._run()
            self._run()
        credit.assert_called_once_with(
            self.referrer, Decimal("3.00"), "referral_bonus",
            reference=f"referral:{Referral.objects.get().id}", description="Referral bonus",
        )
        send.assert_called_once()
        ref = Referral.objects.get()
        self.assertEqual(ref.status, "rewarded")
        self.assertEqual(ref.bonus_amount, Decimal("3.00"))
        self.assertIsNotNone(ref.rewarded_at)
        self.assertEqual(ref.wallet_reference, f"referral:{ref.id}")

    @mock.patch(HAS_DEPOSIT, return_value=True)
    def test_referrer_inactive_then_success_later(self, _):
        User = type(self.referrer)
        User.objects.filter(pk=self.referrer.pk).update(is_active=False)
        with mock.patch(CREDIT) as credit:
            self._run()
            credit.assert_not_called()
            User.objects.filter(pk=self.referrer.pk).update(is_active=True)
            self._run()
            credit.assert_called_once()

    @mock.patch(HAS_DEPOSIT, return_value=True)
    def test_unverified_referred_not_paid(self, _):
        type(self.referred).objects.filter(pk=self.referred.pk).update(is_email_verified=False)
        with mock.patch(CREDIT) as credit:
            self._run()
        credit.assert_not_called()

    @mock.patch(HAS_DEPOSIT, return_value=True)
    def test_amount_frozen_after_payout(self, _):
        with mock.patch(CREDIT):
            self._run()
        with override_settings(REFERRAL_BONUS_USD=Decimal("10.00")):
            self._run()
        self.assertEqual(Referral.objects.get().bonus_amount, Decimal("3.00"))


class StageAndMaskTests(TestCase):
    def test_mask_name(self):
        self.assertEqual(mask_name("john michael doe"), "John D.")
        self.assertEqual(mask_name("john"), "John")
        self.assertEqual(mask_name(""), "User")

    def test_stage_progression(self):
        referrer = make_user("r@example.com")
        referred = make_user("d@example.com", verified=False)
        services.attach_referrer(referred, ReferralProfile.objects.get(user=referrer).code)
        ref = Referral.objects.get()
        self.assertEqual(services.get_stage(ref), "registered")

        type(referred).objects.filter(pk=referred.pk).update(is_email_verified=True)
        ref = Referral.objects.select_related("referred").get()
        with mock.patch(HAS_DEPOSIT, return_value=False):
            self.assertEqual(services.get_stage(ref), "verified")
        ref = Referral.objects.select_related("referred").get()
        with mock.patch(HAS_DEPOSIT, return_value=True):
            self.assertEqual(services.get_stage(ref), "funded")

    def test_no_wallet_import_at_module_level(self):
        for name in ("referral.services", "referral.emails", "referral.models"):
            module = __import__(name, fromlist=["x"])
            for line in inspect.getsource(module).splitlines():
                self.assertFalse(
                    line.startswith(("from wallet", "import wallet")),
                    f"{name} imports wallet at module level",
                )
