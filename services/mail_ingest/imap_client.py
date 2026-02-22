from __future__ import annotations

from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import parseaddr
import imaplib
import logging
import re

from DTOCR.services.mail_ingest.config import IMAPConfig, MailboxConfig
from DTOCR.services.mail_ingest.models import MailAttachment, MailMessageContext

logger = logging.getLogger(__name__)

_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


class IMAPClientError(RuntimeError):
    pass


class IMAPMailClient:
    def __init__(self, config: IMAPConfig, mailbox: MailboxConfig) -> None:
        self.config = config
        self.mailbox = mailbox
        self._imap: imaplib.IMAP4 | None = None

    def connect(self) -> None:
        self.close()
        logger.info("Connecting IMAP host=%s port=%s ssl=%s", self.config.host, self.config.port, self.config.use_ssl)

        if self.config.use_ssl:
            try:
                conn = imaplib.IMAP4_SSL(self.config.host, self.config.port, timeout=self.config.timeout_seconds)
            except TypeError:
                conn = imaplib.IMAP4_SSL(self.config.host, self.config.port)
        else:
            try:
                conn = imaplib.IMAP4(self.config.host, self.config.port, timeout=self.config.timeout_seconds)
            except TypeError:
                conn = imaplib.IMAP4(self.config.host, self.config.port)

        conn.login(self.config.user, self.config.password)
        self._imap = conn
        self.select_inbox()

    def close(self) -> None:
        if self._imap is None:
            return
        try:
            try:
                self._imap.close()
            except Exception:
                pass
            self._imap.logout()
        except Exception:
            pass
        finally:
            self._imap = None

    def noop(self) -> None:
        self._require_conn().noop()

    def select_inbox(self) -> None:
        conn = self._require_conn()
        status, data = conn.select(self.mailbox.inbox, readonly=False)
        if status != "OK":
            raise IMAPClientError(f"Failed to select inbox '{self.mailbox.inbox}': {_imap_data_to_text(data)}")

    def ensure_folders(self) -> None:
        self.ensure_folder(self.mailbox.processed_folder)
        self.ensure_folder(self.mailbox.failed_folder)
        self.select_inbox()

    def ensure_folder(self, folder_name: str) -> None:
        conn = self._require_conn()
        status, data = conn.create(folder_name)
        if status == "OK":
            logger.info("Created IMAP folder: %s", folder_name)
            return
        message = _imap_data_to_text(data).lower()
        if "exists" in message or "already" in message:
            return
        logger.info("IMAP create folder status=%s folder=%s msg=%s", status, folder_name, message)

    def search_unseen_uids(self, limit: int) -> list[str]:
        conn = self._require_conn()
        self.select_inbox()
        criterion = (self.mailbox.search_criterion or "UNSEEN").strip()
        parts = tuple(part for part in criterion.split() if part) or ("UNSEEN",)
        status, data = conn.uid("SEARCH", None, *parts)
        if status != "OK":
            raise IMAPClientError(f"IMAP SEARCH failed: criterion={criterion!r} msg={_imap_data_to_text(data)}")

        raw = b" ".join(item for item in data if isinstance(item, (bytes, bytearray)))
        uids = [token.decode("utf-8", errors="ignore") for token in raw.split() if token]
        return uids[: max(0, int(limit))]

    def fetch_message(self, uid: str) -> MailMessageContext:
        conn = self._require_conn()
        self.select_inbox()
        status, data = conn.uid("FETCH", uid, "(RFC822)")
        if status != "OK":
            raise IMAPClientError(f"IMAP FETCH failed for uid={uid}: {_imap_data_to_text(data)}")

        raw_bytes = _extract_rfc822_bytes(data)
        if raw_bytes is None:
            raise IMAPClientError(f"IMAP FETCH returned no RFC822 payload for uid={uid}")

        email_obj = message_from_bytes(raw_bytes, policy=policy.default)

        from_header = str(email_obj.get("From", "") or "")
        _sender_name, sender_email = parseaddr(from_header)
        reply_to_header = str(email_obj.get("Reply-To", "") or "")
        _reply_name, reply_to_email = parseaddr(reply_to_header)

        headers = {str(k).lower(): str(v) for k, v in email_obj.items()}
        attachments = self._extract_pdf_attachments(email_obj)

        return MailMessageContext(
            uid=str(uid),
            mailbox=self.mailbox.inbox,
            subject=str(email_obj.get("Subject", "") or ""),
            sender=from_header,
            sender_email=(sender_email or "").strip(),
            reply_to=(reply_to_email or "").strip(),
            message_id=str(email_obj.get("Message-ID", "") or ""),
            references=str(email_obj.get("References", "") or ""),
            date_header=str(email_obj.get("Date", "") or ""),
            headers=headers,
            attachments=attachments,
            raw_email=email_obj if isinstance(email_obj, EmailMessage) else None,
        )

    def move_message(self, uid: str, destination_folder: str) -> None:
        conn = self._require_conn()
        self.select_inbox()

        status, data = conn.uid("MOVE", uid, _quote_mailbox(destination_folder))
        if status == "OK":
            return

        logger.info(
            "IMAP MOVE failed/unsupported uid=%s folder=%s msg=%s; using COPY+DELETE",
            uid,
            destination_folder,
            _imap_data_to_text(data),
        )

        copy_status, copy_data = conn.uid("COPY", uid, _quote_mailbox(destination_folder))
        if copy_status != "OK":
            raise IMAPClientError(f"IMAP COPY failed for uid={uid}: {_imap_data_to_text(copy_data)}")
        store_status, store_data = conn.uid("STORE", uid, "+FLAGS.SILENT", "(\\Deleted)")
        if store_status != "OK":
            raise IMAPClientError(f"IMAP STORE failed for uid={uid}: {_imap_data_to_text(store_data)}")
        expunge_status, expunge_data = conn.expunge()
        if expunge_status != "OK":
            raise IMAPClientError(f"IMAP EXPUNGE failed: {_imap_data_to_text(expunge_data)}")

    def _require_conn(self) -> imaplib.IMAP4:
        if self._imap is None:
            raise IMAPClientError("IMAP client is not connected.")
        return self._imap

    @staticmethod
    def _extract_pdf_attachments(email_obj: EmailMessage) -> list[MailAttachment]:
        attachments: list[MailAttachment] = []
        used_names: set[str] = set()
        unnamed_counter = 0

        for part in email_obj.iter_attachments():
            filename = str(part.get_filename() or "").strip()
            content_type = str(part.get_content_type() or "").lower()

            is_pdf = content_type == "application/pdf" or filename.lower().endswith(".pdf")
            if not is_pdf:
                continue

            payload = part.get_payload(decode=True) or b""
            unnamed_counter += 1
            original_name = filename or f"attachment_{unnamed_counter}.pdf"

            safe_name = _sanitize_filename(original_name)
            if not safe_name.lower().endswith(".pdf"):
                safe_name = f"{safe_name}.pdf"
            safe_name = _dedupe_name(safe_name, used_names)
            used_names.add(safe_name)

            attachments.append(
                MailAttachment(
                    filename=original_name,
                    safe_filename=safe_name,
                    content_type=content_type or "application/pdf",
                    data=payload,
                    size_bytes=len(payload),
                )
            )

        return attachments


def _extract_rfc822_bytes(data: object) -> bytes | None:
    if not isinstance(data, list):
        return None
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return None


def _imap_data_to_text(data: object) -> str:
    if isinstance(data, (bytes, bytearray)):
        return bytes(data).decode("utf-8", errors="ignore")
    if isinstance(data, list):
        return " | ".join(_imap_data_to_text(item) for item in data)
    if isinstance(data, tuple):
        return " | ".join(_imap_data_to_text(item) for item in data)
    return str(data)


def _quote_mailbox(mailbox: str) -> str:
    escaped = mailbox.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _sanitize_filename(name: str) -> str:
    cleaned = _FILENAME_SAFE_RE.sub("_", name.strip()).strip("._")
    return (cleaned or "attachment")[:180]


def _dedupe_name(name: str, used_names: set[str]) -> str:
    if name not in used_names:
        return name

    if "." in name:
        stem, suffix = name.rsplit(".", 1)
        suffix = f".{suffix}"
    else:
        stem, suffix = name, ""

    i = 2
    while True:
        candidate = f"{stem}_{i}{suffix}"
        if candidate not in used_names:
            return candidate
        i += 1
