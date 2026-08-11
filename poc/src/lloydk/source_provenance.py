"""모델을 만든 소스가 저장소에 있는가 — finalize 전에 한 번 센다.

왜 필요한가(2026-08-09 실측):

동일 weights 인데 dev200 F1 이 0.816(FNR_TS 0.50)에서 0.990 으로 뛰었다. 원인은 데이터가
아니라 **미커밋 pipeline.py 가드 118줄**이었다. 그 번들의 숫자는 저장소 어디에도 없는
코드가 만든 값이라 재현도, 검증도, 설명도 안 된다.

그리고 **그걸 막아 주는 장치가 없다.** 계약 해시(`serving_aggregation_contract`)를
차단자로 믿기 쉬운데 아니다:

- 계약은 `excluded_post_model_serving_rules` 로 rule-engine FNR override · source-prior cap ·
  metadata floor · escalation tau · ts/s1 tie-break 를 **명시적으로 제외**한다. 위 118줄은
  정확히 그 범주다.
- 계약 해시는 `_sha256_source_members` 로 집계 메서드 3개(`chunk_text`·`_encode_windows`·
  `_aggregate_chunk_probs`) 본문만 잡는다. 파일 전체 raw 해시는 CRLF·범위 문제로 이미
  폐기됐다(Windows 에서 finalize 한 번들이 Linux 컨테이너에서 영원히 로드 거부되던 결함).

즉 미커밋 서빙 가드로 만든 번들은 **로드 거부되지 않는다 — 조용히 로드되고 숫자만 틀린다.**
차단자가 있다고 믿는 쪽이 더 위험하다. 그래서 여기서 명시적으로 센다.

**추적되지 않은 파일도 dirty 로 본다.** 2026-08-12 실측: 학습 계보 전체를 만든 빌더 34개와
패키지 소스 모듈 하나(`proxy_eval_split.py`)가 커밋된 적이 없었다 — HEAD 이미지에는 그
모듈이 아예 없었다. "수정 없음"이 아니라 "존재하지 않음"이고, 재현 불가는 같다.

우회는 막지 않되 **흔적을 남긴다**: ``LLOYDK_ALLOW_DIRTY_FINALIZE=1`` 이면 통과하지만
provenance 에 ``bypassed: true`` 가 박히고 그대로 매니페스트에 실린다. 조용한 우회는 없다.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Sequence

# 모델 결과를 바꿀 수 있는 경로만 본다. datasets/ 본문은 정책상 추적 대상이 아니고
# (매니페스트 해시로 증적), artifacts/ 는 산출물이라 dirty 판정에서 제외한다 —
# 넣으면 finalize 가 자기 출력 때문에 항상 실패한다.
DEFAULT_WATCHED_PATHS: tuple[str, ...] = ("src", "scripts")

BYPASS_ENV = "LLOYDK_ALLOW_DIRTY_FINALIZE"


class SourceProvenanceError(RuntimeError):
    """소스 계보를 증명할 수 없다 — finalize 를 진행하면 안 되는 상태."""


def _git(root: Path, *args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "" if proc.returncode else "")


def git_provenance(
    root: str | Path,
    *,
    watched_paths: Sequence[str] = DEFAULT_WATCHED_PATHS,
) -> dict[str, object]:
    """(commit, dirty, dirty_paths, available) — 판정은 하지 않고 사실만 모은다.

    ``available=False`` 는 git 저장소가 아니거나 git 이 없는 경우다(예: 배포 서버의
    ``~/poc``). 그 상태는 "깨끗하다"가 아니라 **"증명할 수 없다"** 이므로 호출부는
    ``require_clean_source_tree`` 로 fail-closed 처리해야 한다.
    """
    root = Path(root).resolve()
    rc, _ = _git(root, "rev-parse", "--is-inside-work-tree")
    if rc != 0:
        return {
            "available": False,
            "commit": None,
            "dirty": True,
            "dirty_paths": [],
            "reason": "not a git work tree (or git unavailable)",
            "watched_paths": list(watched_paths),
        }

    rc, out = _git(root, "rev-parse", "HEAD")
    commit = out.strip() if rc == 0 else None

    # --untracked-files=all: 미추적 파일도 dirty 다. 디렉터리 단위로 뭉뚱그리면
    # (normal 모드) 새 빌더 하나가 조용히 묻힌다.
    rc, out = _git(
        root, "status", "--porcelain", "--untracked-files=all", "--", *watched_paths
    )
    if rc != 0:
        return {
            "available": False,
            "commit": commit,
            "dirty": True,
            "dirty_paths": [],
            "reason": f"git status failed: {out.strip()[:200]}",
            "watched_paths": list(watched_paths),
        }

    dirty_paths = [line[3:].strip() for line in out.splitlines() if line.strip()]
    return {
        "available": True,
        "commit": commit,
        "dirty": bool(dirty_paths),
        "dirty_paths": sorted(dirty_paths),
        "reason": "ok" if not dirty_paths else "uncommitted or untracked source files",
        "watched_paths": list(watched_paths),
    }


def require_clean_source_tree(
    root: str | Path,
    *,
    watched_paths: Sequence[str] = DEFAULT_WATCHED_PATHS,
    what: str = "finalize",
) -> dict[str, object]:
    """계보를 증명할 수 있을 때만 통과. 반환값은 그대로 매니페스트에 실을 provenance.

    우회(``LLOYDK_ALLOW_DIRTY_FINALIZE=1``)는 통과시키되 ``bypassed=True`` 를 남긴다 —
    연구 반복을 막지 않으면서, 그렇게 만든 번들은 매니페스트만 보면 구분된다.
    """
    provenance = git_provenance(root, watched_paths=watched_paths)
    provenance["bypassed"] = False
    if not provenance["dirty"]:
        return provenance

    if os.environ.get(BYPASS_ENV, "").strip().lower() in {"1", "true", "yes"}:
        provenance["bypassed"] = True
        provenance["bypass_env"] = BYPASS_ENV
        return provenance

    listed = list(provenance.get("dirty_paths") or [])
    shown = "\n  ".join(listed[:20]) or "(경로 목록 없음)"
    more = f"\n  … 외 {len(listed) - 20}건" if len(listed) > 20 else ""
    raise SourceProvenanceError(
        f"{what} 중단 — 모델을 만드는 소스 트리가 깨끗하지 않다"
        f"({provenance.get('reason')}).\n"
        f"  HEAD: {provenance.get('commit') or '알 수 없음'}\n"
        f"  감시 경로: {', '.join(provenance.get('watched_paths') or [])}\n"
        f"  미커밋·미추적:\n  {shown}{more}\n"
        "여기서 만든 번들의 숫자는 저장소에 없는 코드가 만든 값이라 재현·검증이 안 된다.\n"
        "계약 해시는 이걸 막지 못한다 — post-model 서빙 규칙을 명시적으로 제외하므로\n"
        "그런 번들은 로드 거부되지 않고 조용히 로드된다(2026-08-09 실측: 미커밋 가드\n"
        f"118줄이 dev200 F1 을 0.816 → 0.990 으로 바꿨다).\n"
        f"먼저 커밋할 것. 연구 반복이라 의도적으로 넘기려면 {BYPASS_ENV}=1 —\n"
        "그 경우 매니페스트에 bypassed=true 가 남는다."
    )
