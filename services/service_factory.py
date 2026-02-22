from __future__ import annotations

import os
from pathlib import Path

from DTOCR.db.sqlite import Database  # type: ignore
from DTOCR.db.repositories import TemplateRepository  # type: ignore
from DTOCR.services.template_service import TemplateService  # type: ignore


def _default_db_path() -> Path:
    # Project root is .../DTOCR
    project_root = Path(__file__).resolve().parents[1]
    data_dir = project_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "DTOCR.sqlite3"


def create_template_service(db_path: str | Path | None = None) -> TemplateService:
    """
    Construct TemplateService using the shared SQLite DB.

    Resolution order:
    1) explicit db_path argument
    2) DTOCR_DB_PATH environment variable
    3) project-local data/DTOCR.sqlite3
    """
    resolved = db_path
    if resolved is None:
        env_db_path = os.getenv("DTOCR_DB_PATH", "").strip()
        if env_db_path:
            resolved = env_db_path
    db_path_obj = Path(resolved) if resolved is not None else _default_db_path()

    db = Database(db_path=db_path_obj)
    db.initialize()

    template_repo = TemplateRepository(db=db)
    return TemplateService(template_repo=template_repo)
