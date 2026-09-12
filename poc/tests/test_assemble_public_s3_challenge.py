"""Contract tests for the separate public-real S3 challenge assembler."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import assemble_public_s3_challenge as challenge


_PARAGRAPHS = (
    "정부는 지역 주민의 안전을 높이기 위해 재난 대응 계획과 현장 점검 결과를 공개했다. 담당 기관은 시설 상태와 연락 체계를 확인하고 필요한 조치를 매주 기록한다.",
    "이번 정책은 보건 교육 교통 분야의 협업 절차를 구체화한다. 기관별 담당자는 접수 현황과 처리 일정을 공유하고 시민이 이해하기 쉬운 안내 자료를 제공한다.",
    "사업 예산과 추진 일정은 공개된 회의 자료에 따라 관리한다. 분기마다 집행 실적과 개선 과제를 정리하고 외부 의견을 반영한 후속 계획을 발표한다.",
    "현장 조사에서는 서비스 이용자의 불편 사항과 시설 접근성을 함께 살폈다. 조사 결과는 통계표와 설명 자료로 정리해 누구나 확인할 수 있도록 게시한다.",
    "관계 부처는 기상 정보와 교통 상황을 바탕으로 대응 단계를 조정한다. 위험이 커지면 안내 문자를 보내고 의료 복지 기관과 지원 인력을 신속히 연결한다.",
    "교육 프로그램은 실제 사례와 표준 절차를 중심으로 구성한다. 참여자는 모의 훈련을 수행하고 평가 결과에 따라 업무 지침과 안내 문구를 보완한다.",
    "운영 성과는 처리 기간 만족도 민원 감소율을 기준으로 점검한다. 수치의 산정 기준과 조사 기간도 함께 공개해 결과를 오해하지 않도록 설명한다.",
    "향후 계획에는 지역별 수요 조사와 취약 계층 지원 방안이 포함된다. 세부 일정은 협의가 끝나는 대로 공개하고 변경 사항도 같은 게시판에 알린다.",
)


def _text(suffix: str) -> str:
    return "\n\n".join(
        (*_PARAGRAPHS, f"자료 식별 문구는 {suffix}이며 공개 검증을 위한 기록이다.")
    )


def _row(
    news_id: str,
    *,
    agency: str = "행정안전부",
    section: int = 1,
    body_start: int = 0,
    body_end: int | None = None,
) -> dict:
    source_reference = (
        "https://www.korea.kr/briefing/pressReleaseView.do?newsId=" + news_id
    )
    source_hash = hashlib.sha256(f"html-{news_id}".encode()).hexdigest()
    licence_hash = hashlib.sha256(f"licence-{news_id}".encode()).hexdigest()
    text = _text(f"{news_id}-{section}")
    body_end = body_start + len(text) if body_end is None else body_end
    return {
        "doc_id": f"korea-policy-{news_id}-s{section:02d}",
        "text": text,
        "title": f"공개 정책 자료 {news_id}",
        "label": "S3",
        "document_origin": "public_real",
        "proxy_role": "public_document",
        "document_family_id": f"korea-policy-{news_id}",
        "family_profile_id": "korea-policy-press_release",
        "document_type": "government_press_release",
        "domain": "public_policy",
        "industry": "government",
        "source_id": "korea-policy-briefing",
        "source_reference": source_reference,
        "source_url": source_reference,
        "source_title": f"공개 정책 자료 {news_id}",
        "source_agency": agency,
        "published_at": "2026-08-07T10:00:00+09:00",
        "source_license": "KOGL-1",
        "source_sha256": source_hash,
        "raw_html_sha256": source_hash,
        "retrieved_at": "2026-08-08T00:00:00+00:00",
        "license_evidence_sha256": licence_hash,
        "license_exact_snippet": "공공누리 제1유형 출처표시 조건에 따라 자유이용",
        "license_status": "license_hold",
        "training_use_permitted": False,
        "evaluation_use_permitted": True,
        "evaluation_availability": "eligible",
        "excluded_modalities": ["image", "caption", "video", "attachment"],
        "section_index": section,
        "body_start": body_start,
        "body_end": body_end,
        "collection_schema": "korea-policy-public-proxy-v1",
    }


def _page(row: dict, *, section_count: int = 1) -> dict:
    return {
        "news_id": row["doc_id"].split("-")[2],
        "source_reference": row["source_reference"],
        "source_title": row["source_title"],
        "source_agency": row["source_agency"],
        "published_at": row["published_at"],
        "raw_html_sha256": row["raw_html_sha256"],
        "retrieved_at": row["retrieved_at"],
        "license_code": row["source_license"],
        "license_evidence_sha256": row["license_evidence_sha256"],
        "license_status": row["license_status"],
        "training_use_permitted": row["training_use_permitted"],
        "evaluation_use_permitted": row["evaluation_use_permitted"],
        "status": "accepted",
        "section_count": section_count,
    }


def _write_run(tmp_path: Path, run_id: str, rows: list[dict]) -> Path:
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    records = run_dir / "records.jsonl"
    records.write_bytes(challenge._canonical_jsonl_bytes(rows))
    pages: dict[str, dict] = {}
    for row in rows:
        news_id = row["doc_id"].split("-")[2]
        page = pages.setdefault(news_id, _page(row, section_count=0))
        page["section_count"] = max(page["section_count"], row["section_index"])
    (run_dir / "raw_html").mkdir()
    (run_dir / "text").mkdir()
    for news_id, page in pages.items():
        raw_relative = Path("raw_html") / f"{news_id}.html"
        text_relative = Path("text") / f"{news_id}.txt"
        (run_dir / raw_relative).write_bytes(f"html-{news_id}".encode())
        source_rows = [row for row in rows if row["doc_id"].split("-")[2] == news_id]
        body_length = max(row["body_end"] for row in source_rows)
        body = [" "] * body_length
        for row in source_rows:
            start, end = row["body_start"], row["body_end"]
            assert end - start == len(row["text"])
            body[start:end] = row["text"]
        (run_dir / text_relative).write_text("".join(body) + "\n", encoding="utf-8")
        page["raw_html_path"] = raw_relative.as_posix()
        page["text_path"] = text_relative.as_posix()
    manifest = {
        "schema": "korea-policy-public-proxy-run-v1",
        "run_id": run_id,
        "mode": "download",
        "completed_at": "2026-08-08T00:01:00+00:00",
        "policy": {"item_level_license_required": True},
        "pilot": {"sections": len(rows)},
        "pages": list(pages.values()),
    }
    (run_dir / "manifest.json").write_bytes(challenge._canonical_json_bytes(manifest))
    return records


def _write_challenge_artifact(
    tmp_path: Path, artifact_id: str, rows: list[dict]
) -> Path:
    records = _write_run(tmp_path, f"{artifact_id}-source", rows)
    assembly = challenge.assemble_challenge(
        challenge.load_inputs([records]),
        count=len(rows),
        seed=artifact_id,
        assembled_at="fixed",
    )
    records_payload = challenge._canonical_jsonl_bytes(assembly.selected)
    manifest = dict(assembly.manifest)
    manifest["artifact"] = {
        "records_path": "records.jsonl",
        "records": len(rows),
        "records_bytes": len(records_payload),
        "records_sha256": hashlib.sha256(records_payload).hexdigest(),
    }
    output_dir = tmp_path / artifact_id
    challenge._atomic_publish_directory(
        output_dir,
        records_payload=records_payload,
        manifest_payload=challenge._canonical_json_bytes(manifest),
    )
    return output_dir


def test_selection_is_seeded_diverse_and_one_record_per_family(tmp_path: Path):
    rows = [
        _row("1001", agency="기관가"),
        _row("1002", agency="기관가"),
        _row("1003", agency="기관나"),
        _row("1004", agency="기관다"),
    ]
    loaded = challenge.load_inputs([_write_run(tmp_path, "run-a", rows)])

    first = challenge.assemble_challenge(
        loaded, count=3, seed="fixed", assembled_at="fixed"
    )
    second = challenge.assemble_challenge(
        loaded, count=3, seed="fixed", assembled_at="fixed"
    )

    assert [row["doc_id"] for row in first.selected] == [
        row["doc_id"] for row in second.selected
    ]
    assert len({row["document_family_id"] for row in first.selected}) == 3
    assert len({row["source_agency"] for row in first.selected}) == 3
    assert all(
        row["artifact_intended_use"] == "evaluation_only" for row in first.selected
    )
    boundary = first.manifest["claim_boundary"]
    assert boundary["must_not_merge_with_primary_balanced_metrics"] is True
    assert "customer accuracy" in boundary["forbidden"]


def test_cli_publishes_exact_canonical_hashes_and_input_hashes(tmp_path: Path):
    records = _write_run(tmp_path, "run-hash", [_row("2001"), _row("2002")])
    output_dir = tmp_path / "challenge"

    assert (
        challenge.main(
            [
                "--input",
                str(records),
                "--count",
                "2",
                "--seed",
                "hash-test",
                "--out-dir",
                str(output_dir),
            ]
        )
        == 0
    )

    output_bytes = (output_dir / "records.jsonl").read_bytes()
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert output_bytes.endswith(b"\n")
    assert (
        hashlib.sha256(output_bytes).hexdigest()
        == manifest["artifact"]["records_sha256"]
    )
    assert manifest["artifact"]["records_bytes"] == len(output_bytes)
    assert (
        manifest["inputs"][0]["records_sha256"]
        == hashlib.sha256(records.read_bytes()).hexdigest()
    )
    parsed = [json.loads(line) for line in output_bytes.decode().splitlines()]
    assert output_bytes == challenge._canonical_jsonl_bytes(parsed)
    assert manifest["distribution"] == {"S3": 2}
    assert manifest["uniqueness"] == {
        "unique_doc_ids": 2,
        "unique_text_hashes": 2,
        "unique_document_family_ids": 2,
    }


def test_atomic_directory_publish_refuses_overwrite(tmp_path: Path):
    output_dir = tmp_path / "challenge"
    challenge._atomic_publish_directory(
        output_dir,
        records_payload=b'{"first":true}\n',
        manifest_payload=b'{"manifest":true}\n',
    )
    before = (output_dir / "records.jsonl").read_bytes()

    with pytest.raises(challenge.ChallengeAssemblyError, match="refusing to overwrite"):
        challenge._atomic_publish_directory(
            output_dir,
            records_payload=b'{"replacement":true}\n',
            manifest_payload=b'{"replacement":true}\n',
        )
    assert (output_dir / "records.jsonl").read_bytes() == before


def test_train_only_or_unbound_provenance_fails_closed(tmp_path: Path):
    train_only = _row("3001")
    train_only["training_use_permitted"] = True
    train_only["evaluation_use_permitted"] = False
    records = _write_run(tmp_path, "run-train-only", [train_only])
    output_dir = tmp_path / "challenge"

    assert (
        challenge.main(
            ["--input", str(records), "--count", "1", "--out-dir", str(output_dir)]
        )
        == 2
    )
    assert not output_dir.exists()

    unbound = _row("3002")
    records = _write_run(tmp_path, "run-unbound", [unbound])
    manifest_path = records.with_name("manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["pages"][0]["raw_html_sha256"] = "0" * 64
    manifest_path.write_bytes(challenge._canonical_json_bytes(manifest))
    with pytest.raises(challenge.ChallengeAssemblyError, match="provenance mismatch"):
        challenge.assemble_challenge(challenge.load_inputs([records]), count=1)


def test_default_family_cap_is_one_and_multiple_sections_require_explicit_opt_in(
    tmp_path: Path,
):
    rows = [
        _row("4001", section=1, body_start=0),
        _row("4001", section=2, body_start=2000),
    ]
    loaded = challenge.load_inputs([_write_run(tmp_path, "run-sections", rows)])

    with pytest.raises(challenge.ChallengeAssemblyError, match="insufficient eligible"):
        challenge.assemble_challenge(loaded, count=2)
    assembly = challenge.assemble_challenge(
        loaded,
        count=2,
        max_sections_per_family=2,
        assembled_at="fixed",
    )
    assert len(assembly.selected) == 2
    assert assembly.manifest["family_counts"]["unique_document_families"] == 1


def test_missing_run_manifest_and_conflicting_duplicate_id_fail_closed(tmp_path: Path):
    orphan_dir = tmp_path / "orphan"
    orphan_dir.mkdir()
    orphan = orphan_dir / "records.jsonl"
    orphan.write_bytes(challenge._canonical_jsonl_bytes([_row("5001")]))
    with pytest.raises(challenge.ChallengeAssemblyError, match="manifest is required"):
        challenge.load_inputs([orphan])

    first = _row("5002")
    second = dict(first)
    second["text"] = _text("5002-X")
    records = _write_run(tmp_path, "run-conflict", [first, second])
    with pytest.raises(
        challenge.ChallengeAssemblyError,
        match="(?:text/source offsets mismatch|conflicting duplicate doc_id)",
    ):
        challenge.assemble_challenge(challenge.load_inputs([records]), count=1)


def test_repeatable_blocked_corpora_exclude_all_identity_overlaps_before_selection(
    tmp_path: Path,
):
    blocked_a_row = _row("6001")
    blocked_b_row = _row("7001")
    blocked_a = _write_challenge_artifact(tmp_path, "blocked-a", [blocked_a_row])
    blocked_b = _write_challenge_artifact(tmp_path, "blocked-b", [blocked_b_row])

    same_text = _row("6002")
    same_text["text"] = blocked_a_row["text"]
    same_text["body_end"] = len(same_text["text"])
    candidate_rows = [
        dict(blocked_a_row),
        _row("6001", section=2, body_start=2000),
        same_text,
        dict(blocked_b_row),
        _row("8001"),
        _row("8002"),
    ]
    records = _write_run(tmp_path, "blind-source", candidate_rows)
    output_dir = tmp_path / "blind-challenge"

    assert (
        challenge.main(
            [
                "--input",
                str(records),
                "--blocked-corpus",
                str(blocked_a),
                "--blocked-corpus",
                str(blocked_b / "records.jsonl"),
                "--count",
                "2",
                "--seed",
                "blind",
                "--out-dir",
                str(output_dir),
            ]
        )
        == 0
    )

    selected = [
        json.loads(line)
        for line in (output_dir / "records.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert {row["doc_id"] for row in selected} == {
        "korea-policy-8001-s01",
        "korea-policy-8002-s01",
    }
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    blocked = manifest["blocked_corpora"]
    assert blocked["input_artifacts"] == 2
    assert blocked["input_rows"] == 2
    assert blocked["union_uniqueness"] == {
        "unique_doc_ids": 2,
        "unique_document_family_ids": 2,
        "unique_normalized_text_hashes": 2,
    }
    assert blocked["excluded_before_selection"] == {
        "records": 4,
        "reason_counts": {
            "doc_id_overlap": 2,
            "document_family_id_overlap": 3,
            "normalized_text_overlap": 3,
        },
        "reason_combination_counts": {
            "doc_id_overlap+document_family_id_overlap+normalized_text_overlap": 2,
            "document_family_id_overlap": 1,
            "normalized_text_overlap": 1,
        },
    }
    first_audit = blocked["inputs"][0]
    assert first_audit["requested_path"] == str(blocked_a)
    assert first_audit["records_bytes"] == (blocked_a / "records.jsonl").stat().st_size
    assert (
        first_audit["records_sha256"]
        == hashlib.sha256((blocked_a / "records.jsonl").read_bytes()).hexdigest()
    )
    assert first_audit["manifest_bytes"] == (blocked_a / "manifest.json").stat().st_size
    assert (
        first_audit["manifest_sha256"]
        == hashlib.sha256((blocked_a / "manifest.json").read_bytes()).hexdigest()
    )


def test_blocked_corpus_schema_uniqueness_and_record_hash_are_verified(tmp_path: Path):
    wrong_schema = _write_challenge_artifact(tmp_path, "wrong-schema", [_row("9001")])
    schema_manifest_path = wrong_schema / "manifest.json"
    schema_manifest = json.loads(schema_manifest_path.read_text(encoding="utf-8"))
    schema_manifest["schema"] = "untrusted-v0"
    schema_manifest_path.write_bytes(challenge._canonical_json_bytes(schema_manifest))
    with pytest.raises(challenge.ChallengeAssemblyError, match="manifest schema"):
        challenge.load_blocked_corpora([wrong_schema])

    wrong_unique = _write_challenge_artifact(tmp_path, "wrong-unique", [_row("9002")])
    unique_manifest_path = wrong_unique / "manifest.json"
    unique_manifest = json.loads(unique_manifest_path.read_text(encoding="utf-8"))
    unique_manifest["uniqueness"]["unique_doc_ids"] = 0
    unique_manifest_path.write_bytes(challenge._canonical_json_bytes(unique_manifest))
    with pytest.raises(challenge.ChallengeAssemblyError, match="uniqueness count"):
        challenge.load_blocked_corpora([wrong_unique])

    wrong_hash = _write_challenge_artifact(tmp_path, "wrong-hash", [_row("9003")])
    hash_records_path = wrong_hash / "records.jsonl"
    hash_rows = [
        json.loads(line)
        for line in hash_records_path.read_text(encoding="utf-8").splitlines()
    ]
    hash_rows[0]["title"] += " tampered"
    hash_records_path.write_bytes(challenge._canonical_jsonl_bytes(hash_rows))
    with pytest.raises(
        challenge.ChallengeAssemblyError, match="(?:byte count|SHA-256)"
    ):
        challenge.load_blocked_corpora([wrong_hash])


def test_blocked_overlap_fails_when_too_few_candidates_remain(tmp_path: Path):
    row = _row("9101")
    blocked = _write_challenge_artifact(tmp_path, "blocked-only", [row])
    loaded = challenge.load_inputs(
        [_write_run(tmp_path, "insufficient-source", [dict(row)])]
    )

    with pytest.raises(challenge.ChallengeAssemblyError, match="insufficient eligible"):
        challenge.assemble_challenge(
            loaded,
            count=1,
            blocked_corpora=challenge.load_blocked_corpora([blocked]),
        )
