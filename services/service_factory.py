from __future__ import annotations

from pathlib import Path

from DTOCR.db.sqlite import Database  # type: ignore
from DTOCR.db.repositories import TemplateRepository  # type: ignore
from DTOCR.services.template_service import TemplateService  # type: ignore


def create_template_service() -> TemplateService:
    """
    Construct TemplateService using the same SQLite DB as your GUI.
    """
    db_path = Path(r"path/to/data\data\DTOCR.sqlite3")

    db = Database(db_path=db_path)
    db.initialize()

    template_repo = TemplateRepository(db=db)
    return TemplateService(template_repo=template_repo)
