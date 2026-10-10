#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""검수 후보 전달본 하나를 현재 전문가 검수 배치로 지정한다.

후보를 새로 만들거나 지우지 않는다. 각 ``*.metadata.json``의
``review_batch_active`` 표식만 원자적으로 갱신한다. 관리자 콘솔은 이 표식을 읽어
과거 전달본을 임의로 기본 선택하지 않고, 이번 검수에 열어 둔 배치를 기본 범위로 삼는다.

사용 예:
  python scripts/set_active_review_batch.py --batch mock1000_scaleup_20261007 --expected-count 1001
  python scripts/set_active_review_batch.py --batch expert_review_product_1000_20261010_rf --expected-count 1000
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

try:
    from _cli_io import force_utf8_stdio
except ImportError:
    from scripts._cli_io import force_utf8_stdio


force_utf8_stdio()

POC = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = POC / "datasets" / "proxy_gold" / "single_document_candidates"


def _atomic_json(path: Path, payload: dict) -> None:
    """Replace one metadata file atomically; a stopped run never leaves partial JSON."""
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    ) as handle:
        handle.write(encoded)
        temp_name = handle.name
    try:
        os.replace(temp_name, path)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True, help="현재 전문가 검수에 열 배치 식별자")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="후보 metadata 폴더")
    parser.add_argument("--expected-count", type=int, default=0, help="대상 문서 수 사전 확인(0=미검사)")
    parser.add_argument("--dry-run", action="store_true", help="검사만 하고 metadata를 쓰지 않음")
    args = parser.parse_args()

    batch = args.batch.strip()
    root = args.root.resolve()
    if not batch:
        parser.error("--batch must not be blank")
    if not root.is_dir():
        parser.error(f"candidate root does not exist: {root}")

    rows: list[tuple[Path, dict]] = []
    errors: list[str] = []
    for path in sorted(root.glob("*.metadata.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.name}: {type(exc).__name__}")
            continue
        if not isinstance(payload, dict):
            errors.append(f"{path.name}: metadata is not an object")
            continue
        rows.append((path, payload))
    if errors:
        print("중단: metadata를 읽지 못한 파일이 있습니다.", file=sys.stderr)
        print("\n".join(errors[:20]), file=sys.stderr)
        return 2

    counts = Counter(str(payload.get("review_batch") or "").strip() for _, payload in rows)
    counts.pop("", None)
    target_count = counts.get(batch, 0)
    if not target_count:
        print(f"중단: 배치 '{batch}'가 후보 metadata에 없습니다.", file=sys.stderr)
        print("발견한 배치: " + ", ".join(f"{key}({value})" for key, value in sorted(counts.items())), file=sys.stderr)
        return 2
    if args.expected_count and target_count != args.expected_count:
        print(
            f"중단: 대상 배치 {target_count}건 != --expected-count {args.expected_count}건입니다.",
            file=sys.stderr,
        )
        return 2

    changes = [
        (path, payload, str(payload.get("review_batch") or "").strip() == batch)
        for path, payload in rows
        if str(payload.get("review_batch") or "").strip()
    ]
    already = sum(1 for _, payload, active in changes if bool(payload.get("review_batch_active")) == active)
    print(f"대상: {batch} · {target_count}건 · 전체 배치 {len(counts)}개 · 변경 필요 {len(changes) - already}개")
    if args.dry_run:
        print("--dry-run: 파일을 변경하지 않았습니다.")
        return 0

    for path, payload, active in changes:
        if bool(payload.get("review_batch_active")) == active:
            continue
        payload["review_batch_active"] = active
        _atomic_json(path, payload)
    print("완료: 현재 검수 배치 표식을 갱신했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
