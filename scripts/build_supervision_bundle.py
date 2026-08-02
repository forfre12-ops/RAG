#!/usr/bin/env python
"""감리 제출 산출물 동결 번들 생성기 (재현 가능·무결성 포함).

배경 — 2026-07 배포감사 지적 4건을 구조적으로 닫는다:
  1) manifest 산문 산출물 수(31)와 실제 열거(35) 불일치  → 수를 세지 않고 '계산'한다
  2) DOC-DEC-01 이 2개 문서에 중복 부여                  → ID 를 등록부에서만 해석한다
  3) 정식 산출물 7종에 문서 ID 없음                       → 등록부에 있으면 자동 부여
  4) 파일별 SHA-256·소스 commit·모델 SHA 부재            → INTEGRITY.sha256 을 함께 동결

핵심 원칙: 번들은 doc/result/감리정본/ 의 **동결 사본**이고, 문서 ID 의 유일한
진실원은 doc/docs_registry.yaml 이다. 번들이 자체 ID 체계를 갖지 않는다.

사용:
  # 이전 번들의 산출물 구성을 물려받아 새 release_id 로 재동결
  python scripts/build_supervision_bundle.py \
      --from-release 2026-07-감리 --release-id 2026-08-사전진단

  # 동결본 무결성 검증 (재배포·반입 후)
  python scripts/build_supervision_bundle.py --check 2026-08-사전진단
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
# [2026-08-02] 정본 위치 이전 — doc/result/open/ → doc/result/감리정본/.
# open/ 은 KL 제출묶음이 자체 원본으로 분리되면서 진실원이 둘이 되는 문제가 있어
# 참조 대상에서 내렸다. 감리 트랙(8/31 설계감리·11월 테스트점검·11/23 종료감리)은
# 이 폴더를 편집하고 새 release_id 로 동결한다.
CANON = REPO / "doc" / "result" / "감리정본"
RELEASES = REPO / "doc" / "releases"
REGISTRY = REPO / "doc" / "docs_registry.yaml"
ENV_FILE = REPO / "poc" / ".env.onprem-local"

INTEGRITY_NAME = "INTEGRITY.sha256"
# 무결성 대상에서 제외 — 자기 자신과, 해시 계산 후 쓰이는 파일.
INTEGRITY_EXCLUDE = {INTEGRITY_NAME}


# ────────────────────────────── 유틸 ──────────────────────────────

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def load_registry() -> dict[str, str]:
    """정본 파일명 → 문서 ID. 등록부가 유일한 ID 진실원이다.

    번들은 doc/result/감리정본/ 만 동결하므로 그 경로의 등재만 인덱싱한다.
    동일 파일명이 doc/internal/ 에도 있으면 별도 ID 가 정상이다(내부 사본).
    """
    data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    prefix = "doc/result/감리정본/"
    mapping: dict[str, str] = {}
    for doc in data["documents"]:
        path = doc["path"].replace("\\", "/")
        if not path.startswith(prefix):
            continue
        name = Path(path).name
        if name in mapping and mapping[name] != doc["id"]:
            raise SystemExit(f"등록부 충돌: open/{name} 에 ID 2개 ({mapping[name]}, {doc['id']})")
        mapping[name] = doc["id"]
    return mapping


def model_pointer() -> tuple[str, Path | None]:
    """배포 기본값 .env 가 가리키는 분류기 모델 디렉터리."""
    if not ENV_FILE.exists():
        return ("unknown", None)
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("CLASSIFIER_MODEL_DIR="):
            rel = line.split("=", 1)[1].strip()
            return (rel, REPO / "poc" / rel)
    return ("unknown", None)


def base_image_digests() -> list[str]:
    """Dockerfile 이 고정한 base image digest (재현성 근거)."""
    out = []
    for df in sorted((REPO / "poc").glob("Dockerfile*")):
        for line in df.read_text(encoding="utf-8").splitlines():
            if line.startswith("FROM ") and "@sha256:" in line:
                # 멀티스테이지 별칭(`AS base`)은 digest 가 아니므로 떼어낸다.
                ref = line[5:].strip().split(" AS ")[0].strip()
                out.append(f"{df.name}: {ref}")
    return out


# ────────────────────────── 번들 구성 상속 ──────────────────────────

def inherit_layout(src_release: str) -> dict:
    """이전 번들 manifest 에서 산출물 '구성'만 물려받는다(ID·수치는 재계산)."""
    src = RELEASES / src_release / "manifest.yaml"
    if not src.exists():
        raise SystemExit(f"원본 번들 manifest 없음: {src}")
    return yaml.safe_load(src.read_text(encoding="utf-8"))


def strip_id_comment(entry) -> str:
    """구 manifest 항목에서 파일명만 추출(주석 ID 는 버린다 — 등록부에서 다시 받는다)."""
    if isinstance(entry, dict):
        return entry.get("file", "")
    return str(entry)


# ─────────────────────────────── 생성 ───────────────────────────────

def build(src_release: str, release_id: str, purpose: str, attach: list[str] | None = None) -> int:
    registry = load_registry()
    layout = inherit_layout(src_release)
    dest = RELEASES / release_id
    if dest.exists():
        raise SystemExit(f"이미 존재: {dest} — 동결본은 덮어쓰지 않는다. 다른 release_id 를 쓰라.")

    formal_src: dict[str, list[str]] = layout["formal_deliverables"]
    supp_src = [strip_id_comment(e) for e in layout["supplementary"]["documents"]]

    # 주간보고는 open/ 을 다시 스캔한다 — 새 주차가 자동 포함되도록.
    weekly = sorted(p.name for p in CANON.glob("-주간보고-*.docx"))

    dest.mkdir(parents=True)
    (dest / "참고자료").mkdir()
    (dest / "사업관리보고").mkdir()

    missing: list[str] = []
    no_id: list[str] = []
    copied = 0

    def copy_one(name: str, subdir: str = "") -> bool:
        nonlocal copied
        src = CANON / name
        if not src.exists():
            missing.append(name)
            return False
        shutil.copy2(src, dest / subdir / name if subdir else dest / name)
        copied += 1
        return True

    # 정식 산출물
    formal_out: dict[str, list[dict]] = {}
    formal_count = 0
    for category, entries in formal_src.items():
        rows = []
        for entry in entries:
            name = strip_id_comment(entry)
            if not copy_one(name):
                continue
            doc_id = registry.get(name)
            if doc_id is None and name != "index.html":
                no_id.append(name)
            rows.append({"file": name, "id": doc_id or "(미등록)"})
            if name != "index.html":
                formal_count += 1
        formal_out[category] = rows

    # 참고자료
    supp_out = []
    for name in supp_src:
        if copy_one(name, "참고자료"):
            supp_out.append({"file": name, "id": registry.get(name, "(미등록)")})

    # 사업관리보고
    weekly_out = []
    for name in weekly:
        if copy_one(name, "사업관리보고"):
            weekly_out.append(name)

    # assets (로고 등 — HTML 이 상대참조)
    assets_src = CANON / "assets"
    if assets_src.is_dir():
        shutil.copytree(assets_src, dest / "assets")
        copied += sum(1 for _ in (dest / "assets").rglob("*") if _.is_file())

    if missing:
        shutil.rmtree(dest)
        raise SystemExit("정본에 없는 파일 — 중단:\n  " + "\n  ".join(missing))

    # ── index.html 링크 무결성 (회귀 방지) ──
    # 이전 생성기는 index 를 그대로 복사만 해, 참고자료/ 하위로 이동한 문서 링크와
    # 정본에만 있고 layout 에 없던 문서(예: 골든 분류근거 리포트) 링크가 전부 깨졌다.
    # 여기서 (1) index 가 링크하나 미수록인 정본 문서를 참고자료/ 로 자동 수록하고
    #        (2) 참고자료 소재 문서의 루트 링크에 접두사를 붙이며
    #        (3) 미해결 내부 링크가 남으면 동결을 중단한다.
    import re  # noqa: PLC0415

    index_path = dest / "index.html"
    if index_path.exists():
        html = index_path.read_text(encoding="utf-8")
        linked = sorted(set(re.findall(r'href="([^"#?]+\.html)"', html)))
        for href in linked:
            base = href.split("/")[-1]
            if (dest / base).exists() or (dest / "참고자료" / base).exists():
                continue
            if (CANON / base).exists() and copy_one(base, "참고자료"):
                supp_out.append({"file": base, "id": registry.get(base, "(미등록)")})
        for name in {r["file"] for r in supp_out}:
            html = html.replace(f'href="{name}"', f'href="참고자료/{name}"')
        index_path.write_text(html, encoding="utf-8")
        broken_links = sorted({
            h for h in re.findall(r'href="([^"#?]+\.html)"', html)
            if not (dest / h).exists()
        })
        if broken_links:
            shutil.rmtree(dest)
            raise SystemExit("index.html 미해결 링크(동결 중단):\n  " + "\n  ".join(broken_links))

    # 검증 증적(회귀 로그·스캔 리포트 등) — 감리가 "전 시험 통과"를 확인할 수 있게 동봉.
    evidence_out: list[str] = []
    if attach:
        (dest / "검증로그").mkdir()
        for src_path in attach:
            src = Path(src_path)
            if not src.exists():
                shutil.rmtree(dest)
                raise SystemExit(f"첨부 증적 없음: {src}")
            shutil.copy2(src, dest / "검증로그" / src.name)
            evidence_out.append(src.name)
            copied += 1

    # ── manifest 작성 (수치는 전부 계산값) ──
    html_formal = sum(
        1 for rows in formal_out.values() for r in rows
        if r["file"].endswith(".html") and r["file"] != "index.html"
    )
    other_formal = formal_count - html_formal
    commit = git("rev-parse", "HEAD")
    model_rel, model_dir = model_pointer()

    manifest = {
        "release_id": release_id,
        "purpose": purpose,
        "frozen_at": git("log", "-1", "--format=%ad", "--date=short"),
        "source_commit": commit,
        "source_of_truth": "doc/result/감리정본/",
        "id_authority": "doc/docs_registry.yaml",
        "inherited_from": src_release,
        "prepared_by": "로이드케이(수행사)",
        "counts": {
            "formal_deliverables": formal_count,
            "formal_html": html_formal,
            "formal_other_format": other_formal,
            "navigation_hub": 1,
            "root_files_total": formal_count + 1,
            "supplementary": len(supp_out),
            "progress_reports": len(weekly_out),
            "verification_evidence": len(evidence_out),
        },
        "deployed_model": {
            "pointer": model_rel,
            "note": "poc/.env.onprem-local CLASSIFIER_MODEL_DIR 기준 — 배포 기본값",
        },
        "base_image_digests": base_image_digests(),
        "integrity": f"{INTEGRITY_NAME} — 번들 전 파일 SHA-256 + 소스 commit + 모델 SHA",
        "formal_deliverables": formal_out,
        "supplementary": {"path_prefix": "참고자료/", "documents": supp_out},
        "progress_reports": {"path_prefix": "사업관리보고/", "documents": weekly_out},
        "verification_evidence": {"path_prefix": "검증로그/", "documents": evidence_out},
        "notes": [
            "동결 사본이다. 수정이 필요하면 정본(doc/result/감리정본/)을 고치고 새 release_id 로 다시 동결한다.",
            "문서 ID 는 doc/docs_registry.yaml 에서만 해석한다 — 번들은 자체 ID 체계를 갖지 않는다.",
            f"재생성: python scripts/build_supervision_bundle.py --from-release {src_release} --release-id {release_id}",
            f"검증:   python scripts/build_supervision_bundle.py --check {release_id}",
        ],
    }
    (dest / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )

    # ── 무결성 파일 ──
    lines = [
        "# 감리 제출 동결 번들 무결성 매니페스트",
        f"# release_id   : {release_id}",
        f"# source_commit: {commit}",
        f"# git_describe : {git('describe', '--always', '--dirty')}",
        "#",
        "# [배포 모델]",
        f"#   pointer: {model_rel}",
    ]
    if model_dir and model_dir.is_dir():
        for f in sorted(model_dir.iterdir()):
            if f.is_file() and f.suffix in {".safetensors", ".json", ".bin"}:
                lines.append(f"#   {sha256_file(f)}  {f.name}")
    else:
        lines.append("#   (모델 디렉터리 미존재 — 반입 시 별도 검증 필요)")
    lines += ["#", "# [base image digest]"]
    lines += [f"#   {d}" for d in base_image_digests()] or ["#   (고정 없음)"]
    lines += ["#", "# [번들 파일 — sha256sum -c 로 검증 가능]", ""]

    for f in sorted(dest.rglob("*")):
        if f.is_file() and f.name not in INTEGRITY_EXCLUDE:
            lines.append(f"{sha256_file(f)}  {f.relative_to(dest).as_posix()}")

    (dest / INTEGRITY_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"[생성] {dest.relative_to(REPO)}")
    print(f"  정식 산출물     : {formal_count}종 (HTML {html_formal} + 기타형식 {other_formal})")
    print(f"  탐색 허브       : 1 (index.html)")
    print(f"  루트 파일 합계  : {formal_count + 1}")
    print(f"  참고자료        : {len(supp_out)}종")
    print(f"  사업관리보고    : {len(weekly_out)}종")
    print(f"  검증 증적       : {len(evidence_out)}종")
    print(f"  복사 파일 총계  : {copied}")
    print(f"  source_commit   : {commit}")
    if no_id:
        print(f"  ⚠ 등록부 미등재 {len(no_id)}종: {', '.join(no_id)}")
        return 1
    print("  문서 ID         : 전 산출물 등록부 해석 완료 (중복·누락 0)")
    return 0


# ─────────────────────────────── 검증 ───────────────────────────────

def check(release_id: str) -> int:
    dest = RELEASES / release_id
    integ = dest / INTEGRITY_NAME
    if not integ.exists():
        raise SystemExit(f"무결성 파일 없음: {integ}")

    expected: dict[str, str] = {}
    for line in integ.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            digest, _, rel = line.partition("  ")
            expected[rel] = digest

    actual = {
        f.relative_to(dest).as_posix(): sha256_file(f)
        for f in dest.rglob("*")
        if f.is_file() and f.name not in INTEGRITY_EXCLUDE
    }

    changed = [p for p in expected if p in actual and actual[p] != expected[p]]
    removed = [p for p in expected if p not in actual]
    added = [p for p in actual if p not in expected]

    for label, items in (("변조", changed), ("삭제", removed), ("추가", added)):
        for p in items:
            print(f"  ✗ {label}: {p}")
    if changed or removed or added:
        print(f"[FAIL] {release_id} — 변조 {len(changed)} · 삭제 {len(removed)} · 추가 {len(added)}")
        return 1
    print(f"[OK] {release_id} — {len(expected)}개 파일 무결성 일치")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-release", help="산출물 구성을 물려받을 기존 release_id")
    ap.add_argument("--release-id", help="새로 동결할 release_id")
    ap.add_argument("--purpose", default="제3자 감리(supervision) 대응 산출물 제출본")
    ap.add_argument("--attach", nargs="*", default=[],
                    help="검증로그/ 로 동봉할 증적 파일(회귀 로그·스캔 리포트 등)")
    ap.add_argument("--check", metavar="RELEASE_ID", help="동결본 무결성 검증")
    args = ap.parse_args()

    if args.check:
        return check(args.check)
    if not (args.from_release and args.release_id):
        ap.error("--from-release 와 --release-id 를 함께 주거나, --check 를 쓰라.")
    return build(args.from_release, args.release_id, args.purpose, args.attach)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    raise SystemExit(main())
