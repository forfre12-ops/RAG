"""lloydk.golden_review_html 검토본 렌더 단위 테스트 (G4-html).

빌더 후보 레코드 → 인터랙티브 HTML. 렌더 자체는 content-check(문서·등급·필터·임베드 JSON).
"""
import json

from lloydk.golden_review_html import render_review_html, render_review_html_from_jsonl

_RECS = [
    {
        "doc_id": "d1", "text": "기밀 문서 본문", "label": "S1", "llm_grade": "S1",
        "rule_grade": "S1", "llm_confidence": 0.9, "status": "gold_consensus",
        "review_status": "accepted", "agreement": True, "domain": "legal",
    },
    {
        "doc_id": "d2", "text": "애매한 문서", "label": None, "llm_grade": "S1",
        "rule_grade": "S2", "llm_confidence": 0.6, "status": "disagree",
        "review_status": "needs_review", "agreement": False, "domain": "finance",
    },
]


def test_render_contains_records_filters_and_data():
    html = render_review_html(_RECS, title="테스트 검토본")
    assert html.startswith("<!doctype html>")
    assert "테스트 검토본" in html
    # 플레이스홀더 전부 치환
    for ph in ("__DATA__", "__TITLE__", "__TOTAL__", "__GOLD__", "__UNCERTAIN__"):
        assert ph not in html
    # 임베드 JSON에 두 문서 + 후보등급
    assert '"id": "d1"' in html and '"id": "d2"' in html
    assert '"grade": "S1"' in html
    # 등급/상태 필터 버튼
    assert 'data-v="gold"' in html and 'data-v="uncertain"' in html
    assert "gold 후보" in html


def test_uncertain_grade_falls_back_to_llm():
    # label=None(검수대상)이면 후보등급은 llm_grade(S1)로 표시, is_gold=False
    html = render_review_html([_RECS[1]])
    data = json.loads(html.split('type="application/json">')[1].split("</script>")[0])
    assert data[0]["grade"] == "S1" and data[0]["is_gold"] is False


def test_render_from_jsonl(tmp_path):
    bp = tmp_path / "build_x.jsonl"
    up = tmp_path / "uncertain_x.jsonl"
    bp.write_text(json.dumps(_RECS[0], ensure_ascii=False) + "\n", encoding="utf-8")
    up.write_text(json.dumps(_RECS[1], ensure_ascii=False) + "\n", encoding="utf-8")
    html = render_review_html_from_jsonl([bp, up])
    assert '"id": "d1"' in html and '"id": "d2"' in html
    data = json.loads(html.split('type="application/json">')[1].split("</script>")[0])
    assert len(data) == 2 and sum(1 for d in data if d["is_gold"]) == 1
