"""고객사 E2E 데모 드라이버 (2026-06-18) — 로컬 API :8010 대상.

전과정을 터미널에서 순서대로 시연:
  ① 업로드(추출·정규화·청킹·마스킹·원문저장) → ② 분류(등급·근거) →
  ③ 검토 승인(/confirm) → ④ 검토 교정(/relabel, active-learning 적재)

사용:
  cd poc
  .venv/Scripts/python.exe scripts/demo_e2e_8010.py
  # 다른 파일:  ... scripts/demo_e2e_8010.py datasets/test_docs_pdf/finance_S1_030.pdf

전제: docker 인프라 + 로컬 API(:8010) 가동, X-API-Key=devkey, tenant=default.
실시간 SSE 단계 스트리밍은 브라우저 http://localhost:8010/demo/ 에서 확인.
"""
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import httpx

BASE = "http://localhost:8010"
HEADERS = {"X-API-Key": "devkey"}
TENANT = "default"
ACTOR = json.dumps({"user_id": "demo-user", "tenant_id": TENANT, "role": "reviewer"})

GRADE_DESC = {
    "TS": "특급기밀 — 유출 시 회사 존립 위협",
    "S1": "1급 비밀 — 유출 시 중대한 손해",
    "S2": "2급 대외비 — 유출 시 경쟁 불이익",
    "S3": "공개 — 일반 사내자료",
}


def line(c="─"):
    print(c * 64)


def main() -> int:
    pdf = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("datasets/test_docs_pdf/business_S1_030.pdf")
    if not pdf.exists():
        print(f"[오류] 파일 없음: {pdf}")
        return 1

    line("═")
    print("  LLOYDK AI 영업비밀 등급분류 — 전과정 E2E 데모")
    print(f"  대상 파일: {pdf.name}")
    line("═")

    with httpx.Client(timeout=180.0) as cli:
        # ── ① 업로드: 추출 → 정규화 → 청킹 → 마스킹 → 원문/정규화 저장 ──
        print("\n[①] 업로드 → 텍스트추출·정규화·청킹·마스킹·원문저장")
        with open(pdf, "rb") as f:
            r = cli.post(
                f"{BASE}/api/v1/documents",
                headers=HEADERS,
                files={"file": (pdf.name, f, "application/pdf")},
                data={"actor": ACTOR, "index_for_rag": "true"},
            )
        if r.status_code >= 400:
            print("  [실패]", r.status_code, r.text[:300])
            return 1
        up = r.json()
        doc_id = up["doc_id"]
        print(f"  • doc_id          : {doc_id}")
        print(f"  • 추출방식/품질    : {up['extraction_method']} / {up['extraction_quality']}  (OCR={up['ocr_used']})")
        print(f"  • 추출 글자수      : {up['char_count']}자")
        print(f"  • 청크 개수        : {up['chunk_count']}")
        print(f"  • RAG 색인         : {up['rag_indexed']} (collection={up['rag_collection']}, vec={up['rag_vector_count']})")
        print(f"  • 원문 저장(MinIO) : documents-raw / documents-normalized 버킷")

        # ── ② 분류: 등급 · 신뢰도 · 4대 요소 · 근거토큰 ──
        print("\n[②] 분류기 추론 (KF-DeBERTa)")
        r = cli.post(
            f"{BASE}/api/v1/classify",
            headers=HEADERS,
            json={"doc_id": doc_id, "tenant_id": TENANT, "use_rag": False},
        )
        cl = r.json()
        label = cl["label"]
        print(f"  • 예측 등급        : {label}  ({GRADE_DESC.get(label, '')})")
        print(f"  • 신뢰도           : {cl['confidence']:.4f}   model={cl['model_version']}  {cl['elapsed_ms']}ms")
        print(f"  • 점수분포         : " + "  ".join(f"{k}={v:.3f}" for k, v in cl["scores"].items()))
        ef = (cl.get("evaluation_factors") or {})
        if ef:
            s, v, m = ef.get("secrecy"), ef.get("value"), ef.get("management")
            print(f"  • 정본 3요건       : 비공지성(S)={s} 경제유용성(V)={v} 비밀관리성(M)={m}")
            if None not in (s, v, m):
                print(f"  • 등급 산정(B안)   : S×V×M = {int(s)}×{int(v)}×{int(m)} = {int(s) * int(v) * int(m)}")
        for e in (cl.get("evidence") or [])[:6]:
            print(f"      근거 ▸ '{e['text']}'  (가중치 {e['weight']}, {e['tag']})")

        # ── ③ 검토자 승인 ──
        print("\n[③] 관리자 검토 — 승인(/confirm)")
        r = cli.post(
            f"{BASE}/api/v1/confirm",
            headers=HEADERS,
            json={
                "doc_id": doc_id,
                "confirmed_label": label,
                "note": "검토자 확인: 모델 판정 타당",
                "actor": {"user_id": "reviewer-kim", "tenant_id": TENANT, "role": "reviewer"},
            },
        )
        if r.status_code < 400:
            print(f"  • 승인 완료        : confirmation_id={r.json()['confirmation_id']}  → status=confirmed")
        else:
            print("  [실패]", r.status_code, r.text[:200])

        # ── ④ 검토자 교정 (active-learning 적재) ──
        higher = {"S3": "S2", "S2": "S1", "S1": "TS", "TS": "TS"}[label]
        print(f"\n[④] 관리자 검토 — 교정(/relabel) 시연: {label} → {higher} 상향")
        r = cli.post(
            f"{BASE}/api/v1/relabel",
            headers=HEADERS,
            json={
                "doc_id": doc_id,
                "inference_id": cl["inference_id"],
                "original_label": label,
                "corrected_label": higher,
                "reason": "검토자 판단: 민감도 상향 (active-learning 학습 반영 대상)",
                "actor": {"user_id": "reviewer-kim", "tenant_id": TENANT, "role": "reviewer"},
            },
        )
        if r.status_code < 400:
            rj = r.json()
            print(f"  • 교정 완료        : relabel_id={rj['relabel_id']}")
            print(f"  • 재학습 큐        : {rj['queue_size']}건 누적 (임계 {rj['retrain_threshold']}건 → 자동 재학습 트리거)")
        else:
            print("  [실패]", r.status_code, r.text[:200])

    line("═")
    print("  데모 완료. 실시간 단계 시각화는  http://localhost:8010/demo/")
    print("  학습 추적(MLflow)            는  http://localhost:5000")
    line("═")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
