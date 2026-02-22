from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception as exc:  # pragma: no cover
    yaml = None  # type: ignore[assignment]
    _yaml_import_error = exc
else:
    _yaml_import_error = None


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class PollConfig:
    interval_seconds: int = 30
    max_messages_per_cycle: int = 10
    reconnect_backoff_seconds: tuple[int, ...] = (5, 15, 30, 60, 300)


@dataclass(frozen=True)
class MailboxConfig:
    inbox: str
    processed_folder: str
    failed_folder: str
    search_criterion: str = "UNSEEN"


@dataclass(frozen=True)
class RoutingConfig:
    exact_senders: dict[str, int]
    sender_domains: dict[str, int]

    def all_template_ids(self) -> list[int]:
        ids = set(self.exact_senders.values()) | set(self.sender_domains.values())
        return sorted(ids)


@dataclass(frozen=True)
class OCRDefaultsConfig:
    batch_size: int = 8
    num_beams: int = 1
    word_kernel_divisor: int | None = None
    save_crops: bool = True


@dataclass(frozen=True)
class AttachmentPolicyConfig:
    process_all_pdfs: bool = True
    max_pdf_attachments_per_email: int = 10
    max_pdf_size_mb: int = 25

    @property
    def max_pdf_size_bytes(self) -> int:
        return int(self.max_pdf_size_mb) * 1024 * 1024


@dataclass(frozen=True)
class ReplyConfig:
    subject_prefix: str = "[DTOCR]"
    include_excel: bool = True
    include_json: bool = False


@dataclass(frozen=True)
class IMAPConfig:
    host: str
    port: int
    user: str
    password: str
    use_ssl: bool
    timeout_seconds: int = 30


@dataclass(frozen=True)
class SMTPConfig:
    host: str
    port: int
    user: str
    password: str
    starttls: bool
    reply_from: str
    timeout_seconds: int = 30
    use_ssl: bool = False


@dataclass(frozen=True)
class MailIngestConfig:
    config_path: Path
    project_root: Path
    db_path: Path
    poll: PollConfig
    mailbox: MailboxConfig
    routing: RoutingConfig
    ocr_defaults: OCRDefaultsConfig
    attachments: AttachmentPolicyConfig
    reply: ReplyConfig
    imap: IMAPConfig
    smtp: SMTPConfig

    @property
    def storage_mail_root(self) -> Path:
        return self.project_root / "storage" / "mail"

    @property
    def attachments_root(self) -> Path:
        return self.storage_mail_root / "attachments"

    @property
    def results_root(self) -> Path:
        return self.storage_mail_root / "results"

    @property
    def manifests_root(self) -> Path:
        return self.storage_mail_root / "manifests"


def load_mail_ingest_config(path: str | Path) -> MailIngestConfig:
    if yaml is None:
        raise ConfigError(f"PyYAML is required for mail ingestor config: {_yaml_import_error}")

    config_path = Path(path).expanduser().resolve()
    if not config_path.exists():
        raise ConfigError(
            f"Mail ingestor config not found: {config_path}. "
            "Create it from config/mail_ingestor.example.yaml."
        )

    project_root = Path(__file__).resolve().parents[2]
    raw = _load_yaml_mapping(config_path)

    poll_raw = _mapping(raw.get("poll"), "poll")
    mailbox_raw = _mapping(raw.get("mailbox"), "mailbox")
    routing_raw = _mapping(raw.get("routing"), "routing")
    ocr_raw = _mapping(raw.get("ocr_defaults"), "ocr_defaults")
    attachments_raw = _mapping(raw.get("attachments"), "attachments")
    reply_raw = _mapping(raw.get("reply"), "reply")

    poll = PollConfig(
        interval_seconds=_int_value(poll_raw.get("interval_seconds", 30), "poll.interval_seconds", minimum=1),
        max_messages_per_cycle=_int_value(
            poll_raw.get("max_messages_per_cycle", 10),
            "poll.max_messages_per_cycle",
            minimum=1,
        ),
        reconnect_backoff_seconds=tuple(
            _int_list(
                poll_raw.get("reconnect_backoff_seconds", [5, 15, 30, 60, 300]),
                "poll.reconnect_backoff_seconds",
                minimum=1,
            )
        ),
    )

    mailbox = MailboxConfig(
        inbox=_str_value(mailbox_raw.get("inbox", "INBOX"), "mailbox.inbox"),
        processed_folder=_str_value(mailbox_raw.get("processed_folder"), "mailbox.processed_folder"),
        failed_folder=_str_value(mailbox_raw.get("failed_folder"), "mailbox.failed_folder"),
        search_criterion=_str_value(mailbox_raw.get("search_criterion", "UNSEEN"), "mailbox.search_criterion"),
    )
    if mailbox.processed_folder == mailbox.failed_folder:
        raise ConfigError("mailbox.processed_folder and mailbox.failed_folder must be different.")

    routing = RoutingConfig(
        exact_senders=_normalize_route_map(routing_raw.get("exact_senders"), "routing.exact_senders", mode="email"),
        sender_domains=_normalize_route_map(routing_raw.get("sender_domains"), "routing.sender_domains", mode="domain"),
    )

    ocr_defaults = OCRDefaultsConfig(
        batch_size=_int_value(ocr_raw.get("batch_size", 8), "ocr_defaults.batch_size", minimum=1),
        num_beams=_int_value(ocr_raw.get("num_beams", 1), "ocr_defaults.num_beams", minimum=1),
        word_kernel_divisor=_optional_int(ocr_raw.get("word_kernel_divisor"), "ocr_defaults.word_kernel_divisor", minimum=1),
        save_crops=_bool_value(ocr_raw.get("save_crops", True), "ocr_defaults.save_crops"),
    )

    attachments = AttachmentPolicyConfig(
        process_all_pdfs=_bool_value(attachments_raw.get("process_all_pdfs", True), "attachments.process_all_pdfs"),
        max_pdf_attachments_per_email=_int_value(
            attachments_raw.get("max_pdf_attachments_per_email", 10),
            "attachments.max_pdf_attachments_per_email",
            minimum=1,
        ),
        max_pdf_size_mb=_int_value(attachments_raw.get("max_pdf_size_mb", 25), "attachments.max_pdf_size_mb", minimum=1),
    )

    reply = ReplyConfig(
        subject_prefix=_str_value(reply_raw.get("subject_prefix", "[DTOCR]"), "reply.subject_prefix"),
        include_excel=_bool_value(reply_raw.get("include_excel", True), "reply.include_excel"),
        include_json=_bool_value(reply_raw.get("include_json", False), "reply.include_json"),
    )

    db_path_env = os.getenv("DTOCR_DB_PATH", "").strip()
    db_path = Path(db_path_env) if db_path_env else (project_root / "data" / "DTOCR.sqlite3")

    imap = IMAPConfig(
        host=_env_required("DTOCR_MAIL_IMAP_HOST"),
        port=_env_int_required("DTOCR_MAIL_IMAP_PORT", minimum=1),
        user=_env_required("DTOCR_MAIL_IMAP_USER"),
        password=_env_required("DTOCR_MAIL_IMAP_PASSWORD"),
        use_ssl=_env_bool_required("DTOCR_MAIL_IMAP_SSL"),
        timeout_seconds=_env_int_optional("DTOCR_MAIL_IMAP_TIMEOUT_SECONDS", default=30, minimum=1),
    )

    smtp_user = _env_required("DTOCR_MAIL_SMTP_USER")
    smtp = SMTPConfig(
        host=_env_required("DTOCR_MAIL_SMTP_HOST"),
        port=_env_int_required("DTOCR_MAIL_SMTP_PORT", minimum=1),
        user=smtp_user,
        password=_env_required("DTOCR_MAIL_SMTP_PASSWORD"),
        starttls=_env_bool_required("DTOCR_MAIL_SMTP_STARTTLS"),
        reply_from=os.getenv("DTOCR_MAIL_REPLY_FROM", "").strip() or smtp_user,
        timeout_seconds=_env_int_optional("DTOCR_MAIL_SMTP_TIMEOUT_SECONDS", default=30, minimum=1),
        use_ssl=_env_bool_optional("DTOCR_MAIL_SMTP_SSL", default=False),
    )

    return MailIngestConfig(
        config_path=config_path,
        project_root=project_root,
        db_path=db_path,
        poll=poll,
        mailbox=mailbox,
        routing=routing,
        ocr_defaults=ocr_defaults,
        attachments=attachments,
        reply=reply,
        imap=imap,
        smtp=smtp,
    )


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    assert yaml is not None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigError(f"Failed to parse YAML config {path}: {exc}") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError("Top-level YAML config must be a mapping/object.")
    return dict(raw)


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{field_name} must be a mapping/object.")
    return dict(value)


def _normalize_route_map(value: Any, field_name: str, mode: str) -> dict[str, int]:
    raw = _mapping(value, field_name)
    out: dict[str, int] = {}
    for key, route_value in raw.items():
        normalized = str(key).strip().lower()
        if not normalized:
            raise ConfigError(f"{field_name} contains an empty key.")
        if mode == "email":
            if "@" not in normalized:
                raise ConfigError(f"{field_name} key must be an email address: {key}")
        elif mode == "domain":
            normalized = normalized.lstrip("@")
            if "." not in normalized:
                raise ConfigError(f"{field_name} key must be a domain: {key}")
        out[normalized] = _int_value(route_value, f"{field_name}.{key}", minimum=1)
    return out


def _str_value(value: Any, field_name: str) -> str:
    if value is None:
        raise ConfigError(f"Missing required field: {field_name}")
    text = str(value).strip()
    if not text:
        raise ConfigError(f"{field_name} must not be empty.")
    return text


def _bool_value(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "yes", "on"}:
            return True
        if v in {"0", "false", "no", "off"}:
            return False
    raise ConfigError(f"{field_name} must be a boolean.")


def _int_value(value: Any, field_name: str, minimum: int | None = None) -> int:
    try:
        n = int(value)
    except Exception as exc:
        raise ConfigError(f"{field_name} must be an integer.") from exc
    if minimum is not None and n < minimum:
        raise ConfigError(f"{field_name} must be >= {minimum}.")
    return n


def _optional_int(value: Any, field_name: str, minimum: int | None = None) -> int | None:
    if value is None:
        return None
    return _int_value(value, field_name, minimum=minimum)


def _int_list(value: Any, field_name: str, minimum: int | None = None) -> list[int]:
    if not isinstance(value, list):
        raise ConfigError(f"{field_name} must be a list.")
    return [_int_value(item, f"{field_name}[{idx}]", minimum=minimum) for idx, item in enumerate(value)]


def _env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Required environment variable is missing: {name}")
    return value


def _env_int_required(name: str, minimum: int | None = None) -> int:
    return _int_value(_env_required(name), name, minimum=minimum)


def _env_bool_required(name: str) -> bool:
    return _bool_value(_env_required(name), name)


def _env_int_optional(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return _int_value(raw, name, minimum=minimum)


def _env_bool_optional(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return _bool_value(raw, name)
