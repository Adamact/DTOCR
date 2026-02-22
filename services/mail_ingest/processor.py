from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import time
from uuid import uuid4

from DTOCR.services.mail_ingest.config import MailIngestConfig
from DTOCR.services.mail_ingest.imap_client import IMAPMailClient
from DTOCR.services.mail_ingest.models import (
    AttachmentProcessingResult,
    MailAttachment,
    MailMessageContext,
    MessageProcessingResult,
    ResolvedTemplate,
)
from DTOCR.services.mail_ingest.router import TemplateResolver
from DTOCR.services.mail_ingest.smtp_client import SMTPMailClient
from DTOCR.services.service_factory import create_template_service  # type: ignore

logger = logging.getLogger(__name__)


class MailProcessor:
    def __init__(
        self,
        config: MailIngestConfig,
        imap_client: IMAPMailClient,
        smtp_client: SMTPMailClient,
        resolver: TemplateResolver,
        template_service=None,
    ) -> None:
        self.config = config
        self.imap = imap_client
        self.smtp = smtp_client
        self.resolver = resolver
        self.template_service = template_service or create_template_service(db_path=config.db_path)

        self.config.attachments_root.mkdir(parents=True, exist_ok=True)
        self.config.results_root.mkdir(parents=True, exist_ok=True)
        self.config.manifests_root.mkdir(parents=True, exist_ok=True)

    def validate_startup(self) -> None:
        template_ids = self.config.routing.all_template_ids()
        checked = 0
        for template_id in template_ids:
            payload = self.template_service.load_latest_template_payload(template_id=template_id)
            if not payload:
                raise RuntimeError(
                    f"Routing references template_id={template_id}, but no saved template payload was found."
                )
            checked += 1

        logger.info(
            "Mail ingestor startup validation ok: exact_routes=%s domain_routes=%s template_ids_checked=%s",
            len(self.config.routing.exact_senders),
            len(self.config.routing.sender_domains),
            checked,
        )

    def process_uid(self, uid: str) -> MessageProcessingResult:
        run_id = f"mail_{uuid4().hex[:12]}"
        started_perf = time.perf_counter()
        result = MessageProcessingResult(run_id=run_id, uid=uid, started_at=_utc_now_iso())
        message_ctx: MailMessageContext | None = None
        destination_folder = self.config.mailbox.failed_folder

        try:
            message_ctx = self.imap.fetch_message(uid)
            result.subject = message_ctx.subject
            result.sender = message_ctx.sender
            result.sender_email = message_ctx.sender_email
            result.message_id = message_ctx.message_id

            logger.info(
                "Processing email run_id=%s uid=%s sender=%s subject=%s pdf_attachments=%s",
                run_id,
                uid,
                message_ctx.sender_email or message_ctx.sender,
                message_ctx.subject or "(no subject)",
                len(message_ctx.attachments),
            )

            skip_reason = self._skip_reason(message_ctx)
            if skip_reason:
                result.status = "skipped"
                result.skipped_reason = skip_reason
                destination_folder = self.config.mailbox.processed_folder
                return self._finalize(uid, result, message_ctx, destination_folder, started_perf)

            resolved = self.resolver.resolve(message_ctx, None)
            if resolved is None:
                result.status = "failed"
                result.error = "No template route configured for sender."
                self._try_send_result_reply(message_ctx, result)
                destination_folder = self.config.mailbox.failed_folder
                return self._finalize(uid, result, message_ctx, destination_folder, started_perf)

            result.template_id = resolved.template_id
            result.match_reason = resolved.match_reason

            template_payload = self.template_service.load_latest_template_payload(template_id=resolved.template_id)
            if not template_payload:
                result.status = "failed"
                result.error = f"No saved template payload found for template_id={resolved.template_id}."
                self._try_send_result_reply(message_ctx, result)
                destination_folder = self.config.mailbox.failed_folder
                return self._finalize(uid, result, message_ctx, destination_folder, started_perf)

            attachments = self._select_attachments(message_ctx.attachments, run_id)
            if not attachments:
                if not result.error:
                    result.error = "No PDF attachments found in the email."
                result.status = "failed"
                self._try_send_result_reply(message_ctx, result)
                destination_folder = self.config.mailbox.failed_folder
                return self._finalize(uid, result, message_ctx, destination_folder, started_perf)

            for index, attachment in enumerate(attachments, start=1):
                item = self._process_attachment(run_id, template_payload, resolved, attachment, index)
                result.attachment_results.append(item)

            result.status = self._overall_status(result.attachment_results)
            self._try_send_result_reply(message_ctx, result)
            destination_folder = (
                self.config.mailbox.processed_folder
                if result.status == "success" and result.reply_sent
                else self.config.mailbox.failed_folder
            )
            return self._finalize(uid, result, message_ctx, destination_folder, started_perf)

        except Exception as exc:
            logger.exception("Unhandled mail processing error run_id=%s uid=%s: %s", run_id, uid, exc)
            result.status = "failed"
            result.error = _append_error(result.error, str(exc))

            if message_ctx is not None and not result.reply_sent and not result.skipped_reason:
                self._try_send_result_reply(message_ctx, result)

            destination_folder = self.config.mailbox.failed_folder
            return self._finalize(uid, result, message_ctx, destination_folder, started_perf)

    def _select_attachments(self, attachments: list[MailAttachment], run_id: str) -> list[MailAttachment]:
        selected = list(attachments)
        if not self.config.attachments.process_all_pdfs:
            selected = selected[:1]
        max_count = self.config.attachments.max_pdf_attachments_per_email
        if max_count > 0 and len(selected) > max_count:
            logger.info(
                "run_id=%s truncating attachments from %s to %s due to max_pdf_attachments_per_email",
                run_id,
                len(selected),
                max_count,
            )
            selected = selected[:max_count]
        return selected

    def _process_attachment(
        self,
        run_id: str,
        template_payload: dict,
        resolved: ResolvedTemplate,
        attachment: MailAttachment,
        index: int,
    ) -> AttachmentProcessingResult:
        t0 = time.perf_counter()
        local_pdf_path = self.config.attachments_root / run_id / f"{index:02d}_{attachment.safe_filename}"

        item = AttachmentProcessingResult(
            attachment_name=attachment.filename,
            safe_attachment_name=attachment.safe_filename,
            status="failed",
            success=False,
            template_id=resolved.template_id,
            local_pdf_path=str(local_pdf_path),
        )

        try:
            if attachment.size_bytes > self.config.attachments.max_pdf_size_bytes:
                raise RuntimeError(
                    f"Attachment exceeds size limit ({attachment.size_bytes} bytes > "
                    f"{self.config.attachments.max_pdf_size_bytes} bytes)."
                )

            local_pdf_path.parent.mkdir(parents=True, exist_ok=True)
            local_pdf_path.write_bytes(attachment.data)

            local_excel_path = self.config.results_root / run_id / f"{index:02d}_{_safe_stem(attachment.safe_filename)}.xlsx"
            local_excel_path.parent.mkdir(parents=True, exist_ok=True)

            logger.info(
                "Running OCR run_id=%s attachment=%s template_id=%s",
                run_id,
                attachment.safe_filename,
                resolved.template_id,
            )
            ocr_result = self.template_service.run_ocr_pipeline(
                template_payload=template_payload,
                pdf_path=str(local_pdf_path),
                excel_path=str(local_excel_path),
                word_kernel_divisor=self.config.ocr_defaults.word_kernel_divisor,
                save_crops=self.config.ocr_defaults.save_crops,
                batch_size=self.config.ocr_defaults.batch_size,
                num_beams=self.config.ocr_defaults.num_beams,
            )

            item.ocr_output_path = str(ocr_result.get("ocr_output_path", "") or "")
            item.excel_output_path = str(ocr_result.get("excel_output_path", "") or "")

            if self.config.reply.include_excel:
                excel_path = Path(item.excel_output_path) if item.excel_output_path else local_excel_path
                if not excel_path.exists():
                    raise RuntimeError("OCR completed but no Excel output was produced.")
                item.local_excel_path = str(excel_path)
            else:
                if local_excel_path.exists():
                    item.local_excel_path = str(local_excel_path)

            item.status = "success"
            item.success = True
            return item

        except Exception as exc:
            logger.exception(
                "Attachment processing failed run_id=%s attachment=%s template_id=%s: %s",
                run_id,
                attachment.safe_filename,
                resolved.template_id,
                exc,
            )
            item.status = "failed"
            item.success = False
            item.error = str(exc)
            return item

        finally:
            item.duration_seconds = round(time.perf_counter() - t0, 3)

    def _try_send_result_reply(self, message_ctx: MailMessageContext, result: MessageProcessingResult) -> None:
        subject = self._build_reply_subject(message_ctx, result)
        body = self._build_reply_body(message_ctx, result)
        attachments = self._reply_attachments(result)

        try:
            self.smtp.send_reply(message_ctx=message_ctx, subject=subject, body=body, attachment_paths=attachments)
            result.reply_sent = True
        except Exception as exc:
            logger.exception(
                "Failed to send reply run_id=%s uid=%s recipient=%s: %s",
                result.run_id,
                result.uid,
                message_ctx.reply_target,
                exc,
            )
            result.reply_sent = False
            result.error = _append_error(result.error, f"Reply send failed: {exc}")
            result.status = "failed"

    def _reply_attachments(self, result: MessageProcessingResult) -> list[Path]:
        if not self.config.reply.include_excel:
            return []
        paths: list[Path] = []
        for item in result.attachment_results:
            if not item.success or not item.local_excel_path:
                continue
            p = Path(item.local_excel_path)
            if p.exists():
                paths.append(p)
        return paths

    def _build_reply_subject(self, message_ctx: MailMessageContext, result: MessageProcessingResult) -> str:
        prefix = self.config.reply.subject_prefix.strip()
        original = (message_ctx.subject or "").strip() or "(no subject)"
        status = result.status.upper()
        return f"{prefix} {status}: {original}".strip()

    def _build_reply_body(self, message_ctx: MailMessageContext, result: MessageProcessingResult) -> str:
        lines = [
            f"DTOCR Status: {result.status.upper()}",
            f"Sender: {message_ctx.sender_email or message_ctx.sender or '<unknown>'}",
            f"Subject: {message_ctx.subject or '(no subject)'}",
            f"Run ID: {result.run_id}",
        ]
        if result.template_id is not None:
            lines.append(f"Template ID: {result.template_id}")
            if result.match_reason:
                lines.append(f"Template Match: {result.match_reason}")
        else:
            lines.append("Template ID: <not resolved>")

        if result.skipped_reason:
            lines.append(f"Skip Reason: {result.skipped_reason}")

        if result.attachment_results:
            lines.append("")
            lines.append("Attachments:")
            for item in result.attachment_results:
                status = "OK" if item.success else "FAILED"
                line = f"- {status} | {item.attachment_name}"
                if item.local_excel_path:
                    line += f" | excel={Path(item.local_excel_path).name}"
                if item.error:
                    line += f" | error={item.error}"
                lines.append(line)

        if result.error:
            lines.append("")
            lines.append(f"Error: {result.error}")

        lines.append("")
        lines.append("This reply was generated automatically by DTOCR.")
        return "\n".join(lines)

    def _skip_reason(self, message_ctx: MailMessageContext) -> str | None:
        headers = {k.lower(): (v or "") for k, v in message_ctx.headers.items()}

        x_processed = headers.get("x-dtocr-processed", "").strip().lower()
        if x_processed in {"1", "true", "yes"}:
            return "x-dtocr-processed"

        sender = (message_ctx.sender_email or "").strip().lower()
        self_addrs = {
            (self.config.smtp.reply_from or "").strip().lower(),
            (self.config.smtp.user or "").strip().lower(),
        }
        self_addrs.discard("")
        if sender and sender in self_addrs:
            return "self-message"

        auto_submitted = headers.get("auto-submitted", "").strip().lower()
        if auto_submitted and auto_submitted != "no":
            return "auto-submitted"

        precedence = headers.get("precedence", "").strip().lower()
        if precedence in {"bulk", "list", "junk"}:
            return f"precedence:{precedence}"

        if headers.get("list-id", "").strip():
            return "list-id"

        if headers.get("x-autoreply", "").strip() or headers.get("x-autorespond", "").strip():
            return "auto-reply-header"

        return None

    def _overall_status(self, items: list[AttachmentProcessingResult]) -> str:
        if not items:
            return "failed"
        success_count = sum(1 for item in items if item.success)
        if success_count == 0:
            return "failed"
        if success_count == len(items):
            return "success"
        return "partial"

    def _finalize(
        self,
        uid: str,
        result: MessageProcessingResult,
        message_ctx: MailMessageContext | None,
        destination_folder: str,
        started_perf: float,
    ) -> MessageProcessingResult:
        try:
            self.imap.move_message(uid, destination_folder)
            result.moved_to = destination_folder
        except Exception as exc:
            logger.exception(
                "Failed to move email run_id=%s uid=%s folder=%s: %s",
                result.run_id,
                uid,
                destination_folder,
                exc,
            )
            result.error = _append_error(result.error, f"Mailbox move failed: {exc}")

        result.finished_at = _utc_now_iso()
        result.duration_seconds = round(time.perf_counter() - started_perf, 3)

        manifest_path = self.config.manifests_root / f"{result.run_id}.json"
        result.manifest_path = str(manifest_path)
        self._write_manifest(manifest_path, message_ctx, result)

        logger.info(
            "Mail processing complete run_id=%s uid=%s status=%s moved_to=%s reply_sent=%s duration=%.3fs",
            result.run_id,
            uid,
            result.status,
            result.moved_to or "<not-moved>",
            result.reply_sent,
            result.duration_seconds,
        )
        return result

    def _write_manifest(self, path: Path, message_ctx: MailMessageContext | None, result: MessageProcessingResult) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": result.run_id,
            "status": result.status,
            "uid": result.uid,
            "started_at": result.started_at,
            "finished_at": result.finished_at,
            "duration_seconds": result.duration_seconds,
            "reply_sent": result.reply_sent,
            "moved_to": result.moved_to,
            "skipped_reason": result.skipped_reason,
            "error": result.error,
            "routing": {
                "template_id": result.template_id,
                "match_reason": result.match_reason,
            },
            "message": {
                "subject": result.subject,
                "sender": result.sender,
                "sender_email": result.sender_email,
                "message_id": result.message_id,
                "reply_to": message_ctx.reply_to if message_ctx else "",
                "date_header": message_ctx.date_header if message_ctx else "",
            },
            "attachments": [asdict(item) for item in result.attachment_results],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_stem(name: str) -> str:
    stem = Path(name).stem.strip() or "attachment"
    clean_chars: list[str] = []
    for ch in stem:
        clean_chars.append(ch if ch.isalnum() or ch in {"-", "_", "."} else "_")
    cleaned = "".join(clean_chars).strip("._")
    return (cleaned or "attachment")[:160]


def _append_error(existing: str, extra: str) -> str:
    extra = (extra or "").strip()
    if not extra:
        return existing
    if not existing:
        return extra
    if extra in existing:
        return existing
    return f"{existing} | {extra}"
