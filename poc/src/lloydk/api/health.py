"""헬스체크 엔드포인트 — live / ready / deep 3계층.

  GET /healthz       : 하위호환 단일 엔드포인트 (기존 동작 유지)
  GET /healthz/live  : 프로세스 생존 여부 (k8s liveness probe)
  GET /healthz/ready : 서비스 가능 여부 — DB/ES/MinIO/model 준비 상태 (readiness probe)
  GET /healthz/deep  : 전체 구성요소 상세 진단 (운영 대시보드용)

status 값:
  ok       : 모든 필수 구성요소 준비 완료
  degraded : 일부 구성요소 비정상 — 요청 처리는 되지만 정확도/기능 저하 가능
  down     : 필수 구성요소 실패 — 서비스 불가
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from lloydk.config import settings

router = APIRouter(tags=["health"])
_START = time.time()

# app.py lifespan이 _warmup_models 완료 후 True로 설정.
STARTUP_COMPLETE: bool = False


# ────────────────────────────────────────────────
# 개별 구성요소 probe
# ──────────────────────────────────────────────

def _check_model() -> dict:
    model_expected = bool(getattr(settings, "classifier_model_dir", ""))
    model_dir = getattr(settings, "classifier_model_dir", "")
    try:
        from lloydk.services.classify_service import ClassifyService  # noqa: PLC0415
        svc = ClassifyService.get_instance()
        model_loaded = svc.inference._model is not None
    except Exception:
        return {"status": "unknown", "ok": not model_expected, "model_dir": model_dir}
    if model_loaded:
        return {"status": "loaded", "ok": True, "model_dir": model_dir}
    if model_expected:
        return {"status": "degraded", "ok": False, "model_dir": model_dir}
    return {"status": "rule_fallback", "ok": True, "model_dir": model_dir}


def _check_db() -> dict:
    try:
        from lloydk.db import session_scope  # noqa: PLC0415
        with session_scope() as db:
            db.execute(__import__("sqlalchemy").text("SELECT 1"))
        return {"status": "ok", "ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "ok": False, "detail": type(exc).__name__}


def _check_es() -> dict:
    if getattr(settings, "vector_backend", "inmemory") == "inmemory":
        return {"status": "skipped", "ok": True}
    try:
        from lloydk.adapters.vectorstore import build_store  # noqa: PLC0415
        store = build_store()
        client = getattr(store, "_client", None)
        if client is not None:
            client.ping()
        return {"status": "ok", "ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "ok": False, "detail": type(exc).__name__}


def _check_storage() -> dict:
    """M-health-storage: ingestion이 실제로 쓰는 build_storage() 결과를 기준으로 점검.

    기존 버그: storage_backend=local이면 무조건 skip(ok)했지만, backend=minio라도
    build_storage()가 MinIO 미가용 시 LocalStorage로 *조용히 폴백*한다. 이때 폴백
    인스턴스는 _client가 없어 list_buckets 점검을 건너뛰고 ok로 통과 →
    ingestion은 MinIO를 기대하는데 health는 정상이라 보고하는 불일치.

    수정:
    - 설정상 local 백엔드: 폴백이 아니라 의도된 구성 → skipped(ok).
    - 설정상 원격(minio/seaweedfs): build_storage() 결과를 확인.
        * 결과가 local 인스턴스면 = MinIO→Local 폴백 발생 → degraded(ok=False).
        * 원격 인스턴스면 _client 연결(list_buckets)로 실연결 확인.
    """
    backend = getattr(settings, "storage_backend", "local")
    if backend == "local":
        return {"status": "skipped", "ok": True, "backend": backend}
    try:
        from lloydk.adapters.storage import build_storage  # noqa: PLC0415
        storage = build_storage()
        resolved = getattr(storage, "name", type(storage).__name__)
        client = getattr(storage, "_client", None)
        if client is None:
            # 원격 백엔드를 기대했지만 _client 없는 인스턴스(LocalStorage 등)로 폴백됨.
            return {
                "status": "degraded",
                "ok": False,
                "backend": backend,
                "resolved": resolved,
                "detail": "remote storage unavailable; fell back to local",
            }
        # 원격 클라이언트 — 실제 연결 확인.
        client.list_buckets()
        return {"status": "ok", "ok": True, "backend": backend, "resolved": resolved}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "ok": False, "backend": backend, "detail": type(exc).__name__}


def _check_embedder() -> dict:
    try:
        from lloydk.adapters.embedding import build_embedder  # noqa: PLC0415
        emb = build_embedder()
        emb.embed(["probe"])
        return {"status": "ok", "ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "ok": False, "detail": type(exc).__name__}


def _operational_config() -> dict:
    return {
        "classifier_model_dir": getattr(settings, "classifier_model_dir", ""),
        "rag": {
            "collection": getattr(settings, "rag_default_collection", "docs"),
            "embedding_model": getattr(settings, "rag_operational_embedding_model", ""),
            "search_mode": getattr(settings, "rag_operational_search_mode", ""),
            "chunk_size": getattr(settings, "rag_index_chunk_size", None),
            "chunk_overlap": getattr(settings, "rag_index_chunk_overlap", None),
        },
    }


def _readiness_snapshot() -> dict:
    path = Path("reports/operational_readiness.json")
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "path": str(path), "detail": type(exc).__name__}
    blocked = [g for g in payload.get("gates", []) if g.get("status") != "PASS"]
    return {
        "status": "ok",
        "path": str(path),
        "verdict": payload.get("verdict", "UNKNOWN"),
        "blocked_gates": [g.get("name") for g in blocked],
    }


# ──────────────────────────────────────────────
# 엔드포인트
# ──────────────────────────────────────────────

@router.get("/healthz/live")
def healthz_live():
    """k8s liveness probe — 프로세스 생존 여부만 확인. 항상 200."""
    return {"status": "ok", "uptime_sec": int(time.time() - _START)}


@router.get("/healthz/ready")
def healthz_ready():
    """k8s readiness probe — 서비스 가능 여부.

    DB / vectorstore / storage / model 모두 준비돼야 ready.
    하나라도 실패하면 503 반환 — 로드밸런서가 트래픽 차단하도록.
    """
    checks = {
        "model": _check_model(),
        "db": _check_db(),
        "vectorstore": _check_es(),
        "storage": _check_storage(),
        "warmup": {"status": "complete" if STARTUP_COMPLETE else "pending",
                   "ok": STARTUP_COMPLETE},
    }
    all_ok = all(c["ok"] for c in checks.values())
    status_code = 200 if all_ok else 503
    # 튜플 반환은 FastAPI가 배열로 직렬화하고 상태는 항상 200이 됨 →
    # 비정상 시 로드밸런서가 트래픽을 차단하려면 실제 503을 내려야 한다.
    return JSONResponse(
        status_code=status_code,
        content={"status": "ok" if all_ok else "not_ready", "checks": checks},
    )


@router.get("/healthz/deep")
def healthz_deep():
    """전체 구성요소 상세 진단 — 운영 대시보드/수동 점검용."""
    checks = {
        "model": _check_model(),
        "db": _check_db(),
        "vectorstore": _check_es(),
        "storage": _check_storage(),
        "embedder": _check_embedder(),
        "warmup": {"status": "complete" if STARTUP_COMPLETE else "pending",
                   "ok": STARTUP_COMPLETE},
    }
    degraded = [k for k, v in checks.items() if not v["ok"]]
    overall = "ok" if not degraded else ("degraded" if len(degraded) < len(checks) else "down")
    return {
        "status": overall,
        "degraded": degraded,
        "uptime_sec": int(time.time() - _START),
        "deploy_profile": getattr(settings, "deploy_profile", "unknown"),
        "operational_config": _operational_config(),
        "readiness": _readiness_snapshot(),
        "checks": checks,
    }


@router.get("/healthz")
def healthz():
    """하위호환 단일 엔드포인트 — 기존 /healthz 동작 유지 + 데모 콘솔용 필드."""
    model_check = _check_model()
    overall = "ok" if model_check["ok"] else "degraded"
    operational_config = _operational_config()
    return {
        "status": overall,
        "model_version": "poc",
        "uptime_sec": int(time.time() - _START),
        "deploy_profile": getattr(settings, "deploy_profile", "unknown"),
        "embedding_provider": getattr(settings, "embedding_provider", "unknown"),
        "llm_provider": getattr(settings, "llm_provider", "unknown"),
        "vector_backend": getattr(settings, "vector_backend", "unknown"),
        "reranker_provider": getattr(settings, "reranker_provider", "noop"),
        "classifier_model_dir": operational_config["classifier_model_dir"],
        "rag": operational_config["rag"],
        "operational_config": operational_config,
        "readiness": _readiness_snapshot(),
        "warmup_done": STARTUP_COMPLETE,
        "checks": {
            "model": model_check["status"],
        },
    }
