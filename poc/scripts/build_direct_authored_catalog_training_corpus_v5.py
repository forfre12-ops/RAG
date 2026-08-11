"""Build Proxy training corpus v5 — 길이가 등급을 알려주지 않게 만든다.

v4_3 까지의 결함(2026-08-11 실측). GRADE_EXTENSIONS 는 등급마다 길이가 다른 고정 블록이고
(TS 310 · S2 268 · S1 243 · S3 234자), 모든 레코드에 자기 등급 블록만 붙는다. 그래서 문서
길이가 등급을 그대로 알려줬다:

    v4_3 학습셋   길이-only 1NN 1.000   (무작위 0.250)
    dev200        0.940
    final800 봉인 0.960

본문을 한 글자도 읽지 않고 글자 수만 세면 학습셋 100% · 봉인 평가셋 96% 가 맞는다는 뜻이다.
그 셋으로 잰 정확도는 분류 능력의 증거가 아니다.

v5 가 바꾸는 것은 **길이뿐**이다. 등급별 블록의 내용은 그대로 둔다 — 그건 모델이 배워야 할
진짜 신호다. 대신 등급과 무관한 중립 문단을 덧붙여 등급 간 길이 분포가 겹치게 만든다.
길이 변동의 씨앗은 **레코드 해시**다(순번이 아니라). 순번은 소스 정렬이 등급별로 묶여 있으면
그대로 등급과 상관되기 때문이다.

발행 전에 lloydk.dataset_leakage 게이트를 통과해야 한다 — 통과 못 하면 파일을 쓰지 않는다.

⚠ **현재 이 스크립트는 발행되지 않는다.** 게이트의 두 축 중 길이는 넘지만 tell 이 막는다:

    길이-only   1.000 → 0.479   (한계 0.55 — 통과)
    tell 커버   1.000 → 1.000   (한계 0.10 — 차단)

tell 은 여기서 고칠 수 없다. 소스 코퍼스가 이미 그렇기 때문이다(실측):

    v3_9  길이-only 0.979 · tell 74종 커버 1.000   ← 이 스크립트의 SOURCE
    v3_10 0.985 · 85종 · 1.000
    v4_3  1.000 · 91종 · 1.000

즉 등급 전용 문장은 v4 의 덧붙임이 만든 것이 아니라 **계보 전체가 처음부터 등급별 템플릿**
이라서 생긴다. 등급마다 고정 문단을 붙이는 한, 그 문단은 정의상 등급 전용 문장이 된다
(그 등급 문서 100% 에 있고 다른 등급에는 없다).

고치려면 시나리오 카탈로그에서 **내용 모델을 바꿔 재생성**해야 한다 — 문장을 등급 간에
공유하고, 등급은 어떤 문단이 붙었는지가 아니라 진술된 사실(접근통제 유무·수치·조합)로
드러나게. 그건 이 변환 단계의 문제가 아니라 코퍼스 설계의 문제다.

이 파일을 남겨 두는 이유: 게이트 배선과 길이 보정은 그대로 쓸 수 있고, 무엇보다
**fail-closed 라 깨진 코퍼스를 발행할 수 없다.** 내용 모델이 고쳐지면 SOURCE 만 바꿔 돌리면 된다.

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

from lloydk.dataset_leakage import check_or_raise
from lloydk.proxy_corpus import validate_proxy_record


SOURCE = ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v3_9.jsonl"
SOURCE_MANIFEST = (
    ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v3_9.manifest.json"
)
OUT = ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v5.jsonl"
MANIFEST = ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v5.manifest.json"

GRADE_EXTENSIONS = {
    "TS": (
        "추가 검토 기록",
        (
            "이 문서는 단일 수치보다 조합의 재현 가능성을 중점으로 본다. 공개된 기준만으로는 "
            "같은 결과를 다시 만들 수 없고, 실패 이력과 전환 순서와 예외 복구 기준이 함께 있어야 "
            "현장 판단이 가능하다. 열람 범위는 지정 담당자로 제한하고, 내려받기와 반출은 승인 "
            "기록을 남긴 뒤 종료 시점에 권한을 회수한다."
        ),
        (
            "외부에 알려질 경우 단순한 운영 참고가 아니라 납기, 원가, 복구 시간, 고객 대응 "
            "우위에 직접 영향을 줄 수 있다. 검토자는 수치가 좋아졌다는 사실만 보지 않고, 그 "
            "수치를 만들어낸 내부 조합이 공개 자료로 대체 가능한지 별도로 확인한다."
        ),
    ),
    "S1": (
        "추가 경계 검토",
        (
            "현장 조건과 예외 처리 순서는 외부 공개본에 포함되지 않는다. 다만 관리 체계는 "
            "프로젝트 저장소와 업무 공간 중심으로 운영되어, 수신자별 반출 승인과 정기 권한 "
            "회수까지 완전하게 굳어진 상태는 아니다."
        ),
        (
            "적용 순서와 보정 기준을 활용하면 오류 재발을 줄이고 투입 시간을 줄일 수 있다. "
            "검토자는 해당 정보가 경쟁 우위에 실질적으로 연결되는지 확인하되, 관리 수준이 "
            "가장 엄격한 보호 체계에 도달했는지는 별도 근거로 판단한다."
        ),
    ),
    "S2": (
        "추가 중간 민감도 검토",
        (
            "일부 내부 일정과 담당자별 처리 순서는 공개되지 않았지만, 핵심 원리와 일반 절차는 "
            "공개 지침이나 통상 업무 지식으로 설명 가능하다. 자료 접근은 부서와 협력 범위로 "
            "제한하고 공유 이력은 남기지만, 반출 승인과 보존 규칙은 일부 항목에만 적용된다."
        ),
        (
            "운영 참고 가치는 있으나 특정 기술 우위나 장기 경쟁력을 단독으로 만들 정도의 "
            "고유 조합은 확인되지 않는다. 따라서 자동으로 가장 높은 보호 대상으로 보지 않고, "
            "내부성이 있는 낮은 단계의 검토 대상으로 남긴다."
        ),
    ),
    "S3": (
        "추가 공개성 검토",
        (
            "근거가 되는 기준과 설명은 공개 지침과 이미 배포된 안내자료에서 확인된다. 접근 제한, "
            "수신자 제한, 반출 승인, 내부 전용 저장소 같은 관리 조건을 적용하지 않으며, 필요한 "
            "경우 공개 출처를 그대로 제시할 수 있다."
        ),
        (
            "문서는 일반 절차와 공개 기준을 정리한 수준이다. 비공개 조합이나 독자적인 사업 판단 "
            "기준을 포함하지 않으므로, 운영 교육이나 안내자료로 사용해도 특별한 비밀 관리 근거가 "
            "생기지 않는다."
        ),
    ),
}

FORM_NOTES = (
    "검토자는 사실, 판단, 후속 조치를 분리해서 남긴다.",
    "수치표는 원본을 보존하고 본문에는 판단에 필요한 요약만 둔다.",
    "보류 항목은 원인 미확정, 입력 누락, 영향 범위 불명확으로 나눈다.",
    "전달 범위가 바뀌면 같은 내용이라도 관리 상태를 다시 확인한다.",
    "종료 기준은 개선 수치뿐 아니라 접근 범위 확인과 재검토 일정 등록까지 포함한다.",
)


# 등급과 무관한 중립 문단. 전 등급에 고루 섞이므로 등급 전용 문장(tell)이 되지 않는다.
# 문장은 검토 절차 일반론만 담고 비밀성·가치·관리성 판단 어휘를 쓰지 않는다 —
# 그런 어휘가 들어가면 길이 대신 새로운 지름길을 만들게 된다.
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


def _length_seed(row: dict) -> int:
    """길이 변동의 씨앗 — 레코드 자체에서 뽑는다(등급·순번 무관).

    순번(enumerate)을 쓰면 소스가 등급별로 묶여 있을 때 그대로 등급과 상관된다.
    doc_id 는 등급과 무관하게 배정되므로 해시가 등급 독립이다.
    """
    key = str(row.get("doc_id") or row.get("text", ""))[:256]
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16)


def _neutral_padding(row: dict) -> str:
    """등급 간 길이 분포를 겹치게 만드는 중립 문단.

    등급별 블록 길이차는 최대 76자다(TS 310 - S3 234). 그보다 큰 변동을 등급과 무관하게
    줘야 길이가 등급을 알려주지 못한다 — 문장 0~7개를 레코드 해시로 고른다.
    """
    seed = _length_seed(row)
    # 배수 3 은 실측값이다 — 1배 0.725 / 3배 0.479 / 6배 0.376 (한계 0.55, 무작위 0.25).
    count = seed % (len(NEUTRAL_NOTES) * 3)
    if count == 0:
        return ""
    start = (seed >> 8) % len(NEUTRAL_NOTES)
    picked = [NEUTRAL_NOTES[(start + i) % len(NEUTRAL_NOTES)] for i in range(count)]
    return "\n\n## 검토 메모\n\n" + " ".join(picked)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _extension(grade: str, ordinal: int) -> str:
    title, secrecy_management, value = GRADE_EXTENSIONS[grade]
    note = FORM_NOTES[ordinal % len(FORM_NOTES)]
    before = 18 + (ordinal * 5) % 47
    after = 4 + (ordinal * 3) % 19
    span = 3 + ordinal % 8
    reviewers = 2 + ordinal % 4
    return f"""

## {title}

이번 보강 문단은 문서 양식 변화에 따른 판정 흔들림을 줄이기 위해 추가한 검토 기록이다. 관찰 기간은 {span}일이고, 비교 대상 이벤트는 적용 전 {before}건에서 적용 후 {after}건으로 바뀌었다. 검토 담당자는 {reviewers}명으로 분리되어 같은 기준표를 사용했으며, 결론 문장보다 원자료의 범위와 관리 상태를 먼저 확인한다. {note}

{secrecy_management}

{value}

자료 보존은 작성 차수, 승인 차수, 검토 차수로 나누어 관리한다. 자동 판정이 어려운 경우에는 보류 사유와 필요한 추가 자료를 남기고, 다음 검토에서 같은 문서를 다시 확인할 수 있도록 가족 식별자와 원문 해시를 유지한다.
""".rstrip()


def _rewrite_record(row: dict[str, object], ordinal: int) -> dict[str, object]:
    grade = str(row["label"])
    if grade not in GRADE_EXTENSIONS:
        raise ValueError(f"unknown label: {grade}")
    text = (
        str(row["text"]).rstrip()
        + _extension(grade, ordinal)
        + _neutral_padding(row)          # 등급 무관 길이 변동 — v5 의 핵심
        + "\n"
    )
    new_row = dict(row)
    new_row["doc_id"] = str(new_row["doc_id"]).replace("direct-catalog-v3_9-", "direct-catalog-v5-")
    new_row["document_family_id"] = str(new_row["document_family_id"]).replace(
        "direct-catalog-v3_9-family-", "direct-catalog-v5-family-"
    )
    new_row["authoring_method"] = "codex_direct_authored_catalog_training_v4_style_broadening"
    new_row["generation_lineage"] = [
        *list(new_row.get("generation_lineage") or []),
        "transform:codex:document-style-broadening-v4",
    ]
    new_row["text"] = text
    new_row["requested_profile_min_chars"] = 3000
    new_row["requested_profile_max_chars"] = 3800
    evidence = dict(new_row.get("evidence_card") or {})
    evidence["text_sha256"] = _sha256_text(text.strip())
    new_row["evidence_card"] = evidence
    audit = dict(new_row.get("consensus_evidence") or {})
    audit["gate_status"] = "direct_authored_training_candidate"
    new_row["consensus_evidence"] = audit
    return new_row


def _write_new(path: Path, payload: bytes) -> None:
    if path.exists():
        raise RuntimeError(f"refusing to overwrite immutable output: {path}")
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
    source_rows = _read_jsonl(SOURCE)
    rows = [_rewrite_record(row, idx) for idx, row in enumerate(source_rows)]
    failures = {
        str(row["doc_id"]): list(validate_proxy_record(row, stage="eligible", intended_use="training").errors)
        for row in rows
        if not validate_proxy_record(row, stage="eligible", intended_use="training").ok
    }
    if failures:
        raise RuntimeError(json.dumps(failures, ensure_ascii=False, indent=2))
    # [누출 게이트] 발행 전에 막는다 — 통과 못 하면 파일을 쓰지 않는다.
    # v4_3 이 길이-only 1.000 으로 나가 버린 것은 이 자리에 게이트가 없었기 때문이다.
    # 게이트는 검수 후보 빌더(build_kl_review_pool)에만 배선돼 있었다.
    leakage = check_or_raise(
        ((str(row["label"]), str(row["text"])) for row in rows),
        label="direct-authored-catalog-training-v5",
    )
    print(
        "[leakage] 길이-only {:.3f} · tell {}종(커버 {:.3f}) · 등급문자열 {}건".format(
            leakage["length_only_1nn"], leakage["tell_sentences"],
            leakage["tell_coverage"], leakage["grade_token_exposed"],
        )
    )
    payload = b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        for row in rows
    )
    _write_new(OUT, payload)
    lengths = [len(str(row["text"])) for row in rows]
    manifest = {
        "schema": "direct-authored-catalog-training-v5",
        "source_corpus": str(SOURCE.relative_to(ROOT)),
        "source_records_sha256": _sha256_bytes(SOURCE.read_bytes()),
        "source_manifest_sha256": _sha256_bytes(SOURCE_MANIFEST.read_bytes()),
        "records": len(rows),
        "records_sha256": _sha256_bytes(payload),
        "leakage": leakage,
        "grade_counts": dict(sorted(Counter(str(row["label"]) for row in rows).items())),
        "text_length": {
            "min": min(lengths),
            "max": max(lengths),
            "mean": round(sum(lengths) / len(lengths), 2),
        },
        "training_only": True,
        "no_llm_generation": True,
        "evaluation_boundary": (
            "v2_2 development used only to identify style-generalization failure; "
            "v2_2 final remains unopened"
        ),
        "change_summary": (
            "Appended grade-specific review-style sections to v3.9 while preserving "
            "scenario_id and factor_profile_id catalog quotas; refreshed requested "
            "length range to match the longer documents while preserving the original "
            "shape-to-length-profile mapping for the training-pool contract."
        ),
    }
    _write_new(
        MANIFEST,
        (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    print(json.dumps({"output": str(OUT), **manifest}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
