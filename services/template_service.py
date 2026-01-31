from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import re
import tempfile
from pathlib import Path

from DTOCR.db.repositories import TemplateRepository # type: ignore
import fitz
import logging
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

                    # If this is a data_field region, try to split into rows/columns
                    if safe_label.startswith("data_field"):
                        try:
                            subcrops = self._split_data_field_region(str(output_path), label, page_number, output_dir)
                        except Exception as exc:
                            logging.getLogger(__name__).warning(
                                "Failed to split data_field '%s' on page %s: %s", label, page_number, exc
                            )
                            subcrops = []

                        if subcrops:
                            crops.extend(subcrops)
                        else:
                            crops.append(
                                {
                                    "label": label,
                                    "page": page_number,
                                    "dpi": dpi,
                                    "path": str(output_path),
                                }
                            )
                    else:
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

    def run_ocr_pipeline(self, template_payload: dict[str, Any], pdf_path: str, excel_path: str | None = None) -> dict[str, Any]:
        crop_result = self.apply_template_to_pdf(template_payload, pdf_path)
        crops = crop_result.get("crops", [])
        output_dir = crop_result.get("output_dir", "")
        if not crops or not output_dir:
            return {"output_dir": output_dir, "crops": crops, "results": []}
        pipeline = OCRPipeline()
        ocr_result = pipeline.run(crops=crops, output_dir=output_dir, excel_path=excel_path)
        return {
            "output_dir": output_dir,
            "crops": crops,
            "results": ocr_result.get("results", []),
            "ocr_output_path": ocr_result.get("ocr_output_path", ""),
            "excel_output_path": ocr_result.get("excel_output_path", ""),
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

    def _split_data_field_region(self, image_path: str, label: str, page_number: int, output_dir: Path) -> list[dict[str, Any]]:
        """Split a data_field crop into rows and columns using image processing.

        Returns a list of crop dicts (same shape as apply_template_to_pdf returns) for each detected cell.
        Logs bounding boxes and counts for traceability.
        """
        logger = logging.getLogger(__name__)
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except Exception as exc:
            logger.warning("OpenCV or numpy not available, skipping data_field splitting: %s", exc)
            return []

        img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            logger.warning("Failed to read image for data_field splitting: %s", image_path)
            return []

        h_img, w_img = img.shape[:2]

        # Diffuse / denoise
        blur = cv2.GaussianBlur(img, (5, 5), 0)

        # Binarize using Otsu (then invert so text is white on black)
        _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        th = 255 - th

        # Morphologically close horizontally to group into rows
        horiz_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(10, w_img // 40), 1))
        rows_img = cv2.morphologyEx(th, cv2.MORPH_CLOSE, horiz_kernel, iterations=1)

        contours, _ = cv2.findContours(rows_img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        row_boxes = [cv2.boundingRect(cnt) for cnt in contours]
        # Filter tiny boxes
        row_boxes = [b for b in row_boxes if b[2] > 10 and b[3] > 6]
        if not row_boxes:
            logger.info("No rows detected for data_field '%s' on page %s", label, page_number)
            return []

        # Sort rows by y (top to bottom)
        row_boxes.sort(key=lambda x: x[1])

        logger.info("Detected %s rows for data_field '%s' on page %s", len(row_boxes), label, page_number)

        subcrops: list[dict[str, Any]] = []
        cell_index = 0
        for row_idx, (rx, ry, rw, rh) in enumerate(row_boxes, start=1):
            # Crop row area from original image for column detection
            row_img = th[ry : ry + rh, rx : rx + rw]

            # Detect vertical groupings for columns
            vert_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(10, h_img // 40)))
            cols_img = cv2.morphologyEx(row_img, cv2.MORPH_CLOSE, vert_kernel, iterations=1)
            contours_c, _ = cv2.findContours(cols_img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            col_boxes = [cv2.boundingRect(cnt) for cnt in contours_c]
            col_boxes = [b for b in col_boxes if b[2] > 6 and b[3] > 6]
            if not col_boxes:
                # fallback: use the whole row as one column
                col_boxes = [(0, 0, rw, rh)]

            # Sort columns left-to-right
            col_boxes.sort(key=lambda x: x[0])

            logger.info(
                "Row %s for '%s' page %s: detected %s columns", row_idx, label, page_number, len(col_boxes)
            )

            for col_idx, (cx, cy, cw, ch) in enumerate(col_boxes, start=1):
                # Absolute bbox in the crop image
                abs_x = rx + cx
                abs_y = ry + cy
                abs_w = cw
                abs_h = ch

                # Apply a small padding
                pad_x = max(1, abs_w // 30)
                pad_y = max(1, abs_h // 30)
                x0 = max(0, abs_x - pad_x)
                y0 = max(0, abs_y - pad_y)
                x1 = min(w_img, abs_x + abs_w + pad_x)
                y1 = min(h_img, abs_y + abs_h + pad_y)

                cell_img = img[y0:y1, x0:x1]

                # Save sub-image
                cell_index += 1
                sub_filename = f"{self._sanitize_label(label)}_p{page_number}_cell{cell_index}.png"
                sub_path = output_dir / sub_filename
                cv2.imwrite(str(sub_path), cell_img)

                bbox = (x0, y0, x1 - x0, y1 - y0)
                logger.info(
                    "Detected cell %s for '%s' page %s: bbox=%s (row=%s,col=%s)",
                    cell_index,
                    label,
                    page_number,
                    bbox,
                    row_idx,
                    col_idx,
                )

                subcrops.append(
                    {
                        "label": label,
                        "page": page_number,
                        "dpi": None,
                        "path": str(sub_path),
                        "parent_label": label,
                        "row": row_idx,
                        "col": col_idx,
                        "bbox": {
                            "x": int(bbox[0]),
                            "y": int(bbox[1]),
                            "width": int(bbox[2]),
                            "height": int(bbox[3]),
                        },
                    }
                )

        logger.info("Created %s sub-crops for data_field '%s' on page %s", len(subcrops), label, page_number)
        return subcrops

    def _sanitize_label(self, label: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", label.strip())
        return cleaned or "region"
