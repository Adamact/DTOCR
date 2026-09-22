"""Tests for the layout parsers and the parser registry.

These depend only on the standard library, so they run without torch,
OpenCV, or the other heavy runtime dependencies.
"""

from __future__ import annotations

import pytest
from DTOCR.parsers import vendor_parsers as vp
from DTOCR.parsers.registry import available_parsers, get_parser

# A numbered item line, then `key: value` detail lines.
LAYOUT_A_ROWS = [
    ["1", "Crushed stone 0-32", "12.5", "ton", "145.00", "1812.50"],
    ["AB-1234", ""],
    ["Delivery date: 2026-02-14"],
    ["Delivery note: DN-88213"],
    ["Buyers order id: PO-5512"],
    ["U-stalle: Site North; Jane Doe"],
    ["Vagsedel: VS-771; Bilregnr: abc123; Avfallsdeklaration: AD-9"],
]

# One line item per row; surcharge rows attach to the item above them.
# The surcharge matcher looks for the accented spelling, so keep it here.
LAYOUT_B_ROWS = [
    ["Belopp SEK"],
    ["100231 02-14 ABC12 Crushed stone 0-32 ton 12,5 145,00 1 812,50"],
    ["Täkt- och miljöavgift 12,5 4,50 56,25"],
    ["100232 02-15 N XY99 Gravel 8-16 ton 4,0 98,50 394,00"],
]


class TestRegistry:
    def test_available_parsers(self):
        assert available_parsers() == ["grid-only", "layout-a", "layout-b"]

    def test_get_parser_is_case_insensitive(self):
        assert get_parser("LAYOUT-A").name == "layout-a"

    def test_unknown_parser_raises(self):
        with pytest.raises(ValueError, match="Unknown parser"):
            get_parser("does-not-exist")

    def test_empty_parser_name_raises(self):
        with pytest.raises(ValueError, match="Parser name is empty"):
            get_parser("   ")

    @pytest.mark.parametrize("name", ["grid-only", "layout-a", "layout-b"])
    def test_definitions_are_well_formed(self, name):
        parser = get_parser(name)
        assert parser.name == name
        assert parser.preferred_columns
        assert parser.sheet_name

    def test_row_filters_require_identity_fields(self):
        for name in ("layout-a", "layout-b"):
            keep = get_parser(name).row_filter
            assert keep({"vehicle_id": "AB123", "delivery_date": "2026-01-01"})
            assert not keep({"vehicle_id": "", "delivery_date": "2026-01-01"})
            assert not keep({"vehicle_id": "AB123"})


class TestLayoutA:
    def test_extracts_a_complete_record(self):
        records, carry = vp.parse_structured_rows_layout_a_with_carry(LAYOUT_A_ROWS, None)
        # Every required field is present, so the record is flushed, not carried.
        assert carry is None
        assert len(records) == 1

        record = records[0]
        assert record["item_no"] == 1
        assert record["description"] == "Crushed stone 0-32"
        assert record["unit"] == "ton"
        assert record["unit_price"] == 145.00
        assert record["amount"] == 1812.50
        assert record["article_code"] == "AB-1234"
        assert record["delivery_date"] == "2026-02-14"
        assert record["delivery_note"] == "dn-88213"
        assert record["buyer_order_id"] == "po-5512"
        assert record["site"] == "site north"
        assert record["waybill"] == "vs-771"
        assert record["waste_declaration"] == "ad-9"

    def test_digits_in_the_description_are_taken_as_quantity(self):
        """Known limitation, pinned so a future fix is a deliberate change.

        Quantity is read as the first number on the item line, so a size in
        the product name ("0-32") wins over the real quantity (12.5).
        """
        records, _ = vp.parse_structured_rows_layout_a_with_carry(LAYOUT_A_ROWS, None)
        assert records[0]["qty"] == 0.0

    def test_only_the_first_semicolon_field_is_read(self):
        """Known limitation: the value pattern stops at the first ';'.

        "U-stalle: Site North; Jane Doe" therefore fills `site` but never
        reaches the `contact` half.
        """
        records, _ = vp.parse_structured_rows_layout_a_with_carry(LAYOUT_A_ROWS, None)
        assert records[0]["site"] == "site north"
        assert records[0]["contact"] is None

    def test_vehicle_id_is_upper_cased(self):
        records, _ = vp.parse_structured_rows_layout_a_with_carry(LAYOUT_A_ROWS, None)
        assert records[0]["vehicle_id"] == "ABC123"

    def test_incomplete_record_is_carried_not_emitted(self):
        records, carry = vp.parse_structured_rows_layout_a_with_carry(
            [["1", "Item", "2", "ton", "10.00", "20.00"]], None
        )
        assert records == []
        assert carry is not None
        assert carry["delivery_date"] is None

    def test_carry_is_stitched_across_a_page_break(self):
        split = 3
        _, carry = vp.parse_structured_rows_layout_a_with_carry(LAYOUT_A_ROWS[:split], None)
        assert carry is not None, "first page should leave an open record"

        records, final = vp.parse_structured_rows_layout_a_with_carry(LAYOUT_A_ROWS[split:], carry)
        assert final is None
        assert len(records) == 1
        assert records[0]["vehicle_id"] == "ABC123"
        assert records[0]["description"] == "Crushed stone 0-32"

    def test_empty_input(self):
        assert vp.parse_structured_rows_layout_a_with_carry([], None) == ([], None)

    def test_wrapper_flushes_trailing_record(self):
        # The wrapper has no next page to carry into, so it emits the open
        # record even when it is incomplete.
        rows = [["1", "Item", "2", "ton", "10.00", "20.00"]]
        records = vp.parse_structured_rows(rows)
        assert len(records) == 1
        assert records[0]["delivery_date"] is None
        assert len(vp.parse_structured_rows(LAYOUT_A_ROWS)) == 1


class TestLayoutB:
    def test_parses_positional_columns(self):
        records, carry = vp.parse_structured_rows_layout_b_with_carry(LAYOUT_B_ROWS, None)
        assert len(records) == 1
        assert carry is not None  # the last item stays open for the next page

        first = records[0]
        assert first["delivery_note"] == "100231"
        assert first["delivery_date"] == "02-14"
        assert first["vehicle_id"] == "ABC12"
        assert first["product_name"] == "Crushed stone 0-32"
        assert first["unit"] == "ton"
        assert first["quantity"] == 12.5

    def test_surcharge_folds_into_the_preceding_item(self):
        records, _ = vp.parse_structured_rows_layout_b_with_carry(LAYOUT_B_ROWS, None)
        first = records[0]
        assert first["surcharge_a"] is True
        # 145,00 + 4,50 and 1 812,50 + 56,25
        assert first["unit_price"] == pytest.approx(149.50)
        assert first["amount"] == pytest.approx(1868.75)

    def test_placeholder_token_is_skipped_before_the_vehicle_id(self):
        _, carry = vp.parse_structured_rows_layout_b_with_carry(LAYOUT_B_ROWS, None)
        assert carry["vehicle_id"] == "XY99"
        assert carry["product_name"] == "Gravel 8-16"

    def test_header_rows_are_skipped_until_the_amount_column(self):
        records, carry = vp.parse_structured_rows_layout_b_with_carry(
            [["Some header"], ["Another header"]], None
        )
        assert records == []
        assert carry is None

    def test_parsing_stops_at_the_summary_section(self):
        rows = LAYOUT_B_ROWS + [["Sammandrag faktura"], ["999 02-16 ZZ1 Ignored ton 1,0 1,00 1,00"]]
        records, carry = vp.parse_structured_rows_layout_b_with_carry(rows, None)
        ids = [r["vehicle_id"] for r in records] + ([carry["vehicle_id"]] if carry else [])
        assert "ZZ1" not in ids

    def test_decimal_comma_and_space_separators(self):
        records, _ = vp.parse_structured_rows_layout_b_with_carry(LAYOUT_B_ROWS, None)
        assert records[0]["quantity"] == pytest.approx(12.5)

    def test_empty_input(self):
        assert vp.parse_structured_rows_layout_b_with_carry([], None) == ([], None)


class TestGridCells:
    def test_flattens_cells(self):
        cells = [
            {
                "grid_label": "table",
                "page": 1,
                "row_index": 0,
                "col_index": 2,
                "column_label": "Amount",
                "cell_label": "c",
                "text": "42",
            }
        ]
        assert vp.parse_grid_cells(cells) == cells

    def test_missing_keys_get_defaults(self):
        assert vp.parse_grid_cells([{}]) == [
            {
                "grid_label": "",
                "page": 0,
                "row_index": 0,
                "col_index": 0,
                "column_label": "",
                "cell_label": "",
                "text": "",
            }
        ]
