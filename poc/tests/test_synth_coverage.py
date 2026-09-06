"""합성으로 채울 자리 — 격자 계산과 조회 경로.

왜(2026-09-06). 계산은 scripts/synth_coverage_gaps.py 안에만 있었다. 그래서 사람이
터미널에서 표를 읽고 → 조합을 외우고 → 콘솔 폼에 등급·도메인을 손으로 다시 넣어야 했다.
필요한 정보가 이미 있는데 화면이 그것을 몰랐다.

계산을 koipa.services.synth_coverage 로 올리고 GET /synth/coverage 를 붙였다. 이 시험이
잠그는 것은 셋이다.

  ① 격자 계산이 맞다 — 등급×도메인 건수, 실문서 유래 건수, 빈 칸·얇은 칸
  ② 도메인을 **정본으로 접어** 센다 — 안 접으면 빈 칸이 부풀려진다(실측 20→11)
  ③ 학습셋을 못 읽어도 500 이 아니다 — 격자는 참고 정보이고, 이것 때문에 생성 화면
     전체가 죽으면 안 된다
"""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("TESTING", "1")

from koipa.services.synth_coverage import (  # noqa: E402
    analyse,
    coverage_report,
    load_training_rows,
    render_text,
    row_domain,
)


def _row(label: str, domain: str, *, source: str = "synthetic") -> dict:
    return {"text": "본문", "label": label, "domain": domain, "source": source}


# ── ① 격자 계산 ────────────────────────────────────────────────────────────

def test_grid_counts_cells_and_real_origin():
    rows = [
        _row("TS", "반도체"), _row("TS", "반도체"),
        _row("S1", "반도체", source="public_real"),
        _row("S3", "public", source="public_real"),
    ]
    out = analyse(rows, min_per_cell=2)

    assert out["documents"] == 4
    assert out["grid"]["TS|반도체"] == 2
    assert out["grid"]["S1|반도체"] == 1
    assert out["grades"] == {"TS": 2, "S1": 1, "S2": 0, "S3": 1}
    # 실문서 유래 비중 — 빈 칸이라도 실문서가 있으면 합성이 급하지 않다는 판단의 근거다.
    assert out["real_share"] == 0.5


def test_thin_and_empty_cells_are_separated():
    """빈 칸과 얇은 칸은 성격이 다르다 — 하나는 '없다', 하나는 '모자라다'."""
    rows = [_row("TS", "반도체")] + [_row("S3", "public") for _ in range(5)]
    out = analyse(rows, min_per_cell=3)

    thin = {(c["grade"], c["domain"]): c["n"] for c in out["thin"]}
    empty = {(c["grade"], c["domain"]) for c in out["empty"]}

    assert thin == {("TS", "반도체"): 1}          # 1건 < 3 → 얇음
    assert ("S1", "반도체") in empty                # 0건 → 빔
    assert ("S3", "public") not in thin and ("S3", "public") not in empty  # 5건 → 충분
    # 얇은 칸은 적은 것부터 — 화면이 위에서부터 후보로 보여준다.
    assert [c["n"] for c in out["thin"]] == sorted(c["n"] for c in out["thin"])


# ── ② 도메인 정본 접기 ─────────────────────────────────────────────────────

def test_domain_is_folded_to_canonical():
    """영문·한글이 갈리면 빈 칸이 부풀려진다.

    실측: semiconductor 1 vs 반도체 159 로 갈려 있어 둘 다 얇은 칸으로 보고됐다.
    접어 세면 빈 칸 20→11 · 얇은 칸 18→14 다.
    """
    assert row_domain({"domain": "semiconductor"}) == "반도체"
    assert row_domain({"domain": "배터리"}) == "배터리"
    assert row_domain({"doc_type": "battery"}) == "배터리"     # doc_type 도 본다

    out = analyse([_row("TS", "semiconductor"), _row("TS", "반도체")], min_per_cell=3)
    assert out["grid"]["TS|반도체"] == 2, "접지 않으면 같은 산업이 두 칸으로 갈린다"
    assert "semiconductor" not in out["domains"]


def test_bio_is_not_folded_into_agriculture():
    """bio 는 접지 않는다 — 신약·임상과 품종·종자는 다른 산업이다."""
    out = analyse([_row("S1", "bio"), _row("S1", "바이오_농업")], min_per_cell=3)
    assert out["grid"]["S1|bio"] == 1
    assert out["grid"]["S1|바이오_농업"] == 1


# ── ③ 학습셋을 못 읽어도 화면이 죽지 않는다 ────────────────────────────────

def test_missing_dataset_reports_unavailable_instead_of_raising(tmp_path):
    out = coverage_report(dataset_dir=str(tmp_path / "없는경로"))
    assert out["available"] is False
    assert out["reason"], "왜 못 냈는지 사유가 있어야 화면이 설명할 수 있다"


def test_load_skips_missing_splits(tmp_path):
    (tmp_path / "train.jsonl").write_text(
        json.dumps(_row("TS", "반도체"), ensure_ascii=False) + "\n", encoding="utf-8"
    )
    rows = load_training_rows(tmp_path)          # val/test 는 없다
    assert len(rows) == 1


# ── 화면이 띄울 경고 ───────────────────────────────────────────────────────

def test_report_carries_the_human_judgement_caveat(tmp_path):
    """이 격자는 후보일 뿐 판단이 아니다 — 서버가 그 말을 함께 보낸다."""
    (tmp_path / "train.jsonl").write_text(
        "\n".join(json.dumps(_row("TS", "반도체"), ensure_ascii=False) for _ in range(3)),
        encoding="utf-8",
    )
    out = coverage_report(dataset_dir=str(tmp_path))
    assert out["available"] is True
    assert "사람" in out["caveat"] or "억지로" in out["caveat"]


def test_render_text_does_not_crash_on_unavailable():
    lines = render_text({"available": False, "reason": "없다"})
    assert lines and "없다" in lines[0]


# ── 조회 경로 ──────────────────────────────────────────────────────────────

def test_coverage_endpoint_answers():
    """화면이 격자를 받을 수 있어야 칸을 눌러 폼을 채울 수 있다."""
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from koipa.api.app import app  # noqa: PLC0415

    with TestClient(app) as cli:
        res = cli.get(
            "/api/v1/synth/coverage",
            headers={"X-API-Key": "test-key", "X-Actor-Role": "admin"},
        )
    if res.status_code == 404:
        pytest.skip("합성 라우터가 이 프로파일에서 꺼져 있다(enable_training=False)")
    assert res.status_code == 200, res.text
    body = res.json()
    assert "available" in body
    if body["available"]:
        assert body["documents"] > 0
        assert body["cells_total"] == 4 * len(body["domains"])
        for cell in body["thin"]:
            assert cell["n"] < body["min_per_cell"]
