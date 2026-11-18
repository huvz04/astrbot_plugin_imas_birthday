"""Read the file and attribution for a Commons portrait with a reviewed identity."""
import json
from urllib.parse import urlencode, urlsplit

from bs4 import BeautifulSoup

from catalogue_sources import snapshot


def commons_portrait(client, filename):
    params = {"action": "query", "format": "json", "formatversion": 2, "prop": "imageinfo",
              "titles": "File:" + filename, "iiprop": "url|extmetadata", "iiurlwidth": 320}
    raw, checked = snapshot(client, "https://commons.wikimedia.org/w/api.php?" + urlencode(params))
    page, = json.loads(raw)["query"]["pages"]
    info, = page.get("imageinfo", [])
    metadata = info["extmetadata"]
    def value(key):
        raw = metadata.get(key, {}).get("value", "")
        return BeautifulSoup(raw, "html.parser").get_text(" ", strip=True) if "<" in raw else raw.strip()
    author = value("Artist")
    rights = value("LicenseShortName")
    avatar = info["thumburl"]
    if not author or not rights or urlsplit(avatar).hostname not in {"thumb.wikimedia.org", "upload.wikimedia.org"}:
        raise ValueError("Incomplete Commons photo attribution: " + filename)
    return {"avatar_url": avatar, "original_url": info["url"], "source_url": info["descriptionurl"],
            "license": rights, "license_url": value("LicenseUrl") or None,
            "image_author": author, "attribution": value("Attribution") or None, "verified_at": checked}
