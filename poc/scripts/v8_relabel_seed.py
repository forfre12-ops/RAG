"""v8 시드를 3상태 스키마로 재라벨한다 — 등급은 저술자가 적지 않고 파생시킨다.

배경(2026-08-13 실측). 시드 8건이 두 규칙으로 갈려 있었다:

    0001 (2,2,0) -> S1    raw      m=0 을 '입증된 부재'로 읽음
    0005 (1,2,0) -> S2    floored  m=0 을 '미언급'으로 읽음
    0008 (1,0,2) -> S2    floored  v=0 을 '미언급'으로 읽음

본문을 열어 판정한 결과 **0값 4건 전부 부재가 명시적으로 입증돼 있었다**:

    0001 관리성   "열람 제한을 따로 걸지 않았다 · 회수 기록은 남기지 않는다"
    0003 비공지성 "공개된 고시 본문에서 순서대로 옮겼다"
    0003 경제가치 "어디서나 같은 형태로 통용된다 · 선점 효과가 생기지 않는다"
    0005 관리성   "별도 권한 설정을 하지 않았다 · 이력이 남지 않는다"
    0008 경제가치 "동종 업무를 하는 곳이면 어디서나 같은 형태로 쓰인다"

따라서 전부 proven_absent 이고 등급은 raw 규칙이 맞다. **0005·0008 의 라벨이 틀렸다**
(S2 로 적혀 있으나 S3 다). 저술자가 본문은 부재를 입증하게 써 놓고 등급은 floor 규칙으로
매긴 것이다. 이 스크립트는 그 불일치를 구조적으로 없앤다 — 등급을 상태에서 파생시키므로
저술자가 등급을 잘못 적을 자리가 사라진다.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from v8_document_forms import FORM_BY_ID  # noqa: E402
from v8_factor_labels import (  # noqa: E402
    FACTORS,
    PRESENT,
    PROVEN_ABSENT,
    DocumentLabel,
    FactorLabel,
)

SRC = _HERE.parent / "datasets" / "v8_seed" / "seed_authored.jsonl"
OUT = _HERE.parent / "datasets" / "v8_seed" / "seed_authored.v2.jsonl"

# 부재를 **입증하는** 표현. 단순 미언급과 구별하기 위한 것이며, 여기 걸리지 않으면
# unknown 으로 떨어뜨린다(추측으로 proven_absent 를 주지 않는다).
ABSENCE_MARKERS = (
    "걸지 않았다", "하지 않았다", "남기지 않는다", "남지 않는다",
    "생기지 않는다", "필요하지 않다", "옮겼다", "통용되는", "어디서나",
    "그대로 전달해", "공개된",
)


def split_sections(text: str) -> list[tuple[str, str]]:
    """형태마다 섹션 표기가 다르다 — 마크다운 헤더와 번호식 둘 다 읽는다.

    번호식(work_manual 등)을 놓치면 요소 섹션을 못 찾아 라벨이 통째로 빠진다.
    """
    heads = list(re.finditer(r"(?m)^(?:##\s*|(?:\d+)\.\s+)(\S[^\n]*)$", text))
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        out.append((m.group(1).strip(), text[m.end():end].strip()))
    return out


def section_text(text: str, form_id: str, factor: str) -> tuple[str, str]:
    """해당 요소 섹션의 (제목, 본문)을 돌려준다."""
    form = FORM_BY_ID[form_id]
    titles = [t for t, kind in form["sections"] if kind == f"factor:{factor}"]
    if not titles:
        raise ValueError(f"{form_id} 에 factor:{factor} 섹션이 없다")
    title = titles[0]
    for head, body in split_sections(text):
        if head == title or head.startswith(title):
            return title, body
    raise ValueError(f"{form_id}/{title} 섹션을 본문에서 못 찾았다")


def first_sentence(body: str) -> str:
    """근거 span — 개조식이면 첫 항목, 산문이면 첫 문장."""
    body = body.strip()
    if body.startswith("- "):
        return body.split("\n")[0].lstrip("- ").strip()
    m = re.search(r"^(.+?[.다])\s", body + " ")
    return (m.group(1) if m else body[:120]).strip()


def to_factor_label(score: int, body: str) -> FactorLabel:
    span = first_sentence(body)
    if score == 0:
        proven = any(mk in body for mk in ABSENCE_MARKERS)
        if not proven:
            # 부재가 입증되지 않았다 -> unknown. span 을 붙이지 않는다(스키마 계약).
            return FactorLabel(state="unknown", direction="불명", reason="absence_not_proven")
        return FactorLabel(
            state=PROVEN_ABSENT, span=span, direction="부재",
            kind="본문진술", verified="텍스트상주장", reason="absence_stated_in_body",
        )
    return FactorLabel(
        state=PRESENT, level=score, span=span, direction="존재",
        kind="본문진술", verified="텍스트상주장", reason=f"present_level_{score}",
    )


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    rows = [json.loads(l) for l in SRC.read_text(encoding="utf-8").splitlines() if l.strip()]

    out_rows, changed = [], []
    for r in rows:
        scores = r["expected_factor_scores"]
        labels = {}
        for f in FACTORS:
            _, body = section_text(r["text"], r["form_id"], f)
            labels[f] = to_factor_label(int(scores[f]), body)

        doc = DocumentLabel(secrecy=labels["secrecy"], value=labels["value"],
                            management=labels["management"])
        derived = doc.grade()
        if derived != r["label"]:
            changed.append((r["doc_id"], r["label"], derived))

        out_rows.append({
            "doc_id": r["doc_id"],
            "form_id": r["form_id"],
            "document_type": r["document_type"],
            "text": r["text"],
            "label": derived,                       # 파생값 — 저술자가 적지 않는다
            "label_source": "derived_from_factor_states",
            "factor_labels": {
                f: {k: v for k, v in vars(labels[f]).items() if v not in (None, "")}
                for f in FACTORS
            },
            "svm": list(doc._triple(worst=False)),
            "svm_worst_case": list(doc._triple(worst=True)),
            "s3_kind": doc.s3_kind(),
            "schema": "v8-3state-1",
        })

    OUT.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in out_rows) + "\n",
                   encoding="utf-8")

    print(f"재라벨 {len(out_rows)}건 -> {OUT.relative_to(_HERE.parent)}")
    print(f"\n등급이 바뀐 건 {len(changed)}건 (저술자 라벨이 틀렸던 것):")
    for doc_id, old, new in changed:
        print(f"  {doc_id}  {old} -> {new}")

    print("\n전건 요약:")
    for x in out_rows:
        st = "/".join(x["factor_labels"][f]["state"][:4] for f in FACTORS)
        print(f"  {x['doc_id']}  svm={x['svm']}  {x['label']:3s}  states={st}  {x['s3_kind'] or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
