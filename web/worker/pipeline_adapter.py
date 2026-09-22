from __future__ import annotations

from typing import Any

from DTOCR.services.service_factory import create_template_service  # type: ignore


def run_pipeline(
    template_payload: dict[str, Any],
    pdf_path: str,
    excel_path: str | None = None,
    word_kernel_divisor: int | None = None,
    save_crops: bool = True,
    batch_size: int = 8,
    num_beams: int = 1,
) -> dict[str, Any]:
    service = create_template_service()
    return service.run_ocr_pipeline(
        template_payload=template_payload,
        pdf_path=pdf_path,
        excel_path=excel_path,
        word_kernel_divisor=word_kernel_divisor,
        save_crops=save_crops,
        batch_size=batch_size,
        num_beams=num_beams,
    )
