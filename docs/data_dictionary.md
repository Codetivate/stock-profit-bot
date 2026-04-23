# Data dictionary

Field-by-field definitions for `data/processed/{SYMBOL}/*.json`.

## financials.json

| Field | Type | Example | Notes |
|-------|------|---------|-------|
| `schema_version` | int | `1` | Bump when shape changes. |
| `symbol` | str | `"CPALL"` | Uppercase SET ticker. |
| `company_code` | str\|null | `"0737"` | 4-digit prefix in SET zip filenames. Null if not yet mapped. |
| `company_name_en` | str | `"CP All Public Company Limited"` | Full legal name. |
| `company_name_th` | str | `"บริษัท ซีพี ออลล์ จำกัด (มหาชน)"` | Thai registered name. |
| `updated_at` | date | `"2026-02-25"` | Filing date (Gregorian) of the latest report ingested. |
| `quarterly_history[{THAI_YEAR}].Q{1-4}` | number\|null | `7255.88` | Standalone-quarter **shareholder net profit** in **millions of baht**. Derived from SET filings (annual minus 9-month, 9-month minus half, etc.). |
| `sources[]` | array | | Provenance — which raw filings contributed. |
| `sources[].year` | int | `2568` | Thai year. |
| `sources[].period` | str | `"FY"` | One of `Q1` / `H1` / `9M` / `FY`. These are the SET reporting periods, not the standalone quarter. |
| `sources[].raw_path` | str | `"CPALL/financials/2568/FY/source.zip"` | Path under `data/raw/`. |
| `sources[].sha256` | str | `"a1b2..."` | For tamper detection. |

### Period → quarter derivation

SET reports are **cumulative year-to-date**. We compute standalone quarters:

| Period | Contains | Standalone quarter recovered |
|--------|----------|-------------------------------|
| `Q1`   | Q1          | `Q1` |
| `H1`   | Q1 + Q2     | `Q2 = H1 - Q1` |
| `9M`   | Q1+Q2+Q3    | `Q3 = 9M - H1` |
| `FY`   | Q1+Q2+Q3+Q4 | `Q4 = FY - 9M` |

If an earlier period is missing, the subsequent standalone quarter is `null`.

## announcements.json

| Field | Type | Example | Notes |
|-------|------|---------|-------|
| `announcements[].news_id` | str | `"1234567"` | SET news detail id. |
| `announcements[].date` | date | `"2026-03-15"` | Announcement date. |
| `announcements[].type` | enum | `"divestiture"` | See news.schema.json for full list. |
| `announcements[].subject_symbols` | array | `["CPAXT"]` | Related tickers. CPALL divesting CPAXT puts "CPAXT" here. |
| `announcements[].title` | str | | Headline. |
| `announcements[].summary` | str\|null | | Extracted 1-3 sentence summary. |
| `announcements[].source_url` | url | | Permalink to SET newsdetails. |
| `announcements[].raw_path` | str\|null | | Path under `data/raw/{SYMBOL}/announcements/`. |

## validation/{SYMBOL}/latest.json

See `reference/data_schemas/validation.schema.json`. Each check is one of:

| Name | What it asserts |
|------|-----------------|
| `xlsx_parseable` | The source XLSX opens and the PL sheet is found. |
| `required_fields_present` | revenue, net_profit, shareholder_profit, EPS are all non-null. |
| `sum_of_quarters_matches_annual` | Q1+Q2+Q3+Q4 ≈ FY within 1% tolerance. |
| `yoy_prior_year_matches_historical` | The "prior year" column in the new filing agrees with what we stored for the same quarter one year ago. |
| `chart_hero_number_matches_source` | The big number on the rendered PNG equals `quarterly_history[latest_year][latest_quarter]`. |
| `excel_summary_matches_json` | The generated Excel summary's cells match the JSON source of truth. |
