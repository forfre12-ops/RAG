"""Build Proxy training corpus v6 — 등급을 결론 문장이 아니라 **요인 조합**으로 드러낸다.

v5 까지 막혀 있던 지점(실측 2026-08-12, v3_9 2,700행 전수):

    길이-only 1NN     0.979   (무작위 0.250)
    tell 문장         74종 · 커버 1.000
    evidence 인용     8,100건 중 7,150건(88.3%)이 그 tell 문장 안
                      nonpublicity 2,700/2,700 · competitive_value 2,475/2,700
                      access_controls 1,975/2,700

마지막 줄이 핵심이다. 등급 판단의 **"근거 인용"이 곧 등급 전용 결론 문장**이었다 —
근거가 본문 사실을 가리키는 게 아니라 정답을 적어 둔 문장을 되가리키고 있었다.
그래서 두 게이트가 동시에 만족될 수 없었다:

    proxy_corpus.validate_proxy_record   인용이 본문에 글자 그대로 있을 것
    lloydk.dataset_leakage.check_or_raise 바로 그 문장이 없을 것

v5 는 결론 문장을 지워서 풀려 했지만, 본문 중간을 지우면 evidence span 오프셋이 밀려
2,700행 전건이 무효가 됐다(0 → 2,700 실패). 변환 단계에서는 풀 수 없는 문제였다.

── v6 이 바꾸는 것 ─────────────────────────────────────────────────────────

**문장을 등급으로 고르던 것을 요인 수준으로 고른다.** 등급마다 고정 문단을 붙이면 그
문단은 정의상 등급 전용 문장이 된다(그 등급 100%, 다른 등급 0%). 요인 수준으로 고르면
같은 수준을 가진 다른 등급 문서에 같은 문장이 나온다.

소스 데이터가 이미 이 구조를 지원한다(expected_factor_scores 실측):

    management=0 → S1·S3        secrecy=0 → S3            value=0 → S3
    management=1 → S2·S3·TS     secrecy=1 → S2·S3         value=1 → S2·S3
    management=2 → S2·S3·TS     secrecy=2 → S1·S2·S3·TS   value=2 → S1·S2·S3·TS

즉 secrecy=2 문장은 TS·S1·S2·S3 에 모두 나온다. 단일 문장으로는 등급을 알 수 없고
**조합**을 읽어야 등급이 나온다 — 그게 분류기가 실제로 배워야 할 것이다.
그리고 evidence 인용이 그 사실 문장을 가리키므로 두 게이트가 같은 것을 요구하게 된다.

바꾸는 범위는 **요인 문단 2개뿐**이다. evidence span 이 어느 블록에 있는지로 위치를
찾는다(실측: 전 문서가 연속한 두 블록 — nonpublicity+competitive_value 가 한 블록,
access_controls 가 그다음 블록). 나머지 서술·시나리오·수치는 그대로 둔다.

오프셋은 **문단 교체 후 새 본문에서 다시 찾아** 기록한다. v5 가 놓친 것이 이것이다.

길이 보정은 v5 의 방식을 그대로 쓴다(등급 무관 중립 문단, 씨앗은 레코드 해시).

발행 전에 두 게이트를 모두 통과해야 한다 — 못 하면 파일을 쓰지 않는다.

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

from lloydk.dataset_leakage import check_or_raise, grade_tells  # noqa: E402
from lloydk.proxy_corpus import validate_proxy_record  # noqa: E402
from v6_fact_pools import FACTOR_SCORE_KEY, POOLS  # noqa: E402


SOURCE = ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v3_9.jsonl"
SOURCE_MANIFEST = (
    ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v3_9.manifest.json"
)
OUT = ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v6.jsonl"
MANIFEST = ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v6.manifest.json"

# 등급과 무관한 중립 문단(v5 에서 가져옴). 전 등급에 고루 섞이므로 tell 이 되지 않는다.
# 판단 어휘(비밀성·가치·관리성)를 쓰지 않는다 — 쓰면 길이 대신 새 지름길을 만든다.
NEUTRAL_NOTES = (
    "검토 기록은 작성 시점과 확인 시점을 나누어 남기고, 두 시점 사이에 바뀐 항목만 따로 표시한다.",
    "표와 본문이 어긋나면 표를 원본으로 보고 본문 요약을 다시 쓴다. 반대 방향으로 고치지 않는다.",
    "판단이 갈린 항목은 결론 대신 갈린 이유를 남겨, 다음 검토자가 같은 자리에서 다시 시작하게 한다.",
    "인용한 자료는 파일명이 아니라 식별자와 판번호로 적는다. 파일명은 옮겨지면 추적이 끊긴다.",
    "수치가 바뀌면 계산식과 입력 범위를 함께 적는다. 결과만 갱신하면 재현이 되지 않는다.",
    "확인하지 못한 항목은 공란으로 두지 않고 확인 불가 사유를 적는다. 공란은 확인했다는 뜻으로 읽힌다.",
    "후속 조치는 담당과 기한을 함께 적고, 기한이 지나면 상태를 재검토 대상으로 되돌린다.",
    "용어가 문서마다 다르면 처음 쓰는 자리에서 한 번 정의하고 이후에는 같은 표기를 유지한다.",
)


class V6BuildError(RuntimeError):
    """발행 전에 걸러야 하는 구조적 문제."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _seed(row: dict, salt: str = "") -> int:
    """레코드 해시 — 등급·순번과 무관해야 한다.

    enumerate 순번을 쓰면 소스가 등급별로 묶여 있을 때 그대로 등급과 상관된다.
    """
    key = f"{row.get('doc_id') or ''}|{salt}"
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:12], 16)


def _blocks_with_offsets(text: str) -> list[tuple[int, int, str]]:
    out, pos = [], 0
    for block in text.split("\n\n"):
        out.append((pos, pos + len(block), block))
        pos += len(block) + 2
    return out


def _factor_block_index(blocks, start: int, end: int) -> int | None:
    for index, (block_start, block_end, _text) in enumerate(blocks):
        if block_start <= start and end <= block_end:
            return index
    return None


# 등급을 말로 적어 두던 섹션들. 실측(v6 1차 시도): 요인 문단 2개만 갈아 끼웠더니 인용의
# tell 포함률은 88.3% → 0.0% 로 떨어졌지만 tell 문장 25종이 남았고, **전부** 아래 네 섹션에
# 있었다(내 사실 문장 풀 유래 0종). 등급별로 통째 쓰인 판정문이라 요인 수준으로 다시 쓴다.
#   ## 등급 판단 근거 3,375  ## 경계 판단 메모 500  ## 비공개 운영 근거 500  ## 관리 필요성 500
#
# 2차 시도에서 다시 드러난 것: **섹션 제목 자체가 등급을 말한다.** 판정 섹션을 다시 쓰자
# 남은 tell 44종이 아래 제목들 아래로 모였고, 제목만 봐도 등급이 보인다 —
#   TS  핵심 보호 근거 · 고위험 비공개 근거 · 중대 영향 근거
#   S1  제한 공유 판단 · 상향·하향 배제 근거
#   S2  일반 내부자료 판단 · 중간 민감도 검토
#   S3  공개·저민감 판단 · 낮은 민감도 근거
# 본문만 고치고 제목을 두면 모델은 제목을 외운다. 제목도 등급 무관 이름으로 바꾼다.
_VERDICT_SECTIONS = (
    "등급 판단 근거", "경계 판단 메모", "비공개 운영 근거", "관리 필요성",
    "핵심 보호 근거", "고위험 비공개 근거", "중대 영향 근거",
    "제한 공유 판단", "상향·하향 배제 근거",
    "일반 내부자료 판단", "중간 민감도 검토",
    "공개·저민감 판단", "낮은 민감도 근거",
)
# 판정 섹션에 붙일 등급 무관 제목. 레코드 해시로 고르므로 등급과 상관되지 않는다.
_NEUTRAL_SECTION_TITLES = (
    "판단 근거 정리",
    "요인별 확인 결과",
    "검토 근거 기록",
    "확인 사항 정리",
    "근거 항목 대조",
    "판단 요소 기록",
)
# 인용을 실을 섹션. 없으면 남아 있는 판정 섹션 중 첫 번째를 쓴다.
_EVIDENCE_SECTION = "등급 판단 근거"
_FACTOR_ORDER = ("nonpublicity", "competitive_value", "access_controls")


def _pick(row: dict, factor: str, level: int, offset: int = 0) -> tuple[str, str]:
    """요인 수준으로 문장을 고른다 — 등급은 보지 않는다.

    ``offset`` 은 같은 사실을 다른 섹션에서 **다른 표현으로** 다시 진술할 때 쓴다.
    한 문서 안에 같은 문자열이 두 번 나오면 evidence span 이 모호해지고, 무엇보다
    같은 문자열의 등장 빈도가 올라가 tell 판정(등급 문서의 5% 이상)에 가까워진다.
    """
    pool = POOLS[factor].get(level)
    if not pool:
        raise V6BuildError(f"{row['doc_id']}: {factor} 수준 {level} 에 문장 풀이 없다")
    return pool[(_seed(row, factor) + offset) % len(pool)]


def _section_map(blocks: list[str]) -> tuple[dict[str, list[int]], dict[str, int]]:
    """'## 제목' → (본문 블록 번호들, 제목 블록 번호).

    같은 제목이 두 번 나오면 합친다 — 판정 섹션은 어차피 한 덩어리로 다시 쓴다.
    """
    sections: dict[str, list[int]] = {}
    headers: dict[str, int] = {}
    current: str | None = None
    for index, block in enumerate(blocks):
        stripped = block.strip()
        if stripped.startswith("#"):
            current = stripped.lstrip("#").strip()
            sections.setdefault(current, [])
            headers.setdefault(current, index)
            continue
        if current is not None:
            sections[current].append(index)
    return sections, headers


def _rewrite_text(row: dict) -> tuple[str, dict[str, str]]:
    """등급 판정 섹션을 요인 수준 사실로 다시 쓰고, 인용할 조각을 함께 돌려준다."""
    scores = row.get("expected_factor_scores") or {}
    blocks = [block for _s, _e, block in _blocks_with_offsets(str(row["text"]))]
    sections, headers = _section_map(blocks)

    present = [name for name in _VERDICT_SECTIONS if name in sections]
    if not present:
        raise V6BuildError(f"{row['doc_id']}: 등급 판정 섹션이 하나도 없다")
    host = _EVIDENCE_SECTION if _EVIDENCE_SECTION in present else present[0]
    ordered = [host] + [name for name in present if name != host]

    levels = {f: int(scores[FACTOR_SCORE_KEY[f]]) for f in _FACTOR_ORDER}
    quotes: dict[str, str] = {}
    title_seed = _seed(row, "title")

    for order, name in enumerate(ordered):
        sentences = []
        for factor in _FACTOR_ORDER:
            quote, tail = _pick(row, factor, levels[factor], offset=order)
            if order == 0:
                quotes[factor] = quote
            sentences.append(quote + tail)
        body_indices = sections[name]
        if body_indices:
            _replace_section(blocks, body_indices, " ".join(sentences))
        else:
            # 제목만 있고 본문이 없던 섹션 — 제목 블록 뒤에 본문을 붙일 자리가 없으므로
            # 제목 블록 자체를 제목+본문으로 만든다(블록 구조는 그대로 유지).
            blocks[headers[name]] = (
                f"## {_NEUTRAL_SECTION_TITLES[(title_seed + order) % len(_NEUTRAL_SECTION_TITLES)]}"
                "\n\n" + " ".join(sentences)
            )
            continue
        # 제목도 등급을 말한다("핵심 보호 근거"=TS). 등급 무관 이름으로 바꾼다 —
        # 씨앗이 레코드 해시라 제목 선택이 등급과 상관되지 않는다.
        blocks[headers[name]] = (
            f"## {_NEUTRAL_SECTION_TITLES[(title_seed + order) % len(_NEUTRAL_SECTION_TITLES)]}"
        )

    return "\n\n".join(block for block in blocks if block != ""), quotes


def _replace_section(blocks: list[str], indices: list[int], body: str) -> None:
    """섹션 본문을 한 문단으로 대체한다. 남는 블록은 빈 문자열로 두지 않고 접는다."""
    if not indices:
        return
    blocks[indices[0]] = body
    for index in indices[1:]:
        blocks[index] = ""


def _neutral_padding(row: dict) -> str:
    """등급 간 길이 분포를 겹치게 만드는 중립 문단(v5 와 동일한 방식·배수)."""
    seed = _seed(row, "length")
    count = seed % (len(NEUTRAL_NOTES) * 3)
    if count == 0:
        return ""
    start = (seed >> 8) % len(NEUTRAL_NOTES)
    picked = [NEUTRAL_NOTES[(start + i) % len(NEUTRAL_NOTES)] for i in range(count)]
    return "\n\n## 검토 메모\n\n" + " ".join(picked)


def _rebuild_evidence(text: str, quotes: dict[str, str], doc_id: str) -> dict:
    """새 본문에서 인용 위치를 **다시 찾아** span 을 만든다.

    v5 는 이 단계가 없어 본문을 고치고도 옛 오프셋을 그대로 뒀고, 그래서 전건이
    evidence_exact_match 로 죽었다. 오프셋은 본문이 바뀌면 반드시 다시 잡아야 한다.
    """
    factors: dict[str, dict] = {}
    for name, quote in quotes.items():
        start = text.find(quote)
        if start < 0:
            raise V6BuildError(f"{doc_id}: {name} 인용을 새 본문에서 찾지 못했다")
        if text.find(quote, start + 1) >= 0:
            raise V6BuildError(f"{doc_id}: {name} 인용이 본문에 두 번 나온다 — span 이 모호하다")
        end = start + len(quote)
        if text[start:end] != quote:
            raise V6BuildError(f"{doc_id}: {name} 인용 대조 실패")
        factors[name] = {
            "basis": "text",
            "spans": [
                {
                    "start": start,
                    "end": end,
                    "quote": quote,
                    "quote_sha256": _sha256_text(quote),
                }
            ],
        }
    return {"factors": factors, "schema": "proxy-evidence-v1", "text_sha256": _sha256_text(text.strip())}


def _rewrite_record(row: dict) -> dict:
    body, quotes = _rewrite_text(row)
    text = body.rstrip() + _neutral_padding(row) + "\n"
    new_row = dict(row)
    new_row["doc_id"] = str(row["doc_id"]).replace("direct-catalog-v3_9-", "direct-catalog-v6-")
    new_row["document_family_id"] = str(row["document_family_id"]).replace(
        "direct-catalog-v3_9-family-", "direct-catalog-v6-family-"
    )
    new_row["authoring_method"] = "codex_direct_authored_catalog_training_v6_factor_grounded"
    new_row["generation_lineage"] = [
        *list(row.get("generation_lineage") or []),
        "transform:codex:factor-grounded-evidence-v6",
    ]
    new_row["text"] = text
    new_row["requested_profile_min_chars"] = 2400
    new_row["requested_profile_max_chars"] = 4200
    new_row["evidence_card"] = _rebuild_evidence(text, quotes, str(new_row["doc_id"]))
    audit = dict(row.get("consensus_evidence") or {})
    audit["gate_status"] = "direct_authored_training_candidate"
    new_row["consensus_evidence"] = audit
    return new_row


def _write_new(path: Path, payload: bytes) -> None:
    if path.exists():
        raise V6BuildError(f"refusing to overwrite immutable output: {path}")
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


def _evidence_in_tells(rows: list[dict]) -> tuple[int, int]:
    """인용이 등급 전용 문장 안에 있는 비율 — v6 가 고치려는 바로 그 수치."""
    tells = set(grade_tells([(str(r["label"]), str(r["text"])) for r in rows]))
    total = inside = 0
    for row in rows:
        for factor in ((row.get("evidence_card") or {}).get("factors") or {}).values():
            for span in (factor or {}).get("spans") or []:
                total += 1
                quote = " ".join(str(span["quote"]).split()).strip()
                if any(quote in tell for tell in tells):
                    inside += 1
    return inside, total


def main() -> int:
    source_rows = _read_jsonl(SOURCE)
    rows = [_rewrite_record(row) for row in source_rows]

    failures = {
        str(row["doc_id"]): list(
            validate_proxy_record(row, stage="eligible", intended_use="training").errors
        )
        for row in rows
        if not validate_proxy_record(row, stage="eligible", intended_use="training").ok
    }
    if failures:
        sample = dict(list(failures.items())[:5])
        raise V6BuildError(
            f"레코드 검증 실패 {len(failures)}/{len(rows)}건.\n"
            + json.dumps(sample, ensure_ascii=False, indent=2)
        )

    inside, quoted = _evidence_in_tells(rows)
    print(f"[evidence] 인용이 등급 전용 문장 안: {inside}/{quoted} = {inside / max(quoted, 1):.1%}")

    leakage = check_or_raise(
        ((str(row["label"]), str(row["text"])) for row in rows),
        label="direct-authored-catalog-training-v6",
    )
    print(
        "[leakage] 길이-only {:.3f} · tell {}종(커버 {:.3f}) · 등급문자열 {}건".format(
            leakage["length_only_1nn"],
            leakage["tell_sentences"],
            leakage["tell_coverage"],
            leakage["grade_token_exposed"],
        )
    )

    payload = b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        for row in rows
    )
    _write_new(OUT, payload)
    lengths = [len(str(row["text"])) for row in rows]
    manifest = {
        "schema": "direct-authored-catalog-training-v6",
        "source_corpus": str(SOURCE.relative_to(ROOT)),
        "source_records_sha256": _sha256_bytes(SOURCE.read_bytes()),
        "source_manifest_sha256": _sha256_bytes(SOURCE_MANIFEST.read_bytes()),
        "records": len(rows),
        "records_sha256": _sha256_bytes(payload),
        "leakage": leakage,
        "evidence_inside_grade_tells": {"inside": inside, "total": quoted},
        "grade_counts": dict(sorted(Counter(str(row["label"]) for row in rows).items())),
        "text_length": {
            "min": min(lengths),
            "max": max(lengths),
            "mean": round(sum(lengths) / len(lengths), 2),
        },
        "training_only": True,
        "no_llm_generation": True,
        "change_summary": (
            "Rewrote every grade-keyed verdict section — body and heading — from "
            "expected_factor_scores instead of from the grade, and re-anchored every "
            "evidence span in the rewritten text. Sentences are now selected by factor "
            "level, so the same statement appears across the grades that share that "
            "level (secrecy=2 spans TS/S1/S2/S3); grade is expressed only by the "
            "combination. Section headings were grade-announcing too ('핵심 보호 근거' "
            "= TS) and are replaced with record-hash-selected neutral titles. Scenario "
            "ids, factor profiles, expected_factor_scores, and all narrative outside "
            "the verdict sections are unchanged."
        ),
        "known_limits": {
            "tell_coverage_headroom": (
                "tell 커버 0.090 vs 한계 0.10 — 통과지만 여유가 크지 않다. 남은 15종은 "
                "판정 섹션 밖 서술이며, 소스 코퍼스를 재생성하지 않는 한 변환 단계에서 "
                "더 줄이기 어렵다."
            ),
            "s3_only_factor_levels": (
                "secrecy=0·value=0 은 이 카탈로그에서 S3 에만 나온다. 템플릿 인공물이 "
                "아니라 진짜 판별 사실이므로 남기되, 문자열 암기를 막으려고 변형을 "
                "각각 16종 두었다(각 변형이 S3 문서의 5% 미만)."
            ),
            "not_a_generalization_claim": (
                "이 코퍼스로 잰 수치는 합성 내부 일관성이다. 실문서 일반화 근거가 "
                "아니다(자체 실측: 합성학습→실문서 F1 0.26)."
            ),
        },
    }
    _write_new(
        MANIFEST,
        (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    print(json.dumps({"output": str(OUT), **manifest}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
