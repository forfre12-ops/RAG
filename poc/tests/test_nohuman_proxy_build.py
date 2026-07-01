"""[no-human proxy gold] build_nohuman_proxy_gold 감사 후 하드닝 검증.

감사(2026-07-02)에서 발견한 문제 3건의 회귀 가드:
1. 학습셋과 텍스트 동일한 행이 proxy_eval(평가셋)에 섞여 F1을 부풀림(27.5% 리크,
   doc_id는 달라도 텍스트 동일=doc_id 분리 착시) → train_leak로 배제돼야 한다.
2. quarantine 사유(ts_downgrade 등)가 regate_gold에 있어도 proxy_eval에 남던 로직 갭
   → proxy 가지에도 quarantine 필터 적용.
3. proxy_eval이 외부권위(nkt+public) vs 합성/LLM 프록시를 뭉갠 단일 F1 → 분리 집계.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load():
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts_dir) not in sys.path:  # 스크립트가 형제 모듈(analyze_label_noise)을 import
        sys.path.insert(0, str(scripts_dir))
    script = scripts_dir / "build_nohuman_proxy_gold.py"
    spec = importlib.util.spec_from_file_location("_build_nohuman_proxy_gold", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load()


def _row(doc_id, text, label, src, **extra):
    return {"doc_id": doc_id, "text": text, "label": label, "label_source": src, **extra}


def test_train_leak_excluded_from_proxy():
    original = [
        _row("A", "alpha clean secret", "TS", "nkt_designated"),
        _row("C", "gamma leaked into train", "S1", "rule_llm_agreement"),
    ]
    train_texts = {M.norm_text("gamma leaked into train")}
    out = M.build(original, list(original), [], train_texts)
    proxy_ids = {r["doc_id"] for r in out["proxy_eval"]}
    leak_ids = {r["doc_id"] for r in out["train_leak"]}
    assert "C" in leak_ids and "C" not in proxy_ids   # 리크는 eval서 배제
    assert "A" in proxy_ids                            # clean은 남음
    # 자기검증: proxy_eval 잔여 학습중복 0 (부풀림 원천차단)
    assert all(M.norm_text(r.get("text", "")) not in train_texts for r in out["proxy_eval"])


def test_quarantine_applied_to_proxy_branch():
    # 의심 라벨(ts_downgrade)이 regate_gold에 있어도 proxy_eval(평가셋)엔 못 들어간다.
    original = [_row("D", "delta doc", "S1", "public_definitive")]
    regate_review = [{"doc_id": "D", "status": "ts_downgrade_suspect"}]
    out = M.build(original, list(original), regate_review, set())
    proxy_ids = {r["doc_id"] for r in out["proxy_eval"]}
    quar_ids = {r["doc_id"] for r in out["quarantine"]}
    assert "D" in quar_ids and "D" not in proxy_ids


def test_authority_breakdown_splits_external_vs_synthetic():
    original = [
        _row("A", "aa", "TS", "nkt_designated"),
        _row("B", "bb", "S3", "public_definitive"),
        _row("K", "kk", "S2", "koipa_case_based"),
    ]
    out = M.build(original, list(original), [], set())
    ab = out["manifest"]["proxy_eval"]["authority_breakdown"]
    assert ab["external_authority"]["count"] == 2   # nkt + public = 진짜 정답
    assert ab["synthetic_proxy"]["count"] == 1      # koipa = 합성 프록시


def test_main_requires_train(tmp_path, monkeypatch):
    # train 없으면 leakage-free 보장 불가 → 조용히 leaky 셋 만들지 않고 exit 2.
    rg = tmp_path / "regate_gold.jsonl"
    rg.write_text('{"doc_id":"A","text":"a","label":"TS","label_source":"nkt_designated"}\n', encoding="utf-8")
    rv = tmp_path / "regate_review.jsonl"
    rv.write_text("", encoding="utf-8")
    gold = tmp_path / "gold.jsonl"
    gold.write_text('{"doc_id":"A","text":"a","label":"TS","label_source":"nkt_designated"}\n', encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "prog", "--gold", str(gold), "--regate-gold", str(rg), "--regate-review", str(rv),
        "--train", str(tmp_path / "does_not_exist.jsonl"), "--out-dir", str(tmp_path / "out"),
    ])
    assert M.main() == 2
