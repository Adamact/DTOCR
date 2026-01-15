from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from DTOCR.db.sqlite import Database # type: ignore


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class TemplateRepository:
    db: Database

    def create_template(self, name: str, vendor: str, document_type: str) -> int:
        created_at = _utc_now_iso()
        with self.db.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO templates(name, vendor, document_type, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (name, vendor, document_type, created_at),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_templates(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT id, name, vendor, document_type, created_at FROM templates ORDER BY id DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def save_version(self, template_id: int, payload: dict[str, Any]) -> int:
        created_at = _utc_now_iso()
        payload_json = json.dumps(payload, ensure_ascii=False)

        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS max_ver FROM template_versions WHERE template_id = ?",
                (template_id,),
            ).fetchone()
            next_version = int(row["max_ver"]) + 1

            cur = conn.execute(
                """
                INSERT INTO template_versions(template_id, version, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (template_id, next_version, payload_json, created_at),
            )
            conn.commit()
            return int(cur.lastrowid)

    def get_latest_version_payload(self, template_id: int) -> Optional[dict[str, Any]]:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM template_versions
                WHERE template_id = ?
                ORDER BY version DESC
                LIMIT 1
                """,
                (template_id,),
            ).fetchone()
            if row is None:
                return None
            return json.loads(row["payload_json"])

    def delete_template(self, template_id: int) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM template_versions WHERE template_id = ?", (template_id,))
            conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))
            conn.commit()

    def list_label_options(self) -> list[str]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT name FROM label_options ORDER BY name").fetchall()
            return [row["name"] for row in rows]

    def seed_label_options(self, labels: list[str]) -> None:
        with self.db.connect() as conn:
            conn.executemany("INSERT OR IGNORE INTO label_options(name) VALUES (?)", [(label,) for label in labels])
            conn.commit()

    def add_label_option(self, label: str) -> None:
        with self.db.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO label_options(name) VALUES (?)", (label,))
            conn.commit()

    def delete_label_option(self, label: str) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM label_options WHERE name = ?", (label,))
            conn.commit()

    def rename_label_option(self, old_label: str, new_label: str) -> None:
        with self.db.connect() as conn:
            conn.execute("UPDATE label_options SET name = ? WHERE name = ?", (new_label, old_label))
            conn.commit()
