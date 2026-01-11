from __future__ import annotations

from pathlib import Path

from DTOCR.core.logging import configure_logging # type: ignore
from DTOCR.db.sqlite import Database # type: ignore
from DTOCR.db.repositories import TemplateRepository # type: ignore
from DTOCR.services.template_service import TemplateService # type: ignore
from DTOCR.gui.app import App # type: ignore


def main() -> None:
    # 1) Initialize logging
    configure_logging()

    # 2) Initialize DB
    data_dir = Path.cwd() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "DTOCR.sqlite3"

    db = Database(db_path=db_path)
    db.initialize()  # creates tables if missing

    # 3) Create service objects (dependency injection)
    template_repo = TemplateRepository(db=db)
    template_service = TemplateService(template_repo=template_repo)

    # 4) Start GUI
    app = App(template_service=template_service)
    app.run()


if __name__ == "__main__":
    main()
