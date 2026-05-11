"""
Download KSA athlete photos.

Two sources (tries in order):

  1. **GCC API (preferred)** — gccgames.qa serves player photos at
     /files/players/<NOC>_<Name>_<DoB>_<G>_<PersonKey>_photo.jpg with
     no expiry and no auth. Only available for athletes whose entries
     are populated in the API (sport_results_summary).
  2. **BORNAN RegRequest xlsx (fallback)** — AWS-presigned, 5-min TTL.
     Useful for athletes not yet in any sport's participants list.

Usage:
    python download_photos.py                          # both sources
    python download_photos.py --api-only               # skip BORNAN
    python download_photos.py --file path/to/file.xlsx # specify xlsx

Output:
    photos/<PersonKey>.jpg          one file per athlete
    photos/_failed.csv              list of athletes whose URLs failed
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import urllib.request
from pathlib import Path

import pandas as pd

DEFAULT_GLOB = "GCC2026_REG_RegRequest_*.xlsx"
PHOTOS_DIR   = Path(__file__).parent / "photos"


def download_one(url: str, out: Path) -> tuple[bool, int | str]:
    """Fetch a URL and write to out. Returns (ok, bytes_or_error)."""
    if out.exists() and out.stat().st_size > 1000:
        return True, out.stat().st_size  # already there
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
        out.write_bytes(data)
        return True, len(data)
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:80]}"


def download_from_api() -> tuple[int, int]:
    """Hit every sport's participants, collect KSA player_photo URLs, download."""
    from api_client import GccApi
    api = GccApi()
    try:
        sports = [s["id"] for s in api.sports()]
    except Exception as e:
        print(f"[API] sports list failed: {e}")
        return 0, 0

    seen: dict[str, str] = {}   # PersonKey -> URL
    for sport in sports:
        try:
            comps = api.sport_results_summary(sport=sport)
        except Exception:
            continue
        for c in comps:
            for p in (c.get("participants") or []):
                if (p.get("noc_code") or "").upper() != "KSA":
                    continue
                photo = p.get("player_photo") or ""
                if not photo:
                    continue
                # Extract PersonKey from filename:
                # KSA_<Name>_<DoB>_<G>_<PersonKey>_photo.jpg
                parts = photo.rstrip("/").split("/")[-1].split("_")
                pk = parts[-2] if len(parts) >= 2 else ""
                if pk and pk not in seen:
                    seen[pk] = "https://gccgames.qa" + photo

    PHOTOS_DIR.mkdir(exist_ok=True)
    n_ok = n_fail = 0
    for pk, url in seen.items():
        out = PHOTOS_DIR / f"{pk}.jpg"
        ok, info = download_one(url, out)
        name = url.split("/")[-1].split("_")[1].replace("-", " ") if "_" in url else pk
        if ok:
            n_ok += 1
            print(f"  [OK ] {name:30s} -> {pk}.jpg ({(info if isinstance(info,int) else 0)//1024} KB)")
        else:
            n_fail += 1
            print(f"  [ERR] {name:30s} -> {info}")
    return n_ok, n_fail


def download_from_bornan_xlsx(src: Path) -> tuple[int, int]:
    print(f"[BORNAN] {src.name}")
    df = pd.read_excel(src, sheet_name="RegRequest", skiprows=1)
    PHOTOS_DIR.mkdir(exist_ok=True)
    n_ok = n_fail = 0
    failed: list[tuple] = []
    for _, row in df.iterrows():
        pk    = str(row.get("personKey") or "").strip()
        url   = str(row.get("photo")     or "").strip()
        fname = f"{(row.get('givenName') or '').strip()} {(row.get('familyName') or '').strip()}"
        if not pk or not url:
            continue
        out = PHOTOS_DIR / f"{pk}.jpg"
        ok, info = download_one(url, out)
        if ok:
            n_ok += 1
        else:
            n_fail += 1
            failed.append((pk, fname, str(info)))
    if failed:
        log = PHOTOS_DIR / "_failed.csv"
        with log.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["PersonKey", "Name", "Error"])
            w.writerows(failed)
        print(f"  [FAIL] {n_fail} photos — see {log.name}")
        print("    (BORNAN URLs expire 5 min after export — re-export and re-run within 5 min)")
    return n_ok, n_fail


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--api-only", action="store_true", help="Skip BORNAN xlsx fallback")
    p.add_argument("--file", help="RegRequest xlsx path (defaults to latest)")
    args = p.parse_args()

    print("[1/2] Downloading from GCC API (no expiry) ...")
    api_ok, api_fail = download_from_api()
    print(f"  -> {api_ok} OK, {api_fail} failed\n")

    bornan_ok = 0
    if not args.api_only:
        src = Path(args.file) if args.file else next(
            iter(sorted(Path(__file__).parent.glob(DEFAULT_GLOB))), None)
        if src and src.exists():
            print("[2/2] Trying BORNAN xlsx for any athletes the API didn't cover ...")
            bornan_ok, _ = download_from_bornan_xlsx(src)
        else:
            print("[2/2] No BORNAN RegRequest xlsx found — skipping")

    total_present = len(list(PHOTOS_DIR.glob("*.jpg")))
    print(f"\n[DONE] {total_present} photos in {PHOTOS_DIR}/")


if __name__ == "__main__":
    main()
