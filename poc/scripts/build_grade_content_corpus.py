#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""등급이 **본문 내용으로 정해지는** 합성 코퍼스를 만든다.

■ 왜 새로 만드는가 (2026-09-12 실측)

종전 생성기는 등급을 정해 놓고 그 등급 전용 문장을 넣었다. 그 문장을 겹치게 고치자
(`aa8c8222`) **본문에 등급을 정하는 것이 아무것도 남지 않았다.** 제거 실험이 그것을 보였다
(글자 n-gram 로지스틱 · 기준선 25.0%):

    그대로 57.2%  →  문서종류 문자열 지움 38.8%  →  '확인 문제' 문구도 지움 **32.2%**

같은 자료로 kf-deberta 를 5에폭 학습하면 **손실이 ln(4)=1.386 에서 안 움직이고** 전부 한
등급으로 찍는다(f1-macro 0.11 · chunk_expand 켜도 같음). 배울 것이 없기 때문이다.
감리가 본 35.71% 도, 우리가 낸 52.5% 도 그래서 **표면 단서 일치율**이었다.

■ 무엇을 바꾸는가 — 라벨이 내용을 따라가게 한다

    종전:  등급을 고른다 → 그 등급의 문장을 넣는다        (내용 = f(등급))
    여기:  S·V·M 을 고른다 → 그 수준의 **내용**을 쓴다 → 등급은 규칙이 계산한다
                                                      (등급 = f(내용))

등급은 `m3_labeling.rule_engine.grade_from_svm` 이 정한다. 생성기는 등급을 직접 쓰지 않는다.

    S 비공지성   0 공개 자료와 같은 범위 · 1 내부 사정이나 범주 수준 · 2 재현 가능한 구체 수치·조건
    V 경제가치   0 경쟁상 의미 없음 · 1 내부 운영 효율 · 2 협상·원가·경쟁 위치에 직접 영향
    M 관리성     0 표시·접근제한 없음 · 1 표시만 · 2 표시+권한+배포이력

■ 지름길을 **구조로** 막는다

    문서종류·주제   등급과 **무관하게 균등 추출**한다 → 문서종류만으로는 기준선이 나온다
    길이           수준마다 문단 수를 맞춘다 → 등급별 길이 차이가 남지 않는다
    문구           수준마다 서로 다른 표현을 여러 벌 두고 돌려 쓴다(한 문장이 수준을 알리지 않게)
    등급 낱말      본문에 등급 코드·등급 이름을 **쓰지 않는다**([[grade-name-words-are-shortcuts-2026-09-11]])

⛔ 그래도 **사람 확정 정답은 아니다.** 규칙이 매긴 라벨이다. 실문서 일반화 근거로 쓰지 말 것.
   쓸 수 있는 것은 "내용을 읽어야 풀리는 과제에서 모델이 어떻게 하는가"이다.

사용:
    python scripts/build_grade_content_corpus.py --out datasets/grade_content_v1 --n 2400
    python scripts/build_grade_content_corpus.py --dry --n 400
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import io
import json
import random
import sys
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_POC / "src"))

from koipa.modules.m3_labeling.rule_engine import grade_from_svm  # noqa: E402

GRADES = ("TS", "S1", "S2", "S3")

# 등급과 무관하게 뽑는다 — 이 둘이 등급을 알려주면 안 된다.
DOC_TYPES = (
    "검토회의 기록", "변경영향 분석", "의사결정 메모", "현장점검 결과", "기술검증 노트",
    "협의 경과서", "사후분석 보고", "운영 전환 기록", "점검 결과 정리", "업무 인계 기록",
)
THEMES = (
    ("열관리 소재", "배합·열전도"), ("조립 공정", "치구·순서"), ("센서 모듈", "보정·오차"),
    ("품질 대응", "불량·재발"), ("협력사 전환", "단가·공급"), ("접근 통제", "권한·이력"),
    ("운영 자동화", "처리 기준"), ("고객 지원", "문의 유형"), ("사업 제안", "원가·수익"),
    ("해외 진입", "규제·일정"), ("설비 정비", "주기·징후"), ("데이터 품질", "오류·정제"),
)

# ── S 비공지성 ────────────────────────────────────────────────────────────────
S_BLOCKS = {
    0: (
        "본문에 적은 절차는 회사 홈페이지 안내문과 같은 범위이며, 별도 내부 조건을 담지 않았다.",
        "여기 정리한 내용은 이미 대외에 공지한 자료에서 그대로 옮긴 것이고 추가한 조건이 없다.",
        "설명은 공개 배포본과 동일한 수준으로 맞추었고, 내부에서만 아는 사항은 제외하였다.",
    ),
    1: (
        "내부에서 쓰는 구분과 담당 범위를 적었으나, 구체적인 설정값이나 조건은 적지 않았다.",
        "어떤 항목을 점검하는지는 범주로만 남기고, 실제 기준치와 판정 경계는 옮기지 않았다.",
        "업무 흐름과 책임 구분은 기록하되 수치·임계값은 별도 관리대장에 두고 여기서는 뺐다.",
    ),
    2: (
        "{p1} {v1}{u1}, {p2} {v2}{u2} 조건에서 {p3} 를 {v3}{u3} 로 두었을 때 재현된다. 순서를 바꾸면 결과가 달라진다.",
        "적용 조건은 {p1} {v1}{u1} · {p2} {v2}{u2} · {p3} {v3}{u3} 이며, 세 값을 함께 맞추어야 같은 결과가 나온다.",
        "{p1} 를 {v1}{u1} 로, {p2} 를 {v2}{u2} 로 고정하고 {p3} 를 {v3}{u3} 까지 올리는 순서로 진행하였다.",
    ),
}
# 값과 단위를 따로 뽑으면 "인가 전압 391㎛" 같은 말이 나온다 — 짝으로 둔다.
PARAMS = (
    ("예열 시간", "초", 20, 600), ("체류 온도", "℃", 80, 320), ("인가 전압", "V", 3, 48),
    ("도포 두께", "㎛", 5, 120), ("경화 시간", "분", 5, 180), ("검사 주기", "회", 1, 12),
    ("허용 편차", "%", 1, 15), ("가압 하중", "N", 10, 400), ("세정 농도", "ppm", 50, 900),
    ("냉각 속도", "℃/분", 2, 40),
)

# ── V 경제적 유용성 ──────────────────────────────────────────────────────────
V_BLOCKS = {
    0: (
        "이 내용이 알려지더라도 거래 조건이나 경쟁 관계에 미치는 영향은 확인되지 않는다.",
        "외부에 공유되어도 회사의 협상 위치나 비용 구조가 달라질 여지는 없다고 보았다.",
        "판단에 쓰인 정보는 일반적인 안내 수준이어서 경쟁상 이해관계와 연결되지 않는다.",
    ),
    1: (
        "내부 처리 시간과 재작업 횟수가 달라지므로 운영 효율에는 영향이 있다.",
        "담당자 배분과 점검 주기가 바뀌면 처리 지연이 늘 수 있어 운영 측면의 영향은 있다.",
        "업무량 배분에 영향을 주지만 대외 거래 조건까지 이어지는 사안은 아니다.",
    ),
    2: (
        "여기 적힌 원가 가정과 할인 여지가 알려지면 상대방이 우리 하한선을 역산할 수 있다.",
        "공급 단가의 구성과 양보 가능 구간이 드러나므로 협상에서 곧바로 불리해진다.",
        "경쟁사가 이 조건을 알면 입찰 가격을 우리 기준 바로 아래로 맞출 수 있다.",
    ),
}

# ── M 비밀관리성 ─────────────────────────────────────────────────────────────
M_BLOCKS = {
    0: (
        "문서에 보안 표시가 없고 열람 권한도 따로 걸려 있지 않으며 배포 기록도 남아 있지 않다.",
        "표시·권한·배포 이력 가운데 어느 것도 확인되지 않아 관리 상태를 증명할 수 없다.",
        "공유 폴더에 그대로 올려 두었고 누가 열람했는지 남는 기록이 없다.",
    ),
    1: (
        "머리말에 취급 주의 표시는 있으나 열람 권한 제한과 배포 이력은 확인되지 않는다.",
        "표지에는 대외주의가 찍혀 있지만 접근 권한 목록이 따로 관리되고 있지 않다.",
        "표시만 되어 있고 누구에게 전달되었는지는 별도로 기록하지 않았다.",
    ),
    2: (
        "머리말·꼬리말에 취급 표시가 있고 열람 권한이 지정 부서로 제한되며 전달 이력이 남아 있다.",
        "보안 표시, 권한 목록, 전달 기록 세 가지가 모두 확인되어 관리 상태를 증명할 수 있다.",
        "지정된 담당자만 열람하도록 권한이 걸려 있고 반출 시 승인 기록이 함께 남는다.",
    ),
}

# 길이를 맞추기 위한 중립 문단 — 수준·등급과 무관하다.
# ⚠ 수를 넉넉히 둔다. 적으면 길이를 맞추려고 같은 문장을 여러 번 넣게 되고,
#   그 되풀이 자체가 새 단서가 된다(2026-09-12 에 문구 단서로 한 번 데였다).
FILLER = (
    "작성자는 확인한 사실과 아직 확인하지 못한 사항을 문장 단위로 나누어 적었다.",
    "후속 담당자가 같은 기준으로 다시 볼 수 있도록 확인 시점과 출처를 함께 남겼다.",
    "구두로 들은 설명은 근거로 쓰지 않고 기록이 있는 항목만 판단에 사용하였다.",
    "예외로 처리한 항목은 되돌릴 조건과 재점검 시점을 덧붙여 두었다.",
    "표와 본문이 다른 경우에는 원시 기록을 기준으로 삼았다.",
    "이번 검토에서 다루지 않은 범위는 다음 회차 확인 대상으로 옮겼다.",
    "참석자 의견이 갈린 항목은 한쪽으로 정리하지 않고 양쪽을 그대로 남겼다.",
    "같은 사안을 다룬 지난 기록과 번호를 맞추어 이어 볼 수 있게 하였다.",
    "담당 부서가 바뀐 항목은 인계 시점과 인계받은 담당을 함께 적었다.",
    "일정이 바뀐 항목은 변경 전후를 나란히 두어 비교할 수 있게 하였다.",
    "검토 과정에서 새로 생긴 질문은 별도 목록으로 모아 두었다.",
    "기록에 쓰인 용어는 사내 표준 용어를 따랐고 다른 표기는 괄호로 병기하였다.",
    "판단을 바꿀 만한 새 자료가 들어오면 이 기록을 먼저 다시 본다.",
    "회차별로 확인한 항목 수와 남은 항목 수를 마지막에 정리해 두었다.",
)
# 길이가 등급을 알리지 않도록 이 글자 수까지 중립 문단으로 채운다.
TARGET_CHARS = 760


def _concrete(rng: random.Random, template: str) -> str:
    if "{p1}" not in template:
        return template
    ps = rng.sample(PARAMS, 3)
    fields = {}
    for index, (name, unit, low, high) in enumerate(ps, start=1):
        fields[f"p{index}"] = name
        fields[f"v{index}"] = rng.randrange(low, high)
        fields[f"u{index}"] = unit
    return template.format(**fields)


def make_document(rng: random.Random, s: int, v: int, m: int) -> tuple[str, dict]:
    doc_type = rng.choice(DOC_TYPES)          # 등급과 무관
    theme, facet = rng.choice(THEMES)         # 등급과 무관
    body = [f"# {theme} {doc_type}",
            f"이 기록은 {theme} 관련 {facet} 사항을 정리한 것이다."]

    # 수준마다 두 문장씩 — 문단 수를 같게 해 길이가 등급을 알리지 않게 한다.
    for blocks, level in ((S_BLOCKS, s), (V_BLOCKS, v), (M_BLOCKS, m)):
        picks = rng.sample(blocks[level], 2)
        body.extend(_concrete(rng, p) for p in picks)

    # 수준마다 문장 길이가 달라 등급별 길이 차이가 남는다(첫 판 8%). 중립 문단으로 채워 맞춘다.
    filler = list(FILLER)
    rng.shuffle(filler)
    for sentence in filler:
        if sum(len(p) for p in body) >= TARGET_CHARS:
            break
        body.append(sentence)

    tail = body[2:]
    rng.shuffle(tail)
    body = body[:2] + tail
    grade = grade_from_svm(s, v, m)
    return "\n\n".join(body), {
        "label": grade, "s": s, "v": v, "m": m,
        "document_type": doc_type, "theme": theme,
    }


def build(n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    # 네 등급이 고르게 나오도록 S·V·M 조합을 등급별로 모아 두고 균등하게 뽑는다.
    by_grade: dict[str, list[tuple[int, int, int]]] = collections.defaultdict(list)
    for s in (0, 1, 2):
        for v in (0, 1, 2):
            for m in (0, 1, 2):
                by_grade[grade_from_svm(s, v, m)].append((s, v, m))

    rows: list[dict] = []
    per_grade = n // len(GRADES)
    for grade in GRADES:
        combos = by_grade[grade]
        for i in range(per_grade):
            s, v, m = combos[i % len(combos)]
            text, meta = make_document(rng, s, v, m)
            assert meta["label"] == grade
            meta.update({
                "doc_id": f"GC-{grade}-{i + 1:04d}", "text": text,
                "origin": "synthetic", "label_source": "rule_from_content_svm",
                "human_confirmed": False,
            })
            rows.append(meta)
    rng.shuffle(rows)
    return rows


def audit(rows: list[dict]) -> dict:
    """지름길 세 축을 잰다 — 만들었다고 끝이 아니라 재서 확인한다."""
    n = len(rows)
    counts = collections.Counter(r["label"] for r in rows)
    base = max(counts.values()) / n

    def axis(key: str) -> float:
        table: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        for r in rows:
            table[r[key]][r["label"]] += 1
        return sum(c.most_common(1)[0][1] for c in table.values()) / n * 100

    lengths = collections.defaultdict(list)
    for r in rows:
        lengths[r["label"]].append(len(r["text"]))
    med = {g: sorted(v)[len(v) // 2] for g, v in lengths.items()}

    # 길이 구간만 보고 최빈등급 찍기 — 중앙값 배수만으로는 분포 겹침을 못 본다.
    ordered = sorted((len(r["text"]), r["label"]) for r in rows)
    bins, hit = 10, 0
    size = len(ordered) / bins
    for i in range(bins):
        chunk = ordered[int(i * size):int((i + 1) * size)]
        if chunk:
            hit += collections.Counter(g for _c, g in chunk).most_common(1)[0][1]

    return {
        "n": n, "by_grade": dict(counts), "baseline_rate": round(base * 100, 1),
        "document_type_rate": round(axis("document_type"), 1),
        "theme_rate": round(axis("theme"), 1),
        "median_chars": med,
        "median_ratio": round(max(med.values()) / min(med.values()), 2),
        "length_only_rate": round(hit / n * 100, 1),
    }


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="내용으로 등급이 정해지는 합성 코퍼스")
    ap.add_argument("--out", default="datasets/grade_content_v1")
    ap.add_argument("--n", type=int, default=2400)
    ap.add_argument("--val", type=int, default=400)
    ap.add_argument("--seed", type=int, default=20260912)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)

    rows = build(a.n, a.seed)
    rep = audit(rows)
    print("%d건 · 등급별 %s · 기준선 %.1f%%" % (rep["n"], rep["by_grade"], rep["baseline_rate"]))
    print("  문서종류만으로 %.1f%% · 주제만으로 %.1f%%  (기준선에 가까워야 한다)"
          % (rep["document_type_rate"], rep["theme_rate"]))
    print("  길이만으로 %.1f%% · 중앙값 %s · 배수 %s"
          % (rep["length_only_rate"], rep["median_chars"], rep["median_ratio"]))
    print("\n예시(S=2·V=2·M=2 인 문서 첫 4줄):")
    for r in rows:
        if (r["s"], r["v"], r["m"]) == (2, 2, 2):
            print("   " + "\n   ".join(r["text"].splitlines()[:5]))
            break

    if a.dry:
        print("\n  --dry — 아무것도 쓰지 않았다.")
        return 0

    out_dir = _POC / a.out
    out_dir.mkdir(parents=True, exist_ok=True)
    val, train = rows[:a.val], rows[a.val:]
    digests = {}
    for name, part in (("train", train), ("val", val), ("test", val)):
        body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in part)
        (out_dir / f"{name}.jsonl").write_text(body, encoding="utf-8", newline="\n")
        digests[name] = hashlib.sha256(body.encode("utf-8")).hexdigest()
        print("  %-6s %4d건" % (name, len(part)))
    (out_dir / "manifest.json").write_text(json.dumps({
        "built_at": "2026-09-12", "n": a.n, "val": a.val, "seed": a.seed,
        "label_source": "rule_from_content_svm (grade_from_svm)",
        "human_confirmed_count": 0,
        "use": "내용을 읽어야 풀리는 과제. 사람 확정 정답 아님 · 실문서 일반화 근거 아님.",
        "sha256": digests, "shortcut_audit": rep,
    }, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print("  기록: %s" % out_dir.relative_to(_POC))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
