"""human_review 검수 결과가 기존 pseudo 라벨 gold 문서를 SKIP하지 않고
human_review로 '승격'하는지 검증한다.

이 동작이 깨지면(예전 append-only + SKIP), 큐가 기존 gold 문서로 구성되는
탓에 human_review 레코드가 0건만 생겨 운영 readiness 게이트가 영원히
BLOCKED 상태로 남는다.
"""
import json

from scripts import import_review_corrections as iric


def _seed_gold(path, records):
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def _correction(doc_id, model_label, human_label, text="full text"):
    return {
        "doc_id": doc_id,
        "model_label": model_label,
        "human_label": human_label,
        "review_decision": "corrected" if model_label != human_label else "correct",
        "reviewer_id": "reviewer-1",
        "text": text,
        "domain": "",
        "document_type": "",
        "reason_code": "trade_secret",
    }


def test_existing_pseudo_doc_is_promoted_not_skipped(tmp_path, monkeypatch):
    gold = tmp_path / "classification_gold.jsonl"
    _seed_gold(
        gold,
        [
            {"doc_id": "d1", "text": "t", "label": "S3", "label_source": "llm_judge_primary"},
            {"doc_id": "d2", "text": "t", "label": "S2", "label_source": "llm_judge_consensus"},
        ],
    )
    monkeypatch.setattr(iric, "GOLD_REAL_PATH", gold)

    # human says d1 is actually S2 (model had said S3 -> high-risk underclass)
    n = iric.merge_into_gold([_correction("d1", "S3", "S2")], dry_run=False)
    assert n == 1

    rows = [json.loads(l) for l in gold.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 2  # promoted in place, not appended
    by_id = {r["doc_id"]: r for r in rows}
    assert by_id["d1"]["label_source"] == "human_review"
    assert by_id["d1"]["label"] == "S2"
    assert by_id["d1"]["model_label"] == "S3"  # needed for the agreement gate
    assert by_id["d2"]["label_source"] == "llm_judge_consensus"  # untouched


def test_unknown_doc_is_appended_as_human_review(tmp_path, monkeypatch):
    gold = tmp_path / "classification_gold.jsonl"
    _seed_gold(gold, [{"doc_id": "d1", "text": "t", "label": "S3", "label_source": "llm_judge_primary"}])
    monkeypatch.setattr(iric, "GOLD_REAL_PATH", gold)

    n = iric.merge_into_gold([_correction("new1", "S2", "S1")], dry_run=False)
    assert n == 1

    rows = [json.loads(l) for l in gold.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_id = {r["doc_id"]: r for r in rows}
    assert len(rows) == 2
    assert by_id["new1"]["label_source"] == "human_review"
    assert by_id["new1"]["label"] == "S1"


def test_machine_reviewer_id_rejected():
    # human_review는 사람 사인오프만 인정 — ai_assist 등 머신 식별자는 거부.
    # (build_review_assist 사전작성본이 위장 서명으로 승격되는 구멍 차단)
    for rid in ("ai_assist", "AI_ASSIST", "llm_judge_anthropic", "claude-review",
                "auto", "bot_x", "", "human"):
        assert iric._is_machine_reviewer(rid), f"머신 id 미차단: {rid!r}"
    for rid in ("r1", "reviewer-1", "kim@corp.com", "aiden_kim", "홍길동"):
        assert not iric._is_machine_reviewer(rid), f"사람 id 오차단: {rid!r}"


def test_validate_record_rejects_ai_assist():
    row = {"doc_id": "d1", "model_label": "S1", "human_label": "S3",
           "reviewer_id": "ai_assist", "text": "t"}
    rec, errs = iric.validate_record(row, 1)
    assert rec is None
    assert any("reviewer_id" in e for e in errs)


def test_validate_record_accepts_real_reviewer():
    row = {"doc_id": "d1", "model_label": "S1", "human_label": "S3",
           "reviewer_id": "r1", "text": "t"}
    rec, errs = iric.validate_record(row, 1)
    assert rec is not None and not errs
    assert rec["reviewer_id"] == "r1"


# ── [NEW-M3] write_to_db tenant 잔재 제거 회귀 ────────────────────────────────


class _FakeSession:
    def __init__(self):
        self.added = []
        self.executed = []
        self.committed = False

    def add(self, o):
        self.added.append(o)

    def flush(self):
        pass

    def execute(self, stmt):
        self.executed.append(stmt)

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


class _FakeRepo:
    def __init__(self, db):
        pass

    def level_id_by_code(self, code):
        return 1

    def document_exists(self, doc_uuid):
        return False


def test_write_to_db_dry_run_signature_has_no_tenant():
    # 시그니처에서 tenant_id 제거 — 인자 없이 호출 가능(dry-run).
    out = iric.write_to_db([], dry_run=True)
    assert out.get("dry_run") is True


def test_write_to_db_constructs_document_without_tenant(monkeypatch):
    # 테넌트 제거 후 Document(tenant_id=...) 잔재로 write_to_db 가 런타임에 깨졌었다(TypeError).
    # 페이크 세션으로 DB 없이 Document 생성 경로를 태워 더는 깨지지 않음을 확인.
    import lloydk.db as db_mod
    import lloydk.repositories.classify_repo as repo_mod

    fake = _FakeSession()
    monkeypatch.setattr(db_mod, "SessionLocal", lambda: fake)
    monkeypatch.setattr(repo_mod, "ClassifyRepo", _FakeRepo)

    rec = _correction("a1b2c3d4" * 5, "S3", "TS", text="영업비밀 본문")  # 40자 sha1 hex
    out = iric.write_to_db([rec], dry_run=False)

    assert out["written"] == 1
    assert fake.committed is True
    assert len(fake.added) == 1                       # Document 1건 생성됨
    assert not hasattr(fake.added[0], "tenant_id")    # tenant 컬럼 부재(제거 정합)


# ── [G2] --as-candidate: 고객사 반입이 locked_gold_eval(평가정답)을 오염하지 않음 ──

from lloydk import golden_tiers  # noqa: E402


def test_merge_gold_default_is_locked_human_review(tmp_path, monkeypatch):
    # 기본(지재원 골든셋 검수) = human_review → TIER_LOCKED(평가 정답). 기존 동작 보존.
    gold = tmp_path / "classification_gold.jsonl"
    _seed_gold(gold, [])
    monkeypatch.setattr(iric, "GOLD_REAL_PATH", gold)
    iric.merge_into_gold([_correction("c1", "S3", "TS")], dry_run=False)
    rec = [json.loads(l) for l in gold.read_text(encoding="utf-8").splitlines() if l.strip()][0]
    assert rec["label_source"] == "human_review"
    assert golden_tiers.tier_of(rec) == golden_tiers.TIER_LOCKED


def test_merge_gold_as_candidate_is_not_locked(tmp_path, monkeypatch):
    # 고객사 반입(as_candidate) = customer_review/gold_candidate → TIER_CANDIDATE(학습용, 평가정답 아님).
    gold = tmp_path / "classification_gold.jsonl"
    _seed_gold(gold, [])
    monkeypatch.setattr(iric, "GOLD_REAL_PATH", gold)
    iric.merge_into_gold([_correction("c1", "S3", "TS")], dry_run=False, as_candidate=True)
    rec = [json.loads(l) for l in gold.read_text(encoding="utf-8").splitlines() if l.strip()][0]
    assert rec["label_source"] != "human_review"          # 고객 라벨을 human_review로 굳히지 않음
    assert rec["review_status"] == "gold_candidate"
    assert golden_tiers.tier_of(rec) == golden_tiers.TIER_CANDIDATE
    assert golden_tiers.tier_of(rec) != golden_tiers.TIER_LOCKED


def test_to_gold_record_candidate_excluded_from_eval_answers():
    r = _correction("c1", "S3", "TS")
    locked = iric._to_gold_record(r)
    cand = iric._to_gold_record(r, as_candidate=True)
    assert golden_tiers.tier_of(locked) == golden_tiers.TIER_LOCKED
    assert golden_tiers.tier_of(cand) == golden_tiers.TIER_CANDIDATE
    # eval 정답 집합엔 locked만 — candidate(고객 반입)는 평가 정답이 되지 않는다.
    locked_eval, _ = golden_tiers.eval_records([locked, cand], allow_floor_fallback=False)
    assert locked in locked_eval and cand not in locked_eval


def test_write_to_db_as_candidate_runs(monkeypatch):
    import lloydk.db as db_mod
    import lloydk.repositories.classify_repo as repo_mod

    fake = _FakeSession()
    monkeypatch.setattr(db_mod, "SessionLocal", lambda: fake)
    monkeypatch.setattr(repo_mod, "ClassifyRepo", _FakeRepo)

    rec = _correction("b1c2d3e4" * 5, "S3", "S1", text="영업비밀 본문")
    out = iric.write_to_db([rec], dry_run=False, as_candidate=True)
    assert out["written"] == 1
    assert fake.committed is True
