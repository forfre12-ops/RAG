import json
from pathlib import Path

from scripts import (
    build_human_review_queue,
    build_operational_readiness,
    build_p1_boundary_report,
    build_release_manifest,
    check_release_gate,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_operational_readiness_blocks_when_human_review_below_minimum(tmp_path, monkeypatch):
    _write_json(
        tmp_path / "p1_public.json",
        {"metrics": {"f1_macro": 0.83, "fnr_underclass": 0.0, "high_risk_to_s3": 0}},
    )
    _write_json(
        tmp_path / "p1_llm.json",
        {"metrics": {"f1_macro": 0.55, "high_risk_to_s3": 17}},
    )
    _write_json(
        tmp_path / "p2.json",
        {
            "best_config": {
                "label": "KURE / es / hybrid",
                "latency_ms_p50": 174,
                "retrieval_metrics": {"recall_at_k": 0.925, "mrr": 0.842, "ndcg_at_k": 0.862},
            }
        },
    )
    gold = tmp_path / "classification_gold.jsonl"
    gold.write_text(
        "\n".join(
            json.dumps({"label": "S3", "label_source": "public_definitive"})
            for _ in range(700)
        )
        + "\n",
        encoding="utf-8",
    )
    retrieval = tmp_path / "retrieval_gold.jsonl"
    retrieval.write_text(
        "\n".join(json.dumps({"source": "oss_eval_doc_id"}) for _ in range(80)) + "\n",
        encoding="utf-8",
    )

    out = tmp_path / "readiness.md"
    monkeypatch.setattr(
        "sys.argv",
        [
            "build_operational_readiness.py",
            "--p1-public",
            str(tmp_path / "p1_public.json"),
            "--p1-llm",
            str(tmp_path / "p1_llm.json"),
            "--p2",
            str(tmp_path / "p2.json"),
            "--gold",
            str(gold),
            "--retrieval-gold",
            str(retrieval),
            "--out",
            str(out),
            "--min-human-review",
            "40",
        ],
    )

    assert build_operational_readiness.main() == 0
    payload = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert payload["verdict"] == "CONDITIONALLY_READY"
    assert payload["release_gate_policy"]["min_human_review"] == 40


def _readiness_argv(tmp_path, gold, **extra):
    _write_json(
        tmp_path / "p1_public.json",
        {"metrics": {"f1_macro": 0.83, "fnr_underclass": 0.0, "high_risk_to_s3": 0}},
    )
    _write_json(tmp_path / "p1_llm.json", {"metrics": {"f1_macro": 0.55, "high_risk_to_s3": 17}})
    _write_json(
        tmp_path / "p2.json",
        {
            "best_config": {
                "label": "KURE / es / hybrid",
                "latency_ms_p50": 174,
                "retrieval_metrics": {"recall_at_k": 0.925, "mrr": 0.842, "ndcg_at_k": 0.862},
            }
        },
    )
    retrieval = tmp_path / "retrieval_gold.jsonl"
    retrieval.write_text(
        "\n".join(json.dumps({"source": "oss_eval_doc_id"}) for _ in range(80)) + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "readiness.md"
    argv = [
        "build_operational_readiness.py",
        "--p1-public", str(tmp_path / "p1_public.json"),
        "--p1-llm", str(tmp_path / "p1_llm.json"),
        "--p2", str(tmp_path / "p2.json"),
        "--gold", str(gold),
        "--retrieval-gold", str(retrieval),
        "--out", str(out),
        "--min-human-review", "40",
        # parity gate: default evaluated == deployed so it PASSes unless a test overrides
        "--model-dir", "m-eval",
        "--deployed-model", "m-eval",
    ]
    for k, v in extra.items():
        argv += [k, v]
    monkeypatch_argv = argv
    return monkeypatch_argv, out


def _signed(rec: dict, i: int) -> dict:
    """[#8] 유효 서명 envelope(실계정 reviewer·gate_version·signed_at·reviewer_ids) + 실문서
    출처를 붙여 golden_tiers.is_real_locked_eval 를 통과하는 strict human_review 로 만든다."""
    rec.update({
        "label_source": "human_review",
        "reviewer_id": f"reviewer_kim_{i}",
        "gate_version": "human_signoff_v1",
        "signed_at": "2026-07-29T00:00:00Z",
        "reviewer_ids": [f"reviewer_kim_{i}"],
        "document_origin": "public_real",
    })
    return rec


def _gold_with_human_review(tmp_path, n_underclass: int):
    """700 records: 40 strict-signed human_review (n_underclass high-risk -> S3), rest public."""
    lines = []
    for i in range(40):
        if i < n_underclass:
            rec = _signed({"label": "S2", "model_label": "S3"}, i)
        else:
            rec = _signed({"label": "S3", "model_label": "S3"}, i)
        lines.append(json.dumps(rec))
    for _ in range(660):
        lines.append(json.dumps({"label": "S3", "label_source": "public_definitive"}))
    gold = tmp_path / "classification_gold.jsonl"
    gold.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return gold


def test_human_review_gate_fails_when_underclass_exceeds_threshold(tmp_path, monkeypatch):
    # 2 high-risk (label S2) records, both predicted S3 -> underclass rate 1.0 > 0.10.
    gold = _gold_with_human_review(tmp_path, n_underclass=2)
    argv, out = _readiness_argv(tmp_path, gold)
    monkeypatch.setattr("sys.argv", argv)
    assert build_operational_readiness.main() == 1  # FAIL verdict -> non-zero exit
    payload = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    hr_gate = next(g for g in payload["gates"] if g["name"] == "human review gold")
    assert hr_gate["status"] == "FAIL"
    assert payload["verdict"] == "FAIL"


def test_human_review_gate_passes_with_clean_labels(tmp_path, monkeypatch):
    gold = _gold_with_human_review(tmp_path, n_underclass=0)
    argv, out = _readiness_argv(tmp_path, gold)
    monkeypatch.setattr("sys.argv", argv)
    assert build_operational_readiness.main() == 0
    payload = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    hr_gate = next(g for g in payload["gates"] if g["name"] == "human review gold")
    assert hr_gate["status"] == "PASS"
    assert payload["verdict"] == "PASS"


def test_human_review_gate_blocks_on_unsigned_fakes(tmp_path, monkeypatch):
    # [#8] 서명 envelope/실문서 출처 없는 가짜 human_review 40건(reviewer_id=r1, all S3)은
    # strict 계약상 0 → BLOCKED. raw 카운트만 세던 예전 게이트를 가짜로 통과시키던 구멍을 닫는다.
    lines = [
        json.dumps({"label": "S3", "model_label": "S3",
                    "label_source": "human_review", "reviewer_id": "r1"})
        for _ in range(40)
    ]
    lines += [json.dumps({"label": "S3", "label_source": "public_definitive"}) for _ in range(660)]
    gold = tmp_path / "classification_gold.jsonl"
    gold.write_text("\n".join(lines) + "\n", encoding="utf-8")
    argv, out = _readiness_argv(tmp_path, gold)
    monkeypatch.setattr("sys.argv", argv)
    build_operational_readiness.main()
    payload = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    hr_gate = next(g for g in payload["gates"] if g["name"] == "human review gold")
    assert hr_gate["status"] == "BLOCKED"                       # raw 40 이지만 strict 0
    assert "raw label_source=human_review=40" in hr_gate["detail"]


def test_model_parity_gate_blocks_when_deployed_differs(tmp_path, monkeypatch):
    gold = _gold_with_human_review(tmp_path, n_underclass=0)  # clean human-review
    argv, out = _readiness_argv(tmp_path, gold, **{"--deployed-model": "m-other"})
    monkeypatch.setattr("sys.argv", argv)
    build_operational_readiness.main()
    payload = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    parity = next(g for g in payload["gates"] if g["name"] == "model parity")
    assert parity["status"] == "BLOCKED"
    # otherwise-clean readiness drops to CONDITIONALLY_READY purely on parity
    assert payload["verdict"] == "CONDITIONALLY_READY"
    assert payload["deployed_model"] == "m-other"
    assert payload["evaluated_model"] == "m-eval"


def test_release_gate_requires_every_gate_to_pass(tmp_path, monkeypatch):
    readiness = tmp_path / "readiness.json"
    readiness.write_text(
        json.dumps(
            {
                "verdict": "CONDITIONALLY_READY",
                "gates": [{"name": "human review gold", "status": "BLOCKED", "detail": "human_review=0/40"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.argv", ["check_release_gate.py", "--readiness", str(readiness)])

    assert check_release_gate.main() == 1


def _write_readiness(tmp_path, verdict, gates):
    readiness = tmp_path / "readiness.json"
    readiness.write_text(json.dumps({"verdict": verdict, "gates": gates}), encoding="utf-8")
    return readiness


def test_release_gate_pilot_waives_blocked_data_ceiling(tmp_path, monkeypatch):
    # CONDITIONALLY_READY: only BLOCKED gates (human_review ceiling + parity pending).
    readiness = _write_readiness(
        tmp_path,
        "CONDITIONALLY_READY",
        [
            {"name": "human review gold", "status": "BLOCKED", "detail": "human_review=1/40"},
            {"name": "model parity", "status": "BLOCKED", "detail": "deployed unknown"},
            {"name": "P1 classifier", "status": "PASS", "detail": "ok"},
        ],
    )
    # strict (default): a BLOCKED gate still blocks -> exit 1
    monkeypatch.setattr("sys.argv", ["check_release_gate.py", "--readiness", str(readiness)])
    assert check_release_gate.main() == 1
    # pilot: data-ceiling BLOCKED gates waived with audit -> exit 0
    monkeypatch.setattr(
        "sys.argv",
        ["check_release_gate.py", "--readiness", str(readiness), "--allow-conditional"],
    )
    assert check_release_gate.main() == 0


def test_release_gate_pilot_never_waives_fail(tmp_path, monkeypatch):
    # A genuine regression (FAIL gate) must block even in pilot mode.
    readiness = _write_readiness(
        tmp_path,
        "FAIL",
        [
            {"name": "P1 classifier", "status": "FAIL", "detail": "f1 regressed"},
            {"name": "human review gold", "status": "BLOCKED", "detail": "human_review=1/40"},
        ],
    )
    monkeypatch.setattr(
        "sys.argv",
        ["check_release_gate.py", "--readiness", str(readiness), "--allow-conditional"],
    )
    assert check_release_gate.main() == 1


def test_release_gate_pilot_env_flag_enables_waiver(tmp_path, monkeypatch):
    # RELEASE_GATE_ALLOW_CONDITIONAL=1 enables pilot mode without the CLI flag.
    readiness = _write_readiness(
        tmp_path,
        "CONDITIONALLY_READY",
        [{"name": "human review gold", "status": "BLOCKED", "detail": "human_review=1/40"}],
    )
    monkeypatch.setenv("RELEASE_GATE_ALLOW_CONDITIONAL", "1")
    monkeypatch.setattr("sys.argv", ["check_release_gate.py", "--readiness", str(readiness)])
    assert check_release_gate.main() == 0


def test_release_gate_missing_report_never_waived(tmp_path, monkeypatch):
    # A missing report is an evidence gap, not a data ceiling -> never waivable.
    missing = tmp_path / "nope.json"
    monkeypatch.setattr(
        "sys.argv",
        ["check_release_gate.py", "--readiness", str(missing), "--allow-conditional"],
    )
    assert check_release_gate.main() == 1


def test_release_gate_empty_gates_fail_closed(tmp_path, monkeypatch):
    readiness = _write_readiness(tmp_path, "PASS", [])
    monkeypatch.setattr("sys.argv", ["check_release_gate.py", "--readiness", str(readiness)])
    assert check_release_gate.main() == 1


def test_release_gate_invalid_verdict_fail_closed(tmp_path, monkeypatch):
    readiness = _write_readiness(tmp_path, "UNKNOWN", [{"name": "x", "status": "PASS"}])
    monkeypatch.setattr("sys.argv", ["check_release_gate.py", "--readiness", str(readiness)])
    assert check_release_gate.main() == 1


def test_release_gate_malformed_gate_fail_closed(tmp_path, monkeypatch):
    readiness = _write_readiness(tmp_path, "PASS", [{"name": "x"}])
    monkeypatch.setattr("sys.argv", ["check_release_gate.py", "--readiness", str(readiness)])
    assert check_release_gate.main() == 1


def test_human_review_queue_prioritizes_high_risk_underclassification(tmp_path, monkeypatch):
    report = tmp_path / "p1_report.json"
    _write_json(
        report,
        {
            "errors_sample": [
                {"doc_id": "a", "true": "S2", "pred": "S3", "text_preview": "needs review"},
                {"doc_id": "b", "true": "S3", "pred": "S1", "text_preview": "less urgent"},
            ]
        },
    )
    gold = tmp_path / "gold.jsonl"
    gold.write_text(
        json.dumps({"doc_id": "a", "text": "full text", "label": "S2", "label_source": "llm_judge_primary"})
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "queue.csv"
    monkeypatch.setattr(
        "sys.argv",
        [
            "build_human_review_queue.py",
            "--report",
            str(report),
            "--gold",
            str(gold),
            "--out",
            str(out),
            "--limit",
            "2",
        ],
    )

    assert build_human_review_queue.main() == 0
    rows = out.read_text(encoding="utf-8-sig").splitlines()
    assert rows[0].startswith("doc_id,model_label,human_label")
    assert rows[1].startswith("a,S3,S2")


def test_high_risk_queue_excludes_rulings_and_prioritizes_underclass():
    gold = {
        "ruling1": {"doc_id": "ruling1", "source": "판례", "label": "S2", "label_source": "llm_judge_primary", "text": "x"},
        "fin_under": {"doc_id": "fin_under", "source": "금융보고서", "label": "S2", "label_source": "llm_judge_primary", "text": "투자 전략"},
        "fin_ok": {"doc_id": "fin_ok", "source": "금융보고서", "label": "S1", "label_source": "koipa_case_based", "text": "원가 구조"},
        "s3doc": {"doc_id": "s3doc", "source": "금융보고서", "label": "S3", "label_source": "llm_judge_primary", "text": "공개 보도자료"},
        "reviewed": {"doc_id": "reviewed", "source": "금융보고서", "label": "S2", "label_source": "human_review", "text": "이미 검수"},
    }
    preds = {"fin_under": "S3"}  # 모델이 S3로 과소분류
    queue = build_human_review_queue.build_high_risk_queue(gold, preds, limit=10)
    ids = [r["doc_id"] for r in queue]
    assert "ruling1" not in ids        # 판례 제외
    assert "s3doc" not in ids          # 고위험 아님 제외
    assert "reviewed" not in ids       # 이미 human_review 제외
    assert ids[0] == "fin_under"       # underclass 최우선
    assert set(ids) == {"fin_under", "fin_ok"}
    assert queue[0]["model_label"] == "S3" and queue[0]["human_label"] == "S2"


def test_p1_boundary_report_writes_priority_sections(tmp_path, monkeypatch):
    source = tmp_path / "p1.json"
    _write_json(
        source,
        {
            "model_dir": "model",
            "mode": "direct",
            "metrics": {"n": 2, "f1_macro": 0.5, "high_risk_to_s3": 1},
            "pattern_counts": {"S2->S3": 1},
            "priority_errors": {
                "high_risk_to_s3": [{"doc_id": "a", "true": "S2", "pred": "S3", "label_source": "llm"}],
                "s3_overclassified": [],
            },
        },
    )
    out = tmp_path / "boundary.md"
    monkeypatch.setattr("sys.argv", ["build_p1_boundary_report.py", "--report", str(source), "--out", str(out)])

    assert build_p1_boundary_report.main() == 0
    text = out.read_text(encoding="utf-8")
    assert "High-Risk Downgraded To S3" in text
    assert "S2->S3" in text


def test_release_manifest_marks_missing_files(tmp_path, monkeypatch):
    present = tmp_path / "present.txt"
    present.write_text("ok", encoding="utf-8")
    out = tmp_path / "manifest.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["build_release_manifest.py", "--out", str(out), "--files", "present.txt", "missing.txt"],
    )

    assert build_release_manifest.main() == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "INCOMPLETE"
    assert payload["missing"] == ["missing.txt"]
    assert payload["artifacts"][0]["sha256"]


# ── 게이트 입력의 신선도 (2026-08-16) ────────────────────────────────────────
# 왜 이 테스트들이 있나: readiness 리포트 자신은 generated_at·git_sha 를 남기고
# --require-fresh 가 그것을 봤다. 그런데 **그 리포트가 무엇을 읽었는지는 아무도 안 봤다.**
#
#     배포본      vector_backend = pg
#     게이트 입력  reports/p2_gold_kure_es_hybrid_v3.json  (ES · 2026-06-02 생성)
#
# 출하하지 않는 구성의 검색 품질이 2개월 반 동안 게이트를 통과했고, 그 사이 readiness 는
# 매번 당일 날짜로 찍혔다. 경로를 고쳐 그 한 건은 막았지만 기록·검사가 없으면 반복된다.
import hashlib as _hashlib  # noqa: E402
import datetime as _dt2  # noqa: E402


def _iso_days_ago(n: int) -> str:
    return (_dt2.datetime.now(_dt2.timezone.utc)
            - _dt2.timedelta(days=n)).isoformat(timespec="seconds")


def _readiness_with_inputs(tmp_path, inputs):
    readiness = tmp_path / "readiness.json"
    readiness.write_text(json.dumps({
        "verdict": "PASS",
        "gates": [{"name": "P1 classifier", "status": "PASS", "detail": "ok"}],
        "generated_at": _dt2.date.today().isoformat(),
        # git sha 는 여기 관심사가 아니다 - _problems 가 넘기는 expect 값과 맞춰
        # 그 항목이 안 걸리게 둔다(입력 검사만 보려는 것이다).
        "git_sha": "-",
        "evidence_inputs": inputs,
    }), encoding="utf-8")
    return readiness


def _make_input(tmp_path, role, kind, *, days_old=0, body=b"{}", exists=True,
                wrong_sha=False):
    p = tmp_path / f"{role}.json"
    item = {"role": role, "kind": kind, "path": str(p), "exists": exists}
    if not exists:
        return item
    p.write_bytes(body)
    sha = _hashlib.sha256(body).hexdigest()
    item.update({"size": len(body), "mtime": _iso_days_ago(days_old),
                 "sha256": "0" * 64 if wrong_sha else sha})
    return item


def _fresh_argv(readiness):
    # git sha 검사는 여기 관심사가 아니라 비활성(--expect-git-sha 는 빈 값이면 HEAD 를
    # 쓰므로, 리포트에 sha 를 안 넣은 이 픽스처에서는 그 항목이 항상 걸린다).
    return ["check_release_gate.py", "--readiness", str(readiness),
            "--require-fresh", "--expect-git-sha", "-"]


def _problems(readiness, monkeypatch):
    """--require-fresh 로 돌리고 걸린 사유만 뽑는다."""
    import argparse

    ns = argparse.Namespace(require_fresh=True, max_age_days=14,
                            expect_git_sha="-", expect_model_dir="",
                            expect_profile="")
    payload = json.loads(Path(readiness).read_text(encoding="utf-8"))
    return check_release_gate._freshness_problems(payload, ns)


def test_input_freshness_stale_measurement_is_caught(tmp_path, monkeypatch):
    r = _readiness_with_inputs(tmp_path, [
        _make_input(tmp_path, "p2", "measurement", days_old=76),
    ])
    probs = _problems(r, monkeypatch)
    assert any("p2" in p and "76d old" in p for p in probs), probs


def test_input_freshness_frozen_dataset_is_not_aged_out(tmp_path, monkeypatch):
    """골든셋은 동결이 정상이다 - 오래됐다고 릴리스를 막으면 안 된다."""
    r = _readiness_with_inputs(tmp_path, [
        _make_input(tmp_path, "retrieval_gold", "dataset", days_old=400),
    ])
    assert _problems(r, monkeypatch) == []


def test_input_freshness_missing_input_is_caught(tmp_path, monkeypatch):
    r = _readiness_with_inputs(tmp_path, [
        _make_input(tmp_path, "p1_serving", "measurement", exists=False),
    ])
    probs = _problems(r, monkeypatch)
    assert any("missing" in p and "p1_serving" in p for p in probs), probs


def test_input_freshness_changed_file_is_caught(tmp_path, monkeypatch):
    """파일이 그 뒤로 바뀌었으면 이 요약은 그 파일에 대한 것이 아니다."""
    r = _readiness_with_inputs(tmp_path, [
        _make_input(tmp_path, "p2", "measurement", wrong_sha=True),
    ])
    probs = _problems(r, monkeypatch)
    assert any("changed since" in p for p in probs), probs


def test_input_freshness_report_without_inputs_is_flagged(tmp_path, monkeypatch):
    """이 필드가 생기기 전 리포트 - 무엇을 읽었는지 못 보여주는 것 자체가 갭이다."""
    readiness = tmp_path / "readiness.json"
    readiness.write_text(json.dumps({
        "verdict": "PASS", "gates": [{"name": "x", "status": "PASS"}],
        "generated_at": _dt2.date.today().isoformat(), "git_sha": "-",
    }), encoding="utf-8")
    probs = _problems(readiness, monkeypatch)
    assert any("evidence_inputs" in p for p in probs), probs
