from __future__ import annotations

from email.message import EmailMessage
import logging
from pathlib import Path
import smtplib
from typing import Sequence

from DTOCR.services.mail_ingest.config import SMTPConfig
from DTOCR.services.mail_ingest.models import MailMessageContext

logger = logging.getLogger(__name__)


class SMTPDeliveryError(RuntimeError):
    pass


class SMTPMailClient:
    def __init__(self, config: SMTPConfig) -> None:
        self.config = config

    def send_reply(
        self,
        message_ctx: MailMessageContext,
        subject: str,
        body: str,
        attachment_paths: Sequence[Path] | None = None,
    ) -> None:
        recipient = (message_ctx.reply_target or "").strip()
        if not recipient:
            raise SMTPDeliveryError("No reply recipient available (Reply-To/From missing).")

        msg = EmailMessage()
        msg["From"] = self.config.reply_from
        msg["To"] = recipient
        msg["Subject"] = subject
        msg["X-DTOCR-Processed"] = "1"

        message_id = (message_ctx.message_id or "").strip()
        references = (message_ctx.references or "").strip()
        if message_id:
            msg["In-Reply-To"] = message_id
            msg["References"] = f"{references} {message_id}".strip() if references else message_id
        elif references:
            msg["References"] = references

        msg.set_content(body)

        for path in attachment_paths or []:
            p = Path(path)
            if not p.exists() or not p.is_file():
                logger.warning("Reply attachment missing; skipping: %s", p)
                continue
            data = p.read_bytes()
            subtype = "octet-stream"
            if p.suffix.lower() == ".xlsx":
                subtype = "vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            msg.add_attachment(data, maintype="application", subtype=subtype, filename=p.name)

        self._send_message(msg)

    def _send_message(self, msg: EmailMessage) -> None:
        try:
            if self.config.use_ssl:
                with smtplib.SMTP_SSL(self.config.host, self.config.port, timeout=self.config.timeout_seconds) as smtp:
                    self._login_and_send(smtp, msg)
                return

            with smtplib.SMTP(self.config.host, self.config.port, timeout=self.config.timeout_seconds) as smtp:
                smtp.ehlo()
                if self.config.starttls:
                    smtp.starttls()
                    smtp.ehlo()
                self._login_and_send(smtp, msg)
        except (smtplib.SMTPException, OSError) as exc:
            raise SMTPDeliveryError(str(exc)) from exc

    def _login_and_send(self, smtp: smtplib.SMTP, msg: EmailMessage) -> None:
        if self.config.user:
            smtp.login(self.config.user, self.config.password)
        smtp.send_message(msg)
