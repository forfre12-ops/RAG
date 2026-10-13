"""짧은 레거시 noop 합성 문서를 읽을 수 있는 테스트 문서 형식으로 보강한다.

``noop``은 외부 모델을 호출하지 않는 파이프라인 점검용 제공자다. 과거 산출물 중
짧은 한 문장 본문만 남은 테스트 행은 문서 열기에서 빈 화면처럼 보인다. 이 도구는
그 테스트 행만 결정론적·등급 중립 본문으로 바꾸며, 실제 제공자 문서와 이미 충분히
긴 noop 문서는 건드리지 않는다. noop은 서비스의 학습 편입 규칙에서 계속 제외된다.
"""

from __future__ import annotations

from koipa.adapters.llm.noop_provider import build_noop_fixture_body
from koipa.db import session_scope
from koipa.db.models import SampleDocument


MAX_LEGACY_BODY_CHARS = 260
FIXTURE_LABEL_SOURCE = "noop_fixture_expanded_v1"


def backfill_legacy_noop_documents() -> int:
    """짧은 noop 테스트 본문만 보강하고 변경 건수를 돌려준다."""
    changed = 0
    with session_scope() as db:
        rows = (
            db.query(SampleDocument)
            .filter(SampleDocument.llm_provider == "noop")
            .all()
        )
        for row in rows:
            if len((row.generated_content or "").strip()) >= MAX_LEGACY_BODY_CHARS:
                continue
            domain = (getattr(row, "domain_code", None) or row.doc_type or "mixed").strip()
            row.generated_content = build_noop_fixture_body(domain)
            row.label_source = FIXTURE_LABEL_SOURCE
            changed += 1
    return changed


if __name__ == "__main__":
    print(f"expanded_noop_documents={backfill_legacy_noop_documents()}")
