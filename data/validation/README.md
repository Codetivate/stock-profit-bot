# Validation reports

Integrity checks run after every parse. Schema at
[../../reference/data_schemas/validation.schema.json](../../reference/data_schemas/validation.schema.json).

Layout:
```
validation/
└── {SYMBOL}/
    ├── latest.json           # most recent run — quick read
    └── history/
        └── {YYYY-MM-DD}_{PERIOD}.json
```

**Publish gate.** `derived/charts/` and Telegram sends should refuse to
publish a filing whose `latest.json` shows `"passed": false`. See
[../../ARCHITECTURE.md](../../ARCHITECTURE.md) §7.
