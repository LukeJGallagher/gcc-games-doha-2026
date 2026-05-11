"""
Download KSA athlete photos from BORNAN.

BORNAN photo URLs in the RegRequest xlsx are AWS-presigned with a ~5-minute TTL.
You must run this within 5 minutes of exporting a fresh RegRequest file from BORNAN.

Usage:
    python download_photos.py                          # use latest RegRequest xlsx
    python download_photos.py --file path/to/file.xlsx

Output:
    photos/<PersonKey>.jpg          one file per athlete
    photos/_failed.csv              list of athletes whose URLs failed (re-export needed)
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


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--file", help="RegRequest xlsx (defaults to latest)")
    args = p.parse_args()

    src = Path(args.file) if args.file else next(
        iter(sorted(Path(__file__).parent.glob(DEFAULT_GLOB))), None)
    if not src:
        sys.exit(f"No file matching {DEFAULT_GLOB}")
    print(f"[LOAD] {src.name}")

    df = pd.read_excel(src, sheet_name="RegRequest", skiprows=1)
    print(f"  {len(df)} athletes")

    PHOTOS_DIR.mkdir(exist_ok=True)
    ok, failed = [], []
    for i, row in df.iterrows():
        pk    = str(row.get("personKey") or "").strip()
        url   = str(row.get("photo")     or "").strip()
        fname = f"{(row.get('givenName') or '').strip()} {(row.get('familyName') or '').strip()}"
        if not pk or not url:
            continue
        out = PHOTOS_DIR / f"{pk}.jpg"
        if out.exists() and out.stat().st_size > 1000:
            ok.append(pk)
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
            out.write_bytes(data)
            ok.append(pk)
            print(f"  [OK]  {fname:30s} -> {out.name} ({len(data)//1024} KB)")
        except Exception as e:
            failed.append((pk, fname, str(e)[:120]))
            print(f"  [ERR] {fname:30s} -> {type(e).__name__}")

    if failed:
        log = PHOTOS_DIR / "_failed.csv"
        with log.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["PersonKey", "Name", "Error"])
            w.writerows(failed)
        print(f"\n[FAIL] {len(failed)} photos failed - see {log.name}")
        print("  (BORNAN URLs expire in 5 min. Re-export RegRequest and re-run this within 5 min.)")
    print(f"\n[DONE] {len(ok)} photos in {PHOTOS_DIR}/")


if __name__ == "__main__":
    main()
