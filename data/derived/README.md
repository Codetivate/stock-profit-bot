# Derived artifacts

Regenerable outputs built from `processed/`. Gitignored because they're large
and can be rebuilt anytime.

Layout:
```
derived/
└── {SYMBOL}/
    ├── charts/
    │   └── {YEAR}_{PERIOD}.png     # e.g. 2568_Q4.png — what Telegram posts
    └── excel/
        └── summary.xlsx            # per-stock Excel view
```

Rebuild everything: `python -m src.cli.rebuild --all`
Rebuild one symbol: `python -m src.cli.rebuild --symbol CPALL`
