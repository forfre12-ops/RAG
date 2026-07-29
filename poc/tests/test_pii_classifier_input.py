"""[#pii-skew] 분류기 입력 PII 무마스킹 + 저장 경로 마스킹 유지 잠금.

배경: 마스킹된 텍스트(내부 IP·사번·계좌를 [IP]/[EMP]/[CARD]로 치환)가 분류기에 들어가면
(1) 등급 신호 소실 (2) 학습·평가는 무마스킹이라 서빙만 마스킹 = OOD 스큐 → silent FNR.
분류기 입력은 무마스킹(pii_mask_classifier_input=False 기본), 저장/색인은 마스킹 유지.
"""

from __future__ import annotations

from lloydk.config import settings
from lloydk.modules.m2_preprocess.pipeline import PreprocessPipeline

_SAMPLE = "내부 서버 10.1.2.3 에 접속, 사업자등록번호 123-45-67890, 주민 800101-1234567 포함된 대외비 문서."


def test_run_text_does_not_mask_classifier_input_by_default():
    # 기본(False): 분류기 입력은 무마스킹 — 등급 신호 보존, 학습·평가 분포 정합.
    pipe = PreprocessPipeline()
    out = pipe.run_text(_SAMPLE)
    assert "10.1.2.3" in out                       # 내부 IP(등급 신호) 보존
    assert "[IP]" not in out
    assert "[RRN]" not in out and "[BIZNO]" not in out


def test_run_text_masks_classifier_input_when_enabled(monkeypatch):
    # opt-in(True): 학습·평가도 마스킹한 모델 배포 시 분류기 입력 마스킹 가능.
    monkeypatch.setattr(settings, "pii_mask_classifier_input", True, raising=False)
    pipe = PreprocessPipeline()
    out = pipe.run_text(_SAMPLE)
    assert "[RRN]" in out or "[BIZNO]" in out or "[IP]" in out
    assert "800101-1234567" not in out             # 주민번호 원문 노출 안 됨


def test_finalize_storage_path_still_masks():
    # 저장/색인 경로(_finalize)는 pii_masking_enabled로 마스킹 유지 — PII 보호 무변경.
    pipe = PreprocessPipeline(pii_masking=True)
    res = pipe.run_text_full(_SAMPLE)
    assert "[RRN]" in res.text or "[BIZNO]" in res.text
    assert res.pii_counts  # 마스킹 카운트 기록됨
