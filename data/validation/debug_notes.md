# Debug log — per-symbol investigation

Notes from dive-by-dive investigation of SET-vs-parsed mismatches.
Each entry documents what we found in the source XLSX, what SET API
reports, and what (if anything) was fixed in the parser.

The audit harness lives at `scripts/verify_against_set.py`; the
authoritative SET reference is the company-highlight API at
`https://www.set.or.th/api/set/stock/{SYMBOL}/company-highlight/financial-data?lang=th`
(only annual rows — `quarter == "Q9"`).

Rule of thumb: **never fabricate a number.** Every value we publish
must trace back to a specific cell in a SET-filed XLSX. If our value
disagrees with the SET API, the answer is in the workbook somewhere —
find it, then teach the parser to read that exact cell.

---

## Status legend
- ✅ FIXED — parser now matches SET
- 🔧 PARTIAL — parser improved but still off
- 📝 KNOWN — root cause identified, fix deferred (needs design)
- ⏳ TODO — not yet investigated

---

## Symbols investigated

### ✅ 2S — FIXED (cross-sheet shareholder lookup)
- **Symptom:** FY2568 = 144.35 (ours) vs 144.53 (SET); QoQ inverted in caption.
- **Root cause:** 2S FY2568 ships a 2-sheet PL — sheet 1 has only consolidated total (`กำไรสำหรับปี = 144,352`); sheet 2 has parent-vs-NCI breakdown (`ส่วนที่เป็นของบริษัทใหญ่ = 144,533`). Parser only scanned sheet 1.
- **Fix:** [parsers/parse_set_zip.py:790-825](../../parsers/parse_set_zip.py#L790-L825) cross-sheet recovery now scans secondary PL sheets for `_is_shareholder_profit_row` when the primary sheet lacks one.
- **Caption side:** `command_handler.build_rich_caption` was using raw `(now − prior) / prior`; with prior = -0.12 the QoQ flipped sign vs the chart. Switched to `abs(prior)` to match the chart formula across all 5 caption sites.

### ✅ TEAM — FIXED (skip "ไม่เอา" sheets + year filter)
- **Symptom:** 2564 = 202,671 (ours) vs 202.67 (SET) — exactly 1000× off.
- **Root cause:** TEAM 2564 FY zip ships a sheet literally named `PL-ไม่เอา` ("PL — don't use") and additional 3m/6m sheets from a 2562 quarterly filing. Parser sorted by period length, picked `PL-ไม่เอา` (3-month) as primary, derived a stale 2562 value, mis-applied unit divisor.
- **Fix:** Skip filter for sheet names containing `ไม่เอา`/`ไม่ใช้`/`ห้ามใช้`/`DO NOT USE` + year filter that drops sheets whose latest year is older than the workbook's max year.

### ✅ MTC — FIXED (skip TAS reference codes in label column)
- **Symptom:** FY2568 = 22.22 (ours) vs 6,723.27 (SET) — 300× off.
- **Root cause:** MTC's PL10-11 puts an IFRS reference code (`TAS 1.81ก.1`) in column A and the actual label (`กำไรสำหรับปี`) in column B. `_find_label` was returning the TAS code as the row label; nothing matched `_is_netprofit_row`.
- **Fix:** `_find_label` now skips cells whose entire content matches `^(TAS|TFRS|IAS|IFRS)\b…` so the next string cell becomes the label.

### ✅ BEAUTY — FIXED (relax `(ขาดทุน)` regex)
- **Symptom:** Earlier years all 50.85 (stale value from a 2562 sheet stuck in later filings).
- **Root cause:** BEAUTY's row reads `กำไร (ขาดทุน ) สำหรับงวด` with an extra space inside the parens. The regex `\(ขาดทุน\)` (zero-space-only) didn't match, so the row was silently dropped and the parser fell back to the wrong sheet.
- **Fix:** Relaxed the loss-marker pattern to `\(\s*ขาดทุน\s*\)` so the inner whitespace doesn't matter.
- **2566 FY follow-up:** Still flagged because BEAUTY's 2566 FY 'PL' sheet only carries the `กำไร (ขาดทุน) เบ็ดเสร็จรวมสำหรับปี` (comprehensive income) line — no standalone net-profit row. Tried a comprehensive-income fallback but it broke THREL/GYT/MATCH/SLP. Reverted; BEAUTY 2566 FY stays mismatched until per-symbol override is in place.

### ✅ FE — FIXED (always-on content fallback for PL sheet detection)
- **Symptom:** Same 249.30 every year.
- **Root cause:** FE FY 2568 has only stale `กำไรขาดทุน9เดือน` sheets from 2555-2559 plus a current sheet named `งบการเงิน` (which actually contains the income statement). The name-match for "PL"/"กำไรขาดทุน" returned only the stale sheets; the content-fallback loop was guarded `if not candidates`, so it never ran when there were stale-named candidates.
- **Fix:** Content fallback now always runs (in addition to name-match) and merges the results before the dedup pass.

### ✅ ERW — FIXED (lookahead heuristic for footnote refs >100)
- **Symptom:** FY2568 shareholder = 0.00 vs 838 (SET).
- **Root cause:** ERW PL row 66 is `ส่วนที่เป็นของบริษัทใหญ่, 3935, 838085161, …` — 3935 is a footnote reference, not data. The parser's footnote-skip was capped at `abs < 100`; 3935 slipped through and was divided by 1,000,000 → 0.00.
- **Fix:** Added a magnitude lookahead — if the next nonzero numeric in the row is more than 100× larger than the current cell, the current cell is treated as a footnote ref and skipped.

### ✅ PSL — FIXED (tighten shareholder matcher to exclude BS equity rows)
- **Symptom:** FY2564 = 14,365 (ours) vs 4,475 (SET) — 3× too high.
- **Root cause:** PSL bs&plt row 101 is `ส่วนของผู้ถือหุ้นของบริษัทฯ, 14,364,979,270, …` — that's TOTAL SHAREHOLDERS' EQUITY (a balance-sheet line), not net profit. The matcher accepted any label starting with `ส่วนของ…` containing `บริษัท`, so the BS equity line shadowed the real PL row at row 167 (`ส่วนที่เป็นของผู้ถือหุ้นของบริษัทฯ` = 4,474,929,926).
- **Fix:** `_is_shareholder_profit_row` now rejects labels starting with `ส่วนของผู้ถือหุ้น` (BS equity) and `รวมส่วน` (BS aggregate equity rows).

### ✅ BANPU / PTTEP / SPRC / CCET — FIXED (dual-currency THB column offset)
- **Symptom:** Values ~30× off (USD vs THB).
- **Root cause:** These filers print USD columns and THB columns side-by-side in the same PL sheet (`หน่วย: พันเหรียญสหรัฐ` over the first numeric block, `หน่วย: บาท` over a second block). `_extract_numeric` started at column `label_col + 1`, picked the USD values, then the unit divisor (detected as พันบาท) divided them as if they were baht — giving USD-thousand values masquerading as million-baht.
- **Fix:** Detect dual-currency by spotting both USD and THB markers in the top header band, then locate the THB block by finding the SECOND occurrence of the latest year (`พ.ศ. 2564`) in the column-header row. Scan starts at that column. Works across BANPU/PTTEP layout (USD col 5,7 / THB col 9,11), CCET layout (USD col 3,5 / THB col 7,9), and SPRC layout (USD col 6,8 / THB col 10,12).

### ✅ ITC — FIXED (parent-share section with `รวม` total row)
- **Symptom:** 2564 = 1,575.85 (ours) vs 1,598.68 (SET).
- **Root cause:** ITC splits parent share into "continuing" + "discontinued" sub-rows under a `ส่วนที่เป็นของผู้เป็นเจ้าของของบริษัทใหญ่` section header; the row labelled `รวม` (just "total") at row 82 carries the parent-share total = 1,598.68. Parser saw the section header (no values), then skipped past the sub-rows, then fell back to `กำไรสำหรับปี` row 90 = consolidated 1,575.85 (parent + NCI).
- **Fix:** Track `in_parent_section` flag — when the parent-share header row has no values, the next `รวม` row's values become the parent share. Also rejects netprofit rows whose label says `จากการดำเนินงานต่อเนื่อง`/`ที่ยกเลิก` so we get the unqualified total instead of the continuing-ops-only line.

### ✅ ANAN — FIXED (parent section with unlabeled total row)
- **Symptom:** 2564 = -339.23 (ours) vs -457.34 (SET).
- **Root cause:** Same idea as ITC, but ANAN's section-total row has NO LABEL at all — the visual `รวม` is in a merged cell that openpyxl reads as empty. Parser skipped the row entirely.
- **Fix:** Inside `in_parent_section`, accept an unlabeled row with values as the section total.

### ✅ INTUCH — FIXED (inline-share label `กำไรสำหรับปีส่วนที่เป็นของบริษัทใหญ่`)
- **Symptom:** 2565 = 10,730.28 (ours) vs 10,533.09 (SET).
- **Root cause:** INTUCH collapses two phrases into one label: `กำไรสำหรับปีส่วนที่เป็นของบริษัทใหญ่`. It starts with `กำไรสำหรับปี` (matches `_is_netprofit_row`) but the second occurrence — the parent-share total — was rejected by `_is_shareholder_profit_row`'s strict `startswith("ส่วน…")` check.
- **Fix:** `_is_shareholder_profit_row` now also accepts inline labels matching `^กำไร…สำหรับ(ปี|งวด|…)` AND containing `ส่วนที่เป็น`/`ส่วนของ`. Order in the extraction loop ensures the consolidated `กำไรสำหรับปี` row claims `net_profit` first; the second (parent-share-with-mixed-label) row then claims `shareholder_profit`.

### ✅ SPRC 2566–2568 — FIXED (4-section dual-currency layout + wider scan)
- **Symptom:** SPRC 2566 = -34.26 (ours) vs -1,229.93 (SET); 2567 + 2568 returned no values at all (parse miss).
- **Root cause:** SPRC restructured the PL sheet starting 2566 to a 4-section layout: ``USD CONSO | USD SEP | THB CONSO | THB SEP``, each with current+prior columns. The dual-currency detector (which handled BANPU/PTTEP/CCET's 2-section layout) had three bugs against this format:
  1. **Bare ``บาท`` cells.** SPRC's unit row uses single-word ``บาท`` per data column instead of merged ``หน่วย: บาท``. The detector required ``หน่วย`` + ``บาท`` together, so `has_thb_marker` came back False and dual-currency detection bailed out.
  2. **Title rows mistaken for year-headers.** Titles like ``สำหรับปีสิ้นสุดวันที่ 31 ธันวาคม พ.ศ. 2567`` appear at multiple column positions and contain ``2567``. The detector picked them as the "year header" and returned the title's column position (col 13) instead of the real year-header row.
  3. **Scan range cap at col 20.** SPRC's THB CONSO data sits at columns 17–25; ``_extract_numeric`` capped at col 20 saw at most one cell, so even with the right offset the row produced ``len(nums) < 2`` and the assignment was skipped.
- **Fix:**
  1. Accept bare-``บาท`` cells in the unit-row detector.
  2. Filter year occurrences to short cells (≤ 20 chars) so title strings can't masquerade as year headers.
  3. Raise the extraction scan cap to col 30 (still excludes the col-31+ stale-cache slots that DITTO 2566 Q1 has).
- **Lesson:** Single-row heuristics (unit text, year occurrence, scan width) can break in unison when a filer adopts a wider layout. Each guard added for a specific filer (e.g. DITTO's col-20 cap) needs revisiting when another filer goes wider — pin each constant to a comment that explains *why*, so future fixes can reason about whether to relax it.

### ✅ ILINK — FIXED (strip leading bullets in shareholder matcher)
- **Symptom:** All 5 years show consolidated total instead of parent share (~115 MB diff per year, since ILINK has a sizable NCI).
- **Root cause:** ILINK indents allocation rows with a literal hyphen: row 63 reads `- ส่วนที่เป็นของผู้เป็นเจ้าของของบริษัทใหญ่` = 353,108 (parent — matches SET 353.11). The leading `- ` blocked `startswith("ส่วน…")`, so the matcher rejected the row and parser fell back to the consolidated `กำไรสุทธิสำหรับปี` = 467.
- **Fix:** `_is_shareholder_profit_row` now lstrips bullet markers (`-`, `*`, `•`, `◦`, `·`) before the prefix check.

### ✅ TKN / SYNTEC / THE — FIXED (cascaded by cross-sheet shareholder fix)
- Same root cause as 2S. Cross-sheet shareholder lookup picked them up automatically once the 2S fix landed.

---

## Symbols investigated but not fixed

### 📝 MTI — restatement (SET uses restated 2567)
- **Symptom:** 2567 = 754.36 (ours, from 2567 FY filing primary col) vs 1,501.34 (SET) — diff -747 MB.
- **Root cause:** MTI 2567 FY filing's primary `กำไรสุทธิสำหรับปี` = 754,359,862 baht. MTI 2568 FY filing's 2567 prior column = 1,501,335,188 (TFRS17 restated, ~2× the original — insurance accounting policy change). SET uses the restated value.
- **Why not auto-applied:** Same restatement-detection problem as KBANK/AYUD. KBANK (2% restatement) → SET uses restated; AYUD (3.5× restatement) → SET keeps original; MTI (2× restatement) → SET uses restated. No reliable rule from the XLSX alone.

### 📝 KBANK — restatement (SET uses restated 2567)
- **Symptom:** 2567 = 48,598.12 (ours) vs 49,603.54 (SET) — diff -1,005.41 MB.
- **Root cause:** KBANK 2567 FY filing's primary column = 48,598. KBANK 2568 FY filing's prior column (with `2567 (ปรับปรุงใหม่)` marker) = 49,603. SET uses the restated value.
- **Why not auto-applied:** Universal restatement override breaks AYUD (see below), where SET ignores the restated value. SET's behaviour isn't predictable from the XLSX alone (both have `(ปรับปรุงใหม่)` marker but only KBANK's restatement is reflected in SET).
- **Workaround:** Per-symbol opt-in via `parsers/symbol_rules.json` once we have the schema for it.

### 📝 AYUD — SET keeps original (ignores restatement)
- **Symptom:** 2567 = 714.75 (ours, matches SET) — would BREAK if we apply restatement.
- **Root cause:** AYUD 2568 FY filing's 2567 prior column has `(ปรับปรุงใหม่)` marker showing 2,500.51 (insurance accounting policy change ~3.5× jump). SET API still reports 714.75 (the original 2567 audit). 
- **Lesson:** Pure auto-restatement using prior-column would produce 2,500.51 for AYUD — wrong. Restatement detection must be more nuanced (probably magnitude threshold or per-symbol opt-in).

### ✅ SCC — FIXED (filer-overwrote-zip, force-refresh recovered)
- **Symptom:** SCC 2564, 2565, 2567, 2568 all reported the SEPARATE-only "กำไรสำหรับปี" (parent-co. operating income, dominated by intercompany dividends from subs) instead of the CONSOLIDATED net profit attributable to parent that SET reports. Audit gap was huge — 2564 was 95,887 (ours) vs 47,174 (SET); SET100 doubled.
- **Root cause:** **The filer re-uploaded the zip at the SAME news URL after our initial download**. SCC's first upload for each FY mistakenly attached the SEPARATE statement under the "งบการเงินรวมประจำปี" (CONSO) news headline. SCC later corrected the zip — same URL, new content — but our pipeline dedupes by ``news_id`` so it never re-fetched. Local cache held the old (wrong) file forever.
- **Fix:** [scripts/force_refresh_zip.py](../../scripts/force_refresh_zip.py) — manual re-download per `(symbol, year, period)` that compares fresh sha256 against the metadata's recorded sha256 and overwrites zip + xlsx + metadata when they diverge. Restored CONSO data for SCC 2564-2568, all five years now match SET to the cent.
- **Lesson:** SHA-256 of a SET zip URL is **not stable** — filers can correct mistakes by re-uploading. Our content-addressing assumption was wrong. The ingest pipeline must periodically re-validate cached zips against the URL even when news_id matches.

### 📝 SCC pre-2566 originally only had "งบการเงินเฉพาะกิจการ" content
This entry kept for history; superseded by the FIXED entry above.
- **Symptom:** 2564 FY = 95,887 (ours) vs 47,174 (SET) — 2× too high.
- **Root cause:** SCC's FY zip ships only the SEPARATE financials (parent-only). The PL row `กำไรสำหรับปี` = 95,887M baht is dominated by intercompany dividends from SCG subsidiaries (which would be eliminated in a consolidated view). SET reports the consolidated number (47,174M), but the consolidated PL isn't in our XLSX — must be in a different filing or only on SET's website tables.
- **Workaround:** Need to fetch SCC's consolidated PL elsewhere (maybe the 56-1 form). Out of scope for the parser fix loop.

### 📝 THREL — restatement (SET uses dramatically restated 2567)
- **Symptom:** 2567 ours can't extract a value reliably (multi-sheet PL with mostly-zero rows); SET reports -578.60 MB. Earlier comprehensive-income fallback gave -83.43 (still wrong — that was the 3-month standalone, not FY).
- **Root cause:** THREL's 2567 FY filing has unusual layout (T8-9 with comprehensive-only, T10/T11 with mostly-zero matrices). 2568 FY filing's 2567 prior column shows -578.60. SET uses the restated value.
- **Why not auto-applied:** Same restatement-safety problem as KBANK — global override breaks other symbols.

### 📝 AEONTS — Feb fiscal year, label off by one
- **Symptom:** 2565 = 3,815 (ours) vs 3,553 (SET) — but values match if shifted by one year.
- **Root cause:** AEONTS uses Feb-end fiscal year. Their "FY 2565" filing covers year ending Feb 28, 2566; SET labels by calendar year of fiscal end (Buddhist 2566). Our pipeline labels by the headline's `ประจำปี 2565` text.
- **Affected symbols:** AEONTS, JMART, JMT, J, possibly others.
- **Fix shape:** In ingest, parse the actual fiscal year-end date from the XLSX header and re-key the row by `year_of_(end_date)` instead of trusting the headline's `ประจำปี` label.

### 📝 S&J / F&D / L&E — URL encoding bug (now fixed in code, data still bad)
- **Symptom:** S&J's local data is actually S (Singha Estate)'s data.
- **Root cause:** The `&` in the symbol got interpreted as a query-string delimiter when calling SET's news search API — `?symbol=S&J&...` was read as `symbol=S` followed by junk parameters.
- **Fix:** [src/ingest/set_api.py:_search_news_chunk](../../src/ingest/set_api.py) and `get_corporate_actions` now `urllib.parse.quote(symbol, safe="")` everywhere the symbol appears in URLs.
- **Action:** Re-ingest S&J / F&D / L&E after the bulk-ingest pass finishes (their existing folders need to be wiped first, otherwise the deduper won't refetch).

---

## Audit history

| Audit | OK | Mismatch | No-overlap | Notes |
|-------|---:|---------:|-----------:|-------|
| v1 | 441 | 117 | 62 | Initial state — caption + 2S fix only |
| v2 | 460 | 98 | 62 | Cross-sheet, year-filter, skip-ไม่เอา, TAS, BEAUTY regex |
| v3 | 463 | 95 | 62 | Lookahead, content-fallback, equity-exclude, dual-currency v1, ITC discontinued-ops |
| v4 | 461 | 98 | 62 | Comprehensive fallback (regression) |
| v5 | 461 | 98 | 62 | Year-filter before tier (regression) |
| v6 | 465 | 94 | 62 | Reverted comprehensive — strict mode best so far |
| v7 | 438 | 121 | 62 | Restatement pass (over-applied → reverted) |
| v8 | 475 | 96 | 61 | Reverted restatement; bulk-ingest started adding new symbols |
| v9 | (in progress) | | | parent-section logic for ITC + ANAN |
| v10 | (in progress) | | | + INTUCH inline-share matcher |
| v11 | 625 | 95 | — | year-MAX fix; bulk-ingest broader coverage |
| **SET100/100** | **100** | **0** | **0** | COM7+TOA+VGI ingested; MOSHI sara-am normalize; GPSC `(แก้ไข)` kept |
| **Universe/932** | **747** | **107** | **63 no_local + 4 no_overlap** | full audit after bulk round 2 — see "Round 3 follow-up" |

---

## Round 3 follow-up (universe/932 — 107 mismatches identified)

Bulk round 2 brought processed coverage to 932/932. Full audit
against SET highlights API: **747 OK / 107 mismatch / 63 no_local_data
/ 4 no_overlap**. SET100 stayed perfect (100/100). The 63 no_local_data
are REITs / property funds that SET highlights doesn't index the same
way (BAREIT, BTSGIF, CPNREIT, DIF, EGATIF, FTREIT, etc.) — not parser
bugs. The 107 mismatches cluster into three buckets, in priority order:

### A) `1000×` unit-divisor errors — 4 confirmed
`BTW 2568`, `QDC 2568`, `PLANET 2567`, `TNITY 2567` — local value is
exactly 1,000× the SET value (e.g. BTW local `-102,463.64` vs SET
`-102.46`). XLSX top-of-PL marker is `พันบาท` but `_detect_unit_divisor`
fell through to the baht default. **Fix candidate:** tighten the
detector so `พันบาท` near the column-header band always wins.

### B) Parser-locked-on-wrong-row — 2 confirmed
`BTC` reports `852.81` for every year (2564–2566); `FSX` reports `0.00`
for every year. These are PL sheets where the matcher latched onto a
constant row (probably a header/total line) instead of the net-profit
row. **Fix candidate:** verify the row-of-interest changes year over
year before accepting; if all years match the same number to the cent,
treat as a parser miss and re-search.

### C) Fiscal-year offset (`local[Y] == SET[Y-1]`) — ~6 symbols
`AF`, `JDF`, `KWM`, `OGC`, `CCP`, `AMANAH` — local shifts SET values
back by one Buddhist year. Same root cause as AEONTS (Feb fiscal end)
but for filers with a non-Dec fiscal year-end where SET adopts the
fiscal-START year as the label. Per-symbol fiscal-end metadata or a
universal "fiscal-end-year wins over headline year" rule would resolve
the family.

### D) Genuine restatements — long tail
The remaining ~95 are scattered single-year mismatches (~10–30% deltas)
that look like restated numbers (the SET API consistently uses the
next FY filing's prior-period column, our parser still has the
original number from the year's own filing). Same pattern as KBANK
2567 / MTI 2567 / SCGP 2566 etc. that we already handle via
`parsers/manual_overrides.json`. These need symbol-by-symbol XLSX dives
to confirm the SET-side number traces to a real cell.

**Universe audit JSON:** `data/validation/universe_audit_final.json`.

---

## Round 2 fixes (SET100 → 100/100)

### ✅ COM7 / TOA / VGI — FIXED (Windows reserved-name + ingest)
- **Symptom:** missing from data/processed entirely.
- **Root cause for COM7:** Windows reserves `COM1`–`COM9`/`LPT1`–`LPT9`/`CON`/`PRN`/`AUX`/`NUL` as device-file names. `mkdir data/raw/COM7` errors with `[WinError 267] The directory name is invalid`.
- **TOA / VGI:** simply weren't in any prior bulk-ingest watchlist — no parser bug.
- **Fix:** `safe_symbol_dir(sym)` helper in `src/ingest/zip_downloader.py` — appends `_` to reserved names (`COM7` → `COM7_` on disk). Symbol field in JSON stays `"COM7"`. Applied in `zip_downloader.py`, `src/cli/ingest_financials.py`, `src/cli/reparse_financials.py`, `command_handler.py` (reader), `scripts/verify_against_set.py` (audit).
- **Lesson:** any per-symbol filesystem path on Windows must run through this shim. Future readers (chart, monitor) might still need updates if they hit reserved names.

### ✅ MOSHI — FIXED (Thai SARA AM Unicode decomposition)
- **Symptom:** `[parse_zip] No PL sheet in source.zip` for all FY filings 2564–2568.
- **Root cause:** MOSHI's filer encodes Thai SARA AM as the decomposed pair NIKHAHIT (U+0E4D) + SARA AA (U+0E32) instead of the composed character SARA AM (U+0E33). `กำไรสุทธิสำหรับปี` and `กําไรสุทธิสําหรับปี` render identically but byte-compare unequal, so `_is_netprofit_row` regex misses the decomposed form. Sheet `TH 9-10` row 29 has the correct net-profit line `กําไรสุทธิสําหรับปี = 670,221,068` but `_has_pl_data` filtered the sheet out.
- **Fix:** `_normalize_thai_sara_am()` helper in `parsers/parse_set_zip.py` — replaces the `ํา` (NIKHAHIT+SARA AA) sequence with `ำ` (SARA AM) before any regex match. Applied inside `_find_label`, so all downstream label matchers benefit automatically. Unicode NFC normalization does NOT re-compose this pair (Thai SARA AM has no canonical decomposition mapping in the standard).
- **Lesson:** Thai source data may contain visually-identical but byte-different forms. Add normalization at the label-extraction boundary, not at every regex.

### ✅ GPSC 2568 — FIXED (keep `(แก้ไข)` corrections)
- **Symptom:** Q4 2568 = 0; FullYear 2568 mismatched SET (6,399.003).
- **Root cause:** `_is_amendment` was filtering out the `(แก้ไข)` corrected filing as if it were a clarification cover letter. Corrections carry replacement zips with new numbers; clarifications (`คำชี้แจง` / `ชี้แจงเพิ่มเติม`) carry only text.
- **Fix:** modify `_is_amendment` to keep `แก้ไข` + skip only pure-clarification keywords. Re-ingest GPSC → 2568 Q4 = 1,497.84 → FullYear = 6,399.003 ✓ matches SET.
- **Lesson:** SET often re-files with `(แก้ไข)` after audit findings or restatements; the new zip contains the canonical numbers. Treat them as primary, not noise.

### ✅ SCB 2567 Q1 — FIXED (`_latest_year_in_sheet` ignored money-row false-positive)
- **Symptom:** Annual FullYear matched SET (audit OK), but Q1 2567 was missing from `quarterly_history`. The 2567/Q1 filing was downloaded into raw but the parser logged `[parse_zip] No PL sheet in source.zip`.
- **Root cause:** in the SCB Q1 workbook, the **CF (cash flow) sheet row 12 column 11** has cell value `2589` — that's `ค่าเสื่อมราคา` (depreciation) of 2,589 thousand baht for the separate-financials prior-period column. The integer `2589` falls inside the Buddhist year range `[2540, 2600]`, so `_latest_year_in_sheet` mis-classified it as "year 2589" → workbook_latest = 2589 → real PL sheet (year 2567) was filtered out as "stale" since 2567 < 2589.
- **Fix:** in `_latest_year_in_sheet`, skip year-detection on rows that contain any number with `abs > 9999`. Year header rows in SET filings always contain ONLY year-shaped integers and short strings; data rows contain money figures. The `has_money` row-level filter rules out depreciation/balance/whatever happens to fall in `[2540, 2600]` while preserving every legitimate header-row year detection.
- **Lesson:** when scanning numeric cells for "year-shaped" values, always pair with a row-level "is this a header row?" guard. False positives from money figures in the Buddhist year range are easy to miss because the audit only checks FullYear (which can match via OTHER filings' data), masking individual quarter losses.

---

## Round 3 fixes (post-bulk universe audit — Pattern A: stacked-block units)

### ✅ BTW 2568 — FIXED (stacked-block PL with two different units)
**Role-model symbol for: dual-block PL sheet where the parser must apply DIFFERENT divisors to the quarterly block (top) and the annual block (bottom).**

- **Symptom:** SET reports 2568 = -102.46 MB; local reported -102,463.64 (exactly 1,000× too big).
- **Root cause:** the audited FY 2568 zip's `PL_T` sheet stacks **two PL blocks**:
  - Block 1 (rows 1-58): three-month standalone "งวดสามเดือนสิ้นสุด...30 กันยายน 2568" with unit `พันบาท` (divisor 1,000).
  - Block 2 (rows 60-140): annual "สำหรับปีสิ้นสุดวันที่ 31 ธันวาคม" with unit `บาท` (divisor 1,000,000).

  `_detect_unit_divisor` only scans the top 15 rows so it locked onto `พันบาท` and applied divisor 1,000 to BOTH blocks — the annual figure (-102,463,639 baht) was divided by 1,000 instead of 1,000,000.

- **Fix:** added `_build_unit_divisor_map(ws)` and `_divisor_for_row(unit_map, row_idx, default)`. The map records every `(row index, divisor)` seen anywhere in the sheet; the lookup returns the most recent marker at-or-above the row being parsed. Wired into both:
  1. The main per-row loop — each shareholder/net-profit row uses its own block's divisor.
  2. The `shareholder_profit_cum` extractor (Layout 2 same-sheet break) — re-detects the divisor from rows around the transition before re-extracting.

  Plus: relaxed the "first wins" guard so a later, *higher-precision* shareholder row (larger divisor → annual block) overrides an earlier quarterly-block hit. Same-or-lower divisor still keeps original semantics, so single-block sheets are unaffected.

- **Lesson:** SET filings can stack multiple PL blocks per sheet, each with its OWN unit marker. Top-of-sheet detection is unsafe whenever stacked blocks exist. The `unit_map` approach generalises to any future stacked layout. Verify by reparsing — if the FY filing's `shareholder_profit` ends up exactly 1,000× the SET value, this pattern is the suspect.

### ✅ QDC 2568 — FIXED (same stacked-block pattern as BTW)
**Role-model symbol for: same shape as BTW, validates the parser fix generalises.**

- **Symptom:** local 2568 = -111,482.38; SET = -111.48. Exactly 1,000× off.
- **Root cause:** identical to BTW — single PL sheet with quarterly block (พันบาท) above annual block (บาท).
- **Fix:** automatic via the BTW `_build_unit_divisor_map` change; no per-symbol code.

### ✅ TNITY 2567 — FIXED (cumulative extractor used wrong divisor)
**Role-model symbol for: the `shareholder_profit_cum` path in Layout 2 needs its own divisor detection.**

- **Symptom:** local 2567 = -353.40; SET = 0.51 (small positive number). The 1,000× pattern hid because `compute_standalone_quarters` derives FullYear from cumulative values, not the raw shareholder_profit.
- **Root cause:** `_extract_shareholder_from_rows(ws, transition, ws.max_row, unit_divisor)` used the top-of-sheet `unit_divisor`, but for stacked layouts the cumulative section sits in a different unit block.
- **Fix:** call `_detect_unit_divisor(...)` on rows around the transition before invoking the cumulative extractor. Resolved automatically alongside BTW.

### 🔧 PLANET 2567 — DEFERRED (multi-PL-sheet, current-year column blank)
**Role-model symbol for: filer ships TWO PL sheets where one has only prior-year data populated; parser picks the wrong one.**

- **Symptom:** SET reports 2567 = -49.45; local = -207,220.83. Suspiciously similar to the 2566 value (-207.22), suggesting a column or sheet shift.
- **Root cause:** the FY 2567 zip ships sheets `P` and `P8`, both labelled "สำหรับปีสิ้นสุดวันที่ 31 ธันวาคม". Sheet `P` has the 2567 column **all-zero** (col 4) and the 2566 column populated (col 5); sheet `P8` has all four columns properly populated. Our parser picks `pl_sheets[0]` = `P` and `_extract_numeric` skips the zeros → ends up reading the 2566 prior-year value as if it were 2567.
- **Fix candidate:** when multiple PL sheets share the same period, prefer the one whose shareholder/net-profit row has a non-zero value in the *current-year* column. Or: deprioritise sheets where >80 % of numeric cells are zero (template/empty sheet).
- **Open follow-up:** generalises to any filing that ships a "skeleton" sheet alongside the real one.
