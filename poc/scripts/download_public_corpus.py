"""P2-B1: 공개 코퍼스 다운로드 자동화 (D1~D6).

collect_public_data.py가 생성한 매니페스트를 읽어 실제 파일을 다운로드.
라이선스 동의·NDA 필요한 D4(AI Hub)는 안내만 출력 + 수동 진행 요구.

사용:
  python scripts/download_public_corpus.py \
      --manifest datasets/public/manifest.yaml \
      --out datasets/public/raw/ \
      --sources D1 D2 D3 D5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Optional


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_manifest(path: Path) -> dict:
    if not path.exists():
        print(f"[ERR] manifest not found: {path}", file=sys.stderr)
        sys.exit(2)
    if path.suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError:
            print("PyYAML 필요: pip install pyyaml", file=sys.stderr)
            sys.exit(2)
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    return json.loads(path.read_text(encoding="utf-8"))


def _download_one(url: str, dest: Path, *, timeout: int = 60, max_mb: int = 500) -> tuple[bool, Optional[str]]:
    try:
        import httpx  # type: ignore
    except ImportError:
        return False, "httpx not installed"

    try:
        with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as r:
            r.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            total = 0
            limit = max_mb * 1024 * 1024
            with dest.open("wb") as f:
                for chunk in r.iter_bytes(8192):
                    f.write(chunk)
                    total += len(chunk)
                    if total > limit:
                        f.close()
                        dest.unlink(missing_ok=True)
                        return False, f"exceeded max_mb={max_mb}"
            return True, None
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]


def main() -> int:
    parser = argparse.ArgumentParser(description="B1: 공개 코퍼스 자동 다운로드")
    parser.add_argument("--manifest", type=Path, default=Path("datasets/public/manifest.yaml"))
    parser.add_argument("--out", type=Path, default=Path("datasets/public/raw/"))
    parser.add_argument("--sources", nargs="*", default=["D1", "D2", "D3", "D5"])
    parser.add_argument("--max-mb", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    manifest = _load_manifest(args.manifest)
    sources = manifest.get("sources", []) or manifest.get("D", [])
    results: list[dict] = []

    for src in sources:
        sid = src.get("id") or src.get("source_id")
        if sid not in args.sources:
            continue
        if src.get("requires_consent") or src.get("requires_application"):
            print(f"[SKIP] {sid}: 라이선스 동의·신청 필요 — 수동 진행 (안내: {src.get('contact')})")
            results.append({"id": sid, "status": "MANUAL_REQUIRED"})
            continue

        items = src.get("items", []) or src.get("files", [])
        sid_out = args.out / sid
        for it in items:
            url = it.get("url")
            fname = it.get("filename") or url.rsplit("/", 1)[-1]
            dest = sid_out / fname
            if not url:
                continue
            if args.dry_run:
                print(f"[DRY] {sid}/{fname} ← {url}")
                results.append({"id": sid, "file": fname, "status": "DRY"})
                continue
            ok, err = _download_one(url, dest, max_mb=args.max_mb)
            if ok:
                sha = _sha256(dest)
                print(f"[OK]  {sid}/{fname}  sha256={sha[:16]}...")
                results.append({"id": sid, "file": fname, "status": "OK", "sha256": sha})
            else:
                print(f"[FAIL] {sid}/{fname}: {err}")
                results.append({"id": sid, "file": fname, "status": "FAIL", "error": err})

    # 결과 리포트
    rpt = args.out / "_download_report.json"
    rpt.parent.mkdir(parents=True, exist_ok=True)
    rpt.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    n_ok = sum(1 for r in results if r["status"] == "OK")
    n_fail = sum(1 for r in results if r["status"] == "FAIL")
    print(f"\nReport: {rpt}  (OK {n_ok}, FAIL {n_fail}, total {len(results)})")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
