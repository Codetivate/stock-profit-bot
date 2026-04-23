# Raw data (immutable)

Source zips and attached metadata as received from SET. **Never edit files
here.** If source data is wrong, file a note under `../validation/{SYMBOL}/`.

Gitignored because it's large (800 symbols × ~30 filings × ~500 KB ≈ 10 GB
when fully loaded). See [../../ARCHITECTURE.md](../../ARCHITECTURE.md) §5 for
the storage strategy and the trigger to migrate to cloud.

Layout:
```
raw/
└── {SYMBOL}/
    ├── financials/
    │   └── {THAI_YEAR}/
    │       └── {PERIOD}/              # Q1 | H1 | 9M | FY
    │           ├── source.zip         # original from weblink.set.or.th
    │           ├── source.xlsx        # extracted for convenience
    │           └── metadata.json      # url, sha256, ingested_at
    └── announcements/
        └── {YYYY-MM-DD}_{news_id}/
            ├── news.html
            ├── attachments/
            └── metadata.json
```
