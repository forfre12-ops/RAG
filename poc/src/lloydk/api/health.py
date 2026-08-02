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
        # [calibration-visibility] 무보정(T=1.0) 서빙은 OOD 과신→고등급 무음미탐 위험이라
        # 운영 점검 대상. ok는 유지(서빙 가능)하되 calibrated/calibration_source를 노출한다.
        return {
            "status": "loaded",
            "ok": True,
            "model_dir": model_dir,
            "calibrated": getattr(svc.inference, "calibrated", None),
            "calibration_source": getattr(svc.inference, "_calibration_source", "unknown"),
        }
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
    """벡터스토어 헬스 — 백엔드 무관. ES는 _client.ping, PG(pgvector)는 engine SELECT 1.

    이전엔 ES `_client` 만 봐서 PG 백엔드는 _client 부재로 점검 없이 ok(false-ok)였다.
    PgVectorStore 는 _engine(SQLAlchemy)으로 실연결을 확인한다.
    """
    if getattr(settings, "vector_backend", "inmemory") == "inmemory":
        return {"status": "skipped", "ok": True}
    try:
        from lloydk.adapters.vectorstore import build_store  # noqa: PLC0415
        store = build_store()
        client = getattr(store, "_client", None)
        if client is not None:          # ES
            client.ping()
            return {"status": "ok", "ok": True}
        engine = getattr(store, "_engine", None)
        if engine is not None:          # PG (pgvector)
            from sqlalchemy import text  # noqa: PLC0415
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return {"status": "ok", "ok": True}
        return {"status": "ok", "ok": True}  # inmemory 폴백 등 — 점검 대상 없음
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


def _check_reranker() -> dict:
    """[C20] reranker effective 백엔드 가시화 — bge→noop graceful 폴백 노출.

    종전 /healthz는 settings.reranker_provider(설정값)만 보고해, bge 초기화 실패로 noop으로
    조용히 내려간 상태를 가렸다. 본 probe는 *이미 초기화된 캐시*만 읽어 effective 타입을
    노출한다(강제 get_reranker() 호출로 모델 로드를 유발하지 않는다 — health가 느려지지 않게).
    reranker는 품질 보강(+소폭)이라 폴백이 서비스-다운은 아니므로 ok=True 고정·가시화 전용.
    """
    configured = getattr(settings, "reranker_provider", "noop")
    try:
        from lloydk.adapters.reranker import _RERANKER_CACHE  # noqa: PLC0415
        inst = _RERANKER_CACHE.get(str(configured).lower())
        if inst is None:
            return {"status": "not_initialized", "ok": True,
                    "configured": configured, "effective": None}
        effective = type(inst).__name__
        fell_back = str(configured).lower() == "bge" and effective == "NoopReranker"
        return {
            "status": "fallback" if fell_back else "ok",
            "ok": True,
            "configured": configured,
            "effective": effective,
            "fell_back_to_noop": fell_back,
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "ok": True, "configured": configured,
                "detail": type(exc).__name__}


def _check_extractors() -> dict:
    """[C22] 포맷별 추출기/OCR 외부 의존성 가용성 진단 (best-effort, 실행 없이 탐지).

    추출기는 도구 부재 시 graceful degrade(quality=0.0 + error)라 서비스는 죽지 않지만,
    그 degrade가 startup/health에 안 떠서 'HWP 표 누락·PDF 스캔 OCR 불가'가 문서 단위
    무음 실패였다. import-가능성(find_spec)·바이너리 경로만 확인해 가시화한다(.txt/.md는
    의존 없이 항상 가능). optional이므로 ok=True 고정 — unavailable 목록만 노출.
    """
    import importlib.util  # noqa: PLC0415
    import shutil  # noqa: PLC0415

    def _mod(name: str) -> bool:
        try:
            return importlib.util.find_spec(name) is not None
        except Exception:  # noqa: BLE001
            return False

    try:
        from lloydk.modules.m2_preprocess.extractor import (  # noqa: PLC0415
            POPPLER_PATH,
            TESSERACT_CMD,
        )
    except Exception:  # noqa: BLE001
        POPPLER_PATH, TESSERACT_CMD = None, "tesseract"

    _tess_ok = (shutil.which(TESSERACT_CMD) is not None or Path(TESSERACT_CMD).exists()) and _mod("pytesseract")
    probes: dict[str, dict] = {
        "hwp_body(rhwp)": {"available": _mod("rhwp")},
        # .hwp 표 셀 회수(unhwp/MIT). 미설치면 rhwp 본문만 남아 표 속 등급·원가가
        # 분류기에 안 보인다 — 조용한 미탐이 되므로 가용성을 노출한다.
        "hwp_table(unhwp)": {"available": _mod("unhwp")},
        "xls(xlrd)": {"available": _mod("xlrd")},
        "xlsx(openpyxl)": {"available": _mod("openpyxl")},
        "docx(python-docx)": {"available": _mod("docx")},
        "pptx(python-pptx)": {"available": _mod("pptx")},
        "pdf_text(pdfminer)": {"available": _mod("pdfminer")},
        "pdf_table(pdfplumber)": {"available": _mod("pdfplumber")},
        "pdf_render(fitz/pdf2image)": {"available": _mod("fitz") or _mod("pdf2image")},
        "pdf_scan(poppler)": {"available": POPPLER_PATH is not None, "path": POPPLER_PATH},
        "ocr(tesseract)": {"available": bool(_tess_ok), "cmd": TESSERACT_CMD},
        "doc(antiword)": {"available": shutil.which("antiword") is not None},
    }
    unavailable = [k for k, v in probes.items() if not v["available"]]
    return {
        "status": "ok" if not unavailable else "degraded",
        "ok": True,  # optional 의존 — 서비스 가용성 판단엔 미반영(가시화 전용)
        "unavailable": unavailable,
        "probes": probes,
    }


def _check_compute() -> dict:
    """분류 처리량을 좌우하는 CPU·스레드 실효값 진단 — 조용한 3배 감속을 가시화한다.

    [배경 2026-08-02] 구형 .hwp 업로드 시 gunicorn 워커가 강제 재시작되던 증상을 추적하니
    파서가 아니라 처리량이 원인이었다. 8코어 호스트에서 API 컨테이너가 cgroup 으로 2 CPU 에
    묶여 1페이지 분류에 12.7초가 걸렸고(--timeout 60 → 6~7페이지에서 사망), 호스트는 부하
    0.28 로 놀고 있었다. 게다가 torch 는 cgroup 한도를 못 봐 8스레드를 띄워 오버서브스크립션
    까지 겹쳤다. 둘 다 어디에도 안 떠서 오래 방치됐다 — 그래서 여기에 노출한다.

    ok 는 항상 True(가용성 판단 아님). 실효 CPU 와 스레드가 어긋나면 status=degraded.
    """
    import os

    host_cpus = os.cpu_count() or 0
    quota = None
    try:  # cgroup v2 — 컨테이너에 실제로 허용된 CPU
        raw = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if raw[0] != "max":
            quota = round(int(raw[0]) / int(raw[1]), 2)
    except Exception:  # noqa: BLE001
        pass
    if quota is None:
        try:  # cgroup v1
            q = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
            p = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
            quota = round(q / p, 2) if q > 0 else None
        except Exception:  # noqa: BLE001
            pass

    threads = None
    try:
        import torch  # noqa: PLC0415

        threads = torch.get_num_threads()
    except Exception:  # noqa: BLE001
        pass

    effective = quota or host_cpus
    notes: list[str] = []
    if quota and host_cpus and quota <= host_cpus / 2:
        notes.append(
            f"컨테이너가 {quota} CPU 로 제한됨(호스트 {host_cpus}) — 분류 처리량이 그만큼 낮다. "
            f"단일 배포면 API_CPU_LIMIT 를 올릴 것"
        )
    if threads and effective and threads > effective + 0.5:
        notes.append(
            f"torch 스레드({threads}) > 실효 CPU({effective}) — 오버서브스크립션. "
            f"OMP_NUM_THREADS 를 CPU 한도에 맞출 것"
        )
    return {
        "status": "degraded" if notes else "ok",
        "ok": True,  # 처리량 진단 — 가용성 판단엔 미반영
        "host_cpus": host_cpus,
        "container_cpu_limit": quota,
        "torch_threads": threads,
        "omp_num_threads": os.environ.get("OMP_NUM_THREADS"),
        "notes": notes,
    }


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
        "reranker": _check_reranker(),
        "extractors": _check_extractors(),
        "compute": _check_compute(),
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
