from __future__ import annotations

from typing import Any, Optional

from services.template_service import TemplateService


def run_pipeline(
    template_payload: dict[str, Any],
    pdf_path: str,
    excel_path: Optional[str] = None,
    word_kernel_divisor: Optional[int] = None,
    save_crops: bool = True,
    batch_size: int = 8,
    num_beams: int = 1,
) -> dict[str, Any]:
    """
    Headless adapter for running the existing DTOCR pipeline from a background worker.

    This function is the single integration point between the web stack and your current codebase.
    """
    service = TemplateService()

    result = service.run_ocr_pipeline(
        template_payload=template_payload,
        pdf_path=pdf_path,
        excel_path=excel_path,
        word_kernel_divisor=word_kernel_divisor,
        save_crops=save_crops,
        batch_size=batch_size,
        num_beams=num_beams,
    )
    return result
