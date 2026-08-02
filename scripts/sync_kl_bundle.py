#!/usr/bin/env python
"""KL 제출 묶음(doc/result/KL_AI자료_2026-08) 자체 검증 — open/ 비의존.

2026-08-02 방침 변경: KL 묶음은 이제 **자체 원본**이다. 첨부문서/ 사본을 정본
(doc/result/open/)에서 더 이상 끌어오지 않는다(덮어쓰기 없음). 문서는 이 폴더 안에서
직접 편집하고, 이 스크립트는 **묶음이 완결(내부 링크 0 깨짐·인용 문서 전부 존재)인지
검증만** 한다. open/ 은 감리 릴리스(doc/releases/…) 트랙 전용으로 분리됐다.

  python scripts/sync_kl_bundle.py          # 검증(쓰기 없음). 문제 있으면 exit 1

첨부 대상은 "루트 응답문서가 인용하는 문서 + 그 문서들이 인용하는 문서"의 전이 폐포로
계산하되, 폐포 추적은 **묶음 내부 파일**(첨부문서/)만 읽는다. 검증 항목:
  · 누락  — 인용됐으나 첨부문서/ 에 없는 문서(있으면 깨진 링크가 됨)
  · 깨진 링크 — 묶음 안 상대경로 href 가 실제 파일로 해석 안 됨
  · 고아  — 첨부문서/ 에 있으나 아무 문서도 인용하지 않음(정리 후보 — 자동 삭제 안 함)
  · 범위 이탈 — 첨부문서/ 구성이 요청 6건 대응분(DELIVER)과 다름

2026-08-02 범위 축소: 첨부는 한때 26종이었다. 폐포가 "본문이 인용하면 넣는다"로만
돌아가 참조 문서 16종이 따라 들어왔는데, 발주처가 검토를 요청한 적 없는 자료까지
제출하면 감리 대상 표면만 넓어진다. 요청 6건에 해당하는 11종으로 줄이고, 뺀 15종은
doc/result/KL_AI자료_2026-08_미첨부문서/ 에 보관했다(정본은 doc/result/open/).
폐포는 링크를 타고 다시 자라므로 DELIVER 목록으로 범위를 못박는다 — 목록을 안 고치고
파일만 넣으면 발송이 막힌다.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KL = ROOT / "doc" / "result" / "KL_AI자료_2026-08"
ATTACH = KL / "첨부문서"
HELD = ROOT / "doc" / "result" / "KL_AI자료_2026-08_미첨부문서"

# 발송 범위 — 8/5 요청 6건에 해당하는 문서만. 늘리려면 여기부터 고친다.
DELIVER = {
    # 요청 1 — AI 설계 산출물
    "시스템_설계도_v4.html",
    "기능분해도_데이터흐름도.html",
    "기술구현_백서_v7.html",
    "기술구현_백서_부록A_DB스키마.html",
    "기술구현_백서_부록B_API명세.html",
    "기술구현_백서_부록C_지표용어해설.html",
    "서빙_운영점_파라미터_명세.html",
    # 요청 3 — 골든셋·등급 판정근거·정확도
    "골든셋_종합.html",
    "골든셋_분류근거_v5_clean_v-fe4b386b.html",
    "영업비밀_등급분류_기준_적용서_v2.2.html",
    # 요청 4 — RFP 구현현황 근거
    "요구사항_추적표_RTM.html",
}

_HREF = re.compile(r'href="([^"#][^"]*)"')


def _links(html: str) -> list[str]:
    out = []
    for h in _HREF.findall(html):
        if h.startswith(("http", "mailto:", "data:", "javascript:")):
            continue
        out.append(urllib.parse.unquote(h.split("#")[0]))
    return out


def closure() -> set[str]:
    """루트 응답문서에서 출발해 묶음 내부만 따라간 첨부 인용 폐포(파일명 집합)."""
    want: set[str] = set()
    queue: list[str] = []
    for p in sorted(KL.glob("*.html")):
        for h in _links(p.read_text(encoding="utf-8", errors="replace")):
            if h.startswith("첨부문서/"):
                n = h.split("/", 1)[1]
                if n not in want:
                    want.add(n)
                    queue.append(n)
    while queue:
        cur = queue.pop()
        src = ATTACH / cur          # 정본(open/)이 아니라 묶음 내부 사본을 읽는다
        if not src.exists():
            continue
        for h in _links(src.read_text(encoding="utf-8", errors="replace")):
            if "/" in h or not h.endswith(".html"):
                continue
            if h not in want:
                want.add(h)
                queue.append(h)
    return want


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="(호환용) 항상 검증만 수행 — 이 스크립트는 더 이상 쓰지 않는다")
    ap.parse_args()

    if not ATTACH.exists():
        print("첨부문서/ 폴더가 없습니다:", ATTACH)
        return 1

    want = closure()
    present = {f.name for f in ATTACH.iterdir() if f.is_file()}

    missing = sorted(want - present)          # 인용됐는데 파일 없음
    orphan = sorted(present - want)            # 파일 있는데 인용 없음
    intruder = sorted(present - DELIVER)       # 범위 밖인데 첨부됨
    dropped = sorted(DELIVER - present)        # 범위 안인데 빠짐

    broken = []
    for p in sorted(KL.rglob("*.html")):
        for h in _links(p.read_text(encoding="utf-8", errors="replace")):
            if not (p.parent / h).exists():
                broken.append(f"{p.relative_to(KL).as_posix()} -> {h}")

    for n in missing:
        print("누락:    " + n)
    for b in broken:
        print("깨진 링크:", b)
    for n in orphan:
        print("고아(정리 후보):", n)
    for n in intruder:
        print("범위 이탈(요청 6건 대응분 아님):", n)
    for n in dropped:
        print("범위 누락(DELIVER 인데 첨부 안 됨):", n)

    # [전달 검증] 내용이 정합해도 열람 환경에서 깨지면 소용없다. 실제로 두 건이 있었다 —
    # 첨부 26종 전부가 외부 웹폰트를 부르는데 index 는 "외부 링크 없음"이라 적혀 있었고,
    # 판정근거 보고서는 JS 를 끄면 "조건에 맞는 문서가 없습니다"만 남았다(요청 3 핵심 산출물이
    # '근거 없음'으로 읽힘). 링크·수치만 보는 검사로는 둘 다 안 잡혀 발송 게이트에 함께 건다.
    offline = _offline_violations()
    for v in offline:
        print("전달 결함:", v)

    total = sum(f.stat().st_size for f in ATTACH.iterdir() if f.is_file()) / 1048576
    held = len(list(HELD.glob("*.html"))) if HELD.exists() else 0
    print(f"\n첨부문서/ {len(present)}종 · {total:.1f} MB · "
          f"누락 {len(missing)} · 깨진 링크 {len(broken)} · 고아 {len(orphan)} · "
          f"범위 이탈 {len(intruder)} · 전달 결함 {len(offline)}")
    print(f"미첨부 보관(발송 제외) {held}종 — {HELD.relative_to(ROOT).as_posix()}/")
    if missing or broken or offline or intruder or dropped:
        print("→ 발송 불가: 누락/깨진 링크/범위 이탈/전달 결함 해소 필요"
              " (전달 결함은 scripts/harden_bundle_offline.py 로 일괄 수정)")
        return 1
    print("→ 발송 가능(요청 6건 범위 일치·자체 완결·링크 완결·오프라인 판독 가능)"
          + (" · 고아 파일 검토 권장" if orphan else ""))
    return 0


def _offline_violations() -> list[str]:
    """오프라인/JS-off 판독 검사를 발송 게이트로 끌어온다.

    검사 로직은 harden_bundle_offline.py 한 곳에만 두고 여기서는 호출만 한다 —
    규칙이 두 벌이 되면 반드시 어긋난다. 모듈을 못 불러오면 조용히 통과시키지 않고
    그 사실 자체를 결함으로 보고한다. 검사기가 죽은 것을 '이상 없음'으로 읽는 사고가
    이 프로젝트에서 이미 두 번 있었다(외부 URL 을 건너뛴 링크검사, 데이터 형태를
    못 찾고 OK 를 낸 하드닝 검사).
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "harden_bundle_offline", ROOT / "scripts" / "harden_bundle_offline.py"
    )
    if spec is None or spec.loader is None:
        return ["오프라인 하드닝 검사기를 불러오지 못함(scripts/harden_bundle_offline.py)"]
    try:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as exc:  # noqa: BLE001
        return [f"오프라인 하드닝 검사기 로드 실패: {type(exc).__name__}: {exc}"]

    out: list[str] = []
    for p in sorted(KL.rglob("*.html")):
        text = p.read_text(encoding="utf-8", errors="replace")
        rel = p.relative_to(KL).as_posix()
        if mod.FONT_LINK.search(text):
            out.append(f"외부 웹폰트 참조: {rel}")
        elif mod.EXTERNAL.search(text):
            out.append(f"외부 리소스 참조(index 의 '외부 링크 없음'과 상충): {rel}")
        if mod.looks_js_only(text) and mod.NOSCRIPT_MARK not in text:
            out.append(f"JS-off 시 빈 화면(대체 표 없음): {rel}")
    out.extend(mod.stale_twins())
    return out


if __name__ == "__main__":
    raise SystemExit(main())
