import os
from pathlib import Path
import shutil
import sys

FROZEN = getattr(sys, "frozen", False)
BUNDLE = Path(sys._MEIPASS) if FROZEN else Path(__file__).resolve().parent
BASE = Path(sys.executable).parent if FROZEN else BUNDLE
APP_HOME = (
    Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "MathMistakeBook"
    if FROZEN else BASE
)
DATA_DIR = APP_HOME / "data"
ENV_FILE = APP_HOME / ".env"


def find_xelatex() -> str:
    configured = os.getenv("XELATEX", "").strip()
    if configured and configured != "xelatex":
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute() and (ENV_FILE.parent / candidate).is_file():
            return str(ENV_FILE.parent / candidate)
        if executable := shutil.which(configured):
            return executable
        raise FileNotFoundError("找不到 .env 中 XELATEX 指定的程序，请检查路径。")
    for root in (BASE, BUNDLE):
        portable = root / "texlive" / "bin" / "windows" / "xelatex.exe"
        if portable.is_file():
            return str(portable)
    if executable := shutil.which("xelatex"):
        return executable
    raise FileNotFoundError("找不到 XeLaTeX，请保留发布目录中的 texlive，或把 XeLaTeX 加入 PATH。")
