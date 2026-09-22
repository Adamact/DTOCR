"""Registry of the document layout parsers available to templates.

A template stores a ``parser_name``; this module maps that name to the callable
that parses it, the column order used for Excel export, and an optional filter
that drops incomplete rows.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from DTOCR.parsers import vendor_parsers

ParseWithCarry = Callable[[list[list[str]], dict[str, Any] | None], tuple[list[dict[str, Any]], dict[str, Any] | None]]
RowFilter = Callable[[dict[str, Any]], bool]
ParseGrid = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]


@dataclass(frozen=True)
class ParserDefinition:
    name: str
    parse_with_carry: ParseWithCarry
    preferred_columns: list[str]
    sheet_name: str
    row_filter: RowFilter | None = None
    parse_grid: ParseGrid | None = None
    grid_sheet_name: str | None = None


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value is True
    return str(value).strip() != ""


def _keep_row_layout_a(row: dict[str, Any]) -> bool:
    return _has_value(row.get("vehicle_id")) and _has_value(row.get("delivery_date"))


def _keep_row_layout_b(row: dict[str, Any]) -> bool:
    return _has_value(row.get("vehicle_id")) and _has_value(row.get("delivery_date"))


_PARSERS: dict[str, ParserDefinition] = {
    "layout-a": ParserDefinition(
        name="layout-a",
        parse_with_carry=vendor_parsers.parse_structured_rows_layout_a_with_carry,
        preferred_columns=["delivery_date", "vehicle_id", "description", "qty", "unit_price", "amount"],
        sheet_name="Parsed_Text",
        row_filter=_keep_row_layout_a,
    ),
    "layout-b": ParserDefinition(
        name="layout-b",
        parse_with_carry=vendor_parsers.parse_structured_rows_layout_b_with_carry,
        preferred_columns=[
            "delivery_note",
            "delivery_date",
            "vehicle_id",
            "product_name",
            "unit",
            "quantity",
            "unit_price",
            "amount",
            "surcharge_a",
            "surcharge_b",
        ],
        sheet_name="Parsed_Text_LayoutB",
        row_filter=_keep_row_layout_b,
    ),
    "grid-only": ParserDefinition(
        name="grid-only",
        parse_with_carry=vendor_parsers.parse_structured_rows_layout_a_with_carry,
        preferred_columns=["delivery_date", "vehicle_id", "description", "qty", "unit_price", "amount"],
        sheet_name="Parsed_Text",
        parse_grid=vendor_parsers.parse_grid_cells,
        grid_sheet_name="Parsed_Grid",
    ),
}


def available_parsers() -> list[str]:
    return sorted(_PARSERS.keys())


def get_parser(name: str) -> ParserDefinition:
    key = str(name).strip().lower()
    if not key:
        raise ValueError("Parser name is empty.")
    parser = _PARSERS.get(key)
    if not parser:
        raise ValueError(f"Unknown parser '{name}'. Available: {', '.join(available_parsers())}")
    return parser
