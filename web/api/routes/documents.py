from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, UploadFile

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("")
async def upload_document(file: UploadFile = File(...)) -> dict:
    project_root = Path(__file__).resolve().parents[4]  # .../DTOCR
    uploads_dir = project_root / "storage" / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    doc_id = f"doc_{uuid4().hex[:12]}"
    safe_name = file.filename.replace("\\", "_").replace("/", "_")
    dst = uploads_dir / f"{doc_id}_{safe_name}"

    content = await file.read()
    dst.write_bytes(content)

    return {"document_id": doc_id, "path": str(dst), "filename": file.filename}
