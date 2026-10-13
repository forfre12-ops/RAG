"""결정 원장이 '줄바꿈처럼 보이는 문자'에 조용히 결정을 잃지 않는다 (2026-09-22).

왜. 원장(candidate_decisions.jsonl)은 한 줄에 JSON 한 개다. 읽는 쪽이 `str.splitlines()` 였고, 쓰는 쪽은
`json.dumps(ensure_ascii=False)` 라 **U+2028·U+2029·U+0085** 가 이스케이프 없이 글자 그대로 들어간다. splitlines() 는 이
문자들도 줄 끝으로 보므로, 검수자가 사유(reason)에 그 문자를 넣으면(웹·워드 복사에서 흔하다) 결정 한 줄이 둘로 쪼개져
둘 다 JSON 이 아니게 되고 — 읽는 쪽이 파싱 실패를 조용히 건너뛰므로 — **결정이 기록됐다고 답하고도 목록·이력·최신 결정
어디에도 안 나온다.** 평가정답 승격(promote)도 같은 읽기를 쓰므로 그 결정이 정답에 반영되지 않는다.

재현(고치기 전): 같은 문서에 결정 4번(평범·U+2028·U+0085) → decide 는 전부 성공, 결정 이력은 2건, 최신 결정은 세 번째 이전 값.
실측(2026-09-22): poc/datasets 의 jsonl 473개(17.4억 자)에는 이 문자가 원문 그대로 든 파일이 0개다 — 기존 데이터는 안전하고,
새 자유 입력(사유)에서만 생기는 잠복 결함이다.

고친 방식: 읽는 쪽은 줄을 '\\n' 으로만 가른다(원장은 우리가 쓰는 줄 단위 파일이고 줄 구분자는 '\\n' 뿐이다). 쓰는 쪽은 그 세 문자를
JSON 이스케이프(\\u2028 …)로 쓴다 — 파싱하면 같은 값이고, 이 문자가 없는 줄은 바이트 그대로다. 다른 곳의 splitlines() 독자도
안전해진다.
"""
from __future__ import annotations

import json

import pytest

from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService

# splitlines() 만 줄바꿈으로 보고 json.dumps(ensure_ascii=False) 는 이스케이프하지 않는 문자들.
SEPARATORS = {"U+2028": "\u2028", "U+2029": "\u2029", "U+0085": "\u0085"}


@pytest.fixture()
def svc(tmp_path):
    service = ProxyGoldCandidateService(root=tmp_path)
    body = ("이 문서는 검수 시험용 본문입니다. " * 30).encode("utf-8")
    cand = service.create_uploaded_candidate(
        filename="sep.txt", content=body, actor_id="admin-x",
        document_origin="uploaded_document", source_reference="", authorization_basis="",
    )
    service._doc = cand["doc_id"]  # 시험 편의
    return service


def _decide(service, reason: str, grade: str = "S2"):
    return service.decide(doc_id=service._doc, action="change", grade=grade, reason=reason, actor_id="rev-a")


@pytest.mark.parametrize("name,ch", SEPARATORS.items())
def test_decision_with_a_line_separator_in_the_reason_is_not_lost(svc, name, ch):
    reason = f"첫 줄{ch}둘째 줄"
    _decide(svc, "평범한 사유", "S3")
    _decide(svc, reason, "S1")
    detail = svc.get_candidate(svc._doc)
    assert detail["latest_decision"]["reason"] == reason, f"{name}: 방금 기록한 결정이 최신 결정으로 안 보인다"
    assert detail["final_grade"] == "S1"
    assert [e["reason"] for e in detail["decision_history"]] == ["평범한 사유", reason]
    events = svc.recent_decisions(limit=50)["events"]
    assert reason in [e.get("reason") for e in events], f"{name}: 결정 원장 최근 기록에 안 나온다"


def test_many_decisions_all_survive_mixed_separators(svc):
    reasons = [f"사유{i}{ch}끝" for i, ch in enumerate(list(SEPARATORS.values()) * 3)]
    for i, r in enumerate(reasons):
        _decide(svc, r, "S1" if i % 2 else "S2")
    history = svc.get_candidate(svc._doc)["decision_history"]
    assert [e["reason"] for e in history] == reasons, "결정 이력이 기록한 결정 수와 다르다"
    assert len(svc.ledger_rows()) == len(reasons)


def test_writer_keeps_the_ledger_one_json_object_per_splitlines_line(svc):
    """다른 곳의 splitlines() 독자도 안전하려면 원장 파일 자체에 그 문자가 원문 그대로 없어야 한다."""
    for ch in SEPARATORS.values():
        _decide(svc, f"가{ch}나")
    raw = svc.ledger_path.read_text(encoding="utf-8")
    assert not any(ch in raw for ch in SEPARATORS.values()), "원장에 줄바꿈처럼 보이는 문자가 원문 그대로 있다"
    lines = raw.splitlines()
    assert len(lines) == len(SEPARATORS)
    assert all(isinstance(json.loads(line), dict) for line in lines)
    # 이스케이프는 값을 바꾸지 않는다 — 파싱하면 원래 문자가 나온다.
    assert [json.loads(line)["reason"] for line in lines] == [f"가{ch}나" for ch in SEPARATORS.values()]


def test_ordinary_lines_are_byte_identical_to_the_old_writer(svc):
    """이 문자가 없는 줄은 종전과 바이트가 같다(형식 무변경)."""
    event = _decide(svc, "평범한 사유", "S3")
    last = svc.ledger_path.read_text(encoding="utf-8").splitlines()[-1]
    row = json.loads(last)
    assert last == json.dumps(row, ensure_ascii=False, sort_keys=True)
    assert row["reason"] == "평범한 사유"
    assert event is not None


def test_reader_still_parses_lines_an_older_writer_left_raw(svc):
    """이미 원장에 원문 그대로 적힌 줄(종전 작성기가 쓴 것)도 읽는다 — 읽는 쪽은 '\\n' 으로만 가른다."""
    _decide(svc, "먼저")
    legacy = json.loads(svc.ledger_path.read_text(encoding="utf-8").splitlines()[-1])
    legacy["event_id"] = "legacy-raw-line"
    legacy["reason"] = "옛 줄\u2028원문 그대로"
    legacy["decided_at"] = "2099-01-01T00:00:00+00:00"
    with svc.ledger_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(legacy, ensure_ascii=False, sort_keys=True) + "\n")   # 종전 작성기와 같은 방식
    reasons = [e["reason"] for e in svc.get_candidate(svc._doc)["decision_history"]]
    assert "옛 줄\u2028원문 그대로" in reasons
    assert len(svc.ledger_rows()) == 2


def test_locked_file_roundtrip_survives_line_separators_in_the_text(tmp_path):
    """평가정답 잠금 파일(locked_*.jsonl)은 문서 **본문**을 담는다 — 실문서 본문에 U+2028 이 있으면 종전에는 읽을 때 json.loads 가
    예외를 내 배포 게이트가 읽는 파일에서 죽었다. 쓰고 읽으면 같은 값이고, 파일에는 그 문자가 원문 그대로 없다."""
    from koipa.services.golden_build_service import _atomic_write_jsonl, _read_jsonl

    rows = [
        {"doc_id": "D1", "text": "첫 문단\u2028둘째 문단", "label": "S2"},
        {"doc_id": "D2", "text": "가\u2029나\u0085다", "label": "S3"},
        {"doc_id": "D3", "text": "평범한 본문", "label": "TS"},
    ]
    path = tmp_path / "locked_x.jsonl"
    _atomic_write_jsonl(path, rows)
    raw = path.read_text(encoding="utf-8")
    assert not any(ch in raw for ch in SEPARATORS.values())
    assert len(raw.splitlines()) == len(rows)
    assert _read_jsonl(path) == rows


def test_locked_file_reader_accepts_a_line_an_older_writer_left_raw(tmp_path):
    from koipa.services.golden_build_service import _read_jsonl

    path = tmp_path / "locked_old.jsonl"
    old = {"doc_id": "D1", "text": "옛\u2028줄"}
    path.write_text(json.dumps(old, ensure_ascii=False) + "\n" + json.dumps({"doc_id": "D2"}) + "\n", encoding="utf-8")
    assert [r["doc_id"] for r in _read_jsonl(path)] == ["D1", "D2"]
    assert _read_jsonl(path)[0]["text"] == "옛\u2028줄"


def test_dumps_line_only_changes_the_three_characters():
    from koipa.jsonl_lines import dumps_line, split_lines

    plain = {"a": "한글 ascii \ \" \t", "b": [1, 2, {"c": None}]}
    assert dumps_line(plain, sort_keys=True) == json.dumps(plain, ensure_ascii=False, sort_keys=True)
    tricky = {"k\u2028ey": "v\u2029al\u0085ue"}
    line = dumps_line(tricky)
    assert not any(ch in line for ch in SEPARATORS.values())
    assert json.loads(line) == tricky
    assert split_lines("a\nb\n") == ["a", "b", ""]
    assert split_lines("a\u2028b\nc") == ["a\u2028b", "c"]
