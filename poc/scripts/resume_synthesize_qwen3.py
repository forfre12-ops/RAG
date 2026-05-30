"""B-1 Qwen3 합성 800건 — 중단분 이어서 채우기 (resume).

p3_generate_synthetic.py 는 매번 등급별 균등 생성을 처음부터 한다(resume 불가).
이 스크립트는 출력 디렉터리의 기존 등급 분포를 읽어, 목표(등급당 TARGET) 대비
부족한 만큼만 Qwen3(local_openai)로 생성한다.

p3 스크립트와 동일한 레코드 스키마·LENGTH_BANDS·도메인 로테이션을 따른다.
m3 룰 라벨러로 즉시 검증해 라벨 일치도/FNR 를 집계한다.

사용 (호스트에서, .env LLM_PROVIDER=local_openai + Ollama qwen3:14b):
    python scripts/resume_synthesize_qwen3.py [OUT_DIR] [TARGET_PER_GRADE]
기본: datasets/synthetic_qwen3_800, 등급당 200.
"""
from __future__ import annotations

import glob
import json
import sys
import time
from collections import Counter
from pathlib import Path
from uuid import uuid4

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from lloydk.adapters.llm import build_provider  # noqa: E402
from lloydk.modules.m1_synthesis.generator import SynthRequest, SyntheticDocGenerator  # noqa: E402
from lloydk.modules.m3_labeling import LabelingPipeline  # noqa: E402

# 참고 (2026-05-30 실측): Qwen3 14B 가 16GB GPU 에 완전히 안 올라가 일부 CPU
# 오프로딩 → 건당 ~16-20초. /no_think 는 오히려 출력 토큰이 늘어 더 느렸음
# (THINK 16s/472tok vs NO_THINK 30s/633tok). thinking 기본 유지가 최선.

GRADES = ["TS", "S1", "S2", "S3"]
DOMAINS = ["tech", "business", "finance", "hr", "legal", "mixed"]
# 2026-05-30: 긴 밴드(최대 10,000자)는 Qwen3 14B 에서 건당 ~40초로 너무 느려
# 1,800자로 캡 (건당 ~5-6초). 다양성은 4 밴드 유지하되 단축.
# 긴 문서 분포는 이미 synthetic_5k·기존 TS 200건이 일부 커버.
LENGTH_BANDS = [
    (200, 500),
    (400, 800),
    (700, 1200),
    (1000, 1800),
]


def _existing_counts(out_dir: Path) -> Counter:
    counts: Counter = Counter()
    for f in glob.glob(str(out_dir / "*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
            g = d.get("target_grade")
            if g:
                counts[g] += 1
        except Exception:  # noqa: BLE001
            continue
    return counts


def main() -> int:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "datasets/synthetic_qwen3_800")
    target = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    out_dir.mkdir(parents=True, exist_ok=True)

    existing = _existing_counts(out_dir)
    deficits = {g: max(0, target - existing.get(g, 0)) for g in GRADES}
    total_needed = sum(deficits.values())
    print(f"[resume] out={out_dir} target/grade={target}", flush=True)
    print(f"[resume] existing={dict(existing)} deficits={deficits} need={total_needed}", flush=True)
    if total_needed == 0:
        print("[resume] nothing to do — all grades already at target.", flush=True)
        return 0

    llm = build_provider("local_openai")
    gen = SyntheticDocGenerator(llm=llm)
    labeler = LabelingPipeline()

    t0 = time.time()
    made = 0
    label_hits = 0
    label_total = 0
    fnr_under = 0
    high_grade_total = 0

    for grade in GRADES:
        need = deficits[grade]
        # band/domain 인덱스는 기존 개수 이어서 — 분포 다양성 유지
        start_i = existing.get(grade, 0)
        for j in range(need):
            i = start_i + j
            domain = DOMAINS[i % len(DOMAINS)]
            band = LENGTH_BANDS[i % len(LENGTH_BANDS)]
            req = SynthRequest(
                target_grade=grade, domain=domain, count=1,
                len_min=band[0], len_max=band[1],
            )
            try:
                doc = gen.generate_one(req)
            except Exception as exc:  # noqa: BLE001
                print(f"  [{grade}/{i}] FAIL: {exc}", file=sys.stderr, flush=True)
                continue

            full_text = f"{doc.title}\n\n{doc.body}"
            pred = labeler.label(full_text)
            label_total += 1
            if pred.grade == grade:
                label_hits += 1
            if grade in ("TS", "S1"):
                high_grade_total += 1
                if pred.grade in ("S2", "S3"):
                    fnr_under += 1

            rec = {
                "synth_id": str(uuid4()),
                "target_grade": grade,
                "domain": domain,
                "title": doc.title,
                "body": doc.body,
                "document_type": doc.document_type,
                "dept_hint": doc.dept_hint,
                "rationale_tags": doc.rationale_tags,
                "llm_provider": doc.llm_provider,
                "pii_violations": doc.pii_violations,
                "parse_error": doc.parse_error,
                "predicted_label": pred.grade,
                "generated_at": int(time.time()),
            }
            (out_dir / f"{rec['synth_id']}.json").write_text(
                json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            made += 1
            if made % 10 == 0:
                dt = time.time() - t0
                print(
                    f"[resume] {made}/{total_needed} made "
                    f"({grade} {j + 1}/{need}), {dt / made:.1f}s/doc, "
                    f"label_acc={label_hits / label_total:.3f}",
                    flush=True,
                )

    dt = time.time() - t0
    final = _existing_counts(out_dir)
    acc = label_hits / label_total if label_total else 0.0
    fnr = fnr_under / high_grade_total if high_grade_total else 0.0
    print(
        f"[resume] DONE made={made} elapsed={dt:.0f}s "
        f"({dt / made:.1f}s/doc) label_acc={acc:.3f} FNR={fnr:.3f}",
        flush=True,
    )
    print(f"[resume] final distribution={dict(final)} total={sum(final.values())}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
