# Document Control

## Purpose

This repository now treats documents as controlled deliverables, not loose files.
The goal is to make it clear which files are current, which are customer-facing,
and which files are historical or generated.

## Canonical Locations

- `doc/docs_registry.yaml`: document register and source of truth.
- `doc/result/open/`: current customer/shareable HTML deliverables.
- `doc/internal/`: internal review documents.
- `doc/assets/` and `doc/result/open/assets/`: shared document assets.
- `doc/releases/<release-id>/`: frozen customer submission bundles.
- `doc/archive/<reason-date>/`: retained legacy material only.

## Status Values

- `draft`: still being authored.
- `review`: under review.
- `current`: current working deliverable.
- `released`: frozen submission copy.
- `superseded`: replaced by a newer current document.
- `archived`: retained for traceability, not for use.
- `trash-candidate`: safe to delete after owner approval.

## Naming Rules

Do not create active files or folders with:

- `bak`, `backup`, `_bak`
- `copy`, `old`, `temp`, `tmp`
- `복사`, `임시`
- Office lock files such as `~$...`
- date-only folders such as `20260705` under `doc/result/open/`

Use the registry `version` and Git tags instead of filename suffixes like
`최종`, `final`, or ad hoc backup copies.

## Release Rule

When a customer-facing set is sent out, create:

```text
doc/releases/<YYYY-MM-purpose>/
  manifest.yaml
  index.html
  ...
```

The release folder is the frozen submission. Active documents may continue to
change after that.

## Audit

Run:

```bash
python scripts/audit_docs.py
```

The audit fails on missing registered current/released files, duplicate document
IDs, temporary lock files in active folders, and backup/date folders outside
`doc/archive/`.
