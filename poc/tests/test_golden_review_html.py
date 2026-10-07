"""koipa.golden_review_html — jsonl → 서명 화면 렌더 단위 시험.

[2026-08-19] 옛 검토본 렌더러(render_review_html)가 삭제되면서 이 파일이 비게 됐는데,
그때 **jsonl 읽기 경로가 시험 없이 남는다**는 것을 발견해 그쪽으로 바꿔 썼다.

여기서 지키는 계약 하나 — `paths[0]` 이 서명 대상(gold)이고 `paths[1:]` 이 보기 전용이다.
apply_signoff 가 gold_path 의 doc_id 만 서명 대상으로 삼기 때문이며, 이 순서가 어긋나면
2026-08-18 에 났던 사고(전달본 120건이 전부 보기 전용이 되어 아무것도 서명 못 함)가 되돌아온다.
"""
import json

from koipa.golden_review_html import render_signoff_html, render_signoff_html_from_jsonl

_GOLD = {
    "doc_id": "d1", "text": "기밀 문서 본문", "label": "S1", "llm_grade": "S1",
    "rule_grade": "S1", "llm_confidence": 0.9, "status": "gold_consensus",
    "review_status": "accepted", "agreement": True, "domain": "legal",
}
_UNCERTAIN = {
    "doc_id": "d2", "text": "애매한 문서", "label": None, "llm_grade": "S1",
    "rule_grade": "S2", "llm_confidence": 0.6, "status": "disagree",
    "review_status": "needs_review", "agreement": False, "domain": "finance",
}


def _embedded(html: str) -> list[dict]:
    return json.loads(html.split('type="application/json">')[1].split("</script>")[0])


def test_render_substitutes_every_placeholder():
    html = render_signoff_html([_GOLD], job_id="J", post_url="/p")
    assert html.startswith("<!doctype html>")
    for ph in ("__DATA__", "__TITLE__", "__TOTAL__", "__JOB__", "__POST_URL__",
               "__MINPG__", "__NAV__", "__DOC_RENDER_JS__"):
        assert ph not in html, f"{ph} 가 화면에 그대로 남았다"


def test_grade_falls_back_to_llm_when_label_is_missing():
    """label 이 없는 후보(합의 미달)도 등급 배지가 있어야 카드가 그려진다."""
    data = _embedded(render_signoff_html([], job_id="J", post_url="/p", pending=[_UNCERTAIN]))
    assert data[0]["grade"] == "S1"          # label=None → llm_grade
    assert data[0]["signable"] is False


def test_first_path_is_signable_and_the_rest_are_view_only(tmp_path):
    """paths[0]=gold · paths[1:]=보기 전용. 이 순서가 곧 계약이다."""
    gold = tmp_path / "build_x.jsonl"
    unc = tmp_path / "uncertain_x.jsonl"
    gold.write_text(json.dumps(_GOLD, ensure_ascii=False) + "\n", encoding="utf-8")
    unc.write_text(json.dumps(_UNCERTAIN, ensure_ascii=False) + "\n", encoding="utf-8")

    data = _embedded(render_signoff_html_from_jsonl([gold, unc], job_id="J", post_url="/p"))
    assert {d["id"]: d["signable"] for d in data} == {"d1": True, "d2": False}


def test_missing_paths_are_skipped_not_fatal(tmp_path):
    """없는 경로가 섞여도 있는 것만 읽는다 — 잡 산출물 일부가 없을 수 있다."""
    gold = tmp_path / "build_x.jsonl"
    gold.write_text(json.dumps(_GOLD, ensure_ascii=False) + "\n", encoding="utf-8")
    data = _embedded(render_signoff_html_from_jsonl(
        [gold, tmp_path / "없는파일.jsonl"], job_id="J", post_url="/p"))
    assert [d["id"] for d in data] == ["d1"]


def test_full_text_is_embedded_not_a_preview(tmp_path):
    """검수 판단은 전문 기준이다 — 옛 검토본처럼 150자로 자르면 안 된다."""
    long_doc = dict(_GOLD, text="가" * 4000)
    data = _embedded(render_signoff_html([long_doc], job_id="J", post_url="/p"))
    assert len(data[0]["text"]) == 4000
