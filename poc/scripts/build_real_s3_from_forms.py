"""공개 서식 코퍼스에서 **실문서 S3 학습셋**을 만든다.

왜. 우리 학습셋은 전부 합성이다. 그리고 실측된 약점이 하나 있다.

    빈 서식 161건 중 헛경보 21건 = 13.0%   (reports/PUBLIC_FORM_SPECIFICITY.json)
    공개 배포되는 빈 양식을 8건 중 1건꼴로 영업비밀이라고 부른다.

이 코퍼스는 **정답을 안다.** 공개 배포되는 빈 서식(신청서·계약서 양식·대장)에는 영업비밀이
없다 — 누구나 내려받을 수 있어 비공지성이 없고, 내용이 비어 있어 경제적가치도 없다.
사람 검수 없이 라벨이 보증되는 유일한 실문서다.

⚠ 얻을 수 있는 것은 **S3 뿐이다.** 빈 양식에 비밀이 없으므로 TS·S1·S2 는 여기서 안 나온다.
  우리의 진짜 약점(S1 재현율)은 이것으로 안 풀린다. 이 작업이 고치는 것은 헛경보다.

⚠ 분할은 `eval_public_form_specificity.py` 의 해시 분할을 **그대로 쓴다.**
  work 절반 = 학습 후보 · sealed 절반 = 평가 전용. 이미 특이도 측정에 쓴 쪽이 work 이므로
  그쪽을 학습에 넣고 sealed 로 개선을 잰다. 새로 나누면 봉인이 깨진다.

⚠ '마케팅자료' 폴더는 제외한다. 빈 양식이 아니라 실제 기획서·제안서가 섞여 있어 정답이
  불확실하다(헛경보 69.7% 인데 그게 오류인지 정상인지 판단할 근거가 없다).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

# eval_public_form_specificity.py 와 동일해야 한다 — 분할이 어긋나면 봉인이 깨진다.
BLANK_FORM_FOLDERS = (
    "100가지 회사 업무용 서식",
    "각종 법률 서식 모음",
    "계약서 모음",
    "문서양식총람",
    "XLS 엑셀 서식 자료",
)
MIN_CHARS = 120          # 이보다 짧으면 분류 근거가 안 된다
MAX_CHARS = 20000


def half(path: Path) -> str:
    """문서 해시로 work/sealed 고정 분할 — eval_public_form_specificity.py 와 동일 규칙."""
    h = hashlib.sha256(str(path).encode("utf-8")).hexdigest()
    return "work" if int(h[:8], 16) % 2 == 0 else "sealed"


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="공개 서식 -> 실문서 S3 학습셋")
    ap.add_argument("--root", default="../서식모음")
    ap.add_argument("--out-dir", default="datasets/real_s3_forms")
    ap.add_argument("--max-train", type=int, default=600,
                    help="학습용 상한. S3 를 과하게 넣으면 모델이 S3 로 쏠려 미탐이 는다.")
    ap.add_argument("--max-sealed", type=int, default=400)
    ap.add_argument("--seed", type=int, default=20260816)
    args = ap.parse_args(argv)

    os.environ.setdefault("TESTING", "1")
    os.environ.setdefault("VECTOR_BACKEND", "inmemory")
    os.environ.setdefault("REQUIRE_REAL_EMBEDDER", "false")
    from koipa.modules.m2_preprocess.extractor import extract  # noqa: PLC0415

    root = Path(args.root)
    files: list[Path] = []
    for folder in BLANK_FORM_FOLDERS:
        d = root / folder
        if not d.is_dir():
            print(f"  [건너뜀] 폴더 없음: {folder}")
            continue
        files += [p for p in d.rglob("*") if p.is_file()]
    print(f"빈 서식 폴더 {len(BLANK_FORM_FOLDERS)}개 · 파일 {len(files)}건")

    rng = random.Random(args.seed)
    rng.shuffle(files)
    want = {"work": args.max_train, "sealed": args.max_sealed}
    got: dict[str, list[dict]] = {"work": [], "sealed": []}
    seen_hashes: set[str] = set()
    stat = Counter()
    t0 = time.perf_counter()

    for i, p in enumerate(files):
        side = half(p)
        if len(got[side]) >= want[side]:
            if all(len(got[s]) >= want[s] for s in want):
                break
            continue
        try:
            r = extract(p)
            text = (r.text or "").strip()
        except Exception:  # noqa: BLE001
            stat["추출실패"] += 1
            continue
        if len(text) < MIN_CHARS:
            stat["너무짧음"] += 1
            continue
        text = text[:MAX_CHARS]
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if h in seen_hashes:          # 같은 양식이 여러 폴더에 중복돼 있다
            stat["중복본문"] += 1
            continue
        seen_hashes.add(h)
        got[side].append({
            "doc_id": h[:16],
            "text": text,
            "label": "S3",
            "label_source": "public_form_definitive",
            "source": p.relative_to(root).parts[0],
            "origin_file": p.name,
        })
        stat["채택"] += 1
        if (i + 1) % 300 == 0:
            print(f"  {i+1}/{len(files)} 훑음 · 채택 {stat['채택']} · "
                  f"{time.perf_counter()-t0:.0f}s")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for side, name in (("work", "train_s3.jsonl"), ("sealed", "sealed_s3.jsonl")):
        rows = got[side]
        (out / name).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", "utf-8")
        lens = sorted(len(r["text"]) for r in rows)
        med = lens[len(lens)//2] if lens else 0
        print(f"\n[{name}] {len(rows)}건 · 중앙 {med}자 · "
              f"{min(lens) if lens else 0}~{max(lens) if lens else 0}자")
        print("  출처:", dict(Counter(r["source"] for r in rows)))

    print(f"\n훑은 파일 {min(i+1, len(files))} · {dict(stat)} · {time.perf_counter()-t0:.0f}s")
    print(f"\n⚠ sealed 는 평가 전용이다. 학습에 넣으면 개선을 잴 방법이 사라진다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
