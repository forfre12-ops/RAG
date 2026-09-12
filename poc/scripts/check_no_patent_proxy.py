"""[가드] 학습셋에 공개특허-프록시 과라벨이 유입됐는지 검사 — 재현가능 트립와이어.

배경(patent-proxy pollution): 판례-프록시([[court]])와 동형의 오염. 공개특허공보(공개된 문서)는
비공지성 결여로 정의상 S3인데, 과거 step3 빌드(labeled_p1_retrain_v4_step3)는 공개특허 7,200건을
S1(5,400)·TS(1,800)로 라벨해 넣었다(source=공개특허, label_source=patent_proxy_nkt). 이는 모델에
'특허 문체 = 고등급' shortcut을 가르치는 오염원이다. 배포본 v4_clean·v5_clean은 step3를 배제해
현재는 깨끗하나(2026-07-26 감사 확인), **미래 재빌드가 step3/patent_proxy를 재유입시키는 것을 막는
빌드타임 가드가 없었다**. 이 스크립트가 그 가드다(court 디오염 --check 와 동일 패턴).

선정 술어(공개특허 프록시 과라벨):
  - label_source == 'patent_proxy_nkt'  (프록시 태그 — 명시적·모호성 없음), 또는
  - source 가 '공개특허' 또는 '특허' 로 시작(공개 특허공보)
  AND label ∈ {'TS','S1','S2'}          (공개문서를 고등급으로 과상향)

사용:
  python scripts/check_no_patent_proxy.py                                   # v5_clean train 검사(--check)
  python scripts/check_no_patent_proxy.py --path <train.jsonl>              # 임의 학습셋 검사
  python scripts/check_no_patent_proxy.py --path ... --report              # 통계만(exit 0)
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from collections import Counter
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_HIGH = frozenset({"TS", "S1", "S2"})
_PATENT_PREFIXES = ("공개특허", "특허")


def is_patent_proxy_overlabel(r: dict) -> bool:
    """공개특허(공개문서)를 고등급으로 과라벨한 레코드인가 — 결정론적(저장필드만)."""
    if r.get("label") not in _HIGH:
        return False
    if r.get("label_source") == "patent_proxy_nkt":
        return True
    src = str(r.get("source") or "")
    return any(src.startswith(p) for p in _PATENT_PREFIXES)


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]


def main() -> int:
    ap = argparse.ArgumentParser(description="공개특허-프록시 과라벨 유입 가드(트립와이어).")
    ap.add_argument("--path", default="datasets/labeled_p1_v5_clean/train.jsonl")
    ap.add_argument("--report", action="store_true", help="통계만 출력하고 exit 0")
    args = ap.parse_args()

    path = Path(args.path)
    rows = _load(path)
    if not rows:
        print(f"[patent-check] 파일 없음/빈 파일: {path}", file=sys.stderr)
        return 2

    hits = [r for r in rows if is_patent_proxy_overlabel(r)]
    by_label = Counter(r["label"] for r in hits)
    print(f"[patent-check] {path}: 총 {len(rows)}행, 공개특허-프록시 과라벨 {len(hits)}건 {dict(by_label)}")

    if args.report:
        return 0
    if hits:
        print("[patent-check] FAIL — 공개특허(공개문서)를 고등급으로 과라벨한 행이 있습니다.", file=sys.stderr)
        print("        공개 특허공보는 비공지성 결여로 정의상 S3. step3/patent_proxy_nkt 재유입 여부 확인.", file=sys.stderr)
        return 1
    print("[patent-check] OK — 공개특허-프록시 과라벨 0건.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
