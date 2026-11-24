"""Cross-check singer identities against the public Utaite Wiki biographies."""
import json
import re
from datetime import datetime
from urllib.parse import quote, unquote, urlencode, urlsplit

from catalogue_sources import snapshot
from sync_japanese_utaite import accounts, batches, key

WIKI = "https://utaite.wiki/wiki/"
API = "https://utaite.wiki/w/api.php?"
HOSTS = {"utaite.wiki", "utaite.miraheze.org", "utaite.fandom.com", "utaite.wikia.com"}
FOREIGN = re.compile(
    r"\b(?:Chinese|Taiwanese|Hong Kong|Korean|Russian|Israeli|American|Canadian|"
    r"Filipino|Filipina|Indonesian|Malaysian|Singaporean|Thai|Vietnamese|Polish|"
    r"British|Australian|German|French|Swedish|Finnish|Brazilian)\b", re.I)


def profile(raw, title, checked):
    # SNS fields belong to this singer's infobox. Links in the song list may
    # belong to collaborators, so they cannot establish the person's identity.
    raw = re.sub(r"<!--.*?-->", "", raw, flags=re.S)
    header = re.split(r"(?m)^==", raw, 1)[0]
    fields = dict(re.findall(r"\|\s*([A-Za-z0-9]+)\s*=\s*([^|\n{}]*)", header))
    tokens = set()
    for label, value in fields.items():
        value = value.strip()
        if re.fullmatch(r"NNDuserpage|mylist\d+", label) and value.isdigit():
            tokens.add("niconico:" + ("user/" if label == "NNDuserpage" else "mylist/") + value)
        elif re.fullmatch(r"Twitter\d*", label) and re.fullmatch(r"[A-Za-z0-9_]{1,15}", value):
            tokens.add("twitter:" + value.lower())
        elif re.fullmatch(r"YTchannel\d*", label) and re.fullmatch(r"UC[A-Za-z0-9_-]+", value):
            tokens.add("youtube:" + value)
    if not tokens or not re.search(r"{{\s*(?:utaite|singer)\s*\|", raw, re.I):
        return None
    # Only the opening biographical paragraph and explicit country fields count.
    opening = re.search(r"(?m)^[}\s]*('''[^\n]+)", raw)
    intro = opening[1] if opening else ""
    intro = re.sub(r"<ref\b[^>]*>.*?</ref>", "", intro, flags=re.I)
    intro = intro.split(". ", 1)[0]
    nationality = re.search(r"\b(?:is|was)\s+(?:an?\s+)?([^.]{0,80}?)(?:utaite|youtaite|singer|vocalist)", intro, re.I)
    foreign = bool((nationality and FOREIGN.search(nationality[1])) or FOREIGN.search(fields.get("country", "")))
    source = WIKI + quote(title.replace(" ", "_"), safe="():")
    result = {"url": source, "tokens": tokens, "foreign": foreign, "checked": checked}
    day, month = fields.get("birthday", "").strip(), fields.get("birthmonth", "").strip()
    if day.isdigit() and month.isdigit() and re.search(r"\|\s*birthref\s*=\s*<ref", header):
        try:
            result["birthday"] = datetime(2000, int(month), int(day)).strftime("%m-%d")
        except ValueError:
            pass
    return result


def singer_biographies(client, candidates):
    """Names select pages to read. Shared personal SNS accounts verify the match."""
    titles_by_id = {}
    for ident, (artist, _) in candidates.items():
        titles = set()
        for link in artist.get("webLinks", []):
            url = urlsplit(link.get("url", ""))
            if not link.get("disabled") and url.hostname in HOSTS and url.path.startswith("/wiki/"):
                titles.add(unquote(url.path[6:]).replace("_", " "))
        titles.update(name["value"].strip() for name in artist.get("names", [])
                      if name.get("language") in {"Japanese", "Romaji", "English"})
        titles.add(artist["defaultName"])
        titles_by_id[ident] = {t for t in titles if t and not any(c in t for c in "|#[]{}\n")}
    pages, redirects = {}, {}
    for group in batches({title for titles in titles_by_id.values() for title in titles}, 40):
        query = {"action": "query", "format": "json", "formatversion": 2, "redirects": 1,
                 "titles": "|".join(group), "prop": "revisions", "rvprop": "content", "rvslots": "main"}
        raw, checked = snapshot(client, API + urlencode(query), max_age_days=7, interval=.3)
        doc = json.loads(raw)
        if "error" in doc or "query" not in doc or doc.get("continue"):
            raise ValueError("Incomplete Utaite Wiki biography batch")
        for change in [*doc["query"].get("normalized", []), *doc["query"].get("redirects", [])]:
            redirects[key(change["from"])] = key(change["to"])
        for page in doc["query"]["pages"]:
            if page.get("revisions"):
                body = page["revisions"][0]["slots"]["main"]["content"]
                parsed = profile(body, page["title"], checked)
                if parsed:
                    pages[key(page["title"])] = parsed
    result = {}
    for ident, (artist, _) in candidates.items():
        own = {accounts(link["url"]) for link in artist.get("webLinks", [])
               if link.get("category") == "Official" and not link.get("disabled")}
        matches = {}
        for title in titles_by_id[ident]:
            title = key(title)
            seen = set()
            while title in redirects and title not in seen and redirects[title] != title:
                seen.add(title)
                title = redirects[title]
            page = pages.get(title)
            if page and page["tokens"] & own:
                matches[page["url"]] = page
        if len(matches) == 1:
            result[ident] = next(iter(matches.values()))
    return result
