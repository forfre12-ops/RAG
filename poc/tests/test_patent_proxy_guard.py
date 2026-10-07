# -*- coding: utf-8 -*-
"""공개특허-프록시 과라벨 가드 — 판정식과 **배선**을 함께 잠근다.

왜(2026-09-14). 가드 스크립트 `scripts/check_no_patent_proxy.py` 는 2026-07-26 감사 때
만들어졌고 헤더에 "미래 재빌드가 step3/patent_proxy 를 재유입시키는 것을 막는
빌드타임 가드가 없었다. 이 스크립트가 그 가드다" 라고 적혀 있다. 그런데 **아무 데도
배선되지 않아** 손으로 돌려야만 돌았다(Makefile 0건 · CI 0건, 실측 2026-09-14).

만들어 놓고 안 붙이면 없는 것과 같다. 그래서 이 시험은 판정식뿐 아니라
**Makefile·CI 에 실제로 걸려 있는지**까지 본다.

배경 수치 — 공개 특허공보는 비공지성 결여로 정의상 S3 인데
`labeled_p1_retrain_v4_step3/train.jsonl` 은 공개특허 7,200건을 S1 5,400 · TS 1,800 으로
라벨해 넣었다. 모델에 '특허 문체 = 고등급' shortcut 을 가르치는 오염원이다.
배포본 v5_clean 은 깨끗하다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

POC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(POC / "scripts"))

from check_no_patent_proxy import is_patent_proxy_overlabel  # noqa: E402


# ── 판정식 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("row", [
    {"label": "S1", "label_source": "patent_proxy_nkt"},
    {"label": "TS", "label_source": "patent_proxy_nkt"},
    {"label": "S2", "source": "공개특허"},
    {"label": "S1", "source": "특허공보"},
])
def test_overlabel_is_detected(row) -> None:
    assert is_patent_proxy_overlabel(row) is True


@pytest.mark.parametrize("row", [
    {"label": "S3", "source": "공개특허"},          # 공개특허를 S3 라 한 것은 정상이다
    {"label": "S3", "label_source": "patent_proxy_nkt"},
    {"label": "TS", "source": "내부문서"},          # 특허가 아니면 이 가드 대상이 아니다
    {"label": "S1", "source": "금융보고서"},
])
def test_clean_rows_pass(row) -> None:
    assert is_patent_proxy_overlabel(row) is False


def test_public_patent_as_s3_is_the_correct_label() -> None:
    """왜 S3 인가 — 우리 등급식이 직접 답한다. S(비공지성)=0 이면 곱이 0 이다."""
    sys.path.insert(0, str(POC / "src"))
    from koipa.modules.m3_labeling.rule_engine import grade_from_svm
    # 공개특허: 이미 공개됐으므로 S=0. 기술 가치가 높아도(V=2) 관리해도(M=2) S3 다.
    assert grade_from_svm(0, 2, 0) == "S3"
    assert grade_from_svm(0, 2, 2) == "S3"


# ── 배포 학습셋이 실제로 깨끗한가 ────────────────────────────────────────

@pytest.mark.parametrize("split", ["train", "val", "test"])
def test_deployed_training_set_is_clean(split: str) -> None:
    p = POC / f"datasets/labeled_p1_v5_clean/{split}.jsonl"
    if not p.exists():
        pytest.skip(f"{p.name} 없음")
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    hits = [r for r in rows if is_patent_proxy_overlabel(r)]
    assert not hits, (
        f"{split} 에 공개특허-프록시 과라벨 {len(hits)}건 — step3/patent_proxy 재유입 의심"
    )


def test_guard_actually_catches_the_known_polluted_set() -> None:
    """트립와이어가 헛돌지 않는지 — 오염된 셋에서 실제로 잡혀야 한다.

    검사기가 늘 빈 목록을 돌려주면 늘 초록불이다. 알려진 오염원으로 반증한다.
    """
    p = POC / "datasets/labeled_p1_retrain_v4_step3/train.jsonl"
    if not p.exists():
        pytest.skip("step3 셋 없음(gitignore) — 로컬에서만 검증 가능")
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    hits = [r for r in rows if is_patent_proxy_overlabel(r)]
    assert len(hits) == 7200, f"알려진 오염 7,200건이 {len(hits)}건으로 바뀌었다"


# ── ★ 배선 — 만들어 놓고 안 붙이면 없는 것과 같다 ───────────────────────

def test_guard_is_wired_into_makefile() -> None:
    mk = (POC / "Makefile").read_text(encoding="utf-8")
    assert "check-patent-proxy:" in mk, "Makefile 에 check-patent-proxy 타깃이 없다"
    assert "check_no_patent_proxy.py" in mk, "타깃이 가드 스크립트를 부르지 않는다"
    # train 만 보면 평가면 오염을 놓친다 — 채점이 거꾸로 된다
    for split in ("train", "val", "test"):
        assert f"labeled_p1_v5_clean/{split}.jsonl" in mk, f"{split} 이 가드 대상에서 빠졌다"


def test_guard_is_wired_into_ci() -> None:
    ci = POC.parent / ".github/workflows/poc-ci.yml"
    if not ci.exists():
        pytest.skip("CI 파일 없음")
    text = ci.read_text(encoding="utf-8")
    assert "make check-patent-proxy" in text, (
        "CI 에 가드가 안 걸려 있다 — 2026-07-26 에 만들고 배선을 잊은 상태로 돌아갔다"
    )
