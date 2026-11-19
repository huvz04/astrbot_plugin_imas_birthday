"""Discover individual singers in Japanese community wikis and verify their origin."""
from collections import defaultdict
from dataclasses import dataclass, field
import json
import re
from urllib.parse import quote, unquote, urlencode, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from catalogue_sources import snapshot
from sync_japanese_utaite import accounts, batches, claims, key

NICO = "https://dic.nicovideo.jp"
SEESAA = "https://seesaawiki.jp/utaite/"
NICO_LIST = NICO + "/a/" + quote("歌い手一覧")
ORIGIN_DIRECTORY = "https://note.com/utaitematome/n/nb0eec98f2b6c"
OFFICIAL_HOMETOWNS = {"halyosy": ("twitter:halyosy", "https://halyosy.com/about")}
WIKIDATA = "https://www.wikidata.org"
PREFECTURES = re.compile(r"^(?:日本[・、 ,]*)?(北海道|東京都|京都府|大阪府|(?:青森|岩手|宮城|秋田|山形|福島|茨城|栃木|群馬|埼玉|千葉|神奈川|新潟|富山|石川|福井|山梨|長野|岐阜|静岡|愛知|三重|滋賀|兵庫|奈良|和歌山|鳥取|島根|岡山|広島|山口|徳島|香川|愛媛|高知|福岡|佐賀|長崎|熊本|大分|宮崎|鹿児島|沖縄)県)")
FOREIGN = re.compile(r"中国(?:籍|人|出身|生まれ)|台湾(?:人|出身|生まれ)|韓国(?:人|出身|生まれ)|香港(?:人|出身|生まれ)|外国籍")
FOREIGN_PLACE = re.compile(r"^(?:中国(?!地方|山地)|中華人民共和国|台湾|香港|韓国|大韓民国|米国|アメリカ|カナダ|イギリス|フランス|海外)")
VIRTUAL = re.compile(r"VTuber|バーチャル(?:YouTuber|ユーチューバー|ライバー|シンガー)", re.I)


@dataclass
class Entry:
    name: str
    url: str
    site: str
    listed: bool
    providers: set = field(default_factory=set)


def singer_key(name):
    return key(re.sub(r"\s*[（(](?:歌い手|歌手|生放送主|ニコニコ動画)[)）]$", "", name, flags=re.I))


def nico_key(url):
    p = urlsplit(url)
    if p.scheme not in {"http", "https"} or p.hostname not in {"dic.nicovideo.jp", "dic.nicomoba.jp"}:
        return ""
    if re.fullmatch(r"/id/[1-9]\d*/?", p.path):
        return "id:" + p.path.split("/")[2]
    if p.path.startswith("/a/"):
        return "word:" + unquote(p.path[3:].rstrip("/")).casefold()
    return ""


def read_index(client):
    raw, _ = snapshot(client, NICO_LIST, max_age_days=7, interval=5)
    soup = BeautifulSoup(raw, "html.parser")
    entries = {}
    sections = set()
    for h in soup.select("#article h2[id]"):
        label = h.get_text(strip=True)
        if label not in {"男性歌い手", "女性歌い手"} and not label.startswith("性別不明"):
            continue
        sections.add(label)
        for sibling in h.find_next_siblings():
            if sibling.name == "h2":
                break
            for li in sibling.select("li"):
                a = li.find("a", href=True, recursive=False)
                if not a or "non" in a.get("class", []):
                    continue
                url = urljoin(NICO, a["href"])
                if nico_key(url):
                    entries.setdefault(nico_key(url), Entry(a.get_text(strip=True), url, "nico", True))
    if not {"男性歌い手", "女性歌い手"} <= sections or not entries:
        raise ValueError("NicoNicoPedia singer directory structure changed")

    raw, _ = snapshot(client, SEESAA, max_age_days=7, interval=1)
    soup = BeautifulSoup(raw, "html.parser")
    indexes = {a["href"] for a in soup.select("a[href]")
               if a["href"].startswith(SEESAA + "d/") and re.fullmatch(r".行（(?:男性|女性)）", a.get_text(strip=True))}
    if len(indexes) != 20:
        raise ValueError("Incomplete Seesaa kana index")
    for url in sorted(indexes):
        raw, _ = snapshot(client, url, max_age_days=7, interval=1)
        body = BeautifulSoup(raw, "html.parser").select_one("#page-body .user-area")
        if body is None:
            raise ValueError("Missing Seesaa directory body: " + url)
        for a in body.select("a[href]"):
            link = a["href"]
            if link.startswith(SEESAA + "d/") and link not in indexes and a.get_text(strip=True):
                entries.setdefault(link, Entry(a.get_text(strip=True), link, "seesaa", True))
    return entries


def origins_by_account(client):
    """Published hometown and the performer's own linked account, in the same row."""
    raw, _ = snapshot(client, ORIGIN_DIRECTORY, max_age_days=7)
    soup = BeautifulSoup(raw, "html.parser")
    result = defaultdict(set)
    headings = soup.select("article h3[id]")
    if not headings:
        raise ValueError("Missing singer profile headings in origin directory")
    for h in headings:
        if not h.get_text(strip=True).startswith(("★", "☆")):
            continue
        p = h.find_next_sibling()
        if p is None or p.name != "p":
            continue
        fields = p.get_text(" ", strip=True).split("【出身地】", 1)
        if len(fields) != 2:
            continue
        origin = fields[1].split("【", 1)[0].strip()
        if not PREFECTURES.match(origin) or FOREIGN.search(origin) or any(x in origin for x in ("?", "？", "推測", "不明", "説")):
            continue
        url = ORIGIN_DIRECTORY + "#" + h["id"]
        name = re.split(r"[（(［【]", h.get_text(strip=True)[1:], maxsplit=1)[0].strip()
        if not name:
            continue
        # When a profile names an X account, do not let an embedded video of a
        # collaborator substitute for that account.
        nodes = [p] if re.search(r"【(?:X|Twitter)】", p.get_text()) else [p, *p.find_next_siblings(limit=2)]
        for node in nodes:
            if node is not p and node.name != "figure":
                break
            for link in [node.get("data-src", ""), *(a["href"] for a in node.select("a[href]"))]:
                account = accounts(link)
                if account:
                    result[account].add((singer_key(name), url))
    for name, (token, url) in OFFICIAL_HOMETOWNS.items():
        raw, _ = snapshot(client, url, max_age_days=30)
        page = BeautifulSoup(raw, "html.parser")
        linked_accounts = {accounts(a["href"]) for a in page.select("a[href]")}
        hometown = re.search(r"出身地[：:]\s*(\S+)", page.get_text(" ", strip=True))
        if token in linked_accounts and hometown and PREFECTURES.match(hometown[1]):
            result[token] = {(singer_key(name), url)}
    return result


def personal_accounts(artist):
    return {a for l in artist.get("webLinks", []) if l.get("category") == "Official" and not l.get("disabled")
            if (a := accounts(l.get("url", "")))}


def wikidata_humans(client, lookups):
    """Find candidate entities by their exact statements, then check their claims."""
    identifiers = set()
    def fetch(prop, values):
        statement = "|".join(f"{prop}={value}" for value in values)
        params = {"action": "query", "format": "json", "list": "search", "srnamespace": 0, "srlimit": 500,
                  "srprop": "", "srsearch": f'haswbstatement:"{statement}"'}
        while True:
            raw, _ = snapshot(client, WIKIDATA + "/w/api.php?" + urlencode(params), max_age_days=30, interval=1)
            doc = json.loads(raw)
            identifiers.update(r["title"] for r in doc["query"]["search"] if re.fullmatch(r"Q[1-9]\d*", r["title"]))
            if "continue" not in doc:
                return
            params.update(doc["continue"])
    for prop, values in sorted(lookups.items()):
        batch, size = [], 0
        for value in sorted(values):
            if not value or any(ch in value for ch in ('|', '"', '\\')) or not value.isprintable():
                continue
            length = len(value.encode()) + len(prop) + 2
            if batch and (size + length > 1250 or len(batch) >= 65):
                fetch(prop, batch)
                batch, size = [], 0
            batch.append(value)
            size += length
        if batch:
            fetch(prop, batch)
    return wikidata_entities(client, identifiers, japanese_titles=True)


def wikidata_entities(client, ids, *, japanese_titles=False):
    result = {}
    for group in batches(ids, 40):
        params = {"action": "wbgetentities", "format": "json", "ids": "|".join(group), "props": "claims"}
        if japanese_titles:
            params.update(props="claims|sitelinks", sitefilter="jawiki")
        raw, _ = snapshot(client, WIKIDATA + "/w/api.php?" + urlencode(params), max_age_days=30)
        result.update(json.loads(raw)["entities"])
    return result


def origin_of_human(entity, places):
    countries = {v.get("id") for v in claims(entity, "P27")}
    if countries:
        if countries == {"Q17"}:
            return WIKIDATA + "/wiki/" + entity["id"] + "#P27", "citizenship"
        return "", "foreign"
    born = [v["id"] for v in claims(entity, "P19")]
    if len(born) == 1:
        country = {v["id"] for v in claims(places.get(born[0], {}), "P17")}
        if born == ["Q17"] or country == {"Q17"}:
            return WIKIDATA + "/wiki/" + entity["id"] + "#P19", "birthplace"
        if country and "Q17" not in country:
            return "", "foreign"
    return "", ""


def inspect_profile(client, entry):
    """Read the public article. Skip genuinely missing pages; propagate service failures."""
    url = entry.url
    if entry.site == "nico" and nico_key(url).startswith("id:"):
        # The public word-article route is crawlable. Check the numeric ID before using it.
        url = NICO + "/a/" + quote(entry.name, safe="")
    try:
        raw, _ = snapshot(client, url, max_age_days=30, interval=5 if entry.site == "nico" else 1)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (404, 410):
            return None
        raise
    soup = BeautifulSoup(raw, "html.parser")
    if entry.site == "nico":
        body = soup.select_one("#article body")
        header = soup.select_one("meta[property='og:url']")
        description = soup.select_one("meta[property='og:description']")
        if not body or not header or not description:
            return None
        ident = nico_key(header.get("content", ""))
        if not ident.startswith("id:") or (nico_key(entry.url).startswith("id:") and ident != nico_key(entry.url)):
            return None
        pid = "ut:nicopedia:" + ident[3:]
        lead = description.get("content", "").split("。", 1)[0]
    else:
        body = soup.select_one("#page-body .user-area")
        header = soup.select_one("#page-header h2")
        ident = re.search(r"rainman\.articleCount\((\d+)\)", raw)
        if not body or not header or not ident or singer_key(header.get_text(strip=True)) != singer_key(entry.name):
            return None
        pid = "ut:seesaa:" + ident[1]
        lead = body.get_text(" ", strip=True)[:150].split("。", 1)[0]
    if (VIRTUAL.search(lead) or re.search(r"歌い手(?:ユニット|グループ)|(?:二人|[2-9２-９]人)組(?:の|で)", lead)):
        return None
    if entry.site == "nico" and (not entry.listed or not entry.providers):
        performer = re.compile(r"歌い手|歌ってみた|歌手|(?:ボー|ヴォー)カリスト|シンガー")
        if not performer.search(lead):
            own_work = re.compile(r"本人(?:による|が|自ら).{0,12}歌ってみた|歌ってみた(?:動画)?を(?:中心|投稿|アップ)"
                                  r"|自ら歌うことがある|(?:男性|女性)歌い手である")
            if not any(own_work.search(p.get_text("", strip=True)) for p in body.select("p, li")):
                return None
    tokens = set()
    for item in body.select("a[href], iframe[src]"):
        link = item.get("href") or item.get("src", "")
        if token := accounts(link):
            tokens.add(token)
        match = re.fullmatch(r"https?://ext\.nicovideo\.jp/thumb_mylist/(\d+)(?:\?.*)?", link)
        if match:
            tokens.add("niconico:mylist/" + match[1])
    country = ""
    if FOREIGN.search(lead):
        country = "foreign"
    elif re.search(r"(?:とは|は)[、 ]*(?:日本の(?:男性|女性)?(?:歌手|歌い手|シンガー)|日本人(?:の歌い手|である))", lead):
        country = "japanese_profile"
    for tr in body.select("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) != 2:
            continue
        label = cells[0].get_text("", strip=True).rstrip("：:")
        if label in {"国籍", "出身地", "出生地"}:
            value = cells[1].get_text("", strip=True).strip()
            if label == "国籍" and value not in ("", "不明", "非公開", "日本", "日本国"):
                return None
            if label in {"出身地", "出生地"} and FOREIGN_PLACE.match(value):
                country = "foreign"
            if country != "foreign" and (value in {"日本", "日本国"} or PREFECTURES.match(value)):
                country = "citizenship" if label == "国籍" else "birthplace"
    return pid, url, tokens, country


def collect_links(entries, candidates):
    """Index the complete directories. Names suggest a lookup but never prove identity."""
    by_name, by_token, provider_keys = defaultdict(set), defaultdict(set), {}
    lookups = defaultdict(set)
    names_in_wiki = {singer_key(e.name) for e in entries.values()}
    for ident, (artist, _) in candidates.items():
        names = {singer_key(n["value"]) for n in artist.get("names", [])} | {singer_key(artist["defaultName"])}
        keys, explicit = set(), False
        for link in artist.get("webLinks", []):
            if link.get("disabled"):
                continue
            url = link.get("url", "")
            if nk := nico_key(url):
                if nk not in entries:
                    path = urlsplit(url).path
                    name = unquote(path[3:]) if nk.startswith("word:") else artist["defaultName"]
                    entries[nk] = Entry(name, NICO + path, "nico", False)
                entries[nk].providers.add(ident)
                explicit = True
            if url in entries and entries[url].site == "seesaa":
                entries[url].providers.add(ident)
                explicit = True
            token = accounts(url)
            if token and link.get("category") == "Official":
                by_token[token].add(ident)
                if token.startswith("twitter:"):
                    keys.add(("P2002", urlsplit(url).path.strip("/")))
            mb = re.fullmatch(r"https?://(?:www\.)?musicbrainz\.org/artist/([\da-f-]{36})/?", url, re.I)
            if mb:
                keys.add(("P434", mb[1].lower()))
        for name in names:
            by_name[name].add(ident)
        if explicit or names & names_in_wiki:
            provider_keys[ident] = keys
            for prop, value in keys:
                lookups[prop].add(value)
    for entry in entries.values():
        if entry.site != "nico":
            continue
        for prop, value in article_keys(entry):
            lookups[prop].add(value)
    return by_name, by_token, provider_keys, lookups


def article_keys(entry):
    if entry.site != "nico":
        return []
    key = nico_key(entry.url)
    if key.startswith("id:"):
        return [("P14983", key[3:])]
    title = unquote(urlsplit(entry.url).path[3:].rstrip("/"))
    # The encyclopedia normalizes its word URLs to lower case. Wikidata stores
    # both those keys and the original stage-name casing.
    return [("P6900", v) for v in {title, title.casefold()}]


def geography(client, lookups):
    humans = {q: h for q, h in wikidata_humans(client, lookups).items()
              if any(v["id"] == "Q5" for v in claims(h, "P31"))}
    lookup = defaultdict(set)
    for qid, human in humans.items():
        if title := human.get("sitelinks", {}).get("jawiki", {}).get("title"):
            lookup["jawiki", title].add(qid)
        for prop in lookups:
            for value in claims(human, prop):
                if isinstance(value, str):
                    lookup[prop, value.casefold()].add(qid)
    places = wikidata_entities(client, {v["id"] for h in humans.values() if not claims(h, "P27") for v in claims(h, "P19")})
    return lookup, {qid: origin_of_human(h, places) for qid, h in humans.items()}

def lookup_people(keys, wikidata):
    return {q for prop, value in keys for q in wikidata.get((prop, value.casefold()), ())}


def matching_hometowns(names, tokens, origins):
    return {source for token in tokens for name, source in origins.get(token, ()) if name in names}


def community_profiles(client, entries, candidates, indexes, wikidata, countries, origins):
    by_name, by_account, provider_keys, _ = indexes
    origin_names = {name for values in origins.values() for name, _ in values}
    provider_countries = {ident: lookup_people(keys, wikidata) for ident, keys in provider_keys.items()}
    for entry in sorted(entries.values(), key=lambda e: (not e.listed, e.site, e.url)):
        name = singer_key(entry.name)
        plausible = by_name.get(name, set()) | entry.providers
        article_people = lookup_people(article_keys(entry), wikidata)
        potential_people = article_people | {q for ident in plausible for q in provider_countries.get(ident, ())}
        # Seesaa profiles are compact. In the larger encyclopedia, prioritize people
        # cross-referenced by an origin source or a statement about a specific human.
        if (entry.site == "nico" and name not in origin_names
                and not any(countries[q][0] for q in potential_people)):
            continue
        profile = inspect_profile(client, entry)
        if not profile:
            continue
        wiki_id, url, tokens, wiki_origin = profile
        if wiki_origin == "foreign":
            continue
        if entry.site == "nico":
            article_people.update(lookup_people([("P14983", wiki_id.rsplit(":", 1)[1])], wikidata))
        candidates_by_account = {ident for token in tokens for ident in by_account.get(token, ())}
        matched = entry.providers | (candidates_by_account & plausible)
        matched.update(i for i in plausible if article_people & provider_countries.get(i, set()))
        if len(matched) > 1:
            continue
        ident = next(iter(matched), None)
        qids = article_people.copy()
        names, verified_tokens = {name}, set(tokens)
        if ident is not None:
            artist = candidates[ident][0]
            qids.update(provider_countries.get(ident, ()))
            names.update(singer_key(n["value"]) for n in artist.get("names", []))
            verified_tokens = personal_accounts(artist)
            virtual_tag = any(tag.get("tag", {}).get("urlSlug") in {"vtuber", "virtual-youtuber"}
                              for tag in artist.get("tags", []))
            # A separately documented real singer can have a virtual side project.
            if virtual_tag and not (qids and entry.listed and wiki_id.startswith("ut:nicopedia:")):
                continue
        if len(qids) > 1 or any(countries[q][1] == "foreign" for q in qids):
            continue
        qid = next(iter(qids), None)
        source, basis = countries.get(qid, ("", ""))
        hometowns = matching_hometowns(names, verified_tokens, origins)
        if not source and wiki_origin:
            source, basis = url, wiki_origin
        if not source and len(hometowns) == 1:
            source, basis = next(iter(hometowns)), "birthplace"
        if source:
            yield entry, wiki_id, url, ident, qid, tokens, source, basis


def add_community_singers(client, candidates, records, previous, redirects, parse_artist):
    entries = read_index(client)
    counts = {"nicopedia_listed": sum(e.site == "nico" for e in entries.values()),
              "seesaa_listed": sum(e.site == "seesaa" for e in entries.values())}
    indexes = collect_links(entries, candidates)
    lookup, countries = geography(client, indexes[-1])
    origins = origins_by_account(client)
    human_sources = defaultdict(set)
    for (prop, value), people in lookup.items():
        if len(people) != 1:
            continue
        person = next(iter(people))
        if prop == "jawiki":
            human_sources[person].add("https://ja.wikipedia.org/wiki/" + quote(value.replace(" ", "_")))
        elif prop == "P2002" and re.fullmatch(r"[a-z0-9_]{1,15}", value):
            human_sources[person].add("https://x.com/" + value)
    old = {r["id"]: r for r in previous["records"]}
    past = {**previous.get("legacy_names", {}), **old}
    known = set(past) | set(previous.get("removed_ids", [])) | set(redirects)
    past_links = defaultdict(set)
    for row in old.values():
        for value in (row["id"], row.get("wiki_url"), row.get("wikidata_id"), row.get("japan_source"), row.get("utaitedb_id")):
            if value:
                past_links[value].add(row["id"])
        for url in row.get("sources", []):
            if nico_key(url) or url.startswith(SEESAA + "d/"):
                past_links[url].add(row["id"])
    by_qid = {r["wikidata_id"]: r["id"] for r in records.values() if r.get("wikidata_id")}
    by_origin = {}
    by_wiki = {}
    by_provider = {}
    named_accounts = defaultdict(set)
    for row in records.values():
        for link in row["sources"]:
            if token := accounts(link):
                for name in (row["name"], *row["aliases"]):
                    named_accounts[singer_key(name), token].add(row["id"])
    for entry, wiki_id, url, ident, qid, tokens, source, basis in community_profiles(
            client, entries, candidates, indexes, lookup, countries, origins):
        provider_id = f"ut:utaitedb:{ident}" if ident is not None else None
        # One performer can appear in all three wikis. Only join on a stable
        # article, official provider ID, human entity or specific hometown profile.
        matched = {i for i in (provider_id, wiki_id, by_provider.get(provider_id), by_qid.get(qid), by_wiki.get(wiki_id), by_origin.get(source))
                   if i in records}
        matched.update(i for token in tokens for i in named_accounts.get((singer_key(entry.name), token), ()))
        if len(matched) > 1:
            raise ValueError(f"Conflicting singer identities at {url}: {sorted(matched)}")
        existing = next(iter(matched), None)
        if provider_id:
            artist, checked = candidates[ident]
            provider = parse_artist(artist, "utaitedb", "ut", "Utaite", checked)
            if not provider:
                continue
        else:
            provider = None
        prior = {i for value in (provider_id, wiki_id, url, source, qid) for i in past_links.get(value, ())}
        if len(prior) > 1:
            raise ValueError(f"Multiple prior registrations match {url}: {sorted(prior)}")
        previous_id = next(iter(prior), None)
        person_id = existing or previous_id or provider_id or wiki_id
        if existing:
            row = records[existing]
            if qid and row.get("wikidata_id") and row["wikidata_id"] != qid:
                continue
            if provider and provider["id"] != existing:
                if not row.get("avatar_url") and provider.get("avatar_url"):
                    for k in ("avatar_url", "original_url", "source_url", "verified_at", "image_author", "license"):
                        row[k] = provider[k]
                row["aliases"] = list(dict.fromkeys([*row["aliases"], *provider["aliases"]]))
                row["sources"] = list(dict.fromkeys([*row["sources"], *provider["sources"]]))
        else:
            row = provider or {"id": person_id, "name": re.sub(r"\s*[（(]歌い手[)）]$", "", entry.name),
                               "directory": wiki_id.split(":")[1], "aliases": [], "sources": [], "avatar_url": None,
                               "source_url": None, "original_url": None, "license": None, "image_author": None,
                               "verified_at": None}
            row.update(id=person_id, wiki_url=url, wikidata_id=qid, japan_source=source, japan_basis=basis)
            former = past.get(person_id, {})
            row["aliases"] = list(dict.fromkeys([*former.get("aliases", []), *row["aliases"]]))
            for key in ("birthday", "birthday_source", "roles"):
                if key in former:
                    row[key] = former[key]
            records[person_id] = row
        row["sources"] = list(dict.fromkeys([*row["sources"], url, source, *sorted(human_sources[qid])]))
        alias = re.sub(r"\s*[（(]歌い手[)）]$", "", entry.name)
        if alias not in row["aliases"]:
            row["aliases"].append(alias)
        if qid:
            row["wikidata_id"] = qid
            by_qid[qid] = person_id
        by_wiki[wiki_id] = person_id
        by_origin[source] = person_id
        if provider_id:
            by_provider[provider_id] = person_id
            if provider_id != person_id:
                row["utaitedb_id"] = provider_id
        for former in (wiki_id, provider_id, previous_id):
            if former in known and former != person_id:
                redirects[former] = person_id
    return counts
