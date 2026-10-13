"""LLM 요소 판정 탐침 — 천장이 어디인지 몇 시간 만에 확인한다.

왜 만드는가. 지금까지 한 것은 프레임을 늘려 미관측 프레임에 일반화시키는 게임이다.
1차 F1 0.3865 -> 5차 0.7496 까지 왔지만 **기울기만 바꾸고 천장은 안 바뀐다** - 회원사
실문서는 경제적 유용성을 수천 가지로 표현하는데 우리 프레임은 31개다.

LLM 은 프레임을 본 적 없어도 판단한다. 일반화가 학습 데이터 양에 걸려 있지 않다.
그래서 **같은 판정면으로 인코더와 LLM 을 나란히 재면** 방향이 정해진다:

    LLM 이 크게 높다   -> 프레임 늘리기를 접고 LLM 경로로 간다
    LLM 도 비슷하다    -> 과제 자체가 어려운 것이고 실문서 말고 답이 없다

⚠ 이 탐침은 **판정면을 소비한다.** 판정면은 원래 모델 하나를 한 번 재는 자리다. 그래서
   표본만 쓰고(기본 200건) 하이퍼파라미터 조정에 쓰지 않는다. 프롬프트를 결과 보고 고치면
   그 순간 판정면이 튜닝면이 되므로, 프롬프트는 **한 번 쓰고 고치지 않는다.**

모델은 고객사 배포 계열(qwen3)을 기본으로 쓴다 - 여기서 나온 수치가 그대로 현장 근거가 된다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "src"), str(_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

FACTORS = ("secrecy", "value", "management")
KO = {"secrecy": "비공지성", "value": "경제적 유용성", "management": "비밀관리성"}

# 정본 기준을 그대로 옮긴다. 등급은 묻지 않는다 - 요소만 묻고 등급은 규칙으로 만든다.
RUBRIC = {
    "secrecy": (
        "그 문서의 내용이 외부에 공개되지 않았는지",
        "0 = 공개되었음이 본문에 명시됨(게재·고시·배포·공표 등 사실 진술)\n"
        "1 = 일부만 공개되고 일부는 미공개\n"
        "2 = 외부에 공개된 적이 없음이 본문에 명시됨\n"
        "9 = 본문에 공개 여부에 대한 단언이 없음",
    ),
    "value": (
        "그 정보가 경제적 가치를 갖는지",
        "0 = 가치가 없음이 본문에 명시됨(누구나 얻을 수 있음·선점 효과 없음 등)\n"
        "1 = 가치가 있으나 제한적이거나 대체 가능\n"
        "2 = 가치가 크고 대체하기 어려움\n"
        "9 = 본문에 가치에 대한 단언이 없음",
    ),
    "management": (
        "그 문서가 비밀로 관리되고 있는지",
        "0 = 관리하지 않음이 본문에 명시됨(제한 없음·기록 없음 등)\n"
        "1 = 일부 관리하나 통제가 불완전함\n"
        "2 = 접근·반출·이력이 통제되고 있음\n"
        "9 = 본문에 관리 상태에 대한 단언이 없음",
    ),
}

PROMPT = """다음은 어느 회사의 업무 문서다. 영업비밀 판단 요소 중 **{ko}**만 판정하라.

판정 기준 — {what}:
{levels}

규칙:
- 본문에 근거 문장이 있을 때만 0/1/2 를 준다. 추측하지 않는다.
- 본문이 그 요소에 대해 아무 단언도 하지 않으면 9 를 준다.
- 등급(TS/S1/S2/S3)은 판정하지 않는다.

답은 아래 형식 한 줄로만 낸다. 설명을 붙이지 않는다.
판정: <숫자>
근거: <본문에서 그대로 옮긴 문장 하나. 9 면 빈칸>

문서:
---
{text}
---"""


def ask(host: str, model: str, prompt: str, timeout: int) -> str:
    body = json.dumps({
        "model": model, "prompt": prompt, "stream": False,
        "options": {"temperature": 0, "num_predict": 120, "num_ctx": 4096},
        "think": False,
    }).encode()
    req = urllib.request.Request(f"{host}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())["response"]


def parse(out: str) -> int | None:
    m = re.search(r"판정\s*[:：]\s*([0129])", out)
    if m:
        return int(m.group(1))
    m = re.search(r"\b([0129])\b", out)
    return int(m.group(1)) if m else None


def to_code(v: int | None) -> int | None:
    """LLM 출력(0/1/2/9) -> 내부 클래스(0 absent · 1 lv1 · 2 lv2 · 3 unknown)."""
    if v is None:
        return None
    return 3 if v == 9 else v


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="LLM 요소 판정 탐침")
    ap.add_argument("--eval", default="datasets/v8/holdout_forms.jsonl")
    ap.add_argument("--model", default="qwen3:14b")
    ap.add_argument("--host", default="http://localhost:11434")
    ap.add_argument("--n", type=int, default=200, help="표본 수 - 판정면을 아껴 쓴다")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--out", default="reports/V8_LLM_PROBE.jsonl")
    args = ap.parse_args(argv)

    from koipa.modules.m3_labeling.rule_engine import grade_from_svm

    from v8_judge import cls_to_score, row_codes

    rows = [json.loads(l) for l in Path(args.eval).read_text("utf-8").splitlines() if l.strip()]
    # 등급이 고루 섞이게 균등 표집한다. 앞에서 자르면 특정 경계에 쏠린다.
    by: dict[str, list] = {}
    for r in rows:
        by.setdefault(r["label"], []).append(r)
    per = max(1, args.n // len(by))
    sample = [r for g in sorted(by) for r in by[g][:per]][:args.n]
    print(f"[probe] {args.model} · {len(sample)}건 (등급 {dict(Counter(r['label'] for r in sample))})")

    ok = {f: 0 for f in FACTORS}
    n_parsed = {f: 0 for f in FACTORS}
    grade_hit = 0
    under = high = 0
    ORDER = {g: i for i, g in enumerate(("TS", "S1", "S2", "S3"))}
    out_fh = Path(args.out).open("w", encoding="utf-8")

    for i, r in enumerate(sample, 1):
        truth = row_codes(r)
        pred: list[int | None] = []
        for k, f in enumerate(FACTORS):
            what, levels = RUBRIC[f]
            p = PROMPT.format(ko=KO[f], what=what, levels=levels, text=r["text"][:3500])
            try:
                raw = ask(args.host, args.model, p, args.timeout)
            except Exception as e:  # noqa: BLE001
                print(f"  [{i}] {f} 호출 실패: {e}")
                raw = ""
            c = to_code(parse(raw))
            pred.append(c)
            if c is not None:
                n_parsed[f] += 1
                ok[f] += int(c == truth[k])
        if all(c is not None for c in pred):
            g = grade_from_svm(*[cls_to_score(c) for c in pred])  # type: ignore[arg-type]
            grade_hit += int(g == r["label"])
            if ORDER[g] > ORDER[r["label"]]:
                under += 1
            elif ORDER[g] < ORDER[r["label"]]:
                high += 1
        out_fh.write(json.dumps({"doc_id": r["doc_id"], "truth_codes": list(truth),
                                 "pred_codes": pred, "label": r["label"]},
                                ensure_ascii=False) + chr(10))
        if i % 20 == 0:
            acc = {f: round(ok[f] / max(1, n_parsed[f]), 3) for f in FACTORS}
            print(f"  {i}/{len(sample)} 요소정확 {acc} 등급 {grade_hit / i:.3f}")
    out_fh.close()

    print()
    print(f"{'요소':12s}{'정확도':>9s}{'파싱':>8s}")
    for f in FACTORS:
        print(f"{f:12s}{ok[f] / max(1, n_parsed[f]):>9.4f}{n_parsed[f]:>6d}/{len(sample)}")
    print(f"\n등급 일치 {grade_hit / len(sample):.4f} · 낮게봄 {under} · 높게봄 {high}")
    print(f"[saved] {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
