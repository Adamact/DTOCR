from __future__ import annotations

from fastapi import FastAPI

from DTOCR.web.api.routes.documents import router as documents_router  # type: ignore
from DTOCR.web.api.routes.runs import router as runs_router  # type: ignore

app = FastAPI(title="DTOCR Web API", version="0.1.0")

app.include_router(documents_router)
app.include_router(runs_router)


@app.get("/health")
def health() -> dict:
    return {"ok": True}
