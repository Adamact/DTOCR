from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re
from typing import Any

from PIL import Image

from DTOCR.core import text_parsers


@dataclass
class OCRPipeline:
    def run(self, crops: list[dict[str, Any]], output_dir: str, excel_path: str | None = None, batch_size: int = 8, num_beams: int = 1, max_length: int = 128, image_load_workers: int = 4) -> dict[str, Any]:
        import logging
        import sys
        import torch  # type: ignore
        from concurrent.futures import ThreadPoolExecutor

        logger = logging.getLogger(__name__)

        model, processor, device = self._require_trocr()
        logger.info("OCR device: %s", device)

        results: list[dict[str, Any]] = []

        # Estimate work: combine unique pages and number of cells (crops)
        pages = [int(c.get("page", 0)) for c in crops]
        unique_pages = set(pages)
        total_cells = len(crops)
        total_pages = len(unique_pages)
        total_work = total_cells + total_pages if (total_cells + total_pages) > 0 else 0

        processed_cells = 0
        processed_pages_seen: set[int] = set()

        def _print_progress(done: int, total: int) -> None:
            if total <= 0:
                return
            percent = int((done / total) * 100)
            bar_len = 40
            filled = int((done / total) * bar_len)
            bar = "█" * filled + "-" * (bar_len - filled)
            sys.stdout.write(f"\rProgress: |{bar}| {percent}% ({done}/{total})")
            sys.stdout.flush()

        if total_work > 0:
            logger.info("Starting OCR (TrOCR): %s cells across %s pages (estimated work=%s)", total_cells, total_pages, total_work)
            _print_progress(0, total_work)

        # Process in batches for speed
        def _batches(iterable, n):
            for i in range(0, len(iterable), n):
                yield iterable[i : i + n]

        def _append_result(result_item: dict[str, Any]) -> None:
            nonlocal processed_cells, processed_pages_seen
            results.append(result_item)
            page_num = int(result_item.get("page", 0))
            processed_cells += 1
            if page_num and page_num not in processed_pages_seen:
                processed_pages_seen.add(page_num)
            processed_pages = len(processed_pages_seen)
            work_done = processed_cells + processed_pages
            if total_work > 0:
                _print_progress(work_done, total_work)
                logger.debug("Progress update: done=%s/%s (cells=%s pages=%s)", work_done, total_work, processed_cells, processed_pages)

        with torch.no_grad():
            for batch in _batches(crops, batch_size):
                # Concurrently load images for this batch to reduce IO latency
                import io

                image_batch = []
                for crop in batch:
                    text = str(crop.get("text", "")).strip()
                    has_image = bool(crop.get("image_bytes") or crop.get("path"))
                    if text and not has_image:
                        result_item = {
                            "label": crop.get("label", ""),
                            "page": crop.get("page", 0),
                            "path": crop.get("path", ""),
                            "text": text,
                            "text_source": crop.get("text_source"),
                            "structured_rows": crop.get("structured_rows"),
                            "structured_cols": crop.get("structured_cols"),
                            "grid_id": crop.get("grid_id"),
                            "grid_label": crop.get("grid_label"),
                            "row_index": crop.get("row_index"),
                            "col_index": crop.get("col_index"),
                            "column_label": crop.get("column_label"),
                            "cell_label": crop.get("cell_label"),
                        }
                        _append_result(result_item)
                    else:
                        image_batch.append(crop)

                def _load_image_for_crop(crop):
                    try:
                        image_bytes = crop.get("image_bytes")
                        image_path = crop.get("path", "")
                        if image_bytes:
                            try:
                                img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
                                return (crop, img, "<in-memory>")
                            except Exception as exc:  # pragma: no cover - IO error paths
                                logger.warning("Failed to open in-memory image for OCR: %s", exc)
                                return (crop, None, None)
                        elif image_path:
                            try:
                                img = Image.open(image_path).convert("RGB")
                                return (crop, img, image_path)
                            except Exception as exc:  # pragma: no cover - IO error paths
                                logger.warning("Failed to open image for OCR: %s (%s)", image_path, exc)
                                return (crop, None, None)
                        else:
                            return (crop, None, None)
                    except Exception as exc:  # pragma: no cover - defensive
                        logger.warning("Unexpected error while loading image: %s", exc)
                        return (crop, None, None)

                imgs = []
                metas = []
                # Use a thread pool to load images in parallel
                with ThreadPoolExecutor(max_workers=image_load_workers) as ex:
                    for crop, img, meta_path in ex.map(_load_image_for_crop, image_batch):
                        imgs.append(img)
                        metas.append((crop, meta_path))

                # Prepare batch tensors (filter out None images)
                valid_imgs = [im for im in imgs if im is not None]
                if not valid_imgs:
                    for crop, _ in metas:
                        _append_result(
                            {
                                "label": crop.get("label", ""),
                                "page": crop.get("page", 0),
                                "path": crop.get("path", ""),
                                "text": "",
                                "text_source": crop.get("text_source"),
                                "structured_rows": crop.get("structured_rows"),
                                "structured_cols": crop.get("structured_cols"),
                                "grid_id": crop.get("grid_id"),
                                "grid_label": crop.get("grid_label"),
                                "row_index": crop.get("row_index"),
                                "col_index": crop.get("col_index"),
                                "column_label": crop.get("column_label"),
                                "cell_label": crop.get("cell_label"),
                            }
                        )
                    continue

                # Process with PyTorch model on DirectML device
                pixel_values = processor(images=valid_imgs, return_tensors="pt", padding=True).pixel_values.to(device)
                try:
                    generated_ids = model.generate(pixel_values, num_beams=num_beams, max_length=max_length)
                    texts = processor.batch_decode(generated_ids, skip_special_tokens=True)
                except Exception as exc:
                    logger.warning("Model.generate failed on batch: %s", exc)
                    texts = ["" for _ in valid_imgs]

                # Map back texts to metas in order
                text_iter = iter(texts)
                for crop, img_path in metas:
                    if img_path is None:
                        text = ""
                    else:
                        text = next(text_iter, "").strip()
                    result_item = {
                        "label": crop.get("label", ""),
                        "page": crop.get("page", 0),
                        "path": crop.get("path", ""),
                        "text": text,
                        "text_source": crop.get("text_source"),
                        "structured_rows": crop.get("structured_rows"),
                        "structured_cols": crop.get("structured_cols"),
                        "grid_id": crop.get("grid_id"),
                        "grid_label": crop.get("grid_label"),
                        "row_index": crop.get("row_index"),
                        "col_index": crop.get("col_index"),
                        "column_label": crop.get("column_label"),
                        "cell_label": crop.get("cell_label"),
                    }
                    _append_result(result_item)

                    # Log parsed content for traceability and print OCR text to stdout.
                    logger.info("OCR parsed (trocr): label=%s page=%s path=%s", result_item["label"], result_item["page"], result_item.get("path", "<in-memory>"))
                    print(f"OCR: label={result_item['label']} page={result_item['page']} text={text}")
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug("Parsed text for %s: %s", result_item.get("path", "<in-memory>"), text)

                    # Update progress: cells + pages
                    # progress handled in _append_result
        # Finalize progress bar
        if total_work > 0:
            _print_progress(total_work, total_work)
            print("")
            logger.info("OCR progress complete: processed %s work units", total_work)

        output_path = self._write_results(results, output_dir, excel_path)
        logger.info("Wrote OCR JSON to %s", output_path)
        print(f"OCR results written to: {output_path}")
        if excel_path:
            logger.info("Wrote OCR Excel to %s", excel_path)
            print(f"OCR Excel written to: {excel_path}")

        resp = {"results": results, "ocr_output_path": str(output_path)}
        if excel_path:
            resp["excel_output_path"] = str(Path(excel_path))
        return resp

    def _require_trocr(self):
        try:
            import torch  # type: ignore
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "TrOCR requires torch and transformers; install them before running OCR."
            ) from exc

        if not hasattr(self, "_trocr_model"):
            import logging
            logger = logging.getLogger(__name__)
            
            model_name = "microsoft/trocr-base-printed"

            # Prefer GPU if available: try CUDA first, then DirectML (AMD/Intel), then CPU
            device = None
            device_name = "cpu"
            try:
                if torch.cuda.is_available():
                    device = torch.device("cuda")
                    device_name = "cuda"
                    logger.info("Using CUDA device for GPU acceleration")
            except Exception as exc:
                logger.info("CUDA check failed: %s", exc)

            if device is None:
                try:
                    import torch_directml  # type: ignore
                    device = torch_directml.device()
                    device_name = "DirectML"
                    logger.info("Using DirectML device for GPU acceleration")
                except Exception as exc:
                    logger.info("DirectML unavailable: %s", exc)

            if device is None:
                device = torch.device("cpu")
                device_name = "cpu"
                logger.info("Using CPU device for OCR")

            processor = TrOCRProcessor.from_pretrained(model_name)

            model = VisionEncoderDecoderModel.from_pretrained(
                model_name,
                low_cpu_mem_usage=False,
                torch_dtype=torch.float32,
            )
            bad = [n for n, p in model.named_parameters() if p.device.type == "meta"]
            if bad:
                raise RuntimeError(f"Model has meta parameters (first 5): {bad[:5]}")

            model.to(device)
            model.eval()

            # Extra safety: ensure no parameter is on 'meta'
            for name, p in model.named_parameters():
                if getattr(p, "device", None) is not None and p.device.type == "meta":
                    raise RuntimeError(
                        f"Model parameter '{name}' is on meta device. This indicates lazy loading. Try upgrading transformers/torch or disable accelerate/device_map usage."
                    )

            self._trocr_model = model
            self._trocr_processor = processor
            self._trocr_device = device
            self._trocr_device_name = device_name

        return self._trocr_model, self._trocr_processor, self._trocr_device


    def _write_results(self, results: list[dict[str, Any]], output_dir: str, excel_path: str | None = None) -> Path:
        output_path = Path(output_dir) / "ocr_results.json"
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(results, handle, ensure_ascii=True, indent=2)
        if excel_path:
            self._export_to_excel(results, excel_path)
        return output_path

    def _export_to_excel(self, results: list[dict[str, Any]], excel_path: str) -> Path:
        try:
            import pandas as pd  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "Exporting OCR results to Excel requires 'pandas' and 'openpyxl' packages. Install them with: pip install pandas openpyxl"
            ) from exc
        
        excel_p = Path(excel_path)
        excel_p.parent.mkdir(parents=True, exist_ok=True)

        # Separate grid results from non-grid results
        grid_results = [r for r in results if r.get("grid_id") is not None and r.get("row_index") is not None]
        non_grid_results = [r for r in results if r.get("grid_id") is None]
        parsed_rows: list[dict[str, Any]] = []
        carry_by_label: dict[str, dict[str, Any] | None] = {}
        def _text_to_rows(text: str) -> list[list[str]]:
            rows: list[list[str]] = []
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                parts = re.split(r"\s{2,}|\t+", stripped)
                cells = [p.strip() for p in parts if p.strip()]
                if cells:
                    rows.append(cells)
            return rows

        structured_items = [
            item
            for item in non_grid_results
            if isinstance(item.get("structured_rows"), list) or isinstance(item.get("text"), str)
        ]
        structured_items.sort(key=lambda i: (str(i.get("label", "")), int(i.get("page", 0))))
        parser_type = text_parsers.detect_parser_type(non_grid_results)
        for item in structured_items:
            structured_rows = item.get("structured_rows")
            if not isinstance(structured_rows, list) or not structured_rows:
                text_value = item.get("text")
                if not isinstance(text_value, str) or not text_value.strip():
                    continue
                structured_rows = _text_to_rows(text_value)
                if not structured_rows:
                    continue
            label = str(item.get("label", ""))
            if not label.startswith("data_field"):
                continue
            carry = carry_by_label.get(label)
            if parser_type == "layout-b":
                records, carry = text_parsers.parse_structured_rows_layout_b_with_carry(structured_rows, carry)
            else:
                records, carry = text_parsers.parse_structured_rows_with_carry(structured_rows, carry)
            carry_by_label[label] = carry
            for record in records:
                record["label"] = label
                record["page"] = item.get("page")
                parsed_rows.append(record)
        for label, carry in carry_by_label.items():
            if carry:
                carry["label"] = label
                parsed_rows.append(carry)

        def _has_value(value: Any) -> bool:
            if value is None:
                return False
            if isinstance(value, bool):
                return value is True
            return str(value).strip() != ""

        def _keep_row(row: dict[str, Any]) -> bool:
            bilnr = row.get("bilnr")
            levdag = row.get("levdag")
            return _has_value(bilnr) and _has_value(levdag)

        if parsed_rows:
            if parser_type == "layout-b":
                parsed_rows = [row for row in parsed_rows if _keep_row(row)]
            else:
                parsed_rows = [
                    row
                    for row in parsed_rows
                    if _has_value(row.get("bilregnr")) and _has_value(row.get("delivery_date"))
                ]
        
        if not grid_results and not parsed_rows:
            # If no grid or parsed results, export non-grid results with minimal columns
            minimal_rows = [
                {"label": r.get("label", ""), "page": r.get("page", 0), "text": r.get("text", "")}
                for r in non_grid_results
            ]
            df = pd.DataFrame(minimal_rows)
            df.to_excel(excel_p, index=False, engine="openpyxl")
            return excel_p

        # Group results by grid_label (each grid gets its own sheet)
        from collections import defaultdict
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in grid_results:
            grid_label = str(item.get("grid_label", "Grid"))
            groups[grid_label].append(item)

        def _sheet_name(name: str) -> str:
            cleaned = "".join(ch for ch in name if ch not in r"[]:*?/\\")
            return cleaned[:31] or "Grid"

        with pd.ExcelWriter(excel_p, engine="openpyxl") as writer:
            # Write structured text results (parsed via regex) if available
            if parsed_rows:
                df_parsed = pd.DataFrame(parsed_rows)
                if parser_type == "layout-b":
                    preferred = [
                        "foljesedel",
                        "levdag",
                        "bilnr",
                        "produktnamn",
                        "enhet",
                        "kvantitet",
                        "a_pris",
                        "belopp_sek",
                        "takt_miljoavgift",
                        "vintertillagg",
                    ]
                    sheet_name = "Parsed_Text_LayoutB"
                else:
                    preferred = ["delivery_date", "bilregnr", "description", "qty", "unit_price", "amount"]
                    sheet_name = "Parsed_Text"
                ordered = [col for col in preferred if col in df_parsed.columns]
                remaining = [col for col in df_parsed.columns if col not in ordered]
                df_parsed = df_parsed[ordered + remaining]
                df_parsed.to_excel(writer, index=False, sheet_name=sheet_name)

            # Write non-grid results with minimal columns (no meta)
            if non_grid_results:
                minimal_rows = [
                    {"label": r.get("label", ""), "page": r.get("page", 0), "text": r.get("text", "")}
                    for r in non_grid_results
                ]
                df = pd.DataFrame(minimal_rows)
                df.to_excel(writer, index=False, sheet_name="OCR_Results")

            # For each grid, map directly: template row N → Excel row N, template col M → Excel col M
            # Pages after the first continue filling rows
            for grid_label, items in groups.items():
                # Get all unique columns and rows
                cols = sorted(
                    {
                        (int(r.get("col_index", 0)), str(r.get("column_label", f"Column {int(r.get('col_index', 0)) + 1}")))
                        for r in items
                    },
                    key=lambda x: x[0],
                )
                col_labels = [label for _, label in cols]
                col_map = {idx: label for idx, label in cols}

                # Get all unique rows across all pages
                rows = sorted({int(r.get("row_index", 0)) for r in items})
                
                # Get page numbers and their starting row in Excel
                pages = sorted({int(r.get("page_number", r.get("page", 0))) for r in items if int(r.get("page_number", r.get("page", 0))) > 0})

                # Build table with rows continuing across pages
                table_rows: list[dict[str, Any]] = []
                for page_num in pages:
                    for template_row_idx in rows:
                        row_dict = {"page": page_num}
                        row_dict.update({label: "" for label in col_labels})
                        for item in items:
                            if int(item.get("page_number", item.get("page", 0))) != page_num or int(item.get("row_index", 0)) != template_row_idx:
                                continue
                            col_idx = int(item.get("col_index", 0))
                            col_label = col_map.get(col_idx, f"Column {col_idx + 1}")
                            row_dict[col_label] = item.get("text", "")
                        table_rows.append(row_dict)

                grid_df = pd.DataFrame(table_rows)
                sheet_name = _sheet_name(grid_label)
                grid_df.to_excel(writer, index=False, sheet_name=sheet_name)

        return excel_p

