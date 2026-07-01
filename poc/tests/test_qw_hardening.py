"""[QW/P1a] 무중단 하드닝 — SSRF 가드·rule semantic 임계 config화·deploy-gate floor 경계.

- SSRF: outbox publish 대상 URL이 스킴/loopback을 거부(폐쇄망 사설IP는 기본 허용).
- P1a: rule semantic 임계가 settings에서 해석되며 생성자 주입이 우선.
- deploy-gate floor: cand_fnr <= max 경계(==통과, +ε 차단) 시맨틱 고정.
"""

from __future__ import annotations

import pytest

from lloydk.modules.m6_evaluation.deploy_gate import evaluate_deploy_gate
from lloydk.services.outbox import (
    InMemoryOutboxStore,
    _validate_target_url,
    publish,
)


# ── SSRF 가드 ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "gopher://x/1", "data:text/plain,hi", "ftp://h/x", "", "://nohost",
])
def test_ssrf_rejects_bad_scheme_or_host(url):
    with pytest.raises(ValueError):
        _validate_target_url(url)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/cb", "http://localhost:8000/cb", "https://[::1]/cb", "http://127.5.5.5/x",
])
def test_ssrf_rejects_loopback(url):
    with pytest.raises(ValueError):
        _validate_target_url(url)


def test_ssrf_allows_normal_https():
    _validate_target_url("https://kl.example.com/webhook")  # 예외 없으면 통과


def test_ssrf_allows_private_ip_by_default(monkeypatch):
    # 폐쇄망: 사설IP 콜백은 정상 — 기본 허용(과차단 방지).
    from lloydk import config as cfg
    monkeypatch.setattr(cfg.settings, "outbox_block_private_ips", False, raising=False)
    _validate_target_url("http://10.0.0.5/cb")
    _validate_target_url("http://192.168.1.10/cb")


def test_ssrf_blocks_private_ip_when_enabled(monkeypatch):
    from lloydk import config as cfg
    monkeypatch.setattr(cfg.settings, "outbox_block_private_ips", True, raising=False)
    with pytest.raises(ValueError):
        _validate_target_url("http://10.0.0.5/cb")


def test_publish_rejects_bad_url_before_enqueue():
    store = InMemoryOutboxStore()
    with pytest.raises(ValueError):
        publish(store, target_url="file:///etc/passwd", payload={"x": 1})


def test_publish_accepts_good_url():
    store = InMemoryOutboxStore()
    msg = publish(store, target_url="https://kl.example.com/cb", payload={"x": 1})
    assert msg.target_url == "https://kl.example.com/cb"


# ── P1a: rule semantic 임계 config화 ──────────────────────────────────────────

def test_settings_semantic_threshold(monkeypatch):
    from lloydk import config as cfg
    from lloydk.modules.m3_labeling.rule_engine import _settings_semantic_threshold
    monkeypatch.setattr(cfg.settings, "rule_semantic_threshold", 0.83, raising=False)
    assert _settings_semantic_threshold() == 0.83


def test_rule_engine_uses_settings_threshold(monkeypatch):
    from lloydk import config as cfg
    from lloydk.modules.m3_labeling.rule_engine import LabelRuleEngine
    monkeypatch.delenv("EMB_SEMANTIC_THRESHOLD", raising=False)
    monkeypatch.setattr(cfg.settings, "rule_semantic_threshold", 0.88, raising=False)
    assert LabelRuleEngine().semantic_threshold == 0.88


def test_rule_engine_ctor_arg_wins_over_settings(monkeypatch):
    from lloydk import config as cfg
    from lloydk.modules.m3_labeling.rule_engine import LabelRuleEngine
    monkeypatch.setattr(cfg.settings, "rule_semantic_threshold", 0.88, raising=False)
    assert LabelRuleEngine(semantic_threshold=0.6).semantic_threshold == 0.6


def test_rule_engine_env_wins_over_settings(monkeypatch):
    from lloydk import config as cfg
    from lloydk.modules.m3_labeling.rule_engine import LabelRuleEngine
    monkeypatch.setenv("EMB_SEMANTIC_THRESHOLD", "0.70")
    monkeypatch.setattr(cfg.settings, "rule_semantic_threshold", 0.88, raising=False)
    assert LabelRuleEngine().semantic_threshold == 0.70


# ── QW: deploy-gate 최초배포 FNR floor 경계(cand_fnr <= max) ───────────────────

def _report(fnr_high=0.05, f1_macro=0.85):
    return {"fnr_high": fnr_high, "f1_macro": f1_macro}


def test_floor_boundary_equal_passes():
    # cand_fnr == floor → <= 이므로 통과.
    dec = evaluate_deploy_gate(_report(fnr_high=0.05), None, first_deploy_fnr_high_max=0.05)
    assert dec.passed is True


def test_floor_boundary_just_above_blocks():
    dec = evaluate_deploy_gate(_report(fnr_high=0.051), None, first_deploy_fnr_high_max=0.05)
    assert dec.passed is False
    assert "first_deploy_fnr_floor" in dec.reason


def test_floor_boundary_just_below_passes():
    dec = evaluate_deploy_gate(_report(fnr_high=0.049), None, first_deploy_fnr_high_max=0.05)
    assert dec.passed is True
