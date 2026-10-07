# -*- coding: utf-8 -*-
"""audit_nis_ai_security 의 probe 가 '주석·산문'을 근거로 세고 있는지 전수로 센다.

왜(2026-09-08). M18 probe 가 문자열 "HttpOnly" 를 찾았는데 걸린 곳이
`api/_jwt_auth.py:324` 의 **"이 쿠키는 HttpOnly 가 아니다"** 라는 정정 주석이었다.
없는 보호를 있다고 판정해 발주기관 제출 문서에 허위 주장이 실려 나갔다.

audit_nis_ai_security 는 문자열을 그대로 찾고 대상에 .md·주석이 포함되므로,
**구현이 아니라 그것을 설명하는 글**에 걸릴 수 있다. 이 도구가 그 자리를 센다.

  히트가 전부 주석·산문     → 근거 인용이 구현을 가리키지 않는다(사람이 확인할 것)
  코드 줄 히트가 하나라도   → 통과
  히트 0건인데 항목은 통과  → any_mode 항목이다. 어느 근거가 빈 채 통과했는지 알린다

**주의 - '주석에 걸렸다'가 곧 '통제가 없다'는 아니다.** 2026-09-08 실행에서 나온
7건은 전부 통제가 실재했고 인용만 약했다(M02·M10·M11·M17·M23·M25). 위험한 것은
걸린 줄이 **주장을 부정하는 문장**일 때다(M18 이 그랬다). 그 판단은 사람이 한다.

사용:
    python scripts/audit_nis_ai_security.py --json out.json
    python scripts/audit_probe_evidence_quality.py out.json
"""
from __future__ import annotations

# 콘솔 출구를 UTF-8 로 고정한다 — cp949 콘솔에서 em dash 하나에 죽던 것을 막는다.
# 정본은 scripts/_cli_io.py 한 곳이다. 이 파일은 출력줄에 em dash 를 쓰면서 고정이
# 빠져 있었다(2026-09-09, test_scripts_console_encoding 이 잡았다).
try:  # 스크립트로 직접 실행 - scripts/ 가 sys.path 에 들어온다
    from _cli_io import force_utf8_stdio  # noqa: E402
except ImportError:  # 패키지로 import - 릴리스 번들의 import 폐쇄 검사가 이 경로다
    from scripts._cli_io import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def is_prose_line(path: Path, line: str) -> bool:
    """이 줄이 '구현'이 아니라 '설명'인가."""
    s = line.strip()
    suf = path.suffix.lower()
    if suf == ".md":
        return True  # 문서 전체가 산문
    if suf in {".py", ".sh", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".conf"}:
        return s.startswith("#")
    if suf in {".js"}:
        return s.startswith("//") or s.startswith("*") or s.startswith("/*")
    if suf in {".html"}:
        # 태그가 없으면 화면에 보이는 글, 즉 산문
        return "<" not in s
    return False


def main() -> int:
    jp = Path(sys.argv[1])
    data = json.loads(jp.read_text(encoding="utf-8"))

    risky: list[tuple] = []
    partial: list[tuple] = []

    for c in data["controls"]:
        if c.get("scope") != "code":
            continue
        for pr in c.get("probes", []):
            hits = pr.get("where") or []
            if not hits:
                partial.append((c["id"], c["title"], pr.get("label", "?")))
                continue
            prose, code = [], []
            for h in hits:
                # "poc/path/file.py:123" 형태
                loc = h if isinstance(h, str) else str(h)
                fp, _, ln = loc.rpartition(":")
                try:
                    p = REPO / fp
                    if not p.exists():
                        p = REPO / "poc" / fp
                    txt = p.read_text(encoding="utf-8", errors="replace").splitlines()
                    line = txt[int(ln) - 1]
                except Exception:
                    code.append((loc, "<읽기 실패>"))
                    continue
                (prose if is_prose_line(p, line) else code).append((loc, line.strip()[:110]))
            if prose and not code:
                risky.append((c["id"], c["title"], pr.get("label", "?"), prose))

    print("=" * 78)
    print(" 근거가 주석·산문뿐인 probe (M18 과 같은 유형)")
    print("=" * 78)
    if not risky:
        print("  없음")
    for cid, title, label, prose in risky:
        print(f"\n  [{cid}] {title}")
        print(f"     probe: {label}")
        for loc, line in prose:
            print(f"       {loc}")
            print(f"         > {line}")

    print()
    print("=" * 78)
    print(" 히트가 0건인 probe (항목은 통과했는데 이 근거만 비었다)")
    print("=" * 78)
    if not partial:
        print("  없음")
    for cid, title, label in partial:
        print(f"  [{cid}] {title} — probe '{label}' 히트 0")

    print()
    print(f"검사한 probe 수: {sum(len(c.get('probes',[])) for c in data['controls'] if c.get('scope')=='code')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
