"""제출본 체크리스트는 코드에서 다시 만들어지는 상태로 유지된다.

왜(2026-09-08). 이 문서(AI시스템_보안대책_체크리스트.html)는 손으로 쓴 것이 아니라
build_nis_checklist_doc.py 가 audit_nis_ai_security.py 의 기계 판정에 설명을 붙여
**생성**한다. 생성기에는 `--check` 가 있어 문서가 코드와 어긋나면 알려 준다.

그런데 그 `--check` 를 아무 데도 부르지 않고 있었다. 그래서 이런 일이 났다:

    2026-09-08  문서만 손으로 고쳤다. 재생성하면 지워질 상태였는데 아무도 몰랐다.
                (--check 를 직접 돌려 보고서야 exit=1 로 드러났다)

같은 리포의 다른 문서에서 더 나쁜 형태가 확인됐다 - 생성기가 archive/ 로 옮겨져
아무도 안 돌리게 된 뒤, 문서만 몇 달 전 값에 멈춘 채 발주기관에 나갔다. 생성기는
'있는 것'만으로는 부족하고 **돌아야** 한다.

이 시험이 잠그는 것:

  ① 지금 리포의 문서가 생성기 출력과 같다 (손으로 고친 것이 남아 있지 않다)
  ② 근거 판정이 바뀌면 문서도 따라 바뀌어야 한다는 사실이 CI 에서 드러난다

깨졌을 때 할 일은 하나다:

    cd poc && python scripts/build_nis_checklist_doc.py

⚠ 이 시험은 문서 '내용이 옳은가'를 보지 않는다. 코드와 어긋나지 않았는지만 본다.
   내용의 근거는 audit_nis_ai_security.py 가, 근거 인용의 질은
   audit_probe_evidence_quality.py 가 본다.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_POC = Path(__file__).resolve().parents[1]
_GEN = _POC / "scripts" / "build_nis_checklist_doc.py"
_OUT = (_POC.parent / "doc" / "result" / "KL_AI자료_2026-08_미첨부문서"
        / "AI시스템_보안대책_체크리스트.html")


@pytest.mark.skipif(not _GEN.exists(), reason="생성기가 없다")
def test_checklist_doc_matches_generator() -> None:
    """문서가 생성기 출력과 같아야 한다 - 다르면 손으로 고친 것이 섞여 있다."""
    assert _OUT.exists(), (
        f"제출본 문서가 없다: {_OUT}\n"
        "  cd poc && python scripts/build_nis_checklist_doc.py"
    )

    proc = subprocess.run(
        [sys.executable, str(_GEN), "--check"],
        cwd=str(_POC), capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, (
        "제출본 체크리스트가 코드와 어긋난다. 문서를 손으로 고쳤거나, 근거 판정이\n"
        "바뀌었는데 문서를 다시 만들지 않은 것이다. 고치는 법:\n"
        "  cd poc && python scripts/build_nis_checklist_doc.py\n"
        f"--- 생성기 출력 ---\n{proc.stdout}{proc.stderr}"
    )


@pytest.mark.skipif(not _OUT.exists(), reason="문서가 아직 없다")
def test_checklist_doc_has_no_escaped_tags() -> None:
    """태그 글자가 화면에 그대로 보이면 안 된다.

    생성기가 사람이 쓴 문안을 한 번 더 escape 해서 발주기관 화면에
    '<strong>' 이 글자로 찍힌 적이 있다(M28, 2026-09-08).
    """
    text = _OUT.read_text(encoding="utf-8")
    for bad in ("&lt;strong&gt;", "&lt;br", "&amp;middot;", "&amp;mdash;"):
        assert bad not in text, (
            f"이중 이스케이프가 남아 있다: {bad!r}\n"
            "사람이 쓴 문안(evidence·caveat·detail)에 _esc() 를 걸지 않았는지 볼 것."
        )
