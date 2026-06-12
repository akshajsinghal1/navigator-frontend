import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional, Tuple

import aiosmtplib
from app.config import settings

logger = logging.getLogger(__name__)


async def _send(subject: str, to_email: str, body_html: str) -> Tuple[bool, Optional[str]]:
    if not settings.smtp_host:
        logger.warning("SMTP not configured — email not sent to %s", to_email)
        return False, "SMTP not configured"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.email_from
    msg["To"] = to_email
    msg.attach(MIMEText(body_html, "html"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_password,
            start_tls=True,
        )
        return True, None
    except Exception as exc:
        logger.error("Failed to send email to %s: %s", to_email, exc)
        return False, str(exc)


async def send_approval_email(
    admin_email: str,
    admin_name: str,
    org_name: str,
    login_url: str,
) -> Tuple[bool, Optional[str]]:
    subject = "Your organization has been approved"
    body = f"""
    <p>Hello {admin_name},</p>
    <p>Your organization <strong>{org_name}</strong> has been approved.</p>
    <p>You can now log in and access your admin panel:</p>
    <p><a href="{login_url}">{login_url}</a></p>
    <p>Thank you.</p>
    """
    return await _send(subject, admin_email, body)


async def send_rejection_email(
    admin_email: str,
    admin_name: str,
    org_name: str,
    rejection_reason: str,
) -> Tuple[bool, Optional[str]]:
    subject = "Organization request update"
    body = f"""
    <p>Hello {admin_name},</p>
    <p>Your organization request for <strong>{org_name}</strong> was not approved.</p>
    <p><strong>Reason:</strong><br>{rejection_reason}</p>
    <p>Please contact our team if you have questions.</p>
    """
    return await _send(subject, admin_email, body)
