from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .pipeline_adapter import run_pipeline


def process_run(run_payload_path: str, result_path: str) -> None:
    """
    Worker entrypoint.
    - Reads run payload (PDF path + template payload)
    - Runs the OCR pipeline
    - Writes result JSON to disk
    """
    payload = _read_json(run_payload_path)

    template_payload: dict[str, Any] = payload["template_payload"]
    pdf_path: str = payload["pdf_path"]
    excel_path: str | None = payload.get("excel_path")
    word_kernel_divisor: int | None = payload.get("word_kernel_divisor")
    save_crops: bool = payload.get("save_crops", True)
    batch_size: int = payload.get("batch_size", 8)
    num_beams: int = payload.get("num_beams", 1)

    result = run_pipeline(
        template_payload=template_payload,
        pdf_path=pdf_path,
        excel_path=excel_path,
        word_kernel_divisor=word_kernel_divisor,
        save_crops=save_crops,
        batch_size=batch_size,
        num_beams=num_beams,
    )

    _write_json(result_path, result)


def _read_json(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: str, data: dict[str, Any]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
