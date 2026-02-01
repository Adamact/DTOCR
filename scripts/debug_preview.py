"""Debug helper: run TemplateService._split_data_field_region on a generated image and print outputs.

Usage: python scripts/debug_preview.py
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
import sys
# Ensure project root is importable when running the script directly
# Insert the parent directory of project root so 'DTOCR' package dir is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from PIL import Image, ImageDraw
except Exception:
    Image = None

from DTOCR.services.template_service import TemplateService
from DTOCR.core.logging import configure_logging


def main() -> int:
    configure_logging(logging.DEBUG)
    # create a simple test image containing a few words
    if Image is None:
        print("Pillow not installed - please install pillow to run this test")
        return 2
    img = Image.new("L", (800, 200), color=255)
    draw = ImageDraw.Draw(img)
    draw.text((10, 10), "Hello world this is a test", fill=0)
    draw.text((10, 60), "Another line with words here", fill=0)

    outdir = Path(tempfile.mkdtemp(prefix="dtocr_debug_preview_"))
    img_path = outdir / "test.png"
    img.save(img_path)
    print("Wrote test image to", img_path)

    service = TemplateService(template_repo=None)  # type: ignore[arg-type]
    try:
        subcrops = service._split_data_field_region(str(img_path), "data_field_debug", 1, outdir, word_kernel_divisor=10)
    except Exception as exc:
        print("_split_data_field_region raised:", exc)
        import traceback

        traceback.print_exc()
        return 1

    print("Returned subcrops:")
    for s in subcrops:
        print(s)

    print("Files in outdir:")
    for p in sorted(outdir.rglob("*")):
        print(p.relative_to(outdir))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
