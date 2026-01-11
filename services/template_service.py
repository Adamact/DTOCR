from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from DTOCR.db.repositories import TemplateRepository # type: ignore


@dataclass
class TemplateService:
    template_repo: TemplateRepository

    def create_template(self, name: str, vendor: str, document_type: str) -> int:
        # Place for validation rules later
        return self.template_repo.create_template(name=name, vendor=vendor, document_type=document_type)

    def list_templates(self) -> list[dict[str, Any]]:
        return self.template_repo.list_templates()

    def save_template_version(self, template_id: int, payload: dict[str, Any]) -> int:
        # Place for schema validation later
        return self.template_repo.save_version(template_id=template_id, payload=payload)

    def load_latest_template_payload(self, template_id: int) -> dict[str, Any] | None:
        return self.template_repo.get_latest_version_payload(template_id=template_id)
