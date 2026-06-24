"""
LLM-as-judge로 gold_real/classification_gold.jsonl 자동 생성.

전략:
  1. oss_corpus/ 실문서(판례/법령) 로드 — generator와 무관
  2. 룰 라벨러로 1차 분류 + confidence
  3. LLMLabeler(Claude/Qwen3)로 독립 분류 + confidence + rationale
  4. 합의 필터:
       rule_grade == llm_grade
       AND rule_conf ≥ --min-rule-conf
       AND llm_conf ≥ --min-llm-conf
     → gold_real/classification_gold.jsonl
  5. 불일치 케이스 → gold_real/uncertain_cases.jsonl (경계 사례 분석용)

독립성:
  - LLMLabeler는 GRADE_KEYWORDS 목록을 프롬프트에 넣지 않음
  - oss_corpus는 합성 generator와 무관한 실제 법령/판례
  → 룰 라벨러 ↔ LLM 합의 = 순환 의존 없는 pseudo-gold

비용 추정 (Claude Sonnet-4.6):
  100건 × 2,000 in-tok × $3/1M ≈ $0.60
  300건 × 2,000 in-tok × $3/1M ≈ $1.80
  --dry-run 으로 예상 비용 먼저 확인 가능

사용:
  # 비용만 확인 (실제 호출 없음)
  python scripts/make_llm_judge_gold.py --dry-run --n 50

  # Claude Sonnet으로 200건 처리
  python scripts/make_llm_judge_gold.py --provider anthropic --n 200

  # 로컬 Qwen3 (무료)
  python scripts/make_llm_judge_gold.py --provider local_openai --n 300

  # 신뢰도 기준 완화 (더 많은 gold 확보)
  python scripts/make_llm_judge_gold.py --min-llm-conf 0.6 --n 200
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Windows 출력 인코딩
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from lloydk.modules.m3_labeling.consensus import evaluate_consensus  # noqa: E402 (sys.path 설정 후)

LABELS = ["TS", "S1", "S2", "S3"]


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 3)


def load_oss_corpus(
    corpus_dir: Path,
    n: int,
    seed: int,
    grade_filter: list[str] | None,
    source_filter: list[str] | None = None,
    domain_filter: list[str] | None = None,
    exclude_files: set[str] | None = None,
) -> list[dict]:
    files = sorted(corpus_dir.glob("*.json"))
    rng = random.Random(seed)
    rng.shuffle(files)

    docs = []
    for f in files:
        if exclude_files and f.name in exclude_files:
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        grade = d.get("target_grade", "")
        source = d.get("source", "")
        domain = d.get("domain", "")
        if grade_filter and grade not in grade_filter:
            continue
        if source_filter and not any(s in source for s in source_filter):
            continue
        if domain_filter and domain not in domain_filter:
            continue
        body = d.get("body", "")
        if not body or len(body) < 200:
            continue
        docs.append({
            "doc_id": _sha1(body),
            "title": d.get("title", ""),
            "body": body,
            "target_grade": grade,
            "domain": domain,
            "source": source,
            "original_file": f.name,
        })
        if len(docs) >= n:
            break

    return docs


def run_rule_labeler(text: str) -> tuple[str, float]:
    from lloydk.modules.m3_labeling import LabelingPipeline
    pipe = LabelingPipeline()
    result = pipe.label(text)
    grade = result.grade.value if hasattr(result.grade, "value") else str(result.grade)
    return grade, float(result.confidence)


_JUDGE_SYSTEM = """당신은 한국 영업비밀 보호 가이드라인에 정통한 문서 분류 전문가다.
입력 문서를 분석해 보안 등급을 분류하고 평가 근거를 설명한다.

등급 체계:
- TS  특급기밀 : 유출 시 회사 존립 위협 또는 국가 안보 영향
- S1  1급비밀  : 유출 시 경쟁우위 상실 또는 영업비밀 침해
- S2  2급대외비: 외부 공개 시 협상력 약화 또는 이미지 손상
- S3  3급공개  : 공개 예정이거나 이미 공개된 정보

평가 시 주의:
- 문서에 등급명이 직접 쓰여 있어도 그것만 보고 판단하지 말 것
- 실제 내용과 공개 범위, 피해 가능성을 기준으로 판단할 것
- 모호하면 한 단계 위 등급으로 (FNR 회피)

출력 형식 (JSON only, 다른 텍스트 금지):
{"grade":"TS|S1|S2|S3","confidence":0.0~1.0,"rationale":"1-2문장"}"""


def _ollama_judge(text: str, base_url: str = "http://localhost:11434") -> tuple[str, float, str]:
    """Ollama API 직접 호출 — OpenAI 호환 endpoint의 think 파라미터 문제 우회."""
    import json as _json
    import urllib.request as _req

    prompt = f"분류 대상 문서:\n```\n{text[:4000]}\n```\n\nJSON 응답:"
    payload = {
        "model": "qwen3:14b",
        "messages": [
            {"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "think": False,
        "options": {"num_predict": 200, "temperature": 0.1},
    }
    data = _json.dumps(payload).encode()
    req = _req.Request(
        f"{base_url}/api/chat", data=data,
        headers={"Content-Type": "application/json"},
    )
    resp = _req.urlopen(req, timeout=120)
    result = _json.loads(resp.read())
    raw = result.get("message", {}).get("content", "")

    # JSON 파싱
    import re as _re
    m = _re.search(r"\{.*?\}", raw, _re.S)
    if m:
        try:
            parsed = _json.loads(m.group(0))
            grade = parsed.get("grade", "S3")
            if grade not in LABELS:
                grade = "S3"
            return grade, float(parsed.get("confidence", 0.5)), str(parsed.get("rationale", ""))
        except Exception:
            pass
    return "S3", 0.0, raw[:200]


def run_llm_labeler(text: str, provider_name: str) -> tuple[str, float, str, dict]:
    if provider_name in ("local_openai", "ollama", "vllm", "lm_studio"):
        # Ollama 직접 API (think:false 지원)
        base_url = "http://localhost:11434"
        grade, conf, rationale = _ollama_judge(text, base_url)
        return grade, conf, rationale, {}
    # Anthropic / OpenAI
    from lloydk.adapters.llm import build_provider
    from lloydk.modules.m3_labeling.llm_labeler import LLMLabeler
    provider = build_provider(provider_name)
    labeler = LLMLabeler(provider=provider)
    result = labeler.label(text)
    grade = result.grade if result.grade in LABELS else "S3"
    return grade, float(result.confidence), result.rationale, result.factor_scores


def estimate_cost(docs: list[dict], provider: str) -> dict:
    PRICE_PER_M = {
        "anthropic": {"in": 3.0, "out": 15.0},   # claude-sonnet-4-6
        "openai": {"in": 5.0, "out": 15.0},        # gpt-4o
        "local_openai": {"in": 0.0, "out": 0.0},   # 무료 (로컬)
        "noop": {"in": 0.0, "out": 0.0},
    }
    price = PRICE_PER_M.get(provider, {"in": 3.0, "out": 15.0})
    total_in = sum(_estimate_tokens(d["body"][:4000]) for d in docs)
    total_out = len(docs) * 150  # 출력: grade + confidence + factors + rationale
    cost = (total_in * price["in"] + total_out * price["out"]) / 1_000_000
    return {
        "provider": provider,
        "docs": len(docs),
        "est_input_tokens": total_in,
        "est_output_tokens": total_out,
        "est_cost_usd": round(cost, 3),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM-as-judge gold set 생성")
    ap.add_argument("--corpus-dir", default="datasets/oss_corpus")
    ap.add_argument("--out", default="datasets/gold_real/classification_gold.jsonl")
    ap.add_argument("--uncertain-out", default="datasets/gold_real/uncertain_cases.jsonl")
    ap.add_argument("--provider", default="anthropic",
                    help="LLM provider: anthropic|openai|local_openai|noop")
    ap.add_argument("--n", type=int, default=200, help="처리할 문서 수")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--min-rule-conf", type=float, default=0.5,
                    help="룰 라벨러 최소 신뢰도 (기본 0.5)")
    ap.add_argument("--min-llm-conf", type=float, default=0.7,
                    help="LLM 최소 신뢰도 (기본 0.7)")
    ap.add_argument("--dry-run", action="store_true",
                    help="비용 추정만 출력, 실제 LLM 호출 없음")
    ap.add_argument("--append", action="store_true",
                    help="기존 gold 파일에 추가 (기본: 덮어쓰기)")
    ap.add_argument("--grade-filter", default=None,
                    help="처리할 등급 필터 (예: TS,S1). 없으면 전체")
    ap.add_argument("--source-filter", default=None,
                    help="소스 필터 (예: 금융보고서,판례). 없으면 전체")
    ap.add_argument("--domain-filter", default=None,
                    help="도메인 필터 (예: 경영정보,화학_제약). 없으면 전체")
    ap.add_argument("--exclude-processed", action="store_true",
                    help="기존 gold/uncertain에서 처리된 파일 제외")
    args = ap.parse_args()

    corpus_dir = Path(args.corpus_dir)
    if not corpus_dir.exists():
        print(f"[ERROR] corpus_dir 없음: {corpus_dir}", file=sys.stderr)
        return 2

    grade_filter = [g.strip() for g in args.grade_filter.split(",")] if args.grade_filter else None
    source_filter = [s.strip() for s in args.source_filter.split(",")] if args.source_filter else None
    domain_filter = [d.strip() for d in args.domain_filter.split(",")] if args.domain_filter else None

    # 이미 처리된 파일 수집
    exclude_files: set[str] = set()
    if args.exclude_processed or args.append:
        for fp in [args.out, args.uncertain_out]:
            p = Path(fp)
            if p.exists():
                for line in p.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        rec = json.loads(line)
                        if rec.get("original_file"):
                            exclude_files.add(rec["original_file"])
        if exclude_files:
            print(f"[judge] 기처리 제외: {len(exclude_files)}건")

    docs = load_oss_corpus(corpus_dir, args.n, args.seed, grade_filter,
                           source_filter, domain_filter, exclude_files)
    print(f"[judge] oss_corpus 로드: {len(docs)}건")

    # 비용 추정
    cost_info = estimate_cost(docs, args.provider)
    print(
        f"[judge] 비용 추정: provider={args.provider}, "
        f"est_cost=${cost_info['est_cost_usd']:.3f}, "
        f"in_tok={cost_info['est_input_tokens']:,}"
    )

    if args.dry_run:
        print("[judge] --dry-run: LLM 호출 없이 종료")
        print(json.dumps(cost_info, ensure_ascii=False, indent=2))
        return 0

    out_path = Path(args.out)
    uncertain_path = Path(args.uncertain_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 기존 doc_id 로드 (append 모드)
    existing_ids: set[str] = set()
    if args.append and out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                existing_ids.add(json.loads(line).get("doc_id", ""))

    gold_records: list[dict] = []
    uncertain_records: list[dict] = []
    skipped_conf = skipped_agree = 0
    total_cost = 0.0
    grade_counter: Counter[str] = Counter()

    mode = "a" if args.append else "w"
    fout = out_path.open(mode, encoding="utf-8")
    fout_unc = uncertain_path.open(mode, encoding="utf-8")

    for i, doc in enumerate(docs):
        doc_id = doc["doc_id"]
        if doc_id in existing_ids:
            continue

        text = f"{doc['title']}\n\n{doc['body']}"
        print(f"  [{i+1}/{len(docs)}] {doc_id} (target={doc['target_grade']}) ... ", end="", flush=True)

        # 1. 룰 라벨러
        try:
            rule_grade, rule_conf = run_rule_labeler(text)
        except Exception as e:
            print(f"rule_err={e}")
            continue

        # 2. LLM 라벨러
        t0 = time.perf_counter()
        try:
            llm_grade, llm_conf, rationale, factor_scores = run_llm_labeler(text, args.provider)
        except Exception as e:
            print(f"llm_err={e}")
            continue
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        # 비용 추적 (noop은 0)
        in_tok = _estimate_tokens(text[:4000])
        total_cost += (in_tok * 3.0 + 150 * 15.0) / 1_000_000

        # 합의 게이트 (m3_labeling.consensus로 추출 — 빌더·라벨링 자동화와 동일 규칙)
        verdict = evaluate_consensus(
            rule_grade, rule_conf, llm_grade, llm_conf,
            min_rule_conf=args.min_rule_conf, min_llm_conf=args.min_llm_conf,
        )
        agree = verdict.agree
        is_gold = verdict.is_gold
        status = verdict.status
        label_src = verdict.label_source
        review_st = verdict.review_status
        print(
            f"rule={rule_grade}({rule_conf:.2f}) llm={llm_grade}({llm_conf:.2f}) "
            f"→ {status} ({elapsed_ms}ms)"
        )

        rec = {
            "doc_id": doc_id,
            "text": text[:6000],
            "label": llm_grade if is_gold else None,
            "label_source": label_src,
            "source": doc["source"],
            "domain": doc["domain"],
            "original_file": doc["original_file"],
            "target_grade_oss": doc["target_grade"],
            "rule_grade": rule_grade,
            "rule_confidence": round(rule_conf, 4),
            "llm_grade": llm_grade,
            "llm_confidence": round(llm_conf, 4),
            "llm_rationale": rationale,
            "llm_factor_scores": factor_scores,
            "agreement": agree,
            "review_status": review_st,
            "reviewer_id": f"llm_judge_{args.provider}",
        }

        if is_gold:
            rec["label"] = llm_grade
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            gold_records.append(rec)
            grade_counter[llm_grade] += 1
        else:
            if not agree:
                skipped_agree += 1
            else:
                skipped_conf += 1
            fout_unc.write(json.dumps(rec, ensure_ascii=False) + "\n")
            uncertain_records.append(rec)

    fout.close()
    fout_unc.close()

    # 결과 요약
    print()
    print("=== LLM Judge Gold Set 생성 완료 ===")
    print(f"  처리: {len(docs)}건")
    print(f"  gold: {len(gold_records)}건 → {out_path}")
    print(f"  uncertain: {len(uncertain_records)}건 → {uncertain_path}")
    print(f"    (불일치: {skipped_agree}건, 저신뢰도: {skipped_conf}건)")
    print(f"  등급 분포: {dict(sorted(grade_counter.items()))}")
    print(f"  실제 비용 추정: ${total_cost:.3f}")
    print()

    if gold_records:
        print(f"다음 단계: make check-gold  (gold_real 품질 검사)")
        print(f"           make eval-gold   (P1 llm_judge_gold F1)")

    return 0 if gold_records else 1


if __name__ == "__main__":
    raise SystemExit(main())
