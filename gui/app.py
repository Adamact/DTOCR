from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox

from DTOCR.services.template_service import TemplateService


class App:
    def __init__(self, template_service: TemplateService) -> None:
        self.template_service = template_service

        self.root = tk.Tk()
        self.root.title("OCR App (MVP Skeleton)")
        self.root.geometry("700x420")

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

        ttk.Button(container, text="Refresh", command=self._refresh_templates).pack(anchor="e", pady=(10, 0))

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
