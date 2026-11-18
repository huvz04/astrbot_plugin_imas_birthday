"""Refresh the installed music catalogue by VocaDB / UtaiteDB artist ID."""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from catalogue_sources import USER_AGENT, snapshot
from commons_portrait import commons_portrait
from sync_japanese_utaite import accounts, japanese_title, refresh_singers

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = {"utaite.json": ("ut", "utaitedb", "Utaite"),
               "music_creators.json": ("mc", "vocadb", "Producer")}
TANAKA = "mc:manual:tanaka-hidekazu"
TANAKA_FILE = "Hidekazu Tanaka Introduction at Aikatsu! ANION 2019-03-08.jpg"


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


def refresh(client, data, prefix, provider, artist_type, additions):
    payload = {key: data[key] for key in ("schema", "kind", "overrides", "redirects", "removed_ids")}
    active = []
    archived = dict(data.get("legacy_names", {}))
    existing = {row["id"]: row for row in data["records"]}
    for ident in additions:
        existing.setdefault(ident, {"id": ident})
    for ident, old in sorted(existing.items()):
        if ":moegirl:" in ident:
            archived[ident] = {"name": old["name"], "aliases": old.get("aliases", [])}
            continue
        if not re.fullmatch(prefix + ":" + provider + r":[1-9][0-9]*", ident):
            raise ValueError("Unknown provider ID: " + ident)
        number = ident.rsplit(":", 1)[1]
        url = f"https://{provider}.net/api/artists/{number}?fields=Names,MainPicture,WebLinks,Tags"
        raw, checked = snapshot(client, url)
        artist = json.loads(raw)
        if str(artist.get("id")) != number:
            raise ValueError("Different artist returned for " + ident)
        current = parse_artist(artist, provider, prefix, artist_type, checked)
        if current is None:
            raise ValueError(f"{ident} changed identity, type or status; review before replacing the catalogue")
        if ident in additions and ident not in {r["id"] for r in data["records"]}:
            virtual = any(t.get("tag", {}).get("urlSlug") in {"vtuber", "virtual-youtuber"} for t in artist.get("tags", []))
            if virtual or not current["avatar_url"] or ident in payload["removed_ids"]:
                raise ValueError("Unreviewed virtual, retired or unpictured artist: " + ident)
        current["aliases"] = list(dict.fromkeys([*old.get("aliases", []), *current["aliases"]]))
        current["sources"] = list(dict.fromkeys([*current["sources"], *old.get("sources", [])]))
        for key in ("birthday", "birthday_source", "roles", "music_identity_source"):
            if key in old:
                current[key] = old[key]
        if "roles" in current:
            current["roles"] = [role for role in current["roles"] if role != "utaite"]
        active.append(current)
    manual = []
    for old in data.get("manual_records", []):
        if old["id"] == TANAKA:
            photo = commons_portrait(client, TANAKA_FILE)
            row = {key: old[key] for key in ("id", "name", "aliases", "sources", "roles", "directory",
                                             "birthday", "birthday_source") if key in old}
            row.update(photo)
            row["sources"] = list(dict.fromkeys([*row["sources"], photo["source_url"]]))
            manual.append(row)
        else:
            manual.append(old)
    payload.update(records=active, manual_records=manual, legacy_names=archived)
    return payload


def merge_singer_roles(singers, creators):
    # Keep the established creator ID and its local edits when two directories
    # link to the same Japanese biography or primary account.
    by_source = {}
    for row in creators["records"]:
        for url in row["sources"]:
            token = japanese_title(url) or accounts(url)
            if token:
                by_source.setdefault(token, set()).add(row["id"])
    creator_rows = {r["id"]: r for r in creators["records"]}
    unmatched = []
    for singer in singers["records"]:
        matches = set()
        for url in singer["sources"]:
            matches.update(by_source.get(japanese_title(url) or accounts(url), ()))
            voca = re.fullmatch(r"https?://(?:www\.)?vocadb\.net/Ar/(\d+)/?", url)
            if voca and "mc:vocadb:" + voca[1] in creator_rows:
                matches.add("mc:vocadb:" + voca[1])
        if not matches:
            for url in singer.get("wiki_accounts", []):
                for ident in by_source.get(accounts(url), ()):
                    creator = creator_rows[ident]
                    if singer["name"].casefold() in {n.casefold() for n in [creator["name"], *creator["aliases"]]}:
                        matches.add(ident)
        singer.pop("wiki_accounts", None)
        if len(matches) != 1:
            unmatched.append(singer)
            continue
        creator = creator_rows[matches.pop()]
        creator.update({key: singer[key] for key in ("wiki_url", "wikidata_id", "japan_source")})
        creator["aliases"] = list(dict.fromkeys([*creator["aliases"], singer["name"], *singer["aliases"]]))
        creator["sources"] = list(dict.fromkeys([*creator["sources"], *singer["sources"]]))
        creator["roles"] = list(dict.fromkeys([*creator.get("roles", []), "music_creator", "utaite"]))
        singers["redirects"][singer["id"]] = creator["id"]
    singers["records"] = unmatched


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "people")
    parser.add_argument("--add", action="append", default=[], metavar="mc:vocadb:ID",
                        help="Also verify a specific VocaDB creator. Singers are discovered from Japanese wiki profiles.")
    args = parser.parse_args()
    if any(not re.fullmatch(r"mc:vocadb:[1-9][0-9]*", ident) for ident in args.add):
        parser.error("Use a typed artist ID with a positive numeric database ID.")
    results = {}
    with httpx.Client(timeout=25, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        for filename, (prefix, provider, artist_type) in DIRECTORIES.items():
            path = args.data_dir / filename
            data = json.loads(path.read_text(encoding="utf-8"))
            additions = [i for i in args.add if i.startswith(prefix + ":")]
            results[path] = (refresh_singers(client, data, parse_artist) if prefix == "ut" else
                             refresh(client, data, prefix, provider, artist_type, additions))
    merge_singer_roles(results[args.data_dir / "utaite.json"], results[args.data_dir / "music_creators.json"])
    active = {r["id"] for data in results.values() for r in data["records"] + data["manual_records"]}
    known = active | {key for data in results.values() for key in data.get("legacy_names", {})}
    redirects = {key: target for data in results.values() for key, target in data["redirects"].items()}
    for data in results.values():
        for key, target in data["redirects"].items():
            seen = {key}
            while target in redirects and target not in seen:
                seen.add(target)
                target = redirects[target]
            if key in active or target in seen or target not in known:
                raise ValueError("Resolve the cross-directory identity before writing: " + key)
            data["redirects"][key] = target
    for path, data in results.items():
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(path)
        print(f"{path.name}: {len(data['records']) + len(data['manual_records'])} active, {len(data['legacy_names'])} archived")


if __name__ == "__main__":
    main()
