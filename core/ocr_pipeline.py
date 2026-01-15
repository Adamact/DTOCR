from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
from typing import Any

from PIL import Image


@dataclass
class OCRPipeline:
    def run(self, crops: list[dict[str, Any]], output_dir: str) -> dict[str, Any]:
        model, processor, device = self._require_trocr()
        results: list[dict[str, Any]] = []
        for crop in crops:
            image_path = crop.get("path", "")
            if not image_path:
                continue
            image = Image.open(image_path).convert("RGB")
            pixel_values = processor(images=image, return_tensors="pt").pixel_values.to(device)
            generated_ids = model.generate(pixel_values)
            text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
            results.append(
                {
                    "label": crop.get("label", ""),
                    "page": crop.get("page", 0),
                    "path": image_path,
                    "text": text.strip(),
                }
            )
        output_path = self._write_results(results, output_dir)
        return {"results": results, "ocr_output_path": str(output_path)}

    def _require_trocr(self):
        try:
            import torch  # type: ignore
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "TrOCR requires torch and transformers; install them before running OCR."
            ) from exc
        if not hasattr(self, "_trocr_model"):
            processor = TrOCRProcessor.from_pretrained("microsoft/trocr-large-printed")
            model = VisionEncoderDecoderModel.from_pretrained("microsoft/trocr-large-printed")
            device = torch.device("cpu")
            model.to(device)
            model.eval()
            self._trocr_model = model
            self._trocr_processor = processor
            self._trocr_device = device
        return self._trocr_model, self._trocr_processor, self._trocr_device

    def _write_results(self, results: list[dict[str, Any]], output_dir: str) -> Path:
        output_path = Path(output_dir) / "ocr_results.json"
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(results, handle, ensure_ascii=True, indent=2)
        return output_path
