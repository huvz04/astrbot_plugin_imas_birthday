"""Separate singer and music-creator catalogues, with persistent manual overrides."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

CATALOGUES = {"utaite.json": ("utaite", "ut"),
              "music_creators.json": ("music_creator", "mc")}
KIND_LABELS = {"utaite": "唱见", "music_creator": "音乐创作者"}
IMAGE_HOSTS = {"static.vocadb.net", "static.utaitedb.net", "images.ctfassets.net", "storage.moegirl.org.cn"}


def load_catalogues(root: Path, override_root: Path | None = None) -> tuple[dict, dict]:
    records, redirects = {}, {}
    for filename, (kind, prefix) in CATALOGUES.items():
        path = root / filename
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != 1 or payload.get("kind") != kind:
            raise ValueError(f"Invalid public-person catalogue: {filename}")
        if override_root and (override_root / filename).exists():
            custom = json.loads((override_root / filename).read_text(encoding="utf-8"))
            if custom.get("schema") != 1 or custom.get("kind") != kind:
                raise ValueError(f"Invalid public-person override: {filename}")
            payload = {**payload, "manual_records": [*payload.get("manual_records", []), *custom.get("manual_records", [])],
                       "overrides": {**payload.get("overrides", {}), **custom.get("overrides", {})}}
        for row in [*payload.get("records", []), *payload.get("manual_records", [])]:
            original = row
            row = {**row, **payload.get("overrides", {}).get(row.get("id"), {})}
            ident, name = row.get("id", ""), row.get("name", "")
            if (not re.fullmatch(prefix + r":[a-z0-9][a-z0-9:_-]{0,110}", ident)
                    or ident in records or not isinstance(name, str) or not name.strip()
                    or len(name) > 100 or not row.get("sources")):
                raise ValueError(f"Invalid or duplicate public identity: {ident}")
            aliases = row.get("aliases", [])
            if not isinstance(aliases, list) or any(not isinstance(alias, str) or len(alias) > 150 for alias in aliases):
                raise ValueError(f"Invalid aliases: {ident}")
            birthday = row.get("birthday", "")
            if birthday:
                if not isinstance(birthday, str) or not re.fullmatch(r"\d{2}-\d{2}", birthday):
                    raise ValueError(f"Invalid birthday: {ident}")
                datetime.strptime("2000-" + birthday, "%Y-%m-%d")
            records[ident] = {**row, "kind": kind, "is_idolmaster": False,
                              "aliases": list(dict.fromkeys([name, original["name"], *original.get("aliases", []), *aliases]))}
        redirects.update(payload.get("redirects", {}))
    for origin, target in redirects.items():
        if not re.fullmatch(r"(?:ut|mc):[a-z0-9][a-z0-9:_-]{0,110}", origin) or origin in records or target not in records or origin == target:
            raise ValueError(f"Invalid cross-catalogue reference: {origin}")
    return records, redirects


_PLUGIN_DIR = Path(__file__).parent
PUBLIC_PEOPLE_OVERRIDES_ROOT = (_PLUGIN_DIR.parent.parent if _PLUGIN_DIR.parent.name == "plugins" else _PLUGIN_DIR.parent) / "imas_birthday_assets" / "people"
PUBLIC_PEOPLE_CATALOGUE, PUBLIC_PERSON_REDIRECTS = load_catalogues(_PLUGIN_DIR / "data" / "people", PUBLIC_PEOPLE_OVERRIDES_ROOT)
REMOVED_PUBLIC_PERSON_IDS = {ident for filename in CATALOGUES
                             for ident in json.loads((_PLUGIN_DIR / "data" / "people" / filename).read_text(encoding="utf-8")).get("removed_ids", [])}
PUBLIC_PEOPLE_REGISTRY = {"records": PUBLIC_PEOPLE_CATALOGUE, "redirects": PUBLIC_PERSON_REDIRECTS,
                          "kind_labels": KIND_LABELS, "image_hosts": dict.fromkeys(IMAGE_HOSTS),
                          "removed_ids": dict.fromkeys(REMOVED_PUBLIC_PERSON_IDS), "overrides_root": str(PUBLIC_PEOPLE_OVERRIDES_ROOT)}
