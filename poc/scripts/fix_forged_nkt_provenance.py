"""정본 gold의 위조 외부권위 provenance 교정 (2026-07-03 감사, 일회성 마이그레이션).

배경
----
augment_high_risk_gold.py(구버전)가 지정근거(legal_reference) 없는 손작성 시나리오
6건에 label_source=nkt_designated 를 부여해 정본과 no-human proxy의 external_authority
버킷("정부지정 = 사람서명 없이도 진짜 정답")을 오염시켰다. 내용도 M&A 가치평가 메모·
RLHF 모델 등 국가핵심기술 §9와 무관한 것이 포함된다. 게다가 requires_human_signoff=False
로 서명 면제까지 위조 provenance에 얹혀 있었다.

교정 규칙(결정적)
-----------------
label_source == "nkt_designated" AND legal_reference 부재(빈 값)인 레코드만:
  - label_source  → "curated_scenario"  (손작성 시나리오 — 외부권위 주장 제거)
  - requires_human_signoff → True       (서명 면제 회수)
  - notes 에 교정 사유 추가(감사 추적)
label(등급 자체)은 바꾸지 않는다 — 시나리오의 구성 의도 라벨은 별개 문제이고,
이 교정은 provenance(라벨 권위 주장)만 바로잡는다.

수정 전 원본은 <파일>.bak-forged-nkt 로 백업한다. --apply 없이 실행하면 대상만 보고.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

try:  # Windows cp949 콘솔 대응
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

CORRECTION_NOTE = (
    "provenance corrected 2026-07-03: nkt_designated 주장에 legal_reference 없음"
    "(augment_high_risk_gold 유래 위조 외부권위) → curated_scenario로 강등, 서명 면제 회수"
)


def is_forged(rec: dict) -> bool:
    return (
        rec.get("label_source") == "nkt_designated"
        and not str(rec.get("legal_reference") or "").strip()
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="datasets/gold_real/classification_gold.jsonl")
    ap.add_argument("--apply", action="store_true", help="실제로 파일을 고쳐 쓴다(기본: 보고만)")
    args = ap.parse_args()

    path = Path(args.gold)
    if not path.exists():
        print(f"[fix-forged-nkt] not found: {path}")
        return 2

    lines = path.read_text(encoding="utf-8").splitlines()
    out_lines: list[str] = []
    fixed: list[dict] = []
    for line in lines:
        if not line.strip() or line.startswith("#"):
            out_lines.append(line)
            continue
        rec = json.loads(line)
        if is_forged(rec):
            rec["label_source"] = "curated_scenario"
            rec["requires_human_signoff"] = True
            base_notes = str(rec.get("notes") or "").strip()
            rec["notes"] = f"{base_notes} | {CORRECTION_NOTE}" if base_notes else CORRECTION_NOTE
            fixed.append(rec)
            out_lines.append(json.dumps(rec, ensure_ascii=False))
        else:
            out_lines.append(line)

    print(f"[fix-forged-nkt] {path}: 대상 {len(fixed)}건")
    for r in fixed:
        title = (r.get("text") or "").splitlines()[0][:60]
        print(f"  - {r.get('doc_id','?')[:16]}  {r.get('label')}  {title}")

    if not fixed:
        return 0
    if not args.apply:
        print("[fix-forged-nkt] dry-run — 적용하려면 --apply")
        return 0

    backup = path.with_suffix(path.suffix + ".bak-forged-nkt")
    shutil.copy2(path, backup)
    path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print(f"[fix-forged-nkt] applied. backup={backup}")

    # 자기검증: 적용 후 위조 패턴 잔존 0
    remaining = sum(
        1
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#") and is_forged(json.loads(line))
    )
    assert remaining == 0, f"self-check failed: forged nkt still present ({remaining})"
    print("[fix-forged-nkt] self-check OK: forged nkt remaining = 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
