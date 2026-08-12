"""gold_real의 LLM-judge 라벨(현재 Qwen)을 Claude로 재심사하고 Qwen↔Claude를 직접 비교.

배경
----
gold_real/classification_gold.jsonl의 '정답' 중 LLM 판단분(reviewer_id=llm_judge_local_openai,
label_source∈{llm_judge_primary, llm_judge_consensus})은 전부 **Qwen3-14B**가 판단했다.
원본 문서가 oss_corpus의 공개 판례/법령이라 폐쇄망 제약 없이 Claude로 더 정밀하게 재판정할 수 있다.

이 스크립트는:
  1. classification_gold.jsonl을 읽어 LLM-judge 레코드만 추린다(기본).
  2. 각 문서를 Claude(기본 claude-opus-4-8)로 재판정한다(doc_id 보존).
  3. Qwen 라벨 vs Claude 라벨을 직접 비교 → 불일치 케이스 추출(사람 검수 우선순위 큐).

정본 안전
--------
- classification_gold.jsonl은 **읽기만** 한다. 출력은 전부 run-스코프 디렉토리
  (기본 datasets/gold_real/_rejudge_claude/). 정본 덮어쓰기 없음([[golden-builder-track-status]] 정책).
- --resume: 이미 재판정한 doc_id는 건너뛴다(중단 시 비용 낭비 방지).

Opus 4.8 주의
------------
Opus 4.8은 temperature/top_p/top_k를 받으면 400이라, 기존 AnthropicProvider(temperature 강제)를
못 쓴다. 여기서는 anthropic SDK를 직접, **최소 파라미터로** 호출한다(model/max_tokens/system/
messages만 — thinking 미지정=off, 빠르고 저렴, JSON-only 출력). Sonnet 4.6 등 다른 모델도 동일 호출.

사용
----
  # 비용·건수만 (API 호출 없음 — 키 불필요)
  python scripts/rejudge_gold_with_claude.py --dry-run

  # 실제 실행 (ANTHROPIC_API_KEY 필요)
  set ANTHROPIC_API_KEY=sk-ant-...           # Windows
  export ANTHROPIC_API_KEY=sk-ant-...        # bash
  python scripts/rejudge_gold_with_claude.py --resume

  # 표본 100건만 먼저
  python scripts/rejudge_gold_with_claude.py --limit 100 --resume
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

# Windows 콘솔(cp949)에서 한글·≈ 등 출력 깨짐 방지 — UTF-8 강제.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# 재심사 대상 = LLM(Qwen)이 판단한 레코드. 법적근거(public_gold_collector)·codex는 제외.
LLM_JUDGE_SOURCES = {"llm_judge_primary", "llm_judge_consensus"}
LABELS = {"TS", "S1", "S2", "S3"}

# Opus 4.8 단가 (per 1M tokens). 다른 모델이면 --model에 맞춰 표시만 달라짐(비용 추정용).
PRICING = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def _jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            rows.append(json.loads(line))
    return rows


def _build_prompt(text: str) -> str:
    # LLMLabeler.label과 동일 포맷(SVM 기반 SYSTEM_PROMPT 공유) — /no_think(Qwen 전용)만 제거.
    return f"분류 대상 문서:\n```\n{text[:4000]}\n```\n\nJSON 응답:"


def _est_tokens(text: str) -> int:
    return max(1, len(text) // 3)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="datasets/gold_real/classification_gold.jsonl")
    ap.add_argument("--out-dir", default="datasets/gold_real/_rejudge_claude")
    ap.add_argument("--model", default="claude-opus-4-8")
    ap.add_argument("--max-tokens", type=int, default=1024)
    ap.add_argument("--limit", type=int, default=None, help="처리 최대 건수(표본)")
    ap.add_argument("--resume", action="store_true", help="기존 출력의 doc_id 건너뜀")
    ap.add_argument("--all-sources", action="store_true",
                    help="LLM-judge 외 레코드도 포함(기본: llm_judge_*만)")
    ap.add_argument("--dry-run", action="store_true", help="건수·비용 추정만, API 호출 없음")
    args = ap.parse_args()

    os.chdir(_HERE.parent)  # poc/ 기준 상대경로
    gold_path = Path(args.gold)
    if not gold_path.exists():
        print(f"[ERROR] gold 없음: {gold_path}", file=sys.stderr)
        return 2

    rows = _jsonl(gold_path)
    targets = [
        r for r in rows
        if (r.get("label") in LABELS)
        and (args.all_sources or r.get("label_source") in LLM_JUDGE_SOURCES)
        and (r.get("text") or "").strip()
    ]
    if args.limit:
        targets = targets[: args.limit]

    from koipa.modules.m3_labeling.llm_labeler import SYSTEM_PROMPT, _safe_parse_json  # noqa: E402

    # 비용 추정 (system + 본문 입력, 출력 ~150tok 가정)
    in_price, out_price = PRICING.get(args.model, (5.0, 25.0))
    sys_tok = _est_tokens(SYSTEM_PROMPT)
    est_in = sum(sys_tok + _est_tokens(_build_prompt(r["text"])) for r in targets)
    est_out = len(targets) * 150
    est_cost = (est_in * in_price + est_out * out_price) / 1_000_000
    src_dist = Counter(r.get("label_source") for r in targets)

    print(f"[rejudge] 대상 {len(targets)}건 (model={args.model})")
    print(f"[rejudge] label_source 분포: {dict(src_dist)}")
    print(f"[rejudge] 비용추정: in≈{est_in:,}tok out≈{est_out:,}tok → ${est_cost:.2f} "
          f"(단가 ${in_price}/${out_price} per 1M)")

    out_dir = Path(args.out_dir)
    out_path = out_dir / "rejudged.jsonl"
    disagree_path = out_dir / "disagreements.jsonl"
    summary_path = out_dir / "summary.json"

    if args.dry_run:
        print("[rejudge] --dry-run: API 호출 없이 종료")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)

    # resume: 기존 출력 doc_id 수집
    done: set[str] = set()
    if args.resume and out_path.exists():
        for r in _jsonl(out_path):
            if r.get("doc_id"):
                done.add(r["doc_id"])
        print(f"[rejudge] resume: 기처리 {len(done)}건 건너뜀")

    # Claude 클라이언트 (최소 파라미터 — Opus 4.8 temperature 400 회피)
    try:
        from anthropic import Anthropic  # noqa: PLC0415
    except ImportError:
        print("[ERROR] anthropic 패키지 미설치 (pip install anthropic)", file=sys.stderr)
        return 3
    from koipa.config import settings  # noqa: PLC0415
    key = os.environ.get("ANTHROPIC_API_KEY") or getattr(settings, "anthropic_api_key", None)
    if not key:
        print("[ERROR] ANTHROPIC_API_KEY 미설정 — export/set 후 재실행", file=sys.stderr)
        return 4
    client = Anthropic(api_key=key)

    fout = out_path.open("a", encoding="utf-8")
    n = agree = 0
    tot_in = tot_out = 0
    t0 = time.time()
    try:
        for i, r in enumerate(targets):
            doc_id = r.get("doc_id")
            if doc_id in done:
                continue
            qwen_grade = r["label"]
            try:
                msg = client.messages.create(
                    model=args.model,
                    max_tokens=args.max_tokens,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": _build_prompt(r["text"])}],
                )
                text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
                tot_in += getattr(msg.usage, "input_tokens", 0)
                tot_out += getattr(msg.usage, "output_tokens", 0)
            except Exception as exc:  # noqa: BLE001
                print(f"  ✗ {doc_id}: {type(exc).__name__}: {exc}", file=sys.stderr)
                continue

            parsed = _safe_parse_json(text)
            claude_grade = parsed.get("grade", "")
            if claude_grade not in LABELS:
                claude_grade = "PARSE_FAIL"
            same = claude_grade == qwen_grade
            agree += int(same)
            n += 1

            rec = {
                "doc_id": doc_id,
                "qwen_grade": qwen_grade,
                "qwen_label_source": r.get("label_source"),
                "claude_grade": claude_grade,
                "claude_confidence": parsed.get("confidence"),
                "claude_svm": {k: parsed.get(k) for k in ("secrecy", "value", "management")},
                "claude_rationale": str(parsed.get("rationale", ""))[:300],
                "agree": same,
                "source": r.get("source"),
                "domain": r.get("domain"),
            }
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fout.flush()

            if n % 25 == 0:
                cost = (tot_in * in_price + tot_out * out_price) / 1_000_000
                rate = n / max(0.001, time.time() - t0)
                print(f"  [{n}/{len(targets)}] 일치율 {agree/n:.1%} · "
                      f"${cost:.2f} 누적 · {rate:.1f}/s")
    finally:
        fout.close()

    # 요약 + 불일치 큐 (전체 출력 재집계 — resume 포함)
    all_out = _jsonl(out_path)
    disagreements = [r for r in all_out if not r.get("agree")]
    with disagree_path.open("w", encoding="utf-8") as df:
        for r in disagreements:
            df.write(json.dumps(r, ensure_ascii=False) + "\n")

    # confusion: qwen→claude
    confusion: Counter = Counter()
    for r in all_out:
        confusion[(r["qwen_grade"], r["claude_grade"])] += 1
    total = len(all_out)
    agreed = sum(1 for r in all_out if r.get("agree"))
    cost = (tot_in * in_price + tot_out * out_price) / 1_000_000
    summary = {
        "model": args.model,
        "total_rejudged": total,
        "agreement_count": agreed,
        "agreement_rate": round(agreed / total, 4) if total else 0.0,
        "disagreement_count": total - agreed,
        "disagreement_by_qwen_grade": dict(Counter(
            r["qwen_grade"] for r in disagreements)),
        "confusion_qwen_to_claude": {f"{q}->{c}": n for (q, c), n in
                                     sorted(confusion.items(), key=lambda x: -x[1])},
        "tokens_in": tot_in,
        "tokens_out": tot_out,
        "cost_usd_this_run": round(cost, 3),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[rejudge] 완료: {total}건 · 일치 {agreed}({agreed/max(1,total):.1%}) · "
          f"불일치 {total-agreed} · ${cost:.2f}")
    print(f"[rejudge] 출력: {out_path}")
    print(f"[rejudge] 불일치 큐: {disagree_path}")
    print(f"[rejudge] 요약: {summary_path}")
    print("[rejudge] 불일치 상위 confusion:")
    for k, v in list(summary["confusion_qwen_to_claude"].items())[:8]:
        if not k.split("->")[0] == k.split("->")[1]:
            print(f"    {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
