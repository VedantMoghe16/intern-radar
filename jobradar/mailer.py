"""Free, provider-neutral SMTP delivery for the internship digest.

The module deliberately uses only the Python standard library. Missing mail
configuration is a supported state: harvesting can still complete and leave an
HTML artifact for inspection. Credentials are never included in exceptions or
result messages.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, getaddresses, make_msgid
from typing import Mapping, Sequence


_TRUE = {"1", "true", "yes", "on"}
_SECURITY_MODES = {"starttls", "ssl", "plain"}


class MailConfigurationError(ValueError):
    """Raised when SMTP settings are present but internally inconsistent."""


class MailDeliveryError(RuntimeError):
    """Raised after a configured SMTP server rejects or cannot send the mail."""


def _split_recipients(raw: str) -> tuple[str, ...]:
    if "\n" in raw or "\r" in raw:
        raise MailConfigurationError("recipient addresses must not contain newlines")
    addresses = tuple(address for _, address in getaddresses([raw]) if address)
    return addresses


@dataclass(frozen=True, slots=True)
class MailSettings:
    """SMTP settings loaded from ``JOBRADAR_*`` environment variables."""

    host: str
    port: int
    sender: str
    recipients: tuple[str, ...]
    username: str = ""
    password: str = ""
    security: str = "starttls"
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "MailSettings | None":
        values = os.environ if env is None else env
        host = values.get("JOBRADAR_SMTP_HOST", "").strip()
        sender = values.get("JOBRADAR_EMAIL_FROM", "").strip()
        recipients = _split_recipients(values.get("JOBRADAR_EMAIL_TO", ""))

        # No host means delivery was intentionally not configured. This keeps a
        # local run and a forked public repository useful without secrets.
        if not host:
            return None
        if not sender or not recipients:
            raise MailConfigurationError(
                "JOBRADAR_EMAIL_FROM and JOBRADAR_EMAIL_TO are required when "
                "JOBRADAR_SMTP_HOST is set"
            )

        security = values.get("JOBRADAR_SMTP_SECURITY", "starttls").strip().lower()
        if security not in _SECURITY_MODES:
            raise MailConfigurationError(
                "JOBRADAR_SMTP_SECURITY must be starttls, ssl, or plain"
            )

        default_port = 465 if security == "ssl" else 587
        try:
            port = int(values.get("JOBRADAR_SMTP_PORT", str(default_port)))
            timeout = float(values.get("JOBRADAR_SMTP_TIMEOUT", "30"))
        except ValueError as exc:
            raise MailConfigurationError("SMTP port and timeout must be numeric") from exc
        if not 1 <= port <= 65535 or timeout <= 0:
            raise MailConfigurationError("SMTP port or timeout is outside its valid range")

        username = values.get("JOBRADAR_SMTP_USERNAME", "").strip()
        password = values.get("JOBRADAR_SMTP_PASSWORD", "")
        if bool(username) != bool(password):
            raise MailConfigurationError(
                "JOBRADAR_SMTP_USERNAME and JOBRADAR_SMTP_PASSWORD must be set together"
            )

        return cls(
            host=host,
            port=port,
            sender=sender,
            recipients=recipients,
            username=username,
            password=password,
            security=security,
            timeout_seconds=timeout,
        )


@dataclass(frozen=True, slots=True)
class MailResult:
    """Non-secret delivery outcome suitable for logs and run reports."""

    status: str  # sent | skipped | dry-run
    detail: str
    recipients: int = 0
    message_id: str = ""

    @property
    def sent(self) -> bool:
        return self.status == "sent"


def _is_dry_run(env: Mapping[str, str] | None = None) -> bool:
    values = os.environ if env is None else env
    return values.get("JOBRADAR_MAIL_DRY_RUN", "").strip().lower() in _TRUE


def build_message(
    *,
    subject: str,
    text_body: str,
    html_body: str,
    settings: MailSettings,
    message_id: str | None = None,
) -> EmailMessage:
    """Build a multipart/alternative message without contacting the network."""

    if "\n" in subject or "\r" in subject:
        raise MailConfigurationError("email subject must not contain newlines")
    if not text_body.strip() or not html_body.strip():
        raise MailConfigurationError("both plain-text and HTML bodies are required")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.sender
    message["To"] = ", ".join(settings.recipients)
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = message_id or make_msgid(domain=None)
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    return message


def send_digest(
    *,
    subject: str,
    text_body: str,
    html_body: str,
    settings: MailSettings | None = None,
    dry_run: bool = False,
    message_id: str | None = None,
) -> MailResult:
    """Send one digest, or return a safe skip/dry-run result.

    A missing SMTP host is not an error. A configured server that fails is an
    error so callers can keep jobs pending for a later delivery attempt.
    """

    try:
        resolved = settings if settings is not None else MailSettings.from_env()
    except MailConfigurationError:
        # Email is an optional boundary. A partial/invalid secret set should be
        # visible as degraded coverage but must not discard a successful harvest.
        return MailResult("skipped", "SMTP configuration is invalid; artifact only")
    if resolved is None:
        return MailResult("skipped", "SMTP is not configured; artifact only")

    message = build_message(
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        settings=resolved,
        message_id=message_id,
    )
    msg_id = str(message["Message-ID"])
    if dry_run or _is_dry_run():
        return MailResult(
            "dry-run",
            "message rendered but not sent",
            len(resolved.recipients),
            msg_id,
        )

    try:
        if resolved.security == "ssl":
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(
                resolved.host,
                resolved.port,
                timeout=resolved.timeout_seconds,
                context=ssl.create_default_context(),
            )
        else:
            smtp = smtplib.SMTP(
                resolved.host,
                resolved.port,
                timeout=resolved.timeout_seconds,
            )

        with smtp:
            smtp.ehlo()
            if resolved.security == "starttls":
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            if resolved.username:
                smtp.login(resolved.username, resolved.password)
            smtp.send_message(message, to_addrs=list(resolved.recipients))
    except (OSError, smtplib.SMTPException) as exc:
        # Keep the message generic: some SMTP exceptions contain server text
        # that should not be copied into a public Actions log.
        raise MailDeliveryError(
            f"SMTP delivery failed ({type(exc).__name__})"
        ) from exc

    return MailResult(
        "sent",
        "SMTP server accepted the digest",
        len(resolved.recipients),
        msg_id,
    )


__all__: Sequence[str] = (
    "MailConfigurationError",
    "MailDeliveryError",
    "MailResult",
    "MailSettings",
    "build_message",
    "send_digest",
)
