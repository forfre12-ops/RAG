"""배포 정본(deploy_manifest.toml)과 실제 배포물이 어긋나지 않는지 검사한다.

번들·이미지에 실리는 파일은 정본에 적힌 것뿐이다. 이 검사기는 세 가지를 본다.

  1. 정본이 가리키는 원본 파일이 리포에 실재하는가 (선언만 있고 만드는 곳이 없던 README.md 같은 사고 방지)
  2. 도커 이미지에 싣는 scripts 목록(container.scripts.allow)이 '런타임 근거 폐포'와 정확히 같은가
     - 근거: src(perf 하니스 제외)·alembic·Dockerfile·compose·번들 쉘·운영 문서가 부르는 스크립트와, 그 스크립트가 다시 부르는 스크립트
     - 부족하면(근거는 있는데 목록에 없음) 이미지에서 실행이 깨진다 → 실패
     - 남으면(근거 없이 실림) 안 쓰는 파일이 실린다 → 실패
  3. Dockerfile.api.prod·Dockerfile.worker 의 COPY 블록, .dockerignore 의 제외 경로가 정본과 같은가

번들 폴더를 주면 실제 산출물이 정본 밖의 파일을 담고 있는지(선언 외 파일)와 필수 파일이 빠졌는지도 본다.

사용:
  python scripts/check_deploy_manifest.py                  # 1~3 검사 (CI·시험이 부른다)
  python scripts/check_deploy_manifest.py --write          # Dockerfile COPY 블록·.dockerignore 를 정본대로 다시 쓴다
  python scripts/check_deploy_manifest.py --derive         # 런타임 근거 폐포를 근거와 함께 출력(정본 갱신 참고용)
  python scripts/check_deploy_manifest.py --bundle DIR     # 만들어진 번들이 정본과 같은지 검사
"""
from __future__ import annotations

import argparse
import fnmatch
import io
import re
import sys
import tokenize
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deploy_manifest as dm  # noqa: E402

POC = dm.POC_ROOT
SCRIPTS = POC / "scripts"
SKIP_SUFFIX = {".pyc", ".md", ".log", ".bak"}          # .dockerignore 가 거르는 것
DOCS_OPERATOR = ("INSTALL.md", "OPERATION.md", "TROUBLESHOOTING.md")
BLOCK_BEGIN = "# BEGIN deploy_manifest:container_scripts"
BLOCK_END = "# END deploy_manifest:container_scripts"
IGNORE_BEGIN = "# BEGIN deploy_manifest:exclude_paths"
IGNORE_END = "# END deploy_manifest:exclude_paths"


# ── 텍스트 도구 ─────────────────────────────────────────────
def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _strip_py(text: str) -> str:
    """주석과 단독 docstring 을 뺀다(괄호 안 여러 줄 문자열 인수는 남긴다). 실패하면 원문."""
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text
    skip_prev = {tokenize.NEWLINE, tokenize.NL, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING}
    out: list[str] = []
    prev, depth = tokenize.NEWLINE, 0
    for i, t in enumerate(toks):
        if t.type == tokenize.COMMENT:
            continue
        if t.type == tokenize.OP:
            if t.string in "([{":
                depth += 1
            elif t.string in ")]}":
                depth = max(0, depth - 1)
        if t.type == tokenize.STRING and depth == 0 and prev in skip_prev:
            nxt = toks[i + 1].type if i + 1 < len(toks) else tokenize.NEWLINE
            if nxt in (tokenize.NEWLINE, tokenize.NL):
                prev = t.type
                continue
        out.append(t.string)
        prev = t.type
    return " ".join(out)


def _strip_hash_comments(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _exec_text(p: Path) -> str:
    raw = _read(p)
    if p.suffix == ".py":
        return _strip_py(raw)
    if p.suffix in (".sh", ".ps1", ".yml", ".yaml") or p.name.startswith("Dockerfile"):
        return _strip_hash_comments(raw)
    return raw


def _image_universe() -> list[Path]:
    """.dockerignore 를 통과해 이미지에 들어가는 scripts/ 파일(원본 COPY 기준)."""
    return sorted(
        p for p in SCRIPTS.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix not in SKIP_SUFFIX
    )


def _mention_pattern(p: Path) -> re.Pattern[str]:
    pats = [r"(?<![A-Za-z0-9_])" + re.escape(p.name) + r"(?![A-Za-z0-9_])"]
    if p.suffix == ".py":
        stem = re.escape(p.stem)
        pats.append(r"scripts\s*[./]\s*" + stem + r"(?![A-Za-z0-9_])")
        pats.append(r"(?:from|import)\s+" + stem + r"(?![A-Za-z0-9_])")
    return re.compile("|".join(pats))


# ── 런타임 근거 폐포 ─────────────────────────────────────────
def derive_container_scripts(manifest: dict) -> dict[str, str]:
    """이미지에 실려야 하는 scripts → 처음 근거를 댄 곳. 정본의 not_in_image 는 제외한다."""
    host = set(dm.bundle_root_scripts())                      # 호스트에서 도는 번들 루트 스크립트
    skip = set(manifest["container"]["scripts"].get("not_in_image", {}))
    universe = [p for p in _image_universe() if p.relative_to(SCRIPTS).as_posix() not in host]
    universe_all = _image_universe()
    pat = {p: _mention_pattern(p) for p in universe}
    rel = {p: p.relative_to(SCRIPTS).as_posix() for p in universe}

    roots: dict[str, str] = {}                                 # 라벨 → 실행 텍스트
    for p in (POC / "src").rglob("*.py"):
        parts = p.relative_to(POC).parts
        if "__pycache__" in parts or parts[:3] == ("src", "koipa", "perf"):
            continue                                           # perf 는 개발용 하니스(perf 밖에서 import 0건)
        roots[p.relative_to(POC).as_posix()] = _exec_text(p)
    for p in (POC / "alembic").rglob("*.py"):
        if "__pycache__" not in p.parts:
            roots[p.relative_to(POC).as_posix()] = _exec_text(p)
    for name in ("Dockerfile.api.prod", "Dockerfile.worker", "docker-compose.airgap.yml", "docker-compose.gpu.yml"):
        q = POC / name
        if q.exists():
            roots[name] = _exec_text(q)
    for doc in DOCS_OPERATOR:
        q = POC / "docs" / doc
        if q.exists():
            roots["docs/" + doc] = _read(q)
    host_py_only: dict[str, str] = {}                          # 호스트 쉘은 `.py` 언급만 근거(쉘→쉘 호출은 호스트끼리)
    for name in host:
        q = SCRIPTS / name
        if q.exists():
            host_py_only[f"scripts/{name}"] = _exec_text(q)

    reached: dict[str, str] = {}
    frontier: list[Path] = []
    for label, text in roots.items():
        for p in universe:
            if rel[p] not in reached and rel[p] not in skip and pat[p].search(text):
                reached[rel[p]] = label
                frontier.append(p)
    for label, text in host_py_only.items():
        for p in universe:
            if p.suffix == ".py" and rel[p] not in reached and rel[p] not in skip and pat[p].search(text):
                reached[rel[p]] = label
                frontier.append(p)
    while frontier:
        cur = frontier.pop()
        text = _exec_text(cur)
        for p in universe:
            if p != cur and rel[p] not in reached and rel[p] not in skip and pat[p].search(text):
                reached[rel[p]] = "scripts/" + rel[cur]
                frontier.append(p)
    del universe_all
    return dict(sorted(reached.items()))


# ── Dockerfile / .dockerignore 블록 ──────────────────────────
def _copy_block(scripts: list[str]) -> str:
    lines = [BLOCK_BEGIN + " — 자동 생성(scripts/check_deploy_manifest.py --write). 손으로 고치지 않는다.",
             "# 이미지에는 정본(deploy_manifest.toml)의 container.scripts.allow 만 싣는다."]
    srcs = [f"scripts/{s}" for s in scripts]
    lines.append("COPY " + " \\\n     ".join(srcs) + " \\\n     ./scripts/")
    lines.append(BLOCK_END)
    return "\n".join(lines) + "\n"


def _ignore_block(paths: list[str]) -> str:
    lines = [IGNORE_BEGIN + " — 자동 생성(scripts/check_deploy_manifest.py --write)"] + paths + [IGNORE_END]
    return "\n".join(lines) + "\n"


def _replace_block(text: str, begin: str, end: str, block: str) -> str | None:
    """begin~end 줄 사이를 block 으로 바꾼다. 표지가 없으면 None."""
    m = re.search(re.escape(begin) + r".*?" + re.escape(end) + r"\n", text, flags=re.S)
    if not m:
        return None
    return text[:m.start()] + block + text[m.end():]


def _dockerfiles() -> list[Path]:
    return [POC / "Dockerfile.api.prod", POC / "Dockerfile.worker"]


# ── 검사 ────────────────────────────────────────────────────
def check(manifest: dict) -> list[str]:
    errs: list[str] = []

    # 1) 원본 실재
    for s in dm.bundle_root_scripts():
        if not (SCRIPTS / s).is_file():
            errs.append(f"번들 루트 스크립트 원본 없음: scripts/{s}")
    for d in dm.bundle_docs():
        if not (POC / "docs" / d).is_file():
            errs.append(f"번들 문서 원본 없음: docs/{d}")
    readme = manifest["bundle"]["root"]["readme"]
    if not (POC / readme["source"]).is_file():
        errs.append(f"번들 README 원본 없음: {readme['source']}")
    for f in manifest["bundle"]["infra"]["files"]:
        src = manifest["bundle"]["infra"]["sources"].get(f)
        if src and not (POC / src).is_file():
            errs.append(f"infra 원본 없음: {src}")
    for lic in dm.bundle_licenses():
        if not (POC / "licenses" / lic).is_file():
            errs.append(f"라이선스 원본 없음: licenses/{lic}")
    allow = dm.container_scripts()
    for s in allow:
        if not (SCRIPTS / s).is_file():
            errs.append(f"이미지 scripts 허용 목록에 있는데 원본이 없다: scripts/{s}")
    if len(allow) != len(set(allow)):
        errs.append("container.scripts.allow 에 중복이 있다")
    if allow != sorted(allow):
        errs.append("container.scripts.allow 가 정렬돼 있지 않다(diff 가독성)")
    for s, why in dm.container_scripts_not_in_image().items():
        if s in allow:
            errs.append(f"not_in_image 인데 allow 에도 있다: {s} ({why})")

    # 2) 런타임 근거 폐포 == 허용 목록
    derived = derive_container_scripts(manifest)
    missing = sorted(set(derived) - set(allow))
    extra = sorted(set(allow) - set(derived))
    for s in missing:
        errs.append(f"근거가 있는데 이미지 목록에 없다(이미지에서 실행이 깨진다): scripts/{s} ← {derived[s]}")
    for s in extra:
        errs.append(f"근거 없이 이미지에 실린다(안 쓰는 파일): scripts/{s} — 지우거나 참조 근거를 만들 것")

    # 3) Dockerfile COPY 블록 · .dockerignore
    want_copy = _copy_block(allow)
    for df in _dockerfiles():
        text = _read(df)
        new = _replace_block(text, BLOCK_BEGIN, BLOCK_END, want_copy)
        if new is None:
            errs.append(f"{df.name}: COPY 블록 표지({BLOCK_BEGIN})가 없다 — --write 로 만들 것")
        elif new != text:
            errs.append(f"{df.name}: COPY 블록이 정본과 다르다 — --write 로 다시 쓸 것")
        if re.search(r"^COPY\s+scripts\s+\./scripts", text, flags=re.M):
            errs.append(f"{df.name}: `COPY scripts ./scripts` 가 남아 있다(폴더째 실림)")
    ign = POC / ".dockerignore"
    itext = _read(ign)
    inew = _replace_block(itext, IGNORE_BEGIN, IGNORE_END, _ignore_block(dm.container_excluded_paths()))
    if inew is None:
        errs.append(f".dockerignore: 표지({IGNORE_BEGIN})가 없다 — --write 로 만들 것")
    elif inew != itext:
        errs.append(".dockerignore: 제외 경로가 정본과 다르다 — --write 로 다시 쓸 것")
    return errs


def write(manifest: dict) -> list[str]:
    changed: list[str] = []
    want_copy = _copy_block(dm.container_scripts())
    for df in _dockerfiles():
        text = _read(df)
        new = _replace_block(text, BLOCK_BEGIN, BLOCK_END, want_copy)
        if new is None:
            new, n = re.subn(r"^COPY\s+scripts\s+\./scripts\s*\n", lambda _m: want_copy, text, count=1, flags=re.M)
            if n == 0:
                raise SystemExit(f"{df.name}: `COPY scripts ./scripts` 도 표지도 없어 바꿀 자리를 못 찾았다")
        if new != text:
            df.write_text(new, encoding="utf-8", newline="\n")
            changed.append(df.name)
    ign = POC / ".dockerignore"
    itext = _read(ign)
    block = _ignore_block(dm.container_excluded_paths())
    inew = _replace_block(itext, IGNORE_BEGIN, IGNORE_END, block)
    if inew is None:
        inew = itext.rstrip("\n") + "\n\n# ── 이미지 미사용 경로(정본 container.exclude_paths) ──\n" + block
    if inew != itext:
        ign.write_text(inew, encoding="utf-8", newline="\n")
        changed.append(".dockerignore")
    return changed


# ── 번들 산출물 검사 ─────────────────────────────────────────
def bundle_allow_patterns(manifest: dict) -> tuple[list[str], list[str]]:
    """(허용 패턴, 필수 패턴). 번들 루트 기준 상대 경로."""
    b = manifest["bundle"]
    allow: list[str] = []
    required: list[str] = []
    root = b["root"]
    for s in root["scripts"] + root["generated"]:
        allow.append(s)
        required.append(s)
    allow.append(root["readme"]["dest"])
    required.append(root["readme"]["dest"])
    for d in b["docs"]["files"]:
        allow.append(f"docs/{d}")
        required.append(f"docs/{d}")
    for f in b["infra"]["files"]:
        allow.append(f"infra-config/{f}")
        required.append(f"infra-config/{f}")
    for lic in b["licenses"]["files"]:
        allow.append(f"licenses/{lic}")
        required.append(f"licenses/{lic}")
    for f in b["acceptance"]["files"]:
        allow.append(f"acceptance/{f}")
        required.append(f"acceptance/{f}")
    allow.append("acceptance/docs/*")
    required.append("acceptance/docs/*")
    for name in b["images"]["save"]:
        allow.append(f"docker-images/{name}.tar")
        required.append(f"docker-images/{name}.tar")
    for name in b["images"]["observability"]:
        allow.append(f"docker-images/obs-{name}.tar")
    allow += ["observability/" + f for f in b["observability"]["files"]]
    allow += [f"golden_review_batch/{p}" for p in b["review_batch"]["patterns"]]
    allow.append("rpms/*.rpm")
    m = b["models"]
    allow.append("models/classifier-trained/*")
    required.append("models/classifier-trained/model.safetensors")
    for repo in m["hf_cache"]:
        allow.append("models/hf/hub/models--" + repo.replace("/", "--") + "/*")
    return allow, required


def check_bundle(manifest: dict, bundle: Path) -> list[str]:
    errs: list[str] = []
    allow, required = bundle_allow_patterns(manifest)
    files = sorted(p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file())

    def ok(path: str) -> bool:
        return any(fnmatch.fnmatchcase(path, pat) for pat in allow)

    extras = [f for f in files if not ok(f)]
    # 폴더 단위로 묶어 크기와 함께 보여 준다(파일이 수백 개인 폴더가 통째로 어긋나는 경우가 흔하다).
    groups: dict[str, list[str]] = {}
    for f in extras:
        parts = f.split("/")
        key = "/".join(parts[:2]) if parts[0] in ("docker-images", "models") and len(parts) > 2 else parts[0]
        groups.setdefault(key, []).append(f)
    for key, fs in sorted(groups.items(), key=lambda kv: -sum((bundle / f).stat().st_size for f in kv[1])):
        size = sum((bundle / f).stat().st_size for f in fs)
        sample = ", ".join(fs[:2])
        errs.append(f"정본에 없는 파일이 번들에 있다: {key} — {len(fs)}개 {size:,} B (예: {sample})")
    for pat in required:
        if not any(fnmatch.fnmatchcase(f, pat) for f in files):
            errs.append(f"정본이 요구하는 파일이 번들에 없다: {pat}")
    # HF 캐시: refs/main 이 가리키는 리비전 하나만
    for repo in manifest["bundle"]["models"]["hf_cache"]:
        base = bundle / "models" / "hf" / "hub" / ("models--" + repo.replace("/", "--"))
        snaps = base / "snapshots"
        if snaps.is_dir():
            revs = sorted(p.name for p in snaps.iterdir())
            main = _read(base / "refs" / "main").strip()
            if len(revs) != 1 or (main and revs != [main]):
                errs.append(f"HF 캐시 {repo}: refs/main({main[:8] or '없음'}) 하나만 있어야 하는데 스냅샷이 {[r[:8] for r in revs]}")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="Dockerfile COPY 블록·.dockerignore 를 정본대로 쓴다")
    ap.add_argument("--derive", action="store_true", help="런타임 근거 폐포를 출력한다")
    ap.add_argument("--bundle", type=Path, help="만들어진 번들 폴더를 정본과 대조한다")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    manifest = dm.load()

    if args.derive:
        d = derive_container_scripts(manifest)
        print(f"# 런타임 근거 폐포 {len(d)}개")
        for s, why in d.items():
            print(f"{s}\t<- {why}")
        return 0
    if args.write:
        changed = write(manifest)
        print("바꾼 파일:", ", ".join(changed) if changed else "없음(이미 정본과 같다)")
    if args.bundle:
        errs = check_bundle(manifest, args.bundle)
    else:
        errs = check(manifest)
    if errs:
        print(f"[deploy-manifest] 어긋남 {len(errs)}건")
        for e in errs:
            print("  -", e)
        return 1
    print("[deploy-manifest] 정본과 일치")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
