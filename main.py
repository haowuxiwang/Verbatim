from __future__ import annotations

import sys

if __name__ == "__main__":
    if "--local-ocr-worker" in sys.argv[1:]:
        idx = sys.argv.index("--local-ocr-worker")
        from core.services.local_ocr_worker import main as local_ocr_worker_main

        raise SystemExit(local_ocr_worker_main(sys.argv[idx + 1 :]))
    if "--background-task-worker" in sys.argv[1:]:
        idx = sys.argv.index("--background-task-worker")
        from core.services.background_worker import main as background_worker_main

        raise SystemExit(background_worker_main(sys.argv[idx + 1 :]))
    from app.main_window import main as gui_main

    raise SystemExit(gui_main())
