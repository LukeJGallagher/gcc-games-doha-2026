"""
Download static assets from gccgames.qa: sport pictograms (SVGs) + NOC flags.

Pictogram URLs come straight from gms.api.sport.sports.
Flag URLs come from gms.api.medal_standings.medal_standings.

Output:
    assets/pictograms/<sport>.svg
    assets/flags/<noc>.png
"""
from __future__ import annotations

import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from api_client import GccApi
from config import BASE_URL, USER_AGENT

HERE          = Path(__file__).parent
PICTO_DIR     = HERE / "assets" / "pictograms"
FLAG_DIR      = HERE / "assets" / "flags"
PICTO_DIR.mkdir(parents=True, exist_ok=True)
FLAG_DIR.mkdir(parents=True, exist_ok=True)


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def fetch(url: str, dest: Path) -> bool:
    if dest.exists() and dest.stat().st_size > 200:
        return True
    # URL-encode spaces and other unsafe chars in the path portion
    parsed = urllib.parse.urlsplit(url)
    safe_path = urllib.parse.quote(parsed.path, safe="/")
    safe_url  = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, safe_path, parsed.query, parsed.fragment))
    try:
        req = urllib.request.Request(safe_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as r:
            dest.write_bytes(r.read())
        return True
    except Exception as e:
        sys.stdout.write(f"  [ERR] {safe_url[-60:]} -> {type(e).__name__}\n")
        return False


def main():
    api = GccApi()

    # ---- Sport pictograms ----
    print("[PICTOGRAMS] from gms.api.sport.sports")
    sports = api.sports()
    n_ok = 0
    for s in sports:
        name = s.get("english_name") or s.get("id")
        path = (s.get("pictogram") or "").lstrip("/")
        if not path:
            continue
        ext  = Path(path).suffix or ".svg"
        url  = f"{BASE_URL}/{path}"
        dest = PICTO_DIR / f"{slugify(name)}{ext}"
        if fetch(url, dest):
            n_ok += 1
            print(f"  [OK ] {name:18s} -> {dest.name}")
    print(f"  -> {n_ok}/{len(sports)} pictograms\n")

    # ---- NOC flags ----
    print("[FLAGS] from gms.api.medal_standings.medal_standings")
    medals = api.medal_standings()
    n_ok = 0
    for m in medals:
        noc  = m.get("noc")
        path = (m.get("flag") or "").lstrip("/")
        if not noc or not path:
            continue
        ext  = Path(path).suffix or ".png"
        url  = f"{BASE_URL}/{path}"
        dest = FLAG_DIR / f"{noc.lower()}{ext}"
        if fetch(url, dest):
            n_ok += 1
            print(f"  [OK ] {noc} ({m.get('name')}) -> {dest.name}")
    print(f"  -> {n_ok}/{len(medals)} flags")


if __name__ == "__main__":
    main()
