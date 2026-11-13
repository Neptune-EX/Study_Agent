import base64
from html import escape
import json

import streamlit as st


def download_file(data: bytes, filename: str, label: str, mime: str):
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
