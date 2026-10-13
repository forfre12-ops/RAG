"""후보 풀(_combined_no_kl_no_patent_proxy.jsonl)을 학습·시험에 쓰기 전에 무엇이 들어 있는지 센다.

질문(2026-09-21): "이 25,293건을 임시 학습·성능시험 풀로 써도 되나?" — 행 수만으로는 답이 안 된다.
센 것: 고유 본문 수 · 배포모델 학습셋(v5_clean)·holdout109·golden100 v3.0·검수 후보(synth_v3)와의 본문 완전일치 ·
       등급명 노출 · 판결문 서식(등급별) · 출처별 구성.
안 센 것: 근접 중복(글자 유사도) — 25k 전수 쌍 비교라 무겁다. 동일 본문만 본다(하한).

    poc/.venv/Scripts/python.exe -X utf8 scripts/audit_candidate_pool.py [풀.jsonl]
"""

from __future__ import annotations

import collections
import hashlib
import json
import sys
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))
sys.path.insert(0, str(POC / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")

from audit_phase1_quality import COURT  # noqa: E402
from koipa.services.synth_quality import _exposes_grade_token  # noqa: E402

DEFAULT_POOL = "datasets/new_build_candidates_2026-09-17/_combined_no_kl_no_patent_proxy.jsonl"
REFS = {
    "v5_clean(배포모델 학습셋 2,554)": [f"datasets/labeled_p1_v5_clean/{x}.jsonl" for x in ("train", "val", "test")],
    "synth_v3 1,000(검수 후보)": ["datasets/labeled_synth_v3_selfconsistent/all_1000_before_filter.jsonl"],
    "holdout109": ["datasets/gold_real/holdout_eval.jsonl"],
    "golden100 v3.0": ["datasets/gold/golden100_labeled_v3.jsonl"],
}


def sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def text_of(r: dict) -> str:
    return r.get("text") or r.get("body") or r.get("content") or ""


def read_jsonl(path: Path) -> list[dict]:
    out = []
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def main() -> None:
    pool_path = POC / (sys.argv[1] if len(sys.argv) > 1 else DEFAULT_POOL)
    rows = read_jsonl(pool_path)
    n = len(rows)
    hashes = [sha(text_of(r)) for r in rows]
    uniq = set(hashes)
    print(f"[풀] {pool_path.name}: {n}행, 고유 본문 {len(uniq)}건 (같은 본문이 겹친 행 {n - len(uniq)})")
    print("  등급", dict(collections.Counter(r.get("label") or r.get("grade") for r in rows)))
    no_src = sum(1 for r in rows if not r.get("source"))
    print(f"  출처(source) 필드 없는 행 {no_src} ({no_src / n:.1%})")

    print("\n[다른 세트와 본문 완전일치(고유 본문 기준)]")
    for name, paths in REFS.items():
        ref: set[str] = set()
        found = False
        for p in paths:
            f = POC / p
            if f.exists():
                found = True
                ref |= {sha(text_of(r)) for r in read_jsonl(f) if text_of(r)}
        print(f"  {name}: " + (f"{sum(1 for h in uniq if h in ref)}건" if found else "파일 없음"))

    exposed = [_exposes_grade_token(text_of(r)) for r in rows]
    court = [bool(COURT.search(text_of(r))) for r in rows]
    print(f"\n[결함] 등급명 노출 {sum(exposed)} ({sum(exposed) / n:.1%}) · 판결문 서식 {sum(court)} ({sum(court) / n:.1%})")
    print("  판결문 서식 행의 등급", dict(collections.Counter((r.get('label') or r.get('grade')) for r, c in zip(rows, court) if c)))
    by_src: dict = collections.defaultdict(lambda: [0, 0, 0])
    for r, e, c in zip(rows, exposed, court):
        g = by_src[r.get("source")]
        g[0] += 1
        g[1] += e
        g[2] += c
    print("  출처별(건수·등급명 노출·판결문 서식)")
    for src, (k, e, c) in sorted(by_src.items(), key=lambda x: -x[1][0])[:10]:
        print(f"    {src}: {k}건 · 노출 {e / k:.0%} · 판결문 {c / k:.0%}")


if __name__ == "__main__":
    main()
