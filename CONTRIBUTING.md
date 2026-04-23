# Contributing

Conventions for humans (and AI assistants) working on this repo.

## Before you start

1. Read [ARCHITECTURE.md](ARCHITECTURE.md) once through.
2. Copy `.env.example` → `.env` and fill in your bot token.
3. Install deps: `pip install -r requirements.txt`
4. Run tests: `pytest tests/` (should pass on `main`).

## Branch & commit

- Branch names: `feat/<short-desc>`, `fix/<short-desc>`, `chore/<short-desc>`.
- Commit message: short imperative subject (under 72 chars), blank line, then
  bullet points explaining *why*. See `git log` for style.
- One concern per PR. Parser fix and chart tweak go in separate PRs.

## Data changes

- **Never edit files under `data/raw/`.** Raw is append-only. If you find bad
  raw data, add a note in `data/validation/{SYMBOL}/` — do not modify the zip.
- When adding a new symbol, run the ingest pipeline; don't hand-write JSON.
- Changes to `data/processed/` or `data/state/` that come from a pipeline
  run get committed with message `chore: refresh {SYMBOL} data [skip ci]`.

## Schema changes

1. Bump `schema_version` in the affected JSON file.
2. Update the schema under `reference/data_schemas/`.
3. Add a migration script under `scripts/migrate_vN_to_vN+1.py`.
4. Run the migration on all existing `data/processed/` files.
5. Commit the schema, the migration, and the migrated data in one PR.

## Adding a symbol to monitoring

1. Add the symbol to `reference/set50.json` (or `set100.json`) with its
   full name (TH + EN) and 4-digit SET filing code.
2. Run `python -m src.cli.seed --symbol XXXX --years 6` to backfill history.
3. Verify `data/processed/XXXX/financials.json` looks right.
4. Commit the processed file. Raw zips stay on your machine.

## Code style

- Python 3.11+. Use type hints on public functions.
- Format with `ruff format`; lint with `ruff check`.
- Module docstrings summarise *why* the module exists, not *what* it does.
- Inline comments only for non-obvious constraints — never restate the code.

## Testing

- Every parser change needs a fixture under `tests/fixtures/`.
- Integration tests that hit SET are tagged `@pytest.mark.network` and
  skipped in CI by default; run locally with `pytest -m network`.
- Don't mock the XLSX parser — use real fixture zips so we catch XLSX format
  drift when SET changes their schema.

## When in doubt

Open an issue or ping the team before making large structural changes. An
hour of discussion beats a week of rework.
