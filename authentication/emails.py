from django.conf import settings

from core.mailer import send_email

# Security/auth emails: sent from DEFAULT_FROM_EMAIL (from_email omitted on purpose).


def send_registration_otp_email(user, otp_code):
    return send_email(
        to=user.email,
        subject="Your SolvexMarketInc verification code",
        template="authentication/otp_register.html",
        context={"user": user, "otp_code": otp_code, "expiry_minutes": settings.OTP_EXPIRY_MINUTES},
    )


def send_welcome_email(user):
    return send_email(
        to=user.email,
        subject="Welcome to SolvexMarketInc",
        template="authentication/welcome.html",
        context={"user": user},
    )


def send_password_reset_email(user, reset_url):
    return send_email(
        to=user.email,
        subject="Reset your SolvexMarketInc password",
        template="authentication/password_reset.html",
        context={
            "user": user,
            "reset_url": reset_url,
            "expiry_minutes": settings.PASSWORD_RESET_TOKEN_EXPIRY_MINUTES,
        },
    )


def send_password_changed_email(user):
    return send_email(
        to=user.email,
        subject="Your SolvexMarketInc password was changed",
        template="authentication/password_changed.html",
        context={"user": user},
    )
