from __future__ import annotations

import json
from pathlib import Path

from web.worker.pipeline_adapter import run_pipeline

if __name__ == "__main__":
    template_payload = json.loads(Path("data/vendor_configs/example_vendor.json").read_text(encoding="utf-8"))

    pdf_path = r"C:\path\to\some.pdf"  # change
    result = run_pipeline(
        template_payload=template_payload,
        pdf_path=pdf_path,
        excel_path=None,
        save_crops=True,
        batch_size=8,
        num_beams=1,
    )

    print("OK. Keys:", list(result.keys()))
