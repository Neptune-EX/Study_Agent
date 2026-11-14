# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

root = Path(SPECPATH)
template_dir = root / "latex_board"
required_files = [
    root / "app.py",
    root / "launch.py",
    root / "paths.py",
    root / "pyi_runtime.py",
    root / ".streamlit" / "config.toml",
    template_dir / "应用题错题模板.tex",
    template_dir / "图形题错题模板.tex",
]
missing = [str(path.relative_to(root)) for path in required_files if not path.is_file()]
if missing:
    raise SystemExit("Missing project files: " + ", ".join(missing))

datas, binaries, hidden = collect_all("streamlit")
datas += copy_metadata("streamlit", recursive=True)
datas += copy_metadata("langchain-openai", recursive=True)
webview_data, webview_binaries, webview_hidden = collect_all("webview")
datas += webview_data
binaries += webview_binaries
hidden += webview_hidden
datas += [
    (str(root / "app.py"), "."),
    (str(root / ".streamlit" / "config.toml"), ".streamlit"),
    *[(str(template), "latex_board") for template in template_dir.glob("*.tex")],
]
hidden += ["app", "uvicorn.logging"]
for package in ("mistakes", "uvicorn", "tiktoken_ext"):
    hidden += collect_submodules(package)

a = Analysis(
    [str(root / "launch.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    runtime_hooks=[str(root / "pyi_runtime.py")],
    excludes=["IPython", "matplotlib", "scipy", "pandas.tests", "numpy.tests", "pyarrow.tests",
              "PyQt5", "PyQt6", "PySide2", "PySide6", "gi", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="MathMistakeBook", console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="MathMistakeBook", upx=False)
if sys.platform == "darwin":
    app = BUNDLE(coll, name="MathMistakeBook.app", bundle_identifier="org.mathmistakebook.desktop")
