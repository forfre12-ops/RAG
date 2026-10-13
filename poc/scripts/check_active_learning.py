"""
Active Learning 트리거 — corrections 누적 현황 확인 및 재학습 권고.

corrections 테이블의 unconsumed 정정 건수를 확인하여:
  - N건 이상 누적 시: 재학습 권고 출력
  - 정정 방향 분포 (underclass/overclass) 리포트
  - 최근 정정 샘플 표시

사용:
  python scripts/check_active_learning.py
  python scripts/check_active_learning.py --threshold 50
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DEFAULT_THRESHOLD = 30  # 이 건수 이상이면 재학습 권고


def check_db_corrections(threshold: int) -> dict:
    """DB corrections 테이블 조회."""
    from koipa.db import SessionLocal
    from koipa.repositories.classify_repo import ClassifyRepo

    db = SessionLocal()
    try:
        repo = ClassifyRepo(db)
        unconsumed = repo.unconsumed_corrections()
        recent_count = repo.count_recent_corrections(limit_days=7)
        directions = Counter(c.direction for c in unconsumed)
        samples = [
            {
                "direction": c.direction,
                "corrected_by": c.corrected_by,
                "corrected_at": str(c.corrected_at)[:10],
            }
            for c in unconsumed[:5]
        ]
        return {
            "source": "db",
            "total_unconsumed": len(unconsumed),
            "recent_7days": recent_count,
            "direction_dist": dict(directions),
            "samples": samples,
            "recommend_retrain": len(unconsumed) >= threshold,
        }
    except Exception as exc:
        return {"source": "db", "error": str(exc), "total_unconsumed": 0}
    finally:
        db.close()


def check_file_corrections(threshold: int) -> dict:
    """corrections_master.jsonl 파일 기반 조회 (DB 없을 때 fallback)."""
    master = Path("datasets/corrections/corrections_master.jsonl")
    if not master.exists():
        return {"source": "file", "total": 0, "message": "corrections_master.jsonl 없음"}

    recs = [json.loads(l) for l in master.read_text(encoding="utf-8").splitlines() if l.strip()]
    corrected = [r for r in recs if r.get("review_decision") == "corrected"]
    directions = Counter(
        ("underclass" if r.get("human_label", "S3") < r.get("model_label", "S3") else "overclass")
        for r in corrected
    )
    return {
        "source": "file",
        "total_corrections": len(recs),
        "corrected_count": len(corrected),
        "direction_dist": dict(directions),
        "recommend_retrain": len(corrected) >= threshold,
        "retrain_command": (
            "python scripts/p1_train_classifier.py --mode full "
            "--train-path datasets/labeled_v2_balanced/train.jsonl "
            "--no-mlflow --output-dir artifacts/classifier_active_v1"
        ) if len(corrected) >= threshold else None,
    }


def print_report(result: dict, threshold: int) -> None:
    print("\n=== Active Learning 트리거 현황 ===\n")

    if result.get("error"):
        print(f"[DB] 접근 실패: {result['error']}")
        print("[INFO] 파일 기반 corrections 확인 중...\n")
        return

    source = result.get("source", "?")
    total = result.get("total_unconsumed", 0) or result.get("corrected_count", 0)
    recent = result.get("recent_7days", "-")
    directions = result.get("direction_dist", {})
    recommend = result.get("recommend_retrain", False)

    print(f"출처: {source}")
    print(f"미소비 정정: {total}건 (임계값: {threshold}건)")
    if recent != "-":
        print(f"최근 7일: {recent}건")
    print(f"방향 분포: {directions}")
    print()

    if recommend:
        print("★ 재학습 권고 ★")
        print(f"  정정 건수 {total}건이 임계값 {threshold}건을 초과했습니다.")
        print()
        print("  권장 재학습 커맨드:")
        cmd = result.get("retrain_command") or (
            "make p1 (또는 python scripts/p1_train_classifier.py --mode full ...)"
        )
        print(f"  {cmd}")
    else:
        remaining = max(0, threshold - total)
        print(f"재학습 불필요 (임계값까지 {remaining}건 더 필요)")

    if result.get("samples"):
        print("\n  최근 정정 샘플:")
        for s in result["samples"]:
            print(f"    {s.get('corrected_at','?')} | {s.get('direction','?')} | by {s.get('corrected_by','?')}")

    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                    help=f"재학습 권고 임계값 (기본 {DEFAULT_THRESHOLD}건)")
    ap.add_argument("--file-only", action="store_true", help="DB 연결 없이 파일만 확인")
    args = ap.parse_args()

    result = {}
    if not args.file_only:
        result = check_db_corrections(args.threshold)
        if result.get("error"):
            print(f"[INFO] DB 연결 실패 ({result['error'][:60]}...) — 파일 fallback")
            result = check_file_corrections(args.threshold)
    else:
        result = check_file_corrections(args.threshold)

    print_report(result, args.threshold)

    # Makefile에서 exit code로 재학습 필요 여부 판단 가능
    return 0 if not result.get("recommend_retrain") else 1


if __name__ == "__main__":
    raise SystemExit(main())
