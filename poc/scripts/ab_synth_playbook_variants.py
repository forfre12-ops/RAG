"""생성규칙(generation_playbook) 변형 A/B — 제품 생성기를 로컬 Ollama 로 돌려 규칙 문구의 효과를 잰다.

배경(2026-09-29): 규칙 6개와 프롬프트 사이에 문구 충돌이 두 군데 있었다.
  ① 사람 이름 — 시스템 프롬프트는 "가명도 만들지 말고 역할 식별자만"인데 규칙 6번은
     "회사·사람·제품 이름은 가상이되 자연스럽게".
  ② 양식 — 규칙 2번은 "모든 문서를 보고서 구조로 쓰지 않는다"인데, 기본 구조 문구는
     "여러 절과 항목을 사용하고…"이고 시스템 프롬프트는 "표로 끝내지 말고 결론과 후속 조치 문단"을 요구한다.
어느 쪽 문구가 실제 생성물을 어떻게 바꾸는지 모른 채 고치지 않으려고 이 스크립트로 잰다.

팔(arm) — 각 팔은 (지침 문구, 붙이는 방식)
  A  2026-09-29 이전 그대로: 옛 규칙 6 + 기본 구조 문구 뒤에 지침을 덧붙임(append)
  B  A 에서 규칙 6 의 '사람' 문구를 시스템 프롬프트와 맞춤(예시 "[공정책임자A]" 를 그대로 적음 — 실패안)
  C  B 에 규칙 2 의 우선순위 문장을 더함(미채택: A 와 차이 없음)
  D  B 문구를 기본 구조 문구 **대신** 넣음(replace)
  E  D 에서 규칙 6 의 예시를 뺌
  P  채택 후 실제 배포 경로 그대로(구조 요구를 안 주고 모듈의 playbook_text() 를 그대로 씀)
결과 표는 메모리 synth-lessons-playbook-feasibility-2026-09-26 의 9/29 절.

요청마다 팔을 번갈아 만든다 — 시간이 지나며 서버 상태가 바뀌어도 한 팔에만 쏠리지 않게(팔을 나눠 따로 돌리면 그 보장은 없다).
프로그램이 셀 수 있는 것만 잰다(문서 수가 적어 방향만 본다 — 우열 단정 금지).

실행: poc/.venv 의 python, Ollama(localhost:11434) 가 떠 있어야 한다.
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/ab_synth_playbook_variants.py \
        --out-dir reports/docgen_playbook_probe_20260929
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import sys
import time

_POC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_POC, "src"))
os.environ.setdefault("TESTING", "1")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from koipa.adapters.llm.local_openai_provider import LocalOpenAIProvider  # noqa: E402
from koipa.modules.m1_synthesis import generator as gen_mod  # noqa: E402
from koipa.modules.m1_synthesis.generation_playbook import (  # noqa: E402
    AVOID_PHRASES,
    GENERATION_RULES,
    playbook_text,
)
from koipa.modules.m1_synthesis.generator import (  # noqa: E402
    _DEFAULT_STRUCTURE_REQUIREMENTS,
    SyntheticDocGenerator,
    SynthRequest,
)
from koipa.services.synth_quality import (  # noqa: E402
    _document_quality_errors,
    _exposes_grade_token,
    _exposes_self_grade_declaration,
)

# ── 팔 정의 ────────────────────────────────────────────────────────────────
_RULE2 = GENERATION_RULES[1]
# 2026-09-29 이전 규칙 6 — 모듈이 고쳐진 뒤에도 이 시험을 다시 재현할 수 있게 문구를 여기 고정한다.
OLD_RULE6 = (
    "'조성 W'·'모듈 R' 같은 별칭 코드를 만들지 않는다. 회사·사람·제품 이름은 가상이되 "
    "자연스럽게 쓴다."
)
RULE6_FIXED = (
    "'조성 W'·'모듈 R' 같은 별칭 코드를 만들지 않는다. 회사·제품 이름은 가상이되 자연스럽게 쓰고, "
    "사람은 실명이나 가명 없이 [공정책임자A]·품질관리팀처럼 역할로만 부른다."
)
# B·C·D 의 6번은 시스템 프롬프트의 예시 "[공정책임자A]" 를 그대로 적어서 그 낱말이 도메인과 상관없이
# 퍼졌다(리터럴이 든 문서 A 3 → B 9 → D 12 / 16). 예시와 대괄호 표기를 뺀 문구가 E 의 6번이다.
RULE6_NOEX = (
    "'조성 W'·'모듈 R' 같은 별칭 코드를 만들지 않는다. 회사·제품 이름은 가상이되 자연스럽게 쓰고, "
    "사람은 실명이나 가명 없이 부서·직책 같은 역할로만 부른다."
)
RULE2_PRECEDENCE = (
    _RULE2
    + " 시스템 지침의 절 제목·표·결론과 후속 조치 문단 요구는 고른 양식에 그런 것이 실제로 있을 때만 "
    "따르고, 메신저 대화·메모·이메일에는 억지로 만들어 넣지 않는다."
)


def render(rules: tuple[str, ...]) -> str:
    """generation_playbook.playbook_text() 와 같은 모양으로 지침 블록을 만든다."""
    lines = ["[작성 지침 — 앞선 생성 시행에서 얻은 규칙. 아래를 반드시 지킨다]"]
    lines.extend(f"{i}. {rule}" for i, rule in enumerate(rules, start=1))
    avoid = ", ".join(f"'{w}'" for w in AVOID_PHRASES)
    lines.append(f"{len(rules) + 1}. 아래 표현은 이전 문서에서 너무 반복돼 쓰지 않는다: {avoid}.")
    return "\n".join(lines)


def _with(rule2: str, rule6: str) -> tuple[str, ...]:
    rules = list(GENERATION_RULES)
    rules[1] = rule2
    rules[5] = rule6
    return tuple(rules)


_ORIG_PLAYBOOK_TEXT = gen_mod._playbook_text  # P 팔이 모듈 원본을 그대로 쓰려고 보관한다.

# 팔마다 (지침 문구, 붙이는 방식).
#   product = 2026-09-29 이후 실제 배포 경로 그대로(구조 요구를 안 주고 모듈의 playbook_text() 를 그대로 씀).
#   append  = 2026-09-29 이전 생성기 방식. 기본 구조 문구("문서 유형에 자연스러운 여러 절과 항목을 사용하고 …") 뒤에 지침을 덧붙인다.
#   replace = 9/26 예비 시험 방식. 기본 구조 문구를 지침으로 갈아 끼운다(structure_requirements 로 넘김).
ARMS: dict[str, tuple[str, str]] = {
    "A": (render(_with(_RULE2, OLD_RULE6)), "append"),             # 2026-09-29 이전 현행
    "B": (render(_with(_RULE2, RULE6_FIXED)), "append"),           # A + 규칙 6 의 사람 문구 수정
    "C": (render(_with(RULE2_PRECEDENCE, RULE6_FIXED)), "append"), # B + 규칙 2 우선순위 문장
    "D": (render(_with(_RULE2, RULE6_FIXED)), "replace"),          # B 문구를 기본 구조 문구 대신 넣음
    "E": (render(_with(_RULE2, RULE6_NOEX)), "replace"),           # D 에서 6번의 예시를 뺌 — 채택 후보
    "P": (None, "product"),                                        # 채택 후 실제 경로 검증(모듈 원본)
}
# 규칙 6 을 고치기 전(2026-09-29 이전) 모듈에서만 성립한다 — 고친 뒤에는 A 가 '옛 문구'를 재현하는 팔이다.
if GENERATION_RULES[5] == OLD_RULE6:
    assert ARMS["A"][0] == playbook_text(), "A 팔이 현행 playbook_text() 와 다르다"

# 콘솔 「학습 후보 생성」이 고를 수 있는 도메인으로 등급마다 4건 = 16건.
REQUESTS: list[tuple[str, str]] = [
    ("TS", "반도체"), ("TS", "배터리"), ("TS", "ma"), ("TS", "security"),
    ("S1", "경영정보"), ("S1", "소프트웨어"), ("S1", "화학_제약"), ("S1", "배터리"),
    ("S2", "hr"), ("S2", "finance"), ("S2", "경영정보"), ("S2", "legal"),
    ("S3", "public"), ("S3", "public"), ("S3", "business"), ("S3", "기타"),
]

# ── 측정 ───────────────────────────────────────────────────────────────────
SEC_TAG = re.compile(r"보안\s?등급|비밀\s?등급|기밀\s?등급|분류\s?등급|보안\s?수준|Confidential|CONFIDENTIAL")
REPORT_HEAD = re.compile(r"^\s*(\d+[.)]|[■□●○▶#]+)\s*(개요|배경|목적|현황|결론|요약)", re.M)
HEADING_LINE = re.compile(r"^\s*(#{1,4}\s|\d+[.)]\s|[■□●○▶]\s)", re.M)
TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$", re.M)
CLOSING = re.compile(r"후속\s?조치|완료\s?(기준|판정)|책임\s?역할")
INFORMAL_TYPE = re.compile(r"메신저|대화|메모|이메일|메일|회의록|채팅|노트|일지|전사|쪽지|협조")
# 사람 이름 프록시 — 흔한 성 + 두 글자 + 직함. 같은 정규식을 세 팔에 똑같이 쓰므로 오탐도 같이 깔린다.
_SURNAMES = "김이박최정강조윤장임한오서신권황안송류홍전고문양손배백허유남심노하곽성차주우구민나진지엄채원천방공현함변염여추도소석선설마길연"
PERSON_NAME = re.compile(
    rf"(?<![가-힣])[{_SURNAMES}][가-힣]{{2}}\s?(과장|부장|팀장|대리|사원|차장|이사|상무|전무|연구원|책임|선임|매니저|수석|실장|본부장|파트장|센터장|대표|님|씨)"
)
ROLE_BRACKET = re.compile(r"\[[가-힣A-Za-z0-9 ]{2,20}\]")


def shingles(text: str, n: int = 4) -> set[str]:
    t = re.sub(r"\s+", "", text)
    return {t[i:i + n] for i in range(max(0, len(t) - n + 1))}


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if (a | b) else 0.0


def _tail(body: str) -> str:
    return body[int(len(body) * 0.75):]


def measure(rows: list[dict]) -> dict:
    ok = [r for r in rows if r.get("body") and not r.get("parse_error") and not r.get("label_source")]
    m: dict = {"요청": len(rows), "정상 생성": len(ok)}
    if not ok:
        return m
    m["평균 본문 글자"] = round(sum(len(r["body"]) for r in ok) / len(ok))
    m["출력 토큰 한도 도달"] = sum(1 for r in ok if any(a.get("token_limit_reached") for a in r.get("audit", [])))
    # 게이트(콘솔 경로가 실제로 거르는 것)
    m["게이트: 등급 낱말"] = sum(1 for r in ok if _exposes_grade_token(r["title"] + "\n" + r["body"]))
    m["게이트: 자기 기밀 진술"] = sum(1 for r in ok if _exposes_self_grade_declaration(r["body"]))
    m["'보안 등급' 류 표기"] = sum(1 for r in ok if SEC_TAG.search(r["title"] + r["body"]))
    m["개인정보 정규식 적중"] = sum(1 for r in ok if r.get("pii"))
    m["품질 하한 미달(표시만 되는 값)"] = sum(1 for r in ok if _document_quality_errors(r["body"]))
    # 충돌 ① 사람 이름
    m["사람 이름 프록시(성+2글자+직함)가 든 문서"] = sum(1 for r in ok if PERSON_NAME.search(r["body"]))
    m["대괄호 역할 표기가 든 문서"] = sum(1 for r in ok if ROLE_BRACKET.search(r["body"]))
    m["시스템 프롬프트 예시 낱말 [공정책임자…] 이 든 문서"] = sum(1 for r in ok if "[공정책임자" in r["body"])
    # 충돌 ② 양식
    m["보고서식 구조(1. 개요 …)가 든 문서"] = sum(1 for r in ok if REPORT_HEAD.search(r["body"]))
    m["제목·번호 줄 평균 개수"] = round(sum(len(HEADING_LINE.findall(r["body"])) for r in ok) / len(ok), 2)
    m["표가 든 문서"] = sum(1 for r in ok if len(TABLE_LINE.findall(r["body"])) >= 2)
    m["끝 25% 에 후속조치·완료기준·책임역할이 든 문서"] = sum(1 for r in ok if CLOSING.search(_tail(r["body"])))
    m["비정형 양식(메신저·메모·이메일·회의록 등) 유형 문서"] = sum(1 for r in ok if INFORMAL_TYPE.search(r["doc_type"]))
    m["서로 다른 문서 유형 수"] = len({r["doc_type"] for r in ok})
    sh = [shingles(r["body"]) for r in ok]
    pairs = [jaccard(a, b) for a, b in itertools.combinations(sh, 2)]
    m["문서 사이 표현 겹침(글자 4-gram 자카드 평균)"] = round(sum(pairs) / len(pairs), 4) if pairs else None
    m["반복 금지 낱말이 든 문서"] = sum(1 for r in ok if any(w in r["body"] for w in AVOID_PHRASES))
    return m


# ── 실행 ───────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--model", default="qwen3:14b")
    ap.add_argument("--base-url", default="http://localhost:11434/v1")
    ap.add_argument("--limit", type=int, default=0, help="앞에서 N 건만(0=전부)")
    ap.add_argument("--arms", default="P", help="쉼표로 구분한 팔 이름(A,B,C,D,E,P)")
    args = ap.parse_args()

    out_dir = args.out_dir if os.path.isabs(args.out_dir) else os.path.join(_POC, args.out_dir)
    os.makedirs(out_dir, exist_ok=True)
    arms = [a for a in args.arms.split(",") if a in ARMS]
    requests = REQUESTS[: args.limit] if args.limit else REQUESTS

    llm = LocalOpenAIProvider(base_url=args.base_url, api_key="ollama", model=args.model, provider_label="ollama")
    gen_append = SyntheticDocGenerator(llm, use_playbook=True)
    gen_replace = SyntheticDocGenerator(llm, use_playbook=False)
    print(f"모델 {args.model} · 팔 {arms} · 요청 {len(requests)}건", flush=True)

    rows: dict[str, list[dict]] = {a: [] for a in arms}
    files = {a: open(os.path.join(out_dir, f"docs_{a}.jsonl"), "w", encoding="utf-8") for a in arms}
    try:
        for grade, domain in requests:
            for arm in arms:
                text, mode = ARMS[arm]
                # 생성기가 부르는 별칭만 바꾼다(모듈 원본은 그대로). product 팔은 원본을 그대로 쓴다.
                gen_mod._playbook_text = _ORIG_PLAYBOOK_TEXT if text is None else (lambda t=text: t)
                t0 = time.time()
                try:
                    if mode == "replace":
                        req = SynthRequest(target_grade=grade, domain=domain, count=1, structure_requirements=text)
                        d = gen_replace.generate(req)[0]
                    elif mode == "append":
                        # 옛 방식: 기본 구조 문구를 명시적으로 넘기면 그 뒤에 지침이 붙는다.
                        req = SynthRequest(target_grade=grade, domain=domain, count=1,
                                           structure_requirements=_DEFAULT_STRUCTURE_REQUIREMENTS)
                        d = gen_append.generate(req)[0]
                    else:  # product
                        d = gen_append.generate(SynthRequest(target_grade=grade, domain=domain, count=1))[0]
                    row = dict(
                        arm=arm, grade=grade, domain=domain, sec=round(time.time() - t0),
                        title=d.title, body=d.body, doc_type=d.document_type,
                        parse_error=d.parse_error, label_source=d.label_source,
                        pii=d.pii_violations, audit=d.response_audit,
                    )
                except Exception as exc:  # noqa: BLE001
                    row = dict(arm=arm, grade=grade, domain=domain, sec=round(time.time() - t0),
                               title="", body="", error=f"{type(exc).__name__}: {str(exc)[:120]}")
                rows[arm].append(row)
                files[arm].write(json.dumps(row, ensure_ascii=False) + "\n")
                files[arm].flush()
                print(f"[{arm}] {grade}/{domain} {row['sec']}초 본문 {len(row.get('body', ''))}자 유형={row.get('doc_type', '')}", flush=True)
    finally:
        for f in files.values():
            f.close()

    summary = {a: measure(rows[a]) for a in arms}
    summary["_arms_text_chars"] = {a: len(ARMS[a][0] or playbook_text()) for a in arms}
    with open(os.path.join(out_dir, f"summary_{''.join(arms)}.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print("\n===== 요약 =====")
    keys = list(summary[arms[0]].keys())
    print("지표".ljust(48) + "".join(a.rjust(9) for a in arms))
    for k in keys:
        print(k.ljust(48)[:48] + "".join(str(summary[a].get(k, "")).rjust(9) for a in arms))
    print("AB_DONE", flush=True)


if __name__ == "__main__":
    main()
