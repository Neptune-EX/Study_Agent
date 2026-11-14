from pathlib import Path
import os
import subprocess
import sys
from threading import Lock
from paths import BASE, FROZEN

_workers: dict[Path, subprocess.Popen] = {}
_lock = Lock()


def start_worker(path: Path, *, submitted: bool = False):
    path = path.resolve()
    with _lock:
        worker = _workers.get(path)
        if not submitted and worker is not None and worker.poll() is None:
            return
        options = (
            {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt" else {"start_new_session": True}
        )
        if FROZEN:
            command = [sys.executable, "--worker", str(path)]
            options["env"] = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
        else:
            command = [sys.executable, "-m", "mistakes.worker", str(path)]
        with path.with_suffix(".worker.log").open("a", encoding="utf-8") as log:
            _workers[path] = subprocess.Popen(
                command, cwd=BASE, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, **options,
            )
