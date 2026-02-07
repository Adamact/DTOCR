from __future__ import annotations

from typing import Any
import re
import unicodedata

def detect_parser_type(results: list[dict[str, Any]]) -> str:
    text_blob = " ".join(str(r.get("text", "")) for r in results).lower()
    if "layout b marker" in text_blob:
        return "layout-b"
    if "layout-a" in text_blob:
        return "layout-a"
    return "layout-a"


def parse_structured_rows(rows: list[list[str]]) -> list[dict[str, Any]]:
    records, carry = parse_structured_rows_with_carry(rows, None)
    if carry:
        records.append(carry)
    return records


def parse_structured_rows_with_carry(
    rows: list[list[str]], carry: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
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
        required = ["delivery_date", "bilregnr", "description", "qty", "unit_price", "amount"]
        return all(record.get(field) not in (None, "") for field in required)

    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = carry

    for row in rows:
        cells = [str(c).strip() for c in row]
        if not any(cells):
            continue

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
                "u_stalle": None,
                "contact": None,
                "phone": None,
                "vagsedel": None,
                "bilregnr": None,
                "avfallsdeklaration": None,
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
                    current["u_stalle"] = parts[0]
                if len(parts) > 1:
                    current["contact"] = parts[1]
            continue

        phone_match = re.search(r"\b\d{6,}\b", line)
        if phone_match and not current.get("phone"):
            current["phone"] = phone_match.group(0)

        for key, target in [
            ("vagsedel", "vagsedel"),
            ("bilregnr", "bilregnr"),
            ("avfallsdeklaration", "avfallsdeklaration"),
        ]:
            val = _extract_after_key(normalized, key)
            if val:
                current[target] = val.upper() if target == "bilregnr" else val

        _extract_qty_price_amount(line, current)

    if current:
        if _is_complete(current):
            records.append(current)
            current = None

    return records, current


def parse_structured_rows_layout_b_with_carry(
    rows: list[list[str]], carry: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    
    def _extract_numbers(line: str) -> list[str]:
        return re.findall(r"\d{1,3}(?:[ \u00A0]\d{3})*(?:,\d+)?", line)

    def _parse_number_sv(value: str) -> float | None:
        cleaned = value.replace("\u00A0", " ").replace(" ", "")
        cleaned = cleaned.replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return None

    def _is_fee_line(line: str) -> bool:
        lowered = line.lower()
        return "täkt" in lowered and "miljö" in lowered

    def _is_winter_line(line: str) -> bool:
        lowered = line.lower()
        return "vintertillägg" in lowered or "vintertillagg" in lowered

    def _extract_unit(line: str) -> str | None:
        match = re.search(r"\b(ton|kg|m3|m2|st)\b", line.lower())
        return match.group(1) if match else None

    def _extract_bilnr(tokens: list[str], start_idx: int) -> tuple[str | None, int | None]:
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
        foljesedel = None
        if tokens[0].isdigit():
            foljesedel = tokens[0]

        levdag_match = re.search(r"\b\d{2}-\d{2}\b", line)
        levdag = levdag_match.group(0) if levdag_match else None
        levdag_idx = tokens.index(levdag) if levdag and levdag in tokens else 0
        bilnr, bilnr_idx = _extract_bilnr(tokens, levdag_idx + 1)
        if bilnr:
            bilnr = bilnr.upper()

        unit = _extract_unit(line)
        unit_idx = tokens.index(unit) if unit and unit in tokens else None
        ton_idx = None
        for idx, token in enumerate(tokens):
            if idx <= (bilnr_idx or -1):
                continue
            if token.strip().lower() == "ton":
                ton_idx = idx
                break

        if bilnr_idx is not None:
            if ton_idx is not None and ton_idx > bilnr_idx:
                end_idx = ton_idx
            else:
                end_idx = unit_idx if unit_idx is not None and unit_idx > bilnr_idx else len(tokens)
            produkt_tokens = tokens[bilnr_idx + 1 : end_idx]
        elif ton_idx is not None:
            start_idx = levdag_idx + 1
            if start_idx < len(tokens) and tokens[start_idx].strip().upper() == "N":
                start_idx += 1
            produkt_tokens = tokens[start_idx:ton_idx]
        else:
            produkt_tokens = []
        produktnamn = " ".join(produkt_tokens).strip() if produkt_tokens else None

        numbers = _extract_numbers(line)
        qty = unit_price = amount = None
        if len(numbers) >= 3:
            qty = _parse_number_sv(numbers[-3])
            unit_price = _parse_number_sv(numbers[-2])
            amount = _parse_number_sv(numbers[-1])

        return {
            "foljesedel": foljesedel,
            "levdag": levdag,
            "bilnr": bilnr,
            "produktnamn": produktnamn,
            "enhet": unit,
            "kvantitet": qty,
            "a_pris": unit_price,
            "belopp_sek": amount,
            "takt_miljoavgift": False,
            "vintertillagg": False,
        }

    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = carry

    def _is_fee_target(row: dict[str, Any] | None) -> bool:
        if not row:
            return False
        return bool(str(row.get("bilnr", "")).strip()) and bool(str(row.get("levdag", "")).strip())

    last_fee_target: dict[str, Any] | None = current if _is_fee_target(current) else None

    started = bool(carry)
    for row in rows:
        line = " ".join(str(c).strip() for c in row if str(c).strip())
        if not line:
            continue

        lower_line = line.lower()
        if "sammandrag faktura" in lower_line:
            break
        is_takt_line = _is_fee_line(line)
        is_winter_line = _is_winter_line(line)
        if (is_takt_line or is_winter_line) and last_fee_target:
            numbers = _extract_numbers(line)
            if len(numbers) >= 3:
                fee_unit_price = _parse_number_sv(numbers[-2])
                fee_amount = _parse_number_sv(numbers[-1])
            elif len(numbers) >= 2:
                fee_unit_price = _parse_number_sv(numbers[-2])
                fee_amount = _parse_number_sv(numbers[-1])
            else:
                fee_unit_price = None
                fee_amount = None
            target = last_fee_target
            flag_key = "takt_miljoavgift" if is_takt_line else "vintertillagg"
            if flag_key not in target:
                target[flag_key] = False
            if fee_unit_price is not None:
                target["a_pris"] = (target.get("a_pris") or 0) + fee_unit_price
            if fee_amount is not None:
                target["belopp_sek"] = (target.get("belopp_sek") or 0) + fee_amount
            target[flag_key] = True
            continue

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
        if _is_fee_target(parsed):
            last_fee_target = parsed

    return records, current
