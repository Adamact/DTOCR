from __future__ import annotations

from DTOCR.services.service_factory import create_template_service  # type: ignore
from DTOCR.web.worker.pipeline_adapter import run_pipeline  # type: ignore


if __name__ == "__main__":
    service = create_template_service()

    templates = service.list_templates()
    print("Templates in DB:", templates)

    if not templates:
        raise RuntimeError(
            "No templates found in the DB used by service_factory.\n"
            "Open the GUI and create + save a template version first, or point service_factory to your existing DB file."
        )

    # NOTE: keys depend on your repository output. Common keys: 'id' or 'template_id'
    first = templates[0]
    template_id = int(first.get("id") or first.get("template_id"))
    print("Using template_id:", template_id)

    template_payload = service.load_latest_template_payload(template_id=template_id)
    if not template_payload:
        raise RuntimeError(f"Template exists but has no saved payload versions. template_id={template_id}")

    pdf_path = r"path/to/document.pdf"
    result = run_pipeline(
        template_payload=template_payload,
        pdf_path=pdf_path,
        excel_path=None,
        save_crops=True,
        batch_size=8,
        num_beams=1,
    )

    print("OK. Keys:", list(result.keys()))
    print("OCR output path:", result.get("ocr_output_path"))
    print("Excel output path:", result.get("excel_output_path"))
