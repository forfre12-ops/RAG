"""Review serialization/binding safety; not semantic accuracy tests."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "scripts"))

import prepare_content_reference_review as review  # noqa: E402
from content_reference_review_notes import DEV_NOTES  # noqa: E402
from evaluation_inputs import sha256  # noqa: E402


_PACK_MANIFEST = review.PACK / "manifest.json"
_FILE_PIN_ERROR = "Historical source changed"


def _require_pack():
    if not _PACK_MANIFEST.exists():
        pytest.skip("동결 pack 이 없다 — reports/CONTENT_REFERENCE_20260914 는 git 무시 산출물이라 새 체크아웃에는 없다")


def _frozen_sources():
    """동결 자료가 이 체크아웃에서 재현될 때만 이 시험이 뜻을 가진다 — 그렇지 않은 두 경우는 실패가 아니라 건너뜀으로 드러낸다.

    ① pack(reports/CONTENT_REFERENCE_20260914)은 git 무시 산출물이라 새 체크아웃·CI 에는 없다.
    ② pack 이 역사 원본(datasets/gold_real/holdout_eval*.jsonl)의 **파일 해시**를 고정해 두었는데, 그 값은 pack 을 만든 작업트리의
       바이트(CRLF) 기준이고 holdout_eval.jsonl 은 2026-09-20 에 22행 라벨이 정정돼(5137f153) 지금은 그 판이 아니다.
       prepare_content_reference_review.py 를 고쳐 풀 수 없다 — 이 스크립트의 해시가 하류 검수 매니페스트(prepare_content_reference_revision.REVIEW_SHA →
       manifest.source_files)에 다시 고정돼 있다. 풀려면 pack 을 새 판으로 다시 동결해야 하고 그것은 이 병행 트랙의 소유자가 할 일이다.
       그동안 이 검수가 실제로 읽는 네 행의 본문·라벨은 아래 시험이 행 단위로 지킨다.
    그 밖의 무결성 오류("Frozen ... changed" 등)는 그대로 실패한다.
    """
    _require_pack()
    try:
        return review.source_inputs()
    except ValueError as exc:
        if str(exc) == _FILE_PIN_ERROR:
            pytest.skip("pack 이 고정한 역사 원본 파일 해시가 지금 파일과 다르다(2026-09-20 holdout109 22행 정정·줄바꿈) — pack 재동결 필요")
        raise


@pytest.fixture
def sources():
    return _frozen_sources()


@pytest.fixture
def records(sources):
    docs, answers, historical, _ = sources
    return review.development_reviews(docs, answers), review.legacy_reviews(historical)


def test_all_94_have_exact_evidence_and_not_approved(sources, records):
    dev, legacy = records
    result = review.validate_reviews(dev, legacy, sources[0], sources[2])
    assert result["development_reviewed"] == 90 and result["legacy_reviewed"] == 4
    assert result["development_dispositions"] == {"amend_material": 40, "keep": 40, "needs_context": 10}
    assert result["legacy_dispositions"] == {"needs_context": 3, "propose_under_reference": 1}
    assert result["exact_evidence_spans"] == 101
    assert result["development_grade_changes_proposed"] == 0
    assert not result["model_accuracy_measured"] and not result["independent_human_review"]


def test_material_amendments_are_not_40_label_errors(records):
    dev, _ = records
    amendments = [r for r in dev if r["disposition"] == "amend_material"]
    assert len(amendments) == 40
    assert all(r["proposal"]["grade"] == r["source_answer"]["grade"] for r in amendments)
    assert all(r["original_label_replacement"] is None for r in amendments)


def test_three_legacy_abstentions_and_one_new_reference_proposal(records):
    legacy = {r["doc_id"]: r for r in records[1]}
    assert legacy["823545b7edf3a0ef"]["proposal"]["grade"] == "S1"
    assert legacy["9a4ace0da18602c1"]["proposal"]["grade"] is None
    assert legacy["6ea073680b55d1e9"]["proposal"]["grade"] is None
    assert legacy["a1beb524ceafe108"]["proposal"]["grade"] is None
    assert all(r["original_label_replacement"] is None for r in legacy.values())
    assert all(not r["historical_policy_version_verified"] for r in legacy.values())
    assert all(not r["historical_legal_reference_verified"] for r in legacy.values())


def test_sealed_bodies_not_parsed_for_this_review(monkeypatch):
    original = review.read_rows
    opened = []
    def guarded(path):
        opened.append(str(path))
        assert "sealed_candidate" not in str(path)
        return original(path)
    monkeypatch.setattr(review, "read_rows", guarded)
    docs, _, _, protected = _frozen_sources()
    assert len(docs) == 90
    assert any("sealed_candidate" in str(p) for p in protected)
    assert opened


def _disputed_rows_drift(issues, read_rows):
    """pack 이 고정한 역사 행(4건 x 양쪽 원본)이 지금도 같은 본문·라벨인지 — 어긋난 곳의 목록.

    파일 해시는 무관한 행의 정정에도 깨지지만, 이 검수의 근거는 doc_id 로 찾는 한 행이다. 행 단위로 고정하면 그 행이 바뀔 때만 깨진다.
    """
    drift = []
    for issue in issues:
        for side in issue["sides"]:
            path = (POC / side["path"]).resolve()
            assert path.is_relative_to(POC.resolve()), "역사 원본 경로가 작업 범위를 벗어났다"
            matched = [r for r in read_rows(path) if r.get("doc_id") == issue["doc_id"]]
            if len(matched) != 1:
                drift.append((issue["doc_id"], side["path"], "행 %d개" % len(matched)))
                continue
            row = matched[0]
            if review.text_hash(review.text_of(row)) != side["text_sha256"]:
                drift.append((issue["doc_id"], side["path"], "본문"))
            if row["label"] != side["label"]:
                drift.append((issue["doc_id"], side["path"], "라벨 %s→%s" % (side["label"], row["label"])))
    return drift


def _legacy_issues():
    _require_pack()
    issues = json.loads((review.PACK / "legacy_label_issues.json").read_text(encoding="utf-8"))
    assert len(issues) == 4
    return issues


def test_historical_disputed_rows_keep_pinned_body_and_label():
    assert _disputed_rows_drift(_legacy_issues(), review.read_rows) == []


def test_disputed_row_check_detects_a_changed_label_or_body():
    """위 시험이 아무것도 못 잡는 시험이 아님을 보인다 — 한 행의 라벨을, 다른 한 행의 본문을 바꿔 읽히면 각각 잡아야 한다."""
    issues = _legacy_issues()
    first_id = issues[0]["doc_id"]
    second_id = issues[1]["doc_id"]

    def tampered(path):
        rows = copy.deepcopy(review.read_rows(path))
        for row in rows:
            if row.get("doc_id") == first_id:
                row["label"] = "TS" if row["label"] != "TS" else "S3"
            if row.get("doc_id") == second_id:
                for key in ("text", "content", "body"):
                    if key in row:
                        row[key] = row[key] + " (변조)"
        return rows

    kinds = {kind.split()[0] for _, _, kind in _disputed_rows_drift(issues, tampered)}
    assert {"라벨", "본문"} <= kinds


@pytest.mark.parametrize("mutation", ["quote", "span", "hash", "policy", "signature", "gold", "training", "independent", "issue", "grade", "replacement"])
def test_corrupt_review_rejected(sources, records, mutation):
    dev, legacy = copy.deepcopy(records)
    row = dev[0]
    if mutation == "quote":
        row["evidence"][0]["quote"] = "다른 인용"
    elif mutation == "span":
        row["evidence"][0]["start"] = -1
    elif mutation == "hash":
        row["text_sha256"] = "0" * 64
    elif mutation == "policy":
        row["reference_policy"]["version"] = "unrelated-policy"
    elif mutation == "signature":
        row["human_signature"] = "fabricated"
    elif mutation == "gold":
        row["gold_qualified"] = True
    elif mutation == "training":
        row["training_allowed"] = True
    elif mutation == "independent":
        row["independent_human_review"] = True
    elif mutation == "issue":
        row["policy_issue_ids"] = ["CPR-I99"]
    elif mutation == "grade":
        row["proposal"]["grade"] = "S3"
    elif mutation == "replacement":
        row["original_label_replacement"] = "TS"
    with pytest.raises(ValueError):
        review.validate_reviews(dev, legacy, sources[0], sources[2])


def test_missing_or_duplicated_authored_note_rejected(sources, monkeypatch):
    monkeypatch.setattr(review, "DEV_NOTES", DEV_NOTES[:-1] + [DEV_NOTES[0]])
    with pytest.raises(ValueError, match="coverage"):
        review.development_reviews(sources[0], sources[1])


def test_changed_expected_answer_is_not_silently_reused(sources):
    docs, answers, _, _ = copy.deepcopy(sources)
    answers[DEV_NOTES[0][0]]["expected_grade"] = "S3"
    with pytest.raises(ValueError, match="source answer"):
        review.development_reviews(docs, answers)


def test_nonexistent_or_ambiguous_quote_rejected():
    with pytest.raises(ValueError):
        review.evidence("본문", "없는 근거", "test")
    with pytest.raises(ValueError):
        review.evidence("반복 반복", "반복", "test")


def test_pinned_source_version_cannot_drift(monkeypatch):
    _require_pack()
    monkeypatch.setattr(review, "POLICY_SHA", "0" * 64)
    with pytest.raises(ValueError, match="pinned"):
        review.source_inputs()


def test_all_ten_issues_linked_and_no_grading_algorithm_called(sources, records):
    dev, legacy = records
    result = review.validate_reviews(dev, legacy, sources[0], sources[2])
    assert set(result["issue_affected_review_counts"]) == {f"CPR-I{i:02d}" for i in range(1, 11)}
    assert result["sealed_content_semantically_reviewed"] is False


def test_output_cannot_be_inside_frozen_pack():
    with pytest.raises(ValueError, match="frozen"):
        review.build(review.PACK / "forbidden_review_output")


def test_generated_files_roundtrip_and_input_preservation(tmp_path, sources):
    out = tmp_path / "review"
    result = review.build(out)
    assert result["development_reviewed"] == 90
    assert len(review.read_rows(out / "development_review90.jsonl")) == 90
    assert len(review.read_rows(out / "legacy_review4.jsonl")) == 4
    assert (out / "DEVELOPMENT_REVIEW90.md").read_text(encoding="utf-8").count("\n## CR-") == 90
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert all(sha256(out / f["path"]) == f["sha256"] for f in manifest["files"])
    assert all(sha256(path) == digest for path, digest in sources[3].items())
    with pytest.raises(ValueError, match="Output exists"):
        review.build(out)
