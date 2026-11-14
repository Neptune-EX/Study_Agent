"""Process the SQLite generation queue outside the Streamlit server."""
from contextlib import contextmanager
import logging
import os
from pathlib import Path
import sys
from time import sleep

from dotenv import load_dotenv
from paths import ENV_FILE

from .debug import debug_run
from .latex import compile_source
from .store import Store
from .workflow import finish_batch, finish_followup


@contextmanager
def worker_lock(path: Path):
    with path.open("a+b") as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                yield False
                return
        else:
            import fcntl

            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
        # Closing the file releases the lock, including after a process crash.
        yield True


def drain_queue(store: Store, path: Path) -> bool:
    with worker_lock(path.with_suffix(".worker.lock")) as acquired:
        if not acquired:
            return False
        # Allow a new submission to arrive while the worker is finishing its last job.
        while True:
            jobs = store.active_jobs()
            if not jobs:
                sleep(3)
                jobs = store.active_jobs()
                if not jobs:
                    return True
            for candidate in jobs:
                job = store.claim_job(candidate.id)
                if job is None:
                    continue
                try:
                    with debug_run("generate_background", batch_id=job.batch_id, job_id=job.id) as run_id:
                        store.update_job(job.id, "正在后台生成", debug_id=run_id)
                        batch = store.get_batch(job.batch_id)
                        if job.action in {"append", "tutor"}:
                            finish_followup(store, batch, job, lambda message: store.update_job(job.id, message))
                            continue
                        if job.action == "compile":
                            _, tex, _ = store.batch_result(batch.id)
                            if not tex:
                                raise ValueError("这份记录还没有 TeX 源码。")
                            store.update_job(job.id, "正在编译已保存的 TeX")
                            store.save_pdf(batch, tex, compile_source(tex))
                            warnings = []
                            message = "PDF 已保存"
                        else:
                            warnings = finish_batch(
                                store, batch, job.plan, job=job,
                                progress=lambda message: store.update_job(job.id, message),
                            )
                            message = "TeX 已保存，可按需编译 PDF"
                    if warnings:
                        store.update_job(job.id, "；".join(warnings), state="needs_attention")
                    else:
                        store.update_job(job.id, message, state="done")
                except Exception as exc:
                    logging.exception("后台任务失败")
                    store.update_job(job.id, str(exc), state="needs_attention")


def process_queue(path: Path):
    store = Store(path)
    for _ in range(5):
        if drain_queue(store, path):
            return
        sleep(1)


if __name__ == "__main__":
    load_dotenv(ENV_FILE)
    process_queue(Path(sys.argv[1]))
