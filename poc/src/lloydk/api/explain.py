"""P1-A7: /classify/explain — 분류 근거(증거 토큰 재집계 + S/V/M 요인 분해 + RAG context) 반환.

동작:
1) `/classify`를 거쳐 결과 획득 (evidence·factors·rag_context·warnings 포함)
2) evidence 토큰을 등급별·요인별로 재집계 (_aggregate_evidence)
3) 정본 3요건(S·V·M) 곱셈식 점수 분해 + 등급을 제약하는 최저 요소 노출 (_factor_decomposition)
4) RAG context 건수·경고·판정 경로(rule vs model) 메타 첨부

본 라우터는 검수자 UI(FUN-024)가 "왜 이 등급?"을 사용자에게 표시하기 위해 사용.
근거는 **룰 시드 증거 span 기반**이다. 학습 분류기의 attention/IG 등 토큰 어트리뷰션은
현재 미구현(FUN-024 요구 사항 아님) — 향후 확장 시 이 라우터에 첨부한다.
"""

from collections import defaultdict
from fastapi import APIRouter, Depends, Request

from lloydk.api._jwt_auth import require_auth
from lloydk.api.rate_limit import limiter
from lloydk.schemas.classify import ClassifyRequest, ClassifyResponse
from lloydk.services.classify_service import ClassifyService

router = APIRouter(tags=["classify"])


def get_service() -> ClassifyService:
    return ClassifyService.get_instance()


def _aggregate_evidence(result: ClassifyResponse) -> dict:
    """evidence span을 요인(factor)별로 재집계.

    EvidenceSpan 스키마는 {start,end,text,weight,tag}이고 tag=요인 코드(S/V/M)다
    (m3_labeling/pipeline.py에서 `tag=m.factor`). span 단위 등급은 존재하지 않으므로
    (문서 등급은 result.label 하나뿐) 요인별 집계만 제공한다.

    과거엔 스키마에 없는 필드(token/factor/grade/positions)를 getattr 기본값으로 읽어
    by_grade·by_factor가 **항상 빈값**이었다(死집계). 실 필드(text/tag/start·end)로 정합화.
    """
    by_factor: dict[str, list[dict]] = defaultdict(list)
    for e in result.evidence or []:
        factor = e.tag or ""
        if not factor:
            continue
        by_factor[factor].append(
            {
                "token": e.text,
                "weight": float(e.weight or 0.0),
                "span": [e.start, e.end],
            }
        )

    factor_summary = {
        f: {
            "count": len(items),
            "total_weight": round(sum(i["weight"] for i in items), 4),
            "top_tokens": sorted(items, key=lambda i: -i["weight"])[:5],
        }
        for f, items in by_factor.items()
    }
    return {"by_factor": factor_summary}


def _method_label(model_version: str | None) -> str:
    """판정 경로 표기 — 학습 분류기 사용('model+rule+rag') vs 룰 폴백('rule+rag').

    rule-fallback(모델 미로드) 경로의 model_version 은 'rule-fallback-v0'/'rule-fallback'
    (m5_inference/pipeline.py·classify_service.py) 또는 'poc'(기본값)/'none'/빈값이다.
    과거엔 == 'poc' 만 검사해 실제 폴백 문자열 'rule-fallback-v0'를 'model'로 오표기했다
    (rule 판정을 model 사용으로 둔갑). 폴백 마커를 정확히 룰 경로로 판정한다.
    """
    mv = (model_version or "").strip().lower()
    is_model = bool(mv) and mv not in ("poc", "none") and not mv.startswith("rule-fallback")
    return "model+rule+rag" if is_model else "rule+rag"


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
    # tenant 제거: 격리는 KL 포털 전담 → 무스코프 분류.
    result = svc.classify(req)
    body = result.model_dump(mode="json")
    body["explain"] = {
        "evidence_aggregated": _aggregate_evidence(result),
        "factor_decomposition": _factor_decomposition(result),
        "rag_context_count": len(result.rag_context or []),
        "warnings": list(result.warnings or []),
        "method": _method_label(result.model_version),
    }
    return body
