from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys

# Ensure the parent of the project root is on sys.path so `import DTOCR` works
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from DTOCR.core.logging import configure_logging  # type: ignore
from DTOCR.services.mail_ingest import ConfigError, MailPoller, load_mail_ingest_config  # type: ignore


def main() -> None:
    parser = argparse.ArgumentParser(description="DTOCR mail-to-OCR ingestor (IMAP poller)")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).resolve().parents[1] / "config" / "mail_ingestor.yaml"),
        help="Path to mail ingestor YAML config (default: config/mail_ingestor.yaml)",
    )
    parser.add_argument("--once", action="store_true", help="Run a single poll cycle and exit")
    parser.add_argument("--log-level", default="INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR)")
    args = parser.parse_args()

    level = getattr(logging, str(args.log_level).upper(), logging.INFO)
    configure_logging(level=level)

    try:
        config = load_mail_ingest_config(args.config)
    except ConfigError as exc:
        raise SystemExit(f"Config error: {exc}") from exc

    poller = MailPoller(config)
    try:
        if args.once:
            results = poller.run_once()
            logging.getLogger(__name__).info("Completed one poll cycle; processed_messages=%s", len(results))
            return
        poller.run_forever()
    finally:
        poller.close()


if __name__ == "__main__":
    main()
