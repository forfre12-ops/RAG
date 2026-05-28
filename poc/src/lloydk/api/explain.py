"""P1-A7: /classify/explain — 분류 근거(토큰 기여도 + 4 요인 + RAG context) 상세 반환.

기본 동작:
1) `/classify`를 거쳐 결과 획득 (evidence·factors·rag_context 포함)
2) evidence를 등급별 기여도로 재집계
3) 4 평가요소 가중 합산 분해
4) 운영시 학습된 분류기가 있으면 attention/IG 점수도 첨부 (옵션)

본 라우터는 검수자 UI(FUN-024)가 "왜 이 등급?"을 사용자에게 표시하기 위해 사용.
"""

from __future__ import annotations

from collections import defaultdict
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from lloydk.api.rate_limit import limiter
from lloydk.config import settings
from lloydk.schemas.classify import ClassifyRequest, ClassifyResponse
from lloydk.services.classify_service import ClassifyService

router = APIRouter(tags=["classify"])


def require_api_key(x_api_key: str = Header(...)):
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="invalid api key")


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
    """factors 점수의 가중 합산 분해 (검수자 가독성)."""
    if not result.factors:
        return {}
    f = result.factors
    weights = {
        "economic_value": 0.30,
        "non_publicity": 0.25,
        "management_level": 0.15,
        "leak_impact": 0.30,
    }
    rows = []
    for name, w in weights.items():
        s = float(getattr(f, name, 0.0) or 0.0)
        rows.append({
            "factor": name,
            "weight": w,
            "score": round(s, 4),
            "contribution": round(s * w, 4),
        })
    rows.sort(key=lambda r: -r["contribution"])
    total = round(sum(r["contribution"] for r in rows), 4)
    return {"rows": rows, "total": total}


@router.post("/classify/explain", dependencies=[Depends(require_api_key)])
@limiter.limit("30/minute")
def classify_explain(
    request: Request,
    req: ClassifyRequest,
    svc: ClassifyService = Depends(get_service),
):
    """동기 분류 + 근거 상세 분해.

    응답: ClassifyResponse + {explain: {evidence_aggregated, factor_decomposition}}
    """
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
