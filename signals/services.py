from .models import EligibleEmail


def is_email_eligible(email: str) -> bool:
    """True if `email` is on the admin list and the row is active (case-insensitive)."""
    email = (email or "").strip()
    if not email:
        return False
    return EligibleEmail.objects.filter(email__iexact=email, is_active=True).exists()