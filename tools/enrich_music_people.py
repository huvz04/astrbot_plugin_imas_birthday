"""Fill singer/producer birthdays and missing portraits from public Moegirl pages.

No login, search fuzzing, real-name guessing, or runtime crawling. Only existing
catalogue identities matched by unique stage names or official accounts are used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://zh.moegirl.org.cn"
UA = "imas-birthday-catalogue/1.1 (+https://github.com/huvz04/astrbot_plugin_imas_birthday)"


def text(node):
    # Public rendered content is also present inside a skin <template>.
    return node.get_text(" ", strip=True, types=None) if node else ""


def key(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


def account(url):
    p = urlsplit(url)
    host = (p.hostname or "").lower().removeprefix("www.")
    path = p.path.rstrip("/")
    if host in {"x.com", "twitter.com"} and re.fullmatch(r"/[A-Za-z0-9_]+", path):
        return "x:" + path.lower()
    if host in {"youtube.com", "nicovideo.jp", "piapro.jp", "space.bilibili.com"}:
        return host + path if path else ""
    return ""


def categories(html):
    match = re.search(r'"wgCategories":(\[[^\]]*\])', html)
    return json.loads(match[1]) if match else []


def parse_profile(html, url, *, verified_music=False):
    soup = BeautifulSoup(html, "html.parser")
    cats = categories(html)
    if not verified_music and any(re.search(r"虚拟(?:UP主|主播|YouTuber|歌手)|VTuber", cat, re.I) for cat in cats):
        return {"excluded": True}
    if not verified_music and not any(re.search(r"职人|音乐人|作曲家|作词家|歌手|唱见|编曲家", cat) for cat in cats):
        return {}
    table = soup.select_one("table.infobox,table.infotemplatebox")
    if not table:
        return {}
    birthday = ""
    for row in table.select("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) < 2 or text(cells[0]) not in {"生日", "出生", "出生日期", "生年月日"}:
            continue
        value = text(cells[1])
        if re.search(r"未知|不明|不详|非公开|不公开|争议|[?？]", value):
            continue
        dates = set(re.findall(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日", value))
        if len(dates) == 1:
            month, day = next(iter(dates))
            try:
                datetime(2000, int(month), int(day))
                birthday = f"{int(month):02d}-{int(day):02d}"
            except ValueError:
                pass
    image = ""
    for item in table.select("img"):
        if int(item.get("width", 0) or 0) < 100 or int(item.get("height", 0) or 0) < 100:
            continue
        image = urljoin(url, item.get("src", ""))
        if urlsplit(image).hostname != "storage.moegirl.org.cn":
            image = ""
            continue
        # A bounded rendition is adequate for avatars and avoids huge originals.
        image = image.split("!/")[0].split("?")[0] + "!/fw/640"
        break
    canonical = soup.select_one('link[rel="canonical"]')
    source = canonical.get("href", url) if canonical else url
    if urlsplit(source).hostname not in {"zh.moegirl.org.cn", "mzh.moegirl.org.cn"}:
        source = url
    links = {account(a["href"]) for a in soup.select('a.external[href]')} - {""}
    heading = soup.select_one("#firstHeading")
    title = re.sub(r" - 萌娘百科.*$", "", text(soup.title))
    return {"birthday": birthday, "image_url": image, "source": source,
            "title": text(heading) or title, "accounts": links}


def fetch(client, url, cache, interval):
    path = cache / (hashlib.sha256(url.encode()).hexdigest() + ".html")
    if path.exists():
        return path.read_text(encoding="utf-8")
    response = client.get(url)
    response.raise_for_status()
    response.encoding = "utf-8"
    if "mw-content-text" not in response.text and "mw-pages" not in response.text:
        raise ValueError("No public page content; authentication/challenge is not bypassed")
    path.write_text(response.text, encoding="utf-8")
    time.sleep(interval)
    return response.text


def category_titles(client, category, cache, interval):
    url, seen, titles = BASE + "/index.php?title=" + quote("Category:" + category), set(), set()
    while url and url not in seen and len(seen) < 30:
        seen.add(url)
        soup = BeautifulSoup(fetch(client, url, cache, interval), "html.parser")
        titles.update(a.get("title") for a in soup.select("#mw-pages .mw-category a[title]") if a.get("title"))
        next_page = next((a for a in soup.select("#mw-pages > a[href]") if "下一页" in text(a)), None)
        url = urljoin(BASE, next_page["href"]).split("#")[0] if next_page else ""
        if url and urlsplit(url).hostname != "zh.moegirl.org.cn":
            raise ValueError("Unexpected category destination")
    return titles


def api_profiles(client, rows, cache, interval):
    """Public query props support batches even when category-list APIs require login."""
    names = sorted({name.replace("_", " ") for row in rows.values()
                    for name in (row["name"], row.get("moegirl", {}).get("title", ""))
                    if name and "|" not in name})
    # Only individually reviewed singer identities may override incomplete or
    # overlapping voice-actor/VTuber categories. Never admit all such categories.
    verified_titles = {key(row["moegirl"]["title"]) for row in rows.values()
                       if row.get("music_identity_source") in row.get("sources", [])
                       and row.get("moegirl", {}).get("title")}
    for start in range(0, len(names), 50):
        params = {"action": "query", "prop": "pageimages|categories|info", "piprop": "original",
                  "titles": "|".join(names[start:start + 50]), "redirects": 1, "cllimit": "max", "inprop": "url", "format": "json"}
        url = str(httpx.URL(BASE + "/api.php", params=params))
        path = cache / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
            else:
                response = client.get(url)
                response.raise_for_status()
                data = response.json()
                if "error" in data:
                    raise ValueError(data["error"].get("code", "API error"))
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                time.sleep(interval)
            query = data.get("query", {})
            references = {row["from"]: row["to"] for field in ("normalized", "redirects") for row in query.get(field, [])}
            for page in query.get("pages", {}).values():
                if "missing" in page or page.get("ns") != 0:
                    continue
                title = page["title"]
                aliases = {title}
                for origin in references:
                    current, visited = origin, set()
                    while current in references and current not in visited:
                        visited.add(current)
                        current = references[current]
                    if current == title:
                        aliases.add(origin)
                cats = [cat["title"].removeprefix("Category:") for cat in page.get("categories", [])]
                verified_music = key(title) in verified_titles
                excluded = not verified_music and any(re.search(r"虚拟(?:UP主|主播|YouTuber|歌手)|VTuber", cat, re.I) for cat in cats)
                if not excluded and not verified_music and not any(re.search(r"职人|音乐人|作曲家|作词家|歌手|唱见|编曲家", cat) for cat in cats):
                    continue
                dates = [re.fullmatch(r"(\d{1,2})月(\d{1,2})日", cat) for cat in cats]
                dates = {(int(match[1]), int(match[2])) for match in dates if match}
                birthday = ""
                if len(dates) == 1:
                    month, day = next(iter(dates))
                    try:
                        datetime(2000, month, day)
                        birthday = f"{month:02d}-{day:02d}"
                    except ValueError:
                        pass
                picture = page.get("original", {})
                image = picture.get("source", "")
                if urlsplit(image).hostname != "storage.moegirl.org.cn" or min(picture.get("width", 0), picture.get("height", 0)) < 100:
                    image = ""
                elif image:
                    image = image.split("!/")[0] + "!/fw/640"
                yield {"title": title, "aliases": aliases, "birthday": birthday, "image_url": image,
                       "source": BASE + "/" + quote(title.replace(" ", "_")), "revision": page.get("lastrevid"), "excluded": excluded}
        except (httpx.HTTPError, ValueError) as exc:
            print(f"Public query batch {start}: {type(exc).__name__}", flush=True)
        if start % 500 == 0:
            print(f"Queried public names: {min(start + 50, len(names))}/{len(names)}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "people")
    parser.add_argument("--cache-dir", type=Path, default=ROOT.parent / "research" / "imas-birthday-tantou" / "public-people" / "moegirl-cache")
    parser.add_argument("--interval", type=float, default=.7)
    args = parser.parse_args()
    if not .4 <= args.interval <= 30:
        parser.error("--interval must be between 0.4 and 30 seconds")
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    payloads = {filename: json.loads((args.data_dir / filename).read_text(encoding="utf-8"))
                for filename in ("utaite.json", "music_creators.json")}
    rows = {row["id"]: row for p in payloads.values() for row in [*p["records"], *p.get("manual_records", [])]}
    index = {}
    for ident, row in rows.items():
        for alias in [row["name"], *row.get("aliases", [])]:
            if len(key(alias)) >= 2:
                index.setdefault(key(alias), set()).add(ident)
    count = images = birthdays = 0
    excluded = set()
    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": UA}) as client:
        for profile in api_profiles(client, rows, args.cache_dir, args.interval):
            candidates = {ident for alias in profile["aliases"] for ident in index.get(key(alias), [])}
            if len(candidates) != 1:
                continue
            ident = next(iter(candidates))
            if profile["excluded"]:
                excluded.add(ident)
                continue
            row = rows[ident]
            source = profile["source"]
            if profile["birthday"]:
                row["birthday"], row["birthday_source"] = profile["birthday"], source
                birthdays += 1
            if not row.get("image_url") and profile["image_url"]:
                row["image_url"], row["image_source"] = profile["image_url"], source
                images += 1
            row["sources"] = list(dict.fromkeys([*row["sources"], source]))
            row["moegirl"] = {"url": source, "title": profile["title"], "image_url": profile["image_url"],
                              "revision": profile.get("revision"), "retrieved_on": datetime.now(timezone.utc).date().isoformat()}
            count += 1
        titles = set()
        for category in ("网络歌手", "VOCALOID职人", "作曲家"):
            try:
                titles.update(category_titles(client, category, args.cache_dir, args.interval))
            except (httpx.HTTPError, ValueError) as exc:
                print(f"Category {category}: {type(exc).__name__}", flush=True)
        # Explicit reference links can cover pages outside the main categories.
        for row in rows.values():
            for source in row.get("sources", []):
                p = urlsplit(source)
                if p.hostname in {"zh.moegirl.org.cn", "mzh.moegirl.org.cn"}:
                    from urllib.parse import unquote
                    titles.add(unquote(p.path.strip("/")).replace("_", " "))
        titles.add("田中秀和")
        targets = [(title, index[key(title.replace("_", " "))]) for title in sorted(titles)
                   if key(title.replace("_", " ")) in index]
        print(f"Public category pages: {len(titles)}, matching titles: {len(targets)}", flush=True)
        for number, (title, candidates) in enumerate(targets, 1):
            try:
                verified_music = len(candidates) == 1 and any(
                    rows[ident].get("music_identity_source") in rows[ident].get("sources", [])
                    and key(rows[ident].get("moegirl", {}).get("title", "")) == key(title)
                    for ident in candidates)
                profile = parse_profile(fetch(client, BASE + "/" + quote(title.replace(" ", "_")), args.cache_dir, args.interval), BASE + "/" + quote(title), verified_music=verified_music)
                if profile.get("excluded"):
                    if len(candidates) == 1:
                        excluded.update(candidates)
                    continue
                if not profile:
                    continue
                if len(candidates) != 1:
                    candidates = {ident for ident in candidates if profile["accounts"].intersection(account(source) for source in rows[ident]["sources"])}
                if len(candidates) != 1:
                    continue
                row = rows[next(iter(candidates))]
                source = profile["source"]
                if profile["birthday"]:
                    row["birthday"] = profile["birthday"]
                    row["birthday_source"] = source
                    birthdays += 1
                if not row.get("image_url") and profile["image_url"]:
                    row["image_url"] = profile["image_url"]
                    row["image_source"] = source
                    images += 1
                row["sources"] = list(dict.fromkeys([*row["sources"], source]))
                row["moegirl"] = {**row.get("moegirl", {}), "url": source, "title": profile["title"],
                                  "image_url": profile["image_url"], "retrieved_on": datetime.now(timezone.utc).date().isoformat()}
                count += 1
            except (httpx.HTTPError, ValueError) as exc:
                print(f"Skipped {title}: {type(exc).__name__}", flush=True)
            if number % 25 == 0:
                print(f"Reviewed {number}/{len(targets)}, birthdays {birthdays}, portraits {images}", flush=True)
        for p in payloads.values():
            removed = set(p.get("removed_ids", [])) | {ident for ident in excluded if any(row["id"] == ident for row in p["records"])}
            p["records"] = [row for row in p["records"] if row["id"] not in removed]
            p["removed_ids"] = sorted(removed)
            p["redirects"] = {k: v for k, v in p.get("redirects", {}).items() if k not in removed and v not in excluded}
            p["enrichment"] = {"source": BASE, "retrieved_on": datetime.now(timezone.utc).date().isoformat(), "scope": "existing singers and music creators only"}
        for filename, p in payloads.items():
            (args.data_dir / filename).write_text(json.dumps(p, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"matched": count, "birthdays": birthdays, "filled_portraits": images, "excluded_vtubers": len(excluded)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
