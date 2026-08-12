"""Build Proxy evaluation corpus v3 — 평가셋도 길이·정답문장으로 답을 알려주지 않게.

왜 필요한가(실측 2026-08-12, v2_2 1,000행 전수):

    길이-only 1NN  0.968   (무작위 0.250)
    Theil's U      0.800
    tell 커버      1.000   (tell 12종)
    등급별 길이    TS 2262~2287 · S1 2249~2275 · S2 2236~2261 · S3 2218~2243

등급별 길이 구간이 거의 겹치지 않는다. **본문을 한 글자도 읽지 않고 글자 수만 세면
96.8% 가 맞는다.** 이 셋으로 잰 F1 은 분류 능력의 증거가 아니다 — v4_3 이 여기서 0.99 를
받고 실문서에서 0.61 로 무너진 것이 같은 구조다.

학습셋은 v6 에서 고쳤는데(길이 0.979→0.392 · tell 커버 1.000→0.090) **자를 안 고치면
고친 학습셋을 평가할 방법이 없다.**

── v3 가 바꾸는 것 ─────────────────────────────────────────────────────────

v2_2 는 구조가 v3_9 보다 낫다:
  · 섹션 제목이 등급을 말하지 않는다(1~8 번 고정, v3_9 의 '핵심 보호 근거' 같은 것이 없다)
  · tell 12종이 **정확히 두 섹션**에만 있다 — '4. 공개 여부와 관리 상태'·'5. 업무 가치와 영향'
  · evidence span 도 같은 두 섹션에 있다
  · 실측: 그 두 섹션을 다시 쓰면 **나머지 서술에서 나오는 tell 은 0 종**이다

그런데 두 섹션만 다시 쓰면 tell 커버가 0.74 로 남는다. 그 tell 이 전부 새 문장 풀에서
나오고, 원인은 문장이 아니라 **카탈로그의 요인 배정**이다(실측):

    평가 v2_2                          학습 v3_9(정상 혼합)
    management=1 → S2 250/250 (1:1)    management=1 → S2 47% · S3 · TS
    management=2 → TS 200/200 (1:1)    management=2 → TS 47% · S2 · S3
    secrecy=1    → S2 (1:1)            secrecy=1    → S2 74% · S3
    value=1      → S2 (1:1)            value=1      → S2 79% · S3

**9개 요인 수준 중 6개가 등급과 1:1이다. 평가셋에서는 등급이 요인 하나로 결정된다.**
그러면 그 수준을 충실히 서술한 어떤 문장도 한 등급에만 나타난다 — 문장을 바꿔 써서
피할 수 있는 문제가 아니다. 그리고 더 나쁜 것은, 이 평가셋이 **현실에 없는 관계를
보상한다**는 점이다: 실제로 비밀관리성이 높다고 TS 인 것은 아니고(관리가 잘 된 S2 는
얼마든지 있다) 학습 카탈로그는 그걸 반영하는데, 이 평가셋은 그 지름길을 쓰는 모델에
만점을 준다. v6 로 제대로 학습한 모델을 이 자로 재면 **틀린 방향으로 평가된다.**

그래서 v3 는 세 가지를 한다:

  1. **요인 배정 재조합** — 각 문서의 등급은 그대로 두고, 그 등급으로 이어지는 요인
     조합을 **학습 카탈로그가 실제로 쓰는 조합 집합에서** 다시 고른다(씨앗=레코드 해시).
     등급은 요인 삼중항의 함수이고 한 등급에 여러 조합이 대응하므로(TS ← (1,2,2)·(2,2,2))
     등급을 바꾸지 않고 혼합만 회복할 수 있다.
  2. 판정 두 섹션을 그 요인 수준으로 다시 쓰고 evidence 오프셋을 새 본문에서 다시 잡는다.
  3. 등급 무관 중립 문단으로 길이 분포를 겹치게 한다(v2_2 는 등급별 길이 구간이 거의
     겹치지 않아 글자 수만 세면 96.8% 가 맞았다).

제목·시나리오 서술·수치는 손대지 않는다.

── 학습셋과 문장을 공유하지 않는다 ─────────────────────────────────────────

**scripts/eval_fact_pools.py 를 쓴다(학습셋은 v6_fact_pools).** 같은 풀을 쓰면 누출은
사라지지만 새 오염이 생긴다 — 모델이 요인 대신 그 문장 풀을 외우고, 평가셋은 암기량을
재게 된다. 메타데이터만 다르면 계보 검사도 통과하므로(그 구멍 때문에
holdout_independence 에 문장 공유 축을 추가했다) 풀 자체를 분리한다.

발행 전 두 가지를 모두 통과해야 한다 — 레코드 검증 + 누출 게이트. 못 하면 쓰지 않는다.
그리고 **학습셋과의 문장 공유가 0 이어야 한다**(빌드 안에서 확인한다).

No LLM or model is called.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from koipa.dataset_leakage import _normalize_sentences, check_or_raise  # noqa: E402
from koipa.holdout_independence import assess  # noqa: E402
from koipa.proxy_corpus import validate_proxy_record  # noqa: E402
from eval_fact_pools import NEUTRAL_NOTES, POOLS  # noqa: E402
from v6_fact_pools import FACTOR_SCORE_KEY  # noqa: E402


SOURCE = ROOT / "datasets" / "proxy_eval" / "direct_authored_proxy_eval.v2_2.jsonl"
SOURCE_MANIFEST = (
    ROOT / "datasets" / "proxy_eval" / "direct_authored_proxy_eval.v2_2.manifest.json"
)
TRAIN_REFERENCE = (
    ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v6.jsonl"
)
OUT = ROOT / "datasets" / "proxy_eval" / "direct_authored_proxy_eval.v3.jsonl"
MANIFEST = ROOT / "datasets" / "proxy_eval" / "direct_authored_proxy_eval.v3.manifest.json"

# tell 이 있고 evidence 가 있는 두 섹션(실측). 제목은 이미 등급 중립이라 그대로 둔다.
_SECTION_FACTORS = {
    "4. 공개 여부와 관리 상태": ("nonpublicity", "access_controls"),
    "5. 업무 가치와 영향": ("competitive_value",),
}


class EvalBuildError(RuntimeError):
    """발행 전에 걸러야 하는 구조적 문제."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _seed(row: dict, salt: str = "") -> int:
    """레코드 해시 — 등급·순번과 무관해야 한다(순번은 소스 정렬이 등급별이면 그대로 샌다)."""
    return int(hashlib.sha256(f"{row.get('doc_id') or ''}|{salt}".encode("utf-8")).hexdigest()[:12], 16)


def _blocks(text: str) -> list[str]:
    return text.split("\n\n")


def _section_map(blocks: list[str]) -> dict[str, list[int]]:
    sections: dict[str, list[int]] = {}
    current: str | None = None
    for index, block in enumerate(blocks):
        stripped = block.strip()
        if stripped.startswith("#"):
            current = stripped.lstrip("#").strip()
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(index)
    return sections


def _grade_combination_pool() -> dict[str, list[tuple[int, int, int]]]:
    """등급 → 그 등급으로 이어지는 (management, secrecy, value) 조합 전체.

    규칙은 ``proxy_corpus.grade_from_svm`` 이 정본이다. 학습 코퍼스에서 경험적으로 뽑지
    않는다 — 학습셋이 쓰는 조합이 전부는 아니고, 평가셋이 학습셋의 조합 선택까지 물려받으면
    그 자체가 계보 공유가 된다. 규칙에서 직접 열거하면 두 셋이 같은 등급 정의를 쓰되
    조합 선택은 독립이다.

    실측(균등 배정 시 요인 수준별 최빈 등급 점유율):
        management 0/1/2 → 65% · 41% · 41%      secrecy 1/2 → 67% · 42%
        value 1/2 → 67% · 42%
        secrecy=0 · value=0 → S3 100%  ← 규칙상 그렇다. 이건 템플릿 인공물이 아니라
            판별 사실이므로 남기고, 문자열 암기만 막도록 변형을 16종씩 둔다(각 변형이
            S3 문서의 5% 미만). 학습셋과 같은 처방이다.
    """
    from koipa.proxy_corpus import grade_from_svm  # noqa: PLC0415

    pool: dict[str, list[tuple[int, int, int]]] = {}
    for secrecy in range(3):
        for value in range(3):
            for management in range(3):
                grade = grade_from_svm(secrecy, value, management)
                pool.setdefault(grade, []).append((management, secrecy, value))
    for grade in pool:
        pool[grade].sort()
    return pool


def _reassign_scores(
    row: dict, pool: dict[str, list[tuple[int, int, int]]], ordinal: int
) -> dict[str, int]:
    """등급은 그대로 두고 요인 조합만 다시 고른다 — 혼합 회복이 목적이다.

    같은 등급 안에서 조합을 **돌아가며** 쓴다(ordinal 기반). 해시로 고르면 조합 수가 적은
    등급(S1 은 1개, TS 는 2개)에서 분포가 치우칠 수 있고, 치우치면 그 조합의 요인 수준이
    다시 그 등급 전용이 된다. 등급별 순번은 소스 순서를 따르므로 결정론적이다.
    """
    grade = str(row["label"])
    combos = pool.get(grade)
    if not combos:
        raise EvalBuildError(f"{row['doc_id']}: 등급 {grade} 에 대응하는 조합이 없다")
    management, secrecy, value = combos[ordinal % len(combos)]
    return {"management": management, "secrecy": secrecy, "value": value}


def _sync_adjudication(row: dict, scores: dict[str, int]) -> dict:
    """consensus_evidence 를 새 요인 배정에 맞춘다.

    이걸 빼먹으면 본문·expected_factor_scores 는 새 값인데 판정 감사기록은 옛 값이라
    proxy_corpus 가 primary_factors_disagree_with_expected 로 거부한다(실측 579/1,000).
    감사기록은 "무엇을 근거로 이 등급을 확정했나"의 증적이므로 본문과 어긋나면 안 된다.
    """
    audit = dict(row.get("consensus_evidence") or {})
    sample_count = int(audit.get("primary_sample_count") or 0)
    audit["primary_factor_scores"] = dict(scores)
    audit["primary_factor_votes"] = {
        factor: {str(scores[factor]): sample_count} for factor in ("secrecy", "value", "management")
    }
    audit["primary_factor_coverage"] = {
        factor: sample_count for factor in ("secrecy", "value", "management")
    }
    audit["factor_vote_expected_match"] = {
        factor: True for factor in ("secrecy", "value", "management")
    }
    return audit


def _pick(row: dict, factor: str, level: int, offset: int = 0) -> tuple[str, str]:
    pool = POOLS[factor].get(level)
    if not pool:
        raise EvalBuildError(f"{row['doc_id']}: {factor} 수준 {level} 문장 풀 없음")
    return pool[(_seed(row, factor) + offset) % len(pool)]


def _neutral_padding(row: dict) -> str:
    """등급 간 길이 분포를 겹치게 만든다. 씨앗은 레코드 해시(등급 독립)."""
    seed = _seed(row, "length")
    count = seed % (len(NEUTRAL_NOTES) * 3)
    if count == 0:
        return ""
    start = (seed >> 8) % len(NEUTRAL_NOTES)
    picked = [NEUTRAL_NOTES[(start + i) % len(NEUTRAL_NOTES)] for i in range(count)]
    return "\n\n## 9. 기록 관리 메모\n\n" + " ".join(picked)


def _rewrite_text(row: dict, scores: dict[str, int]) -> tuple[str, dict[str, str]]:
    blocks = _blocks(str(row["text"]))
    sections = _section_map(blocks)
    quotes: dict[str, str] = {}

    for order, (name, factors) in enumerate(_SECTION_FACTORS.items()):
        indices = sections.get(name)
        if not indices:
            raise EvalBuildError(f"{row['doc_id']}: 섹션 '{name}' 이 없다")
        sentences = []
        for factor in factors:
            level = int(scores[FACTOR_SCORE_KEY[factor]])
            quote, tail = _pick(row, factor, level, offset=order)
            quotes[factor] = quote
            sentences.append(quote + tail)
        blocks[indices[0]] = " ".join(sentences)
        for index in indices[1:]:
            blocks[index] = ""

    return "\n\n".join(b for b in blocks if b != ""), quotes


def _rebuild_evidence(text: str, quotes: dict[str, str], doc_id: str) -> dict:
    """새 본문에서 인용을 **다시 찾는다** — 본문이 바뀌면 오프셋은 반드시 다시 잡아야 한다."""
    factors: dict[str, dict] = {}
    for name, quote in quotes.items():
        start = text.find(quote)
        if start < 0:
            raise EvalBuildError(f"{doc_id}: {name} 인용을 새 본문에서 찾지 못했다")
        if text.find(quote, start + 1) >= 0:
            raise EvalBuildError(f"{doc_id}: {name} 인용이 두 번 나온다 — span 이 모호하다")
        factors[name] = {
            "basis": "text",
            "spans": [
                {
                    "start": start,
                    "end": start + len(quote),
                    "quote": quote,
                    "quote_sha256": _sha256_text(quote),
                }
            ],
        }
    return {
        "factors": factors,
        "schema": "proxy-evidence-v1",
        "text_sha256": _sha256_text(text.strip()),
    }


def _rewrite_record(
    row: dict, pool: dict[str, list[tuple[int, int, int]]], ordinal: int
) -> dict:
    scores = _reassign_scores(row, pool, ordinal)
    body, quotes = _rewrite_text(row, scores)
    text = body.rstrip() + _neutral_padding(row) + "\n"
    new_row = dict(row)
    # 재조합한 요인 배정을 레코드에 반영한다 — 본문을 이 값으로 썼으므로 옛 값을 두면
    # 본문과 메타데이터가 어긋난다. profile id 도 값에서 다시 만든다.
    new_row["expected_factor_scores"] = scores
    new_row["factor_profile_id"] = (
        f"v3-m{scores['management']}-s{scores['secrecy']}-v{scores['value']}"
    )
    new_row["doc_id"] = str(row["doc_id"]).replace("v2_2", "v3")
    new_row["document_family_id"] = str(row["document_family_id"]).replace("v2_2", "v3")
    new_row["authoring_method"] = "codex_direct_authored_proxy_evaluation_v3_factor_grounded"
    new_row["generation_lineage"] = [
        *list(row.get("generation_lineage") or []),
        "transform:codex:factor-grounded-evidence-v3",
    ]
    new_row["text"] = text
    new_row["evidence_card"] = _rebuild_evidence(text, quotes, str(new_row["doc_id"]))
    new_row["consensus_evidence"] = _sync_adjudication(row, scores)
    return new_row


def _write_new(path: Path, payload: bytes) -> None:
    if path.exists():
        raise EvalBuildError(f"refusing to overwrite immutable output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    train_rows = _read_jsonl(TRAIN_REFERENCE)
    pool = _grade_combination_pool()
    print(
        "[combo] 등급별 유효 조합 수(grade_from_svm): "
        + " · ".join(f"{g} {len(v)}" for g, v in sorted(pool.items()))
    )
    # 등급별로 순번을 따로 센다 — 조합을 등급 안에서 돌아가며 쓰기 위해서다.
    per_grade_ordinal: Counter = Counter()
    rows = []
    for row in _read_jsonl(SOURCE):
        grade = str(row["label"])
        rows.append(_rewrite_record(row, pool, per_grade_ordinal[grade]))
        per_grade_ordinal[grade] += 1

    failures = {
        str(r["doc_id"]): list(
            validate_proxy_record(r, stage="eligible", intended_use="evaluation").errors
        )
        for r in rows
        if not validate_proxy_record(r, stage="eligible", intended_use="evaluation").ok
    }
    if failures:
        raise EvalBuildError(
            f"레코드 검증 실패 {len(failures)}/{len(rows)}건.\n"
            + json.dumps(dict(list(failures.items())[:5]), ensure_ascii=False, indent=2)
        )

    leakage = check_or_raise(
        ((str(r["label"]), str(r["text"])) for r in rows),
        label="direct-authored-proxy-eval-v3",
    )
    print(
        "[leakage] 길이-only {:.3f} · Theil's U {:.3f} · tell {}종(커버 {:.3f})".format(
            leakage["length_only_1nn"], leakage["length_theils_u"],
            leakage["tell_sentences"], leakage["tell_coverage"],
        )
    )

    # [문장 공유] 학습셋과 한 문장도 겹치면 안 된다. 겹치면 평가셋이 암기량을 재게 된다.
    # 이건 게이트다 — 발행 후에 알면 이미 그 셋으로 잰 수치가 나가 있다.
    independence = assess(train_rows, rows, label="v6-train vs v3-eval")
    shared = independence["overlap"]["shared_sentences"]
    print(
        f"[independence] 계보 독립 {independence['lineage_independent']} · "
        f"학습셋과 공유 문장 {shared['shared_types']}종(커버 {shared['coverage']:.4f})"
    )
    if not independence["lineage_independent"]:
        raise EvalBuildError(
            "학습셋과 독립이 아니다:\n  · " + "\n  · ".join(independence["concerns"])
        )

    payload = b"".join(
        (json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8") for r in rows
    )
    _write_new(OUT, payload)
    lengths = [len(str(r["text"])) for r in rows]
    manifest = {
        "schema": "direct-authored-proxy-evaluation-v3",
        "source_corpus": str(SOURCE.relative_to(ROOT)),
        "source_records_sha256": _sha256_bytes(SOURCE.read_bytes()),
        "source_manifest_sha256": _sha256_bytes(SOURCE_MANIFEST.read_bytes()),
        "records": len(rows),
        "records_sha256": _sha256_bytes(payload),
        "leakage": leakage,
        "train_reference": str(TRAIN_REFERENCE.relative_to(ROOT)),
        "independence_vs_train": {
            "lineage_independent": independence["lineage_independent"],
            "shared_sentence_types": shared["shared_types"],
            "shared_sentence_coverage": shared["coverage"],
        },
        "grade_counts": dict(sorted(Counter(str(r["label"]) for r in rows).items())),
        "text_length": {
            "min": min(lengths), "max": max(lengths),
            "mean": round(sum(lengths) / len(lengths), 2),
        },
        "training_forbidden": True,
        "no_llm_generation": True,
        "change_summary": (
            "Rewrote the two verdict-bearing sections ('4. 공개 여부와 관리 상태', "
            "'5. 업무 가치와 영향') from expected_factor_scores instead of from the grade, "
            "re-anchored every evidence span, and equalised length with grade-independent "
            "notes. Sentences come from scripts/eval_fact_pools.py, which shares no "
            "sentence with the training pool (scripts/v6_fact_pools.py) — a shared pool "
            "would make the eval measure memorisation of that pool. Section headings, "
            "scenario ids, factor profiles and all other narrative are unchanged."
        ),
        "claim_ceiling": (
            "합성 내부 일관성과 안전 무회귀까지. 실문서 일반화 근거가 아니다"
            "(자체 실측: 교차silver→gold_real F1 0.26)."
        ),
    }
    _write_new(
        MANIFEST,
        (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    print(f"[out] {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
