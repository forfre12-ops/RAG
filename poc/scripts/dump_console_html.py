#!/usr/bin/env python
"""서버가 렌더하는 콘솔 화면을 파일로 떠 둔다 — e2e 하니스가 그것을 띄운다.

왜 필요한가.
  `/api/v1/golden/candidates/manage.html`(검증문서 후보 관리)은 정적 파일이 아니라
  파이썬이 문자열로 만들어 내려 주는 화면이다. node 하니스는 파이썬을 부를 수 없어
  그 화면을 열 방법이 없었고, 그래서 콘솔 6면 중 이 한 면만 **실행 시험이 0건**이었다
  (test_console_nav 가 링크 목록만 본다).

  여기서 렌더 결과를 떠 두면 하니스가 그것을 서빙해 실제로 띄우고 눌러 볼 수 있다.
  뜬 판이 낡으면 시험이 실제와 다른 것을 보게 되므로, 파이썬 쪽
  `tests/test_e2e_console_rendered_snapshot.py` 가 지금 렌더와 대조해 어긋나면 깨진다.

사용:
    python scripts/dump_console_html.py                # 커밋된 자리에 다시 뜬다
    python scripts/dump_console_html.py --out DIR      # 다른 자리에 (pytest 가 임시 폴더로 쓴다)
    make console-e2e-snapshot                          # 첫 번째와 같은 것

pytest 로 돌릴 때는 시험이 임시 폴더에 **그 자리에서 다시 떠서** 하니스에 넘긴다
(`KOIPA_E2E_RENDERED_DIR`). 그래서 렌더러를 고친 직후에도 시험은 실제 화면을 본다.
커밋된 판은 `node run.mjs` 단독 실행용 편의본이다.

⚠ 렌더러가 설정을 읽는 화면(login.html 의 prefill 토큰 등)은 여기서 뜨지 않는다 —
  환경마다 달라져 스냅샷이 성립하지 않는다. 그 화면들은 전용 파이썬 시험이 이미 본다.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

OUT_DIR = _ROOT / "tests" / "e2e_console" / "lib" / "rendered"

# (파일명, 렌더 함수 이름) — 인자 없이 결정론적으로 같은 문자열을 내는 것만 넣는다.
TARGETS = [("manage.html", "_render_specledger_gold_console_html")]


def render(name: str) -> str:
    from koipa.api import golden as golden_api

    fn = getattr(golden_api, name)
    return fn()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out_dir = OUT_DIR
    if "--out" in argv:
        out_dir = Path(argv[argv.index("--out") + 1])
    out_dir.mkdir(parents=True, exist_ok=True)
    changed = []
    for filename, fn_name in TARGETS:
        html = render(fn_name)
        path = out_dir / filename
        old = path.read_text(encoding="utf-8") if path.exists() else None
        if old != html:
            path.write_text(html, encoding="utf-8", newline="")
            changed.append(filename)
        print(f"{filename}: {len(html):,}자 {'(갱신)' if old != html else '(변동 없음)'}")
    if changed:
        # cp949 콘솔에서 깨지므로 em dash 를 쓰지 않는다.
        print("\n갱신된 파일이 있다. 함께 커밋할 것: " + ", ".join(changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
