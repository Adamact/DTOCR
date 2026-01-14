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
