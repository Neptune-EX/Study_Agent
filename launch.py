import multiprocessing
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from paths import BUNDLE, ENV_FILE


def main():
    multiprocessing.freeze_support()
    load_dotenv(ENV_FILE)
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        from mistakes.worker import process_queue

        process_queue(Path(sys.argv[2]))
        return

    os.chdir(BUNDLE)
    script = str(BUNDLE / "app.py")
    options = {
        "server.fileWatcherType": "none", "browser.gatherUsageStats": "false",
        "global.developmentMode": "false",
    }
    if len(sys.argv) == 3 and sys.argv[1] == "--server-only":
        from streamlit_desktop_app.core import run_streamlit

        options.update({"server.address": "127.0.0.1", "server.port": sys.argv[2], "server.headless": "true"})
        run_streamlit(script, options)
        return
    from streamlit_desktop_app import start_desktop_app

    start_desktop_app(script, title="数学错题本", width=1280, height=860, options=options)


if __name__ == "__main__":
    main()
