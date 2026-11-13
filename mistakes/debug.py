from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
from uuid import uuid4

DEBUG_DIR = Path(__file__).resolve().parents[1] / "data" / "debug"
_run_id: ContextVar[str | None] = ContextVar("mistake_debug_run", default=None)
_fields: ContextVar[dict | None] = ContextVar("mistake_debug_fields", default=None)


@contextmanager
def debug_run(action: str, **fields) -> Iterator[str | None]:
    if os.getenv("MISTAKE_DEBUG", "").strip().lower() not in {"1", "true", "yes", "on"}:
        yield None
        return
    run_id = uuid4().hex[:12]
    token = _run_id.set(run_id)
    try:
        record("run.start", action=action, **fields)
        yield run_id
    except Exception as exc:
        record("run.error", error_type=type(exc).__name__)
        raise
    finally:
        record("run.end", action=action)
        _run_id.reset(token)


@contextmanager
def debug_context(**fields):
    token = _fields.set({**(_fields.get() or {}), **fields})
    try:
        yield
    finally:
        _fields.reset(token)


def debug_enabled() -> bool:
    return _run_id.get() is not None


def record(event: str, **fields):
    if (run_id := _run_id.get()) is None:
        return
    entry = {
        "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "event": event,
        **(_fields.get() or {}),
        **fields,
    }
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        with (DEBUG_DIR / f"{run_id}.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:
        logging.getLogger(__name__).warning("无法写入错题调试日志：%s", exc)


def trace_input(content: str | list[dict]) -> str | list[dict]:
    if isinstance(content, str):
        return content
    blocks = []
    for part in content:
        if part["type"] == "text":
            blocks.append({"type": "text", "text": part["text"]})
        elif part["type"] == "image_url":
            url = part["image_url"]["url"]
            _, _, encoded = url.partition(",")
            blocks.append({
                "type": "image_url",
                "base64_chars": len(encoded),
                "base64_sha256": hashlib.sha256(encoded.encode("ascii")).hexdigest(),
            })
    return blocks
