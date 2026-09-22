from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from DTOCR.services.service_factory import create_template_service  # type: ignore
from DTOCR.web.api.run_store import RUNS, RunRecord, new_run_id, result_path_for  # type: ignore
from DTOCR.web.worker.pipeline_adapter import run_pipeline  # type: ignore

router = APIRouter(prefix="/runs", tags=["runs"])


class StartRunRequest(BaseModel):
    template_id: int
    pdf_path: str


@router.post("")
def start_run(req: StartRunRequest) -> dict:
    service = create_template_service()

    template_payload = service.load_latest_template_payload(template_id=req.template_id)
    if not template_payload:
        raise HTTPException(status_code=404, detail="Template not found or has no saved version payload")

    run_id = new_run_id()
    record = RunRecord(run_id=run_id, template_id=req.template_id, pdf_path=req.pdf_path, status="running")
    RUNS[run_id] = record

    out_path = result_path_for(run_id)

    try:
        result = run_pipeline(
            template_payload=template_payload,
            pdf_path=req.pdf_path,
            excel_path=None,
            save_crops=True,
            batch_size=8,
            num_beams=1,
        )
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        record.status = "done"
        record.result_path = out_path

    except Exception as e:
        record.status = "failed"
        record.error = str(e)
        raise

    return {"run_id": run_id, "status": record.status, "result_path": record.result_path}


@router.get("/{run_id}")
def get_run(run_id: str) -> dict:
    if run_id not in RUNS:
        raise HTTPException(status_code=404, detail="Run not found")
    r = RUNS[run_id]
    return {
        "run_id": r.run_id,
        "template_id": r.template_id,
        "pdf_path": r.pdf_path,
        "status": r.status,
        "result_path": r.result_path,
        "error": r.error,
    }


@router.get("/{run_id}/results")
def get_results(run_id: str) -> dict:
    if run_id not in RUNS:
        raise HTTPException(status_code=404, detail="Run not found")
    r = RUNS[run_id]
    if not r.result_path:
        raise HTTPException(status_code=409, detail="Results not ready")
    with open(r.result_path, encoding="utf-8") as f:
        return json.load(f)
    
def _load_result_from_disk(run_id: str) -> dict:
    disk_path = result_path_for(run_id)
    if not Path(disk_path).exists():
        raise HTTPException(status_code=404, detail="Run not found")
    with open(disk_path, encoding="utf-8") as f:
        return json.load(f)

@router.get("/{run_id}/summary")
def get_summary(run_id: str) -> dict:
    data = _load_result_from_disk(run_id)
    crops = data.get("crops", []) or []
    results = data.get("results", []) or []

    empty_count = 0
    for r in results:
        txt = str(r.get("text", "")).strip()
        if not txt:
            empty_count += 1

    return {
        "run_id": run_id,
        "output_dir": data.get("output_dir", ""),
        "ocr_output_path": data.get("ocr_output_path", ""),
        "excel_output_path": data.get("excel_output_path", ""),
        "counts": {
            "crops": len(crops),
            "results": len(results),
            "empty_results": empty_count,
        },
    }

@router.get("/{run_id}/results_slice")
def get_results_slice(
    run_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
) -> dict:
    data = _load_result_from_disk(run_id)
    results = data.get("results", []) or []
    total = len(results)

    chunk = results[offset : offset + limit]
    return {
        "run_id": run_id,
        "offset": offset,
        "limit": limit,
        "total": total,
        "results": chunk,
    }
