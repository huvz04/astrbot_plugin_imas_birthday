"""Discover music people from complete public Moegirl category pagination.

Unlike enrich_music_people, this imports new identities. No top-N ranking or
external-database membership is required. Public pages/API props only; failures
abort publication rather than silently declaring an incomplete scan complete.
"""
from __future__ import annotations

import argparse
import ast
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit

from bs4 import BeautifulSoup, NavigableString
import httpx

from enrich_music_people import BASE, UA, ROOT, key, text

ROOT_CATEGORIES = ("网络歌手", "歌手", "音乐人", "作曲家", "作词家", "编曲家")
CREATOR_CATEGORY = re.compile(r"职人|音乐人|作曲家|作词家|编曲家|曲师|制作人|钢琴家|吉他手|贝斯手|鼓手")
NON_PERSON_CATEGORY = re.compile(r"组合|乐队|乐团|社团|偶像团体|企业|公司|音乐游戏角色|登场人物|登场角色|虚构人物|虚拟歌手")
VT_CATEGORY = re.compile(r"虚拟(?:UP主|主播|YouTuber|歌手)|VTuber", re.I)
FILES = {"utaite": "utaite.json", "music_creator": "music_creators.json"}
PUBLIC_HOSTS = {"zh.moegirl.org.cn"}


class PublicSource:
    def __init__(self, cache, interval=.7, refresh=False):
        self.cache, self.interval, self.refresh = Path(cache), interval, refresh
        self.cache.mkdir(parents=True, exist_ok=True)
        self.client = httpx.Client(timeout=35, follow_redirects=True, headers={"User-Agent": UA})

    def read(self, url):
        if urlsplit(url).hostname not in PUBLIC_HOSTS:
            raise ValueError("Unexpected public source host")
        path = self.cache / (hashlib.sha256(url.encode()).hexdigest() + ".txt")
        if path.exists() and not self.refresh:
            value = path.read_text(encoding="utf-8")
            if "/api.php" in url or BeautifulSoup(value, 'html.parser').select_one('#mw-pages, #mw-subcategories, .mw-parser-output'):
                return value
            raise ValueError("Cached response has no category content")
        response = self.client.get(url)
        response.raise_for_status()
        if urlsplit(str(response.url)).hostname not in PUBLIC_HOSTS:
            raise ValueError("Unexpected public source redirect")
        if 'Special:MoeAuth' in str(response.url) or '/__verify' in str(response.url):
            raise ValueError('Public page requires browser login or verification')
        response.encoding = "utf-8"
        value = response.text
        if "/api.php" in url:
            if "error" in json.loads(value):
                raise ValueError("Public query unavailable: " + str(json.loads(value)["error"]))
        elif not BeautifulSoup(value, 'html.parser').select_one('#mw-pages, #mw-subcategories, .mw-parser-output'):
            raise ValueError("No public wiki content; stopping at access restriction")
        path.write_text(value, encoding="utf-8")
        time.sleep(self.interval)
        return value


def category_page(html, url):
    soup = BeautifulSoup(html, "html.parser")
    pages = {a["title"] for a in soup.select('#mw-pages .mw-category a[title]')}
    children = {a["title"].removeprefix("Category:") for a in soup.select('#mw-subcategories a[title^="Category:"]')}
    next_links = {urljoin(url, a["href"]).split("#")[0]
                  for a in soup.select('#mw-pages a[href], #mw-subcategories a[href]')
                  if text(a) in {"下一页", "下頁", "下一頁", "next page"}}
    if any(urlsplit(link).hostname != urlsplit(url).hostname for link in next_links):
        raise ValueError("Unexpected category continuation")
    summary = text(soup.select_one('#mw-pages > p'))
    match = re.search(r"共(?:含有)?\s*([\d,]+)\s*个页面", summary)
    expected = int(match[1].replace(",", "")) if match else None
    return pages, children, sorted(next_links), expected


def discover(source, base=BASE):
    queue, seen, memberships, audit = deque(ROOT_CATEGORIES), set(), {}, {}
    while queue:
        category = queue.popleft()
        if category in seen or NON_PERSON_CATEGORY.search(category):
            continue
        seen.add(category)
        urls = deque([base + "/Category:" + quote(category)])
        visited, titles, children, expected, errors = set(), set(), set(), None, []
        while urls:
            url = urls.popleft()
            if url in visited:
                continue
            visited.add(url)
            try:
                found, subcats, more, count = category_page(source.read(url), url)
            except (httpx.HTTPError, ValueError) as exc:
                errors.append({"url": url, "error": str(exc)[:200]})
                continue
            titles.update(found); children.update(subcats)
            if count is not None:
                expected = max(expected or 0, count)
            urls.extend(link for link in more if link not in visited)
        audit[category] = {"pages": len(visited), "discovered": len(titles), "reported_total": expected,
                           "source": base + "/Category:" + quote(category), "errors": errors,
                           "complete": not errors and (expected is None or len(titles) >= expected)}
        for title in titles:
            memberships.setdefault(title, set()).add(category)
        queue.extend(sorted(children - seen))
        print(f"Category {category}: {len(titles)} entries, {len(visited)} pages", flush=True)
    return memberships, audit


def query_pages(source, titles):
    pages, references = {}, {}
    ordered = sorted(set(titles))
    for start in range(0, len(ordered), 50):
        params = {"action": "query", "prop": "pageimages|categories|info|redirects|pageprops",
                  "titles": "|".join(ordered[start:start + 50]), "redirects": 1,
                  "piprop": "original", "cllimit": "max", "rdlimit": "max", "format": "json"}
        continuation, visited = {}, set()
        while True:
            url = str(httpx.URL(BASE + "/api.php", params={**params, **continuation}))
            if url in visited:
                raise ValueError("Repeated API continuation")
            visited.add(url)
            data = json.loads(source.read(url))
            if "query" not in data:
                raise ValueError("Missing public query result")
            query = data["query"]
            references.update({r["from"]: r["to"] for field in ("normalized", "redirects") for r in query.get(field, [])})
            for page in query.get("pages", {}).values():
                title = page["title"]
                previous = pages.get(title, {})
                pages[title] = {**previous, **page}
                for field in ("categories", "redirects"):
                    values = {r["title"]: r for r in [*previous.get(field, []), *page.get(field, [])]}
                    pages[title][field] = list(values.values())
            continuation = data.get("continue")
            if not continuation:
                break
        print(f"Public profiles: {min(start + 50, len(ordered))}/{len(ordered)}", flush=True)
    for page in pages.values():
        aliases = {page["title"], *(r["title"] for r in page.get("redirects", []) if r.get("ns") == 0)}
        for origin in references:
            current, visited = origin, set()
            while current in references and current not in visited:
                visited.add(current); current = references[current]
            if current == page["title"]:
                aliases.add(origin)
        page["aliases"] = sorted(aliases)
    return pages


def merge_browser_categories(memberships, audit, observations):
    """Import visible category lists exported after the user signs in, never cookies."""
    for category, pages in observations.items():
        names, cursors, totals = set(), set(), set()
        for page in pages:
            if page.get('title', '').strip() not in {'Category:' + category, '分类:' + category}:
                raise ValueError('Browser observation category mismatch')
            names.update(page['names'])
            match = re.search(r'共(?:含有)?\s*(\d+)\s*个页面', page['summary'])
            if not match:
                raise ValueError('Missing browser category total')
            totals.add(int(match[1]))
            if page.get('next'):
                parts = urlsplit(urljoin(BASE, page['next']))
                if parts.hostname != 'zh.moegirl.org.cn':
                    raise ValueError('Unexpected browser category continuation host')
                params = parse_qs(parts.query)
                if params.get('title') != ['Category:' + category]:
                    raise ValueError('Browser category continuation mismatch')
                cursors.update(params.get('pagefrom', []))
        # The current site omits some boundary entries from the next page;
        # retain these explicitly linked titles and revalidate via the public API.
        boundary = cursors - names
        names.update(cursors)
        if len(totals) != 1 or len(names) != next(iter(totals)) or pages[-1].get('next'):
            raise ValueError(f'Incomplete browser category: {category}, {len(names)}, {totals}')
        for title in names:
            memberships.setdefault(title, set()).add(category)
        audit[category] = {'source': BASE + '/Category:' + quote(category), 'read_via': 'authenticated_browser',
                           'pages': len(pages), 'discovered': len(names), 'reported_total': len(names),
                           'boundary_titles': sorted(boundary), 'errors': [], 'complete': True}


def profile_metadata(page):
    cats = [c["title"].removeprefix("Category:") for c in page.get("categories", [])]
    dates = {tuple(map(int, m.groups())) for c in cats if (m := re.fullmatch(r"(\d{1,2})月(\d{1,2})日", c))}
    birthday = ""
    if len(dates) == 1:
        month, day = next(iter(dates))
        try:
            datetime(2000, month, day); birthday = f"{month:02d}-{day:02d}"
        except ValueError:
            pass
    picture = page.get("original", {})
    image = picture.get("source", "")
    if urlsplit(image).hostname != "storage.moegirl.org.cn" or min(picture.get("width", 0), picture.get("height", 0)) < 100:
        image = ""
    if image:
        image = image.split("!/")[0] + "!/fw/640"
    return cats, birthday, image


def music_profile_roles(html):
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one('.mw-parser-output')
    if not content:
        raise ValueError("Missing public profile content")
    # Inspect identity/biography only, not lyrics, discographies or collaborator credits.
    for node in content.select('script, style, .navbox, .toc'):
        node.decompose()
    intro = []
    for node in content.descendants:
        if getattr(node, "name", "") == "h2":
            break
        if isinstance(node, NavigableString):
            intro.append(str(node))
    introduction = ' '.join(intro)
    roles = set()
    if re.search(r"唱见|歌い手|网络歌手|網絡歌手|同人(?:音[乐樂])?歌手|同人(?:音[乐樂])?歌姬|同人音[乐樂].{0,12}(?:歌手|演唱|翻唱)|(?:歌手|演唱|翻唱).{0,12}同人音[乐樂]", introduction):
        roles.add('utaite')
    if re.search(r'(?:是|为)[^。]{0,100}(?:作曲家|作词家|编曲人|编曲家|音乐制作人)', introduction):
        roles.add('music_creator')
    for row in content.select('tr'):
        cells = row.find_all(['th', 'td'], recursive=False)
        if len(cells) == 2 and text(cells[0]) in {'职业', '職業'} and re.search('作曲|作词|编曲|音乐制作', text(cells[1])):
            roles.add('music_creator')
    return roles


def online_singer_profile(html):
    return 'utaite' in music_profile_roles(html)


def voice_actor_aliases():
    names = set()
    for filename in ('seiyuu_catalogue.py', 'voice_actor_links.py', 'voice_actor_names.py'):
        tree = ast.parse((ROOT / filename).read_text(encoding='utf-8'))
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
                for row in ast.literal_eval(node.value).values():
                    if isinstance(row, dict):
                        names.update(key(n) for n in [row.get('name', ''), *row.get('aliases', [])] if n)
    return names


def merge_pages(payloads, pages, memberships, *, source=None, actor_aliases=frozenset(), reviewed_profiles=None):
    rows = {r["id"]: r for p in payloads.values() for r in [*p["records"], *p.get("manual_records", [])]}
    source_index = {}
    for ident, row in rows.items():
        moe = row.get("moegirl", {})
        for title in [moe.get("title", ""), unquote(urlsplit(moe.get("url", "")).path.strip("/"))]:
            if title:
                source_index.setdefault(key(title.replace("_", " ")), set()).add(ident)
    today = datetime.now(timezone.utc).date().isoformat()
    audit = {"added": [], "updated": [], "excluded": [], "conflicts": [], "profile_errors": []}
    for number, (title, page) in enumerate(sorted(pages.items()), 1):
        if number % 100 == 0:
            print(f"Reviewed identities: {number}/{len(pages)}", flush=True)
        cats, birthday, image = profile_metadata(page)
        matches = {ident for alias in page["aliases"] for ident in source_index.get(key(alias), [])}
        if len(matches) > 1:
            audit["conflicts"].append({"title": title, "ids": sorted(matches)}); continue
        ident = next(iter(matches), "")
        old = rows.get(ident, {})
        verified = bool(old.get("music_identity_source"))
        reason = ("not_public_person_page" if page.get("ns") != 0 or "missing" in page or re.search(r'(?:列表|一览|名录)$', title) else
                  "disambiguation" if "disambiguation" in page.get("pageprops", {}) else
                  "vtuber" if not verified and any(VT_CATEGORY.search(c) for c in cats) else
                  "group_or_fictional" if any(NON_PERSON_CATEGORY.search(c) for c in cats) else
                  "no_portrait" if not old and not image else "")
        if reason:
            audit["excluded"].append({"title": title, "reason": reason}); continue
        roles = set(old.get("roles", []))
        is_creator = any(CREATOR_CATEGORY.search(c) for c in cats)
        is_singer = any("网络歌手" in c or "唱见" in c for c in cats)
        if not old and any(key(alias) in actor_aliases for alias in page['aliases']):
            audit['excluded'].append({'title': title, 'reason': 'already_in_voice_actor_catalogue'}); continue
        if not old and not is_singer and not is_creator and any('歌手' in c for c in cats):
            reviewed = (reviewed_profiles or {}).get(title)
            if reviewed:
                if (reviewed.get('source') != BASE + '/' + quote(title.replace(' ', '_'))
                        or not set(reviewed.get('roles', [])).issubset({'utaite', 'music_creator'})):
                    raise ValueError('Invalid reviewed profile: ' + title)
                is_singer = 'utaite' in reviewed['roles']
                is_creator = 'music_creator' in reviewed['roles']
            elif source:
                try:
                    profile_roles = music_profile_roles(source.read(BASE + '/' + quote(title.replace(' ', '_'))))
                    is_singer = 'utaite' in profile_roles
                    is_creator = 'music_creator' in profile_roles
                except (httpx.HTTPError, ValueError) as exc:
                    audit['profile_errors'].append({'title': title, 'error': str(exc)[:160]})
            if not is_singer and not is_creator:
                audit['excluded'].append({'title': title, 'reason': 'online_singer_not_verified'}); continue
        if not old and not is_creator and not is_singer:
            audit["excluded"].append({"title": title, "reason": "no_music_identity"}); continue
        if is_creator: roles.add("music_creator")
        if is_singer: roles.add("utaite")
        kind = next((kind for kind, p in payloads.items() if any(r["id"] == ident for r in [*p["records"], *p.get("manual_records", [])])), "")
        kind = kind or ("utaite" if is_singer else "music_creator")
        ident = ident or ("ut" if kind == "utaite" else "mc") + ":moegirl:" + str(page["pageid"])
        source_url = BASE + "/" + quote(title.replace(" ", "_"))
        row = old if old else {"id": ident, "name": title, "directory": "moegirl", "aliases": [], "sources": []}
        row["aliases"] = list(dict.fromkeys([row["name"], *row.get("aliases", []), *page["aliases"]]))
        row["sources"] = list(dict.fromkeys([*row.get("sources", []), source_url]))
        row["roles"] = sorted(roles)
        if birthday:
            row["birthday"], row["birthday_source"] = birthday, source_url
        if image and not row.get("image_url"):
            row["image_url"], row["image_source"] = image, source_url
        row["moegirl"] = {"url": source_url, "title": title, "pageid": page["pageid"], "image_url": image,
                          "revision": page.get("lastrevid"), "retrieved_on": today}
        if not old:
            payloads[kind]["records"].append(row)
        audit["updated" if old else "added"].append({"id": ident, "title": title})
    for kind, payload in payloads.items():
        payload["records"].sort(key=lambda r: r["id"])
        payload["retrieved_on"] = today
        payload["moegirl_only"] = True
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "people")
    parser.add_argument("--cache-dir", type=Path, default=ROOT.parent / "research" / "imas-birthday-tantou" / "public-people" / "moegirl-directory-cache")
    parser.add_argument("--interval", type=float, default=.7)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Publish reviewed data; default writes audit only")
    parser.add_argument("--allow-partial", action="store_true", help="Explicitly permit publication with documented inaccessible category pages")
    parser.add_argument('--browser-categories', type=Path, help='Public category DOM observations exported from an authorized signed-in browser')
    args = parser.parse_args()
    if not .4 <= args.interval <= 30:
        parser.error("--interval must be between 0.4 and 30")
    payloads = {kind: json.loads((args.data_dir / filename).read_text(encoding="utf-8")) for kind, filename in FILES.items()}
    source = PublicSource(args.cache_dir, args.interval, args.refresh)
    try:
        memberships, categories = discover(source)
        if args.browser_categories:
            merge_browser_categories(memberships, categories, json.loads(args.browser_categories.read_text(encoding='utf-8')))
        existing_titles = {r["moegirl"]["title"] for p in payloads.values() for r in [*p["records"], *p.get("manual_records", [])] if r.get("moegirl", {}).get("title")}
        pages = query_pages(source, set(memberships) | existing_titles)
        for category, info in categories.items():
            for title in info.get('boundary_titles', []):
                matched = next((p for p in pages.values() if title in p['aliases']), None)
                if not matched or not any(c['title'] == 'Category:' + category for c in matched.get('categories', [])):
                    raise ValueError('Unverified pagination boundary: ' + title)
        reviews_path = args.data_dir / 'moegirl_reviewed_profiles.json'
        reviews = json.loads(reviews_path.read_text(encoding='utf-8')) if reviews_path.exists() else {}
        report = merge_pages(payloads, pages, memberships, source=source, actor_aliases=voice_actor_aliases(), reviewed_profiles=reviews)
        report.update({"retrieved_on": datetime.now(timezone.utc).isoformat(), "categories": categories,
                       "discovered_titles": len(memberships), "queried_titles": len(set(memberships) | existing_titles),
                       "resolved_pages": len(pages), "complete": all(c["complete"] for c in categories.values()) and not report['profile_errors']})
        report["totals"] = {kind: len(p["records"]) + len(p.get("manual_records", [])) for kind, p in payloads.items()}
        (args.cache_dir / "coverage.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if report["conflicts"]:
            raise ValueError("Identity conflicts require review; catalogue unchanged. See coverage.json")
        if args.apply:
            if not report["complete"] and not args.allow_partial:
                raise ValueError("Some original category pages require login. See coverage.json; use --allow-partial only after review")
            for kind, payload in payloads.items():
                payload["directory_scan"] = {k: report[k] for k in ("retrieved_on", "categories", "discovered_titles", "queried_titles", "resolved_pages", "complete")}
                (args.data_dir / FILES[kind]).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"added": len(report["added"]), "updated": len(report["updated"]), "excluded": len(report["excluded"]), "totals": report["totals"]}, ensure_ascii=False), flush=True)
    finally:
        source.client.close()


if __name__ == "__main__":
    main()
