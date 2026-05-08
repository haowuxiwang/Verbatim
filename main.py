from __future__ import annotations

import ctypes
import json
import os
import sys

if __name__ == "__main__":
    # Best-effort UTF-8 console setup on Windows to avoid mojibake in
    # subprocess paths (--local-ocr-worker, --local-ocr-self-check, etc.)
    # that bypass the GUI entry point's own encoding setup.
    if os.name == "nt":
        try:
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if "--local-ocr-worker" in sys.argv[1:]:
        idx = sys.argv.index("--local-ocr-worker")
        from core.services.local_ocr_worker import main as local_ocr_worker_main

        raise SystemExit(local_ocr_worker_main(sys.argv[idx + 1 :]))
    if "--local-ocr-self-check" in sys.argv[1:]:
        from core.services.ocr_engines import (
            resolve_ocr_json_exe_path,
            resolve_ocr_runtime_dir,
            run_local_ocr_self_check,
        )

        runtime_dir = resolve_ocr_runtime_dir()
        json_exe = resolve_ocr_json_exe_path()
        result = run_local_ocr_self_check(
            runtime_dir=runtime_dir,
            offline_strict=True,
            json_exe=json_exe,
            worker_python="",
        )
        print(json.dumps(result.__dict__, ensure_ascii=True))
        raise SystemExit(0 if result.available else 2)
    if "--background-task-worker" in sys.argv[1:]:
        idx = sys.argv.index("--background-task-worker")
        from core.services.background_worker import main as background_worker_main

        raise SystemExit(background_worker_main(sys.argv[idx + 1 :]))
    from app.main_window import main as gui_main

    raise SystemExit(gui_main())
