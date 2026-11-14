"""Give GUI builds writable streams before Streamlit or multiprocessing starts."""
import os
import sys

from paths import APP_HOME

APP_HOME.mkdir(parents=True, exist_ok=True)
log = (APP_HOME / "desktop.log").open("a", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = log
sys.stdin = open(os.devnull, encoding="utf-8")
