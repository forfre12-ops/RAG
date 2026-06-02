"""POST /answer E2E 실측 — RAG 기반 Qwen3 답변 생성 품질·레이턴시 측정."""
import io
import json
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import httpx

from lloydk.modules.m6_evaluation.answer_metrics import (  # noqa: E402
    citation_metrics,
    grounding_signals,
)

API_URL = "http://localhost:8000"
API_KEY = "devkey"

# 등급 × 도메인 대표 쿼리
QUERIES = [
    {"query": "반도체 공정 핵심 기술 유출 시 법적 책임 범위는?",        "grade": "TS", "domain": "tech"},
    {"query": "임원 인사 이동 정보가 외부에 알려졌을 때 대처 방법은?",   "grade": "TS", "domain": "hr"},
    {"query": "M&A 실사 과정에서 기밀 정보 보호 절차는?",              "grade": "TS", "domain": "business"},
    {"query": "알고리즘 소스코드 유출 방지를 위한 기술적 보안 조치는?", "grade": "S1", "domain": "tech"},
    {"query": "고객 데이터베이스 접근 권한 관리 방법은?",               "grade": "S1", "domain": "business"},
    {"query": "분기 매출 자료의 내부 유통 범위 설정 기준은?",           "grade": "S2", "domain": "finance"},
    {"query": "사업 계획서 작성 시 보안 등급 분류 기준은?",             "grade": "S2", "domain": "business"},
    {"query": "보도자료 배포 전 검토해야 할 공개 범위 기준은?",         "grade": "S3", "domain": "business"},
]

results = []
print("=" * 60)
print("  POST /answer E2E 실측 — RAG+Qwen3 답변 생성")
print("=" * 60)

with httpx.Client(timeout=300.0) as cli:
    for q in QUERIES:
        payload = {
            "query":     q["query"],
            "namespace": "docs",
            "top_k":     5,
        }
        t0 = time.perf_counter()
        try:
            r = cli.post(
                f"{API_URL}/api/v1/answer",
                headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
                json=payload,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000

            if r.status_code == 200:
                data  = r.json()
                ans   = data.get("answer", "")
                hits  = data.get("hits_used", data.get("context_hits", []))
                warns = data.get("warnings", [])
                citations = data.get("citations", [])
                fallback = bool(data.get("deterministic_fallback", False))
                # 근거성 신호 — 사람 gold 없이 산출. zero-hit/LLM 무발동을 통과시키던
                # len(ans)>20 휴리스틱의 사각지대를 제거(합성-청크-투입 수정의 회귀 안전망).
                ground = grounding_signals(ans, n_hits=len(hits), deterministic_fallback=fallback)
                cm = citation_metrics(ans, len(hits))
                # ok = 비어있지 않은 답 + 실제 검색 hit + LLM 발동 + 근거(인용 범위내) 존재.
                ok = bool(ans and len(ans) > 20 and len(hits) > 0 and not fallback and ground["grounded"])
                print(f"\n  [{q['domain']}/{q['grade']}] {elapsed_ms:.0f}ms")
                print(f"  Q: {q['query'][:50]}...")
                print(f"  A: {ans[:120]}...")
                print(
                    f"  검색 {len(hits)}건 | 인용 {cm['n_cited']} | "
                    f"fallback={fallback} | grounded={ground['grounded']} | 경고 {len(warns)}"
                )
                if ground["reasons"]:
                    print(f"  ⚠ {'; '.join(ground['reasons'])}")
                results.append({
                    "domain": q["domain"], "grade": q["grade"],
                    "query": q["query"], "answer_len": len(ans),
                    "hits": len(hits), "elapsed_ms": round(elapsed_ms),
                    "ok": ok, "warnings": warns,
                    "deterministic_fallback": fallback,
                    "n_cited": cm["n_cited"],
                    "citations_out_of_range": cm["out_of_range"],
                    "grounded": ground["grounded"],
                    "hallucination_risk": ground["hallucination_risk"],
                    "grounding_reasons": ground["reasons"],
                })
            else:
                print(f"\n  [{q['domain']}/{q['grade']}] FAIL {r.status_code}: {r.text[:80]}")
                results.append({"domain": q["domain"], "grade": q["grade"],
                                 "ok": False, "elapsed_ms": round(elapsed_ms)})
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            print(f"\n  [{q['domain']}/{q['grade']}] ERROR: {e}")
            results.append({"domain": q["domain"], "grade": q["grade"],
                             "ok": False, "elapsed_ms": round(elapsed_ms)})

ok_count = sum(1 for r in results if r.get("ok"))
grounded_count = sum(1 for r in results if r.get("grounded"))
fallback_count = sum(1 for r in results if r.get("deterministic_fallback"))
halluc_count = sum(1 for r in results if r.get("hallucination_risk"))
avg_ms   = sum(r.get("elapsed_ms", 0) for r in results) / max(len(results), 1)
avg_len  = sum(r.get("answer_len", 0) for r in results) / max(len(results), 1)

print(f"\n{'='*60}")
print(f"  결과: {ok_count}/{len(results)} 응답 정상(근거 있음)")
print(f"  근거 답변(grounded): {grounded_count}/{len(results)}")
print(f"  fallback(LLM/RAG 무발동): {fallback_count} | 환각 위험: {halluc_count}")
print(f"  평균 레이턴시: {avg_ms:.0f}ms")
print(f"  평균 답변 길이: {avg_len:.0f}자")
print(f"{'='*60}")

Path("reports").mkdir(exist_ok=True)
Path("reports/answer_e2e_report.json").write_text(
    json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print("\n  상세 결과: reports/answer_e2e_report.json")
