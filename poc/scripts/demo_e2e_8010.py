"""시나리오 A — 문서 업로드 → 분류 → 검수 E2E 데모 드라이버 (터미널판).

관리자 콘솔 [운영] 탭에서 손으로 누르는 것과 같은 순서를 터미널에서 한 번에 돌린다.
각 단계마다 대응하는 콘솔 카드를 함께 찍어, 터미널 시연과 화면 시연이 같은 대본이 되게 한다.

  ① 업로드      (추출·정규화·청킹·마스킹·원문저장)   ← parse_demo 화면
  ② 분류        (등급·신뢰도·근거)                    ← [운영] 1 분류 실행
  ③ 검수 확정   (/confirm)                            ← [운영] 2 분류 검수 큐
  ④ 검수 교정   (/relabel · 재학습 큐 적재)           ← [운영] 2 분류 검수 큐  ※ --relabel 일 때만

플래그:
  --relabel   ④ 교정 단계까지 수행한다. 기본은 ③ 확정까지만.
              교정은 S1→TS 상향(미탐 방향 = underclass)이라 재학습 큐에 쌓이고,
              누적이 RETRAIN_THRESHOLD_DEFAULT(10, confirm_service.py)에 닿으면
              active_learning_tick(30분)이 URGENT_RETRAIN 을 자동 큐잉한다
              (지재원 full-train 은 enable_training 이 열려 있어 이 경로가 산다).
              1회당 교정 2건이 쌓이므로 리허설을 반복하면 시연용 가짜 교정이 실제
              재학습을 트리거할 수 있다 — 그래서 기본에서 뺐다.
              감리에서 교정 화면을 보여줘야 할 때만 붙이고, 끝나면 OPERATION.md §8 로 정리한다.

루프 B(골든셋 → 재학습 → 배포)는 scripts/demo_e2e_golden.py 가 담당한다.

사용:
  cd poc
  .venv/Scripts/python.exe scripts/demo_e2e_8010.py
  # 다른 파일:  ... scripts/demo_e2e_8010.py datasets/acceptance_pack/docs/acc-TS-01.docx
  # 인자를 주지 않으면 추적되는 인수팩 문서 중 존재하는 첫 후보를 자동 선택한다.

환경변수(미설정 시 로컬 개발 기본값):
  DEMO_BASE_URL   기본 http://localhost:8010
  DEMO_API_KEY    기본 lloydk_dev_apikey (.env.example 값)

전제: 인프라 + API 가동. 실서버 리허설은 배포 서버의 BASE/KEY 를 넣어 그대로 돌린다.

되돌리기 주의 (2026-08-08 실측):
  이 스크립트가 만든 데이터는 화면 경로(parse_demo.html)와 같은 마커를 단다 —
  actor.user_id='demo-console' + RAG 컬렉션 'demo'. 그러나 POST /admin/demo/purge 는
  하드닝 프로파일(onprem-local·full-train)에서 404 다. demo_console_enabled=False 로
  운영에서 파괴적 물리삭제 표면을 없앤 의도된 설계이고, 데모·파일럿(lite-*)에서만 동작한다.
  따라서 실서버 리허설 데이터는 자동으로 지워지지 않는다 — 두 마커로 식별해 DB 측에서 정리한다.
"""
import io
import json
import os
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import httpx

# 하드코딩 대신 환경변수 — 같은 스크립트로 로컬과 실서버 리허설을 모두 돌릴 수 있어야 한다.
BASE = os.getenv("DEMO_BASE_URL", "http://localhost:8010").rstrip("/")
HEADERS = {"X-API-Key": os.getenv("DEMO_API_KEY", "lloydk_dev_apikey")}

# tenant 제거: 격리는 KL 포털 전담(단일 KL 인증). Actor 스키마에 tenant_id 필드가 없다.
ACTOR = json.dumps({"user_id": "demo-console", "role": "reviewer"})
REVIEWER = {"user_id": "demo-console", "role": "reviewer"}

# 기본 시연 문서 후보 — 전부 **git 추적** 경로라 배포본에도 실린다(위 _pick_default_doc 주석 참조).
DEFAULT_DOC_CANDIDATES = (
    "datasets/acceptance_pack/docs/acc-S1-04.pdf",
    "datasets/acceptance_pack/docs/acc-TS-01.docx",
    "datasets/acceptance_pack/docs/acc-S3-08.docx",
)

# 확장자 → MIME. 종전엔 무조건 application/pdf 를 보내 docx·hwp 를 올려도 pdf 로 신고했다.
_MIME = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".hwp": "application/x-hwp",
    ".hwpx": "application/hwp+zip",
    ".txt": "text/plain",
}

GRADE_DESC = {
    "TS": "특급기밀 — 유출 시 회사 존립 위협",
    "S1": "1급 비밀 — 유출 시 중대한 손해",
    "S2": "2급 대외비 — 유출 시 경쟁 불이익",
    "S3": "공개 — 일반 사내자료",
}


def line(c="─"):
    print(c * 68)


def step(no: str, title: str, card: str):
    """단계 머리글 — 콘솔의 어느 카드에 해당하는지 같이 찍는다."""
    print(f"\n[{no}] {title}")
    print(f"     콘솔 대응: {card}")


def _pick_default_doc() -> "Path | None":
    """기본 시연 문서 — **추적되는** 경로에서 고른다.

    종전 기본값 datasets/test_docs_pdf/ 는 .gitignore 대상이라 어떤 배포에도 실리지 않는다
    (실서버 실측 2026-08-08: "[오류] 파일 없음"으로 시나리오 A 가 시작조차 못 했다).
    인수팩 문서는 리포에 추적되므로 로컬·서버 어디서나 존재한다.
    """
    for rel in DEFAULT_DOC_CANDIDATES:
        p = Path(rel)
        if p.exists():
            return p
    return None


def main() -> int:
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_relabel = "--relabel" in sys.argv[1:]
    if argv:
        pdf = Path(argv[0])
    else:
        picked = _pick_default_doc()
        if picked is None:
            print("[오류] 기본 시연 문서를 찾지 못했습니다. 후보:")
            for rel in DEFAULT_DOC_CANDIDATES:
                print(f"        {rel}")
            print("       경로를 인자로 직접 주십시오.")
            return 1
        pdf = picked
    if not pdf.exists():
        print(f"[오류] 파일 없음: {pdf}")
        return 1

    line("═")
    print("  시나리오 A — 문서 업로드 → 분류 → 검수 (루프 A · 일상 운영)")
    print(f"  대상 서버: {BASE}")
    print(f"  대상 파일: {pdf.name}")
    line("═")

    with httpx.Client(timeout=180.0) as cli:
        # ── ① 업로드: 추출 → 정규화 → 청킹 → 마스킹 → 원문/정규화 저장 ──
        step("①", "업로드 → 텍스트추출·정규화·청킹·마스킹·원문저장", "parse_demo 화면 (문서 업로드)")
        with open(pdf, "rb") as f:
            r = cli.post(
                f"{BASE}/api/v1/documents",
                headers=HEADERS,
                files={"file": (pdf.name, f,
                                _MIME.get(pdf.suffix.lower(), "application/octet-stream"))},
                # rag_namespace='demo' 는 화면 경로(parse_demo.html)와 반드시 같아야 한다.
                # 생략하면 설정 기본값('uploads')으로 색인되는데, 데모 초기화
                # (POST /admin/demo/purge)의 스코프는 created_by='demo-console' + collection='demo'
                # 두 상수라 uploads 로 간 벡터는 지워지지 않는다 — 리허설을 반복할수록 잔류가 쌓인다.
                data={"actor": ACTOR, "index_for_rag": "true", "rag_namespace": "demo"},
            )
        if r.status_code >= 400:
            print("  [실패]", r.status_code, r.text[:300])
            return 1
        up = r.json()
        doc_id = up["doc_id"]
        print(f"  • doc_id           : {doc_id}")
        print(f"  • 추출방식/품질     : {up['extraction_method']} / {up['extraction_quality']}  (OCR={up['ocr_used']})")
        print(f"  • 추출 글자수       : {up['char_count']}자")
        print(f"  • 청크 개수         : {up['chunk_count']}")
        print(f"  • RAG 색인          : {up['rag_indexed']} (collection={up['rag_collection']}, vec={up['rag_vector_count']})")
        # 폐쇄망 스토리지는 로컬 파일시스템(LocalStorage + AES-256-GCM 암호화)이다.
        print("  • 원문 저장         : STORAGE_BACKEND 설정 경로(폐쇄망 기본 = 로컬 FS · 암호화 저장)")

        # ── ② 분류: 등급 · 신뢰도 · 4대 요소 · 근거토큰 ──
        step("②", "분류기 추론 (등급 · 신뢰도 · 근거)", "[운영] 탭 → 1 분류 실행")
        r = cli.post(
            f"{BASE}/api/v1/classify",
            headers=HEADERS,
            json={"doc_id": doc_id, "use_rag": False},
        )
        if r.status_code >= 400:
            print("  [실패]", r.status_code, r.text[:300])
            return 1
        cl = r.json()
        label = cl["label"]
        print(f"  • 예측 등급         : {label}  ({GRADE_DESC.get(label, '')})")
        print(f"  • 신뢰도            : {cl['confidence']:.4f}   model={cl['model_version']}  {cl['elapsed_ms']}ms")
        print("  • 점수분포          : " + "  ".join(f"{k}={v:.3f}" for k, v in cl["scores"].items()))
        ef = (cl.get("evaluation_factors") or {})
        if ef:
            s, v, m = ef.get("secrecy"), ef.get("value"), ef.get("management")
            print(f"  • 정본 3요건        : 비공지성(S)={s} 경제유용성(V)={v} 비밀관리성(M)={m}")
        for e in (cl.get("evidence") or [])[:6]:
            print(f"      근거 ▸ '{e['text']}'  (가중치 {e['weight']}, {e['tag']})")

        # ── ③ 검수 확정 ──
        step("③", "검수 — 확정(/confirm)", "[운영] 탭 → 2 분류 검수 큐 · [확정] 버튼")
        r = cli.post(
            f"{BASE}/api/v1/confirm",
            headers=HEADERS,
            json={
                "doc_id": doc_id,
                "confirmed_label": label,
                "note": "검수자 확인: 모델 판정 타당",
                "actor": REVIEWER,
            },
        )
        if r.status_code < 400:
            print(f"  • 확정 완료         : confirmation_id={r.json()['confirmation_id']}  → status=confirmed")
        else:
            print("  [실패]", r.status_code, r.text[:200])

        # ── ④ 검수 교정 (재학습 큐 적재) — 기본 비활성 ──
        if not do_relabel:
            line("═")
            print("  시나리오 A 완료 (③ 확정까지) — 여기까지가 루프 A(일상 운영)입니다.")
            print("  ④ 교정(/relabel) 단계는 재학습 큐에 쌓이므로 기본에서 뺐습니다.")
            print("  교정 화면까지 보여주려면 --relabel 을 붙이고, 끝나면 OPERATION.md §8 로 정리하세요.")
            print(f"  화면으로 같은 흐름 보기: {BASE}/demo/admin.html  → [운영] 탭")
            line("═")
            return 0

        higher = {"S3": "S2", "S2": "S1", "S1": "TS", "TS": "TS"}[label]
        step("④", f"검수 — 교정(/relabel) 시연: {label} → {higher} 상향",
             "[운영] 탭 → 2 분류 검수 큐 · [재라벨] 버튼")
        r = cli.post(
            f"{BASE}/api/v1/relabel",
            headers=HEADERS,
            json={
                "doc_id": doc_id,
                "inference_id": cl["inference_id"],
                "original_label": label,
                "corrected_label": higher,
                "reason": "검수자 판단: 민감도 상향 (재학습 반영 대상)",
                "actor": REVIEWER,
            },
        )
        if r.status_code < 400:
            rj = r.json()
            print(f"  • 교정 완료         : relabel_id={rj['relabel_id']}")
            print(f"  • 재학습 큐         : {rj['queue_size']}건 누적 (임계 {rj['retrain_threshold']}건)")
        else:
            print("  [실패]", r.status_code, r.text[:200])

    line("═")
    print("  시나리오 A 완료 (④ 교정까지) — 여기까지가 루프 A(일상 운영)입니다.")
    print("  ⚠ 교정이 재학습 큐에 쌓였습니다. 리허설을 마치면 OPERATION.md §8 로 정리하세요.")
    print("  이 교정들이 쌓이면 루프 B(모델 갱신)로 넘어갑니다:")
    print("    .venv/Scripts/python.exe scripts/demo_e2e_golden.py --register")
    print(f"  화면으로 같은 흐름 보기: {BASE}/demo/admin.html  → [운영] 탭")
    line("═")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
