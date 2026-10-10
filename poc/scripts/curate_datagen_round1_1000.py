"""211.233.204.32:8765(DataGen Studio) round_1 생성분 2,000건 중 품질 통과분을 분야별로 추려 1,000건을 고른다.

입력: poc/datasets/_raw_downloads/datagen_studio_211_8765_round1_20261007/
      round_1_generated.json(생성 2,000건: scenario_id·text·isgood 등)
      scenario_pool.json(4,000건: scenario_id -> domain·label·_internal 등)

품질 하한(프로젝트 정본 재사용, koipa.services.synth_quality) — 하나라도 걸리면 제외:
    not isgood                 생성기 자체 검증(길이·중복유사도·시나리오 키워드) 탈락
    grade_token_exposed        본문에 등급 코드/한국어 등급 표기 노출
    self_grade_declaration     낱말 없이 "본 문서는 ...기밀/유출금지..." 구조로 자기 진술
    pii_detected               전화번호·이메일 등 개인정보 정규식 매칭

품질 통과분 안에서는 document_quality_errors(한글비율·문단수·수치사실 등 8지표, 0건이
더 좋음) 와 reflection_epoch(0=첫 시도에 통과, 작을수록 더 좋음)로 순위를 매긴다 —
이 두 지표는 프로젝트 관례상 "떨어뜨리는 기준"이 아니라 "줄 세우는 기준"이다.

분야(도메인) 배분: 1,000 / 품질통과 전체 건수의 비율을 분야별 품질통과 건수에 곱해
정수로 배분한다(≈53.6%). 균등 배분이 아니라 **원래 분야 비중을 유지**한다 — 특정 분야를
억지로 부풀리면 그 분야만 하한 미달 문서까지 끌어와야 한다(품질 우선순위에 반함).
분야 안에서는 등급(TS0~TS3) 비중도 같은 방식으로 유지한다(2단계 비례 배분).
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from koipa.services.synth_quality import (  # noqa: E402
    _contains_pii,
    _document_quality_errors,
    _exposes_grade_token,
    _exposes_self_grade_declaration,
)

RAW_DIR = ROOT / "datasets" / "_raw_downloads" / "datagen_studio_211_8765_round1_20261007"
OUT_DIR = ROOT / "datasets" / "datagen_studio_round1_quality1000_20261007"
TOTAL_TARGET = 1000


def largest_remainder_allocation(weights: dict, total: int) -> dict:
    """비례 배분 + 최대나머지법으로 합이 정확히 total 이 되게 정수 배분한다."""
    weight_sum = sum(weights.values())
    if weight_sum == 0:
        return {k: 0 for k in weights}
    raw = {k: (v * total / weight_sum) for k, v in weights.items()}
    floor = {k: int(r) for k, r in raw.items()}
    remainder = total - sum(floor.values())
    order = sorted(weights.keys(), key=lambda k: (raw[k] - floor[k]), reverse=True)
    for k in order[:remainder]:
        floor[k] += 1
    # 배분량이 가용량을 넘지 않게 캡(2단계 배분이라 이론상 안 넘지만 방어적으로 둔다)
    for k in floor:
        floor[k] = min(floor[k], weights[k])
    return floor


def main() -> None:
    with open(RAW_DIR / "round_1_generated.json", encoding="utf-8") as f:
        gen = json.load(f)
    with open(RAW_DIR / "scenario_pool.json", encoding="utf-8") as f:
        pool = json.load(f)
    by_id = {s["scenario_id"]: s for s in pool}
    dataset = gen["dataset"]

    rows = []
    excl_reasons = Counter()
    for d in dataset:
        sid = d["scenario_id"]
        s = by_id[sid]
        text = d["text"]
        reasons = []
        if not d["isgood"]:
            reasons.append("not_isgood")
        if _exposes_grade_token(text):
            reasons.append("grade_token_exposed")
        if _exposes_self_grade_declaration(text):
            reasons.append("self_grade_declaration")
        if _contains_pii(text):
            reasons.append("pii_detected")
        for r in reasons:
            excl_reasons[r] += 1
        qerrors = _document_quality_errors(text) if not reasons else []
        rows.append(
            {
                "scenario_id": sid,
                "domain": s["domain"],
                "label": s["label"],
                "text": text,
                "char_count": len(text),
                "isgood": d["isgood"],
                "reflection_epoch": d.get("reflection_epoch", 0),
                "excluded": bool(reasons),
                "exclusion_reasons": reasons,
                "quality_error_count": len(qerrors),
                "quality_errors": qerrors,
            }
        )

    total = len(rows)
    admitted = [r for r in rows if not r["excluded"]]
    print(f"[denominator] 생성 2,000건 전수 중 품질 하한 통과 {len(admitted)}건 "
          f"(제외 {total - len(admitted)}건: {dict(excl_reasons)})")

    # 1단계: 분야 배분
    dom_weights = Counter(r["domain"] for r in admitted)
    dom_quota = largest_remainder_allocation(dict(dom_weights), TOTAL_TARGET)
    print(f"[분야 배분] {sum(dom_quota.values())}건 / 18개 분야 "
          f"(품질통과 {len(admitted)}건 기준 비례배분): {dom_quota}")

    by_domain = defaultdict(list)
    for r in admitted:
        by_domain[r["domain"]].append(r)

    selected = []
    for dom, quota in dom_quota.items():
        docs = by_domain[dom]
        label_weights = Counter(r["label"] for r in docs)
        label_quota = largest_remainder_allocation(dict(label_weights), quota)
        by_label = defaultdict(list)
        for r in docs:
            by_label[r["label"]].append(r)
        for label, lq in label_quota.items():
            cands = sorted(
                by_label[label],
                key=lambda r: (r["quality_error_count"], r["reflection_epoch"], -r["char_count"]),
            )
            selected.extend(cands[:lq])

    print(f"[최종 선정] {len(selected)}건 (목표 {TOTAL_TARGET})")
    sel_dom = Counter(r["domain"] for r in selected)
    sel_lab = Counter(r["label"] for r in selected)
    print(f"[선정 분야 분포] {dict(sorted(sel_dom.items(), key=lambda x: -x[1]))}")
    print(f"[선정 등급 분포] {dict(sel_lab)}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "documents").mkdir(exist_ok=True)

    manifest_path = OUT_DIR / "curated_manifest.jsonl"
    with open(manifest_path, "w", encoding="utf-8") as mf:
        for i, r in enumerate(sorted(selected, key=lambda r: (r["domain"], r["label"], r["scenario_id"])), start=1):
            doc_id = f"DG1-{i:04d}"
            mf.write(json.dumps({
                "doc_id": doc_id,
                "scenario_id": r["scenario_id"],
                "domain": r["domain"],
                "label": r["label"],
                "char_count": r["char_count"],
                "reflection_epoch": r["reflection_epoch"],
                "quality_error_count": r["quality_error_count"],
                "source": "datagen_studio_211.233.204.32:8765/round_1",
            }, ensure_ascii=False) + "\n")
            with open(OUT_DIR / "documents" / f"{doc_id}.txt", "w", encoding="utf-8") as tf:
                tf.write(r["text"])

    # 전수(2,000건) 기록 — 분모·방법을 남긴다
    full_manifest_path = RAW_DIR / "all_2000_quality_audit.jsonl"
    with open(full_manifest_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({k: v for k, v in r.items() if k != "text"}, ensure_ascii=False) + "\n")

    print(f"[저장] manifest={manifest_path} documents/={OUT_DIR/'documents'} ({len(selected)}개 파일)")
    print(f"[저장] 전수 품질감사 기록={full_manifest_path}")


if __name__ == "__main__":
    main()
