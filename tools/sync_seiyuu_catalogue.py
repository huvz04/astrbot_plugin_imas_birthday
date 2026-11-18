"""Update the public Seigura index and selected, independently identified actresses."""
import json
import re
import runpy
import unicodedata
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
try:
    from opencc import OpenCC
except ImportError:
    OpenCC = None

from catalogue_sources import USER_AGENT, snapshot

ROOT = Path(__file__).resolve().parents[1]
INDEX = "https://seigura.com/directory/"
# Stable agency profile and CSS selector for its portrait. New identities also have
# a reviewed 声優データサーチ profile. Do not create a second actor from a stage name.
AGENCY = {
    "金子 真由美": ("https://www.imenterprise.jp/profile.php?id=49", ".col-sm-4 img[src*='/files/img/']", ""),
    "ルゥ ティン": ("https://www.imenterprise.jp/profile.php?id=95", ".col-sm-4 img[src*='/files/img/']", ""),
    "中澤 ミナ": ("https://haikyo.co.jp/profile/profile.php?ActorID=12900", ".imgArea img", ""),
    "関口 理咲": ("https://mausu.net/talent/sekiguchi-risa.html", ".person-image img", ""),
    "嘉山 未紗": ("https://www.wrightfilm.co.jp/artist/kayamamisa/", ".swiper-slide img[src*='kayama_']", ""),
    "野上 翔": ("https://www.vims.co.jp/talent/356/", "img.wp-post-image[src*='Nogami-Sho']", ""),
    "竹内 恵美子": ("https://www.81produce.co.jp/actor_search/index.php/item?id=239", ".db_profile_img img", "v6IEDftannbOvtgq"),
    "松来 未祐": ("https://www.81produce.co.jp/actor_search/index.php/item?id=347", ".db_profile_img img", "XSquP9ES9UtDK42d"),
}
for name, slug, work_id in [
    ("鶴 ひろみ", "tsuru-hiromi", "NaiVQxSosCSWO571"), ("水谷 優子", "mizutani-yuko", "EBsGc5zAoICXTCH7"),
    ("江森 浩子", "emori-hiroko", "nhWgpYuj4YNOA_qm"), ("本多 知恵子", "honda-chieko", "OhpIVOh7naqhddVX"),
    ("佐藤 智恵", "sato-chie", "wulBDrDehu3iLwhr"), ("山本 圭子", "yamamoto-keiko", "jrYuLgdRzperWzjF")]:
    AGENCY[name] = (f"https://www.aoni.co.jp/search/{slug}.html", ".imgblock img", work_id)


def key(name):
    return "".join(unicodedata.normalize("NFKC", name).split()).casefold()


def portrait(url, source, checked):
    return {"avatar_url": url, "original_url": url, "source_url": source if url else None,
            "image_author": None, "license": None, "verified_at": checked if url else None}


def directory_rows(markup, checked):
    soup = BeautifulSoup(markup, "html.parser")
    rows = {}
    for item in soup.select(".archive-list-item"):
        link = item.select_one(".entry-title a")
        profile = link["href"]
        if not re.fullmatch(r"https://seigura\.com/directory/[0-9]+/", profile) or profile in rows:
            raise ValueError("Unexpected or repeated Seigura profile")
        img = item.select_one(".archive-thumbnail img")
        url = (img.get("data-src") or img.get("src", "")) if img else ""
        if not url.startswith("https://seigura.com/wp-content/uploads/"):
            url = None
        name = " ".join(link.get_text(" ", strip=True).split())
        rows[profile] = {"name": name, "sources": [profile], **portrait(url, profile, checked)}
    if not rows:
        raise ValueError("Empty Seigura directory page")
    pages = [int(a.get_text(strip=True)) for a in soup.select("a.page-numbers") if a.get_text(strip=True).isdigit()]
    return rows, max(pages, default=1)


def collect(client):
    raw, checked = snapshot(client, INDEX)
    public, pages = directory_rows(raw, checked)
    for page in range(2, pages + 1):
        rows, _ = directory_rows(*snapshot(client, INDEX + f"page/{page}/"))
        if public.keys() & rows.keys():
            raise ValueError(f"Overlapping Seigura pages at page {page}; retry with a fresh snapshot")
        public.update(rows)
    if len(public) < 1700:
        raise ValueError("Incomplete Seigura directory")
    return public


def main():
    old = runpy.run_path(str(ROOT / "seiyuu_catalogue.py"))["VOICE_ACTOR_CATALOGUE"]
    reviewed = runpy.run_path(str(ROOT / "voice_actor_links.py"))["VOICE_ACTOR_LINKS"]
    known = {**old, **reviewed}
    ids = {}
    for ident, row in known.items():
        ids.setdefault(key(row["name"]), set()).add(ident)
    ids[key("レニー・ハート")] = {"va:20740"}  # Seigura's two editions spell the same name differently.
    by_url = {url: ident for ident, row in old.items() for url in row.get("sources", []) if url.startswith(INDEX)}
    converter = OpenCC("t2s") if OpenCC else None
    result = {}
    def register(ident, row):
        previous = result.get(ident, old.get(ident, {}))
        name = row["name"]
        aliases = [" ".join(x.split()) for x in [*previous.get("aliases", []), previous.get("name", ""), row.get("reading", "")]]
        if converter:
            aliases.append(converter.convert(name).replace(" ", ""))
        result[ident] = {**row, "aliases": list(dict.fromkeys(x for x in aliases if x and x != name)),
                         "sources": list(dict.fromkeys([*row["sources"], *previous.get("sources", [])]))}

    with httpx.Client(follow_redirects=True, timeout=25, headers={"User-Agent": USER_AGENT}) as client:
        directory = collect(client)
        # Seigura lists two editions of ゆう花's profile, both with the same
        # agency, birthday and credited roles. Prefer the updated profile.
        newer, older = INDEX + "170666/", INDEX + "943/"
        if newer in directory and older in directory:
            if any(directory[u]["name"] != "ゆう花" for u in (newer, older)):
                raise ValueError("Review the two editions of the ゆう花 profile")
            directory[newer]["sources"].append(older)
            del directory[older]
        for url, row in directory.items():
            matches = ids.get(key(row["name"]), set())
            if url in by_url:
                ident = by_url[url]
            elif len(matches) == 1:
                ident = next(iter(matches))
            elif matches:
                raise ValueError("Ambiguous Seigura identity: " + row["name"])
            else:
                ident = "va:seigura-" + url.split("/")[-2]
            if ident in result:
                raise ValueError("Duplicate Seigura identity: " + ident)
            register(ident, row)

        # This mobile edition still lists 矢口アサミ. Use the image attached
        # to the exact directory ID, never a synthesized image filename.
        mobile = "https://sugotoku-seigura.secureserv.jp/directory/list.php?sort=8"
        raw, checked = snapshot(client, mobile)
        item = BeautifulSoup(raw, "html.parser").select_one('a[href="/directory/?id=20678"]')
        if item and "va:20678" not in result:
            name = item.select_one("dd").get_text(strip=True)
            if key(name) != key(old["va:20678"]["name"]):
                raise ValueError("Mobile directory identity changed")
            photo = item.select_one("img")["src"]
            if urlsplit(photo).hostname != "seigura.secureserv.jp":
                raise ValueError("Unexpected Seigura portrait host")
            register("va:20678", {"name": name, "sources": [mobile], **portrait(photo, mobile, checked)})

        for name, (url, selector, work_id) in AGENCY.items():
            matches = ids.get(key(name), set())
            if len(matches) > 1 or (not matches and not work_id):
                raise ValueError("Unknown agency identity: " + name)
            ident = next(iter(matches)) if matches else "va:work-" + work_id
            sources = [url]
            if work_id:
                profile = "https://voice-actor.work/detail/" + work_id
                page = BeautifulSoup(snapshot(client, profile)[0], "html.parser")
                if not page.title or key(page.title.get_text().split("｜")[0]) != key(name):
                    raise ValueError("Voice Actor Data Search identity changed: " + name)
                sources.append(profile)
            raw, checked = snapshot(client, url)
            soup = BeautifulSoup(raw, "html.parser")
            img = soup.select_one(selector)
            if not img or key(name) not in key(soup.get_text(" ", strip=True)):
                raise ValueError("Agency identity or portrait changed: " + name)
            avatar = urljoin(url, img["src"])
            if urlsplit(avatar).hostname != urlsplit(url).hostname or urlsplit(avatar).scheme != "https":
                raise ValueError("Unexpected agency portrait host: " + name)
            register(ident, {"name": name, **portrait(avatar, url, checked), "sources": sources})

    for ident, previous in old.items():
        if ident not in result:
            if "avatar_url" not in previous:
                raise ValueError("Legacy portrait requires review: " + ident)
            result[ident] = previous
    target = ROOT / "data" / "seiyuu.json"
    temp = target.with_suffix(".json.tmp")
    temp.write_text(json.dumps(dict(sorted(result.items())), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(target)
    print(f"{len(result)} voice actors, {sum(bool(r['avatar_url']) for r in result.values())} sourced portraits")


if __name__ == "__main__":
    main()
