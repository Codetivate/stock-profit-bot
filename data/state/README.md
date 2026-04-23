# State files

Stateful cursors the pipeline needs between runs.

| File | Committed? | Why |
|------|------------|-----|
| `news_cursor.json`       | Yes — GitHub Actions commits it back | Shared between local + cron runs |
| `telegram_offset.json`   | Yes — same reason as above | Prevents re-processing old commands |
| `user_approvals.json`    | Yes — distributed sync              | Admin approvals persist across runs |
| `broadcast_processed.json` | Yes — same reason                  | Prevents re-broadcasting same filing |

Anything here that contains secrets or PII should be gitignored; see
`../../.gitignore`.
