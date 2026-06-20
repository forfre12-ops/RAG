"""P1-A7: /classify/explain — 분류 근거(토큰 기여도 + 4 요인 + RAG context) 상세 반환.

기본 동작:
1) `/classify`를 거쳐 결과 획득 (evidence·factors·rag_context 포함)
2) evidence를 등급별 기여도로 재집계
3) 4 평가요소 가중 합산 분해
4) 운영시 학습된 분류기가 있으면 attention/IG 점수도 첨부 (옵션)

본 라우터는 검수자 UI(FUN-024)가 "왜 이 등급?"을 사용자에게 표시하기 위해 사용.
"""

from collections import defaultdict
from fastapi import APIRouter, Depends, Request

from lloydk.api._jwt_auth import require_auth, resolve_effective_tenant
from lloydk.api.rate_limit import limiter
from lloydk.schemas.classify import ClassifyRequest, ClassifyResponse
from lloydk.services.classify_service import ClassifyService

router = APIRouter(tags=["classify"])


def get_service() -> ClassifyService:
    return ClassifyService.get_instance()


def _aggregate_evidence(result: ClassifyResponse) -> dict:
    """evidence 토큰을 등급별·요인별로 재집계."""
    by_grade: dict[str, list[dict]] = defaultdict(list)
    by_factor: dict[str, list[dict]] = defaultdict(list)
    for e in result.evidence or []:
        item = {
            "token": getattr(e, "token", ""),
            "weight": float(getattr(e, "weight", 0.0)),
            "factor": getattr(e, "factor", ""),
            "grade": getattr(e, "grade", ""),
            "positions": list(getattr(e, "positions", []) or []),
        }
        if item["grade"]:
            by_grade[item["grade"]].append(item)
        if item["factor"]:
            by_factor[item["factor"]].append(item)

    grade_summary = {
        g: {
            "count": len(items),
            "total_weight": round(sum(i["weight"] for i in items), 4),
            "top_tokens": sorted(items, key=lambda i: -i["weight"])[:5],
        }
        for g, items in by_grade.items()
    }
    factor_summary = {
        f: {
            "count": len(items),
            "total_weight": round(sum(i["weight"] for i in items), 4),
        }
        for f, items in by_factor.items()
    }
    return {"by_grade": grade_summary, "by_factor": factor_summary}


def _factor_decomposition(result: ClassifyResponse) -> dict:
    """3요건(S·V·M) 점수 분해 — B안 곱셈식이라 최저 요소가 등급을 제약 (검수자 가독성)."""
    if not result.factors:
        return {}
    f = result.factors
    rows = [
        {"factor": "secrecy", "name": "비공지성(S)", "score": round(float(getattr(f, "secrecy", 0.0) or 0.0), 4)},
        {"factor": "value", "name": "경제적 유용성(V)", "score": round(float(getattr(f, "value", 0.0) or 0.0), 4)},
        {"factor": "management", "name": "비밀관리성(M)", "score": round(float(getattr(f, "management", 0.0) or 0.0), 4)},
    ]
    # 곱셈식에서는 가장 낮은 요소가 등급을 제약(0이면 곱=0=공개). 그 요소를 함께 노출.
    limiting = min(rows, key=lambda r: r["score"]) if rows else None
    return {
        "rows": rows,
        "method": "multiplicative(S×V×M)",
        "limiting_factor": limiting["factor"] if limiting else None,
    }


@router.post("/classify/explain", dependencies=[Depends(require_auth)])
@limiter.limit("30/minute")
def classify_explain(
    request: Request,
    req: ClassifyRequest,
    svc: ClassifyService = Depends(get_service),
):
    """동기 분류 + 근거 상세 분해.

    응답: ClassifyResponse + {explain: {evidence_aggregated, factor_decomposition}}
    """
    # 보안(IDOR): /classify와 동일하게 body tenant_id를 인증 컨텍스트에 결속.
    # 누락 시 위조한 tenant_id로 타 테넌트 문서·검증라벨·근거 토큰까지 노출된다.
    req.tenant_id = resolve_effective_tenant(request, req.tenant_id)
    result = svc.classify(req)
    body = result.model_dump(mode="json")
    body["explain"] = {
        "evidence_aggregated": _aggregate_evidence(result),
        "factor_decomposition": _factor_decomposition(result),
        "rag_context_count": len(result.rag_context or []),
        "warnings": list(result.warnings or []),
        "method": "rule+rag" if not result.model_version or result.model_version == "poc" else "model+rule+rag",
    }
    return body
