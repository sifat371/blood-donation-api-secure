"""
One-time repair for coordinates transposed by the location-store bug.

`frontend/src/store/location-store.ts` declared `setLocation(latitude,
longitude)` but destructured the arguments as `(longitude, latitude)`. Both are
`number`, so TypeScript accepted every call site and the values were stored
swapped. Any profile completed — or blood request created — before the fix has
`latitude` and `longitude` the wrong way round.

Coincidentally, two equally-transposed rows still compute a plausible distance
between each other, which is why this hid for so long. It breaks as soon as one
correctly-located user exists: the pair are then thousands of km apart.

Detection is deliberately conservative. Bangladesh spans roughly
lat 20.5..26.7, lon 88.0..92.7 — the two ranges do not overlap, so a row whose
"latitude" sits in the longitude band is unambiguously swapped. Rows that are
already valid, or that are ambiguous, are left alone.

Usage (from the backend/ directory):

    .venv/bin/python scripts/fix_transposed_coordinates.py           # dry run
    .venv/bin/python scripts/fix_transposed_coordinates.py --apply   # write

Safe to run more than once: repaired rows no longer match the heuristic.
"""

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Bangladesh bounding box, with a margin.
LAT_MIN, LAT_MAX = 20.0, 27.0
LON_MIN, LON_MAX = 87.5, 93.0

TARGETS = (("users", "id"), ("blood_requests", "id"), ("user_locations", "id"))


def is_transposed(lat, lon) -> bool:
    """True only when (lat, lon) is out of range but (lon, lat) is in range."""
    if lat is None or lon is None:
        return False
    in_range = LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX
    swapped_in_range = LAT_MIN <= lon <= LAT_MAX and LON_MIN <= lat <= LON_MAX
    return not in_range and swapped_in_range


def db_path_from_settings() -> Path:
    sys.path.insert(0, str(BACKEND_DIR))
    from app.core.config import settings  # noqa: PLC0415

    url = settings.database_url
    if not url.startswith("sqlite"):
        raise SystemExit(f"This script only handles SQLite; got {url.split(':')[0]}")
    raw = url.split("///")[-1]
    path = Path(raw)
    return path if path.is_absolute() else (BACKEND_DIR / path).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="write the fix (default is a dry run)"
    )
    args = parser.parse_args()

    db = db_path_from_settings()
    if not db.is_file():
        print(f"No database at {db} — nothing to repair.")
        return 0

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    existing = {
        r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    plan: list[tuple[str, int, float, float]] = []

    for table, pk in TARGETS:
        if table not in existing:
            continue
        for row in con.execute(f'SELECT "{pk}", latitude, longitude FROM "{table}"'):
            if is_transposed(row["latitude"], row["longitude"]):
                plan.append((table, row[pk], row["latitude"], row["longitude"]))

    if not plan:
        print(f"{db.name}: no transposed coordinates found.")
        con.close()
        return 0

    print(f"{db.name}: {len(plan)} row(s) with transposed coordinates\n")
    for table, pk_value, lat, lon in plan:
        print(
            f"  {table}#{pk_value}: "
            f"lat={lat}, lon={lon}  ->  lat={lon}, lon={lat}"
        )

    if not args.apply:
        print("\nDry run — nothing written. Re-run with --apply to fix.")
        con.close()
        return 0

    backup = db.with_name(
        f"{db.stem}.backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}{db.suffix}"
    )
    shutil.copy2(db, backup)
    print(f"\nBackup written to {backup.name}")

    for table, pk_value, lat, lon in plan:
        con.execute(
            f'UPDATE "{table}" SET latitude = ?, longitude = ? '
            f'WHERE "{dict(TARGETS)[table]}" = ?',
            (lon, lat, pk_value),
        )
    con.commit()
    con.close()
    print(f"Repaired {len(plan)} row(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
