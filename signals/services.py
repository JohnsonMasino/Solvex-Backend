from .models import EligibleEmail, PurchasedEmail


def is_email_eligible(email: str) -> bool:
    """True if `email` is on the admin list and the row is active (case-insensitive)."""
    email = (email or "").strip()
    if not email:
        return False
    return EligibleEmail.objects.filter(email__iexact=email, is_active=True).exists()


def has_purchased(email: str) -> bool:
    """True if `email` is on the purchased list and the row is active (case-insensitive)."""
    email = (email or "").strip()
    if not email:
        return False
    return PurchasedEmail.objects.filter(email__iexact=email, is_active=True).exists()


def get_purchased_emails():
    """Active purchased-email rows, newest first."""
    return PurchasedEmail.objects.filter(is_active=True).order_by("-created_at")