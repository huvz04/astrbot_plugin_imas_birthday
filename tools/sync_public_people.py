"""Refresh Japanese singers and verify the curated Idolmaster production credits."""
import argparse
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx
from bs4 import BeautifulSoup
from PIL import Image

from catalogue_sources import USER_AGENT, snapshot
from commons_portrait import commons_portrait
from sync_japanese_utaite import key, refresh_singers

ROOT = Path(__file__).resolve().parents[1]


def parse_artist(row, provider, prefix, expected_type, checked=None):
    if (row.get("deleted") or row.get("artistType") != expected_type
            or row.get("status") not in {"Approved", "Finished", "Locked"}):
        return None
    name = " ".join((row.get("defaultName") or "").split())
    profile = f"https://{provider}.net/Ar/{row['id']}"
    links = [link for link in row.get("webLinks", []) if not link.get("disabled")
             and urlsplit(link.get("url", "")).scheme in ("http", "https")]
    official = [link["url"] for link in links if link.get("category") == "Official"]
    if not official or not 0 < len(name) <= 100:
        return None
    references = [link["url"] for link in links if link.get("category") == "Reference"
                  and urlsplit(link["url"]).hostname in ("vocadb.net", "utaitedb.net", "utaite.wiki", "ja.wikipedia.org")]
    picture = row.get("mainPicture") or {}
    avatar = picture.get("urlThumb")
    if avatar and (urlsplit(avatar).scheme != "https" or urlsplit(avatar).hostname != f"static.{provider}.net"):
        raise ValueError("Unexpected main picture host for " + profile)
    aliases = [a["value"].strip() for a in row.get("names", []) if 0 < len((a.get("value") or "").strip()) <= 150]
    return {"id": f"{prefix}:{provider}:{row['id']}", "name": name, "aliases": list(dict.fromkeys(aliases)),
            "directory": provider, "source_status": row["status"], "sources": [profile, *official[:3], *references],
            "avatar_url": avatar, "original_url": picture.get("urlOriginal") if avatar else None,
            "source_url": profile if avatar else None, "image_author": None, "license": None,
            "verified_at": checked if avatar else None, "last_seen_on": checked}


def refresh_staff(client, data):
    """Only recheck people with an individually reviewed Idolmaster credit."""
    for row in [*data["records"], *data.get("manual_records", [])]:
        if row.get("directory") != "imas_staff" or not row.get("credit") or not row.get("credit_url"):
            raise ValueError(f"Missing reviewed Idolmaster credit for {row['id']}")
        raw, _ = snapshot(client, row["credit_url"], max_age_days=7)
        article = key(BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)).replace("髙", "高")
        if key(row["name"]).replace("髙", "高") not in article:
            raise ValueError("Review the staff credit at " + row["credit_url"])
        if not row.get("avatar_url"):
            continue
        page = urlsplit(row["source_url"])
        if page.hostname == "commons.wikimedia.org" and page.path.startswith("/wiki/File:"):
            filename = unquote(page.path.split("/wiki/File:", 1)[1]).replace("_", " ")
            zoom = (row.get("portrait_crop") or {}).get("zoom", 1)
            row.update(commons_portrait(client, filename, width=960 if zoom > 1 else 320))
        response = client.get(row["avatar_url"], follow_redirects=False)
        response.raise_for_status()
        if len(response.content) > 2_000_000:
            raise ValueError("Oversize staff portrait for " + row["name"])
        with Image.open(io.BytesIO(response.content)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"} or max(image.size) > 4096:
                raise ValueError("Unsupported staff portrait for " + row["name"])
            image.verify()
        row["verified_at"] = datetime.now(timezone.utc).date().isoformat()
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "people")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--singers-only", action="store_true")
    mode.add_argument("--staff-only", action="store_true")
    args = parser.parse_args()
    results = {name: json.loads((args.data_dir / name).read_text(encoding="utf-8"))
               for name in ("utaite.json", "music_creators.json")}
    with httpx.Client(timeout=25, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        if not args.staff_only:
            results["utaite.json"] = refresh_singers(client, results["utaite.json"], parse_artist)
        if not args.singers_only:
            refresh_staff(client, results["music_creators.json"])

    active = {r["id"] for data in results.values() for r in data["records"] + data["manual_records"]}
    known = active | {key for data in results.values() for key in data.get("legacy_names", {})}
    redirects = {key: target for data in results.values() for key, target in data["redirects"].items()}
    for data in results.values():
        for origin, target in data["redirects"].items():
            seen = {origin}
            while target in redirects and target not in seen:
                seen.add(target)
                target = redirects[target]
            if origin in active or target in seen or target not in known:
                raise ValueError("Resolve the cross-directory identity before writing: " + origin)
            data["redirects"][origin] = target
    for name, data in results.items():
        path = args.data_dir / name
        content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        if path.read_text(encoding="utf-8") != content:
            temp = path.with_suffix(".json.tmp")
            temp.write_text(content, encoding="utf-8")
            temp.replace(path)
        print(f"{name}: {len(data['records']) + len(data['manual_records'])} active")


if __name__ == "__main__":
    main()
