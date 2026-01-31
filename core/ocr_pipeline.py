from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
from typing import Any

from PIL import Image


@dataclass
class OCRPipeline:
    def run(self, crops: list[dict[str, Any]], output_dir: str, excel_path: str | None = None) -> dict[str, Any]:
        import torch  # type: ignore
        import logging
        import sys

        logger = logging.getLogger(__name__)

        model, processor, device = self._require_trocr()

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
            logger.info("Starting OCR: %s cells across %s pages (estimated work=%s)", total_cells, total_pages, total_work)
            _print_progress(0, total_work)

        with torch.inference_mode():
            for crop in crops:
                image_path = crop.get("path", "")
                if not image_path:
                    continue

                image = Image.open(image_path).convert("RGB")
                pixel_values = processor(images=image, return_tensors="pt").pixel_values.to(device)

                generated_ids = model.generate(pixel_values)
                text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
                cleaned = text.strip()

                result_item = {
                    "label": crop.get("label", ""),
                    "page": crop.get("page", 0),
                    "path": image_path,
                    "text": cleaned,
                }
                results.append(result_item)

                # Log and print parsed content for traceability
                logger.info(
                    "OCR parsed: label=%s page=%s path=%s text=%s",
                    result_item["label"],
                    result_item["page"],
                    result_item["path"],
                    cleaned,
                )
                try:
                    # Print a concise, readable block to the console
                    print(f"OCR parsed | label={result_item['label']} | page={result_item['page']} | path={result_item['path']}")
                    print(cleaned)
                    print("---")
                except Exception:
                    # Avoid any print errors affecting OCR run
                    logger.debug("Failed to print OCR result to stdout for %s", result_item["path"]) 

                # Update progress: cells + pages
                processed_cells += 1
                page_num = int(result_item.get("page", 0))
                if page_num and page_num not in processed_pages_seen:
                    processed_pages_seen.add(page_num)
                processed_pages = len(processed_pages_seen)
                work_done = processed_cells + processed_pages
                if total_work > 0:
                    _print_progress(work_done, total_work)
                    logger.debug("Progress update: done=%s/%s (cells=%s pages=%s)", work_done, total_work, processed_cells, processed_pages)

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
            model_name = "microsoft/trocr-large-printed"

            # Force CPU early and avoid any lazy/meta device behavior.
            device = torch.device("cpu")

            processor = TrOCRProcessor.from_pretrained(model_name)

            model = VisionEncoderDecoderModel.from_pretrained(
                model_name,
                low_cpu_mem_usage=False,
                dtype=torch.float32,
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
                        f"Model parameter '{name}' is on meta device. "
                        "This indicates lazy loading. Try upgrading transformers/torch "
                        "or disable accelerate/device_map usage."
                    )

            self._trocr_model = model
            self._trocr_processor = processor
            self._trocr_device = device

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
        df = pd.DataFrame(results)
        excel_p = Path(excel_path)
        excel_p.parent.mkdir(parents=True, exist_ok=True)
        df.to_excel(excel_p, index=False, engine="openpyxl")
        return excel_p
