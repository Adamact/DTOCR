from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
from typing import Any

from PIL import Image


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

        with torch.no_grad():
            for batch in _batches(crops, batch_size):
                # Concurrently load images for this batch to reduce IO latency
                import io

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
                    for crop, img, meta_path in ex.map(_load_image_for_crop, batch):
                        imgs.append(img)
                        metas.append((crop, meta_path))

                # Prepare batch tensors (filter out None images)
                valid_imgs = [im for im in imgs if im is not None]
                if not valid_imgs:
                    for crop, _ in metas:
                        results.append({"label": crop.get("label", ""), "page": crop.get("page", 0), "path": crop.get("path", ""), "text": ""})
                        processed_cells += 1
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
                    result_item = {"label": crop.get("label", ""), "page": crop.get("page", 0), "path": crop.get("path", ""), "text": text}
                    results.append(result_item)

                    # Log parsed content for traceability and print OCR text to stdout.
                    logger.info("OCR parsed (trocr): label=%s page=%s path=%s", result_item["label"], result_item["page"], result_item.get("path", "<in-memory>"))
                    print(f"OCR: label={result_item['label']} page={result_item['page']} text={text}")
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug("Parsed text for %s: %s", result_item.get("path", "<in-memory>"), text)

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
            import logging
            logger = logging.getLogger(__name__)
            
            model_name = "microsoft/trocr-base-printed"

            # Prefer GPU if available: try DirectML first (for AMD on Windows), then CUDA, then CPU
            try:
                import torch_directml
                device = torch_directml.device()
                device_name = "DirectML (AMD GPU)"
                logger.info("Using DirectML device for GPU acceleration")
            except Exception:
                device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
                device_name = str(device)
                logger.info("Using device: %s", device_name)

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
        df = pd.DataFrame(results)
        excel_p = Path(excel_path)
        excel_p.parent.mkdir(parents=True, exist_ok=True)
        df.to_excel(excel_p, index=False, engine="openpyxl")
        return excel_p
