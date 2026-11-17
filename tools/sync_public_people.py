"""Build physically separate snapshots from official rosters and public music APIs.

No network requests run in the plugin's normal command handlers except bounded
portrait downloads. Cached API responses avoid repeated requests while reviewing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urljoin

import httpx
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = "imas-birthday-catalogue/1.0 (+https://github.com/huvz04/astrbot_plugin_imas_birthday)"


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def account_key(url):
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    if host in {"twitter.com", "x.com"}:
        return "x:" + parts.path.strip("/").lower() if parts.path.count("/") <= 2 and parts.path.strip("/") else ""
    if host == "youtube.com" and re.fullmatch(r"/(?:channel/[^/]+|@[^/]+)/?", parts.path):
        return "youtube:" + parts.path.rstrip("/")
    return ""


def merge_record(target, source):
    target["aliases"] = list(dict.fromkeys([*target["aliases"], source["name"], *source["aliases"]]))
    target["sources"] = list(dict.fromkeys([*target["sources"], *source["sources"]]))


def get_cached(client, url, cache, refresh=False, params=None):
    key = hashlib.sha256((url + json.dumps(params, sort_keys=True)).encode()).hexdigest()
    path = cache / (key + ".txt")
    if path.is_file() and not refresh:
        return path.read_text(encoding="utf-8")
    response = client.get(url, params=params)
    response.raise_for_status()
    path.write_text(response.text, encoding="utf-8")
    time.sleep(.4)
    return response.text


def parse_holo(text, base, provider):
    soup = BeautifulSoup(text, "html.parser")
    records = []
    for link in soup.find_all("a", href=True):
        url = link["href"]
        match = re.fullmatch(re.escape(base) + r"([a-z0-9-]+)/", url)
        title, image = link.find("h3"), link.find("img")
        if not match or title is None or image is None:
            continue
        roman = title.find("span")
        aliases = [clean(roman.get_text())] if roman else []
        aliases.append(match.group(1).replace("-", " "))
        if roman:
            roman.extract()
        name = re.sub(r"【[^】]*】", "", title.get_text(" ", strip=True)).strip()
        if not name:
            continue
        records.append({"id": f"vt:{provider}:{match.group(1)}", "name": name, "aliases": aliases,
                        "image_url": image.get("src", ""), "sources": [url], "directory": provider})
    return records


def parse_niji(text):
    soup = BeautifulSoup(text, "html.parser")
    node = soup.find("script", id="__NEXT_DATA__")
    if node is None:
        raise ValueError("Nijisanji roster format changed")
    rows = json.loads(node.string)["props"]["pageProps"]["allLivers"]
    records = []
    for row in rows:
        if not re.fullmatch(r"[a-z0-9-]+", row.get("slug", "")):
            raise ValueError("Invalid Nijisanji profile slug")
        image = row.get("images", {}).get("head", {}).get("url", "")
        if image.startswith("/api/image-proxy?"):
            image = parse_qs(urlsplit(image).query).get("url", [""])[0]
        records.append({"id": f"vt:nijisanji:{row['id']}", "name": row["name"],
                        "aliases": [row["enName"], row["slug"].replace("-", " ")] if row.get("enName") else [row["slug"].replace("-", " ")], "image_url": image,
                        "sources": ["https://www.nijisanji.jp/talents/l/" + row["slug"]], "directory": "nijisanji"})
    return records


def parse_artist(row, provider, prefix, expected_type):
    links = [link for link in row.get("webLinks", []) if not link.get("disabled")]
    official = [link["url"] for link in links if link.get("category") == "Official"]
    if (row.get("artistType") != expected_type or row.get("status") not in {"Approved", "Finished", "Locked"}
            or not official or not clean(row.get("defaultName")) or len(clean(row.get("defaultName"))) > 100):
        return None
    name = clean(row["defaultName"])
    aliases = [clean(item.get("value")) for item in row.get("names", []) if 1 < len(clean(item.get("value"))) <= 150]
    references = [link["url"] for link in links if link.get("category") == "Reference"
                  and any(site in link["url"] for site in ("vocadb.net/Ar/", "utaitedb.net/Ar/", "atwiki.jp/hmiku/"))]
    return {"id": f"{prefix}:{provider}:{row['id']}", "name": name, "aliases": list(dict.fromkeys(aliases)),
            "image_url": row.get("mainPicture", {}).get("urlThumb", ""),
            "sources": [f"https://{provider}.net/Ar/{row['id']}", *official[:3], *references],
            "source_status": row["status"], "directory": provider}


def fetch_artists(client, provider, prefix, artist_type, cache, max_artists, refresh):
    result, total = [], 0
    while total < max_artists:
        params = {"artistTypes": artist_type, "sort": "FollowerCount", "maxResults": min(100, max_artists - total),
                  "start": total, "fields": "Names,MainPicture,WebLinks"}
        data = json.loads(get_cached(client, f"https://{provider}.net/api/artists", cache, refresh, params))
        rows = data.get("items", [])
        if not rows:
            break
        for row in rows:
            record = parse_artist(row, provider, prefix, artist_type)
            if record:
                result.append(record)
        total += len(rows)
        print(f"{provider}: inspected {total}, retained {len(result)}", flush=True)
    return result, total


def write_catalogue(destination, kind, records, scanned, redirects=None):
    previous = json.loads(destination.read_text(encoding="utf-8")) if destination.exists() else {}
    redirects = {**previous.get("redirects", {}), **(redirects or {})}
    current = {item["id"]: item for item in records}
    retained = {item["id"]: item for item in previous.get("records", []) if item["id"] not in redirects}
    for row in retained.values():
        row.setdefault("last_seen_on", previous.get("retrieved_on", ""))
    for row in current.values():
        row["last_seen_on"] = datetime.now(timezone.utc).date().isoformat()
    retained.update(current)
    payload = {"schema": 1, "kind": kind, "retrieved_on": datetime.now(timezone.utc).date().isoformat(),
               "scanned": scanned, "records": sorted(retained.values(), key=lambda item: item["id"]),
               "manual_records": previous.get("manual_records", []), "overrides": previous.get("overrides", {}),
               "redirects": redirects}
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=ROOT.parent / "research" / "imas-birthday-tantou" / "public-people" / "api-cache")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data" / "people")
    parser.add_argument("--max-artists", type=int, default=3000, help="Maximum inspected entries per music directory")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.max_artists <= 20000:
        parser.error("--max-artists must be between 1 and 20000")
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=35, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        vtubers = []
        for base, provider in (("https://hololive.hololivepro.com/talents/", "hololive"), ("https://holostars.hololivepro.com/talent/", "holostars")):
            vtubers.extend(parse_holo(get_cached(client, base, args.cache_dir, args.refresh), base, provider))
        vtubers.extend(parse_niji(get_cached(client, "https://www.nijisanji.jp/talents", args.cache_dir, args.refresh)))
        utaite, u_scanned = fetch_artists(client, "utaitedb", "ut", "Utaite", args.cache_dir, args.max_artists, args.refresh)
        music, m_scanned = fetch_artists(client, "vocadb", "mc", "Producer", args.cache_dir, args.max_artists, args.refresh)
        # A VTuber can also appear as a singer. Verify equal public stage names
        # against the official profile's account URLs, never a guessed real identity.
        vt_names = {clean(row["name"]): row for row in vtubers}
        redirects = {}
        for singer in utaite:
            vt = vt_names.get(clean(singer["name"]))
            if not vt:
                continue
            try:
                text = get_cached(client, vt["sources"][0], args.cache_dir, args.refresh)
                soup = BeautifulSoup(text, "html.parser")
                accounts = {account_key(urljoin(vt["sources"][0], link["href"])) for link in soup.find_all("a", href=True)} - {""}
                if accounts.intersection(account_key(source) for source in singer["sources"]):
                    redirects[singer["id"]] = vt["id"]
                    merge_record(vt, singer)
                    vt["roles"] = ["vtuber", "utaite"]
                    print(f"Verified shared profile: {vt['name']}", flush=True)
            except (httpx.HTTPError, ValueError) as exc:
                print(f"Kept separate profile {vt['name']}: {type(exc).__name__}", flush=True)
    # Explicit database cross-references join dual-role creators without guessing
    # that equal names necessarily identify the same person.
    by_music_id = {record["id"]: record for record in music}
    for singer in utaite:
        for source in list(singer["sources"]):
            match = re.fullmatch(r"https?://(?:www\.)?vocadb\.net/Ar/([0-9]+)", source)
            ident = "mc:vocadb:" + match.group(1) if match else ""
            if ident in by_music_id:
                creator = by_music_id.pop(ident)
                canonical = redirects.get(singer["id"], singer["id"])
                redirects[ident] = canonical
                target = next((row for row in vtubers if row["id"] == canonical), singer)
                merge_record(target, creator)
                target["roles"] = list(dict.fromkeys([*target.get("roles", ["utaite"]), "music_creator"]))
    old_redirects = {}
    for filename in ("vtubers.json", "utaite.json", "music_creators.json"):
        path = args.output_dir / filename
        if path.exists():
            old_redirects.update(json.loads(path.read_text(encoding="utf-8")).get("redirects", {}))
    redirects = {**old_redirects, **redirects}
    for origin in redirects:
        target, seen = redirects[origin], {origin}
        while target in redirects:
            if target in seen:
                raise ValueError("Circular public identity reference")
            seen.add(target)
            target = redirects[target]
        redirects[origin] = target
    write_catalogue(args.output_dir / "vtubers.json", "vtuber", vtubers, len(vtubers))
    utaite = [row for row in utaite if row["id"] not in redirects]
    write_catalogue(args.output_dir / "utaite.json", "utaite", utaite, u_scanned, {k: v for k, v in redirects.items() if k.startswith("ut:")})
    write_catalogue(args.output_dir / "music_creators.json", "music_creator", list(by_music_id.values()), m_scanned, {k: v for k, v in redirects.items() if k.startswith("mc:")})
    print(json.dumps({"vtubers": len(vtubers), "utaite": len(utaite), "music_creators": len(by_music_id), "dual_role": len(redirects)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
