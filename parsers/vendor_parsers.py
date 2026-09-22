"""Line-item parsers for the tabular document layouts DTOCR ships with.

Each layout parser turns OCR'd rows into structured records. Parsers are
stateful across pages: they accept and return a ``carry`` record so a line item
split across a page break is stitched back together by the caller.

Layouts are named ``layout_a`` / ``layout_b`` rather than after any particular
issuer. To support a new document format, add a parser here and register it in
:mod:`DTOCR.parsers.registry`.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


def parse_structured_rows(rows: list[list[str]]) -> list[dict[str, Any]]:
    """Parse ``rows`` with the layout A parser, flushing any trailing record."""
    records, carry = parse_structured_rows_layout_a_with_carry(rows, None)
    if carry:
        records.append(carry)
    return records


def parse_structured_rows_layout_a_with_carry(
    rows: list[list[str]], carry: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Parse a keyed layout: a numbered item line followed by ``key: value`` lines.

    Returns the completed records plus a trailing in-progress record (or
    ``None``), which the caller feeds back in as ``carry`` for the next page.
    """

    def _normalize_line(line: str) -> str:
        normalized = unicodedata.normalize("NFKD", line)
        normalized = normalized.encode("ascii", "ignore").decode("ascii")
        return normalized.lower()

    def _extract_after_key(normalized_line: str, key: str) -> str | None:
        pattern = rf"{re.escape(key)}\s*:\s*([^;]+)"
        match = re.search(pattern, normalized_line)
        if not match:
            return None
        return match.group(1).strip()

    def _extract_numbers(line: str) -> list[float]:
        nums = re.findall(r"\d+\.\d+|\d+", line)
        return [float(n) for n in nums]

    def _extract_qty_price_amount(line: str, record: dict[str, Any]) -> None:
        numbers = _extract_numbers(line)
        if len(numbers) >= 3:
            if record.get("qty") is None:
                record["qty"] = numbers[0]
            if record.get("unit_price") is None:
                record["unit_price"] = numbers[-2]
            if record.get("amount") is None:
                record["amount"] = numbers[-1]
        normalized = _normalize_line(line)
        unit_match = re.search(r"\b(ton|kg|m3|m2|m)\b", normalized)
        if unit_match and not record.get("unit"):
            record["unit"] = unit_match.group(1)

    def _is_article_code(token: str) -> bool:
        return bool(re.fullmatch(r"[A-Z]{1,3}-?\d{2,6}", token.strip()))

    def _is_complete(record: dict[str, Any]) -> bool:
        required = ["delivery_date", "vehicle_id", "description", "qty", "unit_price", "amount"]
        return all(record.get(field) not in (None, "") for field in required)

    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = carry

    for row in rows:
        cells = [str(c).strip() for c in row]
        if not any(cells):
            continue

        # A leading integer starts a new line item; flush the previous one.
        if cells[0].isdigit():
            if current:
                records.append(current)
            current = {
                "item_no": int(cells[0]),
                "description": cells[1],
                "qty": None,
                "unit": None,
                "unit_price": None,
                "amount": None,
                "article_code": None,
                "delivery_date": None,
                "delivery_note": None,
                "buyer_order_id": None,
                "site": None,
                "contact": None,
                "phone": None,
                "waybill": None,
                "vehicle_id": None,
                "waste_declaration": None,
            }
            _extract_qty_price_amount(" ".join(cells[1:]), current)
            continue

        if current is None:
            continue

        if cells[0] and _is_article_code(cells[0]):
            current["article_code"] = cells[0]
            continue

        line = " ".join(cells)
        normalized = _normalize_line(line)
        normalized_no_quote = normalized.replace("'", "")

        if "delivery date" in normalized_no_quote:
            match = re.search(r"(\d{4}-\d{2}-\d{2})", normalized)
            if match:
                current["delivery_date"] = match.group(1)
            continue

        if "delivery note" in normalized_no_quote:
            val = _extract_after_key(normalized, "delivery note")
            if val:
                current["delivery_note"] = val
            continue

        if "buyers order id" in normalized_no_quote:
            val = _extract_after_key(normalized_no_quote, "buyers order id")
            if val:
                current["buyer_order_id"] = val
            continue

        if "u-stalle" in normalized:
            val = _extract_after_key(normalized, "u-stalle")
            if val:
                parts = [p.strip() for p in val.split(";") if p.strip()]
                if parts:
                    current["site"] = parts[0]
                if len(parts) > 1:
                    current["contact"] = parts[1]
            continue

        phone_match = re.search(r"\b\d{6,}\b", line)
        if phone_match and not current.get("phone"):
            current["phone"] = phone_match.group(0)

        # Document keys on the left, record fields on the right.
        for key, target in [
            ("vagsedel", "waybill"),
            ("bilregnr", "vehicle_id"),
            ("avfallsdeklaration", "waste_declaration"),
        ]:
            val = _extract_after_key(normalized, key)
            if val:
                current[target] = val.upper() if target == "vehicle_id" else val

        _extract_qty_price_amount(line, current)

    if current:
        if _is_complete(current):
            records.append(current)
            current = None

    return records, current


def parse_structured_rows_layout_b_with_carry(
    rows: list[list[str]], carry: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Parse a positional layout: one line item per row, plus trailing surcharge rows.

    Surcharge rows carry no identity of their own, so their amounts are folded
    into the most recent line item that has both a vehicle id and a date.
    """

    def _extract_numbers(line: str) -> list[str]:
        return re.findall(r"\d{1,3}(?:[ \u00A0]\d{3})*(?:,\d+)?", line)

    def _parse_decimal(value: str) -> float | None:
        """Parse a number written with space thousands separators and a decimal comma."""
        cleaned = value.replace("\u00A0", " ").replace(" ", "")
        cleaned = cleaned.replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return None

    def _is_surcharge_a_line(line: str) -> bool:
        lowered = line.lower()
        return "täkt" in lowered and "miljö" in lowered

    def _is_surcharge_b_line(line: str) -> bool:
        lowered = line.lower()
        return "vintertillägg" in lowered or "vintertillagg" in lowered

    def _extract_unit(line: str) -> str | None:
        match = re.search(r"\b(ton|kg|m3|m2|st)\b", line.lower())
        return match.group(1) if match else None

    def _extract_vehicle_id(tokens: list[str], start_idx: int) -> tuple[str | None, int | None]:
        for idx in range(start_idx, len(tokens)):
            token = tokens[idx].strip().upper()
            if token in {"N", "-"}:
                continue
            if re.fullmatch(r"[A-Z]{1,3}\d{2,4}[A-Z0-9]{0,2}", token):
                return token, idx
        return None, None

    def _parse_main_line(line: str) -> dict[str, Any] | None:
        tokens = [t for t in line.split() if t.strip()]
        if not tokens:
            return None
        delivery_note = None
        if tokens[0].isdigit():
            delivery_note = tokens[0]

        date_match = re.search(r"\b\d{2}-\d{2}\b", line)
        delivery_date = date_match.group(0) if date_match else None
        date_idx = tokens.index(delivery_date) if delivery_date and delivery_date in tokens else 0
        vehicle_id, vehicle_idx = _extract_vehicle_id(tokens, date_idx + 1)
        if vehicle_id:
            vehicle_id = vehicle_id.upper()

        unit = _extract_unit(line)
        unit_idx = tokens.index(unit) if unit and unit in tokens else None
        ton_idx = None
        for idx, token in enumerate(tokens):
            if idx <= (vehicle_idx or -1):
                continue
            if token.strip().lower() == "ton":
                ton_idx = idx
                break

        # The product name is whatever sits between the vehicle id and the unit.
        if vehicle_idx is not None:
            if ton_idx is not None and ton_idx > vehicle_idx:
                end_idx = ton_idx
            else:
                end_idx = unit_idx if unit_idx is not None and unit_idx > vehicle_idx else len(tokens)
            product_tokens = tokens[vehicle_idx + 1 : end_idx]
        elif ton_idx is not None:
            start_idx = date_idx + 1
            if start_idx < len(tokens) and tokens[start_idx].strip().upper() == "N":
                start_idx += 1
            product_tokens = tokens[start_idx:ton_idx]
        else:
            product_tokens = []
        product_name = " ".join(product_tokens).strip() if product_tokens else None

        numbers = _extract_numbers(line)
        qty = unit_price = amount = None
        if len(numbers) >= 3:
            qty = _parse_decimal(numbers[-3])
            unit_price = _parse_decimal(numbers[-2])
            amount = _parse_decimal(numbers[-1])

        return {
            "delivery_note": delivery_note,
            "delivery_date": delivery_date,
            "vehicle_id": vehicle_id,
            "product_name": product_name,
            "unit": unit,
            "quantity": qty,
            "unit_price": unit_price,
            "amount": amount,
            "surcharge_a": False,
            "surcharge_b": False,
        }

    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = carry

    def _is_surcharge_target(row: dict[str, Any] | None) -> bool:
        if not row:
            return False
        return bool(str(row.get("vehicle_id", "")).strip()) and bool(str(row.get("delivery_date", "")).strip())

    last_surcharge_target: dict[str, Any] | None = current if _is_surcharge_target(current) else None

    started = bool(carry)
    for row in rows:
        line = " ".join(str(c).strip() for c in row if str(c).strip())
        if not line:
            continue

        lower_line = line.lower()
        if "sammandrag faktura" in lower_line:
            break
        is_surcharge_a = _is_surcharge_a_line(line)
        is_surcharge_b = _is_surcharge_b_line(line)
        if (is_surcharge_a or is_surcharge_b) and last_surcharge_target:
            numbers = _extract_numbers(line)
            if len(numbers) >= 2:
                surcharge_unit_price = _parse_decimal(numbers[-2])
                surcharge_amount = _parse_decimal(numbers[-1])
            else:
                surcharge_unit_price = None
                surcharge_amount = None
            target = last_surcharge_target
            flag_key = "surcharge_a" if is_surcharge_a else "surcharge_b"
            if flag_key not in target:
                target[flag_key] = False
            if surcharge_unit_price is not None:
                target["unit_price"] = (target.get("unit_price") or 0) + surcharge_unit_price
            if surcharge_amount is not None:
                target["amount"] = (target.get("amount") or 0) + surcharge_amount
            target[flag_key] = True
            continue

        # Skip the header block until the amount column is seen.
        if not started:
            if "belopp sek" in lower_line:
                started = True
            continue

        parsed = _parse_main_line(line)
        if not parsed:
            continue
        if current:
            records.append(current)
        current = parsed
        if _is_surcharge_target(parsed):
            last_surcharge_target = parsed

    return records, current


def parse_grid_cells(grid_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten detected grid cells into one record per cell."""
    parsed_rows: list[dict[str, Any]] = []
    for item in grid_results:
        parsed_rows.append(
            {
                "grid_label": item.get("grid_label", ""),
                "page": item.get("page", 0),
                "row_index": item.get("row_index", 0),
                "col_index": item.get("col_index", 0),
                "column_label": item.get("column_label", ""),
                "cell_label": item.get("cell_label", ""),
                "text": item.get("text", ""),
            }
        )
    return parsed_rows
