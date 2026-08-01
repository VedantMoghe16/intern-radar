"""Offline contracts for provider-neutral SMTP digest delivery."""

from __future__ import annotations

import smtplib

import pytest

from jobradar.mailer import (
    MailDeliveryError,
    MailSettings,
    build_message,
    send_digest,
)


MAIL_ENV = (
    "JOBRADAR_SMTP_HOST",
    "JOBRADAR_SMTP_PORT",
    "JOBRADAR_SMTP_USERNAME",
    "JOBRADAR_SMTP_PASSWORD",
    "JOBRADAR_SMTP_SECURITY",
    "JOBRADAR_SMTP_TIMEOUT",
    "JOBRADAR_EMAIL_FROM",
    "JOBRADAR_EMAIL_TO",
    "JOBRADAR_MAIL_DRY_RUN",
)


def _clear_mail_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in MAIL_ENV:
        monkeypatch.delenv(name, raising=False)


def _settings(**overrides: object) -> MailSettings:
    values: dict[str, object] = {
        "host": "smtp.example.test",
        "port": 587,
        "sender": "radar@example.test",
        "recipients": ("candidate@example.test",),
        "username": "radar-user",
        "password": "app-password",
        "security": "starttls",
        "timeout_seconds": 4.0,
    }
    values.update(overrides)
    return MailSettings(**values)


def _send(**overrides: object):
    values: dict[str, object] = {
        "subject": "Intern Radar — 1 new role",
        "text_body": "One matching internship.",
        "html_body": "<html><body><p>One matching internship.</p></body></html>",
    }
    values.update(overrides)
    return send_digest(**values)


def test_missing_configuration_is_a_safe_artifact_only_skip(monkeypatch) -> None:
    _clear_mail_env(monkeypatch)

    result = _send()

    assert result.status == "skipped"
    assert result.sent is False
    assert result.recipients == 0
    assert "artifact only" in result.detail


def test_dry_run_builds_multipart_without_opening_smtp(monkeypatch) -> None:
    def unexpected_network(*_args, **_kwargs):
        raise AssertionError("dry-run must not create an SMTP connection")

    monkeypatch.setattr("jobradar.mailer.smtplib.SMTP", unexpected_network)
    monkeypatch.setattr("jobradar.mailer.smtplib.SMTP_SSL", unexpected_network)
    settings = _settings()

    message = build_message(
        subject="Intern Radar",
        text_body="Plain fallback",
        html_body="<strong>HTML digest</strong>",
        settings=settings,
    )
    result = _send(settings=settings, dry_run=True)

    assert message.get_content_type() == "multipart/alternative"
    assert [part.get_content_type() for part in message.iter_parts()] == [
        "text/plain",
        "text/html",
    ]
    assert result.status == "dry-run"
    assert result.recipients == 1
    assert result.message_id


def test_invalid_environment_configuration_is_a_safe_skip(monkeypatch) -> None:
    _clear_mail_env(monkeypatch)
    monkeypatch.setenv("JOBRADAR_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("JOBRADAR_SMTP_SECURITY", "invented-tls")
    monkeypatch.setenv("JOBRADAR_EMAIL_FROM", "radar@example.test")
    monkeypatch.setenv("JOBRADAR_EMAIL_TO", "candidate@example.test")

    result = _send()

    assert result.status == "skipped"
    assert result.sent is False
    assert "invalid" in result.detail


class _RecordingSMTP:
    instances: list["_RecordingSMTP"] = []

    def __init__(self, host: str, port: int, *, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.ehlo_calls = 0
        self.starttls_calls = 0
        self.login_args: tuple[str, str] | None = None
        self.message = None
        self.to_addrs: list[str] | None = None
        type(self).instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def ehlo(self) -> None:
        self.ehlo_calls += 1

    def starttls(self, *, context) -> None:
        assert context is not None
        self.starttls_calls += 1

    def login(self, username: str, password: str) -> None:
        self.login_args = (username, password)

    def send_message(self, message, *, to_addrs: list[str]) -> None:
        self.message = message
        self.to_addrs = to_addrs


def test_mocked_starttls_smtp_success_sends_multipart(monkeypatch) -> None:
    _RecordingSMTP.instances.clear()
    monkeypatch.setattr("jobradar.mailer.smtplib.SMTP", _RecordingSMTP)
    settings = _settings()

    result = _send(settings=settings)

    assert result.sent is True
    assert result.status == "sent"
    smtp = _RecordingSMTP.instances[-1]
    assert (smtp.host, smtp.port, smtp.timeout) == (
        settings.host,
        settings.port,
        settings.timeout_seconds,
    )
    assert smtp.ehlo_calls == 2
    assert smtp.starttls_calls == 1
    assert smtp.login_args == (settings.username, settings.password)
    assert smtp.to_addrs == list(settings.recipients)
    assert smtp.message.get_content_type() == "multipart/alternative"
    assert [part.get_content_type() for part in smtp.message.iter_parts()] == [
        "text/plain",
        "text/html",
    ]


class _FailingSMTP(_RecordingSMTP):
    def send_message(self, message, *, to_addrs: list[str]) -> None:
        raise smtplib.SMTPDataError(554, b"server rejected private detail")


def test_mocked_smtp_failure_is_redacted_and_raised(monkeypatch) -> None:
    _FailingSMTP.instances.clear()
    monkeypatch.setattr("jobradar.mailer.smtplib.SMTP", _FailingSMTP)

    with pytest.raises(MailDeliveryError) as captured:
        _send(settings=_settings())

    assert "SMTPDataError" in str(captured.value)
    assert "private detail" not in str(captured.value)
    assert "app-password" not in str(captured.value)

