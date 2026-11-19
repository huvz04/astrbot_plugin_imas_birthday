"""Resolve Japanese wiki singer profiles against UtaiteDB without joining by name alone."""
import json
import re
import unicodedata
from collections import defaultdict
from urllib.parse import quote, unquote, urlencode, urlsplit

from catalogue_sources import snapshot
from commons_portrait import commons_portrait

API = "https://ja.wikipedia.org/w/api.php?"
WD = "https://www.wikidata.org/w/api.php?"
WIKI = "https://ja.wikipedia.org/wiki/"


def key(name):
    return "".join(unicodedata.normalize("NFKC", name).replace("_", " ").split()).casefold()


def batches(items, size):
    items = sorted(items)
    for i in range(0, len(items), size):
        yield items[i:i + size]


def wiki_query(client, params):
    more = {}
    while True:
        raw, _ = snapshot(client, API + urlencode({"action": "query", "format": "json", "formatversion": 2, **params, **more}))
        doc = json.loads(raw)
        yield doc["query"]
        more = doc.get("continue")
        if not more:
            break


def article_titles(client):
    todo, seen, titles = ["歌い手"], set(), set()
    while todo:
        category = todo.pop()
        if category in seen:
            continue
        seen.add(category)
        for data in wiki_query(client, {"list": "categorymembers", "cmtitle": "Category:" + category, "cmlimit": "max"}):
            for row in data["categorymembers"]:
                if row["ns"] == 14:
                    todo.append(row["title"].removeprefix("Category:"))
                elif row["ns"] == 0:
                    titles.add(row["title"])
    return titles


def provider_rows(client):
    rows, total = {}, None
    start = 0
    while total is None or start < total:
        params = {"artistTypes": "Utaite", "sort": "Name", "maxResults": 100, "start": start,
                  "getTotalCount": "true", "fields": "Names,MainPicture,WebLinks,Tags"}
        raw, checked = snapshot(client, "https://utaitedb.net/api/artists?" + urlencode(params))
        page = json.loads(raw)
        if total is None:
            total = page["totalCount"]
        if total != page["totalCount"] or not page["items"] or any(r["id"] in rows for r in page["items"]):
            raise ValueError("UtaiteDB pagination changed; retry with a fresh snapshot")
        rows.update({r["id"]: (r, checked) for r in page["items"]})
        start += len(page["items"])
    if len(rows) != total:
        raise ValueError("Incomplete UtaiteDB snapshot")
    return rows


def japanese_title(url):
    parts = urlsplit(url)
    if parts.hostname in {"ja.wikipedia.org", "ja.m.wikipedia.org"} and parts.path.startswith("/wiki/"):
        return unquote(parts.path[6:]).replace("_", " ")
    return ""


def accounts(url):
    parts = urlsplit(url)
    host, bits = parts.hostname or "", parts.path.strip("/").split("/")
    if host.removeprefix("www.") in {"twitter.com", "x.com", "mobile.twitter.com"} and len(bits) == 1 and re.fullmatch(r"[\w]{1,15}", bits[0]):
        return "twitter:" + bits[0].lower()
    if host.removeprefix("www.") == "youtube.com" and len(bits) == 2 and bits[0] == "channel":
        return "youtube:" + bits[1]
    if host in {"www.nicovideo.jp", "nicovideo.jp", "sp.nicovideo.jp"} and len(bits) == 2 and bits[0] in {"user", "mylist"} and bits[1].isdigit():
        return "niconico:" + "/".join(bits)
    return ""


def claims(entity, prop):
    return [s["mainsnak"]["datavalue"]["value"] for s in entity.get("claims", {}).get(prop, [])
            if s.get("rank") != "deprecated" and "datavalue" in s.get("mainsnak", {})]


def eligible(page, entity, in_category, singer_match):
    lead = page.get("extract", "").split("\n")[0]
    description = lead.split("。")[0]
    if "disambiguation" in page.get("pageprops", {}) or not lead or not (in_category or singer_match):
        return False
    types = {v["id"] for v in claims(entity, "P31")}
    if types and "Q5" not in types:
        return False
    if (any(re.search(r"バーチャル(?:YouTuber|アーティスト|シンガー)|VTuber", c["title"], re.I)
            for c in page.get("categories", []))
            or re.search(r"バーチャル(?:YouTuber|ユーチューバー|シンガー|歌手|ライバー)|VTuber", description, re.I)):
        return False
    if not re.search(r"歌手|歌い手|歌ってみた|シンガー|ボーカリスト|ボカロP|歌唱動画|歌唱配信|歌唱活動", lead) and not in_category:
        return False
    citizenship = {v["id"] for v in claims(entity, "P27")}
    if citizenship and citizenship != {"Q17"}:
        return False
    if not types and not re.search(r"(?:彼女|彼|生まれ|出身|生誕|生年|\d+月\d+日)", lead):
        return False
    return citizenship == {"Q17"} or bool(re.search(r"日本の(?:[^。]{0,24})(?:歌手|歌い手|シンガー|音楽家|声優|ボーカリスト|作曲家|アーティスト)", lead))


def refresh_singers(client, previous, parse_artist):
    from community_utaite import add_community_singers
    candidates = provider_rows(client)
    by_title, by_account = defaultdict(set), defaultdict(set)
    for ident, (row, _) in candidates.items():
        for link in row.get("webLinks", []):
            if link.get("disabled"):
                continue
            url = link.get("url", "")
            title = japanese_title(url)
            if title:
                by_title[title].add(ident)
            account = accounts(url)
            if account and link.get("category") == "Official":
                by_account[account].add(ident)
    category = article_titles(client)
    # Existing stage names can have a Japanese biography outside the 歌い手 categories.
    old = {r["id"]: r for r in previous["records"]}
    seed = {r["name"] for r in old.values() if r["id"].startswith("ut:utaitedb:")}
    seed.update(r["name"] for i, r in previous.get("legacy_names", {}).items() if i.startswith("ut:utaitedb:"))
    titles = category | set(by_title) | seed
    pages, redirects = {}, {}
    for group in batches(titles, 20):
        for doc in wiki_query(client, {"titles": "|".join(group), "prop": "extracts|pageprops|categories|extlinks",
                                       "cllimit": "max", "ellimit": "max",
                                       "exintro": 1, "explaintext": 1, "exlimit": "max", "redirects": 1}):
            for item in [*doc.get("normalized", []), *doc.get("redirects", [])]:
                redirects[item["from"]] = item["to"]
            for p in doc["pages"]:
                if "missing" not in p and p["ns"] == 0:
                    page = pages.setdefault(p["title"], {})
                    categories = [*page.get("categories", []), *p.get("categories", [])]
                    links = [*page.get("extlinks", []), *p.get("extlinks", [])]
                    page.update(p)
                    page["categories"] = categories
                    page["extlinks"] = links
    def canonical(title):
        seen = set()
        while title in redirects and title not in seen:
            seen.add(title)
            title = redirects[title]
        return title
    category = {canonical(t) for t in category}
    for t, ids in list(by_title.items()):
        by_title[canonical(t)].update(ids)
    qids = {p["pageprops"]["wikibase_item"] for p in pages.values() if p.get("pageprops", {}).get("wikibase_item")}
    entities = {}
    for group in batches(qids, 40):
        params = {"action": "wbgetentities", "format": "json", "ids": "|".join(group), "props": "claims"}
        raw, _ = snapshot(client, WD + urlencode(params))
        entities.update(json.loads(raw)["entities"])
    records, archived, seen_provider = {}, dict(previous.get("legacy_names", {})), {}
    for title, page in sorted(pages.items()):
        qid = page.get("pageprops", {}).get("wikibase_item")
        entity = entities.get(qid, {})
        matched = set(by_title[title])
        if not matched:
            for a in claims(entity, "P2002"):
                matched.update(by_account["twitter:" + a.lower()])
            for a in claims(entity, "P2397"):
                matched.update(by_account["youtube:" + a])
        wiki_links = {accounts(link["url"]): link["url"] for link in page.get("extlinks", []) if accounts(link["url"])}
        if not matched:
            # Article references can mention collaborators. Require both a matching
            # performing name and the same account listed as official by UtaiteDB.
            artist_name = key(re.sub(r" \([^()]+\)$", "", title))
            for token in wiki_links:
                for artist_id in by_account[token]:
                    artist = candidates[artist_id][0]
                    if artist_name in {key(n["value"]) for n in artist.get("names", [])} | {key(artist["defaultName"])}:
                        matched.add(artist_id)
        ident = next(iter(matched)) if len(matched) == 1 else None
        if not eligible(page, entity, title in category, ident is not None):
            continue
        wiki = WIKI + quote(title.replace(" ", "_"))
        if ident is not None:
            artist, checked = candidates[ident]
            row = parse_artist(artist, "utaitedb", "ut", "Utaite", checked)
            if row is None:
                ident = None
            elif ident in seen_provider:
                raise ValueError(f"Two wiki pages match the same singer: {title}, {seen_provider[ident]}")
            else:
                seen_provider[ident] = title
        if ident is None:
            if title not in category:
                continue
            row = {"id": f"ut:jawiki:{page['pageid']}", "name": re.sub(r" \([^()]+\)$", "", title),
                   "directory": "jawiki", "aliases": [], "sources": [], "avatar_url": None, "source_url": None,
                   "original_url": None, "license": None, "image_author": None, "verified_at": None}
        previous_row = old.get(row["id"], {})
        for field in ("birthday", "birthday_source", "roles"):
            if field in previous_row:
                row[field] = previous_row[field]
        if not row.get("avatar_url") and len(files := claims(entity, "P18")) == 1:
            row.update(commons_portrait(client, files[0]))
        row["aliases"] = list(dict.fromkeys([*previous_row.get("aliases", []), *row["aliases"],
                                            re.sub(r" \([^()]+\)$", "", title)]))
        row["wiki_url"] = wiki
        row["wikidata_id"] = qid
        japanese_claim = any(c["id"] == "Q17" for c in claims(entity, "P27"))
        row["japan_source"] = "https://www.wikidata.org/wiki/" + qid + "#P27" if japanese_claim else wiki
        row["sources"].extend("https://x.com/" + acc for acc in claims(entity, "P2002") if re.fullmatch(r"[A-Za-z0-9_]{1,15}", acc))
        if key(row["name"]) == key(re.sub(r" \([^()]+\)$", "", title)):
            # This also lets an existing VocaDB composer keep their original ID
            # when the Japanese singer page links to the composer's account.
            row["wiki_accounts"] = list(wiki_links.values())
        row["sources"] = list(dict.fromkeys([wiki, *row["sources"], *previous_row.get("sources", []),
                                            *([row["source_url"]] if row.get("source_url") else [])]))
        records[row["id"]] = row

    redirects = dict(previous.get("redirects", {}))
    # A Wiki-only profile may acquire a verifiable database link on a later run.
    for row in records.values():
        if row["id"].startswith("ut:utaitedb:"):
            wiki_id = "ut:jawiki:" + str(pages[unquote(row["wiki_url"].split("/wiki/")[1]).replace("_", " ")]["pageid"])
            if wiki_id in old or wiki_id in redirects or wiki_id in archived:
                redirects[wiki_id] = row["id"]
    community = add_community_singers(client, candidates, records, previous, redirects, parse_artist)
    for ident, row in old.items():
        if ident not in records and ident not in redirects:
            archived[ident] = {"name": row["name"], "aliases": row.get("aliases", [])}
    archived = {i: r for i, r in archived.items() if i not in records and i not in redirects}
    return {**{k: previous[k] for k in ("schema", "kind", "overrides", "manual_records")},
            "records": sorted(records.values(), key=lambda r: r["id"]), "legacy_names": archived,
            "removed_ids": sorted(set(previous.get("removed_ids", [])) - set(records) - set(archived) - set(redirects)),
            "redirects": redirects,
            "coverage": {"wiki_categories": "Category:歌い手", "category_articles": len(category),
                         "directory_entries_scanned": len(candidates), "wiki_articles_checked": len(pages), **community}}
