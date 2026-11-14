import os
import shutil
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
BUNDLE = Path(sys._MEIPASS) if FROZEN else Path(__file__).resolve().parent
BASE = Path(sys.executable).parent if FROZEN else BUNDLE

if FROZEN and sys.platform == "win32":
    APP_HOME = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "MathMistakeBook"
elif FROZEN and sys.platform == "darwin":
    APP_HOME = Path.home() / "Library" / "Application Support" / "MathMistakes"
else:
    APP_HOME = BASE

DATA_DIR = APP_HOME / "data"
ENV_FILE = APP_HOME / ".env"


def find_xelatex() -> str:
    configured = os.getenv("XELATEX", "").strip()
    if configured and configured != "xelatex":
        candidate = Path(configured).expanduser()
        if executable := shutil.which(str(ENV_FILE.parent / candidate)) or shutil.which(str(candidate)):
            return executable
        raise FileNotFoundError("找不到 .env 中 XELATEX 指定的程序，请检查路径。")

    if sys.platform == "win32":
        candidates = [root / "texlive/bin/windows/xelatex.exe" for root in (BASE, BUNDLE)]
    elif sys.platform == "darwin":
        resources = BASE.parent / "Resources" if FROZEN else BUNDLE
        candidates = [
            resources / "texlive/bin/universal-darwin/xelatex",
            Path("/Library/TeX/texbin/xelatex"),
        ]
    else:
        candidates = []
    for candidate in candidates:
        if executable := shutil.which(str(candidate)):
            return executable
    if executable := shutil.which("xelatex"):
        return executable
    raise FileNotFoundError("找不到 XeLaTeX，请保留发布目录中的 texlive，或把 XeLaTeX 加入 PATH。")
