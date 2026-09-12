#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LLM 으로 **등급이 내용에 담긴** 문서를 쓴다 — 표현을 매번 다르게.

■ 왜 필요한가

`build_grade_content_corpus.py`(규칙 생성기)는 라벨이 내용을 따라가게 만들었지만,
수준마다 문장이 3벌뿐이라 **외우면 풀린다**(로지스틱 100%). 그래서 그것은 대조군이지
벤치마크가 아니다. 벤치마크가 되려면 같은 수준을 **매번 다른 말로** 써야 한다.

■ 어떻게

S·V·M 수준을 **조건으로 주고** 본문을 쓰게 한다. 등급은 여전히 `grade_from_svm` 이 계산한다 —
모델에게 등급을 알려주지 않고, 등급 낱말을 쓰지 못하게 막는다.

    S 비공지성  0 공개 범위 · 1 범주 수준 · 2 재현 가능한 구체 수치·조건
    V 경제가치  0 경쟁상 무의미 · 1 내부 운영 효율 · 2 협상·원가·경쟁 위치에 직접 영향
    M 관리성    0 표시·권한·이력 없음 · 1 표시만 · 2 표시+권한+이력

■ 받아쓰기 전에 거르는 것 (검사에 걸리면 버리고 다시 쓴다)

    등급 누설    '특급기밀·1급비밀·2급대외비·3급공개·TS·S1·S2·S3' 및 '영업비밀' 등급 표현
    길이         지정 구간 밖이면 버린다(길이가 등급을 알리지 않게)
    문서종류·주제  등급과 무관하게 균등 추출해서 **우리가 정해 준다**

■ 만든 뒤 반드시 재는 것

문서종류·주제·길이만으로 등급을 맞히는 비율. 기준선(25%)에서 크게 벗어나면 그 판은 못 쓴다.
⚠ LLM 이 수준마다 같은 상투구를 쓰면 새 지름길이 생긴다 — `measure_grade_phrase_leak.py` 로
   되풀이 문구도 함께 볼 것. 만들었다고 끝이 아니다(2026-09-12 에 두 번 데였다).

⛔ 사람 확정 정답이 아니다(규칙이 매긴 라벨) · 실문서 일반화 근거가 아니다.

사용:
    python scripts/build_grade_content_llm.py --provider ollama --model qwen3:14b --n 40 --dry
    python scripts/build_grade_content_llm.py --provider ollama --model qwen3:14b --n 800 \
        --out datasets/grade_content_llm_v1 --workers 4
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as futures
import hashlib
import io
import json
import random
import re
import sys
import time
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_POC / "src"))
sys.path.insert(0, str(_POC / "scripts"))

from koipa.modules.m3_labeling.rule_engine import grade_from_svm  # noqa: E402

from build_grade_content_corpus import (  # noqa: E402
    DOC_TYPES,
    THEMES,
    audit,
    print_audit,
)

GRADES = ("TS", "S1", "S2", "S3")

LEVEL_SPEC = {
    "S": {
        0: "이 문서가 담은 내용은 이미 대외에 공개된 자료와 같은 범위다. 내부에서만 아는 조건이 없다.",
        1: "내부 업무 구분과 담당 범위는 나오지만, 설정값·임계값 같은 구체적인 숫자는 적지 않는다.",
        2: "재현에 필요한 구체적인 수치와 조건을 적는다. 항목 이름, 값, 단위, 적용 순서를 함께 쓴다.",
    },
    "V": {
        0: "외부에 알려져도 거래 조건이나 경쟁 관계에 영향이 없다는 점이 드러나야 한다.",
        1: "내부 처리 시간·재작업·담당 배분 같은 운영 효율에 영향이 있다는 점이 드러나야 한다.",
        2: "원가 가정, 할인 여지, 공급 단가 구성처럼 협상·경쟁 위치에 직접 영향을 주는 내용을 담는다.",
    },
    "M": {
        0: "보안 표시도 없고 열람 권한 제한도 배포 기록도 없다는 사실이 문서 안에 드러나야 한다.",
        1: "취급 주의 표시는 있으나 열람 권한 제한과 배포 이력은 없다는 사실이 드러나야 한다.",
        2: "보안 표시, 열람 권한 제한, 전달·반출 기록 세 가지가 모두 있다는 사실이 드러나야 한다.",
    },
}

SYSTEM = (
    "당신은 한국 기업의 실무 문서를 작성합니다. 보고서 말투로 담백하게 씁니다.\n"
    "규칙:\n"
    "1. 보안등급을 절대 언급하지 않습니다. '특급기밀·1급비밀·2급대외비·3급공개·기밀·비밀·"
    "대외비·극비·영업비밀·TS·S1·S2·S3' 같은 말을 쓰지 않습니다.\n"
    "   문서에 표시가 찍혀 있다는 사실을 적을 때도 등급 이름을 쓰지 말고 "
    "'취급 주의 표시' · '보안 표시' 처럼 적습니다.\n"
    "2. 문서가 어느 등급인지 평가하거나 설명하지 않습니다. 업무 기록만 씁니다.\n"
    "3. 지시받은 세 가지 성질이 본문에 자연스럽게 드러나게 씁니다. 성질을 말로 설명하지 말고 "
    "내용으로 보이십시오.\n"
    "4. 표제·머리말을 포함해 한국어로만 씁니다.\n"
    "5. 요약하지 않습니다. 실제 업무 기록처럼 소제목을 나누어 충분한 분량으로 씁니다."
)

# ⚠ '기밀·비밀·대외비' 를 홑낱말로도 막는다. 처음엔 '기밀 등급' 처럼 붙은 꼴만 막았는데,
#   모델이 "보안 표시 기준 '기밀' 적용" 이라고 써서 그대로 통과했다. 그 낱말 하나가
#   등급을 알려 준다([[grade-name-words-are-shortcuts-2026-09-11]]: '기밀' → TS 69.1%).
#   대신 표시가 있다는 사실은 '취급 주의 표시' 로 쓰도록 지시문에서 길을 열어 둔다.
_BANNED = re.compile(
    r"특급|기밀|비밀|대외비|극비|보안\s*등급|\bTS\b|\bS1\b|\bS2\b|\bS3\b")

SCHEMA = {
    "name": "business_document",
    "schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["title", "body"],
        "additionalProperties": False,
    },
}


def prompt_for(s: int, v: int, m: int, doc_type: str, theme: str, facet: str) -> str:
    # ⚠ "600~1,100자" 만 적었더니 한 문단 267자로 압축해서 12/12 가 분량 미달로 탈락했다.
    #   뼈대를 주고 각 절의 분량을 못 박아야 실제 업무 문서 길이가 나온다.
    return (
        f"주제: {theme} ({facet})\n"
        f"문서 종류: {doc_type}\n\n"
        "아래 세 가지가 본문에 드러나도록 작성하십시오. 성질 이름을 쓰지 말고 내용으로 보이십시오.\n"
        f"(가) {LEVEL_SPEC['S'][s]}\n"
        f"(나) {LEVEL_SPEC['V'][v]}\n"
        f"(다) {LEVEL_SPEC['M'][m]}\n\n"
        "다음 네 소제목을 그대로 쓰고, 각 절을 **최소 4문장**으로 채우십시오.\n"
        "## 배경\n## 확인한 내용\n## 영향 검토\n## 문서 취급\n\n"
        "body 는 전체 **700자 이상 1,300자 이하**여야 합니다. 표를 쓰지 마십시오.\n"
        "JSON 으로 title 과 body 를 주십시오."
    )


_THINK = re.compile(r"<think>.*?</think>\s*", re.S)


def _parse(text: str) -> tuple[str, str] | None:
    # qwen3 계열은 답 앞에 <think>…</think> 를 붙인다. 남겨 두면 JSON 파싱이 깨지고,
    # 본문에 섞이면 **모델의 추론 과정이 그대로 학습 자료가 된다**(새 지름길).
    text = _THINK.sub("", text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return None
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    title, body = str(obj.get("title") or "").strip(), str(obj.get("body") or "").strip()
    return (title, body) if body else None


def generate_one(provider, combo, doc_type, theme, facet, *, min_chars, max_chars,
                 tries: int) -> dict | None:
    """⚠ 버리는 사유를 **따로** 센다. 처음엔 '검사 탈락' 하나로 묶었다가 12/12 가 탈락했을 때
    무엇 때문인지 못 봤다 — 사유를 합치면 고칠 자리를 못 찾는다."""
    s, v, m = combo
    prompt = prompt_for(s, v, m, doc_type, theme, facet)
    reason = "시도 없음"
    for attempt in range(tries):
        try:
            out = provider.generate(
                prompt, system=SYSTEM, max_tokens=1600,
                temperature=0.9 if attempt else 0.7, json_schema=SCHEMA)
        except Exception as exc:  # noqa: BLE001
            reason = f"호출 실패 {type(exc).__name__}: {exc}"[:120]
            continue
        parsed = _parse(out.text or "")
        if parsed is None:
            reason = "파싱 실패(앞 60자: %s)" % (out.text or "")[:60].replace("\n", " ")
            continue
        title, body = parsed
        text = f"# {title}\n\n{body}" if title else body
        if not (min_chars <= len(text) <= max_chars):
            reason = "길이 %d자 (허용 %d~%d)" % (len(text), min_chars, max_chars)
            continue
        hit = _BANNED.search(text)
        if hit:
            reason = "금지어 '%s'" % hit.group(0)
            continue
        return {
            "text": text, "label": grade_from_svm(s, v, m), "s": s, "v": v, "m": m,
            "document_type": doc_type, "theme": theme,
            "origin": "synthetic_llm", "label_source": "rule_from_content_svm",
            "human_confirmed": False, "attempts": attempt + 1,
        }
    return None


# 검사는 규칙 생성기와 **같은 자**를 쓴다 — 두 벌로 재면 값이 갈려 비교가 안 된다.
# 특히 '라벨을 섞었을 때' 기준선이 여기 들어 있다(작은 표본에서 헛경보를 막는다).


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="LLM 으로 내용 기반 등급 코퍼스 생성")
    ap.add_argument("--provider", default="ollama")
    ap.add_argument("--model", default="")
    ap.add_argument("--n", type=int, default=800)
    ap.add_argument("--out", default="datasets/grade_content_llm_v1")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--tries", type=int, default=3)
    ap.add_argument("--min-chars", type=int, default=500)
    ap.add_argument("--max-chars", type=int, default=1400)
    ap.add_argument("--val", type=int, default=0, help="0 이면 n 의 20%%")
    ap.add_argument("--seed", type=int, default=20260912)
    ap.add_argument("--dry", action="store_true", help="쓰지 않고 몇 건만 뽑아 본다")
    a = ap.parse_args(argv)

    from koipa.adapters.llm import build_provider

    provider = build_provider(a.provider)
    if a.model:
        provider.model = a.model
    print("provider=%s model=%s" % (a.provider, getattr(provider, "model", "?")))

    # 등급이 고르게 나오도록 S·V·M 조합을 등급별로 모은다.
    by_grade: dict[str, list[tuple[int, int, int]]] = collections.defaultdict(list)
    for s in (0, 1, 2):
        for v in (0, 1, 2):
            for m in (0, 1, 2):
                by_grade[grade_from_svm(s, v, m)].append((s, v, m))

    rng = random.Random(a.seed)
    jobs = []
    for i in range(a.n):
        grade = GRADES[i % len(GRADES)]
        combos = by_grade[grade]
        theme, facet = rng.choice(THEMES)          # 등급과 무관
        jobs.append((combos[(i // len(GRADES)) % len(combos)],
                     rng.choice(DOC_TYPES), theme, facet))   # 문서종류도 무관

    started = time.perf_counter()
    rows: list[dict] = []
    errors: collections.Counter = collections.Counter()
    with futures.ThreadPoolExecutor(max_workers=a.workers) as pool:
        futs = [pool.submit(generate_one, provider, *job,
                            min_chars=a.min_chars, max_chars=a.max_chars, tries=a.tries)
                for job in jobs]
        for index, fut in enumerate(futures.as_completed(futs), start=1):
            row = fut.result()
            if row is None:
                errors["검사 탈락(길이·등급낱말·파싱)"] += 1
            elif "_error" in row:
                errors[row["_error"][:60]] += 1
            else:
                rows.append(row)
            if index % 20 == 0 or index == len(futs):
                print("  %d/%d · 채택 %d · %.0f초"
                      % (index, len(futs), len(rows), time.perf_counter() - started), flush=True)

    if errors:
        print("\n버린 것:", dict(errors))
    if not rows:
        print("⛔ 한 건도 못 만들었다 — 프로바이더·모델 설정을 먼저 확인할 것")
        return 1

    rep = audit(rows)
    print()
    print_audit(rep)
    print("  재시도 분포:", dict(collections.Counter(r["attempts"] for r in rows)))

    if a.dry:
        print("\n예시 한 건 (S=%d V=%d M=%d → %s):" % (
            rows[0]["s"], rows[0]["v"], rows[0]["m"], rows[0]["label"]))
        print("   " + "\n   ".join(rows[0]["text"].splitlines()[:8]))
        print("\n  --dry — 아무것도 쓰지 않았다.")
        return 0

    rng.shuffle(rows)
    n_val = a.val or max(1, len(rows) // 5)
    out_dir = _POC / a.out
    out_dir.mkdir(parents=True, exist_ok=True)
    digests = {}
    for name, part in (("train", rows[n_val:]), ("val", rows[:n_val]), ("test", rows[:n_val])):
        body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in part)
        (out_dir / f"{name}.jsonl").write_text(body, encoding="utf-8", newline="\n")
        digests[name] = hashlib.sha256(body.encode("utf-8")).hexdigest()
        print("  %-6s %4d건" % (name, len(part)))
    (out_dir / "manifest.json").write_text(json.dumps({
        "built_at": "2026-09-12", "provider": a.provider,
        "model": getattr(provider, "model", None), "seed": a.seed,
        "label_source": "rule_from_content_svm (grade_from_svm)",
        "human_confirmed_count": 0,
        "use": "내용을 읽어야 풀리는 과제. 사람 확정 정답 아님 · 실문서 일반화 근거 아님.",
        "sha256": digests, "shortcut_audit": rep,
    }, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print("  기록: %s" % out_dir.relative_to(_POC))
    print("\n⚠ 되풀이 문구 지름길도 따로 볼 것:")
    print("   python scripts/measure_grade_phrase_leak.py --pool ... (또는 같은 방식의 계수)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
