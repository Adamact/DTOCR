from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import re
import tempfile
from pathlib import Path

from DTOCR.db.repositories import TemplateRepository # type: ignore
import fitz
from DTOCR.core.ocr_pipeline import OCRPipeline


@dataclass
class TemplateService:
    template_repo: TemplateRepository

    DEFAULT_LABEL_OPTIONS = [
        "invoice_number",
        "invoice_date",
        "invoice_total_no_tax",
        "invoice_total_with_tax",
        "SortCode",
        "tax_amount",
        "due_date",
        "data_field_1",
        "data_field_2",
        "data_field_3",
        "vendor_name",
        "bill_to",
        "ship_to",
    ]

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

    def delete_template(self, template_id: int) -> None:
        self.template_repo.delete_template(template_id=template_id)

    def list_label_options(self) -> list[str]:
        labels = self.template_repo.list_label_options()
        if not labels:
            self.template_repo.seed_label_options(self.DEFAULT_LABEL_OPTIONS)
            labels = self.template_repo.list_label_options()
        return labels

    def add_label_option(self, label: str) -> bool:
        cleaned = label.strip()
        if not cleaned:
            return False
        existing = {name.lower() for name in self.template_repo.list_label_options()}
        if cleaned.lower() in existing:
            return False
        self.template_repo.add_label_option(cleaned)
        return True

    def delete_label_option(self, label: str) -> None:
        cleaned = label.strip()
        if not cleaned:
            return
        self.template_repo.delete_label_option(cleaned)

    def rename_label_option(self, old_label: str, new_label: str) -> bool:
        old_clean = old_label.strip()
        new_clean = new_label.strip()
        if not old_clean or not new_clean or old_clean == new_clean:
            return False
        labels = self.template_repo.list_label_options()
        labels_lower = {name.lower() for name in labels}
        if old_clean.lower() not in labels_lower:
            return False
        if new_clean.lower() in labels_lower and new_clean.lower() != old_clean.lower():
            return False
        self.template_repo.rename_label_option(old_clean, new_clean)
        return True

    def apply_template_to_pdf(self, template_payload: dict[str, Any], pdf_path: str) -> dict[str, Any]:
        regions = template_payload.get("regions", [])
        if not regions:
            return {"output_dir": "", "crops": []}

        output_dir = Path(tempfile.mkdtemp(prefix="dtocr_crops_"))
        doc = fitz.open(pdf_path)
        crops: list[dict[str, Any]] = []
        dpi = self._template_dpi(template_payload)
        matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)

        try:
            page_count = doc.page_count
            label_counts: dict[str, int] = {}
            for page_index in range(page_count):
                page_number = page_index + 1
                page = doc.load_page(page_index)
                for region in regions:
                    if not self._region_applies_to_page(region, page_number, page_count):
                        continue
                    rect = fitz.Rect(
                        float(region.get("x", 0.0)),
                        float(region.get("y", 0.0)),
                        float(region.get("x", 0.0)) + float(region.get("width", 0.0)),
                        float(region.get("y", 0.0)) + float(region.get("height", 0.0)),
                    )
                    label = str(region.get("label", "unknown"))
                    safe_label = self._sanitize_label(label)
                    label_counts[safe_label] = label_counts.get(safe_label, 0) + 1
                    filename = f"{safe_label}_p{page_number}_{label_counts[safe_label]}.png"
                    output_path = output_dir / filename
                    pix = page.get_pixmap(clip=rect, matrix=matrix)
                    pix.save(str(output_path))
                    crops.append(
                        {
                            "label": label,
                            "page": page_number,
                            "dpi": dpi,
                            "path": str(output_path),
                        }
                    )
        finally:
            doc.close()

        return {"output_dir": str(output_dir), "crops": crops}

    def run_ocr_pipeline(self, template_payload: dict[str, Any], pdf_path: str) -> dict[str, Any]:
        crop_result = self.apply_template_to_pdf(template_payload, pdf_path)
        crops = crop_result.get("crops", [])
        output_dir = crop_result.get("output_dir", "")
        if not crops or not output_dir:
            return {"output_dir": output_dir, "crops": crops, "results": []}
        pipeline = OCRPipeline()
        ocr_result = pipeline.run(crops=crops, output_dir=output_dir)
        return {
            "output_dir": output_dir,
            "crops": crops,
            "results": ocr_result.get("results", []),
            "ocr_output_path": ocr_result.get("ocr_output_path", ""),
        }

    def _region_applies_to_page(self, region: dict[str, Any], page_number: int, page_count: int) -> bool:
        page_scope = region.get("page_scope", "specific")
        if page_scope == "specific":
            return int(region.get("page", 1)) == page_number
        if page_scope == "first":
            return page_number == 1
        if page_scope == "last":
            return page_count > 0 and page_number == page_count
        if page_scope == "middle":
            return page_count > 2 and 1 < page_number < page_count
        return False

    def _template_dpi(self, template_payload: dict[str, Any]) -> int:
        try:
            dpi = int(template_payload.get("dpi", 300))
        except ValueError:
            dpi = 300
        return max(72, min(dpi, 600))

    def _sanitize_label(self, label: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", label.strip())
        return cleaned or "region"
