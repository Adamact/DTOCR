from __future__ import annotations

from DTOCR.core.logging import configure_logging  # type: ignore
from DTOCR.gui.app import App  # type: ignore
from DTOCR.services.service_factory import create_template_service  # type: ignore


def main() -> None:
    # 1) Initialize logging
    configure_logging()

    # 2) Create service object using shared DB path resolution
    # (explicit arg > DTOCR_DB_PATH > project-local data/DTOCR.sqlite3)
    template_service = create_template_service()

    # 3) Start GUI
    app = App(template_service=template_service)
    app.run()


if __name__ == "__main__":
    main()
