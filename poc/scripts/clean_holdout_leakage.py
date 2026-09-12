"""holdout/test에서 train에 누출된 문서를 제거해 클린본을 emit.

배경: train_subset(610)의 텍스트가 classification_gold/holdout에 doc_id만 다른 채
중복돼 있어, holdout_eval 109건 중 67건(42%)이 학습 누출 상태였음(평가 무효화).
본 스크립트는 train 에 걸리는 문서를 holdout 에서 제거하고 <stem>.clean.jsonl 로
저장한다(원본은 보존). check_data_quality.py 의 누출 게이트와 짝.

[2026-09-05] **해시 축만으로는 못 잡는 것이 있다.** 해시는 본문 전체를 잡으므로 같은
문서가 길이를 달리해 들어가면 갈린다. 실측:

    holdout_eval.jsonl #56  vs  labeled_p1_v5_clean/train.jsonl #1933
      둘 다 「두산중공업 경업금지가처분 사건」  ·  문자 유사도 0.855
      6,000자(홀드아웃 절단) 대 8,026자  →  해시 다름  →  옛 게이트 통과
      정규화 문장 51/51 전부 일치  ·  라벨은 S1 대 TS 로 서로 다름

그래서 **문장 축**을 더한다. 정규화는 koipa.dataset_leakage 의 것을 그대로 쓴다 —
홀드아웃 독립성 검사(koipa.holdout_independence)와 축을 맞춰 두 도구가 같은 말을 하게
한다. 다른 정규화를 쓰면 한쪽은 "독립"이라 하고 다른 쪽은 "누출"이라 하는 상태가 생긴다.

판정은 **문서 짝**으로 한다 — 학습셋 전체를 한 덩어리로 보고 교집합을 세지 않는다.
여러 학습 문서에서 한 문장씩 주워 모은 것도 "많이 겹친다"로 읽히기 때문이다. 가장 많이
겹치는 학습 문서 하나를 찾아 그 짝과만 견준다. 비율은 **양방향의 큰 쪽**을 쓴다
(홀드아웃이 학습 문서의 절단본인 경우와 그 반대를 둘 다 잡는다).

    같은 원본으로 본다:  짝과 공유한 문장 >= 3  그리고  max(공유/홀드, 공유/학습) >= 0.60

⚠ 문장이 겹친다고 다 지우면 안 된다. 판결문은 상투어가 많아("그러므로 상고를 기각하고
  ...주문과 같이 판결한다") 무관한 사건끼리도 겹치고, 인용 서식은 숫자가 # 로 뭉개지면서
  거의 같은 문장이 된다("선고 #도# 판결(공#상, #), 대법원 #.").

  위 문턱은 실측으로 잡았다 — 눈으로 확인한 참 6건과 거짓 4건이 이렇게 갈렸다:

      참(같은 원본)   공유  8~51개 · 비율  80~100%
      거짓(상투어)    공유   1~2개 · 비율  17~ 33%

  여백이 넓어(80% 대 33%) 문턱을 조금 옮겨도 판정이 뒤집히지 않는다.

⚠ 학습셋 안에서 문장 빈도를 세어 상투어를 가르는 방법도 시험했는데 **쓰지 않았다.**
  이 학습셋은 판례가 17행뿐이라 판결문 상투어도 "희소"로 잡힌다(문장 21,039종 중 21,011종이
  2개 이하 문서에만 등장). 빈도는 상투어 여부가 아니라 그 코퍼스에 판례가 몇 건인지를 잰다.

usage:
  python scripts/clean_holdout_leakage.py
  python scripts/clean_holdout_leakage.py --train datasets/gold_real/train_subset.jsonl \
      --holdout datasets/gold_real/holdout_eval.jsonl,datasets/gold_real/holdout_business.jsonl
  python scripts/clean_holdout_leakage.py --hash-only   # 옛 동작(해시 축만)
  python scripts/clean_holdout_leakage.py --dry-run     # 세기만 하고 파일은 안 쓴다
  python scripts/clean_holdout_leakage.py --strict      # 누출이 있으면 exit 1
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from koipa.dataset_leakage import _normalize_sentences  # noqa: E402

# 같은 원본으로 보는 기준. 위 주석의 실측으로 잡았다 — 둘 다 넘어야 한다.
DEFAULT_MIN_SHARED = 3
DEFAULT_MIN_RATIO = 0.60


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")]


def text_of(r: dict) -> str:
    return (r.get("text") or (r.get("title", "") + " " + r.get("body", ""))).strip()


def sha(t: str) -> str:
    return hashlib.sha1(t.encode("utf-8", "ignore")).hexdigest()


class TrainIndex:
    """학습셋 문장 역색인 — 홀드아웃 문서마다 학습셋을 다시 훑지 않기 위해."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.sents = [_normalize_sentences(text_of(r)) for r in rows]
        self.inv: dict[str, list[int]] = defaultdict(list)
        for i, s in enumerate(self.sents):
            for x in s:
                self.inv[x].append(i)
        self.n_types = len(self.inv)

    def best_match(self, sents: set[str]) -> tuple[int, int] | None:
        """가장 많이 겹치는 학습 문서 (인덱스, 공유 문장 수). 겹침이 없으면 None."""
        count: dict[int, int] = defaultdict(int)
        for x in sents:
            for i in self.inv.get(x, ()):
                count[i] += 1
        if not count:
            return None
        return max(count.items(), key=lambda kv: kv[1])


def leak_reason(
    record: dict,
    train_hashes: set[str],
    index: TrainIndex | None,
    *,
    min_shared: int,
    min_ratio: float,
) -> tuple[str, str] | None:
    """누출이면 (사유코드, 설명). 아니면 None.

    사유를 코드로 돌려주는 이유 — "몇 건 지웠다"만 남기면 나중에 그 판단을 다시 볼 수
    없다. 축별로 세어야 해시가 잡은 것과 문장이 새로 잡은 것을 가를 수 있다.
    """
    text = text_of(record)
    if sha(text) in train_hashes:
        return ("text_hash", "본문이 학습셋 문서와 완전히 같다")
    if index is None:
        return None

    sents = _normalize_sentences(text)
    if not sents:
        return None
    hit = index.best_match(sents)
    if hit is None:
        return None
    idx, shared = hit
    if shared < min_shared:
        return None
    n_train = len(index.sents[idx]) or 1
    ratio = max(shared / len(sents), shared / n_train)
    if ratio < min_ratio:
        return None
    return (
        "same_source",
        "학습 문서(%s)와 문장 %d개 공유 · 비율 %.0f%% (홀드 %d문장 · 학습 %d문장)"
        % (str(index.rows[idx].get("doc_id"))[:20], shared, ratio * 100, len(sents), n_train),
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="holdout 누출 제거(해시 축 + 문장 축)")
    ap.add_argument("--train", default="datasets/gold_real/train_subset.jsonl")
    ap.add_argument("--holdout",
                    default="datasets/gold_real/holdout_eval.jsonl,datasets/gold_real/holdout_business.jsonl")
    ap.add_argument("--suffix", default=".clean.jsonl")
    ap.add_argument(
        "--hash-only", action="store_true",
        help="옛 동작 — 본문 해시가 같은 것만 뺀다. 같은 문서를 길이 달리해 넣은 것은 못 잡는다.",
    )
    ap.add_argument(
        "--min-shared", type=int, default=DEFAULT_MIN_SHARED,
        help="짝과 공유한 문장이 이 수 이상이어야 판정한다(기본 %d). 상투어 한둘로 지우지 않기 위한 바닥."
             % DEFAULT_MIN_SHARED,
    )
    ap.add_argument(
        "--min-ratio", type=float, default=DEFAULT_MIN_RATIO,
        help="양방향 공유 비율의 큰 쪽이 이 값 이상이면 같은 원본으로 본다(기본 %.2f)"
             % DEFAULT_MIN_RATIO,
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="세기만 하고 파일은 쓰지 않는다. 문턱을 정하기 전에 영향을 본다.",
    )
    ap.add_argument(
        "--strict", action="store_true",
        help="누출이 하나라도 있으면 exit 1. 파이프라인이 조용히 지나가지 않게.",
    )
    args = ap.parse_args(argv)

    tp = ROOT / args.train
    if not tp.exists():
        print(f"[ERROR] train 없음: {tp}", file=sys.stderr)
        return 2
    train_rows = load(tp)
    train_hashes = {sha(text_of(r)) for r in train_rows}
    if args.hash_only:
        index = None
        print(f"[train] {tp.name}: {len(train_hashes)} 텍스트해시 · 문장 축 꺼짐(--hash-only)")
    else:
        index = TrainIndex(train_rows)
        print(f"[train] {tp.name}: {len(train_hashes)} 텍스트해시 · 문장 {index.n_types}종 "
              f"· 문턱 공유>={args.min_shared} 비율>={args.min_ratio:.2f}")

    any_leak = False
    for hpath in [h.strip() for h in args.holdout.split(",") if h.strip()]:
        hp = ROOT / hpath
        if not hp.exists():
            print(f"[SKIP] {hp} 없음", file=sys.stderr)
            continue
        recs = load(hp)

        clean: list[dict] = []
        leaked: list[tuple[dict, tuple[str, str]]] = []
        by_reason: Counter = Counter()
        for r in recs:
            hit = leak_reason(r, train_hashes, index,
                              min_shared=args.min_shared, min_ratio=args.min_ratio)
            if hit is None:
                clean.append(r)
            else:
                leaked.append((r, hit))
                by_reason[hit[0]] += 1
        if leaked:
            any_leak = True

        dist_before = dict(Counter(r.get("label") for r in recs))
        dist_after = dict(Counter(r.get("label") for r in clean))
        print(f"\n[{hp.name}] {len(recs)} → clean {len(clean)} (누출 {len(leaked)} 제거)")
        for code, n in by_reason.most_common():
            print(f"    {code:<14} {n}건")
        print(f"  라벨 before: {dist_before}")
        print(f"  라벨 after : {dist_after}")

        if args.dry_run:
            print("  [dry-run] 파일을 쓰지 않았다")
            for r, (code, why) in leaked[:8]:
                print(f"    - {str(r.get('doc_id'))[:36]:<38} {code}: {why}")
            if len(leaked) > 8:
                print(f"    ... 외 {len(leaked) - 8}건")
            continue

        out = hp.with_name(hp.stem + args.suffix)
        out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in clean),
                       encoding="utf-8")
        print(f"  → {out}")
        # 제거된 doc_id 를 **사유와 함께** 남긴다. 목록만 남기면 왜 뺐는지를 잃는다.
        if leaked:
            ids_out = hp.with_name(hp.stem + ".leaked_ids.txt")
            ids_out.write_text(
                "\n".join("%s\t%s\t%s" % (r.get("doc_id"), code, why)
                          for r, (code, why) in leaked),
                encoding="utf-8",
            )
            print(f"  제거 doc_id·사유 → {ids_out}")

    if not any_leak:
        print("\n[OK] 누출 없음 — 클린본은 원본과 동일")
    if any_leak and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
