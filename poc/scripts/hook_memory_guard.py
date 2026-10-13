#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PostToolUse hook — 메모리 파일을 고친 직후 구조 검사를 돌린다.

왜 있나
    2026-09-14 메모리 전수조사에서, 틀린 것의 대부분이 "본문은 정정됐는데 요약·색인줄·
    명령블록만 옛날"이었다. 사람이 규율로 막는 데 한계가 있어 기계가 잡게 했다.

무엇을 하나
    stdin 으로 오는 hook JSON 에서 고친 파일 경로를 읽는다.
    그 경로가 이 프로젝트의 메모리 폴더 안 .md 가 아니면 **아무것도 하지 않는다**(exit 0).
    맞으면 audit_memory_health.py 를 돌리고, 구조 결함이 있을 때만 결과를 돌려준다.

출력 계약
    결함 없음 → 조용히 exit 0 (hook 이 안 보인다)
    결함 있음 → systemMessage(사용자에게) + additionalContext(모델에게) 를 담은 JSON.
                차단하지는 않는다 — 편집을 되돌리는 것이 아니라 이어서 고치게 하는 것이 목적이다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
POC = os.path.dirname(HERE)
AUDIT = os.path.join(HERE, "audit_memory_health.py")
MEMORY_MARK = os.path.join("projects", "f--antigravity-rag", "memory").replace("\\", "/")


def _python() -> str:
    for cand in (os.path.join(POC, ".venv", "Scripts", "python.exe"),
                 os.path.join(POC, ".venv", "bin", "python")):
        if os.path.exists(cand):
            return cand
    return sys.executable


def _emit(obj: dict) -> None:
    """hook 응답을 낸다.

    ⚠ 윈도 콘솔은 cp949 라 한글·em dash 를 그대로 쓰면 UnicodeEncodeError 로 죽는다.
    2026-09-14 pipe-test 에서 실제로 죽었다(koipa-rename 메모리의 그 함정과 같은 것).
    그래서 ① stdout 을 utf-8 로 다시 열고 ② JSON 은 ensure_ascii 로 이스케이프한다.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.stdout.write(json.dumps(obj, ensure_ascii=True))
    sys.stdout.write("\n")
    sys.stdout.flush()


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # 입력을 못 읽으면 조용히 넘어간다 — hook 이 작업을 막으면 안 된다

    ti = payload.get("tool_input") or {}
    tr = payload.get("tool_response") or {}
    path = (ti.get("file_path") or tr.get("filePath") or "")
    if not isinstance(path, str):
        return 0
    norm = path.replace("\\", "/")
    if MEMORY_MARK not in norm or not norm.endswith(".md"):
        return 0  # 메모리 파일이 아니면 아무 일도 안 한다

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        r = subprocess.run([_python(), AUDIT], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, timeout=120)
    except Exception as exc:
        _emit({"systemMessage": f"메모리 검사기를 돌리지 못했습니다: {exc}"})
        return 0

    if r.returncode == 0:
        return 0  # 구조 결함 없음 — 조용히

    out = r.stdout or ""
    # 결함이 있는 절(A~E, I)만, 그중에서도 **0건이 아닌 절만** 추린다.
    # F·G·H 는 경고성이라 뺀다 — 매번 뜨면 진짜 결함이 묻힌다.
    WANT = ("== A.", "== B.", "== C.", "== D.", "== E.", "== I.")
    blocks: list[list[str]] = []
    cur: list[str] | None = None
    for line in out.splitlines():
        if line.startswith("== "):
            if cur:
                blocks.append(cur)
            cur = [line] if line.startswith(WANT) else None
            continue
        if cur is not None:
            cur.append(line)
    if cur:
        blocks.append(cur)

    def empty(b: list[str]) -> bool:
        head = b[0]
        if "0건" in head:
            return True
        return any(ln.strip() in ("소계 0", "소계 0건") for ln in b)

    keep = [ln for b in blocks if not empty(b) for ln in b]
    detail = "\n".join(keep).strip() or out[-1500:]

    msg = ("메모리 구조 결함이 생겼습니다 — 방금 고친 파일 때문일 수 있습니다.\n"
           "poc/scripts/audit_memory_health.py 로 다시 확인하세요.")
    _emit({
        "systemMessage": msg,
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": (
                "메모리 건강 검사가 구조 결함을 잡았습니다. 방금 편집한 메모리 파일을 이어서 "
                "고치세요(깨진 [[링크]]·MEMORY.md 색인 불일치·frontmatter·대상 아닌 옛 서버 주소).\n"
                "정정을 적었으면 description·MEMORY.md 줄·코드 블록까지 같이 고쳐야 합니다.\n\n"
                + detail
            ),
        },
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
