"""Source snapshots for offline catalogue maintenance (never used by the bot)."""
import hashlib
import json
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

CACHE = Path(tempfile.gettempdir()) / "imasbd-person-sources-v2"
USER_AGENT = "imas-birthday-catalogue/1.1 (+https://github.com/huvz04/astrbot_plugin_imas_birthday)"
_LAST_REQUEST = {}


def snapshot(client, url, cache=CACHE, *, max_age_days=0, interval=0):
    """Return the public response and the UTC date it was actually retrieved."""
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / (hashlib.sha256(url.encode()).hexdigest() + ".json")
    today = datetime.now(timezone.utc).date().isoformat()
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        age = (date.fromisoformat(today) - date.fromisoformat(saved["verified_at"])).days
        if saved["url"] == url and 0 <= age <= max_age_days:
            return saved["text"], saved["verified_at"]
    for attempt in range(3):
        try:
            if interval:
                host = urlsplit(url).hostname
                wait = _LAST_REQUEST.get(host, 0) + interval - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                _LAST_REQUEST[host] = time.monotonic()
            response = client.get(url)
            response.raise_for_status()
            break
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (429, 502, 503, 504) or attempt == 2:
                raise
            retry = exc.response.headers.get("Retry-After", "")
            if retry.isdigit() and int(retry) > 60:
                raise
            time.sleep(int(retry) if retry.isdigit() else 2 ** (attempt + 1))
        except httpx.TransportError:
            if attempt == 2:
                raise
            time.sleep(2 ** (attempt + 1))
    raw = response.text
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=cache, delete=False) as out:
        json.dump({"url": url, "verified_at": today, "text": raw}, out, ensure_ascii=False)
        temporary = Path(out.name)
    temporary.replace(path)
    return raw, today
