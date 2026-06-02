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
    ]
    for k, v in extra.items():
        argv += [k, v]
    monkeypatch_argv = argv
    return monkeypatch_argv, out


def _gold_with_human_review(tmp_path, n_underclass: int):
    """700 records: 40 human_review (n_underclass of them high-risk -> S3), rest public."""
    lines = []
    for i in range(40):
        if i < n_underclass:
            rec = {"label": "S2", "model_label": "S3", "label_source": "human_review"}
        else:
            rec = {"label": "S3", "model_label": "S3", "label_source": "human_review"}
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
