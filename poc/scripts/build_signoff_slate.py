"""실서명 스프린트 슬레이트 빌더 (결정적) — 검수 화면 서명(signoff.html) 연결용.

커밋된 build 파일(datasets/golden_runs/82cbdda1787b, 627건 clean: 학습누출0·평가중복0·
koipa/nkt 오염0)에서 등급별 목표 수량·도메인 다양성으로 서명 대기 슬레이트를 선별해
datasets/golden_runs/signoff_slate_v1/build_signoff_slate_v1.jsonl 로 쓴다.

운영: 이 파일을 POST /golden/jobs/register 로 골든 잡에 등록하면(재라벨링 없이 라벨 보존)
GET /golden/jobs/{id}/signoff.html 에서 검수자가 34건을 화면 클릭 서명한다.

품질 필터: agreement==True & self_consistency>=0.67 & shadow 불일치 없음 & 위험 플래그 없음
(풀 전체가 이미 충족 — 방어적 재확인). 정렬: 근거 있는 항목 우선 → llm_confidence 내림차순.
선택: 등급별 도메인 라운드로빈(편중 방지). 무작위 없음 — 재현 가능.

사용: python scripts/build_signoff_slate.py
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
# 입력 풀은 golden_runs(로컬 run 아티팩트, gitignore). 출력 슬레이트는 tracked 위치에 둬
# 커밋·durable 하게 한다(golden_runs 는 gitignore 라 커밋 안 됨 → 파일럿서 evaporate). 합성
# 아티팩트를 커밋하는 선례=datasets/acceptance_pack/.
POOL = _POC / "datasets" / "golden_runs" / "82cbdda1787b" / "build_82cbdda1787b.jsonl"
OUT_DIR = _POC / "datasets" / "signoff_slate"
OUT = OUT_DIR / "build_signoff_slate_v1.jsonl"

TARGET = {"TS": 10, "S1": 10, "S2": 7, "S3": 7}
BAD_FLAGS = {
    "ts_downgrade_suspect", "production_suspect", "airgap_blocked_upper", "low_agreement",
}


def _load(p: Path) -> list[dict]:
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _ok(r: dict) -> bool:
    if r.get("agreement") is not True:
        return False
    if float(r.get("self_consistency", 0)) < 0.67:
        return False
    sg = r.get("shadow_grade")
    if sg is not None and sg != r.get("label"):
        return False
    if set(r.get("flags") or []) & BAD_FLAGS:
        return False
    return True


def _sort_key(r: dict):
    has_ev = 0 if "no_evidence" in (r.get("flags") or []) else 1
    return (-has_ev, -float(r.get("llm_confidence", 0)))


def build_slate() -> list[dict]:
    pool = _load(POOL)
    selected: list[dict] = []
    for g, n in TARGET.items():
        cands = [r for r in pool if r.get("label") == g and _ok(r)]
        by_dom: dict[str, list[dict]] = defaultdict(list)
        for r in sorted(cands, key=_sort_key):
            by_dom[r.get("domain")].append(r)
        doms = sorted(by_dom, key=lambda d: -len(by_dom[d]))
        picked: list[dict] = []
        di = 0
        while len(picked) < n and any(by_dom[d] for d in doms):
            d = doms[di % len(doms)]
            if by_dom[d]:
                picked.append(by_dom[d].pop(0))
            di += 1
        selected.extend(picked)
    return selected


def main() -> int:
    if not POOL.exists():
        print(f"[ERROR] 풀 없음: {POOL}")
        return 1
    selected = build_slate()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for r in selected:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    by_grade = Counter(r.get("label") for r in selected)
    print(f"[slate] {len(selected)}건 → {OUT.relative_to(_POC)}")
    print(f"        등급분포: {dict(by_grade)}")
    print(
        "        register: POST /golden/jobs/register "
        f'{{"build_path": "{OUT.relative_to(_POC).as_posix()}", "actor": {{...}}}}'
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
