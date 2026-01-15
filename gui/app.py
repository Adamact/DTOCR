from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from DTOCR.services.template_service import TemplateService
from DTOCR.gui.template_editor import TemplateEditor


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

        actions = ttk.Frame(container)
        actions.pack(anchor="e", pady=(10, 0))
        ttk.Button(actions, text="Refresh", command=self._refresh_templates).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Edit Template Regions", command=self._on_edit_template).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Manage Labels", command=self._on_manage_labels).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Apply Template to PDF", command=self._on_apply_template).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Run OCR Pipeline", command=self._on_run_ocr).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Delete Template", command=self._on_delete_template).pack(side=tk.LEFT, padx=4)

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
        result = self.template_service.apply_template_to_pdf(payload, pdf_path)
        crops = result.get("crops", [])
        output_dir = result.get("output_dir", "")
        if not crops:
            messagebox.showinfo("No Regions", "No regions were found to crop for this template.")
            return
        messagebox.showinfo(
            "Template Applied",
            f"Saved {len(crops)} crops to:\n{output_dir}",
        )

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
        try:
            result = self.template_service.run_ocr_pipeline(payload, pdf_path)
        except RuntimeError as exc:
            messagebox.showerror("OCR Unavailable", str(exc))
            return
        results = result.get("results", [])
        output_dir = result.get("output_dir", "")
        if not results:
            messagebox.showinfo("No OCR Results", "No regions were found to parse.")
            return
        messagebox.showinfo(
            "OCR Complete",
            f"Parsed {len(results)} regions.\nOutput:\n{output_dir}",
        )
