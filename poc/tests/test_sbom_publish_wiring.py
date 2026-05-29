"""D4: SBOM 자동 갱신 hook 정적 검증.

- Makefile에 sbom-publish 타깃이 .PHONY와 본문에 정의돼 있는지
- CI poc-ci.yml에 doc/21 자동 갱신 + artifact 업로드 단계가 있는지
- render_sbom_html.py 스크립트가 존재하는지
"""

from __future__ import annotations

import re
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
ROOT = POC.parent


def test_render_sbom_html_script_exists():
    assert (POC / "scripts" / "render_sbom_html.py").exists()


def test_makefile_has_sbom_publish_target():
    text = (POC / "Makefile").read_text(encoding="utf-8")
    # .PHONY에 등록
    assert re.search(r"\.PHONY:.*sbom-publish", text, re.MULTILINE | re.DOTALL)
    # 타깃 본문 존재
    assert re.search(r"^sbom-publish:.*licenses$", text, re.MULTILINE)
    # render_sbom_html 호출
    assert "render_sbom_html.py" in text
    # doc/21 출력
    assert "doc/21_OSS_SBOM_보고서.html" in text


def test_ci_workflow_has_doc21_render_step():
    text = (ROOT / ".github" / "workflows" / "poc-ci.yml").read_text(encoding="utf-8")
    assert "Render doc/21 OSS SBOM HTML" in text
    assert "render_sbom_html.py" in text
    # artifact에 doc/21 포함
    assert "doc/21_OSS_SBOM_보고서.html" in text


def test_ci_does_not_auto_commit_doc21():
    """자동 commit은 의도적으로 안 함 — 운영자 PR 머지로 진실 소스 유지."""
    text = (ROOT / ".github" / "workflows" / "poc-ci.yml").read_text(encoding="utf-8")
    # git add + commit doc/21 같은 자동 커밋 패턴이 없는지
    assert "git commit" not in text or "doc/21" not in text.split("git commit")[1][:200] if "git commit" in text else True
    # actions/upload-artifact는 사용 — 검수용 다운로드 가능
    assert "actions/upload-artifact" in text
