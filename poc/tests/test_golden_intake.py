"""발주처 실문서 골든셋 인테이크 — 블라인드 라벨링·서명 게이트 잠금.

이 경로가 만드는 것은 감리에 제출될 **평가 정답지**다. 정답지의 자격은 네 가지로
결정되며(블라인드·서명 신원·학습 격리·동결), 그중 앞의 셋이 여기서 강제된다.
하나라도 새면 "기계가 정한 정답으로 기계를 채점"하는 순환으로 되돌아간다.
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

_POC = Path(__file__).resolve().parents[1]
_SCRIPT = _POC / "scripts" / "golden_intake.py"

SHEET = "label_sheet.csv"
INTAKE = "intake.jsonl"
LOCKED = "locked_gold_eval.jsonl"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        cwd=str(_POC), capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300,
    )


def _read_sheet(d: Path) -> list[dict]:
    with (d / SHEET).open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _write_sheet(d: Path, rows: list[dict]) -> None:
    with (d / SHEET).open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def _fill(rows: list[dict], **kw) -> list[dict]:
    for r in rows:
        r["등급"] = kw.get("grade", "S2")
        r["판단근거"] = kw.get("reason", "내부 원가정보 — 경쟁상 이익")
        r["확정자"] = kw.get("who", "kipo.hong")
        r["확정일"] = kw.get("when", "2026-08-02")
    return rows


@pytest.fixture()
def docs(tmp_path: Path) -> Path:
    d = tmp_path / "docs"
    d.mkdir()
    (d / "내부_원가표.txt").write_text("2026년 원가 산정 내역과 협력사별 단가 " * 20, encoding="utf-8")
    (d / "공개_보도자료.txt").write_text("당사는 신제품을 공개 발표하였습니다. " * 20, encoding="utf-8")
    return d


class TestScanIsBlind:
    """시트에 시스템 예측이 실리면 담당자가 그 답에 끌린다(앵커링) → 정답지 자격 상실."""

    def test_sheet_has_no_predicted_grade(self, docs: Path, tmp_path: Path):
        out = tmp_path / "intake"
        r = _run("scan", "--docs-dir", str(docs), "--out-dir", str(out))
        assert r.returncode == 0, r.stderr

        rows = _read_sheet(out)
        assert len(rows) == 2
        # 등급 칸은 반드시 비어 있어야 한다 — 예측값이 들어가면 블라인드가 깨진다.
        assert all(r["등급"] == "" for r in rows)
        assert all(r["판단근거"] == "" and r["확정자"] == "" for r in rows)
        # 시트 어디에도 등급 문자열이 예측으로 새어들어가지 않는다.
        header_and_meta = {k for r in rows for k in r}
        assert "예측등급" not in header_and_meta and "model_grade" not in header_and_meta

    def test_intake_carries_text_and_hash(self, docs: Path, tmp_path: Path):
        out = tmp_path / "intake"
        _run("scan", "--docs-dir", str(docs), "--out-dir", str(out))
        recs = [json.loads(ln) for ln in (out / INTAKE).read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(recs) == 2
        assert all(r["text"] and len(r["text_sha256"]) == 64 for r in recs)

    def test_guide_file_written(self, docs: Path, tmp_path: Path):
        out = tmp_path / "intake"
        _run("scan", "--docs-dir", str(docs), "--out-dir", str(out))
        guide = (out / "기입안내.txt").read_text(encoding="utf-8")
        assert "분류를 돌리기 전에" in guide  # 블라인드 지시가 담당자에게 전달돼야 한다


class TestBuildGates:
    def test_promotes_with_human_review_source(self, docs: Path, tmp_path: Path):
        out = tmp_path / "intake"
        _run("scan", "--docs-dir", str(docs), "--out-dir", str(out))
        _write_sheet(out, _fill(_read_sheet(out)))

        r = _run("build", "--intake-dir", str(out))
        assert r.returncode == 0, r.stderr

        locked = [json.loads(ln) for ln in (out / LOCKED).read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(locked) == 2
        for rec in locked:
            assert rec["label_source"] == "human_review"   # 평가 정답지의 자격
            assert rec["tier"] == "locked_gold_eval"
            assert rec["reviewer_id"] == "kipo.hong"
            assert rec["note"]                              # 판단근거 = 감리 질의 대응
            assert rec["document_origin"] == "customer_real"

    def test_machine_reviewer_rejected(self, docs: Path, tmp_path: Path):
        # 기계/보조 계정이 사람 서명으로 섞이면 정답지가 다시 기계 라벨이 된다.
        out = tmp_path / "intake"
        _run("scan", "--docs-dir", str(docs), "--out-dir", str(out))
        _write_sheet(out, _fill(_read_sheet(out), who="ai_assist_bot"))

        r = _run("build", "--intake-dir", str(out))
        assert not (out / LOCKED).exists() or not (out / LOCKED).read_text(encoding="utf-8").strip()
        assert "machine_reviewer" in r.stdout or r.returncode != 0

    def test_training_document_excluded(self, tmp_path: Path):
        # 학습에 쓴 문서를 평가에 쓰면 train-on-test — 측정이 통째로 무효가 된다.
        train = _POC / "datasets" / "gold_real" / "train_subset.jsonl"
        if not train.exists():
            pytest.skip("train_subset.jsonl 없음")
        text = json.loads(train.read_text(encoding="utf-8").splitlines()[0])["text"]

        d = tmp_path / "docs"
        d.mkdir()
        (d / "학습셋문서.txt").write_text(text, encoding="utf-8")

        out = tmp_path / "intake"
        _run("scan", "--docs-dir", str(d), "--out-dir", str(out))
        _write_sheet(out, _fill(_read_sheet(out)))

        r = _run("build", "--intake-dir", str(out))
        assert "학습셋과 동일 문서" in r.stdout
        assert r.returncode == 2   # 유효 행 0 → 중단

    @pytest.mark.parametrize(
        "kw,label",
        [({"grade": ""}, "등급 미기입"), ({"grade": "S9"}, "등급 값 오류"),
         ({"who": ""}, "확정자 미기입"), ({"reason": ""}, "판단근거 미기입")],
    )
    def test_incomplete_rows_excluded(self, docs: Path, tmp_path: Path, kw: dict, label: str):
        out = tmp_path / "intake"
        _run("scan", "--docs-dir", str(docs), "--out-dir", str(out))
        _write_sheet(out, _fill(_read_sheet(out), **kw))

        r = _run("build", "--intake-dir", str(out))
        assert label in r.stdout
        assert r.returncode == 2   # 전 행이 제외되면 정답지를 만들지 않는다

    def test_partial_fill_keeps_valid_rows(self, docs: Path, tmp_path: Path):
        # 판단이 어려운 문서는 비워 두라고 안내한다 — 나머지는 정상 승격돼야 한다.
        out = tmp_path / "intake"
        _run("scan", "--docs-dir", str(docs), "--out-dir", str(out))
        rows = _fill(_read_sheet(out))
        rows[0]["등급"] = ""
        _write_sheet(out, rows)

        r = _run("build", "--intake-dir", str(out))
        assert r.returncode == 0
        locked = [json.loads(ln) for ln in (out / LOCKED).read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(locked) == 1
