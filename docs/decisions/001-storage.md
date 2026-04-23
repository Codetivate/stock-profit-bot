# ADR 001 — Raw storage strategy

**Status**: Accepted (2026-04-22)
**Deciders**: @Nes

## Context

At full scale (~800 listed Thai equities × 6 years × 4 reports × ~500 KB) the
raw SET financial-statement zip corpus is roughly **10 GB**. Git + GitHub are
not the right home for that, but the rest of the repo (code, schemas,
processed JSON, charts, validation reports) belongs on GitHub.

Processed outputs are small (~30 KB/symbol) → committing them is fine.

## Options considered

| | **A. Gitignore raw** | **B. Git LFS** | **C. Cloud (S3/R2/GCS)** |
|---|---|---|---|
| Setup effort | 0 | medium | high (IAM, keys, bucket) |
| Repo size | tiny | tiny (pointers) | tiny |
| Re-ingest cost on data loss | ~2 h with Playwright | 0 | 0 |
| Team-share raw | impossible | via git | via signed URLs |
| Bandwidth cost | 0 | billed per GB | billed per GB |
| Vendor lock | none | GitHub | cloud provider |

## Decision

**Start with Option A.** Raw zips live only on the ingester's disk. Only
`processed/`, `validation/`, and `reference/` get committed.

## Consequences

**Accept:**
- Losing a laptop means re-running ~2 hours of Playwright ingestion against SET.
- Team members can't share raw binaries without a separate channel.
- Parser regression triage requires whoever has the raw zip.

**Mitigate:**
- `sources[]` manifest in `processed/{SYMBOL}/financials.json` lists every
  zip URL so any ingester can re-fetch deterministically.
- `sha256` in the manifest detects tampering or SET filename collisions.

## Trigger to revisit

Move to Option C when **any** of:
- Raw corpus exceeds 5 GB (roughly half-scale).
- More than one person runs ingestion (team coordination pain).
- SET removes a historical zip (we lose the ability to re-fetch).

Migration path: add an `S3_BUCKET` env var, teach `ingest/zip_downloader.py`
to dual-write (disk + S3), backfill existing raw.
