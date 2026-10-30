"""build_html: embed payload JSON + inline CSS/JS into ONE offline HTML file."""

from __future__ import annotations

import html
import json
from pathlib import Path

ASSETS = Path(__file__).with_name("assets")


def _read(name: str) -> str:
    return (ASSETS / name).read_text(encoding="utf-8")


def payload_json(payload: dict) -> str:
    """JSON safe to embed inside <script type="application/json">."""
    s = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    # never let the data close the script element or open a comment
    return s.replace("</", "<\\/").replace("<!--", "<\\!--")


def build_html(payload: dict, out_path) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    meta = payload.get("meta", {})
    status = meta.get("status", "provisional")
    tpl = _read("template.html")
    doc = (tpl.replace("/*__CSS__*/", _read("page.css"))
              .replace("/*__JS__*/", _read("page.js"))
              .replace("__STATUS__", html.escape(status))
              .replace("__DESCRIPTION__", html.escape(meta.get("description", "")))
              .replace("__PAYLOAD__", payload_json(payload)))
    out.write_text(doc, encoding="utf-8")
    return out


def extract_payload(html_text: str) -> dict:
    """Inverse of build_html (used by tests / headless checks)."""
    start_tag = '<script type="application/json" id="payload">'
    i = html_text.index(start_tag) + len(start_tag)
    j = html_text.index("</script>", i)
    return json.loads(html_text[i:j].replace("<\\/", "</").replace("<\\!--", "<!--"))
