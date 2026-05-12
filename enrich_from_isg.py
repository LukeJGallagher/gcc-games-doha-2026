"""
Build a KSA athlete bio + medal-history file from the ISG 2025 outputs
(Riyadh, Nov 2025) for re-use as enrichment for GCC Games 2026.

ISG 2025 captured Age, Gender, sport entries and final medal results
that BORNAN's GCC API doesn't expose. ~23% of KSA GCC roster overlap
with ISG 2025 — useful enough to flag medal hopes and show age on cards.

Inputs (read from the sibling 'ISG 2025' folder):
    KSA_DAILY_SCHEDULE_EXPANDED.csv  → athlete + age + gender
    KSA_MEDAL_WINNERS_*.csv (latest) → ISG medals per athlete

Output:
    data/history/isg_2025_ksa.csv
        Athlete_Name_Lower, Age, Gender, ISG_Sport, ISG_Medal_G,
        ISG_Medal_S, ISG_Medal_B, ISG_Medal_Events
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

HERE     = Path(__file__).parent
ISG_DIR  = HERE.parent / "ISG 2025"
OUT_DIR  = HERE / "data" / "history"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def name_key(name: str) -> str:
    """Normalised lowercase key for joining: 'Hussain Al Hizam' -> 'al hizam hussain'."""
    parts = sorted(w for w in (name or "").lower().replace(",", " ").split() if w.isalpha())
    return " ".join(parts)


def main():
    if not ISG_DIR.exists():
        # Cloud agents won't have the sibling ISG 2025 folder. The committed
        # data/history/isg_2025_ksa.csv stays valid (ISG is historical) so
        # this is a non-fatal no-op outside the local laptop.
        print(f"[SKIP] ISG 2025 folder not found at {ISG_DIR} — using committed lookup file as-is")
        return

    # 1. Athlete bios (age + gender + sport)
    sched_file = ISG_DIR / "KSA_DAILY_SCHEDULE_EXPANDED.csv"
    if not sched_file.exists():
        sys.exit(f"Missing: {sched_file}")

    bios: dict[str, dict] = {}
    with sched_file.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            n = (r.get("Athlete Name") or "").strip()
            if not n:
                continue
            key = name_key(n)
            existing = bios.setdefault(key, {
                "Athlete_Name_Lower": key, "Original_Name": n,
                "Age": "", "Gender": "", "ISG_Sports": set(),
            })
            if r.get("Age") and not existing["Age"]:
                existing["Age"] = r["Age"]
            if r.get("Gender") and not existing["Gender"]:
                existing["Gender"] = r["Gender"]
            if r.get("Sport"):
                existing["ISG_Sports"].add(r["Sport"])

    print(f"[BIOS] {len(bios)} unique KSA athletes from ISG 2025")

    # 2. Medal totals from latest KSA_MEDAL_WINNERS file
    medal_files = sorted(ISG_DIR.glob("KSA_MEDAL_WINNERS_*.csv"))
    if not medal_files:
        sys.exit("No KSA_MEDAL_WINNERS file in ISG 2025 folder")
    latest = medal_files[-1]
    print(f"[MEDALS] reading {latest.name}")

    medal_totals: dict[str, dict] = defaultdict(
        lambda: {"G": 0, "S": 0, "B": 0, "events": []}
    )
    with latest.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            athletes_field = (r.get("Athlete(s)") or "").strip()
            medal = (r.get("Medal") or "").strip().upper()[:1]
            sport = (r.get("Sport") or "").strip()
            event = (r.get("Event") or "").strip()
            if medal not in {"G", "S", "B"}:
                continue
            # Athletes field can be a single name or 'Country (Athlete1, Athlete2, ...)'
            inner = athletes_field
            if "(" in inner and ")" in inner:
                inner = inner[inner.find("(") + 1 : inner.rfind(")")]
            for nm in [s.strip() for s in inner.replace(";", ",").split(",")]:
                if not nm:
                    continue
                key = name_key(nm)
                medal_totals[key][medal] += 1
                medal_totals[key]["events"].append(f"{sport}:{event}({medal})")

    print(f"[MEDALS] {len(medal_totals)} athletes with at least one ISG medal")

    # 3. Merge + write
    out = OUT_DIR / "isg_2025_ksa.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        cols = ["Athlete_Name_Lower", "Original_Name", "Age", "Gender",
                "ISG_Sports", "ISG_Medal_G", "ISG_Medal_S", "ISG_Medal_B",
                "ISG_Medal_Events"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        # Union of all keys
        all_keys = set(bios) | set(medal_totals)
        for k in sorted(all_keys):
            b = bios.get(k, {"Original_Name": k, "Age": "", "Gender": "", "ISG_Sports": set()})
            m = medal_totals.get(k, {"G": 0, "S": 0, "B": 0, "events": []})
            w.writerow({
                "Athlete_Name_Lower": k,
                "Original_Name":      b.get("Original_Name", ""),
                "Age":                b.get("Age", ""),
                "Gender":             b.get("Gender", ""),
                "ISG_Sports":         ",".join(sorted(b.get("ISG_Sports") or [])),
                "ISG_Medal_G":        m["G"],
                "ISG_Medal_S":        m["S"],
                "ISG_Medal_B":        m["B"],
                "ISG_Medal_Events":   "; ".join(m["events"]),
            })
    print(f"[SAVE] {out}  ({len(all_keys)} athletes)")


if __name__ == "__main__":
    main()
