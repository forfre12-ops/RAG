"""공개 서식 코퍼스에서 **실문서 S3 학습셋**을 만든다.

왜. 우리 학습셋은 전부 합성이다. 그리고 실측된 약점이 하나 있다.

    빈 서식 161건 중 헛경보 21건 = 13.0%   (reports/PUBLIC_FORM_SPECIFICITY.json)
    공개 배포되는 빈 양식을 8건 중 1건꼴로 영업비밀이라고 부른다.

이 코퍼스는 **정답을 안다.** 공개 배포되는 빈 서식(신청서·계약서 양식·대장)에는 영업비밀이
없다 - 누구나 내려받을 수 있어 비공지성이 없고, 내용이 비어 있어 경제적가치도 없다.
사람 검수 없이 라벨이 보증되는 유일한 실문서다.

⚠ 얻을 수 있는 것은 **S3 뿐이다.** 빈 양식에 비밀이 없으므로 TS·S1·S2 는 여기서 안 나온다.
  우리의 진짜 약점(S1 재현율)은 이것으로 안 풀린다. 이 작업이 고치는 것은 헛경보다.

⚠ 분할은 `eval_public_form_specificity.py` 의 해시 분할을 **그대로 쓴다.**
  work 절반 = 학습 후보 · sealed 절반 = 평가 전용. 이미 특이도 측정에 쓴 쪽이 work 이므로
  그쪽을 학습에 넣고 sealed 로 개선을 잰다. 새로 나누면 봉인이 깨진다.

⚠ '마케팅자료' 폴더는 제외한다. 빈 양식이 아니라 실제 기획서·제안서가 섞여 있어 정답이
  불확실하다(헛경보 69.7% 인데 그게 오류인지 정상인지 판단할 근거가 없다).

⚠ "실문서니까 학습셋과 겹칠 수 없다" 는 표현은 과했다(외부 검토 지적). 서식에도 표준문구·
  개정본·복제본이 있다. 그래서 세 겹으로 거른다 - 본문 해시 · 근접중복(3-gram Jaccard)
  · 기존 학습셋과의 문장 겹침.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

for _s in ("stdout", "stderr"):
    _f = getattr(sys, _s)
    if getattr(_f, "encoding", "") and _f.encoding.lower() not in ("utf-8", "utf-8-sig"):
        import io as _io
        setattr(sys, _s, _io.TextIOWrapper(_f.buffer, encoding="utf-8", errors="replace"))

# eval_public_form_specificity.py 와 동일해야 한다 - 분할이 어긋나면 봉인이 깨진다.
BLANK_FORM_FOLDERS = (
    "100가지 회사 업무용 서식",
    "각종 법률 서식 모음",
    "계약서 모음",
    "문서양식총람",
    "XLS 엑셀 서식 자료",
)
MIN_CHARS = 120
MAX_CHARS = 20000

# [가중치] 개수가 아니라 **가중치 합**이 학습 신호를 정한다(외부 검토 지적).
#   기존 S3 813건의 가중치 합 = 391.4 (평균 0.481)  <- 실측
#   신규 600건을 1.0 으로 넣으면 S3 신호의 600/991 = 60.5% 가 서식 문체가 된다
#   0.5 로 넣으면 300/691 = 43.4%
# 실패 방향이 비대칭이라 보수적으로 잡는다 - 헛경보가 덜 줄어드는 것은 되돌릴 수 있지만
# 미탐이 늘면 안전 주장이 깨진다.
DEFAULT_WEIGHT = 0.5

# [근접중복] 서식은 제목·셀만 바뀐 개정본이 흔하다. 본문 해시가 달라도 같은 문서다.
NEAR_DUP_JACCARD = 0.90
_NGRAM = 3
_SENT_SPLIT = re.compile(r"[.\n]")


def ngrams(text: str) -> set[str]:
    t = "".join(text.split())
    return {t[i:i + _NGRAM] for i in range(max(0, len(t) - _NGRAM + 1))}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def sentences(text: str) -> set[str]:
    return {x.strip() for x in _SENT_SPLIT.split(text or "") if len(x.strip()) >= 12}


def half(path: Path) -> str:
    """문서 해시로 work/sealed 고정 분할 - eval_public_form_specificity.py 와 동일 규칙."""
    h = hashlib.sha256(str(path).encode("utf-8")).hexdigest()
    return "work" if int(h[:8], 16) % 2 == 0 else "sealed"


def file_sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def git_sha() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"],
                           capture_output=True, text=True, cwd=str(_ROOT))
        return r.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="공개 서식 -> 실문서 S3 학습셋")
    ap.add_argument("--root", default="../서식모음")
    ap.add_argument("--out-dir", default="datasets/real_s3_forms")
    ap.add_argument("--max-train", type=int, default=600)
    ap.add_argument("--max-sealed", type=int, default=400)
    ap.add_argument("--seed", type=int, default=20260816)
    ap.add_argument("--weight", type=float, default=DEFAULT_WEIGHT)
    ap.add_argument("--exclude-train", default="datasets/labeled_p1_v5_clean/train.jsonl",
                    help="이 학습셋과 문장이 겹치는 서식은 뺀다(뺄 비용이 0이다)")
    args = ap.parse_args(argv)

    os.environ.setdefault("TESTING", "1")
    os.environ.setdefault("VECTOR_BACKEND", "inmemory")
    os.environ.setdefault("REQUIRE_REAL_EMBEDDER", "false")
    from koipa.modules.m2_preprocess.extractor import extract  # noqa: PLC0415

    # 기존 학습셋 문장 풀 - 이것과 겹치는 서식은 뺀다
    exclude_sents: set[str] = set()
    ex = Path(args.exclude_train)
    if ex.exists():
        for line in ex.read_text(encoding="utf-8").splitlines():
            if line.strip():
                exclude_sents |= sentences(json.loads(line).get("text") or "")
        print(f"제외 기준 학습셋 문장 {len(exclude_sents)}종")

    root = Path(args.root)
    files: list[Path] = []
    for folder in BLANK_FORM_FOLDERS:
        d = root / folder
        if d.is_dir():
            files += [p for p in d.rglob("*") if p.is_file()]
        else:
            print(f"  [건너뜀] 폴더 없음: {folder}")
    print(f"빈 서식 폴더 {len(BLANK_FORM_FOLDERS)}개 · 파일 {len(files)}건")

    rng = random.Random(args.seed)
    rng.shuffle(files)
    want = {"work": args.max_train, "sealed": args.max_sealed}
    got: dict[str, list[dict]] = {"work": [], "sealed": []}
    seen_text: set[str] = set()
    kept_ngrams: list[set[str]] = []      # 근접중복 비교용(전체 공용 - 양쪽에 갈려도 잡는다)
    stat = Counter()
    t0 = time.perf_counter()
    scanned = 0

    for p in files:
        scanned += 1
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
        if h in seen_text:
            stat["중복본문"] += 1
            continue

        # 기존 학습셋과 문장이 겹치면 뺀다
        if exclude_sents and (sentences(text) & exclude_sents):
            stat["학습셋문장겹침"] += 1
            continue

        # 근접중복(개정본·복제본)
        ng = ngrams(text)
        if any(jaccard(ng, prev) >= NEAR_DUP_JACCARD for prev in kept_ngrams):
            stat["근접중복"] += 1
            continue

        seen_text.add(h)
        kept_ngrams.append(ng)
        got[side].append({
            "doc_id": h[:16],
            "text": text,
            "label": "S3",
            "sample_weight": args.weight,
            "label_source": "public_form_definitive",
            "source": p.relative_to(root).parts[0],
            "origin_file": p.name,
            "origin_sha256": file_sha(p),
            "text_sha256": h,
        })
        stat["채택"] += 1
        if scanned % 400 == 0:
            print(f"  {scanned}/{len(files)} 훑음 · 채택 {stat['채택']} · "
                  f"{time.perf_counter() - t0:.0f}s")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "built_at_git_sha": git_sha(),
        "seed": args.seed,
        "sample_weight": args.weight,
        "split_rule": "sha256(path)[:8] % 2 == 0 -> work, else sealed "
                      "(eval_public_form_specificity.py 와 동일)",
        "folders": list(BLANK_FORM_FOLDERS),
        "excluded_folder": "마케팅자료 (빈 양식 아님 - 정답 불확실)",
        "filters": {
            "min_chars": MIN_CHARS, "max_chars": MAX_CHARS,
            "near_dup_jaccard": NEAR_DUP_JACCARD, "ngram": _NGRAM,
            "exclude_train": args.exclude_train,
        },
        "counts": dict(stat),
        "unverified": [
            "원 출처 URL·기관 (사용자 확인 필요)",
            "학습·재배포 이용 근거 (공개 다운로드 가능 != 재배포 가능)",
            "수집일",
        ],
    }
    for side, name in (("work", "train_s3.jsonl"), ("sealed", "sealed_s3.jsonl")):
        rows = got[side]
        body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
        (out / name).write_text(body, "utf-8")
        lens = sorted(len(r["text"]) for r in rows)
        med = lens[len(lens) // 2] if lens else 0
        manifest[name] = {
            "n": len(rows),
            "median_chars": med,
            "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "by_source": dict(Counter(r["source"] for r in rows)),
            "weight_sum": round(sum(r["sample_weight"] for r in rows), 2),
        }
        print(f"\n[{name}] {len(rows)}건 · 중앙 {med}자 · 가중치합 "
              f"{manifest[name]['weight_sum']}")
        print(f"  sha256 {manifest[name]['sha256'][:24]}")
        print("  출처:", manifest[name]["by_source"])

    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n훑은 파일 {scanned} · {dict(stat)} · {time.perf_counter() - t0:.0f}s")
    print(f"[manifest] {out / 'manifest.json'}")
    print("\n⚠ sealed 는 평가 전용이다. 학습에 넣으면 개선을 잴 방법이 사라진다.")
    print("⚠ 원 출처 URL·이용 근거는 미확인으로 manifest 에 남겼다 - 사용자 확인 필요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
