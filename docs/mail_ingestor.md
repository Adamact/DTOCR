## DTOCR Mail Ingestor (IMAP -> OCR -> Reply)

This service watches an IMAP inbox for unread emails, extracts PDF attachments, routes them to DTOCR templates using sender mappings, runs the OCR pipeline, and replies with Excel outputs.

### Setup

1. Create `config/mail_ingestor.yaml` from `config/mail_ingestor.example.yaml`.
2. Set required environment variables:
   - `DTOCR_DB_PATH` (recommended)
   - `DTOCR_MAIL_IMAP_HOST`
   - `DTOCR_MAIL_IMAP_PORT`
   - `DTOCR_MAIL_IMAP_USER`
   - `DTOCR_MAIL_IMAP_PASSWORD`
   - `DTOCR_MAIL_IMAP_SSL`
   - `DTOCR_MAIL_SMTP_HOST`
   - `DTOCR_MAIL_SMTP_PORT`
   - `DTOCR_MAIL_SMTP_USER`
   - `DTOCR_MAIL_SMTP_PASSWORD`
   - `DTOCR_MAIL_SMTP_STARTTLS`
   - `DTOCR_MAIL_REPLY_FROM` (optional)

### Run

Single cycle (smoke test):

```powershell
python scripts/run_mail_ingestor.py --config config/mail_ingestor.yaml --once
```

Continuous poller:

```powershell
python scripts/run_mail_ingestor.py --config config/mail_ingestor.yaml
```

### Runtime Artifacts

The service writes runtime artifacts under:

- `storage/mail/attachments/`
- `storage/mail/results/`
- `storage/mail/manifests/`

### Operational Notes

- Messages are moved to the configured `Processed` or `Failed` folders.
- The processor skips self-sent and auto-generated messages to avoid mail loops.
- Sender routing is implemented as a pluggable resolver so future automatic template detection can replace it without changing the poller flow.
