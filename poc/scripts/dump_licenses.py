"""OSS 라이선스 + SBOM 자동 추출 (doc/14 §11.1 약속 이행).

폐쇄망 번들의 licenses/third-party-licenses.txt 표준 산출물 + CycloneDX SBOM.

산출 형식:
  --format=plain     : pip-licenses 텍스트 (NOTICE 동봉 권장)
  --format=markdown  : 마크다운 표 (PR·검수 친화)
  --format=json      : 구조화 JSON (스크립트 처리용)
  --format=cyclonedx : CycloneDX 1.5 JSON SBOM (운영 전환 시 발주처 검수 첨부)

화이트리스트 검증 (--check):
  허용형 (Apache/MIT/BSD/MPL/ISC/Python/PostgreSQL) 외 발견 시 비-0 종료.
  CI에서 의존성 추가가 라이선스 위험 도입하는 것을 사전 차단.

dry-run:
  pip-licenses·cyclonedx-bom 패키지 없이도 동작 — 현재 환경 venv를
  읽고 가능한 만큼만 보고. 실제 패키지 있으면 풀 산출.

사용:
  python scripts/dump_licenses.py --output licenses/         # 전체 산출
  python scripts/dump_licenses.py --check                    # CI용 라이선스 검증
  python scripts/dump_licenses.py --format=cyclonedx -o sbom.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent  # poc/


# ─────────────────────────────────────────────────────────────
# 라이선스 등급 (doc/14 §2)
# ─────────────────────────────────────────────────────────────

# 허용형 (Permissive) — 공공사업·온프레미스 상용 배포 적합
PERMISSIVE_PATTERNS = [
    r"\bMIT\b",
    r"\bBSD\b",
    r"Apache.*2\.0|Apache.*Software",
    r"ISC",
    r"Python Software Foundation",
    r"PSF",
    r"PostgreSQL",
    r"Public Domain",
    r"Unlicense",
    r"\bZPL\b",
]

# Weak Copyleft — 동적 링킹 시 안전
WEAK_COPYLEFT_PATTERNS = [
    r"\bLGPL\b",
    r"Mozilla Public License|\bMPL\b",
]

# Strong Copyleft — 주의 필요
STRONG_COPYLEFT_PATTERNS = [
    r"\bAGPL\b",
    r"GNU Affero",
    r"\bGPL[-\s]?[23]",
    r"GNU General Public",
]

# 듀얼 라이선스 / 상용 옵션 패키지 (수동 검토 필요)
DUAL_LICENSE_PACKAGES = {
    "PyMuPDF": "AGPL-3.0 / Artifex Commercial (doc/14 §3.1)",
    "pymupdf": "AGPL-3.0 / Artifex Commercial",
    "fitz":    "AGPL-3.0 / Artifex Commercial",
}


def classify_license(license_str: str) -> str:
    """라이선스 문자열 → 등급 (permissive|weak|strong|unknown)."""
    if not license_str or license_str.upper() in ("UNKNOWN", "NONE"):
        return "unknown"
    for pat in STRONG_COPYLEFT_PATTERNS:
        if re.search(pat, license_str, re.IGNORECASE):
            return "strong"
    for pat in WEAK_COPYLEFT_PATTERNS:
        if re.search(pat, license_str, re.IGNORECASE):
            return "weak"
    for pat in PERMISSIVE_PATTERNS:
        if re.search(pat, license_str, re.IGNORECASE):
            return "permissive"
    return "unknown"


# ─────────────────────────────────────────────────────────────
# pip-licenses 호출 (lazy)
# ─────────────────────────────────────────────────────────────


def _run_pip_licenses() -> list[dict] | None:
    """pip-licenses --format=json 호출. 미설치면 None."""
    try:
        out = subprocess.check_output(
            [sys.executable, "-m", "piplicenses", "--format=json", "--with-urls"],
            text=True, timeout=60,
        )
        return json.loads(out)
    except (subprocess.CalledProcessError, FileNotFoundError, ImportError):
        # 대체 시도: pip-licenses CLI
        try:
            out = subprocess.check_output(
                ["pip-licenses", "--format=json", "--with-urls"],
                text=True, timeout=60,
            )
            return json.loads(out)
        except Exception:  # noqa: BLE001
            return None


# ─────────────────────────────────────────────────────────────
# pyproject 직접 의존성 파싱 (pip-licenses 폴백)
# ─────────────────────────────────────────────────────────────


def _parse_pyproject_deps(pyproject_path: Path) -> list[str]:
    """pip-licenses 없을 때 폴백 — pyproject의 dependencies만 이름 추출."""
    try:
        import tomllib  # 3.11+
    except ImportError:
        return []
    if not pyproject_path.exists():
        return []
    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)
    deps = data.get("project", {}).get("dependencies", []) or []
    names = []
    for d in deps:
        # "fastapi>=0.115" → "fastapi"
        name = re.split(r"[<>=!\[~;]", d.strip(), maxsplit=1)[0].strip()
        if name:
            names.append(name)
    return names


# ─────────────────────────────────────────────────────────────
# 패키지 정보 정규화
# ─────────────────────────────────────────────────────────────


def collect_packages(*, scope: str = "venv") -> list[dict]:
    """현재 환경의 패키지 라이선스 정보 수집.

    scope:
      - "venv": pip-licenses (전체 transitive 포함)
      - "direct": pyproject dependencies만 — venv에서 해당 패키지 라이선스 매핑
      - "pyproject": pyproject 텍스트만 (venv 없을 때 폴백, 이름만)
    """
    venv_data = _run_pip_licenses()

    if scope == "venv":
        if venv_data is None:
            return collect_packages(scope="pyproject")
        return [_normalize(p) for p in venv_data]

    if scope == "direct":
        direct_names = {n.lower() for n in _parse_pyproject_deps(_REPO_ROOT / "pyproject.toml")}
        if not direct_names:
            return []
        if venv_data is None:
            # venv 없으면 이름만 (라이선스 미상)
            return collect_packages(scope="pyproject")
        # extras 변형 ("psycopg[binary]" → "psycopg") 이미 _parse에서 처리됨
        # 정규화 비교: 하이픈·언더스코어·case insensitive
        def _key(n: str) -> str:
            return n.lower().replace("_", "-")

        direct_norm = {_key(n) for n in direct_names}
        return [_normalize(p) for p in venv_data if _key(p.get("Name", "")) in direct_norm]

    # pyproject scope — venv 없을 때
    names = _parse_pyproject_deps(_REPO_ROOT / "pyproject.toml")
    return [
        {
            "name": n,
            "version": "",
            "license": "UNKNOWN (run with pip-licenses installed)",
            "url": "",
            "tier": "unknown",
            "dual_license_note": DUAL_LICENSE_PACKAGES.get(n),
        }
        for n in names
    ]


def _normalize(p: dict) -> dict:
    return {
        "name": p.get("Name", ""),
        "version": p.get("Version", ""),
        "license": p.get("License", "UNKNOWN"),
        "url": p.get("URL", ""),
        "tier": classify_license(p.get("License", "")),
        "dual_license_note": DUAL_LICENSE_PACKAGES.get(p.get("Name", "")),
    }


# ─────────────────────────────────────────────────────────────
# 화이트리스트 검증
# ─────────────────────────────────────────────────────────────


# 알려진 위험 — 발주처 협의 사항으로 분리됨 (doc/14 §3.1·§11.2). CI에선 noise 회피.
KNOWN_RISKS_ALLOWLIST = {
    "PyMuPDF",   # AGPL/Artifex 듀얼, doc/14 §3.1
    "pymupdf",
    "pdf2docx",  # PyMuPDF transitive
}


def check_licenses(packages: list[dict], *, allow_known: bool = True) -> tuple[bool, list[dict]]:
    """strong copyleft 또는 unknown 발견 시 fail.

    Args:
        allow_known: True면 KNOWN_RISKS_ALLOWLIST 패키지는 위반에서 제외.

    반환: (ok, violations)
    """
    violations = []
    for p in packages:
        if not p["name"]:
            continue
        if p["tier"] not in ("strong", "unknown"):
            continue
        if allow_known and p["name"] in KNOWN_RISKS_ALLOWLIST:
            continue
        violations.append(p)
    return len(violations) == 0, violations


# ─────────────────────────────────────────────────────────────
# 출력 포맷터
# ─────────────────────────────────────────────────────────────


def format_plain(packages: list[dict]) -> str:
    lines = [
        "# Third-Party OSS Licenses (Lloydk AI Engine)",
        f"# Generated by scripts/dump_licenses.py (doc/14)",
        f"# Total packages: {len(packages)}",
        "",
    ]
    for p in sorted(packages, key=lambda x: x["name"].lower()):
        lines.append(f"{p['name']} {p['version']}")
        lines.append(f"  License: {p['license']}")
        if p["url"]:
            lines.append(f"  URL: {p['url']}")
        if p["dual_license_note"]:
            lines.append(f"  NOTICE: {p['dual_license_note']}")
        lines.append("")
    return "\n".join(lines)


def format_markdown(packages: list[dict]) -> str:
    tier_counts = Counter(p["tier"] for p in packages)
    lines = [
        "# OSS 라이선스 목록 (자동 생성)",
        "",
        f"_생성: `scripts/dump_licenses.py` · 총 {len(packages)}개 패키지_",
        "",
        "## 등급 요약",
        "",
        "| 등급 | 개수 |",
        "|---|---:|",
        f"| 허용형 (MIT/BSD/Apache) | {tier_counts.get('permissive', 0)} |",
        f"| Weak Copyleft (LGPL/MPL) | {tier_counts.get('weak', 0)} |",
        f"| Strong Copyleft (GPL/AGPL) ⚠ | {tier_counts.get('strong', 0)} |",
        f"| Unknown ⚠ | {tier_counts.get('unknown', 0)} |",
        "",
        "## 패키지 목록",
        "",
        "| 패키지 | 버전 | 라이선스 | 등급 | URL | 비고 |",
        "|---|---|---|:---:|---|---|",
    ]
    tier_icon = {"permissive": "★", "weak": "△", "strong": "⚠", "unknown": "?"}
    for p in sorted(packages, key=lambda x: x["name"].lower()):
        note = p["dual_license_note"] or ""
        url = f"[link]({p['url']})" if p["url"] and p["url"] != "UNKNOWN" else ""
        lines.append(
            f"| {p['name']} | {p['version']} | {p['license']} | "
            f"{tier_icon.get(p['tier'], '?')} | {url} | {note} |"
        )
    return "\n".join(lines)


def format_json(packages: list[dict]) -> str:
    return json.dumps(
        {
            "generated_by": "scripts/dump_licenses.py",
            "total": len(packages),
            "tier_counts": dict(Counter(p["tier"] for p in packages)),
            "packages": packages,
        },
        ensure_ascii=False, indent=2,
    )


def format_cyclonedx(packages: list[dict]) -> str:
    """CycloneDX 1.5 SBOM 표준 JSON.

    cyclonedx-bom 패키지가 있으면 더 풍부한 SBOM 가능하지만,
    본 구현은 최소 스펙으로 외부 의존성 없이 산출 가능하게 설계.
    """
    import datetime as dt
    import uuid

    components = []
    for p in packages:
        if not p["name"]:
            continue
        purl = f"pkg:pypi/{p['name'].lower()}"
        if p["version"]:
            purl += f"@{p['version']}"
        components.append({
            "type": "library",
            "name": p["name"],
            "version": p["version"] or "unknown",
            "purl": purl,
            "licenses": [{"license": {"name": p["license"]}}] if p["license"] != "UNKNOWN" else [],
            "externalReferences": (
                [{"type": "website", "url": p["url"]}]
                if p["url"] and p["url"] != "UNKNOWN" else []
            ),
        })

    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tools": [{"name": "lloydk-dump-licenses", "version": "1.0"}],
            "component": {
                "type": "application",
                "name": "lloydk-ai",
                "version": "0.1.0",
            },
        },
        "components": components,
    }
    return json.dumps(sbom, ensure_ascii=False, indent=2)


_FORMATTERS = {
    "plain": ("third-party-licenses.txt", format_plain),
    "markdown": ("third-party-licenses.md", format_markdown),
    "json": ("third-party-licenses.json", format_json),
    "cyclonedx": ("sbom.json", format_cyclonedx),
}


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--format", "-f", choices=list(_FORMATTERS) + ["all"], default="all",
        help="출력 포맷 (기본: all — 4종 모두 산출)",
    )
    ap.add_argument(
        "--output", "-o", default="licenses",
        help="출력 디렉터리 또는 파일 경로",
    )
    ap.add_argument(
        "--scope", choices=["venv", "direct", "pyproject"], default="venv",
        help="venv: 전체 transitive / direct: 직접 의존성만 (venv 정보 사용) / pyproject: 이름만 폴백",
    )
    ap.add_argument(
        "--check", action="store_true",
        help="strong copyleft 또는 unknown 발견 시 비-0 종료 (CI용)",
    )
    ap.add_argument(
        "--strict", action="store_true",
        help="--check 시 KNOWN_RISKS_ALLOWLIST(PyMuPDF 등) 무시 — 전체 검사",
    )
    args = ap.parse_args()

    packages = collect_packages(scope=args.scope)
    if not packages:
        print("[licenses] no packages collected (pip-licenses 미설치 + pyproject 부재?)", file=sys.stderr)
        return 2

    # --check 모드
    if args.check:
        ok, violations = check_licenses(packages, allow_known=not args.strict)
        mode_tag = "strict" if args.strict else "with-allowlist"
        if ok:
            print(f"[licenses] OK ({mode_tag}) — {len(packages)} packages, all permissive/weak", file=sys.stderr)
            return 0
        print(f"[licenses] FAIL ({mode_tag}) — {len(violations)} risky license(s):", file=sys.stderr)
        for v in violations:
            print(f"  - {v['name']} {v['version']}: {v['license']} (tier={v['tier']})", file=sys.stderr)
        return 1

    # 산출
    out = Path(args.output)
    formats = list(_FORMATTERS) if args.format == "all" else [args.format]

    # 출력이 디렉터리면 multi-file, 파일이면 single
    is_dir_mode = args.format == "all" or out.suffix == ""

    if is_dir_mode:
        out.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for fmt in formats:
        default_name, formatter = _FORMATTERS[fmt]
        text = formatter(packages)
        if is_dir_mode:
            target = out / default_name
        else:
            target = out
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        written.append(target)
        print(f"[licenses] {fmt:10s} -> {target} ({len(text)} bytes)", file=sys.stderr)

    print(
        f"\n[licenses] OK — {len(packages)} packages, "
        f"tiers: {dict(Counter(p['tier'] for p in packages))}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
