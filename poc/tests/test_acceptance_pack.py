"""고객 인수 샘플팩 — 생성기 렌더러 round-trip + 러너 severity-floor veto 판정 단위 테스트.

핵심(모델 불요): 러너가 정확 등급일치가 아니라 **severity floor**(고등급 미탐=veto)로 판정하고,
숫자무손실/파싱실패도 veto 하며, over-분류(안전방향)는 통과시키는지 잠근다. 렌더러는 base-dep
포맷이 extract() 로 왕복되며 숫자가 무손실인지 검증한다(팩이 fake-green 이 아님을 보장).
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

_POC = Path(__file__).resolve().parents[1]
for _p in (_POC / "scripts", _POC / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import build_acceptance_pack as bap  # noqa: E402
import run_acceptance as ra  # noqa: E402


# ── 러너 판정(judge_doc) — severity floor veto (핵심, 모델 불요) ──────────────

def test_judge_clean_pass():
    j = ra.judge_doc("TS", ["840173"], parse_ok=True, text="문서 840173 특급기밀", pred_label="TS", status="staging")
    assert j["veto"] is False and j["under_classified"] is False and j["numeric_recall"] == 1.0


def test_judge_under_classification_vetoes():
    j = ra.judge_doc("TS", ["840173"], parse_ok=True, text="문서 840173", pred_label="S3", status="staging")
    assert j["veto"] is True and j["under_classified"] is True and "under_classified" in j["veto_reasons"]


def test_judge_numeric_loss_vetoes():
    j = ra.judge_doc("S2", ["840173"], parse_ok=True, text="문서(숫자 유실)", pred_label="S2", status="staging")
    assert j["veto"] is True and j["missing_numbers"] == ["840173"] and "numeric_loss" in j["veto_reasons"]


def test_judge_parse_fail_vetoes():
    j = ra.judge_doc("S1", ["1"], parse_ok=False, text="", pred_label=None, status="parse_error")
    assert j["veto"] is True and "parse_fail" in j["veto_reasons"]


def test_judge_over_classification_is_safe_not_veto():
    # 기대 S2 인데 S1(더 민감)로 = 안전방향 과분류 → veto 아님(보고만).
    j = ra.judge_doc("S2", ["1"], parse_ok=True, text="1", pred_label="S1", status="needs_review")
    assert j["veto"] is False and j["over_classified"] is True and j["under_classified"] is False


def test_judge_none_pred_treated_least_severe():
    # 분류 크래시(pred None) → 최저심각도(S3) 집계 → 고등급이면 under=veto (미탐이 통과로 둔갑 금지).
    j = ra.judge_doc("TS", ["1"], parse_ok=True, text="1", pred_label=None, status="classify_error")
    assert j["veto"] is True and j["under_classified"] is True


# ── 리포트 verdict 집계 ──────────────────────────────────────────────────────

def _res(**kw):
    base = dict(doc_id="d", format="txt", expected="S3", pred="S3", status="staging",
                parse_ok=True, numeric_recall=1.0, missing_numbers=[], under_classified=False,
                over_classified=False, exact=True, veto=False, veto_reasons=[], elapsed_ms=100)
    base.update(kw)
    return base


def test_report_pass_when_no_veto():
    _, s = ra._report([_res(), _res(expected="TS", pred="TS")], "inproc", 3000)
    assert s["verdict"] == "PASS" and s["veto_count"] == 0


def test_report_fail_on_any_veto():
    v = _res(expected="TS", pred="S3", under_classified=True, veto=True, veto_reasons=["under_classified"])
    _, s = ra._report([_res(), v], "inproc", 3000)
    assert s["verdict"] == "FAIL" and s["under_classified"] == 1


# ── 생성기 렌더러 round-trip (base-dep 포맷; 숫자 무손실) ─────────────────────

@pytest.fixture
def spec():
    seeds = bap.load_seeds()
    return bap._doc_spec(seeds[0], 1)


def _extract(path: Path):
    from lloydk.modules.m2_preprocess.extractor import extract
    return extract(str(path))


@pytest.mark.parametrize("fmt,renderer", [
    ("txt", bap.render_txt), ("docx", bap.render_docx),
    ("xlsx", bap.render_xlsx), ("pptx", bap.render_pptx),
])
def test_renderer_roundtrip_numeric_preserved(tmp_path, spec, fmt, renderer):
    path = tmp_path / f"s.{fmt}"
    assert renderer(spec, path) is True  # base dep — 항상 성공
    r = _extract(path)
    assert not r.error and r.quality > 0
    for num in spec["numbers"]:
        assert num in (r.text or ""), f"{fmt}: 숫자 {num} 유실"


def test_render_hwpx_roundtrip_if_available(tmp_path, spec):
    path = tmp_path / "s.hwpx"
    if not bap.render_hwpx(spec, path):
        pytest.skip("hwpx 템플릿 없음")
    r = _extract(path)
    if r.error or r.quality == 0:
        pytest.skip("rhwp([hwp] extra) 미설치 — round-trip 검증 스킵")
    for num in spec["numbers"]:
        assert num in (r.text or "")


# ── build_pack 매니페스트 구조 + 커밋 팩 정합 ───────────────────────────────

def test_build_pack_balanced_and_files_exist(tmp_path):
    m = bap.build_pack(tmp_path)
    bygrade = Counter(d["expected_grade"] for d in m["docs"])
    assert set(bygrade) == set(bap.GRADES)  # 전 등급 커버
    for d in m["docs"]:
        assert (tmp_path / d["file"]).exists()
        assert d["expected_numeric_tokens"], "기대 숫자 토큰 있어야(숫자무손실 검증 근거)"
        assert d["expected_grade"] in bap.GRADES


def test_committed_pack_manifest_consistent():
    """커밋된 datasets/acceptance_pack 이 매니페스트와 정합(파일 존재)."""
    import json
    pack = _POC / "datasets" / "acceptance_pack"
    mf = pack / "expected_labels.json"
    if not mf.exists():
        pytest.skip("커밋 팩 없음 — make acceptance-pack 선행")
    docs = json.loads(mf.read_text(encoding="utf-8"))["docs"]
    assert docs, "팩에 문서 0건"
    for d in docs:
        assert (pack / d["file"]).exists(), f"매니페스트 파일 누락: {d['file']}"
    # 전 포맷 대표성(적어도 txt/docx/xlsx 는 base-dep 라 항상 존재)
    fmts = {d["format"] for d in docs}
    assert {"txt", "docx", "xlsx"} <= fmts
