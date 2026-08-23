"""Fail-closed contracts for the public-real S3 training-only pool."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

import pytest

from scripts import assemble_public_s3_challenge as challenge
from scripts import assemble_public_s3_training_pool as training


_PARAGRAPHS = (
    "정부는 지역 주민의 생활 안전을 높이기 위한 현장 점검 결과를 공개했다. 담당 기관은 시설 상태와 연락 체계를 확인하고 필요한 조치의 진행 상황을 매주 기록한다.",
    "이번 정책은 보건 교육 교통 분야의 협업 절차를 구체화한다. 기관별 담당자는 접수 현황과 처리 일정을 공유하고 시민이 이해하기 쉬운 안내 자료를 제공한다.",
    "사업 예산과 추진 일정은 공개된 회의 자료에 따라 관리한다. 분기마다 집행 실적과 개선 과제를 정리하고 외부 의견을 반영한 후속 계획을 발표한다.",
    "현장 조사에서는 서비스 이용자의 불편 사항과 시설 접근성을 함께 살폈다. 조사 결과는 통계표와 설명 자료로 정리해 누구나 확인할 수 있도록 게시한다.",
    "관계 부처는 기상 정보와 교통 상황을 바탕으로 대응 단계를 조정한다. 위험이 커지면 안내 문자를 보내고 의료 복지 기관과 지역 인력을 신속하게 연결한다.",
    "교육 프로그램은 실제 사례와 표준 절차를 중심으로 구성한다. 참여자는 모의 훈련을 수행하고 평가 결과에 따라 업무 지침과 안내 문구를 보완한다.",
    "운영 성과는 처리 기간 만족도 민원 감소율을 기준으로 평가한다. 수치의 산정 기준과 조사 기간을 함께 공개해 결과를 오해하지 않도록 설명한다.",
    "향후 계획에는 지역별 수요 조사와 취약 계층 지원 방안을 포함한다. 세부 일정은 협의가 끝나는 대로 공개하고 변경 사항도 같은 게시판에 알린다.",
)


def _text(suffix: str, *, repeat: int = 1) -> str:
    paragraphs = [
        f"{paragraph} 문서 식별 항목은 {suffix}이다." for paragraph in _PARAGRAPHS
    ]
    return "\n\n".join(paragraphs * repeat)


def _licence_html(news_id: str, licence: str) -> str:
    return (
        f'<div class="type" data-news-id="{news_id}">'
        f"공공누리 {licence} 출처표시 조건에 따라 텍스트 자유이용"
        "</div>"
    )


def _row(
    news_id: str,
    *,
    section: int = 1,
    agency: str = "행정안전부",
    body_start: int = 0,
    text: str | None = None,
    licence: str = "KOGL-1",
    document_type: str = "government_press_release",
    family_profile_id: str = "korea-policy-press_release",
) -> dict[str, object]:
    source_reference = (
        "https://www.korea.kr/briefing/pressReleaseView.do?newsId=" + news_id
    )
    licence_html = _licence_html(news_id, licence)
    raw_payload = f"<html>{licence_html}<body>{news_id}</body></html>".encode()
    body = text or _text(f"{news_id}-{section}")
    return {
        "doc_id": f"korea-policy-{news_id}-s{section:02d}",
        "text": body,
        "title": f"공개 정책 자료 {news_id}",
        "label": "S3",
        "document_origin": "public_real",
        "proxy_role": "public_document",
        "document_family_id": f"korea-policy-{news_id}",
        "family_profile_id": family_profile_id,
        "document_type": document_type,
        "domain": "public_policy",
        "industry": "government",
        "source_id": "korea-policy-briefing",
        "source_reference": source_reference,
        "source_url": source_reference,
        "source_title": f"공개 정책 자료 {news_id}",
        "source_agency": agency,
        "published_at": "2026-08-08T10:00:00+09:00",
        "source_license": licence,
        "source_sha256": hashlib.sha256(raw_payload).hexdigest(),
        "raw_html_sha256": hashlib.sha256(raw_payload).hexdigest(),
        "retrieved_at": "2026-08-08T01:30:00+00:00",
        "license_evidence_sha256": hashlib.sha256(
            licence_html.encode("utf-8")
        ).hexdigest(),
        "license_exact_snippet": (
            f"공공누리 {licence} 출처표시 조건에 따라 텍스트 자유이용"
        ),
        "license_status": "training_eligible",
        "training_use_permitted": True,
        "evaluation_use_permitted": True,
        "evaluation_availability": "eligible",
        "excluded_modalities": ["image", "caption", "video", "attachment"],
        "section_index": section,
        "body_start": body_start,
        "body_end": body_start + len(body),
        "collection_schema": "korea-policy-public-proxy-v1",
    }


def _page(row: dict[str, object], *, section_count: int) -> dict[str, object]:
    news_id = str(row["doc_id"]).split("-")[2]
    licence_html = _licence_html(news_id, str(row["source_license"]))
    return {
        "news_id": news_id,
        "source_reference": row["source_reference"],
        "source_title": row["source_title"],
        "source_agency": row["source_agency"],
        "published_at": row["published_at"],
        "raw_html_sha256": row["raw_html_sha256"],
        "retrieved_at": row["retrieved_at"],
        "license_code": row["source_license"],
        "license_exact_html": licence_html,
        "license_exact_snippet": row["license_exact_snippet"],
        "license_evidence_sha256": row["license_evidence_sha256"],
        "license_status": row["license_status"],
        "training_use_permitted": row["training_use_permitted"],
        "evaluation_use_permitted": row["evaluation_use_permitted"],
        "permission_basis": (
            "page-level KOG-L 1 text wording; official 2025-Q3 guidance "
            "permits AI training with source attribution"
        ),
        "status": "accepted",
        "section_count": section_count,
    }


def _write_run(tmp_path: Path, run_id: str, rows: list[dict[str, object]]) -> Path:
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    records_path = run_dir / "records.jsonl"
    records_path.write_bytes(challenge._canonical_jsonl_bytes(rows))
    (run_dir / "raw_html").mkdir()
    (run_dir / "text").mkdir()

    pages: dict[str, dict[str, object]] = {}
    for row in rows:
        news_id = str(row["doc_id"]).split("-")[2]
        page = pages.setdefault(news_id, _page(row, section_count=0))
        page["section_count"] = max(
            int(page["section_count"]), int(row["section_index"])
        )
    for news_id, page in pages.items():
        raw_relative = Path("raw_html") / f"{news_id}.html"
        text_relative = Path("text") / f"{news_id}.txt"
        licence_html = str(page["license_exact_html"])
        raw_payload = f"<html>{licence_html}<body>{news_id}</body></html>".encode()
        (run_dir / raw_relative).write_bytes(raw_payload)
        source_rows = [
            row for row in rows if str(row["doc_id"]).split("-")[2] == news_id
        ]
        body_length = max(int(row["body_end"]) for row in source_rows)
        extracted_body = [" "] * body_length
        for row in source_rows:
            start, end = int(row["body_start"]), int(row["body_end"])
            row_text = str(row["text"])
            assert end - start == len(row_text)
            extracted_body[start:end] = row_text
        (run_dir / text_relative).write_text(
            "".join(extracted_body) + "\n", encoding="utf-8"
        )
        page["raw_html_path"] = raw_relative.as_posix()
        page["text_path"] = text_relative.as_posix()

    manifest = {
        "schema": "korea-policy-public-proxy-run-v1",
        "run_id": run_id,
        "mode": "download",
        "completed_at": "2026-08-08T01:31:00+00:00",
        "policy": {
            "item_level_license_required": True,
            "accepted_license_markers": ["KOGL-0", "KOGL-1", "KOGL-AI"],
            "kogl_1_training_policy": "training_eligible_with_source_attribution",
            "training_permission_evidence": {
                "issuer": training.TRAINING_PERMISSION_ISSUER,
                "title": training.TRAINING_PERMISSION_TITLE,
                "url": training.TRAINING_PERMISSION_URL,
                "rule": training.TRAINING_PERMISSION_RULE,
                "attribution_required": True,
            },
        },
        "pilot": {"sections": len(rows)},
        "pages": list(pages.values()),
    }
    (run_dir / "manifest.json").write_bytes(challenge._canonical_json_bytes(manifest))
    return records_path


def _write_blocked(
    tmp_path: Path, artifact_id: str, rows: list[dict[str, object]]
) -> Path:
    source = _write_run(tmp_path, f"{artifact_id}-source", rows)
    assembly = challenge.assemble_challenge(
        challenge.load_inputs([source]),
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


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_cli_blocks_both_holdouts_and_publishes_attested_training_only_pool(
    tmp_path: Path,
):
    blocked_one_row = _row("1001")
    blocked_two_row = _row("1002")
    blocked_one = _write_blocked(tmp_path, "public-s3-dev-v1", [blocked_one_row])
    blocked_two = _write_blocked(tmp_path, "public-s3-blind-v2", [blocked_two_row])

    family_overlap = _row("1001", section=2, body_start=2000)
    text_overlap = _row("1003", text=str(blocked_two_row["text"]))
    candidates = [
        dict(blocked_one_row),
        family_overlap,
        text_overlap,
        dict(blocked_two_row),
        _row("2001", agency="보건복지부"),
        _row("2002", agency="산업통상자원부", text=_text("2002", repeat=2)),
        _row("2003", agency="행정안전부"),
        _row("2004", agency="환경부", text=_text("2004", repeat=2)),
    ]
    source = _write_run(tmp_path, "train-public-source", candidates)
    output_dir = tmp_path / "public-s3-train-3-v1"

    assert (
        training.main(
            [
                "--input",
                str(source),
                "--blocked-corpus",
                str(blocked_one),
                "--blocked-corpus",
                str(blocked_two / "records.jsonl"),
                "--count",
                "3",
                "--seed",
                "fixed",
                "--out-dir",
                str(output_dir),
            ]
        )
        == 0
    )

    rows, audit = training.load_public_s3_training_pool(output_dir)
    assert len(rows) == audit["records"] == 3
    assert {row["doc_id"] for row in rows} <= {
        "korea-policy-2001-s01",
        "korea-policy-2002-s01",
        "korea-policy-2003-s01",
        "korea-policy-2004-s01",
    }
    assert all(row["source_evaluation_use_permitted"] is True for row in rows)
    assert all(row["artifact_evaluation_use_permitted"] is False for row in rows)
    assert all(row["evaluation_pool_use_prohibited"] is True for row in rows)
    assert all(
        str(row["source_attribution"]["rendered_text"]).startswith("출처:")
        for row in rows
    )
    assert all(row["license_exact_html"] for row in rows)

    manifest = _load_json(output_dir / "manifest.json")
    blocked = manifest["blocked_corpora"]
    assert blocked["input_artifacts"] == 2
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
    records_payload = (output_dir / "records.jsonl").read_bytes()
    assert (
        manifest["artifact"]["records_sha256"]
        == hashlib.sha256(records_payload).hexdigest()
    )
    assert (
        manifest["inputs"][0]["records_sha256"]
        == hashlib.sha256(source.read_bytes()).hexdigest()
    )
    complete = _load_json(output_dir / "COMPLETE.json")
    manifest_payload = (output_dir / "manifest.json").read_bytes()
    assert complete["manifest_sha256"] == hashlib.sha256(manifest_payload).hexdigest()
    assert complete["artifact_evaluation_use_permitted"] is False


def test_selection_is_deterministic_unique_and_agency_length_balanced(tmp_path: Path):
    blocked_one = _write_blocked(tmp_path, "dev", [_row("3001")])
    blocked_two = _write_blocked(tmp_path, "blind", [_row("3002")])
    rows = [
        _row("3101", agency="기관가"),
        _row("3102", agency="기관가", text=_text("3102", repeat=2)),
        _row("3103", agency="기관나"),
        _row("3104", agency="기관나", text=_text("3104", repeat=2)),
    ]
    inputs = training.load_training_inputs([_write_run(tmp_path, "train", rows)])
    blocked = challenge.load_blocked_corpora([blocked_one, blocked_two])

    first = training.assemble_training_pool(
        inputs, blocked=blocked, count=4, seed="same", assembled_at="fixed"
    )
    second = training.assemble_training_pool(
        inputs, blocked=blocked, count=4, seed="same", assembled_at="fixed"
    )

    assert first.selected == second.selected
    assert len({row["doc_id"] for row in first.selected}) == 4
    assert len({row["document_family_id"] for row in first.selected}) == 4
    assert first.manifest["source_agency_counts"] == {"기관가": 2, "기관나": 2}
    assert set(first.manifest["document_length_bin_counts"]) == {
        "1200-1599",
        "1600-2199",
    }


def test_exact_document_type_strata_are_attested_and_reloaded(tmp_path: Path):
    blocked_one = _write_blocked(tmp_path, "type-dev", [_row("3201")])
    blocked_two = _write_blocked(tmp_path, "type-blind", [_row("3202")])
    rows = [
        _row("3211", agency="기관가"),
        _row("3212", agency="기관나"),
        _row(
            "3221",
            agency="기관다",
            document_type="government_policy_article",
            family_profile_id="korea-policy-policy_news",
        ),
        _row(
            "3222",
            agency="기관라",
            document_type="government_policy_article",
            family_profile_id="korea-policy-policy_news",
        ),
    ]
    inputs = training.load_training_inputs([_write_run(tmp_path, "typed", rows)])
    blocked = challenge.load_blocked_corpora([blocked_one, blocked_two])
    targets = {
        "government_policy_article": 1,
        "government_press_release": 2,
    }

    assembly = training.assemble_training_pool(
        inputs,
        blocked=blocked,
        count=3,
        seed="typed",
        document_type_targets=targets,
        assembled_at="fixed",
    )
    output_dir = tmp_path / "typed-output"
    training.publish_training_pool(output_dir, assembly)
    selected, _ = training.load_public_s3_training_pool(output_dir)

    assert dict(sorted(Counter(row["document_type"] for row in selected).items())) == targets
    assert assembly.manifest["selection"]["document_type_targets"] == targets
    assert assembly.manifest["family_profile_counts"] == {
        "korea-policy-policy_news": 1,
        "korea-policy-press_release": 2,
    }

    with pytest.raises(
        training.PublicTrainingAssemblyError,
        match="insufficient eligible records for document type quota",
    ):
        training.assemble_training_pool(
            inputs,
            blocked=blocked,
            count=4,
            document_type_targets={
                "government_policy_article": 3,
                "government_press_release": 1,
            },
        )


def test_requires_two_blockers_and_fails_when_overlap_exhausts_candidates(
    tmp_path: Path,
):
    row = _row("4001")
    blocker = _write_blocked(tmp_path, "only-blocker", [row])
    inputs = training.load_training_inputs(
        [_write_run(tmp_path, "only-source", [dict(row)])]
    )
    one_blocked = challenge.load_blocked_corpora([blocker])

    with pytest.raises(training.PublicTrainingAssemblyError, match="both development"):
        training.assemble_training_pool(inputs, blocked=one_blocked, count=1)

    second = _write_blocked(tmp_path, "second-blocker", [_row("4002")])
    blocked = challenge.load_blocked_corpora([blocker, second])
    with pytest.raises(
        training.PublicTrainingAssemblyError, match="insufficient eligible"
    ):
        training.assemble_training_pool(inputs, blocked=blocked, count=1)


def test_source_and_item_license_evidence_are_reverified(tmp_path: Path):
    row = _row("5001")
    source = _write_run(tmp_path, "evidence-source", [row])
    loaded = training.load_training_inputs([source])
    blocker_one = _write_blocked(tmp_path, "evidence-dev", [_row("5002")])
    blocker_two = _write_blocked(tmp_path, "evidence-blind", [_row("5003")])
    blocked = challenge.load_blocked_corpora([blocker_one, blocker_two])

    manifest_path = source.with_name("manifest.json")
    manifest = _load_json(manifest_path)
    manifest["pages"][0]["license_exact_html"] += "tampered"
    manifest_path.write_bytes(challenge._canonical_json_bytes(manifest))
    tampered = training.load_training_inputs([source])
    with pytest.raises(
        training.PublicTrainingAssemblyError, match="evidence bytes/hash"
    ):
        training.assemble_training_pool(tampered, blocked=blocked, count=1)

    raw_path = source.parent / "raw_html" / "5001.html"
    raw_path.write_bytes(b"tampered")
    with pytest.raises(
        training.PublicTrainingAssemblyError, match="raw source bytes/hash"
    ):
        training.assemble_training_pool(loaded, blocked=blocked, count=1)


def test_committed_envelope_is_immutable_and_loader_detects_tampering(tmp_path: Path):
    blockers = [
        _write_blocked(tmp_path, "immutable-dev", [_row("6001")]),
        _write_blocked(tmp_path, "immutable-blind", [_row("6002")]),
    ]
    inputs = training.load_training_inputs(
        [_write_run(tmp_path, "immutable-source", [_row("6003")])]
    )
    assembly = training.assemble_training_pool(
        inputs,
        blocked=challenge.load_blocked_corpora(blockers),
        count=1,
        assembled_at="fixed",
    )
    output_dir = tmp_path / "immutable-training"
    training.publish_training_pool(output_dir, assembly)

    with pytest.raises(training.PublicTrainingAssemblyError, match="overwrite"):
        training.publish_training_pool(output_dir, assembly)

    rows = [
        json.loads(line)
        for line in (output_dir / "records.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    rows[0]["title"] += " tampered"
    (output_dir / "records.jsonl").write_bytes(challenge._canonical_jsonl_bytes(rows))
    with pytest.raises(
        training.PublicTrainingAssemblyError, match="manifest attestation"
    ):
        training.load_public_s3_training_pool(output_dir)


def test_non_training_permission_and_unapproved_license_fail_closed(tmp_path: Path):
    blocker_one = _write_blocked(tmp_path, "permission-dev", [_row("7001")])
    blocker_two = _write_blocked(tmp_path, "permission-blind", [_row("7002")])
    blocked = challenge.load_blocked_corpora([blocker_one, blocker_two])

    denied = _row("7003")
    denied["training_use_permitted"] = False
    source = _write_run(tmp_path, "denied", [denied])
    with pytest.raises(
        training.PublicTrainingAssemblyError, match="insufficient eligible"
    ):
        training.assemble_training_pool(
            training.load_training_inputs([source]), blocked=blocked, count=1
        )

    unsupported = _row("7004", licence="KOGL-2")
    source = _write_run(tmp_path, "unsupported", [unsupported])
    with pytest.raises(
        training.PublicTrainingAssemblyError, match="unsupported training"
    ):
        training.assemble_training_pool(
            training.load_training_inputs([source]), blocked=blocked, count=1
        )
