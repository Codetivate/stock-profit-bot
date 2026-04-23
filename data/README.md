# Data layout

See [../ARCHITECTURE.md](../ARCHITECTURE.md) for the rationale. Quick map:

```
data/
├── raw/            # immutable source from SET — gitignored
├── processed/      # parsed, committed
├── derived/        # charts/xlsx generated from processed — gitignored
├── validation/     # integrity reports — committed
└── state/          # cursors + offsets — partial commit (see .gitignore)
```

**Finding data for CPALL?** Start at `processed/CPALL/financials.json`.
Everything else is either raw source (for validation) or regenerable output.
