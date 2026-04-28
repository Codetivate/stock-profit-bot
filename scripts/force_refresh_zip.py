"""force_refresh_zip.py — re-download a specific raw zip from its source URL.

Some SET filers re-upload corrected zip files at the SAME news URL
(SCC 2564/2565 FY were originally posted with the SEPARATE statement
mislabelled as CONSO; SCC re-uploaded the CONSO file later but our
cached copies still hold the wrong content). The normal ingest path
dedupes on news_id and won't refetch, so this script does an
explicit forced re-download per (symbol, year, period) tuple,
overwriting source.zip + source.xlsx + metadata.json's sha256.

Usage:
    python scripts/force_refresh_zip.py SCC 2564 FY
    python scripts/force_refresh_zip.py SCC 2564 FY 2564 H1 2565 FY
"""
from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import requests


def force_refresh(symbol: str, year: int, period: str) -> bool:
    raw_dir = Path("data/raw") / symbol / "financials" / str(year) / period
    meta_path = raw_dir / "metadata.json"
    if not meta_path.exists():
        print(f"  ✗ {symbol} {year} {period}: metadata.json missing — nothing to refresh")
        return False

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    url = meta.get("source_url")
    if not url:
        print(f"  ✗ {symbol} {year} {period}: no source_url in metadata")
        return False

    print(f"  → Fetching {url}")
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    fresh_bytes = resp.content
    fresh_sha = hashlib.sha256(fresh_bytes).hexdigest()
    old_sha = meta.get("sha256", "")
    if fresh_sha == old_sha:
        print(f"    {symbol} {year} {period}: same sha256 — nothing changed")
        return False

    # Overwrite source.zip + extract first XLSX/XLS
    zip_path = raw_dir / "source.zip"
    zip_path.write_bytes(fresh_bytes)
    print(f"    overwrote {zip_path}  ({len(fresh_bytes):,} bytes)")

    # Re-extract XLSX
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            upper = name.upper()
            if upper.endswith(".XLSX") or upper.endswith(".XLS"):
                ext = ".xlsx" if upper.endswith(".XLSX") else ".xls"
                target = raw_dir / f"source{ext}"
                target.write_bytes(zf.read(name))
                print(f"    extracted {target}")
                break

    # Update metadata
    meta["sha256"] = fresh_sha
    meta["size_bytes"] = len(fresh_bytes)
    from datetime import datetime, timezone
    meta["refreshed_at"] = datetime.now(timezone.utc).isoformat()
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"    {symbol} {year} {period}: sha256 {old_sha[:8]}… → {fresh_sha[:8]}… ✓")
    return True


def main() -> int:
    args = sys.argv[1:]
    if len(args) < 3 or (len(args) - 1) % 2 != 0:
        print("Usage: python scripts/force_refresh_zip.py SYMBOL YEAR PERIOD [YEAR PERIOD ...]")
        return 2

    symbol = args[0].upper()
    pairs = args[1:]
    refreshed = 0
    for i in range(0, len(pairs), 2):
        try:
            year = int(pairs[i])
        except ValueError:
            print(f"Skipping non-numeric year: {pairs[i]}")
            continue
        period = pairs[i + 1].upper()
        if force_refresh(symbol, year, period):
            refreshed += 1
    print(f"\nDone. {refreshed} file(s) refreshed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
