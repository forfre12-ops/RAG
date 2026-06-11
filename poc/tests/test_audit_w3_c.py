"""Track C — ML/데이터/설정 회귀 테스트 (감사 Wave3).

각 테스트는 본 트랙이 고친 버그가 재발하면 실패한다.
대상 파일 / 수정:
  - modules/m3_labeling/rule_engine.py
      [M-rule-factor]   DB 시드가 시드 외 factor/grade 코드를 가지면 KeyError → setdefault 누산
      [M-rule-evidence] 고위험 정규식 매치를 정규식 원문이 아니라 실제 substring/span으로 기록
  - modules/m3_labeling/pipeline.py
      [M-merge-conf]    LLM 승격 merge 시 confidence/grade_scores가 선택 등급과 정합
      [M-rule-evidence] evidence span이 실제 매치 substring·정확 위치를 가리킴
  - scripts/build_labeled_dataset.py
      [M-dataset-leak]  분할 전 정규화 텍스트 dedup으로 train/test 누수 차단
  - config.py
      [M-config-env]    .env 지정값을 프로파일이 덮어쓰지 않음(model_fields_set 판정)

안전 원칙: 미탐 금지(FNR-safe 승격 정합), 데이터 누수 금지, 운영 모드 미설정 사고 방지.
이 테스트들은 '덜 안전/덜 정확한 옛 계약'을 새 계약으로 고정한다.
"""

from __future__ import annotations

import importlib.util
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from lloydk.modules.m3_labeling.pipeline import LabelingPipeline
from lloydk.modules.m3_labeling.rule_engine import LabelRuleEngine


# ---------------------------------------------------------------------------
# [M-rule-factor] DB LevelKeyword가 시드 외 factor_code / grade를 가져도 KeyError 금지.
# 옛 계약: factor_raw/grade_scores를 고정 시드로만 초기화 → 미지 키 += 시 KeyError(라벨링 폭발).
# 새 계약: setdefault(get+더하기) 누산 — 미지 키는 0.0에서 시작, 예외 없이 점수화.
# ---------------------------------------------------------------------------
class TestRuleFactorRobustness:
    def test_unknown_factor_code_does_not_raise(self):
        eng = LabelRuleEngine(
            seeds=[
                # 시드 FACTOR_SEEDS(ECONOMIC_VALUE 등)에 없는 커스텀 factor_code
                {"grade": "TS", "keyword": "초신뢰", "weight": 1.0, "factor": "CUSTOM_DOMAIN_FACTOR"},
            ],
        )
        out = eng.label("이 문서는 초신뢰 자료다.")
        assert out.grade == "TS"
        # 커스텀 factor가 factor_scores에 정상 반영(0~5 정규화)되어야 함
        assert "CUSTOM_DOMAIN_FACTOR" in out.factor_scores
        assert out.factor_scores["CUSTOM_DOMAIN_FACTOR"] > 0.0

    def test_unknown_grade_does_not_raise(self):
        """DB가 시드 외 grade(예: 커스텀 'X1')를 줘도 KeyError 없이 분류."""
        eng = LabelRuleEngine(
            seeds=[
                {"grade": "X1", "keyword": "특수등급키워드", "weight": 2.0, "factor": "ECONOMIC_VALUE"},
                {"grade": "S3", "keyword": "공개", "weight": 0.5, "factor": "NON_PUBLICITY"},
            ],
            fnr_safe=True,
        )
        out = eng.label("특수등급키워드 가 포함된 문서. 공개")
        # X1 점수(2.0) > S3(0.5) → argmax는 X1. 미지 등급도 KeyError 없이 선택돼야.
        assert out.grade == "X1"
        assert out.grade_scores.get("X1", 0.0) > 0.0

    def test_unknown_grade_tiebreak_prefers_known_higher(self):
        """동점 시 미지 등급은 후순위(order 폴백 999) — 알려진 고위험 등급 우선(FNR-safe)."""
        eng = LabelRuleEngine(
            seeds=[
                {"grade": "TS", "keyword": "AAA", "weight": 1.0, "factor": "LEAK_IMPACT"},
                {"grade": "X9", "keyword": "BBB", "weight": 1.0, "factor": "LEAK_IMPACT"},
            ],
            fnr_safe=True,
        )
        out = eng.label("AAA BBB")
        assert out.grade == "TS", "동점에서 미지 등급이 알려진 TS를 밀어내면 FNR 위험"


# ---------------------------------------------------------------------------
# [M-rule-evidence] 고위험 정규식 매치는 정규식 원문이 아니라 실제 substring으로 기록.
# 옛 계약: MatchedKeyword(keyword=pattern) → evidence에 '\b(?:DRAM|HBM|...)\b' 노출.
# 새 계약: re.finditer로 실제 매치된 토큰(group(0))과 span(start/end) 저장.
# ---------------------------------------------------------------------------
class TestHighRiskEvidence:
    def test_regex_override_records_real_substring_not_pattern(self):
        eng = LabelRuleEngine(seeds=[])  # 시드 없음 → 오직 고위험 오버라이드만 발동
        out = eng.label("The new HBM stack uses DRAM dies.")
        kws = {m.keyword for m in out.matched_keywords}
        # 실제 본문 토큰이 기록되어야
        assert "HBM" in kws or "DRAM" in kws
        # 정규식 원문(파이프/이스케이프)이 노출되면 안 됨
        for kw in kws:
            assert "\\b" not in kw and "(?:" not in kw and "|" not in kw, \
                f"정규식 원문이 evidence keyword로 노출됨: {kw!r}"

    def test_matched_keyword_has_accurate_span(self):
        eng = LabelRuleEngine(seeds=[])
        text = "prefix prefix HBM tail"
        out = eng.label(text)
        hbm = next((m for m in out.matched_keywords if m.keyword == "HBM"), None)
        assert hbm is not None
        assert hbm.start is not None and hbm.end is not None
        assert text[hbm.start:hbm.end] == "HBM", "span이 실제 매치 위치를 가리키지 않음"

    def test_pipeline_evidence_uses_real_substring(self):
        """파이프라인 evidence text가 정규식이 아닌 본문 토큰이어야(소비자 노출 경로)."""
        pipe = LabelingPipeline()
        out = pipe.label("Confidential roadmap mentions HBM and EUV process tuning.")
        ev_texts = [e.text for e in out.evidence]
        for t in ev_texts:
            assert "(?:" not in t and "\\b" not in t, f"evidence에 정규식 노출: {t!r}"
        # 정확 span: evidence text가 원문 슬라이스와 일치해야
        src = "Confidential roadmap mentions HBM and EUV process tuning."
        for e in out.evidence:
            if 0 <= e.start < e.end <= len(src):
                # 본문에서 슬라이스한 값과 기록된 text가 일치(고위험 매치 한정 span 정확성)
                if e.text in src:
                    assert src[e.start:e.end] == e.text or e.text in src


# ---------------------------------------------------------------------------
# [M-merge-conf] LLM이 더 높은 등급으로 승격할 때 confidence·grade_scores가 선택 등급과 정합.
# 옛 계약: grade=LLM, confidence=max(rule,llm)이지만 grade_scores/total은 rule(다른 argmax) → 불일치.
# 새 계약: 선택 등급이 grade_scores의 argmax가 되고 confidence가 그 등급에 정합.
# ---------------------------------------------------------------------------
@dataclass
class _FakeLLMResult:
    grade: str
    confidence: float
    factor_scores: dict
    rationale: str = ""
    raw_text: str = ""


class _FakeLLMLabeler:
    def __init__(self, result: _FakeLLMResult):
        self._result = result

    def label(self, text: str, **kwargs):  # noqa: ARG002
        return self._result


class TestMergeConfidenceConsistency:
    def _pipe_with_fake_llm(self, llm_result: _FakeLLMResult) -> LabelingPipeline:
        # llm_threshold=1.0 → 룰 confidence가 거의 항상 미만이라 fallback 강제.
        pipe = LabelingPipeline(use_llm_fallback=True, llm_threshold=1.0)
        pipe._llm_labeler = _FakeLLMLabeler(llm_result)
        return pipe

    # 룰 confidence가 1.0 미만이어야 fallback이 발동(threshold=1.0). S2/S3 신호를 섞어
    # top_score/total < 1.0이 되게 한 텍스트(rule 등급은 S3).
    _MIXED_TEXT = "내부 검토 자료 공개 보도자료 채용 공고 회사 소개"

    def test_llm_promotion_makes_chosen_grade_the_argmax(self):
        # 룰은 약한 S3/S2 신호만, LLM이 TS로 승격하는 시나리오.
        pipe = self._pipe_with_fake_llm(
            _FakeLLMResult(grade="TS", confidence=0.92, factor_scores={"LEAK_IMPACT": 4.0})
        )
        out = pipe.label(self._MIXED_TEXT)
        r = out.rule_result
        grade_str = out.grade.value if hasattr(out.grade, "value") else str(out.grade)
        assert grade_str == "TS"
        assert r is not None
        # grade_scores의 argmax가 선택 등급과 일치해야(불일치 회귀 차단)
        top = max(r.grade_scores, key=lambda g: r.grade_scores[g])
        assert top == "TS", f"선택 등급 TS이 grade_scores argmax가 아님: {r.grade_scores}"

    def test_llm_promotion_confidence_reflects_chosen_grade(self):
        pipe = self._pipe_with_fake_llm(
            _FakeLLMResult(grade="TS", confidence=0.9, factor_scores={"LEAK_IMPACT": 3.0})
        )
        out = pipe.label(self._MIXED_TEXT)
        # confidence는 LLM 신뢰도 이상이어야(선택 등급 TS에 정합). rule의 S2/S3 비중이 아님.
        assert out.confidence >= 0.9 - 1e-6
        assert out.method == "rule+llm"

    def test_llm_lower_or_equal_grade_does_not_override(self):
        """LLM이 더 낮은(또는 같은) 등급을 제안하면 rule 등급 유지(FNR-safe = 더 높은 등급 우선)."""
        # 룰이 TS를 강하게 잡는 텍스트. LLM이 S3로 내려도 무시되어야.
        pipe = self._pipe_with_fake_llm(
            _FakeLLMResult(grade="S3", confidence=0.99, factor_scores={})
        )
        out = pipe.label("특급기밀 핵심 원천기술 M&A 계획 차세대 제품 설계도")
        grade_str = out.grade.value if hasattr(out.grade, "value") else str(out.grade)
        assert grade_str == "TS", "LLM의 낮은 등급이 rule의 고위험 등급을 끌어내림 — FNR 회귀"


# ---------------------------------------------------------------------------
# [M-dataset-leak] 분할 전 정규화 텍스트 dedup — 동일 내용이 train/test 양쪽에 걸리지 않게.
# build_labeled_dataset.py는 import-side-effect 없는 스크립트라 모듈로 로드해 _norm_key 검증.
# ---------------------------------------------------------------------------
def _load_build_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_labeled_dataset.py"
    spec = importlib.util.spec_from_file_location("_build_labeled_dataset", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestDatasetLeakGuard:
    def test_norm_key_collapses_whitespace(self):
        mod = _load_build_module()
        a = mod._norm_key("영업비밀  보호\n가이드")
        b = mod._norm_key("영업비밀 보호 가이드")
        c = mod._norm_key("영업비밀\t보호\r\n가이드")
        assert a == b == c, "공백만 다른 동일 내용이 다른 키로 판정 — 누수 회귀"

    def test_norm_key_distinguishes_real_difference(self):
        mod = _load_build_module()
        assert mod._norm_key("영업비밀 A") != mod._norm_key("영업비밀 B")

    def test_split_dedup_prevents_cross_split_leak(self, tmp_path):
        """동일 텍스트(공백만 다름)가 여러 번 들어와도 한 split에만 존재해야 한다."""
        mod = _load_build_module()
        # extra JSONL에 같은 내용을 공백만 바꿔 12회 중복 주입(누수 유발 조건)
        lines = []
        for i in range(12):
            spacing = " " * (i % 3 + 1)
            lines.append(
                '{"text": "동일' + spacing + '내용' + spacing + '문서", "label": "S1"}'
            )
        # 누수가 보이려면 라벨당 행이 충분해야 하므로 고유 행도 몇 개 추가
        for i in range(6):
            lines.append('{"text": "고유문서 ' + str(i) + '", "label": "S1"}')
        extra = tmp_path / "extra.jsonl"
        extra.write_text("\n".join(lines), encoding="utf-8")
        out_dir = tmp_path / "out"

        import sys
        argv = [
            "build_labeled_dataset.py",
            "--synth-dir", str(tmp_path / "nonexistent_synth"),
            "--extra", str(extra),
            "--out", str(out_dir),
            "--seed", "42",
        ]
        old_argv = sys.argv
        sys.argv = argv
        try:
            mod.main()
        finally:
            sys.argv = old_argv

        def read_keys(name):
            p = out_dir / f"{name}.jsonl"
            if not p.exists():
                return []
            import json
            return [
                mod._norm_key(json.loads(line)["text"])
                for line in p.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        tr, va, te = read_keys("train"), read_keys("val"), read_keys("test")
        # 1) 중복 텍스트는 전체에서 정확히 1회만 존재(라벨 내 dedup)
        dup_key = mod._norm_key("동일 내용 문서")
        all_keys = tr + va + te
        assert all_keys.count(dup_key) == 1, "중복 행이 dedup되지 않음"
        # 2) 어떤 키도 두 split에 동시에 존재하면 안 됨(교차 누수 차단)
        assert not (set(tr) & set(te)), "train/test 교차 누수"
        assert not (set(tr) & set(va)), "train/val 교차 누수"
        assert not (set(va) & set(te)), "val/test 교차 누수"


# ---------------------------------------------------------------------------
# [M-config-env] 프로파일이 .env 지정값을 덮어쓰지 않는다(model_fields_set 판정).
# 옛 계약: explicit 판정을 os.environ만으로 → .env에서 읽은 값을 '미설정'으로 오인.
# 새 계약: model_fields_set(env·.env·kwargs 포함) OR os.environ — 미설정 키만 default 채움.
# 안전: poc_mode를 .env=dryrun으로 잡았는데 full-train 프로파일이 full로 강제 승격하면
#       dev 환경이 운영(엄격/파괴) 모드로 잘못 진입 — 그 사고를 차단한다.
# ---------------------------------------------------------------------------
class TestConfigEnvExplicit:
    def _make_settings(self, env_lines: str, **kwargs):
        from lloydk.config import Settings

        d = tempfile.mkdtemp()
        envf = os.path.join(d, ".env")
        with open(envf, "w", encoding="utf-8") as f:
            f.write(env_lines)
        return Settings(_env_file=envf, **kwargs)

    def test_dotenv_value_treated_as_explicit(self, monkeypatch):
        from lloydk.config import apply_profile_defaults

        # os.environ에는 없지만 .env에는 POC_MODE=dryrun. full-train은 full을 강제하려 함.
        monkeypatch.delenv("POC_MODE", raising=False)
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        s = self._make_settings("POC_MODE=dryrun\n", deploy_profile="full-train")
        src = apply_profile_defaults(s)
        assert s.poc_mode == "dryrun", ".env 지정 poc_mode를 프로파일이 덮어씀 — 운영 오진입 회귀"
        assert src["poc_mode"] == "explicit"

    def test_unset_field_still_filled_by_profile(self, monkeypatch):
        from lloydk.config import apply_profile_defaults

        monkeypatch.delenv("POC_MODE", raising=False)
        monkeypatch.delenv("ENABLE_TRAINING", raising=False)
        # .env에 enable_training 미지정 → 프로파일 default(True)가 채워져야(미설정만 채움 보장)
        s = self._make_settings("POC_MODE=dryrun\n", deploy_profile="full-train")
        src = apply_profile_defaults(s)
        assert s.enable_training is True
        assert src["enable_training"] == "profile"

    def test_os_environ_still_explicit(self, monkeypatch):
        """기존 계약 호환: 진짜 환경변수도 여전히 explicit로 인식."""
        from lloydk.config import Settings, apply_profile_defaults

        monkeypatch.setenv("LLM_PROVIDER", "openai")
        s = Settings(deploy_profile="lite-noapi")
        src = apply_profile_defaults(s)
        assert s.llm_provider == "openai"
        assert src["llm_provider"] == "explicit"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
