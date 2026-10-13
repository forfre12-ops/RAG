"""등급 표식 마스킹 — 운영 학습셋이 무엇으로 맞히고 있었는지 가른다.

왜(실측 2026-08-14). `labeled_p1_v5_clean` 3분할 2,554건 중 **1,273건(49.8%)** 이 본문에
등급 표식을 달고 있고, 표식만 보면 등급이 거의 결정된다:

    1급 비밀   380건 -> 100% S1
    대외비     382건 ->  99% S2
    기밀       432건 ->  82% TS
    영업비밀   446건 ->  85% S1

배포본 v5 의 내부 지표(val F1 0.951)가 이 표식 위에서 나온 값이다. 모델이 내용을 읽어서
맞힌 것인지 표식을 읽어서 맞힌 것인지 구분되지 않는다. 마스킹 후 재학습하면 그 차이가
숫자로 드러난다 — **그것이 사실상 첫 정직한 측정치**가 된다.

⚠ 마스킹 방식. 표식을 지우지 않고 **중립 토큰으로 치환**한다. 통째로 지우면 문장이
   깨지고 길이가 바뀌어 다른 신호(길이)가 생긴다. 치환하면 문장 구조가 유지된다.

⚠ 무엇을 마스킹하지 않는가. '보안' '취급' '공개' 같은 일반 어휘는 건드리지 않는다.
   그것까지 지우면 요소 판단의 근거 자체가 사라져 과제가 달라진다. 지우는 것은
   **등급을 직접 지시하는 표식**뿐이다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

# 등급을 직접 지시하는 표식만 고른다. 치환어는 문장 구조를 유지하는 중립 명사로 둔다.
TELLS: list[tuple[str, str]] = [
    (r"[1-3]\s*급\s*비밀", "내부 자료"),
    (r"대외\s*비", "내부 자료"),
    (r"극비", "내부 자료"),
    (r"영업\s*비밀", "내부 자료"),
    (r"기밀\s*(자료|문서|정보|사항)", "내부 자료"),
    (r"기밀", "내부"),
    (r"보안\s*등급\s*[:：]?\s*[^\s,.]{0,6}", "취급 구분"),
    (r"\b(TS|S1|S2|S3)\s*등급", "해당 구분"),
    (r"등급\s*[:：]\s*(TS|S1|S2|S3)", "구분 : 해당"),
    (r"\b(TS|S1|S2|S3)\b", "해당 구분"),
]
_COMPILED = [(re.compile(p), r) for p, r in TELLS]


def mask(text: str) -> tuple[str, int]:
    n = 0
    for rx, rep in _COMPILED:
        text, k = rx.subn(rep, text)
        n += k
    return text, n


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="등급 표식 마스킹")
    ap.add_argument("--src", default="datasets/labeled_p1_v5_clean")
    ap.add_argument("--dst", default="datasets/labeled_p1_v5_masked")
    ap.add_argument("--splits", default="train,val,test")
    args = ap.parse_args(argv)

    src, dst = Path(args.src), Path(args.dst)
    dst.mkdir(parents=True, exist_ok=True)

    total = touched = subs = 0
    by_label: Counter = Counter()
    for split in args.splits.split(","):
        rows = [json.loads(l) for l in (src / f"{split}.jsonl").read_text("utf-8").splitlines() if l.strip()]
        out = []
        for r in rows:
            key = "text" if "text" in r else ("content" if "content" in r else None)
            total += 1
            if key:
                new, k = mask(r[key])
                if k:
                    touched += 1
                    subs += k
                    by_label[r.get("label")] += 1
                    r = {**r, key: new, "tell_masked": k}
            out.append(r)
        (dst / f"{split}.jsonl").write_text(
            chr(10).join(json.dumps(x, ensure_ascii=False) for x in out) + chr(10), "utf-8")
        print(f"  {split:6s} {len(rows):5d}건 -> {dst / f'{split}.jsonl'}")

    print(f"\n총 {total}건 중 {touched}건({touched / total:.1%}) 마스킹 · 치환 {subs}회")
    print(f"등급별 마스킹 건수 {dict(by_label)}")

    # 잔여 확인 — 마스킹이 실제로 먹었는지 같은 패턴으로 다시 센다
    left = 0
    for split in args.splits.split(","):
        for l in (dst / f"{split}.jsonl").read_text("utf-8").splitlines():
            if not l.strip():
                continue
            r = json.loads(l)
            t = r.get("text") or r.get("content") or ""
            if any(rx.search(t) for rx, _ in _COMPILED):
                left += 1
    print(f"잔여 표식 문서 {left}건 {'— 마스킹 완료' if left == 0 else '⚠ 패턴 보완 필요'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
