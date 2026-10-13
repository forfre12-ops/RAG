#!/usr/bin/env python
"""무근거 게이트 수치(AUROC 0.58 · ECE 0.184 · 63.4% · 56.2% · conf>=0.7)가
리포 어디에 남아 있는지 세는 도구.

출처: poc/scripts/archive/gen_gate_whitepaper.py 의 하드코딩 상수.
그 값을 만든 측정 스크립트·로그·json 은 리포에 없다.

사용:
    PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe poc/scripts/audit_gate_numbers.py
    (리포 루트에서 실행)
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 세는 대상 텍스트 확장자 (분모를 여기서 고정한다)
TEXT_EXT = {
    ".py", ".md", ".html", ".htm", ".json", ".txt", ".yml", ".yaml",
    ".ts", ".tsx", ".js", ".jsx", ".sql", ".toml", ".ini", ".cfg", ".sh",
}

# 각 주장별 정규식. 숫자만 맞는 우연 일치를 거르려고 문맥어를 좁은 창(±40자) 안에서 요구한다.
# HTML 한 줄이 수만 자인 경우가 있어(예: const DATA=[...] 데이터 블롭) 줄 단위가 아니라
# "매치된 자리 주변"만 본다.
CLAIMS = {
    # AUROC 0.58 — conf 판별력
    "AUROC_0.58": re.compile(
        r"(?:AUROC|판별력|변별력|AUC)[^\n]{0,40}?0\.58(?![0-9])"
        r"|0\.58(?![0-9])[^\n]{0,40}?(?:AUROC|판별력|변별력)",
        re.IGNORECASE,
    ),
    # ECE 0.184 — golden500 보정오차
    "ECE_0.184": re.compile(
        r"(?:ECE|보정오차|보정 오차)[^\n]{0,40}?0\.184(?![0-9])"
        r"|0\.184(?![0-9])[^\n]{0,40}?(?:ECE|보정오차)",
        re.IGNORECASE,
    ),
    # 자동확정 정답률 63.4%
    "AUTO_63.4": re.compile(
        r"(?:자동확정|자동 확정|정답률|정밀도|auto)[^\n]{0,40}?63\.4(?![0-9])"
        r"|63\.4\s*%?[^\n]{0,40}?(?:자동확정|자동 확정|conf)",
        re.IGNORECASE,
    ),
    # 검수대상 정답률 56.2%
    "REVIEW_56.2": re.compile(
        r"(?:검수|정답률|review)[^\n]{0,40}?56\.2(?![0-9])"
        r"|56\.2\s*%?[^\n]{0,40}?(?:검수|conf)",
        re.IGNORECASE,
    ),
    # conf>=0.7 운영점 (2026-08-24 에 0.50 + 합의게이트로 대체됨)
    "CONF_0.7_GATE": re.compile(
        r"(?:conf|confidence|신뢰도|임계|threshold)[^\n]{0,30}?"
        r"(?:>=|≥|&gt;=|이상|미만|<|&lt;|=|:)?\s*0\.7[0]?(?![0-9])"
        r"|0\.7[0]?(?![0-9])[^\n]{0,30}?(?:임계|threshold|자동확정|검수 라우팅)",
        re.IGNORECASE,
    ),
}

# 맥락 키워드 — 주장 자체가 아니라 "이 지표가 언급된 자리". 총계에서 분리해 센다.
KEYWORDS = {
    "KW_AUROC": re.compile(r"AUROC", re.IGNORECASE),
    "KW_ECE": re.compile(r"\bECE\b|기대보정오차"),
}

# 과거형 + 날짜 표기 여부 (방어 가능한 서술인지 가른다)
PAST_TENSE = re.compile(
    r"(20\d\d[-./]\d\d[-./]\d\d|20\d\d년\s*\d+월)[^\n]{0,60}?"
    r"(측정|시점|당시|기준|계측|실측)"
    r"|(측정|계측|실측)[^\n]{0,30}?(당시|시점|했다|였다|됐다)"
    r"|당시\s*측정|측정\s*당시|폐기|무근거|근거\s*없|출처\s*없|재현\s*불가|미검증"
)


def classify_surface(rel: str) -> str:
    """(a) 발주기관 제출본 / (b) 내부 문서 / (c) 코드·스크립트"""
    p = rel.replace("\\", "/")
    if p.endswith(".py") or "/scripts/" in p or "/src/" in p or "/tests/" in p:
        return "c-코드/스크립트"
    if p.startswith("doc/result/"):
        return "a-발주기관 제출본"
    if "감리정본" in p or "KL_AI자료" in p:
        return "a-발주기관 제출본"
    return "b-내부 문서"


def main() -> int:
    # -z 필수: 한글 파일명을 git 이 8진 이스케이프로 따옴표 씌워 내보내면
    # 경로가 안 열려 조용히 빠진다(doc/result/ 전부가 그렇게 누락됐었다).
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True
    )
    files = [f for f in out.stdout.decode("utf-8").split("\0") if f.strip()]
    scanned, hits = 0, []

    for rel in files:
        path = ROOT / rel
        if path.suffix.lower() not in TEXT_EXT:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        scanned += 1
        # 줄번호는 오프셋으로 환산한다(한 줄이 수만 자인 HTML 이 있어 줄 단위로 못 본다).
        line_starts = [0]
        for m in re.finditer(r"\n", text):
            line_starts.append(m.end())

        def lineno(pos: int) -> int:
            lo, hi = 0, len(line_starts) - 1
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if line_starts[mid] <= pos:
                    lo = mid
                else:
                    hi = mid - 1
            return lo + 1

        for name, rx in {**CLAIMS, **KEYWORDS}.items():
            for m in rx.finditer(text):
                a, b = m.start(), m.end()
                ctx = text[max(0, a - 300): b + 300]
                snippet = re.sub(r"<[^>]+>", " ", text[max(0, a - 90): b + 90])
                snippet = re.sub(r"\s+", " ", snippet).strip()
                hits.append(
                    {
                        "file": rel,
                        "line": lineno(a),
                        "claim": name,
                        "kind": "keyword" if name in KEYWORDS else "claim",
                        "surface": classify_surface(rel),
                        "past_tense": bool(PAST_TENSE.search(ctx)),
                        "match": m.group(0)[:120],
                        "text": snippet[:260],
                    }
                )

    report = {
        "denominator_files_tracked": len(files),
        "denominator_files_scanned_text": scanned,
        "total_hits": len(hits),
        "hits": hits,
    }
    dest = ROOT / "poc" / "reports" / "gate_numbers_audit.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"tracked={len(files)} scanned_text={scanned} hits={len(hits)}")
    by_claim: dict[str, int] = {}
    by_file: dict[str, set] = {}
    for h in hits:
        by_claim[h["claim"]] = by_claim.get(h["claim"], 0) + 1
        by_file.setdefault(h["file"], set()).add(h["claim"])
    print("\n-- claim별 --")
    for k, v in sorted(by_claim.items(), key=lambda x: -x[1]):
        print(f"{v:5d}  {k}")
    print(f"\n-- 파일 수: {len(by_file)} --")
    for f in sorted(by_file):
        print(f"{classify_surface(f):20s} {f}  {sorted(by_file[f])}")
    print(f"\nwrote {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
