# -*- coding: utf-8 -*-
"""서버를 다시 세울 때 **git 에 없는 데이터**를 내보낸다 — 옮긴 뒤 검증까지.

왜 필요한가(2026-09-07). OS 를 Rocky 로 바꾸며 테스트 서버를 다시 세운다. 그런데
클론만으로는 아무것도 못 돌린다 - 실측:

    poc/datasets    실제 16,508 파일(3.4GB)  ·  git 추적 234개   <- 98.6% 가 git 밖
    poc/artifacts   실제  1,726 파일( 331GB) ·  git 추적   0개   <- 전부 gitignore

  골든셋 검수 후보(datasets/golden_review/ 1,336파일)·평가셋·학습셋이 여기 있다.
  폐쇄망 번들(build_offline_bundle.py)은 **설치용**이라 코드·이미지·모델·의존성만 담고
  데이터는 담지 않는다. 그 자리를 이 스크립트가 채운다.

무엇을 담나
  · datasets/ 아래에서 **git 이 추적하지 않는** 파일 전부.
    선별하지 않는다 - "이건 안 써도 되겠지"는 옮긴 뒤엔 되돌릴 수 없는 판단이다.
  · artifacts/ 는 **담지 않는다**(331GB, 대부분 학습 체크포인트). 배포 모델 한 벌은
    번들이 이미 담는다(build_offline_bundle --classifier-model-dir).

왜 그냥 복사가 아닌가
  복사는 도착지에서 맞는지 알 수 없다. 파일마다 sha256 을 적은 MANIFEST.json 을 함께
  내고, 받는 쪽에서 --verify 로 대조한다. 전송 중 잘린 파일이 조용히 섞이면 나중에
  "그 문서가 원래 그랬나"를 가릴 방법이 없다.

사용:
    # 내보내기(로컬)
    python scripts/export_migration_data.py --out F:/antigravity/rag/_migration/data

    # 받는 쪽에서 검증
    python scripts/export_migration_data.py --verify /opt/koipa/_migration/data

    # 무엇이 담기는지만 보기
    python scripts/export_migration_data.py --out ... --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

try:  # 스크립트로 직접 실행 - scripts/ 가 sys.path 에 들어온다
    from _cli_io import force_utf8_stdio  # noqa: E402
except ImportError:  # 패키지로 import - 릴리스 번들의 import 폐쇄 검사가 이 경로다
    from scripts._cli_io import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

_POC = Path(__file__).resolve().parents[1]
# 기본 대상. artifacts/ 는 뺀다 - 331GB 이고 배포 모델은 번들이 담는다.
DEFAULT_ROOTS = ("datasets",)
MANIFEST_NAME = "MANIFEST.json"
_SKIP_DIRS = {"__pycache__", ".ipynb_checkpoints"}


def log(msg: str) -> None:
    print(f"[export][{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _tracked_files(root: str) -> set[str]:
    """git 이 추적하는 파일 목록(poc 기준 상대경로).

    추적본은 클론으로 따라오므로 옮길 필요가 없다. git 이 없거나 실패하면 **아무것도
    추적하지 않는 것으로 보고 전부 담는다** - 빠뜨리는 쪽보다 더 담는 쪽이 안전하다.
    """
    try:
        proc = subprocess.run(
            ["git", "ls-files", root],
            cwd=str(_POC), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300,
        )
        if proc.returncode != 0:
            log(f"git ls-files 실패 - 전부 담는다: {proc.stderr[-200:]}")
            return set()
        return {line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()}
    except Exception as exc:  # noqa: BLE001
        log(f"git 조회 실패 - 전부 담는다: {exc}")
        return set()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def collect(roots: tuple[str, ...]) -> list[Path]:
    """옮길 파일 목록 — git 밖의 것만."""
    out: list[Path] = []
    for root in roots:
        base = _POC / root
        if not base.exists():
            log(f"없는 경로 건너뜀: {root}")
            continue
        tracked = _tracked_files(root)
        for path in base.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            rel = path.relative_to(_POC).as_posix()
            if rel in tracked:
                continue
            out.append(path)
    return sorted(out)


def export(files: list[Path], out_dir: Path, *, dry_run: bool) -> dict:
    total = sum(f.stat().st_size for f in files)
    log(f"대상 {len(files):,}파일 · {total / 1e9:.2f}GB")
    if dry_run:
        by_top: dict[str, int] = {}
        for f in files:
            key = f.relative_to(_POC).parts[1] if len(f.relative_to(_POC).parts) > 1 else "(루트)"
            by_top[key] = by_top.get(key, 0) + 1
        for key, n in sorted(by_top.items(), key=lambda kv: -kv[1])[:20]:
            log(f"   {n:>6}  {key}")
        return {"dry_run": True, "files": len(files), "bytes": total}

    out_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    done = 0
    for path in files:
        rel = path.relative_to(_POC).as_posix()
        dest = out_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        entries.append({"path": rel, "size": path.stat().st_size, "sha256": _sha256(path)})
        done += 1
        if done % 1000 == 0:
            log(f"   {done:,}/{len(files):,}")

    manifest = {
        "source": str(_POC),
        "files": len(entries),
        "bytes": total,
        "entries": entries,
    }
    (out_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    log(f"완료 {len(entries):,}파일 → {out_dir}")
    log(f"검증: python scripts/export_migration_data.py --verify {out_dir}")
    return manifest


def verify(out_dir: Path) -> int:
    """받은 쪽에서 대조한다. 하나라도 어긋나면 non-zero — 조용한 손상 금지."""
    manifest_path = out_dir / MANIFEST_NAME
    if not manifest_path.exists():
        log(f"MANIFEST 가 없다: {manifest_path}")
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    missing: list[str] = []
    corrupt: list[str] = []
    for i, entry in enumerate(manifest["entries"], 1):
        path = out_dir / entry["path"]
        if not path.exists():
            missing.append(entry["path"])
            continue
        if path.stat().st_size != entry["size"] or _sha256(path) != entry["sha256"]:
            corrupt.append(entry["path"])
        if i % 2000 == 0:
            log(f"   {i:,}/{len(manifest['entries']):,}")
    log(f"검사 {len(manifest['entries']):,}파일 · 없음 {len(missing)} · 깨짐 {len(corrupt)}")
    for name in (missing[:5] + corrupt[:5]):
        log(f"   문제: {name}")
    if missing or corrupt:
        log("전송이 온전하지 않다 — 다시 받을 것.")
        return 1
    log("전부 일치.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="git 밖 데이터 내보내기 · 검증")
    ap.add_argument("--out", help="내보낼 폴더")
    ap.add_argument("--verify", help="받은 폴더를 MANIFEST 로 대조")
    ap.add_argument("--roots", default=",".join(DEFAULT_ROOTS),
                    help="담을 최상위 경로(쉼표 구분). artifacts 는 기본에서 뺀다(331GB)")
    ap.add_argument("--dry-run", action="store_true", help="목록·크기만 보고 복사하지 않는다")
    args = ap.parse_args(argv)

    if args.verify:
        return verify(Path(args.verify))
    if not args.out:
        ap.error("--out 또는 --verify 중 하나가 필요하다")

    roots = tuple(r.strip() for r in args.roots.split(",") if r.strip())
    files = collect(roots)
    if not files:
        log("담을 파일이 없다 — 경로를 확인할 것.")
        return 2
    export(files, Path(args.out), dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
