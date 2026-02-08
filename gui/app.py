from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from DTOCR.services.template_service import TemplateService
from DTOCR.gui.template_editor import TemplateEditor

# PIL is optional (for annotated image viewer). If missing, the viewer falls back to telling user where images were saved.
try:
    from PIL import Image, ImageTk
except Exception:
    Image = None
    ImageTk = None

# pypdfium2 is optional and used to render PDF pages for preview
from DTOCR.core.pdf_renderer import PdfDocument

import glob
import os
import tempfile
import shutil
import logging
from pathlib import Path


class App:
    def __init__(self, template_service: TemplateService) -> None:
        self.template_service = template_service

        self.root = tk.Tk()
        self.root.title("OCR App (MVP Skeleton)")
        self.root.geometry("700x600")

        self._build_ui()
        self._refresh_templates()

    def run(self) -> None:
        self.root.mainloop()

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        # Create template section
        create_frame = ttk.LabelFrame(container, text="Create Template", padding=12)
        create_frame.pack(fill=tk.X)

        self.name_var = tk.StringVar(value="Example Template")
        self.vendor_var = tk.StringVar(value="VendorX")
        self.doc_type_var = tk.StringVar(value="invoice")

        ttk.Label(create_frame, text="Name").grid(row=0, column=0, sticky="w")
        ttk.Entry(create_frame, textvariable=self.name_var, width=30).grid(row=0, column=1, padx=8, pady=4)

        ttk.Label(create_frame, text="Vendor").grid(row=0, column=2, sticky="w")
        ttk.Entry(create_frame, textvariable=self.vendor_var, width=20).grid(row=0, column=3, padx=8, pady=4)

        ttk.Label(create_frame, text="Document Type").grid(row=1, column=0, sticky="w")
        ttk.Entry(create_frame, textvariable=self.doc_type_var, width=30).grid(row=1, column=1, padx=8, pady=4)

        ttk.Button(create_frame, text="Create", command=self._on_create_template).grid(
            row=1, column=3, sticky="e", padx=8, pady=4
        )

        # Templates list section
        list_frame = ttk.LabelFrame(container, text="Templates", padding=12)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        self.tree = ttk.Treeview(list_frame, columns=("id", "name", "vendor", "doc_type", "created_at"), show="headings")
        for col, title, w in [
            ("id", "ID", 60),
            ("name", "Name", 200),
            ("vendor", "Vendor", 140),
            ("doc_type", "Type", 120),
            ("created_at", "Created", 160),
        ]:
            self.tree.heading(col, text=title)
            self.tree.column(col, width=w, anchor="w")

        self.tree.pack(fill=tk.BOTH, expand=True)

        # Small settings area for OCR controls
        settings = ttk.Frame(container)
        settings.pack(fill=tk.X, pady=(8, 6))
        ttk.Label(settings, text="OCR batch size").pack(side=tk.LEFT, padx=(0, 6))
        self.ocr_batch_size_var = tk.IntVar(value=8)
        self.ocr_batch_size_spin = ttk.Spinbox(settings, from_=1, to=32, textvariable=self.ocr_batch_size_var, width=5)
        self.ocr_batch_size_spin.pack(side=tk.LEFT)
        ttk.Label(settings, text="OCR beams").pack(side=tk.LEFT, padx=(16, 6))
        self.ocr_num_beams_var = tk.IntVar(value=1)
        self.ocr_num_beams_spin = ttk.Spinbox(settings, from_=1, to=8, textvariable=self.ocr_num_beams_var, width=5)
        self.ocr_num_beams_spin.pack(side=tk.LEFT)

        # Live preview controls
        self.preview_image_path: str | None = None
        self.preview_img_photo = None
        self.live_preview_var = tk.BooleanVar(value=False)
        # PDF preview state: whether the selected preview is a PDF and which page to render
        self.preview_is_pdf = False
        self.preview_pdf_page_var = tk.IntVar(value=1)
        self.preview_page_spin: ttk.Spinbox | None = None

        # Preview selection and controls
        preview_sel = ttk.Frame(container)
        preview_sel.pack(fill=tk.X, pady=(6, 6))
        ttk.Button(preview_sel, text="Select Preview Image", command=self._on_select_preview_image).pack(side=tk.LEFT)
        self.preview_path_lbl = ttk.Label(preview_sel, text="No preview image selected", width=60)
        self.preview_path_lbl.pack(side=tk.LEFT, padx=8)
        # Page selector for PDFs (disabled for image files until a PDF is selected)
        ttk.Label(preview_sel, text="Page:").pack(side=tk.LEFT, padx=(6,0))
        self.preview_page_spin = ttk.Spinbox(preview_sel, from_=1, to=1, textvariable=self.preview_pdf_page_var, width=4, state="disabled")
        self.preview_page_spin.pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(preview_sel, text="Live Preview", variable=self.live_preview_var).pack(side=tk.LEFT, padx=6)
        ttk.Button(preview_sel, text="Preview", command=self._on_preview).pack(side=tk.LEFT, padx=6)
        # Trace changes: auto preview when live preview toggled or page changes
        try:
            self.live_preview_var.trace_add("write", lambda *a: self._on_preview() if self.live_preview_var.get() and self.preview_image_path else None)
            # When the selected PDF page changes, update preview if live preview is enabled
            self.preview_pdf_page_var.trace_add("write", lambda *a: self._on_preview() if self.live_preview_var.get() and self.preview_is_pdf else None)
        except Exception:
            # Older tkinter versions may not support trace_add; ignore
            pass

        actions = ttk.Frame(container)
        actions.pack(anchor="e", pady=(10, 0))
        ttk.Button(actions, text="Refresh", command=self._refresh_templates).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Edit Template Regions", command=self._on_edit_template).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Manage Labels", command=self._on_manage_labels).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Apply Template to PDF", command=self._on_apply_template).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Run OCR Pipeline", command=self._on_run_ocr).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Delete Template", command=self._on_delete_template).pack(side=tk.LEFT, padx=4)

        # Preview pane with scrollable canvas and zoom controls
        preview_frame = ttk.LabelFrame(container, text="Preview", padding=6)
        preview_frame.pack(fill=tk.BOTH, expand=False, pady=(6, 12))

        # Zoom controls
        zoom_row = ttk.Frame(preview_frame)
        zoom_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(zoom_row, text="-", width=3, command=lambda: self._change_zoom(-10)).pack(side=tk.LEFT)
        self.zoom_var = tk.IntVar(value=100)
        self.zoom_label = ttk.Label(zoom_row, textvariable=tk.StringVar(value=f"{self.zoom_var.get()}%"))
        self.zoom_label.pack(side=tk.LEFT, padx=(6,4))
        self.zoom_scale = ttk.Scale(zoom_row, from_=10, to=400, orient=tk.HORIZONTAL, command=lambda v: self._set_zoom(int(float(v))), value=100)
        self.zoom_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0,6))
        ttk.Button(zoom_row, text="+", width=3, command=lambda: self._change_zoom(10)).pack(side=tk.LEFT)
        ttk.Button(zoom_row, text="Reset", command=lambda: self._set_zoom(100)).pack(side=tk.LEFT, padx=(6,0))

        # Canvas with scrollbars
        canvas_row = ttk.Frame(preview_frame)
        canvas_row.pack(fill=tk.BOTH, expand=True)
        self.preview_v_scroll = ttk.Scrollbar(canvas_row, orient=tk.VERTICAL)
        self.preview_h_scroll = ttk.Scrollbar(canvas_row, orient=tk.HORIZONTAL)
        self.preview_canvas = tk.Canvas(canvas_row, bg="white", xscrollcommand=self.preview_h_scroll.set, yscrollcommand=self.preview_v_scroll.set)
        self.preview_v_scroll.config(command=self.preview_canvas.yview)
        self.preview_h_scroll.config(command=self.preview_canvas.xview)
        self.preview_v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.preview_h_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        self.preview_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Internal preview state
        self.preview_pil_image = None
        self.preview_img_photo = None
        self.preview_img_id = None
        # Bind mousewheel for scrolling
        self.preview_canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        # Update zoom label when zoom_var or scale change
        def _update_zoom_label(val=None):
            z = int(self.zoom_var.get())
            try:
                self.zoom_label.config(text=f"{z}%")
                self.zoom_scale.set(z)
            except Exception:
                pass
        self.zoom_var.trace_add("write", lambda *a: _update_zoom_label())


    def _on_create_template(self) -> None:
        name = self.name_var.get().strip()
        vendor = self.vendor_var.get().strip()
        doc_type = self.doc_type_var.get().strip()

        if not name or not vendor or not doc_type:
            messagebox.showerror("Validation", "Name, Vendor, and Document Type are required.")
            return

        template_id = self.template_service.create_template(name=name, vendor=vendor, document_type=doc_type)

        # For now, save an initial empty version payload.
        payload = {
            "template_id": template_id,
            "name": name,
            "vendor": vendor,
            "document_type": doc_type,
            "regions": [],
            "dpi": 300,
            "text_mode": "auto",
        }
        self.template_service.save_template_version(template_id=template_id, payload=payload)

        messagebox.showinfo("Created", f"Template created with ID: {template_id}")
        self._refresh_templates()

    def _refresh_templates(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)

        templates = self.template_service.list_templates()
        for t in templates:
            self.tree.insert(
                "",
                "end",
                values=(t["id"], t["name"], t["vendor"], t["document_type"], t["created_at"]),
            )

    def _on_edit_template(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Select Template", "Please select a template to edit.")
            return
        item = self.tree.item(selection[0])
        template_id = int(item["values"][0])
        payload = self.template_service.load_latest_template_payload(template_id)
        if not payload:
            messagebox.showerror("Missing Template", "No template payload found for this template.")
            return
        label_options = self.template_service.list_label_options()
        TemplateEditor(self.root, self.template_service, template_id, payload, label_options)

    def _on_delete_template(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Select Template", "Please select a template to delete.")
            return
        item = self.tree.item(selection[0])
        template_id = int(item["values"][0])
        template_name = item["values"][1]
        if not messagebox.askyesno(
            "Delete Template",
            f"Delete template '{template_name}' and all saved versions?",
        ):
            return
        self.template_service.delete_template(template_id)
        self._refresh_templates()

    def _on_manage_labels(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("Manage Labels")
        window.geometry("360x420")
        window.transient(self.root)
        window.grab_set()

        container = ttk.Frame(window, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        ttk.Label(container, text="Labels").pack(anchor="w")
        labels_list = tk.Listbox(container, height=14)
        labels_list.pack(fill=tk.BOTH, expand=True, pady=(6, 8))

        entry_var = tk.StringVar()
        ttk.Label(container, text="Label Name").pack(anchor="w")
        entry = ttk.Entry(container, textvariable=entry_var)
        entry.pack(fill=tk.X, pady=(0, 8))

        def refresh() -> None:
            labels_list.delete(0, tk.END)
            for label in self.template_service.list_label_options():
                labels_list.insert(tk.END, label)

        def add_label() -> None:
            label = entry_var.get().strip()
            if not label:
                messagebox.showwarning("Label", "Label name cannot be empty.")
                return
            if not self.template_service.add_label_option(label):
                messagebox.showwarning("Label", "Label already exists.")
                return
            entry_var.set("")
            refresh()

        def delete_label() -> None:
            selection = labels_list.curselection()
            if not selection:
                messagebox.showwarning("Label", "Select a label to delete.")
                return
            label = labels_list.get(selection[0])
            if not messagebox.askyesno("Delete Label", f"Delete label '{label}'?"):
                return
            self.template_service.delete_label_option(label)
            refresh()

        def rename_label() -> None:
            selection = labels_list.curselection()
            if not selection:
                messagebox.showwarning("Label", "Select a label to rename.")
                return
            old_label = labels_list.get(selection[0])
            new_label = entry_var.get().strip()
            if not new_label:
                messagebox.showwarning("Label", "New label name cannot be empty.")
                return
            if not self.template_service.rename_label_option(old_label, new_label):
                messagebox.showwarning("Label", "Rename failed. Label may already exist.")
                return
            entry_var.set("")
            refresh()

        button_row = ttk.Frame(container)
        button_row.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(button_row, text="Add", command=add_label).pack(side=tk.LEFT)
        ttk.Button(button_row, text="Rename", command=rename_label).pack(side=tk.LEFT, padx=6)
        ttk.Button(button_row, text="Delete", command=delete_label).pack(side=tk.LEFT)
        ttk.Button(button_row, text="Refresh", command=refresh).pack(side=tk.RIGHT)

        refresh()

    def _on_apply_template(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Select Template", "Please select a template to apply.")
            return
        item = self.tree.item(selection[0])
        template_id = int(item["values"][0])
        payload = self.template_service.load_latest_template_payload(template_id)
        if not payload:
            messagebox.showerror("Missing Template", "No template payload found for this template.")
            return
        pdf_path = filedialog.askopenfilename(
            title="Select PDF",
            filetypes=[("PDF Files", "*.pdf")],
        )
        if not pdf_path:
            return
        divisor = None
        result = self.template_service.apply_template_to_pdf(payload, pdf_path, word_kernel_divisor=divisor)
        crops = result.get("crops", [])
        output_dir = result.get("output_dir", "")
        if not crops:
            messagebox.showinfo("No Regions", "No regions were found to crop for this template.")
            return
        messagebox.showinfo(
            "Template Applied",
            f"Saved {len(crops)} crops to:\n{output_dir}\n(Annotated images if debug enabled are in {output_dir}/annotated)",
        )
        # If annotated images exist, show them for quick inspection
        try:
            self._show_annotated_images(output_dir, divisor)
        except Exception:
            # Ignore viewer errors; the files are still saved on disk
            pass
        # If a preview image is selected and live preview is enabled, refresh it
        try:
            if self.live_preview_var.get() and self.preview_image_path:
                self._on_preview()
        except Exception:
            pass
    def _on_run_ocr(self) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Select Template", "Please select a template to apply.")
            return
        item = self.tree.item(selection[0])
        template_id = int(item["values"][0])
        payload = self.template_service.load_latest_template_payload(template_id)
        if not payload:
            messagebox.showerror("Missing Template", "No template payload found for this template.")
            return
        pdf_path = filedialog.askopenfilename(
            title="Select PDF",
            filetypes=[("PDF Files", "*.pdf")],
        )
        if not pdf_path:
            return
        # Ask user where to save Excel (optional). Cancel to skip Excel export.
        excel_save = filedialog.asksaveasfilename(
            title="Save OCR Results as Excel (optional) - Cancel to skip",
            defaultextension=".xlsx",
            filetypes=[("Excel Files", "*.xlsx")],
        )
        excel_path = excel_save or None
        divisor = None
        batch_size = int(self.ocr_batch_size_var.get() or 8)
        num_beams = int(self.ocr_num_beams_var.get() or 1)
        try:
            result = self.template_service.run_ocr_pipeline(payload, pdf_path, excel_path=excel_path, word_kernel_divisor=divisor, batch_size=batch_size, num_beams=num_beams)
        except RuntimeError as exc:
            messagebox.showerror("OCR Unavailable", str(exc))
            return
        # If a preview image is selected and live preview is enabled, refresh it
        try:
            if self.live_preview_var.get() and self.preview_image_path:
                self._on_preview()
        except Exception:
            pass
        results = result.get("results", [])
        output_dir = result.get("output_dir", "")
        if not results:
            messagebox.showinfo("No OCR Results", "No regions were found to parse.")
            return
        # Build informative completion message
        msg = f"Parsed {len(results)} regions.\nOutput:\n{output_dir}"
        ocr_out = result.get("ocr_output_path", "")
        if ocr_out:
            msg += f"\nOCR JSON:\n{ocr_out}"
        excel_out = result.get("excel_output_path", "")
        if excel_out:
            msg += f"\nExcel:\n{excel_out}"
        messagebox.showinfo(
            "OCR Complete",
            msg,
        )
        # Show annotated images when present (helpful to visualize the chosen kernel)
        try:
            self._show_annotated_images(output_dir, divisor)
        except Exception:
            pass

    def _show_annotated_images(self, output_dir: str, divisor: int | None = None) -> None:
        """Open a simple viewer to show annotated images (if any) saved to output_dir/annotated.

        Falls back to a messagebox if Pillow is not available.
        """
        annotated_dir = os.path.join(output_dir, "annotated")
        if not os.path.isdir(annotated_dir):
            return
        images = sorted(glob.glob(os.path.join(annotated_dir, "*.png")))
        if not images:
            return
        if Image is None or ImageTk is None:
            messagebox.showinfo("Annotated Images Saved", f"Annotated images saved to {annotated_dir} (install Pillow to preview)")
            return

        viewer = tk.Toplevel(self.root)
        title = f"Annotated Images (word kernel divisor={divisor})" if divisor is not None else "Annotated Images"
        viewer.title(title)
        viewer.geometry("800x480")

        canvas = tk.Canvas(viewer, bg="white")
        h_scroll = ttk.Scrollbar(viewer, orient="horizontal", command=canvas.xview)
        canvas.configure(xscrollcommand=h_scroll.set)

        frame = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=frame, anchor="nw")

        canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        h_scroll.pack(side=tk.BOTTOM, fill=tk.X)

        # Load thumbnails
        for img_path in images:
            try:
                img = Image.open(img_path)
                img.thumbnail((700, 700))
                photo = ImageTk.PhotoImage(img)
                lbl = ttk.Label(frame, image=photo, text=os.path.basename(img_path), compound="top")
                lbl.image = photo
                lbl.pack(side=tk.LEFT, padx=6, pady=6)
            except Exception as exc:
                print("Failed to load annotated image", img_path, exc)

        frame.update_idletasks()
        canvas.config(scrollregion=canvas.bbox("all"))

    def _on_select_preview_image(self) -> None:
        try:
            path = filedialog.askopenfilename(
                title="Select Preview Image",
                filetypes=[("Image/PDF Files", "*.png;*.jpg;*.jpeg;*.tif;*.bmp;*.pdf")],
            )
            if not path:
                return
            self.preview_image_path = path
            p = Path(path)
            # PDF selected: enable page selector and read page count
            if p.suffix.lower() == ".pdf":
                try:
                    doc = PdfDocument(path)
                    page_count = doc.page_count
                    doc.close()
                except Exception as exc:
                    logging.getLogger(__name__).exception("Failed to open PDF for preview: %s", exc)
                    messagebox.showerror("PDF Error", f"Failed to open PDF: {exc}")
                    return
                self.preview_is_pdf = True
                self.preview_pdf_page_var.set(1)
                try:
                    # configure spinbox range and enable it
                    self.preview_page_spin.config(from_=1, to=page_count, state="normal")
                except Exception:
                    pass
                self.preview_path_lbl.config(text=f"{os.path.basename(path)} (pages={page_count})")
                if self.live_preview_var.get():
                    try:
                        self._on_preview()
                    except Exception as exc:
                        logging.getLogger(__name__).exception("Live preview failed: %s", exc)
            else:
                # Regular image
                self.preview_is_pdf = False
                self.preview_pdf_page_var.set(1)
                try:
                    self.preview_page_spin.config(state="disabled")
                except Exception:
                    pass
                self.preview_path_lbl.config(text=os.path.basename(path))
                if self.live_preview_var.get():
                    try:
                        self._on_preview()
                    except Exception as exc:
                        logging.getLogger(__name__).exception("Live preview failed: %s", exc)
        except Exception as exc:
            logging.getLogger(__name__).exception("Preview image selection failed: %s", exc)
            messagebox.showerror("Preview Error", f"Failed to select preview image: {exc}")


    def _on_preview(self) -> None:
        try:
            if not self.preview_image_path:
                messagebox.showwarning("No Preview Image", "Select a preview image first.")
                return
            if Image is None or ImageTk is None:
                messagebox.showinfo("Pillow missing", "Install Pillow to show previews.")
                return
            
            # Get the selected template to apply its grid structure
            selection = self.tree.selection()
            if not selection:
                messagebox.showwarning("No Template Selected", "Select a template to preview first.")
                return
            item = self.tree.item(selection[0])
            template_id = int(item["values"][0])
            payload = self.template_service.load_latest_template_payload(template_id)
            if not payload:
                messagebox.showerror("Missing Template", "No template payload found.")
                return
            
            # Use a fixed divisor for preview
            divisor = 15
            # Create a temporary output directory for preview artifacts
            outdir = Path(tempfile.mkdtemp(prefix="dtocr_preview_"))
            serv_logger = logging.getLogger("DTOCR.services.template_service")
            prev_level = serv_logger.getEffectiveLevel()
            serv_logger.setLevel(logging.DEBUG)

            # If previewing a PDF, render the selected page into a temporary image first
            temp_rendered_img = None
            if self.preview_is_pdf:
                page_num = int(self.preview_pdf_page_var.get() or 1)
                try:
                    doc = PdfDocument(self.preview_image_path)
                    if page_num < 1 or page_num > doc.page_count:
                        messagebox.showerror("Invalid Page", f"Page {page_num} not found in PDF")
                        return
                    page = doc.load_page(page_num - 1)
                    pix = page.render_pixmap(scale=2.0)  # 2x zoom for better quality
                    temp_rendered_img = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                    pix.save(temp_rendered_img.name)
                    temp_rendered_img.close()
                    source_path = temp_rendered_img.name
                    doc.close()
                except Exception as exc:
                    messagebox.showerror("PDF Rendering Error", f"Failed to render PDF page: {exc}")
                    return
            else:
                source_path = self.preview_image_path
            
            try:
                # Apply the template to see the grid structure overlaid
                result = self.template_service.apply_template_to_pdf(payload, source_path if not self.preview_is_pdf else source_path, word_kernel_divisor=divisor, save_crops=True)
                
                # Look for an annotated image showing the grid applied
                annotated_dir = Path(outdir) / "annotated"
                if annotated_dir.exists():
                    imgs = sorted(annotated_dir.glob("*.png"))
                    if imgs:
                        img_path = imgs[0]
                    else:
                        img_path = None
                else:
                    img_path = None
                
                if not img_path:
                    # Fallback: show original image with template regions
                    img_path = Path(source_path)
            finally:
                serv_logger.setLevel(prev_level)
                if temp_rendered_img and hasattr(temp_rendered_img, 'name'):
                    try:
                        os.unlink(temp_rendered_img.name)
                    except Exception:
                        pass

            if not img_path or not img_path.exists():
                messagebox.showinfo("No Preview", "Could not generate preview image.")
                return

            try:
                self.preview_pil_image = Image.open(str(img_path))
                self._render_preview_image(scale_percent=int(self.zoom_var.get()))
            except Exception as exc:
                messagebox.showerror("Preview Error", f"Failed to load preview image: {exc}")
            finally:
                try:
                    shutil.rmtree(outdir, ignore_errors=True)
                except Exception:
                    pass
        except Exception as exc:
            logging.getLogger(__name__).exception("Preview failed: %s", exc)

    def _change_zoom(self, delta_percent: int) -> None:
        # delta_percent is added to current zoom percent
        try:
            new_z = max(10, min(400, int(self.zoom_var.get()) + int(delta_percent)))
            self._set_zoom(new_z)
        except Exception:
            pass

    def _set_zoom(self, percent: int) -> None:
        # Update zoom variable and re-render current preview image if present
        percent = max(10, min(400, int(percent)))
        try:
            self.zoom_var.set(percent)
        except Exception:
            pass
        if self.preview_pil_image is not None:
            self._render_preview_image(scale_percent=percent)

    def _render_preview_image(self, scale_percent: int = 100) -> None:
        """Resize preview image according to scale_percent and display it on canvas with scrollbars."""
        if self.preview_pil_image is None:
            return
        try:
            orig = self.preview_pil_image
            w, h = orig.size
            new_w = max(1, int(w * (scale_percent / 100.0)))
            new_h = max(1, int(h * (scale_percent / 100.0)))
            resized = orig.resize((new_w, new_h), Image.LANCZOS)
            photo = ImageTk.PhotoImage(resized)
            self.preview_img_photo = photo
            # Remove previous image if any
            if self.preview_img_id is not None:
                try:
                    self.preview_canvas.delete(self.preview_img_id)
                except Exception:
                    pass
            self.preview_img_id = self.preview_canvas.create_image(0, 0, anchor="nw", image=photo)
            # configure canvas scrollregion
            self.preview_canvas.config(scrollregion=(0, 0, new_w, new_h))
            # optionally center view
            try:
                self.preview_canvas.xview_moveto(0)
                self.preview_canvas.yview_moveto(0)
            except Exception:
                pass
        except Exception as exc:
            print("Failed to render preview image", exc)

    def _on_mousewheel(self, event) -> None:
        # Scroll canvas vertically with mousewheel; if Shift held, scroll horizontally
        try:
            if event.state & 0x0001:  # Shift (may vary by platform)
                # horizontal scroll
                delta = -1 * (event.delta / 120)
                self.preview_canvas.xview_scroll(int(delta), "units")
            else:
                delta = -1 * (event.delta / 120)
                self.preview_canvas.yview_scroll(int(delta), "units")
        except Exception:
            pass

