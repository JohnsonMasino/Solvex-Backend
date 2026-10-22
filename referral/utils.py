"""Small pure helpers for the referral app (no model / wallet imports)."""

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
CODE_LENGTH = 8


def normalize_code(code) -> str:
    """Strip + uppercase user input."""
    return (code or "").strip().upper()


def mask_name(full_name) -> str:
    """'john michael doe' -> 'John D.' ; 'john' -> 'John' ; '' -> 'User'."""
    parts = (full_name or "").split()
    if not parts:
        return "User"
    first = parts[0][:1].upper() + parts[0][1:]
    if len(parts) == 1:
        return first
    return f"{first} {parts[-1][0].upper()}."
