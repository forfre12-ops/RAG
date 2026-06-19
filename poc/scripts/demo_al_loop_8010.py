"""능동학습 루프 라이브 데모 — :8010 (문서 시드 → classify → confirm → relabel → 큐 증가).

content-only 분류는 doc_id가 documents에 없어 persistence가 audit-only가 된다. 여기서는
tenant 'default'에 실 Document를 시드한 뒤 그 doc_id로 분류해 classification이 DB에 저장되게
하고, confirm/relabel이 실제 correction을 적재해 재학습 큐가 증가하는 A2 경로를 라이브로 보인다.
끝나면 시드 정리.
"""
from __future__ import annotations

import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import httpx  # noqa: E402  (stdout UTF-8 래핑 후 import — 데모 출력 인코딩 보장)

BASE = "http://localhost:8010"
H = {"X-API-Key": "devkey"}
T = "default"
SECRET = ("마스터 키 롤링 절차: HSM root CA 개인키 90일 교체. EUV 노광 레시피·DRAM 공정 "
          "파라미터(sccm, Torr) 사외 유출 금지 — 특급 영업비밀. 고객사 단가표·원가구조 포함.")


def seed_document() -> str:
    from lloydk.db import SessionLocal
    from lloydk.db.models import Chunk, Document
    s = SessionLocal()
    try:
        doc = Document(tenant_id=T, filename="demo_secret.pdf", source_format="pdf",
                       text_preview=SECRET[:2000], char_count=len(SECRET),
                       processing_status="completed")
        s.add(doc)
        s.flush()
        s.add(Chunk(doc_id=doc.doc_id, tenant_id=T, chunk_index=0,
                    content=SECRET, token_count=len(SECRET.split()), char_count=len(SECRET)))
        s.commit()
        return str(doc.doc_id)
    finally:
        s.close()


def cleanup(doc_id: str):
    import uuid as _u

    from lloydk.db import session_scope
    from lloydk.db.models import Chunk, Classification, Correction, Document
    try:
        with session_scope() as db:
            du = _u.UUID(doc_id)
            cls_ids = [c.classification_id for c in
                       db.query(Classification).filter_by(doc_id=du).all()]
            if cls_ids:
                db.query(Correction).filter(Correction.classification_id.in_(cls_ids)).delete(
                    synchronize_session=False)
            db.query(Classification).filter_by(doc_id=du).delete()
            db.query(Chunk).filter_by(doc_id=du).delete()
            db.query(Document).filter_by(doc_id=du).delete()
    except Exception as exc:  # noqa: BLE001
        print(f"[cleanup warn] {exc}")


def al_status() -> dict:
    from lloydk.modules.m6_evaluation.active_learning import evaluate_retraining_need
    return evaluate_retraining_need().to_dict()


def main() -> int:
    print("=" * 64)
    print("  LLOYDK 능동학습 루프 라이브 데모 (문서 시드 → classify → confirm → relabel)")
    print("=" * 64)
    doc_id = seed_document()
    print(f"① 문서 시드 완료: doc_id={doc_id} (tenant=default)")
    before = al_status()
    print(f"   재학습 큐(전): unconsumed={before['unconsumed_total']} status={before['retrain_status']}")

    try:
        with httpx.Client(timeout=180.0) as cli:
            r = cli.post(f"{BASE}/api/v1/classify", headers=H,
                         json={"doc_id": doc_id, "tenant_id": T, "use_rag": False, "content": SECRET})
            d = r.json()
            inf = d.get("inference_id")
            print(f"\n② classify → {d['label']} conf={d['confidence']:.3f} "
                  f"(persist: {'OK' if not any('persistence skipped' in w for w in (d.get('warnings') or [])) else 'SKIPPED'})")
            for w in (d.get("warnings") or []):
                print(f"     ⚑ {w}")

            r = cli.post(f"{BASE}/api/v1/confirm", headers=H,
                         json={"doc_id": doc_id, "inference_id": inf, "confirmed_label": d["label"],
                               "note": "검토 확인",
                               "actor": {"user_id": "rev-1", "tenant_id": T, "role": "reviewer"}})
            print(f"\n③ confirm → {r.status_code}")

            higher = {"S3": "S2", "S2": "S1", "S1": "TS", "TS": "TS"}[d["label"]]
            r = cli.post(f"{BASE}/api/v1/relabel", headers=H,
                         json={"doc_id": doc_id, "inference_id": inf, "original_label": d["label"],
                               "corrected_label": higher, "reason": "민감도 상향",
                               "actor": {"user_id": "rev-2", "tenant_id": T, "role": "reviewer"}})
            rj = r.json()
            print(f"④ relabel {d['label']}→{higher} → {r.status_code}  queue_size={rj.get('queue_size')}")

        after = al_status()
        print(f"\n⑤ 재학습 큐(후): unconsumed={after['unconsumed_total']} "
              f"underclass={after['pending_underclass']} status={after['retrain_status']}")
        delta = after["unconsumed_total"] - before["unconsumed_total"]
        print(f"   → 큐 증가량 Δ={delta} (검토 교정이 active-learning에 적재됨)")
        ok = delta >= 1
        print("\n" + ("=== 능동학습 루프 라이브 검증 PASS ===" if ok else "=== 큐 미증가 (확인 필요) ==="))
        return 0 if ok else 1
    finally:
        cleanup(doc_id)
        print("[cleanup] 시드 문서/분류/교정 정리 완료")


if __name__ == "__main__":
    raise SystemExit(main())
