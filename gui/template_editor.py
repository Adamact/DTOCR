from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import tkinter as tk
from PIL import Image, ImageTk
from tkinter import filedialog, messagebox, ttk

from DTOCR.services.template_service import TemplateService
from DTOCR.core.grid_detector import auto_detect_grid_structure, default_detection_settings, merge_detection_settings
from DTOCR.core.pdf_renderer import PdfDocument, PdfRect, scale_from_dpi
from DTOCR.parsers.registry import available_parsers, get_parser


@dataclass
class Region:
    region_id: str
    page: int
    page_scope: str
    label: str
    x: float
    y: float
    width: float
    height: float
    grid: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "id": self.region_id,
            "page": self.page,
            "page_scope": self.page_scope,
            "label": self.label,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }
        if self.grid:
            payload["grid"] = self.grid
        return payload


class TemplateEditor:
    PAGE_SCOPES = ("all", "first", "middle", "last", "specific")
    REGION_OUTLINE_COLOR = "#1976d2"
    REGION_SELECTED_COLOR = "#d32f2f"
    REGION_OUTLINE_WIDTH = 2
    REGION_SELECTED_WIDTH = 3

    def __init__(
        self,
        parent: tk.Tk,
        template_service: TemplateService,
        template_id: int,
        template_payload: dict[str, Any],
        label_options: list[str],
    ) -> None:
        self.template_service = template_service
        self.template_id = template_id
        self.template_payload = template_payload
        self.label_options = label_options
        self.regions: list[Region] = [
            Region(
                region_id=region.get("id", str(uuid4())),
                page=region.get("page", 1),
                page_scope=region.get("page_scope", "specific"),
                label=region.get("label", ""),
                x=region.get("x", 0.0),
                y=region.get("y", 0.0),
                width=region.get("width", 0.0),
                height=region.get("height", 0.0),
                grid=region.get("grid"),
            )
            for region in template_payload.get("regions", [])
        ]
        self.source_pdf = template_payload.get("source_pdf", "")
        self.dpi_var = tk.StringVar(value=str(template_payload.get("dpi", 300)))
        self.text_mode_var = tk.StringVar(value=str(template_payload.get("text_mode", "auto")))
        self.parser_options = available_parsers()
        self.parser_placeholder = "<select>"
        current_parser = str(template_payload.get("parser_name", "")).strip()
        if not current_parser or current_parser not in self.parser_options:
            current_parser = self.parser_placeholder
        self.parser_name_var = tk.StringVar(value=current_parser)
        self.detection_settings = merge_detection_settings(template_payload.get("detection_settings"))
        self.row_content_threshold_var = tk.StringVar()
        self.min_row_height_pct_var = tk.StringVar()
        self.col_white_threshold_var = tk.StringVar()
        self.col_gap_width_var = tk.StringVar()
        self.min_col_width_pct_var = tk.StringVar()
        self.col_content_threshold_var = tk.StringVar()
        self.row_kernel_divisor_var = tk.StringVar()
        self.word_kernel_divisor_var = tk.StringVar()
        self._load_detection_settings(self.detection_settings)

        self.window = tk.Toplevel(parent)
        self.window.title(f"Template Editor: {template_payload.get('name', '')}")
        self.window.geometry("1100x720")

        self.doc: PdfDocument | None = None
        self.page_index = 0
        self.page_scale = 1.0
        self.zoom = 1.0
        self.page_image: ImageTk.PhotoImage | None = None
        self.canvas_image_id: int | None = None
        self.canvas_rect_id: int | None = None
        self.canvas_region_map: dict[str, int] = {}
        self.canvas_col_label_map: dict[int, tuple[str, int]] = {}  # text_item_id -> (region_id, col_idx)
        self.drag_start: tuple[float, float] | None = None
        self.inline_edit_entry: tk.Entry | None = None
        self.inline_edit_data: tuple[str, int] | None = None  # (region_id, col_idx)
        self.drawing_mode: str = "region"  # "region" or "grid"
        self.grid_active_region_id: str | None = None
        self.grid_rows_var = tk.StringVar(value="")
        self.grid_cols_var = tk.StringVar(value="")
        self.grid_row_height_var = tk.StringVar(value="")
        self.grid_col_width_var = tk.StringVar(value="")
        self.grid_col_label_var = tk.StringVar(value="")
        self._updating_slider = False  # Prevent slider feedback loops
        self._detect_update_after_id: str | None = None
        self.live_detect_var = tk.BooleanVar(value=True)
        self._selected_grid_row_idx: int | None = None
        self._selected_grid_col_idx: int | None = None

        self._build_ui()
        self._bind_detection_live_updates()

        if self.source_pdf:
            self._load_pdf(self.source_pdf)

    def _build_ui(self) -> None:
        container = ttk.Frame(self.window, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        controls = ttk.Frame(container)
        controls.pack(fill=tk.X)

        ttk.Button(controls, text="Load PDF", command=self._on_load_pdf).pack(side=tk.LEFT)
        ttk.Button(controls, text="Zoom In", command=lambda: self._apply_zoom(1.1)).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(controls, text="Zoom Out", command=lambda: self._apply_zoom(1 / 1.1)).pack(side=tk.LEFT, padx=4)
        
        ttk.Separator(controls, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)
        
        self.create_grid_btn = ttk.Button(controls, text="✚ Create Grid", command=self._toggle_grid_mode)
        self.create_grid_btn.pack(side=tk.LEFT, padx=(0, 4))
        self.mode_label = ttk.Label(controls, text="Mode: Normal", foreground="#666")
        self.mode_label.pack(side=tk.LEFT, padx=(0, 12))
        
        nav_frame = ttk.Frame(controls)
        nav_frame.pack(side=tk.RIGHT)
        ttk.Button(nav_frame, text="◀ Prev", command=self._prev_page).pack(side=tk.LEFT, padx=4)
        ttk.Button(nav_frame, text="Next ▶", command=self._next_page).pack(side=tk.LEFT, padx=4)

        self.page_indicator = ttk.Label(nav_frame, text="Page 0/0")
        self.page_indicator.pack(side=tk.LEFT, padx=8)

        body = ttk.Frame(container)
        body.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        canvas_frame = ttk.Frame(body)
        canvas_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(canvas_frame, bg="#f5f5f5")
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        v_scroll = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL, command=self.canvas.yview)
        v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        h_scroll = ttk.Scrollbar(body, orient=tk.HORIZONTAL, command=self.canvas.xview)
        h_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        self.canvas.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)
        self.canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.canvas.bind("<MouseWheel>", self._on_mouse_wheel)
        self.canvas.bind("<Button-4>", self._on_mouse_wheel)
        self.canvas.bind("<Button-5>", self._on_mouse_wheel)
        self.canvas.bind("<Control-MouseWheel>", self._on_zoom_wheel)

        sidebar = ttk.Frame(body, width=280)
        sidebar.pack(side=tk.RIGHT, fill=tk.Y, padx=(6, 0))
        sidebar.pack_propagate(False)

        sidebar_canvas = tk.Canvas(sidebar, borderwidth=0, highlightthickness=0)
        sidebar_scroll = ttk.Scrollbar(sidebar, orient=tk.VERTICAL, command=sidebar_canvas.yview)
        sidebar_canvas.configure(yscrollcommand=sidebar_scroll.set)
        sidebar_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        sidebar_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        sidebar_inner = ttk.Frame(sidebar_canvas)
        sidebar_window_id = sidebar_canvas.create_window((0, 0), window=sidebar_inner, anchor="nw")

        def _on_sidebar_configure(_: tk.Event) -> None:
            sidebar_canvas.configure(scrollregion=sidebar_canvas.bbox("all"))

        def _on_sidebar_canvas_configure(event: tk.Event) -> None:
            sidebar_canvas.itemconfig(sidebar_window_id, width=event.width)

        def _on_sidebar_mousewheel(event: tk.Event) -> str:
            if getattr(event, "delta", 0):
                delta = -1 * int(event.delta / 120)
            else:
                delta = 1 if getattr(event, "num", 0) == 5 else -1
            sidebar_canvas.yview_scroll(delta, "units")
            return "break"

        def _bind_sidebar_scroll(_: tk.Event) -> None:
            sidebar_canvas.bind_all("<MouseWheel>", _on_sidebar_mousewheel)
            sidebar_canvas.bind_all("<Button-4>", _on_sidebar_mousewheel)
            sidebar_canvas.bind_all("<Button-5>", _on_sidebar_mousewheel)

        def _unbind_sidebar_scroll(_: tk.Event) -> None:
            sidebar_canvas.unbind_all("<MouseWheel>")
            sidebar_canvas.unbind_all("<Button-4>")
            sidebar_canvas.unbind_all("<Button-5>")

        def _block_mousewheel(widget: tk.Widget) -> None:
            widget.bind("<MouseWheel>", lambda e: "break")
            widget.bind("<Button-4>", lambda e: "break")
            widget.bind("<Button-5>", lambda e: "break")

        sidebar_inner.bind("<Configure>", _on_sidebar_configure)
        sidebar_canvas.bind("<Configure>", _on_sidebar_canvas_configure)
        sidebar.bind("<Enter>", _bind_sidebar_scroll)
        sidebar.bind("<Leave>", _unbind_sidebar_scroll)

        ttk.Label(sidebar_inner, text="Detection Settings", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        detect_frame = ttk.LabelFrame(sidebar_inner, text="Detection Settings")
        detect_frame.pack(fill=tk.BOTH, expand=False, pady=(6, 0))

        ttk.Label(detect_frame, text="Row detection", font=("TkDefaultFont", 9, "bold")).pack(anchor="w", padx=8, pady=(6, 2))
        row_grid = ttk.Frame(detect_frame)
        row_grid.pack(fill=tk.X, padx=8)
        ttk.Label(row_grid, text="Content threshold (%)").grid(row=0, column=0, sticky="w")
        row_thresh_spin = ttk.Spinbox(row_grid, from_=0.1, to=90.0, increment=0.1, textvariable=self.row_content_threshold_var, width=7)
        row_thresh_spin.grid(row=0, column=1, sticky="w", padx=(6, 0))
        _block_mousewheel(row_thresh_spin)
        ttk.Label(row_grid, text="Min row height (%)").grid(row=1, column=0, sticky="w", pady=(4, 0))
        min_row_spin = ttk.Spinbox(row_grid, from_=0.1, to=25.0, increment=0.1, textvariable=self.min_row_height_pct_var, width=7)
        min_row_spin.grid(row=1, column=1, sticky="w", padx=(6, 0), pady=(4, 0))
        _block_mousewheel(min_row_spin)

        ttk.Label(detect_frame, text="Column detection", font=("TkDefaultFont", 9, "bold")).pack(anchor="w", padx=8, pady=(8, 2))
        col_grid = ttk.Frame(detect_frame)
        col_grid.pack(fill=tk.X, padx=8)
        ttk.Label(col_grid, text="White threshold (%)").grid(row=0, column=0, sticky="w")
        col_white_spin = ttk.Spinbox(col_grid, from_=0.01, to=10.0, increment=0.01, textvariable=self.col_white_threshold_var, width=7)
        col_white_spin.grid(row=0, column=1, sticky="w", padx=(6, 0))
        _block_mousewheel(col_white_spin)
        ttk.Label(col_grid, text="Gap width (px)").grid(row=1, column=0, sticky="w", pady=(4, 0))
        col_gap_spin = ttk.Spinbox(col_grid, from_=2, to=200, increment=1, textvariable=self.col_gap_width_var, width=7)
        col_gap_spin.grid(row=1, column=1, sticky="w", padx=(6, 0), pady=(4, 0))
        _block_mousewheel(col_gap_spin)
        ttk.Label(col_grid, text="Min col width (%)").grid(row=2, column=0, sticky="w", pady=(4, 0))
        min_col_spin = ttk.Spinbox(col_grid, from_=0.1, to=25.0, increment=0.1, textvariable=self.min_col_width_pct_var, width=7)
        min_col_spin.grid(row=2, column=1, sticky="w", padx=(6, 0), pady=(4, 0))
        _block_mousewheel(min_col_spin)
        ttk.Label(col_grid, text="Content threshold (%)").grid(row=3, column=0, sticky="w", pady=(4, 0))
        col_content_spin = ttk.Spinbox(col_grid, from_=0.1, to=50.0, increment=0.1, textvariable=self.col_content_threshold_var, width=7)
        col_content_spin.grid(row=3, column=1, sticky="w", padx=(6, 0), pady=(4, 0))
        _block_mousewheel(col_content_spin)

        ttk.Label(detect_frame, text="Data field", font=("TkDefaultFont", 9, "bold")).pack(anchor="w", padx=8, pady=(8, 2))
        data_grid = ttk.Frame(detect_frame)
        data_grid.pack(fill=tk.X, padx=8)
        ttk.Label(data_grid, text="Row kernel divisor").grid(row=0, column=0, sticky="w")
        row_kernel_spin = ttk.Spinbox(data_grid, from_=5, to=120, increment=1, textvariable=self.row_kernel_divisor_var, width=7)
        row_kernel_spin.grid(row=0, column=1, sticky="w", padx=(6, 0))
        _block_mousewheel(row_kernel_spin)
        ttk.Label(data_grid, text="Word kernel divisor").grid(row=1, column=0, sticky="w", pady=(4, 0))
        word_kernel_spin = ttk.Spinbox(data_grid, from_=5, to=80, increment=1, textvariable=self.word_kernel_divisor_var, width=7)
        word_kernel_spin.grid(row=1, column=1, sticky="w", padx=(6, 0), pady=(4, 0))
        _block_mousewheel(word_kernel_spin)

        detect_actions = ttk.Frame(detect_frame)
        detect_actions.pack(fill=tk.X, padx=8, pady=(8, 8))
        ttk.Checkbutton(detect_actions, text="Live Detect", variable=self.live_detect_var).pack(side=tk.LEFT)
        ttk.Button(detect_actions, text="Reset Defaults", command=self._reset_detection_settings).pack(side=tk.RIGHT)

        ttk.Separator(sidebar_inner, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10)

        ttk.Label(sidebar_inner, text="Regions", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        self.region_list = tk.Listbox(sidebar_inner, height=9, exportselection=False)
        self.region_list.pack(fill=tk.X, pady=(6, 8))
        self.region_list.bind("<<ListboxSelect>>", self._on_region_select)

        ttk.Label(sidebar_inner, text="Crop DPI").pack(anchor="w", pady=(4, 0))
        dpi_spin = ttk.Spinbox(sidebar_inner, from_=72, to=600, textvariable=self.dpi_var, width=8)
        dpi_spin.pack(anchor="w", pady=(0, 8))
        _block_mousewheel(dpi_spin)

        ttk.Label(sidebar_inner, text="Extraction Mode").pack(anchor="w", pady=(2, 0))
        mode_select = ttk.Combobox(
            sidebar_inner,
            textvariable=self.text_mode_var,
            values=("auto", "ocr_only", "text_only"),
            state="readonly",
            width=12,
        )
        mode_select.pack(anchor="w", pady=(0, 8))

        ttk.Label(sidebar_inner, text="Parser").pack(anchor="w", pady=(2, 0))
        parser_values = [self.parser_placeholder] + list(self.parser_options)
        parser_select = ttk.Combobox(
            sidebar_inner,
            textvariable=self.parser_name_var,
            values=parser_values,
            state="readonly",
            width=16,
        )
        parser_select.pack(anchor="w", pady=(0, 8))

        ttk.Button(sidebar_inner, text="Delete Region", command=self._delete_region).pack(fill=tk.X)
        ttk.Button(sidebar_inner, text="Save Template", command=self._save_template).pack(fill=tk.X, pady=(8, 0))

        ttk.Separator(sidebar_inner, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10)
        grid_frame = ttk.LabelFrame(sidebar_inner, text="Grid (data_field_grid) [disabled]")
        grid_frame.pack(fill=tk.BOTH, expand=False)

        self.grid_status_label = ttk.Label(grid_frame, text="Select a grid region")
        self.grid_status_label.pack(anchor="w", padx=8, pady=(6, 4))

        grid_info = ttk.Frame(grid_frame)
        grid_info.pack(fill=tk.X, padx=8)
        ttk.Label(grid_info, text="Rows").grid(row=0, column=0, sticky="w")
        ttk.Label(grid_info, textvariable=self.grid_rows_var).grid(row=0, column=1, sticky="w", padx=(6, 0))
        ttk.Label(grid_info, text="Cols").grid(row=0, column=2, sticky="w", padx=(12, 0))
        ttk.Label(grid_info, textvariable=self.grid_cols_var).grid(row=0, column=3, sticky="w", padx=(6, 0))

        ttk.Label(grid_frame, text="Rows").pack(anchor="w", padx=8, pady=(6, 0))
        self.grid_rows_list = tk.Listbox(grid_frame, height=5, exportselection=False)
        self.grid_rows_list.pack(fill=tk.X, padx=8, pady=(0, 4))
        self.grid_rows_list.bind("<<ListboxSelect>>", self._on_grid_row_select)

        row_edit = ttk.Frame(grid_frame)
        row_edit.pack(fill=tk.X, padx=8, pady=(0, 4))
        ttk.Label(row_edit, text="Height:").grid(row=0, column=0, sticky="w")
        ttk.Button(row_edit, text="-", command=lambda: self._adjust_row_height(-5), width=3).grid(row=0, column=1, padx=(6, 2))
        ttk.Entry(row_edit, textvariable=self.grid_row_height_var, width=6, justify="center").grid(row=0, column=2)
        ttk.Button(row_edit, text="+", command=lambda: self._adjust_row_height(5), width=3).grid(row=0, column=3, padx=(2, 0))
        ttk.Label(row_edit, text="%").grid(row=0, column=4, sticky="w", padx=(2, 0))

        self.row_height_slider = tk.Scale(
            grid_frame, from_=2, to=80, orient=tk.HORIZONTAL,
            variable=self.grid_row_height_var, showvalue=False,
            command=self._on_row_slider_change
        )
        self.row_height_slider.pack(fill=tk.X, padx=8, pady=(0, 6))

        row_buttons = ttk.Frame(grid_frame)
        row_buttons.pack(fill=tk.X, padx=8, pady=(0, 6))
        ttk.Button(row_buttons, text="Add", command=self._add_grid_row, width=8).pack(side=tk.LEFT)
        ttk.Button(row_buttons, text="Remove", command=self._remove_grid_row, width=8).pack(side=tk.LEFT, padx=4)
        ttk.Button(row_buttons, text="Equal Heights", command=self._equalize_row_heights, width=12).pack(side=tk.LEFT)

        ttk.Label(grid_frame, text="Columns").pack(anchor="w", padx=8, pady=(6, 0))
        self.grid_cols_list = tk.Listbox(grid_frame, height=5, exportselection=False)
        self.grid_cols_list.pack(fill=tk.X, padx=8, pady=(0, 4))
        self.grid_cols_list.bind("<<ListboxSelect>>", self._on_grid_col_select)

        col_edit = ttk.Frame(grid_frame)
        col_edit.pack(fill=tk.X, padx=8, pady=(0, 4))
        ttk.Label(col_edit, text="Label:").grid(row=0, column=0, sticky="w")
        label_entry = ttk.Entry(col_edit, textvariable=self.grid_col_label_var, width=18)
        label_entry.grid(row=0, column=1, columnspan=4, sticky="ew", padx=(6, 0))
        label_entry.bind("<Return>", lambda e: self._update_selected_col())
        label_entry.bind("<FocusOut>", lambda e: self._update_selected_col())
        
        ttk.Label(col_edit, text="Width:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Button(col_edit, text="-", command=lambda: self._adjust_col_width(-5), width=3).grid(row=1, column=1, padx=(6, 2), pady=(4, 0))
        ttk.Entry(col_edit, textvariable=self.grid_col_width_var, width=6, justify="center").grid(row=1, column=2, pady=(4, 0))
        ttk.Button(col_edit, text="+", command=lambda: self._adjust_col_width(5), width=3).grid(row=1, column=3, padx=(2, 0), pady=(4, 0))
        ttk.Label(col_edit, text="%").grid(row=1, column=4, sticky="w", padx=(2, 0), pady=(4, 0))

        self.col_width_slider = tk.Scale(
            grid_frame, from_=5, to=95, orient=tk.HORIZONTAL,
            variable=self.grid_col_width_var, showvalue=False,
            command=self._on_col_slider_change
        )
        self.col_width_slider.pack(fill=tk.X, padx=8, pady=(0, 6))

        col_buttons = ttk.Frame(grid_frame)
        col_buttons.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Button(col_buttons, text="Add", command=self._add_grid_col, width=8).pack(side=tk.LEFT)
        ttk.Button(col_buttons, text="Remove", command=self._remove_grid_col, width=8).pack(side=tk.LEFT, padx=4)
        ttk.Button(col_buttons, text="Equal Widths", command=self._equalize_col_widths, width=12).pack(side=tk.LEFT)

        self._set_grid_controls_enabled(grid_frame, enabled=False)


    def _on_load_pdf(self) -> None:
        file_path = filedialog.askopenfilename(
            title="Select PDF",
            filetypes=[("PDF Files", "*.pdf")],
        )
        if not file_path:
            return
        self._load_pdf(file_path)

    def _bind_detection_live_updates(self) -> None:
        for var in [
            self.row_content_threshold_var,
            self.min_row_height_pct_var,
            self.col_white_threshold_var,
            self.col_gap_width_var,
            self.min_col_width_pct_var,
            self.col_content_threshold_var,
            self.row_kernel_divisor_var,
            self.word_kernel_divisor_var,
        ]:
            var.trace_add("write", lambda *_: self._on_detection_settings_change())

    def _on_detection_settings_change(self) -> None:
        if not bool(self.live_detect_var.get()):
            return
        if self._detect_update_after_id and self.window.winfo_exists():
            try:
                self.window.after_cancel(self._detect_update_after_id)
            except Exception:
                pass
        if not self.window.winfo_exists():
            return
        self._detect_update_after_id = self.window.after(250, self._refresh_active_grid_detection)

    def _refresh_active_grid_detection(self) -> None:
        if not self.doc:
            return
        region = self._active_grid_region()
        if not region:
            return
        page_count = self.doc.page_count
        page_index = self._page_index_for_region(region, page_count)
        if page_index is None or not (0 <= page_index < page_count):
            return
        try:
            page = self.doc.load_page(page_index)
            dpi = self._parse_dpi()
            scale = scale_from_dpi(dpi)
            rect = PdfRect(
                float(region.x),
                float(region.y),
                float(region.x + region.width),
                float(region.y + region.height),
            )
            pix = page.render_pixmap(scale=scale, clip=rect)
            import tempfile
            temp_path = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
            pix.save(temp_path)
            try:
                grid = auto_detect_grid_structure(
                    temp_path,
                    region.label,
                    detection_settings=self._parse_detection_settings(),
                )
            finally:
                try:
                    import os
                    os.unlink(temp_path)
                except Exception:
                    pass

            if not grid:
                return

            if isinstance(region.grid, dict):
                existing_cols = region.grid.get("columns", [])
                new_cols = grid.get("columns", [])
                if len(existing_cols) == len(new_cols):
                    for idx, col in enumerate(existing_cols):
                        label = str(col.get("label", f"Column {idx + 1}"))
                        new_cols[idx]["label"] = label
                existing_rows = region.grid.get("rows", [])
                new_rows = grid.get("rows", [])
                if len(existing_rows) == len(new_rows):
                    grid["cell_labels"] = region.grid.get("cell_labels", {})

            region.grid = grid
            self._refresh_grid_lists(region)
            self._render_page()
        except Exception:
            return

    def _page_index_for_region(self, region: Region, page_count: int) -> int | None:
        if page_count <= 0:
            return None
        if region.page_scope == "specific":
            return max(0, min(page_count - 1, region.page - 1))
        if region.page_scope in ("first", "all"):
            return 0
        if region.page_scope == "last":
            return page_count - 1
        if region.page_scope == "middle":
            return 1 if page_count > 2 else 0
        return self.page_index

    def _load_pdf(self, file_path: str) -> None:
        self.doc = PdfDocument(file_path)
        self.source_pdf = file_path
        self.page_index = 0
        self._render_page()

    def _render_page(self) -> None:
        if not self.doc:
            return
        page = self.doc.load_page(self.page_index)
        pix = page.render_pixmap(scale=self.zoom)
        image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        self.page_image = ImageTk.PhotoImage(image)
        self.page_scale = pix.width / page.rect.width
        self.canvas.delete("all")
        self.canvas.config(scrollregion=(0, 0, pix.width, pix.height))
        self.canvas_image_id = self.canvas.create_image(0, 0, anchor="nw", image=self.page_image)
        self._render_regions()
        self.page_indicator.config(text=f"Page {self.page_index + 1}/{self.doc.page_count}")

    def _render_regions(self) -> None:
        self.canvas_region_map.clear()
        self.canvas_col_label_map.clear()
        self.region_list.delete(0, tk.END)
        current_page = self.page_index + 1
        page_count = self.doc.page_count if self.doc else 0
        for region in self.regions:
            display_text = self._region_display_text(region)
            self.region_list.insert(tk.END, display_text)
            if self._region_applies_to_page(region, current_page, page_count):
                x1 = region.x * self.page_scale
                y1 = region.y * self.page_scale
                x2 = (region.x + region.width) * self.page_scale
                y2 = (region.y + region.height) * self.page_scale
                rect_id = self.canvas.create_rectangle(
                    x1,
                    y1,
                    x2,
                    y2,
                    outline=self.REGION_OUTLINE_COLOR,
                    width=self.REGION_OUTLINE_WIDTH,
                )
                self.canvas_region_map[region.region_id] = rect_id
                if self._is_grid_region(region):
                    self._render_grid_overlay(region, x1, y1, x2, y2)

    def _prev_page(self) -> None:
        if not self.doc or self.page_index <= 0:
            return
        self.page_index -= 1
        self._render_page()

    def _next_page(self) -> None:
        if not self.doc or self.page_index >= self.doc.page_count - 1:
            return
        self.page_index += 1
        self._render_page()

    def _on_canvas_press(self, event: tk.Event) -> None:
        if not self.doc:
            return
        start_x = self.canvas.canvasx(event.x)
        start_y = self.canvas.canvasy(event.y)
        
        # Check if clicking on a column label for inline editing
        clicked_items = self.canvas.find_overlapping(start_x - 2, start_y - 2, start_x + 2, start_y + 2)
        for item_id in clicked_items:
            if item_id in self.canvas_col_label_map:
                self._start_inline_column_edit(item_id, start_x, start_y)
                return
        
        self.drag_start = (start_x, start_y)
        if self.canvas_rect_id:
            self.canvas.delete(self.canvas_rect_id)
        self.canvas_rect_id = self.canvas.create_rectangle(
            start_x, start_y, start_x, start_y, outline="#ff7043", width=2, dash=(4, 2)
        )

    def _on_canvas_drag(self, event: tk.Event) -> None:
        if not self.drag_start or not self.canvas_rect_id:
            return
        current_x = self.canvas.canvasx(event.x)
        current_y = self.canvas.canvasy(event.y)
        self.canvas.coords(self.canvas_rect_id, self.drag_start[0], self.drag_start[1], current_x, current_y)

    def _on_canvas_release(self, event: tk.Event) -> None:
        if not self.drag_start or not self.canvas_rect_id:
            return
        start_x, start_y = self.drag_start
        end_x = self.canvas.canvasx(event.x)
        end_y = self.canvas.canvasy(event.y)
        self.drag_start = None

        x1, x2 = sorted([start_x, end_x])
        y1, y2 = sorted([start_y, end_y])
        if abs(x2 - x1) < 4 or abs(y2 - y1) < 4:
            self.canvas.delete(self.canvas_rect_id)
            self.canvas_rect_id = None
            return

        # Grid mode: auto-create grid with auto-detection from image
        if self.drawing_mode == "grid":
            label, page_scope = self._prompt_grid_label()
            if not label:
                self.canvas.delete(self.canvas_rect_id)
                self.canvas_rect_id = None
                return
            
            # Create temporary crop to analyze for grid structure
            if not self.doc:
                self.canvas.delete(self.canvas_rect_id)
                self.canvas_rect_id = None
                return
            
            page = self.doc.load_page(self.page_index)
            dpi = self._parse_dpi()
            scale = scale_from_dpi(dpi)
            
            rect = PdfRect(
                x1 / self.page_scale,
                y1 / self.page_scale,
                x2 / self.page_scale,
                y2 / self.page_scale,
            )
            
            pix = page.render_pixmap(scale=scale, clip=rect)
            import tempfile
            temp_path = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
            pix.save(temp_path)
            
            # Auto-detect grid structure
            grid = auto_detect_grid_structure(temp_path, label, detection_settings=self._parse_detection_settings())
            if not grid:
                # Fallback to default 5x3 grid if detection fails
                messagebox.showwarning(
                    "Grid Detection Failed",
                    "Could not auto-detect grid structure from the image.\nUsing default 5 rows × 3 columns.",
                )
                grid = self._build_default_grid(5, 3)
            
            # Clean up temp file
            try:
                import os
                os.unlink(temp_path)
            except Exception:
                pass
            
            # Switch back to normal mode after creating grid
            self.drawing_mode = "region"
            self._update_mode_ui()
        else:
            # Normal region mode
            label, page_scope = self._prompt_region_metadata()
            if not label:
                self.canvas.delete(self.canvas_rect_id)
                self.canvas_rect_id = None
                return
            
            grid: dict[str, Any] | None = None
            # Still support creating grids via label prefix
            if self._is_grid_label(label):
                rows, cols = self._prompt_grid_dimensions()
                if rows is None or cols is None:
                    self.canvas.delete(self.canvas_rect_id)
                    self.canvas_rect_id = None
                    return
                grid = self._build_default_grid(rows, cols)

        region = Region(
            region_id=str(uuid4()),
            page=self.page_index + 1,
            page_scope=page_scope,
            label=label,
            x=x1 / self.page_scale,
            y=y1 / self.page_scale,
            width=(x2 - x1) / self.page_scale,
            height=(y2 - y1) / self.page_scale,
            grid=grid,
        )
        self.regions.append(region)
        self.canvas_rect_id = None
        self._render_page()

    def _on_region_select(self, _: tk.Event) -> None:
        selection = self.region_list.curselection()
        for rect_id in self.canvas_region_map.values():
            self.canvas.itemconfig(
                rect_id,
                outline=self.REGION_OUTLINE_COLOR,
                width=self.REGION_OUTLINE_WIDTH,
            )
        if not selection:
            # Only clear grid controls if we don't have an active grid already
            if not self.grid_active_region_id:
                self._set_grid_controls(None)
            return
        index = selection[0]
        region = self.regions[index]
        self._set_grid_controls(region)
        current_page = self.page_index + 1
        page_count = self.doc.page_count if self.doc else 0
        if not self._region_applies_to_page(region, current_page, page_count):
            return
        rect_id = self.canvas_region_map.get(region.region_id)
        if rect_id:
            self.canvas.itemconfig(
                rect_id,
                outline=self.REGION_SELECTED_COLOR,
                width=self.REGION_SELECTED_WIDTH,
            )

    def _delete_region(self) -> None:
        selection = self.region_list.curselection()
        if not selection:
            return
        index = selection[0]
        self.regions.pop(index)
        self._render_page()
        self._set_grid_controls(None)

    def _save_template(self) -> None:
        payload = dict(self.template_payload)
        parser_name = str(self.parser_name_var.get() or "").strip()
        if not parser_name or parser_name == self.parser_placeholder:
            messagebox.showerror("Parser Required", "Select a parser before saving the template.")
            return
        try:
            get_parser(parser_name)
        except ValueError as exc:
            messagebox.showerror("Invalid Parser", str(exc))
            return
        payload["regions"] = [region.to_payload() for region in self.regions]
        payload["source_pdf"] = self.source_pdf
        payload["dpi"] = self._parse_dpi()
        payload["text_mode"] = str(self.text_mode_var.get() or "auto")
        payload["detection_settings"] = self._parse_detection_settings()
        payload["parser_name"] = parser_name
        self.template_service.save_template_version(template_id=self.template_id, payload=payload)
        messagebox.showinfo("Saved", "Template regions saved.")

    def _parse_dpi(self) -> int:
        try:
            dpi = int(self.dpi_var.get())
        except ValueError:
            dpi = 300
        return max(72, min(dpi, 600))

    def _load_detection_settings(self, settings: dict[str, Any]) -> None:
        row = settings.get("row", {})
        col = settings.get("col", {})
        data_field = settings.get("data_field", {})

        self.row_content_threshold_var.set(f"{float(row.get('content_threshold', 0.20)) * 100:.2f}")
        self.min_row_height_pct_var.set(f"{float(row.get('min_row_height_pct', 1.0)):.2f}")
        self.col_white_threshold_var.set(f"{float(col.get('white_threshold', 0.001)) * 100:.2f}")
        self.col_gap_width_var.set(str(int(col.get("gap_width_px", 20))))
        self.min_col_width_pct_var.set(f"{float(col.get('min_col_width_pct', 1.0)):.2f}")
        self.col_content_threshold_var.set(f"{float(col.get('content_threshold', 0.05)) * 100:.2f}")
        self.row_kernel_divisor_var.set(str(int(data_field.get("row_kernel_divisor", 40))))
        self.word_kernel_divisor_var.set(str(int(data_field.get("word_kernel_divisor", 15))))

    def _reset_detection_settings(self) -> None:
        self._load_detection_settings(default_detection_settings())

    def _parse_detection_settings(self) -> dict[str, Any]:
        settings = merge_detection_settings(self.template_payload.get("detection_settings"))

        def _safe_float(value: str, default: float) -> float:
            try:
                return float(value)
            except ValueError:
                return default

        row = settings.get("row", {})
        col = settings.get("col", {})
        data_field = settings.get("data_field", {})

        row_content_pct = _safe_float(self.row_content_threshold_var.get(), float(row.get("content_threshold", 0.20)) * 100)
        min_row_height_pct = _safe_float(self.min_row_height_pct_var.get(), float(row.get("min_row_height_pct", 1.0)))
        col_white_pct = _safe_float(self.col_white_threshold_var.get(), float(col.get("white_threshold", 0.001)) * 100)
        col_gap_width = _safe_float(self.col_gap_width_var.get(), float(col.get("gap_width_px", 20)))
        min_col_width_pct = _safe_float(self.min_col_width_pct_var.get(), float(col.get("min_col_width_pct", 1.0)))
        col_content_pct = _safe_float(self.col_content_threshold_var.get(), float(col.get("content_threshold", 0.05)) * 100)
        row_kernel_divisor = _safe_float(self.row_kernel_divisor_var.get(), float(data_field.get("row_kernel_divisor", 40)))
        word_kernel_divisor = _safe_float(self.word_kernel_divisor_var.get(), float(data_field.get("word_kernel_divisor", 15)))

        settings["row"] = {
            "content_threshold": row_content_pct / 100.0,
            "min_row_height_pct": min_row_height_pct,
        }
        settings["col"] = {
            "white_threshold": col_white_pct / 100.0,
            "gap_width_px": int(col_gap_width),
            "min_col_width_pct": min_col_width_pct,
            "content_threshold": col_content_pct / 100.0,
        }
        settings["data_field"] = {
            "row_kernel_divisor": int(row_kernel_divisor),
            "word_kernel_divisor": int(word_kernel_divisor),
        }

        return merge_detection_settings(settings)

    def _prompt_region_metadata(self) -> tuple[str | None, str]:
        dialog = tk.Toplevel(self.window)
        dialog.title("Select Region Label")
        dialog.transient(self.window)
        dialog.grab_set()

        ttk.Label(dialog, text="Label").pack(anchor="w", padx=12, pady=(12, 4))
        label_var = tk.StringVar(value=self.label_options[0] if self.label_options else "")
        label_box = ttk.Combobox(
            dialog,
            textvariable=label_var,
            values=self.label_options,
            state="readonly",
        )
        label_box.pack(fill=tk.X, padx=12, pady=(0, 12))

        ttk.Label(dialog, text="Page Scope").pack(anchor="w", padx=12, pady=(0, 4))
        default_scope = self._default_page_scope()
        scope_var = tk.StringVar(value=default_scope)
        scope_box = ttk.Combobox(
            dialog,
            textvariable=scope_var,
            values=list(self.PAGE_SCOPES),
            state="readonly",
        )
        scope_box.pack(fill=tk.X, padx=12, pady=(0, 12))

        result_label: list[str | None] = [None]
        result_scope: list[str] = [default_scope]

        def on_ok() -> None:
            result_label[0] = label_var.get()
            result_scope[0] = scope_var.get()
            dialog.destroy()

        def on_cancel() -> None:
            dialog.destroy()

        button_frame = ttk.Frame(dialog)
        button_frame.pack(fill=tk.X, padx=12, pady=(0, 12))
        ttk.Button(button_frame, text="Cancel", command=on_cancel).pack(side=tk.RIGHT)
        ttk.Button(button_frame, text="OK", command=on_ok).pack(side=tk.RIGHT, padx=6)

        dialog.wait_window()
        return result_label[0], result_scope[0]

    def _default_page_scope(self) -> str:
        if not self.doc:
            return "specific"
        if self.doc.page_count == 1:
            return "first"
        # For multi-page documents, default to 'all' for consistent processing
        if self.doc.page_count > 1:
            return "all"
        if self.page_index == 0:
            return "first"
        if self.page_index == self.doc.page_count - 1:
            return "last"
        return "middle"

    def _region_display_text(self, region: Region) -> str:
        grid_tag = " [grid]" if self._is_grid_region(region) else ""
        if region.page_scope == "specific":
            return f"{region.label}{grid_tag} (p{region.page})"
        return f"{region.label}{grid_tag} ({region.page_scope})"

    def _is_grid_label(self, label: str) -> bool:
        return label.strip().lower().startswith("data_field_grid")

    def _is_grid_region(self, region: Region) -> bool:
        return self._is_grid_label(region.label) and isinstance(region.grid, dict)

    def _prompt_grid_dimensions(self) -> tuple[int | None, int | None]:
        dialog = tk.Toplevel(self.window)
        dialog.title("Grid Dimensions")
        dialog.transient(self.window)
        dialog.grab_set()

        ttk.Label(dialog, text="Rows").grid(row=0, column=0, padx=12, pady=(12, 6), sticky="w")
        rows_var = tk.StringVar(value="5")
        ttk.Spinbox(dialog, from_=1, to=50, textvariable=rows_var, width=6).grid(row=0, column=1, padx=12, pady=(12, 6), sticky="w")

        ttk.Label(dialog, text="Columns").grid(row=1, column=0, padx=12, pady=(0, 12), sticky="w")
        cols_var = tk.StringVar(value="3")
        ttk.Spinbox(dialog, from_=1, to=20, textvariable=cols_var, width=6).grid(row=1, column=1, padx=12, pady=(0, 12), sticky="w")

        result: list[tuple[int | None, int | None]] = [(None, None)]

        def on_ok() -> None:
            try:
                rows_val = max(1, int(rows_var.get()))
                cols_val = max(1, int(cols_var.get()))
            except ValueError:
                rows_val, cols_val = 5, 3
            result[0] = (rows_val, cols_val)
            dialog.destroy()

        def on_cancel() -> None:
            dialog.destroy()

        buttons = ttk.Frame(dialog)
        buttons.grid(row=2, column=0, columnspan=2, padx=12, pady=(0, 12), sticky="e")
        ttk.Button(buttons, text="Cancel", command=on_cancel).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="OK", command=on_ok).pack(side=tk.RIGHT, padx=(0, 6))

        dialog.wait_window()
        return result[0]

    def _build_default_grid(self, rows: int, cols: int) -> dict[str, Any]:
        row_ratio = 1.0 / max(rows, 1)
        col_ratio = 1.0 / max(cols, 1)
        return {
            "type": "explicit",
            "rows": [{"height_ratio": row_ratio} for _ in range(rows)],
            "columns": [
                {"label": f"Column {idx + 1}", "width_ratio": col_ratio}
                for idx in range(cols)
            ],
            "cell_labels": {},
        }

    def _render_grid_overlay(self, region: Region, x1: float, y1: float, x2: float, y2: float) -> None:
        grid = region.grid or {}
        rows = grid.get("rows", [])
        cols = grid.get("columns", [])
        if not rows or not cols:
            return
        row_ratios = self._normalize_ratios([float(r.get("height_ratio", 0.0)) for r in rows])
        col_ratios = self._normalize_ratios([float(c.get("width_ratio", 0.0)) for c in cols])

        height = y2 - y1
        width = x2 - x1

        y_cursor = y1
        for ratio in row_ratios[:-1]:
            y_cursor += height * ratio
            self.canvas.create_line(x1, y_cursor, x2, y_cursor, fill="#66bb6a", width=1)

        x_cursor = x1
        for idx, ratio in enumerate(col_ratios[:-1]):
            x_cursor += width * ratio
            self.canvas.create_line(x_cursor, y1, x_cursor, y2, fill="#66bb6a", width=1)

        # Column labels on first row (clickable for inline editing)
        label_y = y1 + 6
        x_cursor = x1
        for idx, ratio in enumerate(col_ratios):
            col_width = width * ratio
            col = cols[idx]
            label = str(col.get("label", f"Column {idx + 1}"))
            text_id = self.canvas.create_text(
                x_cursor + col_width / 2,
                label_y,
                text=label,
                fill="#2e7d32",
                font=("TkDefaultFont", 8),
            )
            # Track this text item for click detection
            self.canvas_col_label_map[text_id] = (region.region_id, idx)
            x_cursor += col_width

    def _normalize_ratios(self, ratios: list[float]) -> list[float]:
        total = sum(r for r in ratios if r > 0)
        if total <= 0:
            count = max(len(ratios), 1)
            return [1.0 / count for _ in ratios]
        return [max(r, 0.0) / total for r in ratios]

    def _set_grid_controls_enabled(self, parent: tk.Widget, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"

        def _walk(widget: tk.Widget) -> None:
            for child in widget.winfo_children():
                try:
                    child.configure(state=state)
                except Exception:
                    pass
                _walk(child)

        _walk(parent)

    def _selected_region(self) -> Region | None:
        selection = self.region_list.curselection()
        if not selection:
            return None
        return self.regions[selection[0]]

    def _set_grid_controls(self, region: Region | None) -> None:
        if not region or not self._is_grid_label(region.label):
            self.grid_active_region_id = None
            self.grid_status_label.config(text="Select a grid region")
            self.grid_rows_var.set("0")
            self.grid_cols_var.set("0")
            self.grid_rows_list.delete(0, tk.END)
            self.grid_cols_list.delete(0, tk.END)
            self.grid_row_height_var.set(1.0)  # Scale requires numeric value
            self.grid_col_width_var.set(1.0)   # Scale requires numeric value
            self.grid_col_label_var.set("")
            return

        if not isinstance(region.grid, dict):
            region.grid = self._build_default_grid(rows=5, cols=3)
        self.grid_active_region_id = region.region_id
        self.grid_status_label.config(text=f"Grid: {region.label}")
        self._refresh_grid_lists(region)

    def _refresh_grid_lists(self, region: Region) -> None:
        grid = region.grid or {}
        rows = grid.get("rows", [])
        cols = grid.get("columns", [])
        row_ratios = self._normalize_ratios([float(r.get("height_ratio", 0.0)) for r in rows])
        col_ratios = self._normalize_ratios([float(c.get("width_ratio", 0.0)) for c in cols])

        row_selection = self._selected_grid_row_idx
        col_selection = self._selected_grid_col_idx

        self.grid_rows_var.set(str(len(rows)))
        self.grid_cols_var.set(str(len(cols)))

        self.grid_rows_list.delete(0, tk.END)
        for idx, ratio in enumerate(row_ratios):
            self.grid_rows_list.insert(tk.END, f"Row {idx + 1} - {ratio * 100:.1f}%")
        if row_selection is not None and 0 <= row_selection < len(row_ratios):
            self.grid_rows_list.selection_set(row_selection)
            self.grid_rows_list.activate(row_selection)

        self.grid_cols_list.delete(0, tk.END)
        for idx, ratio in enumerate(col_ratios):
            label = str(cols[idx].get("label", f"Column {idx + 1}"))
            self.grid_cols_list.insert(tk.END, f"Col {idx + 1}: {label} - {ratio * 100:.1f}%")
        if col_selection is not None and 0 <= col_selection < len(col_ratios):
            self.grid_cols_list.selection_set(col_selection)
            self.grid_cols_list.activate(col_selection)

    def _active_grid_region(self) -> Region | None:
        if not self.grid_active_region_id:
            return None
        for region in self.regions:
            if region.region_id == self.grid_active_region_id:
                return region
        return None

    def _on_grid_row_select(self, _: tk.Event) -> None:
        region = self._active_grid_region()
        if not region or not region.grid:
            return
        selection = self.grid_rows_list.curselection()
        if not selection:
            return
        idx = selection[0]
        self._selected_grid_row_idx = idx
        rows = region.grid.get("rows", [])
        if idx >= len(rows):
            return
        ratio = float(rows[idx].get("height_ratio", 0.0))
        self._updating_slider = True
        self.grid_row_height_var.set(f"{ratio * 100:.1f}")
        self._updating_slider = False

    def _on_grid_col_select(self, _: tk.Event) -> None:
        region = self._active_grid_region()
        if not region or not region.grid:
            return
        selection = self.grid_cols_list.curselection()
        if not selection:
            return
        idx = selection[0]
        self._selected_grid_col_idx = idx
        cols = region.grid.get("columns", [])
        if idx >= len(cols):
            return
        col = cols[idx]
        self.grid_col_label_var.set(str(col.get("label", f"Column {idx + 1}")))
        ratio = float(col.get("width_ratio", 0.0))
        self._updating_slider = True
        self.grid_col_width_var.set(f"{ratio * 100:.1f}")
        self._updating_slider = False

    def _update_selected_row_height(self) -> None:
        region = self._active_grid_region()
        if not region or not region.grid:
            return
        selection = self.grid_rows_list.curselection()
        if not selection:
            return
        idx = selection[0]
        try:
            new_pct = float(self.grid_row_height_var.get())
        except ValueError:
            return
        self._set_ratio(region.grid.get("rows", []), idx, new_pct / 100.0, "height_ratio")
        self._refresh_grid_lists(region)
        self._render_page()
    
    def _adjust_row_height(self, delta: float) -> None:
        """Increment or decrement the selected row height by delta percentage."""
        try:
            current = float(self.grid_row_height_var.get())
        except ValueError:
            return
        new_value = max(2.0, min(80.0, current + delta))
        self.grid_row_height_var.set(f"{new_value:.1f}")
        self._update_selected_row_height()
    
    def _on_row_slider_change(self, value: str) -> None:
        """Handle row height slider changes."""
        if self._updating_slider:
            return
        self._update_selected_row_height()
    
    def _equalize_row_heights(self) -> None:
        """Set all row heights to equal values."""
        region = self._active_grid_region()
        if not region or not region.grid:
            return
        rows = region.grid.get("rows", [])
        if not rows:
            return
        equal_ratio = 1.0 / len(rows)
        for row in rows:
            row["height_ratio"] = equal_ratio
        self._refresh_grid_lists(region)
        self._render_page()

    def _update_selected_col(self) -> None:
        region = self._active_grid_region()
        if not region or not region.grid:
            return
        selection = self.grid_cols_list.curselection()
        if not selection:
            return
        idx = selection[0]
        cols = region.grid.get("columns", [])
        if idx >= len(cols):
            return
        cols[idx]["label"] = self.grid_col_label_var.get().strip() or f"Column {idx + 1}"
        try:
            new_pct = float(self.grid_col_width_var.get())
        except ValueError:
            new_pct = None
        if new_pct is not None:
            self._set_ratio(cols, idx, new_pct / 100.0, "width_ratio")
        self._refresh_grid_lists(region)
        self._render_page()
    
    def _adjust_col_width(self, delta: float) -> None:
        """Increment or decrement the selected column width by delta percentage."""
        try:
            current = float(self.grid_col_width_var.get())
        except ValueError:
            return
        new_value = max(5.0, min(95.0, current + delta))
        self.grid_col_width_var.set(f"{new_value:.1f}")
        self._update_selected_col()
    
    def _on_col_slider_change(self, value: str) -> None:
        """Handle column width slider changes."""
        if self._updating_slider:
            return
        self._update_selected_col()
    
    def _equalize_col_widths(self) -> None:
        """Set all column widths to equal values."""
        region = self._active_grid_region()
        if not region or not region.grid:
            return
        cols = region.grid.get("columns", [])
        if not cols:
            return
        equal_ratio = 1.0 / len(cols)
        for col in cols:
            col["width_ratio"] = equal_ratio
        self._refresh_grid_lists(region)
        self._render_page()

    def _add_grid_row(self) -> None:
        region = self._active_grid_region()
        if not region or not region.grid:
            return
        rows = region.grid.setdefault("rows", [])
        rows.append({"height_ratio": 1.0})
        self._normalize_ratio_list(rows, "height_ratio")
        self._refresh_grid_lists(region)
        self._render_page()

    def _remove_grid_row(self) -> None:
        region = self._active_grid_region()
        if not region or not region.grid:
            return
        selection = self.grid_rows_list.curselection()
        if not selection:
            return
        idx = selection[0]
        rows = region.grid.get("rows", [])
        if len(rows) <= 1:
            return
        rows.pop(idx)
        self._normalize_ratio_list(rows, "height_ratio")
        self._refresh_grid_lists(region)
        self._render_page()

    def _add_grid_col(self) -> None:
        region = self._active_grid_region()
        if not region or not region.grid:
            return
        cols = region.grid.setdefault("columns", [])
        cols.append({"label": f"Column {len(cols) + 1}", "width_ratio": 1.0})
        self._normalize_ratio_list(cols, "width_ratio")
        self._refresh_grid_lists(region)
        self._render_page()

    def _remove_grid_col(self) -> None:
        region = self._active_grid_region()
        if not region or not region.grid:
            return
        selection = self.grid_cols_list.curselection()
        if not selection:
            return
        idx = selection[0]
        cols = region.grid.get("columns", [])
        if len(cols) <= 1:
            return
        cols.pop(idx)
        self._normalize_ratio_list(cols, "width_ratio")
        self._refresh_grid_lists(region)
        self._render_page()

    def _set_ratio(self, items: list[dict[str, Any]], index: int, new_ratio: float, key: str) -> None:
        if not items or index < 0 or index >= len(items):
            return
        new_ratio = max(0.02, min(new_ratio, 0.98))
        total_other = sum(float(item.get(key, 0.0)) for i, item in enumerate(items) if i != index)
        remaining = max(0.0, 1.0 - new_ratio)
        if total_other <= 0:
            per = remaining / max(len(items) - 1, 1)
            for i, item in enumerate(items):
                item[key] = new_ratio if i == index else per
        else:
            for i, item in enumerate(items):
                if i == index:
                    item[key] = new_ratio
                else:
                    current = float(item.get(key, 0.0))
                    item[key] = current * (remaining / total_other)

    def _normalize_ratio_list(self, items: list[dict[str, Any]], key: str) -> None:
        ratios = [float(item.get(key, 0.0)) for item in items]
        normed = self._normalize_ratios(ratios)
        for item, ratio in zip(items, normed):
            item[key] = ratio

    def _toggle_grid_mode(self) -> None:
        """Toggle between normal region drawing and grid creation mode."""
        if self.drawing_mode == "region":
            self.drawing_mode = "grid"
        else:
            self.drawing_mode = "region"
        self._update_mode_ui()
    
    def _update_mode_ui(self) -> None:
        """Update UI to reflect current drawing mode."""
        if self.drawing_mode == "grid":
            self.mode_label.config(text="Mode: Create Grid", foreground="#2e7d32")
            self.create_grid_btn.config(text="✓ Grid Mode")
            self.canvas.config(cursor="crosshair")
        else:
            self.mode_label.config(text="Mode: Normal", foreground="#666")
            self.create_grid_btn.config(text="✚ Create Grid")
            self.canvas.config(cursor="")
    
    def _prompt_grid_label(self) -> tuple[str | None, str]:
        """Prompt for grid label only (dimensions are auto-detected)."""
        dialog = tk.Toplevel(self.window)
        dialog.title("Create Grid Region")
        dialog.transient(self.window)
        dialog.grab_set()
        
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(frame, text="Select a label for this grid:", font=("TkDefaultFont", 9, "bold")).pack(anchor="w", pady=(0, 8))
        
        label_var = tk.StringVar(value="data_field_grid_1")
        label_combo = ttk.Combobox(
            frame,
            textvariable=label_var,
            values=[opt for opt in self.label_options if "grid" in opt.lower()] or ["data_field_grid_1", "data_field_grid_2"],
            width=25,
            state="readonly",
        )
        label_combo.pack(fill=tk.X, pady=8)
        
        ttk.Label(frame, text="Page Scope:").pack(anchor="w", pady=(8, 4))
        default_scope = self._default_page_scope()
        scope_var = tk.StringVar(value=default_scope)
        scope_combo = ttk.Combobox(
            frame,
            textvariable=scope_var,
            values=list(self.PAGE_SCOPES),
            width=25,
            state="readonly",
        )
        scope_combo.pack(fill=tk.X, pady=(0, 8))
        
        info_text = ttk.Label(frame, text="Grid dimensions will be auto-detected\nfrom the selected region.", foreground="#666")
        info_text.pack(anchor="w", pady=(8, 16))
        
        result: list[tuple[str | None, str]] = [(None, default_scope)]
        
        def on_ok() -> None:
            label = label_var.get().strip()
            if not label:
                label = "data_field_grid_1"
            result[0] = (label, scope_var.get())
            dialog.destroy()
        
        def on_cancel() -> None:
            dialog.destroy()
        
        buttons = ttk.Frame(frame)
        buttons.pack(fill=tk.X, pady=(8, 0), anchor="e")
        ttk.Button(buttons, text="Cancel", command=on_cancel).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(buttons, text="Create", command=on_ok, default=tk.ACTIVE).pack(side=tk.RIGHT)
        
        dialog.wait_window()
        return result[0]

    def _start_inline_column_edit(self, text_id: int, canvas_x: float, canvas_y: float) -> None:
        """Start inline editing of a column label on the canvas."""
        if text_id not in self.canvas_col_label_map:
            return
        
        region_id, col_idx = self.canvas_col_label_map[text_id]
        region = None
        for r in self.regions:
            if r.region_id == region_id:
                region = r
                break
        if not region or not region.grid:
            return
        
        cols = region.grid.get("columns", [])
        if col_idx >= len(cols):
            return
        
        current_label = str(cols[col_idx].get("label", f"Column {col_idx + 1}"))
        
        # Get the text item position
        bbox = self.canvas.bbox(text_id)
        if not bbox:
            return
        
        # Create an entry widget on the canvas
        entry_var = tk.StringVar(value=current_label)
        entry = tk.Entry(self.canvas, textvariable=entry_var, font=("TkDefaultFont", 8), width=15)
        entry_window = self.canvas.create_window(
            (bbox[0] + bbox[2]) / 2,
            (bbox[1] + bbox[3]) / 2,
            window=entry,
        )
        
        self.inline_edit_entry = entry
        self.inline_edit_data = (region_id, col_idx)
        
        entry.select_range(0, tk.END)
        entry.focus_set()
        
        def on_commit(event=None) -> None:
            self._commit_inline_column_edit(entry_var.get())
        
        def on_cancel(event=None) -> None:
            self._cancel_inline_column_edit()
        
        entry.bind("<Return>", on_commit)
        entry.bind("<Escape>", on_cancel)
        entry.bind("<FocusOut>", on_commit)
    
    def _commit_inline_column_edit(self, new_label: str) -> None:
        """Save the edited column label and refresh."""
        if not self.inline_edit_entry or not self.inline_edit_data:
            return
        
        region_id, col_idx = self.inline_edit_data
        region = None
        for r in self.regions:
            if r.region_id == region_id:
                region = r
                break
        
        if region and region.grid:
            cols = region.grid.get("columns", [])
            if col_idx < len(cols):
                cols[col_idx]["label"] = new_label.strip() or f"Column {col_idx + 1}"
                # Update the side panel if this region is active
                if self.grid_active_region_id == region_id:
                    self._refresh_grid_lists(region)
        
        self._cleanup_inline_edit()
        self._render_page()
    
    def _cancel_inline_column_edit(self) -> None:
        """Cancel inline editing without saving."""
        self._cleanup_inline_edit()
    
    def _cleanup_inline_edit(self) -> None:
        """Remove the inline edit entry widget."""
        if self.inline_edit_entry:
            try:
                self.inline_edit_entry.destroy()
            except Exception:
                pass
            self.inline_edit_entry = None
        self.inline_edit_data = None

    def _region_applies_to_page(self, region: Region, page_number: int, page_count: int) -> bool:
        if region.page_scope == "all":
            return True
        if region.page_scope == "specific":
            return region.page == page_number
        if region.page_scope == "first":
            return page_number == 1
        if region.page_scope == "last":
            return page_count > 0 and page_number == page_count
        if region.page_scope == "middle":
            return page_count > 2 and 1 < page_number < page_count
        return False

    def _apply_zoom(self, factor: float) -> None:
        if not self.doc:
            return
        new_zoom = min(max(self.zoom * factor, 0.5), 4.0)
        if abs(new_zoom - self.zoom) < 0.01:
            return
        self.zoom = new_zoom
        self._render_page()

    def _on_mouse_wheel(self, event: tk.Event) -> None:
        if event.num == 4 or event.delta > 0:
            self.canvas.yview_scroll(-1, "units")
        elif event.num == 5 or event.delta < 0:
            self.canvas.yview_scroll(1, "units")

    def _on_zoom_wheel(self, event: tk.Event) -> None:
        if event.delta > 0:
            self._apply_zoom(1.1)
        elif event.delta < 0:
            self._apply_zoom(1 / 1.1)
