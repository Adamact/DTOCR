"""Grid structure auto-detection from images using contour analysis."""

from __future__ import annotations

import copy
import logging
from typing import Any

DEFAULT_DETECTION_SETTINGS: dict[str, Any] = {
    "row": {
        "content_threshold": 0.20,
        "min_row_height_pct": 1.0,
    },
    "col": {
        "white_threshold": 0.001,
        "gap_width_px": 20,
        "min_col_width_pct": 1.0,
        "content_threshold": 0.05,
    },
    "fallback": {
        "row_kernel_divisor": 30,
        "word_kernel_divisor": 12,
    },
    "data_field": {
        "row_kernel_divisor": 40,
        "word_kernel_divisor": 15,
    },
}


def default_detection_settings() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_DETECTION_SETTINGS)


def merge_detection_settings(overrides: dict[str, Any] | None) -> dict[str, Any]:
    settings = default_detection_settings()
    if not overrides:
        return settings

    def _merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
        for key, val in src.items():
            if isinstance(val, dict) and isinstance(dst.get(key), dict):
                _merge(dst[key], val)
            else:
                dst[key] = val

    _merge(settings, overrides)

    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(value, high))

    row = settings.get("row", {})
    row["content_threshold"] = _clamp(float(row.get("content_threshold", 0.20)), 0.01, 0.90)
    row["min_row_height_pct"] = _clamp(float(row.get("min_row_height_pct", 1.0)), 0.1, 25.0)

    col = settings.get("col", {})
    col["white_threshold"] = _clamp(float(col.get("white_threshold", 0.001)), 0.0001, 0.10)
    col["gap_width_px"] = int(_clamp(float(col.get("gap_width_px", 20)), 2.0, 200.0))
    col["min_col_width_pct"] = _clamp(float(col.get("min_col_width_pct", 1.0)), 0.1, 25.0)
    col["content_threshold"] = _clamp(float(col.get("content_threshold", 0.05)), 0.001, 0.50)

    fallback = settings.get("fallback", {})
    fallback["row_kernel_divisor"] = int(_clamp(float(fallback.get("row_kernel_divisor", 30)), 5.0, 120.0))
    fallback["word_kernel_divisor"] = int(_clamp(float(fallback.get("word_kernel_divisor", 12)), 5.0, 80.0))

    data_field = settings.get("data_field", {})
    data_field["row_kernel_divisor"] = int(_clamp(float(data_field.get("row_kernel_divisor", 40)), 5.0, 120.0))
    data_field["word_kernel_divisor"] = int(_clamp(float(data_field.get("word_kernel_divisor", 15)), 5.0, 80.0))

    settings["row"] = row
    settings["col"] = col
    settings["fallback"] = fallback
    settings["data_field"] = data_field
    return settings


def auto_detect_grid_structure(image_path: str, label: str, detection_settings: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Auto-detect grid structure by finding gaps between rows and columns.
    
    Scans the image to find white/light areas that separate rows and columns,
    then uses those boundaries to define the grid structure.
    """
    logger = logging.getLogger(__name__)
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore  # noqa: F401
    except Exception as exc:
        logger.warning("OpenCV or numpy not available for auto grid detection: %s", exc)
        return None
    
    try:
        img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            logger.warning("Failed to read image for grid detection: %s", image_path)
            return None
        
        h_img, w_img = img.shape[:2]
        
        # Threshold to get binary image
        _, binary = cv2.threshold(img, 127, 255, cv2.THRESH_BINARY)
        
        settings = merge_detection_settings(detection_settings)

        # Find row boundaries by scanning for light horizontal bands
        row_boundaries = _find_row_boundaries(binary, h_img, w_img, settings)
        if not row_boundaries:
            logger.warning("Could not detect row boundaries")
            return _detect_grid_from_content(img, h_img, w_img, logger)
        
        # Find column boundaries by scanning for light vertical bands
        col_boundaries = _find_col_boundaries(binary, h_img, w_img, settings)
        if not col_boundaries:
            logger.warning("Could not detect column boundaries")
            return _detect_grid_from_content(img, h_img, w_img, logger, settings)
        
        num_rows = len(row_boundaries) - 1
        num_cols = len(col_boundaries) - 1
        
        logger.info("Auto-detected grid from gaps: %d rows × %d columns", num_rows, num_cols)
        
        if num_rows < 1 or num_cols < 1:
            logger.warning("Grid detection found invalid dimensions")
            return _detect_grid_from_content(img, h_img, w_img, logger, settings)
        
        # Calculate adaptive row heights from boundaries
        row_heights = []
        for i in range(len(row_boundaries) - 1):
            height = row_boundaries[i + 1] - row_boundaries[i]
            row_heights.append(height)
        
        # Calculate adaptive column widths from boundaries
        col_widths = []
        for i in range(len(col_boundaries) - 1):
            width = col_boundaries[i + 1] - col_boundaries[i]
            col_widths.append(width)
        
        # Normalize to ratios
        total_height = sum(row_heights)
        total_width = sum(col_widths)
        
        row_ratios = [h / total_height for h in row_heights] if total_height > 0 else [1.0 / num_rows] * num_rows
        col_ratios = [w / total_width for w in col_widths] if total_width > 0 else [1.0 / num_cols] * num_cols
        
        grid: dict[str, Any] = {
            "type": "auto_detected_gaps",
            "rows": [{"height_ratio": r} for r in row_ratios],
            "columns": [
                {"label": f"Column {idx + 1}", "width_ratio": r}
                for idx, r in enumerate(col_ratios)
            ],
            "cell_labels": {},
            "detection_info": {
                "rows": num_rows,
                "columns": num_cols,
                "method": "gap_finding",
                "row_boundaries": row_boundaries,
                "col_boundaries": col_boundaries,
            }
        }
        
        return grid
    
    except Exception as exc:
        logger.error("Error during grid structure auto-detection: %s", exc)
        return None


def _find_row_boundaries(binary: Any, h_img: int, w_img: int, settings: dict[str, Any]) -> list[int]:
    """Find row boundaries ensuring padding above and below rows without data overlap.
    
    Detects content regions and places boundaries in gaps between them.
    Each boundary is positioned in the middle of a gap to provide padding on both sides.
    Returns y-coordinates of row starts.
    """
    try:
        import numpy as np  # type: ignore
    except Exception:
        return []
    
    # Calculate darkness per row (sum of black pixels)
    darkness = np.sum(binary == 0, axis=1)  # Sum black pixels per row
    
    min_darkness = np.min(darkness)
    max_darkness = np.max(darkness)
    
    if max_darkness <= min_darkness:
        return [0, h_img]
    
    # Define a threshold to identify content (non-gap) rows
    darkness_range = max_darkness - min_darkness
    row_settings = settings.get("row", {})
    threshold_ratio = float(row_settings.get("content_threshold", 0.20))
    content_threshold = min_darkness + (darkness_range * threshold_ratio)  # Rows above this have content
    
    # Find all content regions (continuous stretches of high darkness)
    content_regions = []
    in_content = False
    region_start = 0
    
    for y in range(h_img):
        has_content = darkness[y] > content_threshold
        
        if has_content and not in_content:
            # Start of content region
            region_start = y
            in_content = True
        elif not has_content and in_content:
            # End of content region
            content_regions.append((region_start, y))
            in_content = False
    
    if in_content:
        content_regions.append((region_start, h_img))
    
    if not content_regions:
        return [0, h_img]
    
    # Place boundaries in gaps between content regions
    # Boundaries are placed in the middle of gaps for padding on both sides
    boundaries = [0]
    
    for i, (content_start, _content_end) in enumerate(content_regions):
        # Calculate where this row should start:
        # Either at the start, or in the middle of the gap above this content
        if i == 0:
            # First row: put boundary where content starts
            boundaries.append(content_start)
        else:
            prev_content_end = content_regions[i - 1][1]
            gap_size = content_start - prev_content_end
            # Place boundary roughly in middle of gap for padding on both sides
            gap_middle = prev_content_end + gap_size // 2
            boundaries.append(gap_middle)
    
    boundaries.append(h_img)  # Always end at image height
    
    # Remove duplicates and sort
    boundaries = sorted(set(boundaries))
    
    # Filter out boundaries too close together
    min_row_height_pct = float(row_settings.get("min_row_height_pct", 1.0))
    min_row_height = max(3, int(h_img * (min_row_height_pct / 100.0)))
    filtered = [boundaries[0]]
    for b in boundaries[1:]:
        if b - filtered[-1] >= min_row_height:
            filtered.append(b)
    filtered.append(h_img)
    
    return sorted(set(filtered))


def _find_col_boundaries(binary: Any, h_img: int, w_img: int, settings: dict[str, Any]) -> list[int]:
    """Find column boundaries by scanning for consecutive white columns.
    
    Scans for gaps of ~45 consecutive mostly-white columns to mark boundaries.
    This provides a smart baseline that respects variable column widths.
    Returns x-coordinates of column starts.
    """
    try:
        import numpy as np  # type: ignore
    except Exception:
        return []
    
    # Calculate darkness per column (sum of black pixels)
    darkness = np.sum(binary == 0, axis=0)  # Sum black pixels per column
    
    # Normalize to percentage of image height
    darkness_pct = darkness / h_img
    
    col_settings = settings.get("col", {})

    # A column is "white" if it has a very low % of black pixels
    white_threshold = float(col_settings.get("white_threshold", 0.001))
    
    boundaries = [0]  # Start at left edge
    gap_size_required = int(col_settings.get("gap_width_px", 20))
    
    x = 10  # Start scanning after ~10 pixels from left
    
    while x < w_img:
        # Check if this position and the next ~19 columns are mostly white
        if x + gap_size_required <= w_img:
            gap_region = darkness_pct[x:x + gap_size_required]
            if np.all(gap_region < white_threshold):  # All columns in gap are white
                # Found a column separator
                boundaries.append(x)
                # Skip past this gap to find next column
                x += gap_size_required
                continue
        
        x += 1
    
    boundaries.append(w_img)  # End at right edge
    
    # Remove duplicates and sort
    boundaries = sorted(set(boundaries))
    
    # Filter out boundaries too close together
    min_col_width_pct = float(col_settings.get("min_col_width_pct", 1.0))
    min_col_width = max(5, int(w_img * (min_col_width_pct / 100.0)))
    filtered = [boundaries[0]]
    for b in boundaries[1:]:
        if b - filtered[-1] >= min_col_width:
            filtered.append(b)
    filtered.append(w_img)
    
    # Post-process: remove boundaries that create completely empty columns
    # A column is empty if all its pixels are white (no content)
    content_threshold = float(col_settings.get("content_threshold", 0.05))
    
    final_boundaries = [filtered[0]]
    for i in range(1, len(filtered) - 1):
        # Check the column segment between this boundary and the next
        col_start = filtered[i]
        col_end = filtered[i + 1]
        col_segment = darkness_pct[col_start:col_end]
        
        # If this segment has any content, keep the boundary
        if np.any(col_segment > content_threshold):
            final_boundaries.append(filtered[i])
    
    final_boundaries.append(filtered[-1])  # Always end at right edge
    
    return sorted(set(final_boundaries))


def _detect_grid_from_content(img: Any, h_img: int, w_img: int, logger: Any, settings: dict[str, Any]) -> dict[str, Any] | None:
    """Fallback: detect grid from content using morphological operations."""
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore  # noqa: F401
    except Exception:
        return None
    
    # Preprocess
    blur = cv2.GaussianBlur(img, (5, 5), 0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = 255 - th
    
    # Detect rows
    fallback_settings = settings.get("fallback", {})
    row_kernel_divisor = int(fallback_settings.get("row_kernel_divisor", 30))
    horiz_kernel_size = max(15, w_img // max(1, row_kernel_divisor))
    horiz_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (horiz_kernel_size, 1))
    rows_img = cv2.morphologyEx(th, cv2.MORPH_CLOSE, horiz_kernel, iterations=2)
    
    contours, _ = cv2.findContours(rows_img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    row_boxes = [cv2.boundingRect(cnt) for cnt in contours]
    row_boxes = [b for b in row_boxes if b[2] > 10 and b[3] > 4]
    
    if not row_boxes:
        logger.warning("Content-based detection also failed")
        return None
    
    row_boxes.sort(key=lambda x: x[1])
    num_rows = len(row_boxes)
    
    # Detect columns from first 3 rows
    col_count = 0
    for rx, ry, rw, rh in row_boxes[:min(3, len(row_boxes))]:
        row_img = th[ry:ry + rh, rx:rx + rw]
        word_divisor = int(fallback_settings.get("word_kernel_divisor", 12))
        word_kernel_width = max(5, rw // max(1, word_divisor))
        word_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (word_kernel_width, 1))
        words_img = cv2.morphologyEx(row_img, cv2.MORPH_CLOSE, word_kernel, iterations=1)
        
        contours_w, _ = cv2.findContours(words_img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        word_boxes = [cv2.boundingRect(cnt) for cnt in contours_w]
        word_boxes = [b for b in word_boxes if b[2] > 4 and b[3] > 3]
        col_count = max(col_count, len(word_boxes))
    
    logger.info("Fallback content-based detection: %d rows × %d columns", num_rows, col_count)
    
    row_ratio = 1.0 / num_rows if num_rows > 0 else 1.0
    col_ratio = 1.0 / col_count if col_count > 0 else 1.0
    
    grid: dict[str, Any] = {
        "type": "auto_detected_content",
        "rows": [{"height_ratio": row_ratio} for _ in range(max(1, num_rows))],
        "columns": [
            {"label": f"Column {idx + 1}", "width_ratio": col_ratio}
            for idx in range(max(1, col_count))
        ],
        "cell_labels": {},
        "detection_info": {
            "rows": num_rows,
            "columns": col_count,
            "method": "content_based",
        }
    }
    
    return grid
