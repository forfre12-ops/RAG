"""연합평가 리포트 — 고객사 폐쇄망 내부 교정을 실분포 평가로 집계 (반출 0).

폐쇄망 안에서만 실행한다. tb_corrections(사람 검수자의 확정 교정)를 (모델예측, 사람정답)
쌍으로 읽어 federated_eval로 층화 집계한다. 산출:
  - 현장 리포트(full): reports/federated_eval.json — 폐쇄망 내부 소비 전용.
  - 반출 페이로드(scalar-only): reports/federated_eval.export.json — redact_for_export로
    최소화·assert_export_safe로 유출 0 강제. **반출 승인 절차를 거친 뒤에만 반출**.

문서/텍스트/doc_id/검수자 신원은 EvalPair 계약에 없어 구조적으로 유출 불가.

review_context(선택편향 층):
  - --escalation-tau 지정 시 신뢰도 **프록시**로 층화(context_source=confidence_proxy):
    conf < τ = escalated_conf_proxy(어려운 케이스 쏠림·FNR 상한 성격),
    conf >= τ = high_confidence_conf_proxy(무작위 감사 아님·편향 표본의 고신뢰 부분집합).
    ⚠️ 어느 쪽도 무편향 아님 — random_audit(무편향)은 스키마에 명시적 감사 플래그가 생겨야
    채워진다(현재 미존재). 고신뢰 검수분을 '감사 표본'으로 오인하지 말 것.
  - 미지정 시 전부 unknown(편향 성격 미상, 단독 인용 금지).

사용(폐쇄망 내부):
  python scripts/federated_eval_report.py --escalation-tau 0.8
  python scripts/federated_eval_report.py --since 2026-01-01
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from koipa.golden_tiers import is_human_reviewer  # noqa: E402
from koipa.modules.m6_evaluation.federated_eval import (  # noqa: E402
    CTX_ESCALATED_PROXY,
    CTX_HIGH_CONF_PROXY,
    CTX_RANDOM_AUDIT,
    CTX_UNKNOWN,
    EvalPair,
    build_federated_report,
    redact_for_export,
)


def rows_to_pairs(
    rows, *, escalation_tau: float | None, audit_doc_ids: "frozenset[str] | None" = None,
) -> list[EvalPair]:
    """DB 행(dict) → EvalPair. 사람 검수자 교정만·등급 코드/신뢰도/맥락만 취한다(순수·테스트 가능).

    행 계약: {model_pred, human_truth, confidence, corrected_by, doc_id}. corrected_by가 사람
    검수자(is_human_reviewer)가 아니면 제외(머신/플레이스홀더 교정은 실분포 정답 아님).

    review_context 우선순위:
      1. doc_id가 audit_doc_ids(무작위 감사 배치 매니페스트)에 있으면 CTX_RANDOM_AUDIT
         — **유일한 무편향 층**. 명시적 감사 사실이라 신뢰도 프록시보다 우선.
      2. escalation_tau 있으면 신뢰도 프록시(escalated/high_confidence — 둘 다 편향, 무편향 아님).
      3. 그 외 unknown.
    doc_id/corrected_by는 admission·맥락 판정에만 쓰이고 EvalPair/리포트/반출로 흐르지 않는다
    (구조적 무유출 — EvalPair 계약에 식별 필드 없음).
    """
    audit = audit_doc_ids or frozenset()
    pairs: list[EvalPair] = []
    for r in rows:
        mp = str(r.get("model_pred") or "").strip()
        ht = str(r.get("human_truth") or "").strip()
        if not mp or not ht:
            continue
        if not is_human_reviewer(r.get("corrected_by")):
            continue  # 머신/플레이스홀더 교정 = 실분포 정답 아님(admission)
        conf = r.get("confidence")
        conf = float(conf) if conf is not None else None
        did = str(r.get("doc_id") or "")
        if did and did in audit:
            ctx = CTX_RANDOM_AUDIT  # 명시적 감사 배치 = 무편향(신뢰도 프록시보다 우선)
        elif escalation_tau is not None and conf is not None:
            # 신뢰도 프록시 — 어느 층도 무편향(random_audit) 아님. 고신뢰 검수분을
            # '감사 표본'으로 오표기하지 않는다(적대검증 B 반영).
            ctx = CTX_ESCALATED_PROXY if conf < escalation_tau else CTX_HIGH_CONF_PROXY
        else:
            ctx = CTX_UNKNOWN
        pairs.append(EvalPair(model_pred=mp, human_truth=ht, confidence=conf, review_context=ctx))
    return pairs


def load_audit_manifest(path: str) -> "frozenset[str]":
    """무작위 감사 배치 매니페스트 → doc_id 집합(폐쇄망 내부 입력, 반출 대상 아님).

    형식: jsonl({"doc_id": "..."} 또는 {"classification_id"→미지원, doc_id 권장) 또는 줄당 doc_id
    평문. 이 파일은 '어떤 문서가 무작위로 감사 검수됐는가'를 기록하며 federated 리포트의
    무편향 층(random_audit)을 채운다. 리포트/반출에는 doc_id가 실리지 않는다(맥락 판정에만).
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    ids: set[str] = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("{"):
            try:
                obj = json.loads(line)
                did = str(obj.get("doc_id") or "").strip()
                if did:
                    ids.add(did)
            except json.JSONDecodeError:
                continue
        else:
            ids.add(line)
    return frozenset(ids)


def _fetch_rows(since: dt.date | None) -> list[dict]:
    """폐쇄망 DB에서 교정×분류×등급 조인 → 행 dict. **집계 필드만 SELECT**(문서/텍스트 제외)."""
    from sqlalchemy import select  # noqa: PLC0415

    from koipa.db import SessionLocal  # noqa: PLC0415
    from koipa.db.models import Classification, ClassificationLevel, Correction  # noqa: PLC0415

    orig = ClassificationLevel.__table__.alias("orig")
    corr = ClassificationLevel.__table__.alias("corr")
    stmt = (
        select(
            orig.c.level_code.label("model_pred"),
            corr.c.level_code.label("human_truth"),
            Classification.confidence.label("confidence"),
            Correction.corrected_by.label("corrected_by"),
            Classification.doc_id.label("doc_id"),  # 감사 매니페스트 조인·맥락 판정용(반출 안 됨)
        )
        .join(Classification, Correction.classification_id == Classification.classification_id)
        .join(orig, Correction.original_level_id == orig.c.level_id)
        .join(corr, Correction.corrected_level_id == corr.c.level_id)
    )
    if since is not None:
        stmt = stmt.where(Correction.corrected_at >= dt.datetime(since.year, since.month, since.day))
    db = SessionLocal()
    try:
        return [dict(m._mapping) for m in db.execute(stmt).all()]
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--escalation-tau", type=float, default=None,
                    help="신뢰도 프록시 층화 임계(conf<τ=escalated). 미지정=전부 unknown. ⚠️프록시.")
    ap.add_argument("--audit-manifest", default="",
                    help="무작위 감사 배치 doc_id 매니페스트(jsonl/평문). 해당 문서=random_audit"
                         "(무편향 층). 폐쇄망 내부 입력 — doc_id는 반출 안 됨.")
    ap.add_argument("--since", default="", help="YYYY-MM-DD 이후 교정만(기본 전체)")
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--report", default="reports/federated_eval.json")
    ap.add_argument("--export", default="reports/federated_eval.export.json")
    args = ap.parse_args()

    since = None
    if args.since.strip():
        y, m, d = (int(x) for x in args.since.split("-"))
        since = dt.date(y, m, d)

    audit_ids: "frozenset[str]" = frozenset()
    if args.audit_manifest.strip():
        try:
            audit_ids = load_audit_manifest(args.audit_manifest)
            print(f"[federated] 감사 매니페스트 {len(audit_ids)}건 → random_audit(무편향) 층", file=sys.stderr)
        except FileNotFoundError:
            print(f"[federated] 감사 매니페스트 없음: {args.audit_manifest}", file=sys.stderr)
            return 2

    try:
        rows = _fetch_rows(since)
    except Exception as exc:  # noqa: BLE001
        print(f"[federated] DB 조회 실패({type(exc).__name__}: {exc}) — 폐쇄망 DB 연결 확인", file=sys.stderr)
        return 2

    pairs = rows_to_pairs(rows, escalation_tau=args.escalation_tau, audit_doc_ids=audit_ids)
    if not pairs:
        print("[federated] 사람 검수자 교정 0건 — 아직 운영 검수 신호 없음(배포 전/초기)", file=sys.stderr)
        return 1

    report = build_federated_report(pairs, min_n=args.min_n)
    export = redact_for_export(report)

    out = Path(args.report)
    if not out.is_absolute():
        out = _HERE.parent / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    exp = Path(args.export)
    if not exp.is_absolute():
        exp = _HERE.parent / exp
    exp.write_text(json.dumps(export, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "pairs": len(pairs),
        "contexts": {c: b["n"] for c, b in report["by_context"].items()},
        "onsite_report": str(out),
        "export_payload": str(exp),
        "note": "export_payload는 스칼라-only. 반출 승인 절차 후에만 반출(반출0 정책).",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
