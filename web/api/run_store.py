from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4


@dataclass
class RunRecord:
    run_id: str
    template_id: int
    pdf_path: str
    status: Literal["queued", "running", "done", "failed"] = "queued"
    result_path: str | None = None
    error: str | None = None


RUNS: dict[str, RunRecord] = {}


def new_run_id() -> str:
    return f"run_{uuid4().hex[:12]}"


def result_path_for(run_id: str) -> str:
    # Store results under project-level storage/
    base = Path(__file__).resolve().parents[3] / "storage" / "artifacts" / "runs"
    base.mkdir(parents=True, exist_ok=True)
    return str(base / f"{run_id}_result.json")
