"""G-track '마지막 1마일' 실가동 — split → tier/readiness → signoff (진입점 부재 갭 해소).

배경: golden_split / golden_tiers / golden_signoff 는 라이브러리로만 존재하고 **어떤 run에도
실행된 적이 없다**(golden_runs/* 어디에도 silver_train.jsonl / synthetic_holdout.jsonl /
split_stats.json 이 없음 — 완성도 감사 P1). 이 CLI 는 그 3단을 run-스코프로 한 번 돌려:

  1) split_run_dir : build_*.jsonl(gold_candidate) → silver_train / synthetic_holdout (등급층화
     75/25, 결정적) + 누출 가드(eval holdout·정본 gold 중복 드롭). run-dir에 산출물 기록.
  2) tier/readiness: 파생 tier 분포 + eval_readiness(locked 충분한가). 무실데이터 단계에선
     locked=0 → ready=False 여야 정상.
  3) signoff     : promote_to_locked. 사람 서명이 없으면(현재 상태) **전건 거부(no_signoff)**여야
     한다 = locked_gold 는 사람 서명 없이는 절대 안 생김(안전 속성 실증). --signoffs 로 실서명
     jsonl(doc_id/reviewer_id/grade/signed_at)을 주면 그때 승격. 승격된 locked 레코드 자체를
     run-스코프 locked_gold_eval.jsonl 로 **기록**한다(과거엔 통계만 쓰고 레코드는 버려
     readiness 읽기경로가 영영 no_locked_records였다 — last-mile 단절 해소).

  (publish) --publish-locked : 승격 locked 레코드를 운영 readiness **읽기경로**
     (settings.locked_eval_jsonl 또는 --locked-eval-path)에 doc_id dedup 누적 병합한다.
     그러면 locked_eval_readiness / deploy gate 가 사람 서명을 실제로 보게 되어, 서명이
     충분히 쌓이면 readiness=ready 로 자동 전환된다(명시 opt-in — 운영 배포 게이트 소비).

정본(classification_gold.jsonl) 미변경 — run-스코프 출력 + (opt-in) 읽기경로 누적. 결정적·LLM 불요.

사용:
  python scripts/run_golden_split_signoff.py datasets/golden_runs/e946233b3662
  python scripts/run_golden_split_signoff.py <run_dir> --signoffs signoffs.jsonl
  python scripts/run_golden_split_signoff.py <run_dir> --signoffs s.jsonl --publish-locked
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from lloydk.golden_signoff import (  # noqa: E402
    Signoff,
    merge_locked_records,
    promote_to_locked,
)
from lloydk.golden_split import split_run_dir  # noqa: E402
from lloydk.golden_tiers import eval_readiness, partition_by_tier  # noqa: E402

# 누출 가드 대상(silver 가 eval/정본과 본문 겹치면 드롭) — eval holdout·정본 gold.
DEFAULT_GUARD = [
    "datasets/gold_real/holdout_eval.hardened.jsonl",
    "datasets/gold_real/holdout_eval.clean.jsonl",
    "datasets/gold_real/holdout_business.clean.jsonl",
    "datasets/gold_real/classification_gold.jsonl",
]


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _write_jsonl(path: Path, records: list[dict]) -> None:
    """원자적 jsonl 기록(tmp→replace) — 부분 기록으로 읽기경로가 깨지지 않게."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(path)


def _publish_locked(new_locked: list[dict], override_path: str | None) -> None:
    """승격 locked 레코드를 운영 readiness 읽기경로에 doc_id dedup 누적 병합(opt-in)."""
    dest = override_path
    if dest is None:
        from lloydk.config import settings  # noqa: PLC0415

        dest = getattr(settings, "locked_eval_jsonl", "") or ""
    if not dest:
        print(
            "[publish] ⚠️ 대상 경로 없음 — settings.locked_eval_jsonl 미설정 & --locked-eval-path "
            "미지정. publish 건너뜀(읽기경로 무변경).",
            file=sys.stderr,
        )
        return
    if not new_locked:
        print("[publish] locked 0건 — 읽기경로 무변경(no-op). 사람 서명이 없으면 정상.")
        return

    from lloydk.modules.m6_evaluation.locked_readiness import (  # noqa: PLC0415
        locked_eval_readiness,
    )

    dest_path = Path(dest)
    existing = _load_jsonl(dest_path)
    merged = merge_locked_records(existing, new_locked)
    _write_jsonl(dest_path, merged)
    ready = locked_eval_readiness(path=str(dest_path))
    print(
        f"[publish] {dest_path} ← locked {len(new_locked)}건 병합(총 {len(merged)}건). "
        f"readiness: ready={ready['ready']} per_grade={ready['per_grade']} "
        f"reason={ready['reason']}"
    )


def _load_signoffs(path: Path | None) -> list[Signoff]:
    if not path or not path.exists():
        return []
    out = []
    for r in _load_jsonl(path):
        out.append(
            Signoff(
                doc_id=r["doc_id"], reviewer_id=r["reviewer_id"], grade=r["grade"],
                signed_at=r.get("signed_at", ""), note=r.get("note", ""),
            )
        )
    return out


def main() -> int:
    # Windows 콘솔(cp949)에서 한글·기호(— 등 비-cp949) print가 UnicodeEncodeError로 죽지 않게
    # stdout/stderr를 UTF-8로 재설정(가능한 환경에서만; 리다이렉트/래핑 시 무시).
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", help="golden_runs/<run_id> (build_*.jsonl 포함)")
    ap.add_argument("--signoffs", default=None, help="사람 서명 jsonl(없으면 게이트가 전건 거부 실증)")
    ap.add_argument("--holdout-frac", type=float, default=0.25)
    ap.add_argument("--guard", nargs="*", default=DEFAULT_GUARD, help="누출 가드 코퍼스 경로")
    ap.add_argument(
        "--publish-locked",
        action="store_true",
        help="승격 locked 레코드를 운영 readiness 읽기경로(locked_eval_jsonl)에 dedup 누적 병합",
    )
    ap.add_argument(
        "--locked-eval-path",
        default=None,
        help="publish 대상 경로 override(기본 settings.locked_eval_jsonl)",
    )
    args = ap.parse_args()

    rd = Path(args.run_dir)
    if not list(rd.glob("build_*.jsonl")):
        print(f"[ERROR] build_*.jsonl 없음: {rd}", file=sys.stderr)
        return 1

    # ── 1) split ──
    split = split_run_dir(rd, existing_corpus_paths=args.guard, holdout_frac=args.holdout_frac)
    print("[1/3 split]", json.dumps(split.to_dict(), ensure_ascii=False))
    print("        → silver_train.jsonl / synthetic_holdout.jsonl / split_stats.json 기록됨")

    # ── 2) tier / readiness ── (build 후보 기준)
    build_records = []
    for gf in sorted(rd.glob("build_*.jsonl")):
        build_records += _load_jsonl(gf)
    tiers = {k: len(v) for k, v in partition_by_tier(build_records).items()}
    ready = eval_readiness(build_records)
    print("[2/3 tier ]", json.dumps(tiers, ensure_ascii=False))
    print("[2/3 ready]", json.dumps(ready, ensure_ascii=False))

    # ── 3) signoff ──
    signoffs = _load_signoffs(Path(args.signoffs) if args.signoffs else None)
    res = promote_to_locked(build_records, signoffs)
    print("[3/3 signoff]", json.dumps(res.stats, ensure_ascii=False))
    with (rd / "signoff_result.json").open("w", encoding="utf-8") as f:
        json.dump(
            {"stats": res.stats, "n_signoffs": len(signoffs), "locked": len(res.locked)},
            f, ensure_ascii=False, indent=2,
        )

    # 승격된 locked 레코드 **자체**를 run-스코프로 기록(과거엔 통계만 쓰고 레코드는 버려
    # readiness 읽기경로가 영영 no_locked_records였다 — last-mile 단절). 0건이어도 파일 생성(진실).
    locked_run_path = rd / "locked_gold_eval.jsonl"
    _write_jsonl(locked_run_path, res.locked)
    print(f"        → {locked_run_path.name} 기록됨 (locked {len(res.locked)}건)")

    # 선택(opt-in): 운영 readiness 읽기경로에 dedup 누적 publish — deploy gate/게이지가 소비.
    if args.publish_locked:
        _publish_locked(res.locked, args.locked_eval_path)

    # ── 안전 속성 검증 ──
    if not signoffs:
        assert res.stats["locked"] == 0, "사람 서명 0인데 locked>0 — 게이트 파손!"
        assert not ready["ready"], "locked 0인데 eval_readiness ready=True — readiness 게이트 파손!"
        print("\n✅ 안전 속성 확인: 사람 서명 0 → locked_gold 0 (게이트 유지), eval_readiness=False.")
        print("   = split은 학습 시드(silver) 산출, 평가 정답(locked)은 사람 서명 없이 절대 안 생김.")
    else:
        print(f"\n[signoff] {len(signoffs)}건 서명 → locked {res.stats['locked']}건 승격.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
