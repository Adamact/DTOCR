from __future__ import annotations

import imaplib
import logging
import time

from DTOCR.services.mail_ingest.config import MailIngestConfig
from DTOCR.services.mail_ingest.imap_client import IMAPMailClient
from DTOCR.services.mail_ingest.models import MessageProcessingResult
from DTOCR.services.mail_ingest.processor import MailProcessor
from DTOCR.services.mail_ingest.router import SenderMapResolver
from DTOCR.services.mail_ingest.smtp_client import SMTPMailClient

logger = logging.getLogger(__name__)


class MailPoller:
    def __init__(self, config: MailIngestConfig) -> None:
        self.config = config
        self.imap_client = IMAPMailClient(config.imap, config.mailbox)
        self.smtp_client = SMTPMailClient(config.smtp)
        self.resolver = SenderMapResolver(
            exact_senders=config.routing.exact_senders,
            sender_domains=config.routing.sender_domains,
        )
        self.processor = MailProcessor(
            config=config,
            imap_client=self.imap_client,
            smtp_client=self.smtp_client,
            resolver=self.resolver,
        )
        self._startup_validated = False

    def validate_startup(self) -> None:
        self.processor.validate_startup()
        self.imap_client.connect()
        self.imap_client.ensure_folders()
        self._startup_validated = True

    def run_once(self) -> list[MessageProcessingResult]:
        if not self._startup_validated:
            self.validate_startup()
        else:
            self.imap_client.noop()

        uids = self.imap_client.search_unseen_uids(self.config.poll.max_messages_per_cycle)
        logger.info(
            "Mail poll cycle: inbox=%s search=%s found=%s",
            self.config.mailbox.inbox,
            self.config.mailbox.search_criterion,
            len(uids),
        )

        results: list[MessageProcessingResult] = []
        for uid in uids:
            results.append(self.processor.process_uid(uid))
        return results

    def run_forever(self) -> None:
        backoffs = list(self.config.poll.reconnect_backoff_seconds or (5, 15, 30, 60, 300))
        backoff_index = 0

        while True:
            try:
                self.run_once()
                backoff_index = 0
                time.sleep(self.config.poll.interval_seconds)
            except KeyboardInterrupt:
                raise
            except (imaplib.IMAP4.error, OSError, TimeoutError, ConnectionError) as exc:
                wait_seconds = backoffs[min(backoff_index, len(backoffs) - 1)]
                logger.exception("Mail poller connection error: %s. Reconnecting in %ss", exc, wait_seconds)
                self._reset_imap_connection()
                self._startup_validated = False
                time.sleep(wait_seconds)
                backoff_index += 1
            except Exception as exc:
                logger.exception("Mail poller unexpected error: %s", exc)
                time.sleep(max(1, self.config.poll.interval_seconds))

    def close(self) -> None:
        self.imap_client.close()

    def _reset_imap_connection(self) -> None:
        try:
            self.imap_client.close()
        except Exception:
            pass
