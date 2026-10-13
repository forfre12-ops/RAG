"""동결 번들의 INTEGRITY.sha256 재봉인 — 브랜드 리네임처럼 **전 번들 일괄 편집** 뒤에만 쓴다.

⚠ 동결본은 원래 다시 봉인하는 물건이 아니다. `build_supervision_bundle.py` 에 재봉인
옵션이 없는 것은 설계이지 누락이 아니다 — 한 번 낸 것은 그대로 두는 게 원칙이다.

그런데 2026-08-12 발주처 지시로 lloydk → koipa 브랜드 리네임을 **과거 릴리스 스냅샷까지
포함해** 수행했다(doc/releases/* 144파일). 그 결과 13벌의 무결성 매니페스트가 전부
[FAIL] 이 됐다. 세 선택지가 있었다:

    그대로 둠   13벌 영구 FAIL. 나중에 감리가 검증하면 변조로 보이고 **이유를 모른다**
    되돌림      무결성 복구 + 과거 기록 보존. 단 lloydk.co.kr 이 144파일에 부활 — 지시 위반
    재봉인      검증 통과. 단 "그때 보낸 것"의 해시가 아니게 된다  ← 선택

"어떠한 곳에도 lloydk 가 들어가면 안 된다"와 "동결본이 검증돼야 한다"는 동시에 성립하지
않는다. 지시를 우선하되 **언제 왜 다시 봉인했는지를 매니페스트에 남긴다** — 해시만 바꾸고
기록을 안 남기면 그게 진짜 변조다. 원본 내용과 해시는 git 이력(태그 pre-koipa-rename)에
그대로 있다.

선례: 기존 매니페스트에도 "소급 생성(2026-08-02) — 원 동결 시점 매니페스트 부재분 보강"
이라는 note 가 있다. 같은 방식이다.

사용:
    python scripts/reseal_release_integrity.py --check       # 어느 릴리스가 깨졌는지만
    python scripts/reseal_release_integrity.py --apply
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RELEASES = REPO / "doc" / "releases"
NAME = "INTEGRITY.sha256"
NOTE = (
    "재봉인(2026-08-12) — 발주처 지시로 lloydk→koipa 브랜드 리네임을 과거 릴리스까지 "
    "적용해 파일 내용이 바뀌었다. 원본 내용·해시는 git 태그 pre-koipa-rename 에 보존. "
    "이 매니페스트는 리네임 **이후** 상태의 해시이며 최초 동결 시점의 해시가 아니다."
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def head_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(REPO),
            capture_output=True, text=True, timeout=30,
        )
        return out.stdout.strip() if out.returncode == 0 else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def entries(bundle: Path) -> list[tuple[str, Path]]:
    out = []
    for path in sorted(bundle.rglob("*")):
        if not path.is_file() or path.name == NAME:
            continue
        out.append((path.relative_to(bundle).as_posix(), path))
    return out


def verify(bundle: Path) -> tuple[int, int]:
    """(불일치, 전체) — 기존 매니페스트 기준."""
    manifest = bundle / NAME
    if not manifest.is_file():
        return (0, 0)
    recorded = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("  ", 1)
        if len(parts) == 2:
            recorded[parts[1]] = parts[0]
    bad = 0
    for rel, path in entries(bundle):
        if recorded.get(rel) and recorded[rel] != sha256_file(path):
            bad += 1
    return (bad, len(recorded))


def reseal(bundle: Path, commit: str) -> int:
    manifest = bundle / NAME
    old = manifest.read_text(encoding="utf-8").splitlines() if manifest.is_file() else []
    header, seen_note = [], False
    for line in old:
        if not line.startswith("#"):
            break
        if line.startswith("# last_commit"):
            header.append(f"# last_commit  : {commit}")
            continue
        if line.startswith("# note"):
            header.append(line)
            header.append(f"# note         : {NOTE}")
            seen_note = True
            continue
        if line.startswith("# [번들 파일"):
            if not seen_note:
                header.append(f"# note         : {NOTE}")
            header.append(line)
            break
        header.append(line)
    rows = entries(bundle)
    body = [f"{sha256_file(p)}  {rel}" for rel, p in rows]
    manifest.write_text("\n".join(header + [""] + body) + "\n", encoding="utf-8")
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="없으면 검사만 한다")
    args = parser.parse_args(argv)

    commit = head_commit()
    total_bad = 0
    for bundle in sorted(RELEASES.iterdir()):
        if not bundle.is_dir() or not (bundle / NAME).is_file():
            continue
        bad, n = verify(bundle)
        total_bad += bad
        if args.apply:
            written = reseal(bundle, commit)
            print(f"  재봉인 {bundle.name}: 불일치 {bad}/{n} → {written}개 해시 갱신")
        else:
            mark = "FAIL" if bad else "OK"
            print(f"  [{mark}] {bundle.name}: 불일치 {bad}/{n}")
    if not args.apply:
        # ⚠ 콘솔로 나가는 문자열에 em dash(U+2014)를 쓰지 말 것. cp949 콘솔에서
        # UnicodeEncodeError 로 죽는다(이 프로젝트에서 세 번 당했다 — --help, 채점기, 여기).
        # 주석·docstring 은 출력되지 않으므로 상관없다.
        print(f"\n총 불일치 {total_bad}건. 재봉인하려면 --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
