"""Check the packaged server and PDF worker without calling DeepSeek."""

import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from urllib.error import URLError
from urllib.request import urlopen

import pymupdf

from mistakes.latex import render_batch
from mistakes.models import Diagram, Exercise, Original, PracticePlan
from mistakes.store import Store


def check_server(executable: Path, env: dict[str, str], home: Path):
    with subprocess.Popen([executable, "--server-only", "8524"], env=env) as server:
        try:
            deadline = monotonic() + 30
            while server.poll() is None and monotonic() < deadline:
                try:
                    with urlopen("http://127.0.0.1:8524/_stcore/health", timeout=1) as result:
                        if result.read() == b"ok":
                            return
                except (URLError, TimeoutError):
                    pass
                sleep(0.25)
            log = home / "Library/Application Support/MathMistakes/desktop.log"
            if log.is_file():
                print(log.read_text(encoding="utf-8"), file=sys.stderr)
            raise RuntimeError("The packaged Streamlit service did not start.")
        finally:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()


def check_pdf(executable: Path, env: dict[str, str], directory: Path):
    store = Store(directory / "smoke.sqlite3")
    plan = PracticePlan(count=1)
    for kind in ("blank", "geometry"):
        original = Original(kind=kind, topic="数学", statement="两小时等于 ______ 分钟。", sources=["1-1"])
        exercise = Exercise(statement="请完成练习。", answer="120", solution=r"$2 \times 60 = 120$")
        if kind == "geometry":
            exercise.diagram = Diagram(circles=[{"center": [50, 50], "radius": 15}])
        batch_id = store.save_intake("sample.png", [(original, "", [], [])], "三年级", "", plan)
        batch = store.get_batch(batch_id)
        question, = store.batch_questions(batch_id)
        question.exercises = [exercise]
        tex = render_batch([question], plan, math=True).encode("utf-8")
        store.save_batch(batch, plan, [(question, question.exercises)], tex, [])
        store.queue_pdf(store.get_batch(batch_id))

    subprocess.run([executable, "--worker", str(store.path)], env=env, check=True, timeout=120)
    for batch in store.batches():
        job = store.latest_job(batch.id)
        if job is None or job.state != "done":
            raise RuntimeError(f"PDF worker failed: {job.message if job else 'missing job'}")
        pdf, _, _ = store.batch_result(batch.id)
        with pymupdf.open(stream=pdf, filetype="pdf") as document:
            text = "".join(page.get_text() for page in document)
            if "两小时等于" not in text or "120" not in text:
                raise RuntimeError("The generated PDF is missing Chinese text or the answer.")


if __name__ == "__main__":
    app = Path(sys.argv[1]).resolve()
    executable = app / "Contents/MacOS/MathMistakeBook"
    tex_bin = app / "Contents/Resources/texlive/bin/universal-darwin"
    for name in ("xelatex", "xdvipdfmx"):
        if not os.access(tex_bin / name, os.X_OK):
            raise RuntimeError(f"The app is missing bundled {name}.")
    with TemporaryDirectory(prefix="mathmistakebook-smoke-") as temporary:
        home = Path(temporary)
        env = {
            "HOME": str(home),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            # Match Finder's minimal PATH so PDF compilation exercises bundle discovery.
            "TMPDIR": temporary,
        }
        check_server(executable, env, home)
        check_pdf(executable, env, home)
