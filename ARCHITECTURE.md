# Architecture

End-to-end design of the Thai stock financial-report bot. Audience: engineers
(human + AI) onboarding to this project.

---

## 1. Goals

1. **On-demand** — any Telegram user types a Thai stock symbol (e.g. `CPALL`) and
   receives a professional quarterly net-profit chart within seconds.
2. **Always fresh** — when a company files a new financial statement on SET,
   the bot detects it within ~15 minutes, parses it, updates the history, and
   pushes an updated chart to subscribers.
3. **Coverage** — eventually every listed Thai equity (~800 symbols). Start
   with SET50, expand to SET100, then full market.
4. **Traceable** — every number in every chart is linked back to the exact
   SET zip file it came from. Validation is a first-class concern.

---

## 2. Pipeline

```
                    ┌────────────────────────────────────────────┐
                    │  SET website (Incapsula-protected)         │
                    │  - News center (news list)                 │
                    │  - weblink.set.or.th (zip files)           │
                    └───────────────┬────────────────────────────┘
                                    │
                  ┌─────────────────┼─────────────────┐
                  ▼                 ▼                 ▼
              ┌────────┐       ┌─────────┐      ┌──────────┐
              │ monitor│       │ ingest  │      │  backfill│
              │ (cron) │       │(on-demand)     │ (one-off)│
              └───┬────┘       └────┬────┘      └────┬─────┘
                  │                 │                │
                  └────────┬────────┴────────────────┘
                           ▼
              ┌────────────────────────────┐
              │  data/raw/ (immutable)     │
              │  - source.zip              │
              │  - source.xlsx             │
              │  - metadata.json           │
              └────────────┬───────────────┘
                           │
                           ▼ parse + validate
              ┌────────────────────────────┐
              │  data/processed/           │
              │  - financials.json         │
              │  - financials.xlsx         │
              │  - announcements.json      │
              └────────────┬───────────────┘
                           │
                           ▼ build
              ┌────────────────────────────┐
              │  data/derived/             │
              │  - charts/{YEAR}_{Q}.png   │
              │  - excel/summary.xlsx      │
              └────────────┬───────────────┘
                           │
                           ▼
              ┌────────────────────────────┐
              │  Telegram                  │
              │  - DM replies              │
              │  - Channel broadcasts      │
              └────────────────────────────┘
```

---

## 3. Directory layout

See [README.md](README.md) for the full tree. Key principles:

| Layer         | Purpose                         | Regenerable? | Committed? |
|---------------|---------------------------------|--------------|------------|
| `raw/`        | Immutable source from SET       | No (dl again)| No (gitignored) |
| `processed/`  | Parsed, structured, typed       | Yes, from raw| **Yes** |
| `derived/`    | Visual output (PNG / XLSX)      | Yes, from processed | No (large) |
| `validation/` | Integrity check reports         | Yes          | Yes |
| `state/`      | Cursors, offsets, approvals     | No (stateful)| Partial — safe ones only |
| `reference/`  | Master lists (SET50, schemas)   | No (curated) | **Yes** |

Rule of thumb: **if we deleted it, could we rebuild it?** Answers the
commit/gitignore question.

---

## 4. Data contracts

All structured data conforms to schemas in [reference/data_schemas/](reference/data_schemas/):

- `financial.schema.json` — quarterly net profit history
- `news.schema.json` — corporate announcements (M&A, dividends, etc.)
- `validation.schema.json` — integrity-check report shape

When schema changes, **bump `schema_version`** in the file and add a migration
script in `scripts/migrate_vN_to_vN+1.py`.

---

## 5. Storage strategy

**Current (MVP)**: Option A — raw zips stay on the ingester's disk, only
`processed/` and `derived/charts/` get committed.

**Future trigger for Option C (cloud)**: when raw exceeds 5 GB or >1 ingester.

See [docs/decisions/001-storage.md](docs/decisions/001-storage.md).

---

## 6. Security boundaries

| Asset | Where it lives | Why |
|-------|---------------|-----|
| `TELEGRAM_BOT_TOKEN` | `.env` (local) + GitHub Secrets (CI) | Compromise = hijacked bot |
| `TELEGRAM_CHAT_ID` (admin DM) | `.env` + GitHub Secrets | Used for approval notifications |
| Approved user list (`data/state/user_approvals.json`) | Committed | Distributed sync needed |
| User PII (names, usernames) | Only stored if user DMs first | Minimize footprint |

Never commit: `.env`, `run_bot.bat`, anything under `data/raw/`, browser
profile directories.

---

## 7. Failure modes & responses

| Failure | Detection | Response |
|---------|-----------|----------|
| Incapsula blocks scraper | HTTP 403 from API | Rotate User-Agent, back off, alert admin after 3 consecutive |
| SET changes XLSX layout | Parser returns None for known fields | Mark filing as `needs_review` in validation, skip downstream |
| New filing missed | `monitor` sees no new news_id for > 48 h | Alert; run manual `ingest` |
| Telegram rate limit | HTTP 429 | Exponential backoff, queue |
| Disk full | Ingest fails to write | Alert; triggers Option C migration |

---

## 8. Team workflow

- **One symbol = one unit of work.** Ingest/parse/validate scripts take a
  `--symbol` flag. No global state edits.
- **Raw is append-only.** Never delete or edit `data/raw/` — it's the single
  source of truth for reproducing any computed value.
- **Every change carries a test.** If you change the parser, add a fixture
  zip to `tests/fixtures/` and an assertion to `tests/unit/test_parse.py`.
- **Document decisions.** Non-obvious trade-offs get an ADR in
  `docs/decisions/`.

---

## 9. Roadmap

- **Phase 0** ✅ — MVP: bare-symbol replies with seeded CPALL data
- **Phase 1** (now) — Skeleton refactor, schemas, documentation
- **Phase 2** — Playwright scraper, ingest CPALL 6-year history end-to-end
- **Phase 3** — Bulk SET50 (50 symbols)
- **Phase 4** — News monitor (cron) + auto-broadcast on new filings
- **Phase 5** — User approval flow (pre-whitelist + approval queue)
- **Phase 6** — Corporate-action announcements (e.g. CPALL → CPAXT divestiture)
- **Phase 7** — Scale to SET100, then full market (~800 symbols)
