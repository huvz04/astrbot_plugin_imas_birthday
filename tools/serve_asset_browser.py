from __future__ import annotations

import argparse
import csv
import html
import mimetypes
import os
import re
import shutil
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "asset_candidates" / "asset_candidates_save_as_name.csv"
USER_AGENT = "AstrBot-IMAS-Birthday-AssetBrowser/0.1"
IMAGE_MAGIC = (
    b"\xff\xd8\xff",
    b"\x89PNG\r\n\x1a\n",
    b"RIFF",
)


def is_image_bytes(data: bytes) -> bool:
    if len(data) < 16:
        return False
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return True
    return any(data.startswith(prefix) for prefix in IMAGE_MAGIC[:2])


def image_content_type(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def load_rows(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        raise FileNotFoundError(f"候选清单不存在：{csv_path}")
    with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    usable: list[dict[str, str]] = []
    for index, row in enumerate(rows):
        save_name = row.get("save_as_name") or Path(row.get("target_filename", "")).name
        if not save_name:
            continue
        row = dict(row)
        row["_id"] = str(index)
        row["_save_name"] = save_name
        row["_image_url"] = f"/image/{index}/{urllib.parse.quote(save_name)}"
        usable.append(row)
    return usable


def page_html(rows: list[dict[str, str]], assets_dir: str) -> bytes:
    brands = sorted({row.get("brand", "") for row in rows if row.get("brand")})
    brand_options = ['<option value="">全部企划</option>'] + [
        f'<option value="{html.escape(brand, quote=True)}">{html.escape(brand)}</option>'
        for brand in brands
    ]
    cards = []
    for row in rows:
        brand = row.get("brand", "")
        character = row.get("character", "")
        card_name = row.get("card_name") or row.get("name", "")
        target = row.get("target_filename", "")
        source = row.get("detail_url") or row.get("url") or ""
        source_link = (
            f'<a href="{html.escape(source, quote=True)}" target="_blank">source</a>'
            if source
            else ""
        )
        cards.append(
            f"""
<article class="tile" data-brand="{html.escape(brand, quote=True)}" data-character="{html.escape(character, quote=True)}" data-text="{html.escape((brand + " " + character + " " + card_name + " " + target).lower(), quote=True)}">
  <a class="image" href="{html.escape(row["_image_url"], quote=True)}" download="{html.escape(row["_save_name"], quote=True)}" target="_blank" title="右键保存图片，默认文件名：{html.escape(row["_save_name"], quote=True)}">
    <img loading="lazy" src="{html.escape(row["_image_url"], quote=True)}" alt="{html.escape(card_name, quote=True)}">
  </a>
  <div class="caption">
    <strong>{html.escape(card_name)}</strong>
    <small>{html.escape(brand)} / {html.escape(character)}</small>
    <small>保存名：<code>{html.escape(row["_save_name"])}</code></small>
    <small>建议覆盖：<code>{html.escape(target)}</code></small>
    <button class="install" data-id="{html.escape(row["_id"], quote=True)}">覆盖到映射目录</button>
    <small class="result"></small>
    {source_link}
  </div>
</article>"""
        )

    body = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IM@S Asset Browser</title>
<style>
:root {{ --bg:#f6f2eb; --ink:#24272d; --muted:#68717d; --line:#d8d0c4; --card:#fffdf8; --accent:#ef4f82; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif; background:var(--bg); color:var(--ink); }}
header {{ position:sticky; top:0; z-index:2; padding:18px 24px; background:rgba(246,242,235,.94); backdrop-filter:blur(14px); border-bottom:1px solid var(--line); }}
h1 {{ margin:0; font-size:24px; letter-spacing:0; }}
header p {{ margin:6px 0 0; color:var(--muted); font-size:14px; }}
.controls {{ display:flex; flex-wrap:wrap; gap:10px; margin-top:12px; }}
input, select {{ height:34px; border:1px solid var(--line); border-radius:6px; padding:0 10px; background:#fffdf8; color:var(--ink); }}
input {{ min-width:280px; flex:1; }}
main {{ padding:20px 24px 48px; }}
.summary {{ display:flex; flex-wrap:wrap; gap:10px; margin-bottom:20px; }}
.pill {{ border:1px solid var(--line); border-radius:999px; padding:7px 12px; background:#fffaf1; font-size:13px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:16px; align-items:start; }}
.tile {{ background:var(--card); border:1px solid var(--line); border-radius:8px; overflow:hidden; box-shadow:0 2px 10px rgba(31,35,40,.05); }}
.tile .image {{ display:block; background:#ece7dd; }}
.tile img {{ display:block; width:100%; height:280px; object-fit:contain; background:#ece7dd; }}
.caption {{ padding:10px 12px 12px; display:grid; gap:4px; }}
.caption strong {{ font-size:14px; line-height:1.35; overflow-wrap:anywhere; }}
.caption small {{ color:var(--muted); font-size:12px; }}
.caption code {{ font-family:Consolas,"SFMono-Regular",monospace; color:#2c3138; overflow-wrap:anywhere; }}
.caption a {{ color:#2374ab; font-size:12px; text-decoration:none; }}
.install {{ width:max-content; max-width:100%; height:30px; border:1px solid var(--line); border-radius:6px; padding:0 10px; background:#2f343b; color:#fff; cursor:pointer; font-size:12px; }}
.install:disabled {{ opacity:.55; cursor:default; }}
.result.ok {{ color:#247a39; }}
.result.err {{ color:#b42318; }}
.hidden {{ display:none !important; }}
@media (min-width:1500px) {{ .grid {{ grid-template-columns:repeat(auto-fill,minmax(360px,1fr)); }} .tile img {{ height:320px; }} }}
</style>
</head>
<body>
<header>
  <h1>IM@S Asset Browser</h1>
  <p>右键图片另存为时，默认文件名就是插件映射文件名。浏览器不能自动替你选择目录，请手动保存到对应子目录。当前插件图片目录：<code>{html.escape(assets_dir)}</code></p>
  <div class="controls">
    <input id="q" placeholder="搜索角色 / 卡名 / 保存路径">
    <select id="brand">{"".join(brand_options)}</select>
  </div>
</header>
<main>
  <div class="summary">
    <span class="pill">候选图：{len(rows)}</span>
    <span class="pill">图片 URL 以中文保存名结尾</span>
    <span class="pill">点击图可直接下载；右键也会带中文默认文件名</span>
  </div>
  <section class="grid">
    {"".join(cards)}
  </section>
</main>
<script>
const q = document.querySelector('#q');
const brand = document.querySelector('#brand');
function applyFilter() {{
  const needle = q.value.trim().toLowerCase();
  const b = brand.value;
  document.querySelectorAll('.tile').forEach(tile => {{
    const ok = (!b || tile.dataset.brand === b) && (!needle || tile.dataset.text.includes(needle));
    tile.classList.toggle('hidden', !ok);
  }});
}}
q.addEventListener('input', applyFilter);
brand.addEventListener('change', applyFilter);
document.querySelectorAll('.install').forEach(btn => {{
  btn.addEventListener('click', async () => {{
    const result = btn.parentElement.querySelector('.result');
    btn.disabled = true;
    result.className = 'result';
    result.textContent = '写入中...';
    try {{
      const resp = await fetch('/install/' + encodeURIComponent(btn.dataset.id), {{ method: 'POST' }});
      const text = await resp.text();
      if (!resp.ok) throw new Error(text || resp.statusText);
      result.className = 'result ok';
      result.textContent = text;
    }} catch (err) {{
      result.className = 'result err';
      result.textContent = err.message || String(err);
    }} finally {{
      btn.disabled = false;
    }}
  }});
}});
</script>
</body>
</html>"""
    return body.encode("utf-8")


class AssetBrowserHandler(BaseHTTPRequestHandler):
    rows: list[dict[str, str]] = []
    rows_by_id: dict[str, dict[str, str]] = {}
    assets_dir: str = ""
    assets_dir_path: Path | None = None
    http = httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=45,
    )

    def log_message(self, fmt: str, *args):
        print(f"[asset-browser] {self.address_string()} {fmt % args}")

    def send_bytes(self, data: bytes, content_type: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in {"/", "/index.html"}:
            return self.send_bytes(page_html(self.rows, self.assets_dir), "text/html; charset=utf-8")
        match = re.fullmatch(r"/image/(\d+)/(.+)", path)
        if match:
            return self.serve_image(match.group(1), urllib.parse.unquote(match.group(2)))
        self.send_error(404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        match = re.fullmatch(r"/install/(\d+)", path)
        if match:
            return self.install_image(match.group(1))
        self.send_error(404)

    def row_image_bytes(self, row: dict[str, str]) -> bytes:
        source_file = row.get("save_as_file") or row.get("file")
        if source_file:
            path = ROOT / source_file
            if path.exists():
                data = path.read_bytes()
                if not is_image_bytes(data):
                    raise ValueError("local source is not a valid image")
                return data

        source_url = row.get("url") or row.get("detail_url")
        if not source_url:
            raise FileNotFoundError("no image source")
        response = self.http.get(source_url)
        response.raise_for_status()
        data = response.content
        if not is_image_bytes(data):
            raise ValueError("remote source is not a valid image")
        return data

    def serve_image(self, row_id: str, requested_name: str):
        row = self.rows_by_id.get(row_id)
        if not row:
            return self.send_error(404, "unknown image")
        if requested_name != row["_save_name"]:
            # Keep the URL basename stable for "Save image as".
            return self.send_error(404, "filename mismatch")

        try:
            data = self.row_image_bytes(row)
        except Exception as exc:
            return self.send_error(502, f"fetch failed: {exc}")

        content_type = image_content_type(data) or mimetypes.guess_type(row["_save_name"])[0] or "image/jpeg"
        self.send_bytes(data, content_type)

    def install_image(self, row_id: str):
        row = self.rows_by_id.get(row_id)
        if not row:
            return self.send_error(404, "unknown image")
        if self.assets_dir_path is None:
            return self.send_error(400, "server was started without a writable --assets-dir")
        target_filename = row.get("target_filename", "").strip()
        if not target_filename:
            return self.send_error(400, "row has no target_filename")
        assets_root = self.assets_dir_path.resolve()
        target = (assets_root / target_filename).resolve()
        if assets_root != target and assets_root not in target.parents:
            return self.send_error(400, "target path escapes assets dir")
        try:
            data = self.row_image_bytes(row)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_bytes(data)
            shutil.move(str(tmp), str(target))
        except Exception as exc:
            return self.send_error(500, f"install failed: {exc}")
        message = f"OK 已覆盖：{target}"
        self.send_bytes(message.encode("utf-8"), "text/plain; charset=utf-8")


def parse_args():
    parser = argparse.ArgumentParser(description="Serve a local IM@S asset browser with right-click friendly filenames.")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Candidate CSV generated by the asset browser builder.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--assets-dir", default=os.environ.get("IMAS_BIRTHDAY_ASSETS_DIR", ""), help="Only shown as a hint in the page header.")
    return parser.parse_args()


def main():
    args = parse_args()
    rows = load_rows(args.csv)
    AssetBrowserHandler.rows = rows
    AssetBrowserHandler.rows_by_id = {row["_id"]: row for row in rows}
    AssetBrowserHandler.assets_dir = args.assets_dir or "AstrBot/data/imas_birthday_assets/characters"
    AssetBrowserHandler.assets_dir_path = Path(args.assets_dir).resolve() if args.assets_dir else None
    server = ThreadingHTTPServer((args.host, args.port), AssetBrowserHandler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Asset browser ready: {url}")
    print(f"Rows: {len(rows)}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping asset browser.")
    finally:
        server.shutdown()
        threading.Thread(target=server.server_close).start()


if __name__ == "__main__":
    main()
