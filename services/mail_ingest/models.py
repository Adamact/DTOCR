from __future__ import annotations

from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Literal


@dataclass
class MailAttachment:
    filename: str
    safe_filename: str
    content_type: str
    data: bytes
    size_bytes: int


@dataclass
class MailMessageContext:
    uid: str
    mailbox: str
    subject: str
    sender: str
    sender_email: str
    reply_to: str
    message_id: str
    references: str
    date_header: str
    headers: dict[str, str] = field(default_factory=dict)
    attachments: list[MailAttachment] = field(default_factory=list)
    raw_email: EmailMessage | None = None

    @property
    def reply_target(self) -> str:
        return (self.reply_to or self.sender_email or "").strip()


@dataclass(frozen=True)
class ResolvedTemplate:
    template_id: int
    match_reason: str
    confidence: float | None = None


@dataclass
class AttachmentProcessingResult:
    attachment_name: str
    safe_attachment_name: str
    status: Literal["success", "failed"]
    success: bool
    template_id: int | None = None
    local_pdf_path: str = ""
    local_excel_path: str = ""
    ocr_output_path: str = ""
    excel_output_path: str = ""
    error: str = ""
    duration_seconds: float = 0.0


@dataclass
class MessageProcessingResult:
    run_id: str
    uid: str
    status: Literal["success", "failed", "partial", "skipped"] = "failed"
    subject: str = ""
    sender_email: str = ""
    sender: str = ""
    message_id: str = ""
    template_id: int | None = None
    match_reason: str = ""
    reply_sent: bool = False
    moved_to: str = ""
    skipped_reason: str = ""
    error: str = ""
    manifest_path: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0
    attachment_results: list[AttachmentProcessingResult] = field(default_factory=list)
