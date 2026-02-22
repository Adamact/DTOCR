from __future__ import annotations

from typing import Protocol

from DTOCR.services.mail_ingest.models import MailAttachment, MailMessageContext, ResolvedTemplate


class TemplateResolver(Protocol):
    def resolve(
        self,
        message_ctx: MailMessageContext,
        attachment_ctx: MailAttachment | None = None,
    ) -> ResolvedTemplate | None:
        ...


class SenderMapResolver:
    def __init__(self, exact_senders: dict[str, int] | None = None, sender_domains: dict[str, int] | None = None) -> None:
        self.exact_senders = {str(k).strip().lower(): int(v) for k, v in (exact_senders or {}).items()}
        self.sender_domains = {str(k).strip().lower().lstrip("@"): int(v) for k, v in (sender_domains or {}).items()}

    def resolve(
        self,
        message_ctx: MailMessageContext,
        attachment_ctx: MailAttachment | None = None,
    ) -> ResolvedTemplate | None:
        del attachment_ctx  # Reserved for future attachment-aware matching.

        sender_email = (message_ctx.sender_email or "").strip().lower()
        if not sender_email:
            return None

        if sender_email in self.exact_senders:
            return ResolvedTemplate(
                template_id=self.exact_senders[sender_email],
                match_reason=f"exact_sender:{sender_email}",
            )

        if "@" not in sender_email:
            return None

        domain = sender_email.split("@", 1)[1].strip().lower()
        if domain in self.sender_domains:
            return ResolvedTemplate(
                template_id=self.sender_domains[domain],
                match_reason=f"sender_domain:{domain}",
            )
        return None
