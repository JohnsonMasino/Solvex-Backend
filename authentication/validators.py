import re

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError


def password_error_messages(password: str, user=None) -> list:
    """Return a list of human-readable problems with `password` (empty list = OK)."""
    messages = []
    if len(password) < 8:
        messages.append("Password must be at least 8 characters long.")
    if not re.search(r"[A-Za-z]", password):
        messages.append("Password must contain at least one letter.")
    if not re.search(r"\d", password):
        messages.append("Password must contain at least one digit.")
    try:
        validate_password(password, user)
    except DjangoValidationError as exc:
        messages.extend(exc.messages)
    return list(dict.fromkeys(messages))  # de-duplicate, keep order
