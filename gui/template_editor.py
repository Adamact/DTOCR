from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import fitz
import tkinter as tk
from PIL import Image, ImageTk
from tkinter import filedialog, messagebox, ttk

from DTOCR.services.template_service import TemplateService


@dataclass
class Region:
    region_id: str
    page: int
    label: str
    x: float
    y: float
    width: float
    height: float

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.region_id,
            "page": self.page,
            "label": self.label,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


LABEL_OPTIONS = (
    "invoice_number",
    "invoice_date",
    "invoice_total",
    "vendor_name",
    "bill_to",
    "ship_to",
)


class TemplateEditor:
    def __init__(
        self,
        parent: tk.Tk,
        template_service: TemplateService,
        template_id: int,
        template_payload: dict[str, Any],
    ) -> None:
        self.template_service = template_service
        self.template_id = template_id
        self.template_payload = template_payload
        self.regions: list[Region] = [
            Region(
                region_id=region.get("id", str(uuid4())),
                page=region.get("page", 1),
                label=region.get("label", ""),
                x=region.get("x", 0.0),
                y=region.get("y", 0.0),
                width=region.get("width", 0.0),
                height=region.get("height", 0.0),
            )
            for region in template_payload.get("regions", [])
        ]
        self.source_pdf = template_payload.get("source_pdf", "")

        self.window = tk.Toplevel(parent)
        self.window.title(f"Template Editor: {template_payload.get('name', '')}")
        self.window.geometry("1100x720")

        self.doc: fitz.Document | None = None
        self.page_index = 0
        self.page_scale = 1.0
        self.zoom = 1.0
        self.page_image: ImageTk.PhotoImage | None = None
        self.canvas_image_id: int | None = None
        self.canvas_rect_id: int | None = None
        self.canvas_region_map: dict[str, int] = {}
        self.drag_start: tuple[int, int] | None = None

        self._build_ui()

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
        self.pdf_label = ttk.Label(controls, text=self.source_pdf or "No PDF loaded")
        self.pdf_label.pack(side=tk.LEFT, padx=12)

        nav_frame = ttk.Frame(controls)
        nav_frame.pack(side=tk.RIGHT)
        ttk.Button(nav_frame, text="◀ Prev", command=self._prev_page).pack(side=tk.LEFT, padx=4)
        ttk.Button(nav_frame, text="Next ▶", command=self._next_page).pack(side=tk.LEFT, padx=4)

        self.page_indicator = ttk.Label(nav_frame, text="Page 0/0")
        self.page_indicator.pack(side=tk.LEFT, padx=8)

        body = ttk.Frame(container)
        body.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

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
        sidebar.pack(side=tk.RIGHT, fill=tk.Y, padx=(12, 0))

        ttk.Label(sidebar, text="Regions", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        self.region_list = tk.Listbox(sidebar, height=20)
        self.region_list.pack(fill=tk.BOTH, expand=True, pady=(6, 8))
        self.region_list.bind("<<ListboxSelect>>", self._on_region_select)

        ttk.Button(sidebar, text="Delete Region", command=self._delete_region).pack(fill=tk.X)
        ttk.Button(sidebar, text="Save Template", command=self._save_template).pack(fill=tk.X, pady=(8, 0))

    def _on_load_pdf(self) -> None:
        file_path = filedialog.askopenfilename(
            title="Select PDF",
            filetypes=[("PDF Files", "*.pdf")],
        )
        if not file_path:
            return
        self._load_pdf(file_path)

    def _load_pdf(self, file_path: str) -> None:
        self.doc = fitz.open(file_path)
        self.source_pdf = file_path
        self.pdf_label.config(text=file_path)
        self.page_index = 0
        self._render_page()

    def _render_page(self) -> None:
        if not self.doc:
            return
        page = self.doc.load_page(self.page_index)
        matrix = fitz.Matrix(self.zoom, self.zoom)
        pix = page.get_pixmap(matrix=matrix)
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
        self.region_list.delete(0, tk.END)
        current_page = self.page_index + 1
        for region in self.regions:
            display_text = f"{region.label} (p{region.page})"
            self.region_list.insert(tk.END, display_text)
            if region.page == current_page:
                x1 = region.x * self.page_scale
                y1 = region.y * self.page_scale
                x2 = (region.x + region.width) * self.page_scale
                y2 = (region.y + region.height) * self.page_scale
                rect_id = self.canvas.create_rectangle(x1, y1, x2, y2, outline="#1976d2", width=2)
                self.canvas_region_map[region.region_id] = rect_id

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
        self.drag_start = (event.x, event.y)
        if self.canvas_rect_id:
            self.canvas.delete(self.canvas_rect_id)
        self.canvas_rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#ff7043", width=2, dash=(4, 2)
        )

    def _on_canvas_drag(self, event: tk.Event) -> None:
        if not self.drag_start or not self.canvas_rect_id:
            return
        self.canvas.coords(self.canvas_rect_id, self.drag_start[0], self.drag_start[1], event.x, event.y)

    def _on_canvas_release(self, event: tk.Event) -> None:
        if not self.drag_start or not self.canvas_rect_id:
            return
        start_x, start_y = self.drag_start
        end_x, end_y = event.x, event.y
        self.drag_start = None

        x1, x2 = sorted([start_x, end_x])
        y1, y2 = sorted([start_y, end_y])
        if abs(x2 - x1) < 4 or abs(y2 - y1) < 4:
            self.canvas.delete(self.canvas_rect_id)
            self.canvas_rect_id = None
            return

        label = self._prompt_label()
        if not label:
            self.canvas.delete(self.canvas_rect_id)
            self.canvas_rect_id = None
            return

        region = Region(
            region_id=str(uuid4()),
            page=self.page_index + 1,
            label=label,
            x=x1 / self.page_scale,
            y=y1 / self.page_scale,
            width=(x2 - x1) / self.page_scale,
            height=(y2 - y1) / self.page_scale,
        )
        self.regions.append(region)
        self.canvas_rect_id = None
        self._render_page()

    def _on_region_select(self, _: tk.Event) -> None:
        selection = self.region_list.curselection()
        if not selection:
            return
        index = selection[0]
        region = self.regions[index]
        if region.page != self.page_index + 1:
            return
        rect_id = self.canvas_region_map.get(region.region_id)
        if rect_id:
            self.canvas.itemconfig(rect_id, outline="#d32f2f", width=3)

    def _delete_region(self) -> None:
        selection = self.region_list.curselection()
        if not selection:
            return
        index = selection[0]
        self.regions.pop(index)
        self._render_page()

    def _save_template(self) -> None:
        payload = dict(self.template_payload)
        payload["regions"] = [region.to_payload() for region in self.regions]
        payload["source_pdf"] = self.source_pdf
        self.template_service.save_template_version(template_id=self.template_id, payload=payload)
        messagebox.showinfo("Saved", "Template regions saved.")

    def _prompt_label(self) -> str | None:
        dialog = tk.Toplevel(self.window)
        dialog.title("Select Region Label")
        dialog.transient(self.window)
        dialog.grab_set()

        ttk.Label(dialog, text="Label").pack(anchor="w", padx=12, pady=(12, 4))
        label_var = tk.StringVar(value=LABEL_OPTIONS[0] if LABEL_OPTIONS else "")
        label_box = ttk.Combobox(dialog, textvariable=label_var, values=LABEL_OPTIONS, state="readonly")
        label_box.pack(fill=tk.X, padx=12, pady=(0, 12))

        result: list[str | None] = [None]

        def on_ok() -> None:
            result[0] = label_var.get()
            dialog.destroy()

        def on_cancel() -> None:
            dialog.destroy()

        button_frame = ttk.Frame(dialog)
        button_frame.pack(fill=tk.X, padx=12, pady=(0, 12))
        ttk.Button(button_frame, text="Cancel", command=on_cancel).pack(side=tk.RIGHT)
        ttk.Button(button_frame, text="OK", command=on_ok).pack(side=tk.RIGHT, padx=6)

        dialog.wait_window()
        return result[0]

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
