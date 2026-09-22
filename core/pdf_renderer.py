"""PDF rendering and text extraction using pypdfium2."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

from PIL import Image


def _get_pdfium() -> Any:
    try:
        import pypdfium2 as pdfium  # type: ignore
    except Exception as exc:
        raise RuntimeError("pypdfium2 is required for PDF rendering") from exc
    return pdfium


def scale_from_dpi(dpi: int) -> float:
    return float(dpi) / 72.0


@dataclass
class PdfRect:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return float(self.x1 - self.x0)

    @property
    def height(self) -> float:
        return float(self.y1 - self.y0)


@dataclass
class PdfPixmap:
    image: Any

    @property
    def width(self) -> int:
        return int(self.image.width)

    @property
    def height(self) -> int:
        return int(self.image.height)

    @property
    def samples(self) -> bytes:
        img = self.image
        if getattr(img, "mode", "RGB") != "RGB":
            img = img.convert("RGB")
        return img.tobytes()

    def save(self, path: str) -> None:
        self.image.save(path)

    def tobytes(self, fmt: str = "png") -> bytes:
        fmt_lower = fmt.lower()
        if fmt_lower in ("jpg", "jpeg"):
            pil_fmt = "JPEG"
        elif fmt_lower == "png":
            pil_fmt = "PNG"
        else:
            pil_fmt = fmt.upper()
        buffer = io.BytesIO()
        self.image.save(buffer, format=pil_fmt)
        return buffer.getvalue()


class PdfPage:
    def __init__(self, page: Any) -> None:
        self._page = page

    @property
    def rect(self) -> PdfRect:
        width, height = self._page.get_size()
        return PdfRect(0.0, 0.0, float(width), float(height))

    @property
    def width(self) -> float:
        return float(self._page.get_size()[0])

    @property
    def height(self) -> float:
        return float(self._page.get_size()[1])

    def render_pixmap(self, scale: float = 1.0, clip: PdfRect | None = None) -> PdfPixmap:
        bitmap = self._page.render(scale=scale)
        try:
            image = bitmap.to_pil()
        except Exception:
            try:
                image = Image.fromarray(bitmap.to_numpy())
            except Exception:
                image = Image.frombytes("RGB", (bitmap.width, bitmap.height), bitmap.tobytes())
        if clip is not None:
            left = max(0, int(round(clip.x0 * scale)))
            top_px = max(0, int(round(clip.y0 * scale)))
            right = max(left + 1, int(round(clip.x1 * scale)))
            bottom = max(top_px + 1, int(round(clip.y1 * scale)))
            image = image.crop((left, top_px, right, bottom))
        return PdfPixmap(image=image)

    def extract_text_payload(self, rect: PdfRect) -> dict[str, Any]:
        try:
            textpage = self._page.get_textpage()
        except Exception:
            return {"text": "", "words": []}

        page_height = float(self.height)
        left = float(rect.x0)
        right = float(rect.x1)
        top = float(rect.y0)
        bottom = float(rect.y1)
        pdf_bottom = max(0.0, page_height - bottom)
        pdf_top = max(pdf_bottom, page_height - top)

        text = ""
        try:
            if hasattr(textpage, "get_text_bounded"):
                text = textpage.get_text_bounded(left, pdf_bottom, right, pdf_top) or ""
            if not text and hasattr(textpage, "get_text_range"):
                # Fallback to full-page text if bounded extraction is unavailable.
                text = textpage.get_text_range() or ""
        except Exception:
            text = ""

        return {"text": text.strip(), "words": []}


class PdfDocument:
    def __init__(self, path: str) -> None:
        pdfium = _get_pdfium()
        self._doc = pdfium.PdfDocument(path)

    @property
    def page_count(self) -> int:
        try:
            return int(len(self._doc))
        except Exception:
            return int(self._doc.page_count)

    def load_page(self, index: int) -> PdfPage:
        page = self._doc.get_page(index)
        return PdfPage(page)

    def close(self) -> None:
        try:
            self._doc.close()
        except Exception:
            pass
