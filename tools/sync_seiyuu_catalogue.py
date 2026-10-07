"""Refresh the Seigura public-directory index without touching user data.

The directory listing is public; profile pages currently require access. Preserves
reviewed aliases already in seiyuu_catalogue.py when an ID remains present.
"""
import asyncio
import html
import importlib.util
import pprint
import re
from pathlib import Path

import httpx
try:
    from opencc import OpenCC
except ImportError:
    OpenCC = None

ROOT = Path(__file__).resolve().parents[1]
URL = "https://sugotoku-seigura.secureserv.jp/directory/list.php"
ROW = re.compile(r'<a\s+href="/directory/\?id=(\d+)"[^>]*>.*?<dd>\s*(.*?)\s*</dd>', re.S)


def parse_rows(markup):
    return {"va:" + ident: html.unescape(re.sub(r"<[^>]+>", "", name)).strip()
            for ident, name in ROW.findall(markup)}


async def main():
    semaphore = asyncio.Semaphore(6)
    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
        async def fetch(page):
            async with semaphore:
                for attempt in range(3):
                    try:
                        response = await client.get(URL, params={"page": page} if page > 1 else {})
                        response.raise_for_status()
                        rows = parse_rows(response.text)
                        if not rows:
                            raise ValueError(f"empty directory page {page}")
                        return rows
                    except Exception:
                        if attempt == 2:
                            raise
                        await asyncio.sleep(attempt + 1)
        first = await fetch(1)
        response = await client.get(URL)
        pages = max(int(value) for value in re.findall(r"list\.php\?page=(\d+)", response.text))
        results = await asyncio.gather(*(fetch(page) for page in range(2, pages + 1)))
    all_rows = dict(first)
    for rows in results:
        all_rows.update(rows)
    if len(all_rows) < 1700:
        raise ValueError("Directory appears incomplete; catalogue was not changed")
    target = ROOT / "seiyuu_catalogue.py"
    spec = importlib.util.spec_from_file_location("existing_seiyuu_catalogue", target)
    existing = {}
    if spec and spec.loader:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        existing = module.VOICE_ACTOR_CATALOGUE
    converter = OpenCC("t2s") if OpenCC else None
    catalogue = {}
    for key, name in all_rows.items():
        previous = existing.get(key, {})
        simplified = converter.convert(name).replace(" ", "") if converter else ""
        aliases = list(dict.fromkeys([*previous.get("aliases", []), simplified,
                                       *([previous["name"]] if previous.get("name") and previous["name"] != name else [])]))
        aliases = [alias for alias in aliases if alias and alias != name.replace(" ", "")]
        catalogue[key] = {"name": name, "aliases": aliases,
                          "image_url": f"https://seigura.secureserv.jp/img/talent/{key[3:]}.jpg"}
    source = ("# Generated from the public Seigura directory listing.\n"
              "# Covers all publicly listed voice actors; profile pages require access.\n"
              "# Chinese names are search aliases only. Refresh with tools/sync_seiyuu_catalogue.py.\n"
              "VOICE_ACTOR_CATALOGUE = " + pprint.pformat(catalogue, sort_dicts=True, width=110, compact=True) + "\n")
    target.write_text(source, encoding="utf-8")
    print(f"Updated {len(catalogue)} voice actors from {pages} directory pages")


if __name__ == "__main__":
    asyncio.run(main())
