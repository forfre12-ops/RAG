"""constructed_floor 세트 빌더 — witness 심은 합성 문서 생성→결정적 admission (사람검수 0).

체인: witness taxonomy(사실+근거) → 생성 LLM이 사실을 **원문 그대로** 포함한 문서 작성
→ constructed_floor 결정적 검증(전출현 witness·문단 공개선언·상위등급 negative scan·TS
외부인용) → admitted/rejected 버킷 + run-스코프 산출(정본 무변경).

라벨 결정에 LLM 심판 없음(순환 0) — 생성 LLM은 '문서를 쓰는' 역할뿐이고 라벨 근거는
taxonomy의 법령/가이드 인용 + 결정적 체크다. 산출 라벨 의미는 전부 floor(≥등급).

shortcut 방어(도메인→등급 누출 85.6% 교훈의 정밀판):
  - 생성 프롬프트가 등급명·기밀 마킹·공개선언·과장된 비밀성 수사를 금지
    (gen_mundane_s3.py 마킹어 금지 계열)
  - witness 표면형이 학습 코퍼스(--train-guard)에 있으면 그 witness의 레코드 전부 탈락
    (witness_lexicon_disjoint — 평가가 witness-token 검출력 측정으로 퇴화하는 것 차단)

사용:
  python scripts/build_constructed_floor_set.py --fake --per-type 2      # LLM 없이 배관 검증
  python scripts/build_constructed_floor_set.py --per-type 5             # Ollama qwen3:14b
출력: datasets/constructed_floor_runs/<run_id>/{admitted.jsonl, rejected.jsonl, manifest.json}
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import uuid
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from koipa.modules.m1_synthesis.witness_taxonomy import (  # noqa: E402
    WITNESS_TYPES,
    WitnessSpec,
    instantiate_witness,
)
from koipa.modules.m6_evaluation.constructed_floor import (  # noqa: E402
    LABEL_SOURCE_CONSTRUCTED_FLOOR,
    TIER_CONSTRUCTED_FLOOR,
    TRUTH_WARNING,
    evaluate_constructed_floor,
    witness_lexicon_disjoint,
)

SYSTEM_PROMPT = (
    "너는 기업 내부 문서를 사실적으로 작성하는 문서 작성자다. 규칙:\n"
    "1) 등급명(TS/S1/S2/S3/특급기밀/1급/2급/대외비/극비 등)과 기밀 표장·보안 마킹을 절대 쓰지 마라.\n"
    "2) '이미 공개', '보도자료', '공개공보', '홈페이지 게시' 같은 공개 선언 문구를 쓰지 마라.\n"
    "3) 지시된 사실 문장 외에 더 민감해 보이는 정보(국가핵심기술·군사·인수합병 가격 등)를 창작하지 마라.\n"
    "4) 담담한 실무 문체(회의록·검토 메모·보고서)로, 과장된 비밀성 수사 없이 써라.\n"
    "5) 출력은 순수 본문 텍스트만 — 제목 줄 1개 + 본문. JSON·코드블록·설명 금지."
)

USER_TEMPLATE = (
    "{domain} 분야의 사내 실무 문서(검토 메모/회의록/보고서 중 택1)를 500~1200자로 작성하라.\n"
    "다음 사실 문장을 본문에 **한 글자도 바꾸지 말고 그대로** 1회 이상 포함하라:\n"
    "『{token}』\n"
    "이 사실 주변에는 일상적인 실무 맥락(담당 부서, 후속 일정, 검토 의견)을 자연스럽게 붙여라."
)


def _fake_generate(spec: WitnessSpec, token: str, index: int) -> str:
    """LLM 없이 배관 검증용 — 결정적 본문(witness 포함, 오염 없음)."""
    return (
        f"{spec.name} 검토 메모 #{index}\n\n"
        f"{spec.domain} 파트 주간 검토 결과를 공유한다. 핵심 확인 사항은 다음과 같다: "
        f"{token} 이 값은 담당 팀 검토를 거쳐 차주 회의에서 재확인한다.\n\n"
        f"후속 일정: 관련 부서 회람 후 의견 취합."
    )


def make_llm_generate(base_url: str, model: str):
    from koipa.adapters.llm.local_openai_provider import LocalOpenAIProvider  # noqa: PLC0415

    llm = LocalOpenAIProvider(base_url=base_url, api_key="ollama", model=model,
                              enable_thinking=False, provider_label="ollama-witness-gen")

    def _gen(spec: WitnessSpec, token: str, index: int) -> str:
        user = USER_TEMPLATE.format(domain=spec.domain, token=token)
        resp = llm.generate(user, system=SYSTEM_PROMPT, temperature=0.6, max_tokens=1800)
        return (resp.text or "").strip()

    return _gen


def run_build(
    specs: list[WitnessSpec],
    per_type: int,
    generate_fn,
    train_texts: list[str],
    log=print,
) -> dict:
    """생성→결정적 admission — 순수 로직(파일 I/O 없음, 테스트 가능).

    반환: {admitted:[], rejected:[], stats:{}} — rejected에는 reasons가 남는다
    ('실패만 정보' 규율: admitted 수가 아니라 rejection 사유 분포가 품질 신호다).
    """
    admitted: list[dict] = []
    rejected: list[dict] = []
    reason_hist: Counter = Counter()
    gen_fail = 0

    # witness 표면형 학습누출 사전 검사 — 오염 witness 유형은 통째로 탈락시킨다.
    tokens_by_spec = {
        s.witness_id: [instantiate_witness(s, i) for i in range(per_type)] for s in specs
    }
    all_tokens = [t for toks in tokens_by_spec.values() for t in toks]
    offenders = witness_lexicon_disjoint(all_tokens, train_texts)
    leaked_tokens = {o["token"] for o in offenders}
    if offenders:
        log(f"[witness] ⚠️ 학습누출 witness {len(offenders)}건 — 해당 레코드 전부 탈락")

    for spec in specs:
        for i in range(per_type):
            token = tokens_by_spec[spec.witness_id][i]
            try:
                body = generate_fn(spec, token, i)
            except Exception as exc:  # noqa: BLE001 — 1건 실패가 런 전체를 죽이지 않게
                gen_fail += 1
                log(f"  생성 실패 {spec.witness_id}#{i}: {type(exc).__name__}")
                continue
            if not body or len(body.strip()) < 50:
                gen_fail += 1
                continue
            record = {
                "doc_id": f"cf-{spec.grade}-{hashlib.sha1(body.encode('utf-8')).hexdigest()[:12]}",
                "text": body,
                "intended_grade": spec.grade,
                "witnesses": [{"token": token, "basis": spec.basis}],
                "witness_id": spec.witness_id,
                "domain": spec.domain,
                "factor_profile": spec.factor_profile,
                "label_source": LABEL_SOURCE_CONSTRUCTED_FLOOR,
                "tier": TIER_CONSTRUCTED_FLOOR,
            }
            if token in leaked_tokens:
                record["reject_reasons"] = ["lexicon_leak_train(witness가 학습 코퍼스에 존재)"]
                reason_hist["lexicon_leak_train"] += 1
                rejected.append(record)
                continue
            verdict = evaluate_constructed_floor(record)
            if verdict.admitted:
                admitted.append({
                    **record,
                    "label": verdict.grade_floor,          # ⚠️ floor(≥등급) — exact 아님
                    "label_kind": "floor",
                    "truth_warning": TRUTH_WARNING,
                    "verdict": verdict.to_dict(),
                })
            else:
                record["reject_reasons"] = verdict.reasons
                for r in verdict.reasons:
                    reason_hist[r.split(":", 1)[0]] += 1
                rejected.append(record)

    stats = {
        "specs": len(specs),
        "per_type": per_type,
        "generated_target": len(specs) * per_type,
        "gen_fail": gen_fail,
        "admitted": len(admitted),
        "rejected": len(rejected),
        "admit_rate": round(len(admitted) / max(1, len(admitted) + len(rejected)), 4),
        "reject_reason_hist": dict(reason_hist),
        "lexicon_offenders": offenders,
        "admitted_by_grade": dict(Counter(r["intended_grade"] for r in admitted)),
    }
    return {"admitted": admitted, "rejected": rejected, "stats": stats}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-type", type=int, default=2)
    ap.add_argument("--gen-model", default="qwen3:14b")
    ap.add_argument("--base-url", default="http://localhost:11434/v1")
    ap.add_argument("--fake", action="store_true", help="LLM 없이 배관 검증(결정적 본문)")
    ap.add_argument("--grades", nargs="*", default=None, help="예: TS S1 (기본 전체)")
    ap.add_argument("--train-guard", nargs="*",
                    default=["datasets/gold_real/train_subset.jsonl"])
    ap.add_argument("--out-dir", default="datasets/constructed_floor_runs")
    args = ap.parse_args()

    specs = list(WITNESS_TYPES)
    if args.grades:
        wanted = {g.upper() for g in args.grades}
        specs = [s for s in specs if s.grade in wanted]

    train_texts: list[str] = []
    for p in args.train_guard:
        path = Path(p)
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                try:
                    train_texts.append(str(json.loads(line).get("text") or ""))
                except json.JSONDecodeError:
                    continue

    generate_fn = _fake_generate if args.fake else make_llm_generate(args.base_url, args.gen_model)
    result = run_build(specs, args.per_type, generate_fn, train_texts)

    run_id = f"{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    out_dir = Path(args.out_dir) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    def _write(name: str, rows: list[dict]) -> None:
        (out_dir / name).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""),
            encoding="utf-8",
        )

    _write("admitted.jsonl", result["admitted"])
    _write("rejected.jsonl", result["rejected"])
    manifest = {
        "run_id": run_id,
        "mode": "fake" if args.fake else f"llm:{args.gen_model}",
        "stats": result["stats"],
        "tier": TIER_CONSTRUCTED_FLOOR,
        "truth_warning": TRUTH_WARNING,
        "consumption_contract": [
            "admitted 라벨은 floor(≥등급) — exact-grade·실문서 성능 인용 금지",
            "FNR 방향 회귀 전용(자체 셀) — '명백 케이스에서조차 미탐하면 FAIL'",
            "학습 편입 금지 기본 — witness 표면형 누출 시 평가 무효",
            "심판 LLM은 admission에 불참(순환 0) — 별도 negative filter는 opt-in",
        ],
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[constructed-floor] admitted={result['stats']['admitted']} "
          f"rejected={result['stats']['rejected']} admit_rate={result['stats']['admit_rate']} "
          f"→ {out_dir}")
    print(f"  reject 사유: {result['stats']['reject_reason_hist']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
