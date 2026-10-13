# -*- coding: utf-8 -*-
"""규정 참고 표시의 문서별 조회 지연을 실제 PostgreSQL(pgvector) 위에서 잰다 — 예산 p95 200ms(설계서 §2.10).

무엇을 재나
  GET /documents/{doc_id}/regulation-evidence 가 하는 일 그대로: 문서 대표 벡터 읽기(pgvector) → 청크 글자 읽기 → 조항 순위합산 → 문장 선택.
  ① 서비스 계층(RegulationEvidenceService.find_for_document) — 첫 호출(색인 적재 포함)과 이후 호출을 나눠서
  ② HTTP 계층(TestClient, 인증·권한·속도 제한 미들웨어 포함) — 검수 화면이 실제로 겪는 값에 가깝다

무엇을 재지 않나
  임베딩 시간(조회는 임베딩을 부르지 않는다 — 문서 벡터는 이미 저장돼 있다). 벡터 값은 결정형 가짜라 **적중 품질을 재지 않는다**.
  (품질·색인 속도는 measure_regulation_runtime.py 가 운영 임베더로 잰다.)

안전
  DB 에 시험 행(문서·청크·문서 벡터·규정)을 넣고 끝에서 **모두 지운다**. 그래서 localhost 가 아닌 DB 는 --allow-remote 없이는 거절한다.
  운영·시험 서버 DB 에는 돌리지 않는다.

사용:
  DATABASE_URL=postgresql+psycopg://koipa:koipa_dev@localhost:15433/koipa \\
    python scripts/measure_regulation_latency.py --regulation 규정.md [--docs 30] [--calls 300] [--json out.json]
"""

from __future__ import annotations

import argparse
import io
import json
import os
import statistics
import sys
import tempfile
import time
import types
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

DIM = 1024                 # tad_dm_doc_vctr_mng 의 vector(1024) 와 같아야 한다
MODEL = "latency-fake"


def _pct(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))] if s else 0.0


def _summary(xs_ms: list[float]) -> dict:
    return {"n": len(xs_ms), "p50_ms": round(_pct(xs_ms, 0.5), 2), "p95_ms": round(_pct(xs_ms, 0.95), 2),
            "p99_ms": round(_pct(xs_ms, 0.99), 2), "max_ms": round(max(xs_ms), 2) if xs_ms else 0.0,
            "mean_ms": round(statistics.fmean(xs_ms), 2) if xs_ms else 0.0}


class FakeEmbedder:
    """글자 2-gram 을 1024 차원에 해시한 결정형 임베더 — 값은 중요하지 않고 **크기와 차원**만 운영과 같다."""
    name = "latency-fake"
    dim = DIM

    def embed(self, texts):
        import hashlib
        import re

        import numpy as np

        from koipa.adapters.embedding.base import EmbeddingResult
        out = []
        for t in texts:
            v = np.zeros(DIM, dtype=np.float32)
            s = re.sub(r"\s+", "", t)
            for i in range(len(s) - 1):
                v[int(hashlib.md5(s[i:i + 2].encode()).hexdigest(), 16) % DIM] += 1.0
            out.append(v.tolist())
        return EmbeddingResult(vectors=out, dim=DIM, model=MODEL)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regulation", required=True)
    ap.add_argument("--docs", type=int, default=30, help="시험 문서 수")
    ap.add_argument("--chunks", type=int, default=12, help="문서당 청크 수(운영 문서 중앙값은 7 안팎)")
    ap.add_argument("--calls", type=int, default=300, help="따뜻한 호출 횟수")
    ap.add_argument("--json", default="")
    ap.add_argument("--allow-remote", action="store_true")
    a = ap.parse_args(argv)
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

    os.environ.setdefault("TESTING", "1")
    os.environ.setdefault("REGULATION_REFERENCE_ENABLED", "true")
    # HTTP 계층을 재려면 인증이 필요하다 — 이 프로세스 안에서만 쓰는 시험 키다(밖으로 나가지 않는다)
    os.environ.setdefault("AUTH_MODE", "api_key")
    os.environ.setdefault("API_KEY", "latency-bench-key")
    os.environ.setdefault("API_KEY_ROLE", "admin")
    from sqlalchemy.engine import make_url  # noqa: PLC0415

    from koipa.config import settings  # noqa: PLC0415
    host = make_url(settings.database_url).host or "localhost"
    if host not in ("localhost", "127.0.0.1", "::1") and not a.allow_remote:
        print(f"⛔ DB 가 localhost 가 아니다({host}) — 시험 행을 넣고 지우는 도구라 거절한다. 정말 필요하면 --allow-remote.")
        return 2

    import numpy as np  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415

    from koipa.adapters.storage.local_store import LocalStorage  # noqa: PLC0415
    from koipa.adapters.vectorstore.document_vectors import DocumentVectorStore  # noqa: PLC0415
    from koipa.db import SessionLocal, engine  # noqa: PLC0415
    from koipa.db.models import Chunk, Document  # noqa: PLC0415
    from koipa.services.regulation_evidence_service import RegulationEvidenceService  # noqa: PLC0415
    from koipa.services.regulation_service import RegulationService  # noqa: PLC0415

    with engine.connect() as conn:
        if conn.execute(text("SELECT to_regclass('tad_rm_rgltn_mng')")).scalar() is None:
            print("⛔ 규정 표가 없다 — alembic upgrade head 를 먼저 적용한 DB 가 필요하다.")
            return 2

    cfg = types.SimpleNamespace(max_upload_mb=20, regulation_max_sentences=3000, regulation_active_max=5,
                                regulation_evidence_max_items=1, regulation_min_similarity=0.0)
    tmp = tempfile.mkdtemp(prefix="reg_latency_")
    RegulationEvidenceService.reset_singleton()
    holder: dict = {}
    svc = RegulationService(storage=LocalStorage(tmp), embedder_factory=FakeEmbedder,
                            dispatcher=lambda rid: holder["svc"].index(rid), settings_obj=cfg)
    holder["svc"] = svc
    evidence = RegulationEvidenceService(settings_obj=cfg)      # 저장소·세션은 운영과 같은 것(pgvector, session_scope)
    RegulationEvidenceService._instance = evidence

    reg_text = Path(a.regulation).read_text(encoding="utf-8")
    body = reg_text.encode("utf-8") + f"\n<!-- latency {uuid.uuid4()} -->\n".encode()     # 같은 파일 중복 등록 방지
    doc_ids: list[str] = []
    reg_id: str | None = None
    out: dict = {"inputs": {"regulation": a.regulation, "docs": a.docs, "chunks_per_doc": a.chunks, "calls": a.calls},
                 "note": "벡터 값은 결정형 가짜 — 지연만 잰다. 적중 품질이 아니다."}
    try:
        # 규정 하나를 등록·색인·활성화한다
        t0 = time.perf_counter()
        res = svc.register(data=body, filename="latency.md", name=f"latency-{uuid.uuid4().hex[:8]}", version_label="v1",
                           effective_date=None, actor_id="latency-bench", actor_role="admin")
        reg_id = res.reg_id
        svc.activate(reg_id, scope_confirmed=True, scope_note="지연 측정", actor_id="latency-bench", actor_role="admin")
        d = svc.get(reg_id)
        out["regulation"] = {"clauses": d["clause_count"], "display_clauses": d["display_clause_count"],
                             "sentences": d["sentence_count"], "index_and_activate_seconds": round(time.perf_counter() - t0, 2)}
        print(f"규정: 조항 {d['clause_count']} · 표시 대상 {d['display_clause_count']} · 문장 {d['sentence_count']}")

        # 시험 문서 — 문서 행 + 청크(약 500자) + 대표 벡터. 본문은 규정의 문단을 돌려 써서 낱말 채널에도 일이 생기게 한다.
        paras = [p.strip() for p in reg_text.split("\n") if len(p.strip()) > 30] or [reg_text]
        store = DocumentVectorStore()
        from koipa.modules.m2_preprocess.chunker import Chunk as ChunkRec  # noqa: PLC0415
        with SessionLocal() as db:
            for i in range(a.docs):
                doc = Document(filename=f"latency-{i}-{uuid.uuid4().hex[:6]}.txt", source_format="txt")
                db.add(doc)
                db.flush()
                chunks = []
                for j in range(a.chunks):
                    t = " ".join(paras[(i * 7 + j * 3 + k) % len(paras)] for k in range(4))[:500]
                    chunks.append(ChunkRec(index=j, text=t, char_count=len(t)))
                for c in chunks:
                    db.add(Chunk(doc_id=doc.doc_id, chunk_index=c.index, content=c.text, token_count=len(c.text) // 2,
                                 char_count=c.char_count))
                db.commit()
                doc_ids.append(str(doc.doc_id))
                v = FakeEmbedder().embed(["\n".join(c.text for c in chunks)]).vectors[0]
                n = float(np.linalg.norm(v)) or 1.0
                store.upsert(doc_id=str(doc.doc_id), embedding=[x / n for x in v], model=MODEL, chunk_count=len(chunks))
        print(f"시험 문서 {len(doc_ids)}건 준비(청크 {a.chunks}개씩)")

        # ① 서비스 계층
        t = time.perf_counter()
        first = evidence.find_for_document(doc_ids[0])
        cold_ms = (time.perf_counter() - t) * 1000
        warm: list[float] = []
        shown = 0
        for k in range(a.calls):
            t = time.perf_counter()
            r = evidence.find_for_document(doc_ids[k % len(doc_ids)])
            warm.append((time.perf_counter() - t) * 1000)
            shown += bool(r.items)
        out["service"] = {"cold_first_call_ms": round(cold_ms, 2), "warm": _summary(warm), "answered_with_items": shown,
                          "first_call_had_items": bool(first.items)}
        print(f"서비스 첫 호출(색인 적재 포함) {cold_ms:.1f}ms · 이후 {_summary(warm)}")

        # ② HTTP 계층
        from fastapi.testclient import TestClient  # noqa: PLC0415

        from koipa.api.app import app  # noqa: PLC0415
        headers = {"X-API-Key": os.environ["API_KEY"], "X-Actor-Role": "admin"}
        with TestClient(app) as client:
            probe = client.get(f"/api/v1/documents/{doc_ids[0]}/regulation-evidence", headers=headers)
            if probe.status_code != 200:
                out["http"] = {"skipped": f"{probe.status_code} — 이 환경에서는 HTTP 계층을 못 쟀다(인증 설정)"}
                print(f"HTTP 계층은 건너뛴다: {probe.status_code} {probe.text[:120]}")
            else:
                http_ms: list[float] = []
                for k in range(min(a.calls, 200)):            # 속도 제한(분당 120)에 걸리지 않게 창을 넘지 않는다
                    t = time.perf_counter()
                    r = client.get(f"/api/v1/documents/{doc_ids[k % len(doc_ids)]}/regulation-evidence", headers=headers)
                    http_ms.append((time.perf_counter() - t) * 1000)
                    if r.status_code == 429:
                        break
                out["http"] = _summary(http_ms)
                print(f"HTTP 계층: {out['http']}")
        budget = 200.0
        p95 = out["service"]["warm"]["p95_ms"]
        out["budget_p95_ms"] = budget
        out["service_warm_within_budget"] = p95 <= budget
        print(f"예산 p95 {budget:.0f}ms — 서비스 계층 따뜻한 호출 p95 {p95}ms → {'안' if p95 <= budget else '밖'}")
    finally:
        # 시험 행을 지운다(하드 삭제 — 문서 벡터·조항·문장은 FK CASCADE).
        # 감사 로그(규정 등록·활성화 기록)는 지우지 않는다 — 해시 체인이라 지우는 도구를 만들지 않는다.
        with engine.begin() as conn:
            if doc_ids:
                conn.execute(text("DELETE FROM tad_cm_chnk_mng WHERE doc_id = ANY(CAST(:ids AS uuid[]))"), {"ids": doc_ids})
                conn.execute(text("DELETE FROM tad_dm_doc_mng WHERE doc_id = ANY(CAST(:ids AS uuid[]))"), {"ids": doc_ids})
            if reg_id:
                conn.execute(text("DELETE FROM tad_rm_rgltn_mng WHERE rgltn_id = CAST(:r AS uuid)"), {"r": reg_id})
        RegulationEvidenceService.reset_singleton()
        print("시험 행 삭제 완료")
    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"저장: {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
