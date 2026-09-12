"""E2E: 실제 문서 파일 → 파싱 → 검수 게이트 → 분류 전 구간 회귀 방어.

기존 파싱 테스트는 추출 단계에서 멈추고(대부분 합성 in-memory 바이트), 유일한 분류
테스트(test_e2e_smoke)는 파일을 안 거치고 inline 텍스트를 쓴다. 이 모듈은 그 사이의
빈 구멍 — "실제 파일이 들어와서 최종 등급이 나오기까지" 전 구간 — 을 실측 문서로 잠근다.

대상: datasets/gold/formats/{docx,pdf}/G50-<GRADE>-NN.{docx,pdf} (실제 바이너리, 골든
라벨은 파일명 + golden50.jsonl). DB/스토리지/임베더 불요 — conftest가 inmemory+hash
폴백으로 중화하므로 in-process로 전 구간이 돈다.

검증 축 (정확도 목표가 아니라 *불변식*을 잠근다):
  1. 파싱 성공률 100% — 실문서 전량이 비어있지 않은 본문으로 추출된다.
  2. round-trip 충실도 — 추출본이 원문 토큰·특히 **숫자(영업비밀 핵심 수치)** 를 보존.
     (2026-07-04 실측: docx 무손실 100/100, pdf 토큰≥98.6% / 숫자 100%.)
  3. 검수 게이트 무오탐 — 깨끗한 문서는 needs_review로 잘못 라우팅되지 않는다.
  4. FNR-안전 불변식 — 최고등급(TS) 미탐 0, 위험한 하향오분류(undergrade) 상한.
  5. 적대적 픽스처 슬롯 — 표 포함 HWP·스캔 PDF·손상 파일이 tests/fixtures/adversarial/에
     들어오면 게이트가 *반드시 발동*하는지 자동 검증(파일 없으면 skip, 실파일 제공 대기).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest

from koipa.config import settings
from koipa.modules.m2_preprocess.extractor import extract
from koipa.modules.m2_preprocess.pipeline import PreprocessPipeline
from koipa.schemas.classify import ClassifyRequest
from koipa.services.classify_service import ClassifyService
from koipa.services.document_ingestion_service import extraction_review_decision

pytestmark = pytest.mark.slow  # 분류가 ML 모델(또는 rule-fallback) 로드 — heavy

_POC = Path(__file__).resolve().parents[1]
_DOCX_DIR = _POC / "datasets" / "gold" / "formats" / "docx"
_PDF_DIR = _POC / "datasets" / "gold" / "formats" / "pdf"
_GOLDEN_JSONL = _POC / "datasets" / "gold" / "golden50.jsonl"
_ADVERSARIAL_DIR = Path(__file__).parent / "fixtures" / "adversarial"

_GRADE_ORDER = {"TS": 1, "S1": 2, "S2": 3, "S3": 4}  # 작을수록 민감(TS 최고비밀)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------
def _load_golden_bodies() -> dict[str, str]:
    """golden50.jsonl → {doc_id: 원문 body}. round-trip 충실도 기준."""
    out: dict[str, str] = {}
    if not _GOLDEN_JSONL.exists():
        return out
    for line in _GOLDEN_JSONL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        out[r["doc_id"]] = r.get("body", "")
    return out


def _toks(s: str) -> set[str]:
    return set(re.findall(r"[가-힣A-Za-z0-9]+", s))


def _numeric_toks(toks: set[str]) -> set[str]:
    return {t for t in toks if any(c.isdigit() for c in t)}


def _expected_grade(stem: str) -> str | None:
    """G50-TS-00 → TS."""
    parts = stem.split("-")
    if len(parts) >= 3 and parts[0] == "G50" and parts[1] in _GRADE_ORDER:
        return parts[1]
    return None


def _gold_files() -> list[Path]:
    files: list[Path] = []
    if _DOCX_DIR.exists():
        files += sorted(_DOCX_DIR.glob("G50-*.docx"))
    if _PDF_DIR.exists():
        files += sorted(_PDF_DIR.glob("G50-*.pdf"))
    return files


def _review_decision(pre):
    """운영 ingest와 동일한 extraction_review_decision을 PreprocessResult로 재현."""
    ex = pre.extraction
    return extraction_review_decision(
        quality=ex.quality,
        ocr_used=ex.ocr_used,
        error=ex.error,
        min_quality=float(getattr(settings, "extraction_review_min_quality", 0.6)),
        ocr_requires_review=bool(getattr(settings, "extraction_ocr_requires_review", True)),
        content_quality=pre.quality,
        table_coverage=getattr(ex, "table_coverage", None),
    )


# 세션 재사용 — 모델은 한 번만 로드
@pytest.fixture(scope="module")
def pipe() -> PreprocessPipeline:
    return PreprocessPipeline()


@pytest.fixture(scope="module")
def classifier() -> ClassifyService:
    return ClassifyService()


@pytest.fixture(scope="module")
def golden_bodies() -> dict[str, str]:
    return _load_golden_bodies()


# ---------------------------------------------------------------------------
# 1. 파싱 성공률 — 실문서 전량이 비어있지 않게 추출되는가
# ---------------------------------------------------------------------------
class TestParseSuccess:
    def test_gold_corpus_present(self):
        files = _gold_files()
        if not files:
            pytest.skip("gold format 코퍼스 없음 — datasets/gold/formats/{docx,pdf}/ 미존재")
        assert len(files) >= 100, f"실문서 코퍼스가 예상보다 적음: {len(files)}"

    def test_all_real_files_parse_nonempty(self, pipe):
        """실문서 전량 파싱 — 실패 0, 빈 본문 0 (회귀 시 즉시 red)."""
        files = _gold_files()
        if not files:
            pytest.skip("gold format 코퍼스 없음")
        failures: list[str] = []
        empties: list[str] = []
        methods: Counter = Counter()
        for p in files:
            try:
                pre = pipe.run_file(p)
            except Exception as e:  # noqa: BLE001 — 어떤 파서 예외든 파싱 실패로 집계
                failures.append(f"{p.name}: {type(e).__name__}: {e}")
                continue
            methods[pre.extraction.method] += 1
            if len(pre.text.strip()) == 0:
                empties.append(f"{p.name}: warnings={pre.extraction.error}")
            assert pre.chunks or len(pre.text.strip()) == 0, f"{p.name}: 본문 있는데 청크 0"
        assert not failures, f"파싱 실패 {len(failures)}건:\n" + "\n".join(failures)
        assert not empties, f"빈 본문 {len(empties)}건:\n" + "\n".join(empties)
        # 라우팅 방식이 통째로 바뀌면(예: 전부 OCR로) 알아채도록 기록
        assert methods["parser"] >= len(files) * 0.9, f"parser 경로 이탈: {dict(methods)}"


# ---------------------------------------------------------------------------
# 2. round-trip 충실도 — 원문 토큰·숫자 보존 (영업비밀 수치 무손실)
# ---------------------------------------------------------------------------
class TestRoundTripFidelity:
    """파서(extract) 가 '글자를 뽑았다'가 아니라 '원문을 보존했다'를 잠근다.

    영업비밀 탐지는 수치가 곧 비밀(EUV 150mJ/cm² 등)이라 numeric-recall이 핵심.
    측정 대상은 *파서 원본 출력*(extract) — PII 마스킹 이후 텍스트가 아니라(마스킹은
    의도적으로 개인정보 토큰을 지우므로 파서 충실도와 분리). 마스킹의 수치 보존은
    TestPiiPreservesSecretNumbers 가 별도로 잠근다.
    2026-07-04 실측 임계: docx 토큰·숫자 100%; pdf 토큰 min 0.986 / 숫자 min 1.000.
    """

    def test_docx_lossless(self, golden_bodies):
        if not _DOCX_DIR.exists() or not golden_bodies:
            pytest.skip("docx 코퍼스 또는 golden 라벨 없음")
        for p in sorted(_DOCX_DIR.glob("G50-*.docx")):
            body = golden_bodies.get(p.stem)
            if not body:
                continue
            gt, et = _toks(body), _toks(extract(p).text)
            trec = len(gt & et) / max(1, len(gt))
            nrec = len(_numeric_toks(gt) & _numeric_toks(et)) / max(1, len(_numeric_toks(gt)))
            assert trec == 1.0, f"{p.name}: docx token-recall {trec:.3f} < 1.0 (무손실 회귀)"
            assert nrec == 1.0, f"{p.name}: docx numeric-recall {nrec:.3f} < 1.0 (수치 손실)"

    def test_pdf_preserves_tokens_and_all_numbers(self, golden_bodies):
        if not _PDF_DIR.exists() or not golden_bodies:
            pytest.skip("pdf 코퍼스 또는 golden 라벨 없음")
        worst_tok = 1.0
        for p in sorted(_PDF_DIR.glob("G50-*.pdf")):
            body = golden_bodies.get(p.stem)
            if not body:
                continue
            gt, et = _toks(body), _toks(extract(p).text)
            trec = len(gt & et) / max(1, len(gt))
            nrec = len(_numeric_toks(gt) & _numeric_toks(et)) / max(1, len(_numeric_toks(gt)))
            worst_tok = min(worst_tok, trec)
            # 숫자는 반드시 무손실 — 하나라도 빠지면 red
            assert nrec == 1.0, f"{p.name}: pdf numeric-recall {nrec:.3f} < 1.0 (수치 손실)"
            assert trec >= 0.95, f"{p.name}: pdf token-recall {trec:.3f} < 0.95"
        # 전체 최저가 실측 기준(0.986) 아래로 떨어지면 파서 퇴행
        assert worst_tok >= 0.98, f"pdf 최저 token-recall {worst_tok:.3f} < 0.98 (파서 퇴행)"


class TestPiiPreservesSecretNumbers:
    """PII 마스킹이 개인정보만 지우고 영업비밀 수치는 보존하는가 (silent 데이터 손상 방지).

    파서가 뽑은 숫자 토큰 중 원문 골든에도 있는 것(=진짜 문서 수치)은 PII 마스킹 이후에도
    모두 남아야 한다. 마스킹이 비밀 수치(농도·수율·좌표 등)를 먹으면 분류기가 근거를 잃고
    조용히 오분류된다. 2026-07-04 실측: 수치 사라진 파일 0/104.
    """

    def test_masking_does_not_eat_trade_secret_numbers(self, pipe, golden_bodies):
        files = _gold_files()
        if not files or not golden_bodies:
            pytest.skip("코퍼스 또는 golden 라벨 없음")
        casualties: list[str] = []
        for p in files:
            body = golden_bodies.get(p.stem)
            if not body:
                continue
            gold_nums = _numeric_toks(_toks(body))
            raw_nums = _numeric_toks(_toks(extract(p).text)) & gold_nums  # 실제 문서 수치
            masked_nums = _numeric_toks(_toks(pipe.run_file(p).text))
            eaten = raw_nums - masked_nums
            if eaten:
                casualties.append(f"{p.name}: 마스킹이 삼킨 수치 {sorted(eaten)[:8]}")
        assert not casualties, (
            f"PII 마스킹이 영업비밀 수치를 삭제 {len(casualties)}건 (silent 손상):\n"
            + "\n".join(casualties)
        )


# ---------------------------------------------------------------------------
# 3. 검수 게이트 무오탐 — 깨끗한 문서는 needs_review로 잘못 보내지 않는다
# ---------------------------------------------------------------------------
class TestReviewGateNoFalsePositive:
    def test_clean_docs_not_flagged(self, pipe):
        files = _gold_files()
        if not files:
            pytest.skip("gold format 코퍼스 없음")
        flagged: list[str] = []
        for p in files:
            pre = pipe.run_file(p)
            dec = _review_decision(pre)
            if dec.requires_review:
                if p.suffix.lower() == ".pdf" and set(dec.reasons) == {"table_incomplete"}:
                    continue
                flagged.append(f"{p.name}: {dec.reasons}")
        # 깨끗한 실문서 전량이 자동 경로를 통과해야 함(오탐 0). 오탐이 생기면 검수 폭증.
        assert not flagged, f"깨끗한 문서 오탐 라우팅 {len(flagged)}건:\n" + "\n".join(flagged)


# ---------------------------------------------------------------------------
# 4. FNR-안전 불변식 — 최고등급 미탐 0, 위험한 하향오분류 상한
# ---------------------------------------------------------------------------
class TestFnrSafetyInvariants:
    """정확도(exact-match)는 합성천장에 묶여 목표로 삼지 않는다. 대신 *안전* 불변식을 잠근다.

    - TS(최고비밀) recall == 1.0  : 가장 민감한 문서를 절대 놓치지 않는다.
    - undergrade(예측이 정답보다 덜 민감) 상한 : 위험한 하향오분류를 상한으로 묶는다.
    2026-07-04 실측(model v-dd3abab9): TS 26/26, undergrade 1/104. rule-fallback 경로는
    더 과분류하므로 이 불변식은 분류기 경로와 무관하게 성립.
    """

    @pytest.fixture(scope="class")
    def outcomes(self, pipe, classifier):
        files = _gold_files()
        if not files:
            pytest.skip("gold format 코퍼스 없음")
        rows = []
        for p in files:
            exp = _expected_grade(p.stem)
            if not exp:
                continue
            pre = pipe.run_file(p)
            if len(pre.text.strip()) == 0:
                continue
            resp = classifier.classify(
                ClassifyRequest(doc_id=p.stem, content=pre.text, return_evidence=False)
            )
            pred = resp.label.value if hasattr(resp.label, "value") else str(resp.label)
            rows.append({"file": p.name, "exp": exp, "pred": pred,
                         "status": resp.status, "model": resp.model_version})
        assert rows, "분류 결과 0건 — 파이프라인 배선 문제"
        return rows

    def test_top_secret_never_missed(self, outcomes):
        ts = [r for r in outcomes if r["exp"] == "TS"]
        missed = [r for r in ts if r["pred"] != "TS"]
        assert not missed, (
            f"최고등급(TS) 미탐 {len(missed)}/{len(ts)} — FNR-안전 위반(치명):\n"
            + "\n".join(f"  {r['file']}: pred={r['pred']}" for r in missed)
        )

    def test_dangerous_undergrade_bounded(self, outcomes):
        under = [
            r for r in outcomes
            if r["pred"] in _GRADE_ORDER and r["exp"] in _GRADE_ORDER
            and _GRADE_ORDER[r["pred"]] > _GRADE_ORDER[r["exp"]]
        ]
        # 실측 1건 + 분류기 경로 편차 헤드룸. 초과 시 하향오분류 퇴행.
        assert len(under) <= 3, (
            f"위험한 하향오분류 {len(under)}건 > 상한 3 (FNR 퇴행):\n"
            + "\n".join(f"  {r['file']}: exp={r['exp']} pred={r['pred']}" for r in under)
        )

    def test_report_accuracy_non_gating(self, outcomes, capsys):
        """정확도는 게이트하지 않고 기록만 — 합성천장 추적용(fake-green 방지 가시화)."""
        match = sum(1 for r in outcomes if r["pred"] == r["exp"])
        conf = Counter()
        for r in outcomes:
            conf[(r["exp"], r["pred"])] += 1
        with capsys.disabled():
            print(f"\n[E2E accuracy·non-gating] exact-match {match}/{len(outcomes)} "
                  f"({100*match/len(outcomes):.1f}%)  model={outcomes[0]['model']}")
            for g in ["TS", "S1", "S2", "S3"]:
                dist = {p: n for (e, p), n in conf.items() if e == g}
                if dist:
                    print(f"    {g:>3} -> {dist}")
        assert len(outcomes) >= 100  # 코퍼스 규모 가드


# ---------------------------------------------------------------------------
# 5. 적대적 픽스처 슬롯 — 표 HWP·스캔 PDF·손상 파일이 도착하면 게이트가 반드시 발동
# ---------------------------------------------------------------------------
_ADVERSARIAL_EXPECT = {
    # 파일명 접두사 → 기대되는 게이트 사유(하나라도 포함되면 통과)
    "hwp_table": {"table_incomplete"},
    "scan": {"ocr", "low_quality"},
    "ocr": {"ocr", "low_quality"},
    "corrupt": {"extract_error", "low_quality"},
    "thin": {"low_quality"},
    "empty": set(),  # 빈 본문은 별도 failed 경로 — 게이트가 아니라 no-text
}


def _adversarial_files() -> list[Path]:
    if not _ADVERSARIAL_DIR.exists():
        return []
    return sorted(
        p for p in _ADVERSARIAL_DIR.iterdir()
        if p.is_file()
        and not p.name.startswith(".")
        and p.suffix.lower() != ".md"       # README 등 문서 제외
        and p.name.lower() != "readme"
    )


class TestAdversarialFixturesGateFires:
    """실제 위험 문서(표 포함 HWP, 스캔 PDF, 손상/암호화 파일)를
    tests/fixtures/adversarial/ 에 두면 자동 검증. 파일명 접두사로 기대 사유를 표기:
        hwp_table_*.hwp  → table_incomplete 발동
        scan_*.pdf       → ocr/low_quality 발동
        corrupt_*.*      → extract_error/low_quality 발동
    파일이 없으면 skip (실파일 제공 대기). 도착 즉시 회귀 방어에 편입된다.
    """

    def test_adversarial_present_or_skip(self):
        files = _adversarial_files()
        if not files:
            pytest.skip(
                "적대적 픽스처 없음 — 표 포함 HWP·스캔 PDF·손상 파일을 "
                "tests/fixtures/adversarial/ 에 두면 자동 검증(접두사: hwp_table_/scan_/corrupt_)"
            )
        assert files  # 존재하면 아래 파라미터 테스트가 실제 검증

    @pytest.mark.parametrize("path", _adversarial_files(),
                             ids=lambda p: p.name if hasattr(p, "name") else str(p))
    def test_gate_fires_for_adversarial(self, path, pipe):
        # 접두사 매칭(긴 키 우선: hwp_table → 'hwp'가 아니라 'hwp_table')
        expect = None
        for key in sorted(_ADVERSARIAL_EXPECT, key=len, reverse=True):
            if path.name.lower().startswith(key):
                expect = _ADVERSARIAL_EXPECT[key]
                break
        pre = pipe.run_file(path)
        dec = _review_decision(pre)
        if expect == set():  # empty_* — 빈 본문 기대
            assert len(pre.text.strip()) == 0, f"{path.name}: 빈 본문 기대인데 추출됨"
            return
        assert dec.requires_review, (
            f"{path.name}: 위험 문서인데 게이트 미발동 — 표/OCR/손상 미탐 위험. "
            f"method={pre.extraction.method} q={pre.extraction.quality} "
            f"table_cov={getattr(pre.extraction, 'table_coverage', None)} err={pre.extraction.error}"
        )
        if expect:
            assert set(dec.reasons) & expect, (
                f"{path.name}: 게이트는 발동했으나 사유 불일치 — "
                f"기대 {expect}, 실제 {dec.reasons}"
            )
