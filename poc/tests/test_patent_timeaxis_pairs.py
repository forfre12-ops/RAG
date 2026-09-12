"""특허 시간축 페어 빌더 — 결정적 로직(날짜·위생·페어 의미론) 회귀 테스트.

라벨 권위=특허법 §64 공개일(비인적 사실)이라는 설계의 코드 계약:
  - 공개 전 시점 라벨은 S2 **floor**(exact 아님), 공개 후는 S3(publicness 경로 전용)
  - 기존 앵커/프록시 app_no·학습 텍스트와 결정적으로 disjoint
  - 최근 공개만 채택(오래된 공개특허는 반사실 시점 주장이 약함)
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path


def _load():
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    script = scripts_dir / "build_patent_timeaxis_pairs.py"
    spec = importlib.util.spec_from_file_location("_build_patent_timeaxis", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load()
AS_OF = dt.date(2026, 7, 3)
LONG_ABSTRACT = "실텍스트 초록 " * 30  # 길이 필터와 무관한 빌더 입력용


def _item(app_no, open_date, title="공정 제어 방법", abstract=None):
    return {
        "app_no": app_no, "title": title,
        "abstract": abstract or (LONG_ABSTRACT + app_no),
        "open_date": open_date, "application_date": "2025.01.02",
    }


def _build(items, **kw):
    defaults = dict(as_of=AS_OF, published_within_days=180,
                    known_app_nos=set(), train_norm_texts=set())
    defaults.update(kw)
    return M.build_pairs(items, **defaults)


def test_date_parsing_formats():
    assert M.parse_kipris_date("2026.05.01") == dt.date(2026, 5, 1)
    assert M.parse_kipris_date("20260501") == dt.date(2026, 5, 1)
    assert M.parse_kipris_date("2026-05-01") == dt.date(2026, 5, 1)
    assert M.parse_kipris_date("") is None
    assert M.parse_kipris_date("2026.13.01") is None


def test_pair_semantics_floor_s2_and_publicness_s3():
    out = _build([_item("10-2025-0000001", "2026.05.01")])
    assert len(out["pairs"]) == 1
    p = out["pairs"][0]
    # 공개 전 = S2 floor(exact 아님), 공개 후 = S3
    assert p["label_pre_publication"] == "S2"
    assert p["pre_publication_label_kind"] == "floor"
    assert p["label_post_publication"] == "S3"
    prepub, published = M.materialize_views(out["pairs"])
    assert prepub[0]["floor_label"] is True
    assert published[0]["requires_publicness_metadata"] is True
    # 두 뷰는 같은 페어를 가리킨다(모순 라벨이 아니라 시점 반전)
    assert prepub[0]["pair_ref"] == published[0]["pair_ref"] == p["doc_id"]


def test_recency_window_enforced():
    out = _build([
        _item("A", "2026.05.01"),   # 63일 전 — 채택
        _item("B", "2025.06.01"),   # 397일 전 — 배제
        _item("C", "2026.08.01"),   # 미래 공개일 — 배제
        _item("D", ""),             # 공개일 없음 — 배제
    ])
    kept = {p["app_no"] for p in out["pairs"]}
    assert kept == {"A"}
    assert out["dropped"]["published_too_long_ago"] == 1
    assert out["dropped"]["open_date_in_future"] == 1
    assert out["dropped"]["no_open_date"] == 1


def test_disjointness_guards_app_no_and_train_text():
    leak_item = _item("LEAK", "2026.05.01")
    leak_norm = M.norm_text(f"{leak_item['title']}\n\n{leak_item['abstract']}")
    out = _build(
        [_item("KNOWN", "2026.05.01"), leak_item, _item("OK", "2026.05.01")],
        known_app_nos={"KNOWN"},
        train_norm_texts={leak_norm},
    )
    kept = {p["app_no"] for p in out["pairs"]}
    assert kept == {"OK"}
    assert out["dropped"]["app_no_in_existing_anchor_or_proxy"] == 1
    assert out["dropped"]["train_text_overlap"] == 1


def test_dedup_by_app_no():
    out = _build([_item("X", "2026.05.01"), _item("X", "2026.05.01")])
    assert len(out["pairs"]) == 1
    assert out["dropped"]["dup_app_no"] == 1


def test_mapping_rule_is_conservative_no_s1_claim():
    # 정책 계약: 출원=공개 전제라 영업비밀 3요소 완전충족 아님 → S1 매핑 금지(보수 floor).
    assert M.MAPPING_RULE["pre_publication"]["label"] == "S2"
    assert M.MAPPING_RULE["pre_publication"]["kind"] == "floor"
    assert M.MAPPING_RULE["post_publication"]["label"] == "S3"
