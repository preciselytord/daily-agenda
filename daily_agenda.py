"""Append one typed-text page per day to a reMarkable notebook via the cloud.

Usage:
    daily_agenda.py append AGENDA.md [--name "Daily agenda"] [--folder /] [--mode pdf|text]
    daily_agenda.py render AGENDA.md          # print the flattened page text, no upload

Default mode "pdf": the agenda is rendered with agenda_pdf.py into a designed
page and appended to a growing PDF document (existing pages keep their ids, so
handwriting on earlier days survives). Mode "text" appends a typed-text page to
a native notebook instead.

Requirements:
  - rmapi (ddvk fork). Found via $RMAPI, PATH, ~/go/bin, or auto-downloaded
    from GitHub releases into ~/.local/bin.
  - reMarkable device token: either an existing ~/.rmapi config, or the env
    var RMAPI_DEVICE_TOKEN (the "devicetoken" value), which is written to
    ~/.rmapi on first run.
  - python packages: reportlab + pypdf (pdf mode), rmscene (text mode).

The notebook is downloaded, a new page is appended locally (existing pages and
handwriting are preserved), and the bundle is re-uploaded with `rmapi put
--force`, which recreates the document in place.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path

RMAPI_RELEASE = "https://github.com/ddvk/rmapi/releases/latest/download/rmapi-linux-amd64.tar.gz"


# ---------------------------------------------------------------- rmapi setup

def find_rmapi() -> str:
    cand = [os.environ.get("RMAPI"), shutil.which("rmapi"),
            str(Path.home() / "go/bin/rmapi"), str(Path.home() / ".local/bin/rmapi")]
    for c in cand:
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return c
    dest = Path.home() / ".local/bin"
    dest.mkdir(parents=True, exist_ok=True)
    print(f"downloading rmapi -> {dest}", file=sys.stderr)
    with urllib.request.urlopen(RMAPI_RELEASE, timeout=60) as r:
        data = r.read()
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        for m in tf.getmembers():
            if m.isfile() and Path(m.name).name == "rmapi":
                m.name = "rmapi"
                tf.extract(m, dest)
    p = dest / "rmapi"
    p.chmod(0o755)
    return str(p)


def ensure_token() -> None:
    cfg = Path(os.environ.get("RMAPI_CONFIG", Path.home() / ".rmapi"))
    if cfg.exists():
        return
    tok = os.environ.get("RMAPI_DEVICE_TOKEN")
    if not tok:
        sys.exit("no ~/.rmapi and RMAPI_DEVICE_TOKEN is not set")
    cfg.write_text(f"devicetoken: {tok}\nusertoken: \"\"\n")
    cfg.chmod(0o600)


def rmapi(binary: str, *args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run([binary, *args], cwd=cwd, text=True, capture_output=True)
    if check and p.returncode != 0:
        sys.exit(f"rmapi {' '.join(args)} failed:\n{p.stdout}\n{p.stderr}")
    return p


# ---------------------------------------------------------------- page text

def markdown_to_page_text(md: str) -> str:
    """Flatten simple markdown into typed text the tablet renders as-is."""
    out: list[str] = []
    for raw in md.splitlines():
        line = raw.rstrip()
        m = re.match(r"^(#+)\s+(.*)", line)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            if out and out[-1] != "":
                out.append("")
            out.append(title.upper() if level <= 2 else title)
            if level == 1:
                out.append("─" * min(len(title), 40))
            continue
        m = re.match(r"^(\s*)[-*]\s+(.*)", line)
        if m:
            indent = len(m.group(1)) // 2
            out.append("  " * indent + "• " + m.group(2))
            continue
        m = re.match(r"^(\s*)\[( |x)\]\s+(.*)", line)
        if m:
            out.append("  " * (len(m.group(1)) // 2) + ("☑ " if m.group(2) == "x" else "☐ ") + m.group(3))
            continue
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"`(.+?)`", r"\1", line)
        out.append(line)
    text = "\n".join(out).strip("\n")
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text + "\n"


def page_rm_bytes(text: str) -> bytes:
    from rmscene import simple_text_document, write_blocks  # type: ignore
    buf = io.BytesIO()
    write_blocks(buf, list(simple_text_document(text)))
    return buf.getvalue()


# ---------------------------------------------------------------- notebook bundle

def next_page_idx(values: list[str]) -> str:
    values = [v for v in values if isinstance(v, str) and v]
    if not values:
        return "ba"
    last = max(values)
    tail = last[-1]
    return last[:-1] + chr(ord(tail) + 1) if tail < "z" else last + "a"


def now_ms() -> str:
    return str(int(time.time() * 1000))


def new_bundle(name: str) -> tuple[str, dict, dict, dict[str, bytes]]:
    doc_id = str(uuid.uuid4())
    content = {
        "coverPageNumber": -1,
        "documentMetadata": {},
        "extraMetadata": {},
        "fileType": "notebook",
        "fontName": "",
        "formatVersion": 2,
        "lineHeight": -1,
        "orientation": "portrait",
        "pageCount": 0,
        "pageTags": [],
        "tags": [],
        "textAlignment": "left",
        "textScale": 1,
        "zoomMode": "bestFit",
        "cPages": {
            "lastOpened": {"timestamp": "1:1", "value": ""},
            "original": {"timestamp": "0:0", "value": -1},
            "pages": [],
            "uuids": [{"first": str(uuid.uuid4()), "second": 1}],
        },
    }
    meta = {
        "createdTime": now_ms(),
        "lastModified": now_ms(),
        "lastOpened": now_ms(),
        "lastOpenedPage": 0,
        "new": False,
        "parent": "",
        "pinned": False,
        "source": "",
        "type": "DocumentType",
        "visibleName": name,
    }
    return doc_id, content, meta, {}


def load_bundle(path: Path) -> tuple[str, dict, dict, dict[str, bytes]]:
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            files[n] = z.read(n)
    content_name = next(n for n in files if n.endswith(".content"))
    doc_id = content_name[:-len(".content")]
    content = json.loads(files.pop(content_name))
    meta = json.loads(files.pop(f"{doc_id}.metadata", b"{}") or b"{}")
    return doc_id, content, meta, files


def append_page(doc_id: str, content: dict, files: dict[str, bytes], rm_bytes: bytes) -> int:
    cpages = content.setdefault("cPages", {"pages": [], "uuids": [], "original": {"timestamp": "0:0", "value": -1}})
    pages = cpages.setdefault("pages", [])
    live = [p for p in pages if "deleted" not in p]
    page_id = str(uuid.uuid4())
    idx = next_page_idx([p.get("idx", {}).get("value") for p in live])
    pages.append({
        "id": page_id,
        "idx": {"timestamp": "1:2", "value": idx},
        "template": {"timestamp": "1:2", "value": "Blank"},
    })
    cpages["lastOpened"] = {"timestamp": "1:2", "value": page_id}
    content["pageCount"] = len(live) + 1
    files[f"{doc_id}/{page_id}.rm"] = rm_bytes
    return content["pageCount"]


def write_bundle(path: Path, doc_id: str, content: dict, meta: dict, files: dict[str, bytes]) -> None:
    meta["lastModified"] = now_ms()
    meta["lastOpenedPage"] = max(content.get("pageCount", 1) - 1, 0)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{doc_id}.content", json.dumps(content, indent=4))
        z.writestr(f"{doc_id}.metadata", json.dumps(meta, indent=4))
        for n, b in files.items():
            z.writestr(n, b)


# ---------------------------------------------------------------- pdf bundle

def new_pdf_bundle(name: str) -> tuple[str, dict, dict, dict[str, bytes]]:
    doc_id, content, meta, files = new_bundle(name)
    content.update({"fileType": "pdf", "dummyDocument": False, "textAlignment": "left"})
    content["cPages"]["original"] = {"timestamp": "1:2", "value": 0}
    return doc_id, content, meta, files


def append_pdf_page(doc_id: str, content: dict, files: dict[str, bytes], page_pdf: bytes) -> int:
    from pypdf import PdfReader, PdfWriter  # type: ignore
    writer = PdfWriter()
    existing = files.get(f"{doc_id}.pdf")
    if existing:
        for pg in PdfReader(io.BytesIO(existing)).pages:
            writer.add_page(pg)
    for pg in PdfReader(io.BytesIO(page_pdf)).pages:
        writer.add_page(pg)
    buf = io.BytesIO()
    writer.write(buf)
    files[f"{doc_id}.pdf"] = buf.getvalue()
    total = len(writer.pages)

    cpages = content.setdefault("cPages", {"pages": [], "uuids": [], "original": {"timestamp": "1:2", "value": 0}})
    pages = cpages.setdefault("pages", [])
    live = [p for p in pages if "deleted" not in p]
    page_id = str(uuid.uuid4())
    pages.append({
        "id": page_id,
        "idx": {"timestamp": "1:2", "value": next_page_idx([p.get("idx", {}).get("value") for p in live])},
        "redir": {"timestamp": "1:2", "value": total - 1},
        "template": {"timestamp": "1:2", "value": "Blank"},
    })
    cpages["lastOpened"] = {"timestamp": "1:2", "value": page_id}
    cpages["original"] = {"timestamp": "1:2", "value": total}
    content["pageCount"] = len(live) + 1
    files[f"{doc_id}.pagedata"] = b"Blank\n" * (len(live) + 1)
    return content["pageCount"]


def render_agenda_pdf(markdown_path: Path) -> bytes:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import agenda_pdf  # type: ignore
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
        out = Path(tf.name)
    try:
        agenda_pdf.render(markdown_path.read_text(), out)
        return out.read_bytes()
    finally:
        out.unlink(missing_ok=True)


# ---------------------------------------------------------------- commands

def cmd_render(args: argparse.Namespace) -> None:
    sys.stdout.write(markdown_to_page_text(Path(args.markdown).read_text()))


def cmd_append(args: argparse.Namespace) -> None:
    md_path = Path(args.markdown)
    if args.mode == "pdf":
        page_pdf = render_agenda_pdf(md_path)
    else:
        rm = page_rm_bytes(markdown_to_page_text(md_path.read_text()))
    ensure_token()
    binary = find_rmapi()
    remote = args.folder.rstrip("/") + "/" + args.name
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        got = rmapi(binary, "get", remote, cwd=tmp, check=False)
        bundle = tmp / f"{args.name}.rmdoc"
        wanted = "pdf" if args.mode == "pdf" else "notebook"
        if got.returncode == 0 and bundle.exists():
            doc_id, content, meta, files = load_bundle(bundle)
            if content.get("fileType", "notebook") != wanted:
                print(f"existing document is {content.get('fileType')}, not {wanted}; replacing it", file=sys.stderr)
                doc_id, content, meta, files = (new_pdf_bundle if wanted == "pdf" else new_bundle)(args.name)
            else:
                print(f"downloaded existing document ({content.get('pageCount')} pages)", file=sys.stderr)
        else:
            doc_id, content, meta, files = (new_pdf_bundle if wanted == "pdf" else new_bundle)(args.name)
            print("document not found; creating it", file=sys.stderr)
        if args.mode == "pdf":
            n = append_pdf_page(doc_id, content, files, page_pdf)
        else:
            n = append_page(doc_id, content, files, rm)
        write_bundle(bundle, doc_id, content, meta, files)
        rmapi(binary, "put", "--force", str(bundle), args.folder, cwd=tmp)
        print(f"uploaded {remote}: page {n} added", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render", help="print the flattened page text")
    r.add_argument("markdown")
    r.set_defaults(func=cmd_render)
    a = sub.add_parser("append", help="append the markdown as a new page and upload")
    a.add_argument("markdown")
    a.add_argument("--name", default="Daily agenda")
    a.add_argument("--folder", default="/")
    a.add_argument("--mode", choices=["pdf", "text"], default="pdf")
    a.set_defaults(func=cmd_append)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
