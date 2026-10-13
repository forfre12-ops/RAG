# Archive Index

This file is intentionally kept small and tracked. The archived payloads under
`doc/archive/` are not tracked by Git because they are superseded copies,
intermediate exports, or temporary files.

## legacy_20260710

Created on 2026-07-10 to consolidate scattered backup and dated export folders.

Moved into `doc/archive/legacy_20260710/`:

- `doc/bak/`
- `doc/result/bak/`
- `doc/result/open/bak/`
- `doc/result/open/_superseded/`
- `doc/result/open/20260623/`
- `doc/result/open/20260629/`
- `doc/result/open/20260705/`
- `doc/result/open/real/`
- Office lock/temp files from `doc/result/open/` into `trash_candidates/`

Policy:

- Do not use `bak`, date folders, or `_superseded` under active document paths.
- Use Git history/tags for normal rollback.
- Use `doc/releases/<release-id>/` for customer submission snapshots.
- Use `doc/archive/<reason-date>/` only for non-current material that must be retained outside normal Git history.
