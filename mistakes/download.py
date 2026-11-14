import base64
from html import escape
import json
import os
import sys            # 新增
import subprocess     # 新增
from pathlib import Path

import streamlit as st

from paths import FROZEN


def save_export(data: bytes, target: Path):
    output = target.open("xb")
    try:
        with output:
            output.write(data)
    except OSError:
        target.unlink(missing_ok=True)
        raise


@st.dialog("选择保存位置")
def export_dialog(data: bytes, filename: str, key: str):
    initial = st.session_state.get("last-export-dir", Path.home() / "Downloads")
    if not initial.is_dir():
        initial = Path.home()
    directory_key = f"{key}-directory"
    directory_text = st.text_input("保存文件夹", value=str(initial), key=directory_key,
                                   help=r"可以粘贴资源管理器地址栏中的完整路径，例如 D:\学习资料。")
    directory = Path(directory_text.strip().strip('"')).expanduser()
    if directory.is_absolute() and directory.is_dir():
        try:
            folders = sorted((child for child in directory.iterdir() if child.is_dir()), key=lambda p: p.name.casefold())
        except OSError:
            folders = []
        locations = dict.fromkeys([directory.parent, *folders])
        picker_key = f"{key}-browse"

        def browse():
            st.session_state[directory_key] = str(st.session_state[picker_key])
            st.session_state[picker_key] = None

        st.selectbox("浏览文件夹", locations, index=None, key=picker_key, on_change=browse,
                     format_func=lambda path: f"上一级 · {path}" if path == directory.parent else path.name)
    name = st.text_input("文件名", value=filename, key=f"{key}-name").strip()
    if not st.button("保存文件", type="primary"):
        return
    if not directory.is_absolute() or not directory.is_dir():
        st.error("请输入一个已存在的完整文件夹路径。")
        return
    if not name or name.endswith(".") or any(char in name for char in '<>:"/\\|?*') or any(ord(char) < 32 for char in name):
        st.error("请输入有效的文件名。")
        return
    suffix = Path(filename).suffix
    if not name.lower().endswith(suffix.lower()):
        name += suffix
    target = directory / name
    try:
        save_export(data, target)
    except FileExistsError:
        st.error("该文件已存在，请换一个文件名或保存文件夹。")
    except OSError as exc:
        st.error(f"保存失败：{exc}")
    else:
        st.session_state[f"{key}-saved"] = target
        st.session_state["last-export-dir"] = target.parent
        st.rerun()


def download_file(data: bytes, filename: str, label: str, mime: str):
    if FROZEN:
        key = f"dl-{filename}"
        if st.button(label, width="stretch", key=key):
            export_dialog(data, filename, key)
        # if saved := st.session_state.get(f"{key}-saved"):
        #     st.caption(f"已保存：{saved}")
        #     if os.name == "nt" and st.button("打开保存目录", key=f"{key}-folder"):
        #         try:
        #             os.startfile(saved.parent)
        #         except OSError as exc:
        #             st.error(f"无法打开目录：{exc}")
        if saved := st.session_state.get(f"{key}-saved"):
            st.caption(f"已保存：{saved}")
            if st.button("打开保存目录", key=f"{key}-folder"):
                try:
                    if sys.platform == "win32":
                        os.startfile(saved.parent)
                    elif sys.platform == "darwin":
                        subprocess.run(["open", str(saved.parent)], check=False)
                    else:
                        subprocess.run(["xdg-open", str(saved.parent)], check=False)
                except Exception as exc:
                    st.error(f"无法打开目录：{exc}")
        return

    encoded = base64.b64encode(data).decode("ascii")
    # Build the download from the file bytes already delivered to the browser.
    # This avoids routing an attachment request through the browser's PDF handler.
    st.html(f"""
        <button type="button" style="width:100%;padding:.5rem 1rem;background:#146457;color:#fff;
            border:0;border-radius:.55rem;font:inherit;font-weight:600;cursor:pointer">{escape(label)}</button>
        <script>
        {{
            const button = document.currentScript.previousElementSibling;
            button.addEventListener("click", () => {{
                const bytes = Uint8Array.from(atob("{encoded}"), char => char.charCodeAt(0));
                const url = URL.createObjectURL(new Blob([bytes], {{type: {json.dumps(mime)}}}));
                const link = document.createElement("a");
                link.href = url;
                link.download = {json.dumps(filename).replace("<", r"\u003c")};
                document.body.appendChild(link);
                link.click();
                link.remove();
                setTimeout(() => URL.revokeObjectURL(url), 30000);
            }});
        }}
        </script>
    """, unsafe_allow_javascript=True)
