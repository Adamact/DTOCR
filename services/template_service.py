from __future__ import annotations

import io
import logging
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from DTOCR.core.grid_detector import merge_detection_settings
from DTOCR.core.ocr_pipeline import OCRPipeline
from DTOCR.core.pdf_renderer import PdfDocument, PdfPage, PdfRect, scale_from_dpi
from DTOCR.db.repositories import TemplateRepository  # type: ignore


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
        "data_field_grid_1",
        "data_field_grid_2",
        "data_field_grid_3",
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

    def apply_template_to_pdf(self, template_payload: dict[str, Any], pdf_path: str, word_kernel_divisor: int | None = None, save_crops: bool = True) -> dict[str, Any]:
        """Apply template and optionally control how data_field word-kernel is computed.

        word_kernel_divisor: smaller values produce larger kernels (more aggressive merging).
        save_crops: when False, returned subcrop dicts will include in-memory PNG bytes under 'image_bytes' instead of saving cell images to disk.
        
        For multi-page documents: column structure is cached from page 1 and reused for subsequent pages,
        while row detection remains adaptive per page.
        """
        regions = template_payload.get("regions", [])
        if not regions:
            return {"output_dir": "", "crops": []}

        output_dir = Path(tempfile.mkdtemp(prefix="dtocr_crops_"))
        doc = PdfDocument(pdf_path)
        crops: list[dict[str, Any]] = []
        dpi = self._template_dpi(template_payload)
        scale = scale_from_dpi(dpi)

        # Cache for column boundaries per label (page 1 is the reference)
        col_boundaries_cache: dict[str, list[int]] = {}

        try:
            text_mode = self._template_text_mode(template_payload)
            allow_text = text_mode in ("auto", "text_only")
            allow_ocr = text_mode in ("auto", "ocr_only")
            detection_settings = merge_detection_settings(template_payload.get("detection_settings"))
            page_count = doc.page_count
            label_counts: dict[str, int] = {}
            for page_index in range(page_count):
                page_number = page_index + 1
                page = doc.load_page(page_index)
                for region in regions:
                    if not self._region_applies_to_page(region, page_number, page_count):
                        continue
                    rect = PdfRect(
                        float(region.get("x", 0.0)),
                        float(region.get("y", 0.0)),
                        float(region.get("x", 0.0)) + float(region.get("width", 0.0)),
                        float(region.get("y", 0.0)) + float(region.get("height", 0.0)),
                    )
                    label = str(region.get("label", "unknown"))
                    safe_label = self._sanitize_label(label)
                    label_counts[safe_label] = label_counts.get(safe_label, 0) + 1
                    if allow_text:
                        text_payload = self._extract_text_payload(page, rect)
                        has_text = self._is_text_meaningful(text_payload)
                    else:
                        text_payload = {"text": "", "words": []}
                        has_text = False
                    # Explicit grid: override auto-detection
                    if safe_label.startswith("data_field_grid") and isinstance(region.get("grid"), dict):
                        if allow_text and has_text:
                            subcrops = self._split_grid_region_text(
                                page=page,
                                region=region,
                                label=label,
                                page_number=page_number,
                                rect=rect,
                            )
                            if subcrops:
                                crops.extend(subcrops)
                                continue
                        if allow_ocr:
                            subcrops = self._split_grid_region(
                                page=page,
                                region=region,
                                label=label,
                                page_number=page_number,
                                output_dir=output_dir,
                                scale=scale,
                                dpi=dpi,
                                save_crops=save_crops,
                                detection_settings=detection_settings,
                            )
                            if subcrops:
                                crops.extend(subcrops)
                        continue

                    if allow_text and has_text and safe_label.startswith("data_field"):
                        structured = self._structure_text_payload(text_payload)
                        crops.append(
                            {
                                "label": label,
                                "page": page_number,
                                "dpi": dpi,
                                "text": structured.get("text", ""),
                                "text_source": "pdf_text",
                                "structured_rows": structured.get("rows"),
                                "structured_cols": structured.get("cols"),
                            }
                        )
                        continue
                    if allow_text and has_text and not safe_label.startswith("data_field"):
                        crops.append(
                            {
                                "label": label,
                                "page": page_number,
                                "dpi": dpi,
                                "text": text_payload.get("text", ""),
                                "text_source": "pdf_text",
                            }
                        )
                        continue

                    if not allow_ocr:
                        continue

                    filename = f"{safe_label}_p{page_number}_{label_counts[safe_label]}.png"
                    output_path = output_dir / filename
                    pix = page.render_pixmap(scale=scale, clip=rect)
                    pix.save(str(output_path))

                    # If this is a data_field region, try to split into rows/columns
                    if safe_label.startswith("data_field"):
                        try:
                            # On first page of this label, auto-detect and cache columns
                            cached_cols = None
                            if label_counts[safe_label] > 1 and safe_label in col_boundaries_cache:
                                # Use cached columns from first page
                                cached_cols = col_boundaries_cache[safe_label]
                            
                            subcrops = self._split_data_field_region(
                                str(output_path),
                                label,
                                page_number,
                                output_dir,
                                word_kernel_divisor=word_kernel_divisor,
                                save_crops=save_crops,
                                cached_col_boundaries=cached_cols,
                                col_boundaries_cache=col_boundaries_cache,
                                detection_settings=detection_settings,
                            )

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

    def run_ocr_pipeline(
        self,
        template_payload: dict[str, Any],
        pdf_path: str,
        excel_path: str | None = None,
        word_kernel_divisor: int | None = None,
        save_crops: bool = True,
        batch_size: int = 8,
        num_beams: int = 1,
        progress_callback: Callable[[int, int], None] | None = None,
        dev_mode: bool = False,
    ) -> dict[str, Any]:
        crop_result = self.apply_template_to_pdf(template_payload, pdf_path, word_kernel_divisor=word_kernel_divisor, save_crops=save_crops)
        crops = crop_result.get("crops", [])
        output_dir = crop_result.get("output_dir", "")
        if not crops or not output_dir:
            return {"output_dir": output_dir, "crops": crops, "results": []}
        parser_name = str(template_payload.get("parser_name", "")).strip()
        if not parser_name:
            raise RuntimeError("Template payload missing parser_name; configure parser before running OCR.")
        pipeline = OCRPipeline()
        ocr_result = pipeline.run(
            crops=crops,
            output_dir=output_dir,
            parser_name=parser_name,
            excel_path=excel_path,
            batch_size=batch_size,
            num_beams=num_beams,
            progress_callback=progress_callback,
            dev_mode=dev_mode,
        )
        return {
            "output_dir": output_dir,
            "crops": crops,
            "results": ocr_result.get("results", []),
            "ocr_output_path": ocr_result.get("ocr_output_path", ""),
            "excel_output_path": ocr_result.get("excel_output_path", ""),
        }

    def _region_applies_to_page(self, region: dict[str, Any], page_number: int, page_count: int) -> bool:
        # Grid regions (data_field_grid_*) default to "all" pages for multi-page consistency
        label = str(region.get("label", ""))
        if label.startswith("data_field_grid"):
            page_scope = region.get("page_scope", "all")
        else:
            page_scope = region.get("page_scope", "specific")
        
        if page_scope == "all":
            return True
        if page_scope == "specific":
            return int(region.get("page", 1)) == page_number
        if page_scope == "first":
            return page_number == 1
        if page_scope == "last":
            return page_count > 0 and page_number == page_count
        if page_scope == "middle":
            return page_count > 2 and 1 < page_number < page_count
        return False

    def _extract_text_payload(self, page: PdfPage, rect: PdfRect) -> dict[str, Any]:
        return page.extract_text_payload(rect)

    def _is_text_meaningful(self, payload: dict[str, Any]) -> bool:
        text = str(payload.get("text", "")).strip()
        return len(text) > 0

    def _structure_text_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        words = list(payload.get("words", []))
        if not words:
            return {"text": payload.get("text", ""), "rows": [], "cols": []}

        words.sort(key=lambda w: (w["y0"], w["x0"]))
        heights = [max(1.0, w["y1"] - w["y0"]) for w in words]
        avg_height = sum(heights) / max(1, len(heights))
        line_tol = max(2.0, avg_height * 0.6)

        lines: list[list[dict[str, Any]]] = []
        for word in words:
            placed = False
            for line in lines:
                if abs(word["y0"] - line[0]["y0"]) <= line_tol:
                    line.append(word)
                    placed = True
                    break
            if not placed:
                lines.append([word])

        for line in lines:
            line.sort(key=lambda w: w["x0"])

        x_centers: list[float] = []
        for line in lines:
            for word in line:
                x_centers.append((word["x0"] + word["x1"]) / 2)
        x_centers.sort()
        if not x_centers:
            return {"text": payload.get("text", ""), "rows": [], "cols": []}

        avg_word_width = sum(max(1.0, w["x1"] - w["x0"]) for w in words) / max(1, len(words))
        col_tol = max(8.0, avg_word_width * 1.5)
        col_centers: list[float] = []
        for center in x_centers:
            if not col_centers or abs(center - col_centers[-1]) > col_tol:
                col_centers.append(center)

        rows: list[list[str]] = []
        for line in lines:
            row_cells = ["" for _ in col_centers]
            for word in line:
                center = (word["x0"] + word["x1"]) / 2
                col_idx = min(range(len(col_centers)), key=lambda i: abs(col_centers[i] - center))
                if row_cells[col_idx]:
                    row_cells[col_idx] = f"{row_cells[col_idx]} {word['text']}"
                else:
                    row_cells[col_idx] = word["text"]
            rows.append(row_cells)

        return {"text": payload.get("text", ""), "rows": rows, "cols": col_centers}

    def _split_grid_region_text(
        self,
        page: PdfPage,
        region: dict[str, Any],
        label: str,
        page_number: int,
        rect: PdfRect,
    ) -> list[dict[str, Any]]:
        grid = region.get("grid", {})
        rows = grid.get("rows", [])
        cols = grid.get("columns", [])
        if not rows or not cols:
            return []

        row_ratios = self._normalize_ratios([float(r.get("height_ratio", 0.0)) for r in rows])
        col_ratios = self._normalize_ratios([float(c.get("width_ratio", 0.0)) for c in cols])

        crops: list[dict[str, Any]] = []
        y_cursor = rect.y0
        for row_idx, row_ratio in enumerate(row_ratios):
            row_height = rect.height * row_ratio
            x_cursor = rect.x0
            for col_idx, col_ratio in enumerate(col_ratios):
                col_width = rect.width * col_ratio
                cell_rect = PdfRect(x_cursor, y_cursor, x_cursor + col_width, y_cursor + row_height)
                payload = self._extract_text_payload(page, cell_rect)
                crops.append(
                    {
                        "label": label,
                        "page": page_number,
                        "text": payload.get("text", ""),
                        "text_source": "pdf_text",
                        "grid_id": region.get("id"),
                        "grid_label": label,
                        "row_index": row_idx,
                        "col_index": col_idx,
                        "column_label": str(cols[col_idx].get("label", f"Column {col_idx + 1}")),
                        "cell_label": grid.get("cell_labels", {}).get(f"{row_idx}:{col_idx}", ""),
                    }
                )
                x_cursor += col_width
            y_cursor += row_height
        return crops

    def _template_dpi(self, template_payload: dict[str, Any]) -> int:
        try:
            dpi = int(template_payload.get("dpi", 300))
        except ValueError:
            dpi = 300
        return max(72, min(dpi, 600))

    def _template_text_mode(self, template_payload: dict[str, Any]) -> str:
        mode = str(template_payload.get("text_mode", "auto")).strip().lower()
        if mode not in {"auto", "ocr_only", "text_only"}:
            return "auto"
        return mode

    def _split_grid_region(
        self,
        page: PdfPage,
        region: dict[str, Any],
        label: str,
        page_number: int,
        output_dir: Path,
        scale: float,
        dpi: int,
        save_crops: bool = True,
        detection_settings: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        grid = region.get("grid", {})
        rows = grid.get("rows", [])
        cols = grid.get("columns", [])
        if not cols:
            return []

        # Extract region as image for row detection
        origin_x = float(region.get("x", 0.0))
        origin_y = float(region.get("y", 0.0))
        width = float(region.get("width", 0.0))
        height = float(region.get("height", 0.0))
        
        rect = PdfRect(origin_x, origin_y, origin_x + width, origin_y + height)
        pix = page.render_pixmap(scale=scale, clip=rect)
        region_image = pix.image
        img_w, img_h = region_image.size
        
        # Auto-detect rows adaptively per page
        logger = logging.getLogger(__name__)
        logger.info("Processing grid '%s' on page %d - extracting region at (%d,%d) size %dx%d", 
                   label, page_number, int(origin_x), int(origin_y), int(width), int(height))
        
        row_boxes = None
        h_img = None
        w_img = None
        
        try:
            import cv2
            import numpy as np
            
            # Convert pixmap to image for row detection
            img_cv = cv2.cvtColor(np.array(region_image), cv2.COLOR_RGB2GRAY)
            
            h_img, w_img = img_cv.shape[:2]
            
            # Threshold to get binary image
            _, binary = cv2.threshold(img_cv, 127, 255, cv2.THRESH_BINARY)
            
            # Use gap-finding algorithm to detect row boundaries
            # Calculate darkness per row (sum of black pixels)
            darkness = np.sum(binary == 0, axis=1)
            
            min_darkness = np.min(darkness)
            max_darkness = np.max(darkness)
            
            settings = merge_detection_settings(detection_settings)
            row_settings = settings.get("row", {})

            if max_darkness > min_darkness:
                # Define threshold to identify content rows
                darkness_range = max_darkness - min_darkness
                threshold_ratio = float(row_settings.get("content_threshold", 0.20))
                content_threshold = min_darkness + (darkness_range * threshold_ratio)
                
                # Find content regions
                content_regions = []
                in_content = False
                region_start = 0
                
                for y in range(h_img):
                    has_content = darkness[y] > content_threshold
                    
                    if has_content and not in_content:
                        region_start = y
                        in_content = True
                    elif not has_content and in_content:
                        content_regions.append((region_start, y))
                        in_content = False
                
                if in_content:
                    content_regions.append((region_start, h_img))
                
                # Convert content regions to row boxes
                if content_regions:
                    row_boxes = [(0, start, w_img, end - start) for start, end in content_regions]
                    min_row_height_pct = float(row_settings.get("min_row_height_pct", 1.0))
                    min_row_height = max(3, int(h_img * (min_row_height_pct / 100.0)))
                    row_boxes = [b for b in row_boxes if b[3] >= min_row_height]
                    logger.info("Gap-finding detected %d content regions on page %d", len(row_boxes), page_number)
                else:
                    row_boxes = []
            else:
                row_boxes = []
            
            if row_boxes:
                logger.info("Auto-detected %d rows for grid '%s' on page %d (template had %d rows) - image size: %dx%d", 
                           len(row_boxes), label, page_number, len(rows), w_img, h_img)
                logger.debug("Row boxes for page %d: %s", page_number, row_boxes[:5])  # Log first 5 rows
            else:
                # Fallback to template rows if detection fails
                logger.warning("Row detection found no rows for grid '%s' page %d, using template rows", label, page_number)
                row_boxes = None
        except Exception as exc:
            logger.warning("Could not auto-detect rows for grid '%s' page %d: %s, using template rows", 
                          label, page_number, exc)
            row_boxes = None
            h_img = img_h
            w_img = img_w

        # Use template column structure (consistent across pages)
        col_ratios = self._normalize_ratios([float(c.get("width_ratio", 0.0)) for c in cols])

        # If rows were detected, use them; otherwise use template rows
        if row_boxes and h_img is not None:
            # Use detected row positions (adaptive)
            logger.info("Using %d detected rows with template columns for page %d", len(row_boxes), page_number)
            crops: list[dict[str, Any]] = []
            for row_idx, (_rx, ry, _rw, rh) in enumerate(row_boxes):
                for col_idx, col_ratio in enumerate(col_ratios):
                    x_offset_px = sum(col_ratios[:col_idx]) * w_img
                    cell_width_px = col_ratio * w_img
                    left = int(max(0, round(x_offset_px)))
                    top = int(max(0, round(ry)))
                    right = int(min(w_img, round(x_offset_px + cell_width_px)))
                    bottom = int(min(h_img, round(ry + rh)))
                    if right <= left or bottom <= top:
                        continue
                    cell_image = region_image.crop((left, top, right, bottom))

                    crop_item: dict[str, Any] = {
                        "label": label,
                        "page": page_number,
                        "dpi": dpi,
                        "grid_id": region.get("id"),
                        "grid_label": label,
                        "row_index": row_idx,
                        "col_index": col_idx,
                        "column_label": str(cols[col_idx].get("label", f"Column {col_idx + 1}")),
                        "cell_label": grid.get("cell_labels", {}).get(f"{row_idx}:{col_idx}", ""),
                    }

                    if save_crops:
                        filename = f"{self._sanitize_label(label)}_grid_p{page_number}_r{row_idx + 1}_c{col_idx + 1}.png"
                        output_path = output_dir / filename
                        cell_image.save(str(output_path))
                        crop_item["path"] = str(output_path)
                    else:
                        buffer = io.BytesIO()
                        cell_image.save(buffer, format="PNG")
                        crop_item["image_bytes"] = buffer.getvalue()

                    crops.append(crop_item)
        else:
            # Fallback: use template row structure
            logger.info("Using template row structure (%d rows) for page %d", len(rows), page_number)
            if not rows:
                return []
            row_ratios = self._normalize_ratios([float(r.get("height_ratio", 0.0)) for r in rows])
            crops: list[dict[str, Any]] = []
            for row_idx, row_ratio in enumerate(row_ratios):
                y_offset_px = sum(row_ratios[:row_idx]) * img_h
                cell_height_px = row_ratio * img_h
                for col_idx, col_ratio in enumerate(col_ratios):
                    x_offset_px = sum(col_ratios[:col_idx]) * img_w
                    cell_width_px = col_ratio * img_w
                    left = int(max(0, round(x_offset_px)))
                    top = int(max(0, round(y_offset_px)))
                    right = int(min(img_w, round(x_offset_px + cell_width_px)))
                    bottom = int(min(img_h, round(y_offset_px + cell_height_px)))
                    if right <= left or bottom <= top:
                        continue
                    cell_image = region_image.crop((left, top, right, bottom))

                    crop_item: dict[str, Any] = {
                        "label": label,
                        "page": page_number,
                        "dpi": dpi,
                        "grid_id": region.get("id"),
                        "grid_label": label,
                        "row_index": row_idx,
                        "col_index": col_idx,
                        "column_label": str(cols[col_idx].get("label", f"Column {col_idx + 1}")),
                        "cell_label": grid.get("cell_labels", {}).get(f"{row_idx}:{col_idx}", ""),
                    }

                    if save_crops:
                        filename = f"{self._sanitize_label(label)}_grid_p{page_number}_r{row_idx + 1}_c{col_idx + 1}.png"
                        output_path = output_dir / filename
                        cell_image.save(str(output_path))
                        crop_item["path"] = str(output_path)
                    else:
                        buffer = io.BytesIO()
                        cell_image.save(buffer, format="PNG")
                        crop_item["image_bytes"] = buffer.getvalue()

                    crops.append(crop_item)

        return crops

    def _normalize_ratios(self, ratios: list[float]) -> list[float]:
        total = sum(r for r in ratios if r > 0)
        if total <= 0:
            count = max(len(ratios), 1)
            return [1.0 / count for _ in ratios]
        return [max(r, 0.0) / total for r in ratios]

    def _split_data_field_region(self, image_path: str, label: str, page_number: int, output_dir: Path, word_kernel_divisor: int | None = None, save_crops: bool = True, cached_col_boundaries: list[int] | None = None, col_boundaries_cache: dict[str, list[int]] | None = None, detection_settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Split a data_field crop into rows and columns using image processing.

        Returns a list of crop dicts (same shape as apply_template_to_pdf returns) for each detected cell.
        Logs bounding boxes and counts for traceability.

        word_kernel_divisor: optional integer; smaller values result in a larger horizontal kernel
        (and therefore more aggressive merging of characters into words). Default: 15.
        save_crops: when False, subcrop dicts include 'image_bytes' with PNG bytes and do not write cell image files to disk.
        cached_col_boundaries: if provided, use these column boundaries instead of auto-detecting columns.
        col_boundaries_cache: dict to store extracted column boundaries; will cache columns from first page of each label.
        """
        logger = logging.getLogger(__name__)
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore  # noqa: F401
        except Exception as exc:
            logger.warning("OpenCV or numpy not available, skipping data_field splitting: %s", exc)
            return []

        img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            logger.warning("Failed to read image for data_field splitting: %s", image_path)
            return []

        h_img, w_img = img.shape[:2]

        # Track preproc files and annotated visualization (to include in metadata)
        preproc_files: list[str] = []
        annotated_path_str: str | None = None

        # Diffuse / denoise
        blur = cv2.GaussianBlur(img, (5, 5), 0)

        # Binarize using Otsu (then invert so text is white on black)
        _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        th = 255 - th

        settings = merge_detection_settings(detection_settings)
        row_settings = settings.get("row", {})
        data_settings = settings.get("data_field", {})

        # Morphologically close horizontally to group into rows
        row_kernel_divisor = int(data_settings.get("row_kernel_divisor", 40))
        horiz_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(10, w_img // max(1, row_kernel_divisor)), 1))
        rows_img = cv2.morphologyEx(th, cv2.MORPH_CLOSE, horiz_kernel, iterations=1)

        # If debug logging is enabled, save preprocessing images and log their paths/sizes/shapes
        # Save these into a dedicated 'preproc' folder and prepare an 'annotated' folder for visualizations
        if logger.isEnabledFor(logging.DEBUG):
            base_name = f"{self._sanitize_label(label)}_p{page_number}"
            preproc_dir = output_dir / "preproc"
            annotated_dir = output_dir / "annotated"
            preproc_dir.mkdir(parents=True, exist_ok=True)
            annotated_dir.mkdir(parents=True, exist_ok=True)
            try:
                blur_path = preproc_dir / f"{base_name}_blur.png"
                th_path = preproc_dir / f"{base_name}_th.png"
                rows_path = preproc_dir / f"{base_name}_rows.png"
                cv2.imwrite(str(blur_path), blur)
                cv2.imwrite(str(th_path), th)
                cv2.imwrite(str(rows_path), rows_img)
                # Record preproc files to include in metadata
                preproc_files.extend([str(blur_path), str(th_path), str(rows_path)])
                try:
                    blur_size = blur_path.stat().st_size
                except Exception:
                    blur_size = None
                try:
                    th_size = th_path.stat().st_size
                except Exception:
                    th_size = None
                try:
                    rows_size = rows_path.stat().st_size
                except Exception:
                    rows_size = None
                logger.debug(
                    "Saved preprocessing images for '%s' page %s: blur=(path=%s,size=%s,shape=%s) th=(path=%s,size=%s,shape=%s) rows=(path=%s,size=%s,shape=%s)",
                    label,
                    page_number,
                    str(blur_path),
                    blur_size,
                    getattr(blur, "shape", None),
                    str(th_path),
                    th_size,
                    getattr(th, "shape", None),
                    str(rows_path),
                    rows_size,
                    getattr(rows_img, "shape", None),
                )
            except Exception as exc:
                logger.debug("Failed to save preprocessing images for '%s' page %s: %s", label, page_number, exc)

            # Prepare an annotated visualization image (color) to draw rows/columns/cells on
            try:
                vis = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            except Exception:
                vis = None

        contours, _ = cv2.findContours(rows_img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        row_boxes = [cv2.boundingRect(cnt) for cnt in contours]
        # Filter tiny boxes
        min_row_height_pct = float(row_settings.get("min_row_height_pct", 1.0))
        min_row_height = max(6, int(h_img * (min_row_height_pct / 100.0)))
        row_boxes = [b for b in row_boxes if b[2] > 10 and b[3] > min_row_height]
        if not row_boxes:
            logger.info("No rows detected for data_field '%s' on page %s", label, page_number)
            return []

        # Sort rows by y (top to bottom)
        row_boxes.sort(key=lambda x: x[1])

        logger.info("Detected %s rows for data_field '%s' on page %s", len(row_boxes), label, page_number)

        subcrops: list[dict[str, Any]] = []
        cell_index = 0
        
        # When using cached columns, we'll extract them from the first row's processed result
        extracted_col_boundaries: list[int] | None = None
        
        for row_idx, (rx, ry, rw, rh) in enumerate(row_boxes, start=1):
            # Crop row area from original image for column detection
            row_img = th[ry : ry + rh, rx : rx + rw]

            # Determine column boundaries for this row
            if cached_col_boundaries is not None:
                # Use cached column boundaries from first page (row-relative coordinates)
                col_boxes = []
                for i in range(len(cached_col_boundaries) - 1):
                    col_start = cached_col_boundaries[i]
                    col_end = cached_col_boundaries[i + 1]
                    col_width = col_end - col_start
                    if col_width > 0 and col_start < rw:
                        # Ensure column doesn't exceed row width
                        actual_width = min(col_width, rw - col_start)
                        col_boxes.append((col_start, 0, actual_width, rh))
                if not col_boxes:
                    col_boxes = [(0, 0, rw, rh)]
                logger.info(
                    "Row %s for '%s' page %s: using cached %s columns (boundaries: %s)", row_idx, label, page_number, len(col_boxes), cached_col_boundaries
                )
            else:
                # Auto-detect columns (for first page of this label)
                # First try to detect whole words by closing horizontally across the row
                # Use a wider kernel to merge characters into word-level blobs
                if word_kernel_divisor is not None:
                    divisor = int(word_kernel_divisor)
                else:
                    divisor = int(data_settings.get("word_kernel_divisor", 15))
                # Keep a sensible minimum kernel width for very small rows
                word_kernel_width = max(5, rw // max(1, divisor))
                word_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (word_kernel_width, 1))
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug("Using word kernel divisor=%s => kernel_width=%s for row width=%s", divisor, word_kernel_width, rw)
                words_img = cv2.morphologyEx(row_img, cv2.MORPH_CLOSE, word_kernel, iterations=1)

                # Save per-row word visualization if debugging
                if logger.isEnabledFor(logging.DEBUG):
                    try:
                        words_path = preproc_dir / f"{base_name}_row{row_idx}_words.png"
                        cv2.imwrite(str(words_path), words_img)
                        preproc_files.append(str(words_path))
                        try:
                            words_size = words_path.stat().st_size
                        except Exception:
                            words_size = None
                        logger.debug("Saved words image for row %s: path=%s size=%s shape=%s kernel_width=%s", row_idx, str(words_path), words_size, getattr(words_img, "shape", None), word_kernel_width)
                    except Exception as exc:
                        logger.debug("Failed to save words image for row %s: %s", row_idx, exc)

                contours_w, _ = cv2.findContours(words_img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                word_boxes = [cv2.boundingRect(cnt) for cnt in contours_w]
                word_boxes = [b for b in word_boxes if b[2] > 6 and b[3] > 6]

                if word_boxes:
                    # use detected word boxes as columns (word-level boxes)
                    col_boxes = sorted(word_boxes, key=lambda x: x[0])
                else:
                    # fallback to vertical grouping for columns when words not detected
                    vert_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(10, h_img // 40)))
                    cols_img = cv2.morphologyEx(row_img, cv2.MORPH_CLOSE, vert_kernel, iterations=1)

                    # Save per-row column visualization if debugging
                    if logger.isEnabledFor(logging.DEBUG):
                        try:
                            cols_path = preproc_dir / f"{base_name}_row{row_idx}_cols.png"
                            cv2.imwrite(str(cols_path), cols_img)
                            preproc_files.append(str(cols_path))
                            try:
                                cols_size = cols_path.stat().st_size
                            except Exception:
                                cols_size = None
                            logger.debug("Saved cols image for row %s: path=%s size=%s shape=%s", row_idx, str(cols_path), cols_size, getattr(cols_img, "shape", None))
                        except Exception as exc:
                            logger.debug("Failed to save cols image for row %s: %s", row_idx, exc)

                    contours_c, _ = cv2.findContours(cols_img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    col_boxes = [cv2.boundingRect(cnt) for cnt in contours_c]
                    col_boxes = [b for b in col_boxes if b[2] > 6 and b[3] > 6]
                    if not col_boxes:
                        # fallback: use the whole row as one column
                        col_boxes = [(0, 0, rw, rh)]

                # Sort columns left-to-right
                col_boxes.sort(key=lambda x: x[0])
                
                # If this is the first row, extract and cache the column boundaries (row-relative x-coordinates)
                if row_idx == 1 and cached_col_boundaries is None and col_boundaries_cache is not None:
                    # Store row-relative boundaries: just the x offsets within the row, plus row width
                    extracted_col_boundaries = [box[0] for box in col_boxes] + [rw]
                    col_boundaries_cache[label] = extracted_col_boundaries
                    logger.info("Cached %d row-relative column boundaries for '%s' from first row: %s", len(extracted_col_boundaries), label, extracted_col_boundaries)

                logger.info(
                    "Row %s for '%s' page %s: detected %s columns", row_idx, label, page_number, len(col_boxes)
                )

            # If an annotated viz was prepared, draw the row rectangle
            if logger.isEnabledFor(logging.DEBUG) and vis is not None:
                try:
                    cv2.rectangle(vis, (rx, ry), (rx + rw, ry + rh), (0, 255, 0), 1)
                except Exception:
                    pass

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

                # Save sub-image (to disk unless save_crops=False, in which case keep PNG bytes in-memory)
                cell_index += 1
                sub_filename = f"{self._sanitize_label(label)}_p{page_number}_cell{cell_index}.png"
                sub_path = output_dir / sub_filename
                image_bytes = None
                if save_crops:
                    cv2.imwrite(str(sub_path), cell_img)
                    try:
                        file_size = sub_path.stat().st_size
                    except Exception:
                        file_size = None
                    try:
                        img_shape = getattr(cell_img, "shape", None)
                    except Exception:
                        img_shape = None
                    logger.info(
                        "Saved cell image: path=%s size=%s bytes shape=%s",
                        str(sub_path),
                        file_size,
                        img_shape,
                    )
                else:
                    try:
                        ret, buf = cv2.imencode('.png', cell_img)
                        if ret:
                            image_bytes = buf.tobytes()
                            logger.debug("Created in-memory cell image for '%s' page %s: size=%s shape=%s", label, page_number, len(image_bytes), getattr(cell_img, "shape", None))
                        else:
                            image_bytes = None
                            logger.debug("Failed to encode in-memory cell image for '%s' page %s", label, page_number)
                    except Exception as exc:
                        image_bytes = None
                        logger.debug("Exception encoding in-memory cell image for '%s' page %s: %s", label, page_number, exc)

                # If annotation is enabled, draw column / cell boxes and indices
                if logger.isEnabledFor(logging.DEBUG) and vis is not None:
                    try:
                        cv2.rectangle(vis, (abs_x, abs_y), (abs_x + abs_w, abs_y + abs_h), (255, 0, 0), 1)
                        text = f"r{row_idx}c{col_idx}"
                        cv2.putText(vis, text, (abs_x, max(0, abs_y - 2)), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 0, 255), 1, cv2.LINE_AA)
                    except Exception:
                        pass

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

                # Build subcrop dict and include preproc / annotated metadata when available
                subcrop: dict[str, Any] = {
                    "label": label,
                    "page": page_number,
                    "dpi": None,
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

                # Attach either a filesystem path (when saved) or in-memory PNG bytes
                if save_crops:
                    subcrop["path"] = str(sub_path)
                else:
                    if image_bytes:
                        subcrop["image_bytes"] = image_bytes

                if preproc_files:
                    subcrop["preproc_images"] = list(preproc_files)
                if annotated_path_str:
                    subcrop["annotated_image"] = annotated_path_str

                subcrops.append(subcrop)

        # Save annotated image showing rows/columns/cells when debug enabled
        if logger.isEnabledFor(logging.DEBUG) and 'vis' in locals() and vis is not None:
            try:
                annotated_dir = output_dir / "annotated"
                annotated_dir.mkdir(parents=True, exist_ok=True)
                annotated_path = annotated_dir / f"{base_name}_annotated.png"
                # Optionally annotate the divisor used as text on the image for traceability
                if word_kernel_divisor is not None:
                    try:
                        text_div = f"divisor={word_kernel_divisor}"
                        cv2.putText(vis, text_div, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1, cv2.LINE_AA)
                    except Exception:
                        pass
                cv2.imwrite(str(annotated_path), vis)
                # expose annotated path for inclusion in metadata
                annotated_path_str = str(annotated_path)
                try:
                    ann_size = annotated_path.stat().st_size
                except Exception:
                    ann_size = None
                logger.debug(
                    "Saved annotated image for '%s' page %s: path=%s size=%s shape=%s",
                    label,
                    page_number,
                    str(annotated_path),
                    ann_size,
                    getattr(vis, "shape", None),
                )
            except Exception as exc:
                logger.debug("Failed to save annotated image for '%s' page %s: %s", label, page_number, exc)

        logger.info("Created %s sub-crops for data_field '%s' on page %s", len(subcrops), label, page_number)
        return subcrops

    def _sanitize_label(self, label: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", label.strip())
        return cleaned or "region"
