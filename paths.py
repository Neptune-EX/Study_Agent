import sys
import os
import shutil
from pathlib import Path

# 统一在这里定义 FROZEN，供其他地方导入使用
FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    # Windows: EXE 同级目录
    if sys.platform == "win32":
        BASE = Path(sys.executable).parent
    # macOS: 统一放到 ~/Library/Application Support/MathMistakes
    elif sys.platform == "darwin":
        BASE = Path.home() / "Library" / "Application Support" / "MathMistakes"
        # 首次在 Mac 上运行时，确保该目录存在
        BASE.mkdir(parents=True, exist_ok=True)
    else:
        BASE = Path(sys.executable).parent
    BUNDLE = Path(sys._MEIPASS)
else:
    BASE = Path(__file__).resolve().parent
    BUNDLE = BASE

ROOT = BASE
DATA_DIR = ROOT / "data"
ENV_FILE = ROOT / ".env"


def find_xelatex() -> str:
    configured = os.getenv("XELATEX", "").strip()
    if configured and configured != "xelatex":
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute() and (ENV_FILE.parent / candidate).is_file():
            return str(ENV_FILE.parent / candidate)
        if executable := shutil.which(configured):
            return executable
        raise FileNotFoundError("找不到 .env 中 XELATEX 指定的程序，请检查路径。")

    # 优先查找随应用分发的便携版 TeX Live
    for root in (BASE, BUNDLE):
        # 检查 Windows 版本
        portable_win = root / "texlive" / "bin" / "windows" / "xelatex.exe"
        if portable_win.is_file():
            return str(portable_win)
        
        # 检查 macOS 版本
        portable_mac = root / "texlive" / "bin" / "universal-darwin" / "xelatex"
        if portable_mac.is_file():
            return str(portable_mac)

    # 如果都没找到，最后尝试系统 PATH（比如用户自己安装了 MacTeX）
    if executable := shutil.which("xelatex"):
        return executable
    
    raise FileNotFoundError("找不到 XeLaTeX，请保留发布目录中的 texlive，或把 XeLaTeX 加入 PATH。")