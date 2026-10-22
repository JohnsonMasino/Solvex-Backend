"""
core/mailer.py
Single entry-point for all outbound email in SolvexMarketInc.
Uses Mailgun's HTTP Messages API directly (no Django email backend / SMTP).

    from core.mailer import send_email
    send_email(to="user@example.com", subject="Hello",
               template="authentication/otp_register.html",
               context={"otp_code": "123456", "expiry_minutes": 10})
"""
import logging
import re

import requests
from django.conf import settings
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(style|head|script).*?</\1>", "", html)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</tr>|</h\d>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def send_email(to, subject: str, template: str, context: dict = None, from_email: str = None) -> bool:
    """
    Render an HTML email template and send it via the Mailgun HTTP API.
    Returns True on success, False on failure (errors are logged, never raised).
    """
    context = dict(context or {})
    context.setdefault("FRONTEND_URL", settings.FRONTEND_URL)
    context.setdefault("BRAND_NAME", "SolvexMarketInc")

    try:
        html_body = render_to_string(template, context)
    except Exception as exc:
        logger.error("mailer: template render failed (%s): %s", template, exc)
        return False

    text_body = _html_to_text(html_body)
    recipients = [to] if isinstance(to, str) else list(to)

    api_key = getattr(settings, "MAILGUN_API_KEY", "")
    domain = getattr(settings, "MAILGUN_DOMAIN", "")
    if not api_key or not domain:
        if settings.DEBUG:
            # Local dev without Mailgun: log recipient + subject (OTP only in DEBUG).
            logger.info("mailer[DEBUG, no Mailgun keys]: '%s' -> %s", subject, recipients)
            if context.get("otp_code"):
                logger.info("mailer[DEBUG]: OTP for %s is %s", recipients, context["otp_code"])
            return True
        logger.error("mailer: MAILGUN_API_KEY / MAILGUN_DOMAIN not configured; email not sent.")
        return False

    sender = from_email or settings.DEFAULT_FROM_EMAIL
    payload = {"from": sender, "to": recipients, "subject": subject, "text": text_body, "html": html_body}

    try:
        response = requests.post(
            f"https://api.mailgun.net/v3/{domain}/messages",
            auth=("api", api_key),
            data=payload,
            timeout=10,
        )
        if response.status_code == 200:
            logger.info('mailer: sent "%s" -> %s', subject, recipients)
            return True
        logger.error(
            'mailer: Mailgun returned %s for "%s" -> %s: %s',
            response.status_code, subject, recipients, response.text,
        )
        return False
    except requests.RequestException as exc:
        logger.error('mailer: request failed for "%s": %s', subject, exc)
        return False
