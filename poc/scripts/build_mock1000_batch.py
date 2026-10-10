# -*- coding: utf-8 -*-
"""지재원 요청 — 모의문서 1,000건(등급당 250건), 등급·도메인 분산 설계 반영.

오늘(2026-10-01~02) 검증에서 확인된 것 반영:
  - 등급·도메인을 분리한다(부서 하나가 한 등급에 몰리지 않게, 도메인을 고르게 순회).
  - 생성규칙 1번을 좁힌다(누가 보는지·어디 두는지는 안 씀).
  - 등급별 상황문을 3버전으로 돌린다(고정 어휘 지문 완화). TS의 "보안" 과다 의존은
    이번 판에서 줄였다(실측: 학습된 분류기의 TS 1위 가중치 낱말이 "보안"이었음).
  - 자동 게이트(등급 낱말·자기진술·PII·반복금지어)는 적용하되 **떨어뜨리지 않고 표시만**
    한다 — 검수자가 직접 보고 판단한다(안내문 원칙과 동일).

⚠ 알려진 한계(숨기지 않음, 지재원에 같이 안내할 것):
  - 통계적 지름길(본문 전체 글자 패턴)은 오늘 두 차례 실험에서 프롬프트 수정으로는
    안 풀렸다 — 등급별로 원래 다른 주제를 쓰게 설계돼 있어서다. 이 1,000건도 같은
    성격을 가질 가능성이 높다.
  - 검수(사람)는 "등급이 맞는지"는 확인해 주지만, 이 통계적 지름길(여러 문서를 통계로
    봐야 보이는 것)은 검수로 안 잡힌다.
  - 그래서 이 1,000건은 검수를 거쳐도, 통계적 지름길 검사(가족단위+라벨섞기)를 별도로
    통과하기 전까지는 "실문서 정확도의 근거"로 쓰지 않는다(회귀·규칙준수 시험용).

실행: 로컬 Ollama(qwen3:14b), 동시 4건 처리. 1,000건 예상 소요 약 2~3시간.
"""
from __future__ import annotations

import collections
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

_POC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_POC, "src"))
os.environ.setdefault("TESTING", "1")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from koipa.adapters.llm.local_openai_provider import LocalOpenAIProvider  # noqa: E402
from koipa.modules.m1_synthesis import generator as gen_mod  # noqa: E402
from koipa.modules.m1_synthesis.generator import SyntheticDocGenerator, SynthRequest  # noqa: E402
from koipa.services.synth_quality import (  # noqa: E402
    _document_quality_errors,
    _exposes_grade_token,
    _exposes_self_grade_declaration,
)

GRADES = ["TS", "S1", "S2", "S3"]
N_PER_GRADE = 250
N_TOTAL = N_PER_GRADE * len(GRADES)
N_WORKERS = 4
MODEL = "qwen3:14b"

DOMAINS = [
    "tech", "business", "finance", "hr", "legal", "mixed", "security", "ma",
    "semiconductor", "bio", "배터리", "반도체", "화학_제약", "바이오_농업",
    "소프트웨어", "경영정보", "기타",
]

OUT_DIR = os.path.join(_POC, "reports", "mock1000_batch_20261002")

# ── 좁힌 규칙 1 (오늘 검증한 것과 동일) ───────────────────────────────────────
NARROWED_RULE_1 = (
    "문서가 자기 자신의 기밀·보안 수준을 말하지 않는다. '이 문서는 …기밀/비밀/대외비', "
    "'보안 등급:', '유출 금지 문서', '임원 이상 공유 가능' 같은 자기 진술을 쓰지 않는다. "
    "민감함은 업무에서 무엇을 다루는지(내용)로만 드러낸다. 누가 이 문서를 보는지(접근 "
    "권한자·승인권자·열람 대상)와 문서를 어디에 보관·관리하는지는 쓰지 않는다."
)


def _patched_generation_rules():
    from koipa.modules.m1_synthesis.generation_playbook import GENERATION_RULES  # noqa: PLC0415

    rules = list(GENERATION_RULES)
    rules[0] = NARROWED_RULE_1
    return tuple(rules)


def _patched_playbook_text():
    from koipa.modules.m1_synthesis.generation_playbook import AVOID_PHRASES  # noqa: PLC0415

    rules = _patched_generation_rules()
    lines = ["[작성 지침 — 앞선 생성 시행에서 얻은 규칙. 아래를 반드시 지킨다]"]
    lines.extend(f"{i}. {r}" for i, r in enumerate(rules, start=1))
    avoid = ", ".join(f"'{w}'" for w in AVOID_PHRASES)
    lines.append(f"{len(rules) + 1}. 아래 표현은 이전 문서에서 너무 반복돼 쓰지 않는다: {avoid}.")
    return "\n".join(lines)


# ── 등급당 3버전 상황문 — TS의 "보안" 의존을 줄인 판(오늘 실측 반영) ──────────────
_VARIANTS = {
    "TS": [
        {
            "situation": ("유출 시 회사 존립 또는 국가 안보에 심각한 피해를 줄 수 있는 최고 전략 자료. "
                          "예: 아직 공개되지 않은 핵심 원천기술 내용, 진행 중인 비공개 M&A 실사, "
                          "정부 방산 기밀에 준하는 기술 명세, 양산 전 단계의 독자 공정 전체 도면."),
            "disclosure_scope": "극소수 C-level 임원 및 특정 업무 담당자만 접근 가능. 외부 공유 절대 불가.",
            "harm_potential": "경쟁사 기술 복제, 진행 중인 인수협상 무력화, 핵심 설비 노하우 유출 가능.",
        },
        {
            "situation": ("새어 나가면 회사의 존속 자체를 위협하거나 국가 차원의 피해로 이어질 수 있는 "
                          "자료. 예: 비공개 지분 거래 추진 상황, 특허 출원 전 핵심 발명 내용, "
                          "국가 지정 전략기술에 준하는 사양서, 독점 공급 계약의 세부 조건."),
            "disclosure_scope": "대표이사와 극히 일부 책임자만 열람. 조직 밖으로는 절대 전달되지 않음.",
            "harm_potential": "모방 제품 출시, 추진 중이던 지분 거래 무산, 독점 지위 상실 가능성.",
        },
        {
            "situation": ("외부에 알려지는 순간 사업 전체가 흔들릴 수 있는 최상위 수준의 자료. "
                          "예: 공개 전인 차세대 설계도, 극비 제휴 협상 진행 사항, 수년간 축적한 "
                          "핵심 제조 레시피, 아직 등록되지 않은 원천 특허 아이디어."),
            "disclosure_scope": "소수의 최고위 책임자만 접근. 조직 경계를 넘는 전달은 일어나지 않음.",
            "harm_potential": "기술 복제로 인한 시장 선점권 상실, 추진 중인 제휴 백지화, 특허 선점 기회 상실.",
        },
    ],
    "S1": [
        {
            "situation": ("유출 시 회사의 경쟁우위나 사업 기회에 상당한 피해를 주는 영업비밀 수준 자료. "
                          "예: 핵심 공정 노하우, 원가 구조, 주요 고객사 데이터, 미공개 마케팅 전략."),
            "disclosure_scope": "해당 사업부 임원·팀장급 이상. 외부 공유 금지.",
            "harm_potential": "경쟁사 가격 역산, 고객 이탈 유도, 영업 전략 무력화 가능.",
        },
        {
            "situation": ("경쟁사 손에 들어가면 사업 기회를 직접 빼앗길 수 있는 자료. 예: 특정 거래처와의 "
                          "단가 체계, 축적된 생산 숙련 방식, 주요 수주처의 계약 조건, 다음 분기 영업 전략."),
            "disclosure_scope": "담당 본부 책임자급까지만 공유. 조직 밖 인원에게는 전달하지 않음.",
            "harm_potential": "거래 조건 역산으로 인한 가격 경쟁력 상실, 핵심 거래처 이탈, 전략 노출.",
        },
        {
            "situation": ("회사의 사업적 강점을 떠받치는 비공개 자료. 예: 설비 운용의 세부 요령, 공급가 "
                          "협상 내역, 신규 고객군 확보 계획, 외부에 밝히지 않은 품질관리 기법."),
            "disclosure_scope": "관련 임원과 선임급 담당자 한정. 사외로는 넘어가지 않음.",
            "harm_potential": "유사 방식의 모방, 거래 조건 역이용, 확보 예정이던 고객군 선점 가능.",
        },
    ],
    "S2": [
        {
            "situation": ("외부 공개 시 회사 이미지나 협상력에 불이익을 줄 수 있는 내부 검토 자료. "
                          "예: 확정 전 분기 매출 초안, 내부 사업 계획 검토안, 거래처 협상 조건."),
            "disclosure_scope": "내부 검토 단계. 부서 내 공유 가능, 외부 미공개.",
            "harm_potential": "협상력 약화, 미확정 정보로 인한 시장 혼란 가능.",
        },
        {
            "situation": ("아직 확정되지 않아 바깥에 알려지면 오해나 불이익이 생길 수 있는 자료. 예: 검토 "
                          "단계의 조직 개편안, 잠정 실적 집계, 추진 검토 중인 협력 조건."),
            "disclosure_scope": "담당 부서 구성원 간 공유. 외부에는 아직 알리지 않음.",
            "harm_potential": "확정 전 수치가 바깥에 알려져 생기는 혼선, 진행 중인 논의에 불리한 영향.",
        },
        {
            "situation": ("완결되지 않은 상태라 바깥에서 보면 오독될 수 있는 내부 자료. 예: 초안 단계의 "
                          "예산 배분안, 검토 중인 제휴 범위, 잠정 집계된 운영 지표."),
            "disclosure_scope": "관련 부서 내부에서만 열람. 사외 공유는 아직 이뤄지지 않음.",
            "harm_potential": "확정 전 내용이 바깥에 알려지며 생기는 혼란, 진행 중인 논의에 불리한 영향.",
        },
    ],
    "S3": [
        {
            "situation": ("이미 외부에 공개되었거나 공개를 전제로 작성된 자료로, 누구나 열람해도 회사에 "
                          "불이익이 없다. 예: 배포된 보도자료, 채용 공고, 법정 공시, 회사 소개, 이용약관, "
                          "고객 안내 FAQ. **미공개 수치·내부 전략·협상 조건·원가·매출 추정·고객 명단·"
                          "기술 사양은 절대 포함하지 않는다** — 포함되면 더 이상 공개 자료가 아니다. "
                          "문체도 대외 홍보·안내문처럼 작성한다."),
            "disclosure_scope": "일반 대중에게 공개됨(또는 공개 예정). 접근 제한 없음.",
            "harm_potential": "없음 — 이미 공개된 정보 수준이라 유출로 인한 피해가 성립하지 않는다.",
        },
        {
            "situation": ("누구나 볼 수 있도록 바깥에 내놓은 자료. 예: 홈페이지 공지, 행사 안내문, 모집 "
                          "안내, 제품 소개 페이지, 약관 사본. **미확정 수치·내부 방침·거래 조건·원가·"
                          "실적 전망·고객 정보·설계 사양은 넣지 않는다** — 넣으면 공개 자료가 아니게 된다. "
                          "말투도 대외 안내·소개문처럼 쓴다."),
            "disclosure_scope": "누구나 접근 가능하도록 이미 열려 있음. 별도 제한 없음.",
            "harm_potential": "없음 — 원래부터 바깥에 내놓은 내용이라 추가로 새어 생기는 불이익이 없다.",
        },
        {
            "situation": ("공개를 목적으로 작성돼 이미 바깥에 나가 있는 자료. 예: 설명회 자료, 공지문, "
                          "모집 요강, 서비스 소개, 공식 발표문. **아직 안 알려진 수치·내부 전략·계약 "
                          "조건·비용·매출 전망·거래처 목록·기술 세부사항은 담지 않는다** — 담기면 공개 "
                          "자료의 성격을 벗어난다. 어조도 대외 공지·소개 자료처럼 쓴다."),
            "disclosure_scope": "대외적으로 열람에 제한이 없는 상태.",
            "harm_potential": "없음 — 애초에 공개를 전제로 작성돼 유출 개념이 성립하지 않는다.",
        },
    ],
}


def patched_situation_prompts(seed: int = 20261002):
    rng = __import__("random").Random(seed)

    class _RotatingPrompts(dict):
        def get(self, key, default=None):
            variants = _VARIANTS.get(key)
            return rng.choice(variants) if variants else super().get(key, default)

        def __getitem__(self, key):
            variants = _VARIANTS.get(key)
            if variants is None:
                raise KeyError(key)
            return rng.choice(variants)

    return _RotatingPrompts()


def build_requests() -> list[tuple[str, str]]:
    rng = __import__("random").Random(20261002)
    reqs: list[tuple[str, str]] = []
    for grade in GRADES:
        doms = DOMAINS.copy()
        rng.shuffle(doms)
        picked = (doms * ((N_PER_GRADE // len(doms)) + 1))[:N_PER_GRADE]
        rng.shuffle(picked)
        reqs.extend((grade, d) for d in picked)
    rng.shuffle(reqs)  # 생성 순서도 섞는다 — 등급이 몰려서 돌지 않게.
    return reqs


_LOCK_PRINT = __import__("threading").Lock()


def generate_one(gen: SyntheticDocGenerator, idx: int, grade: str, domain: str) -> dict:
    t0 = time.time()
    try:
        d = gen.generate(SynthRequest(target_grade=grade, domain=domain, count=1))[0]
        row = dict(
            review_id=f"MK-{idx:04d}", true_grade=grade, domain=domain,
            title=d.title, body=d.body, doc_type=d.document_type,
            parse_error=d.parse_error, label_source=d.label_source, pii=d.pii_violations,
        )
    except Exception as exc:  # noqa: BLE001
        row = dict(review_id=f"MK-{idx:04d}", true_grade=grade, domain=domain,
                   title="", body="", error=f"{type(exc).__name__}: {str(exc)[:160]}")
    with _LOCK_PRINT:
        print(f"[{idx + 1}/{N_TOTAL}] {grade}/{domain} {time.time() - t0:.0f}초 "
              f"본문 {len(row.get('body', ''))}자", flush=True)
    return row


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    gen_mod._playbook_text = _patched_playbook_text
    gen_mod.GRADE_SITUATION_PROMPTS = patched_situation_prompts()

    reqs = build_requests()
    print(f"총 {len(reqs)}건 · 동시 {N_WORKERS}건 · 모델 {MODEL}", flush=True)

    # 생성기 인스턴스를 워커마다 따로 둔다(LLM 클라이언트 공유로 인한 경합 방지).
    def _make_gen():
        llm = LocalOpenAIProvider(base_url="http://localhost:11434/v1", api_key="ollama",
                                   model=MODEL, provider_label="ollama")
        return SyntheticDocGenerator(llm)

    gens = [_make_gen() for _ in range(N_WORKERS)]

    rows: list[dict] = [None] * len(reqs)  # noqa: PT018
    out_path = os.path.join(OUT_DIR, "docs_internal.jsonl")
    t_start = time.time()
    with ThreadPoolExecutor(max_workers=N_WORKERS) as ex, open(out_path, "w", encoding="utf-8") as f:
        futs = {ex.submit(generate_one, gens[i % N_WORKERS], i, g, d): i
                for i, (g, d) in enumerate(reqs)}
        done = 0
        for fut in as_completed(futs):
            i = futs[fut]
            row = fut.result()
            rows[i] = row
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            done += 1
            if done % 50 == 0:
                elapsed = time.time() - t_start
                rate = done / elapsed
                eta_min = (len(reqs) - done) / rate / 60 if rate > 0 else -1
                print(f"=== 진행 {done}/{len(reqs)} · 경과 {elapsed/60:.1f}분 · 예상 잔여 {eta_min:.0f}분 ===",
                      flush=True)

    print("\n=== 생성 완료 — 게이트·내보내기 ===", flush=True)
    ok = [r for r in rows if r and r.get("body") and not r.get("parse_error") and not r.get("label_source")]
    bad = [r for r in rows if r and (not r.get("body") or r.get("parse_error") or r.get("label_source"))]
    print(f"정상 {len(ok)} / 실패·대체본 {len(bad)}")

    grade_hits = [r["review_id"] for r in ok if _exposes_grade_token(r["title"] + "\n" + r["body"])]
    decl_hits = [r["review_id"] for r in ok if _exposes_self_grade_declaration(r["body"])]
    pii_hits = [r["review_id"] for r in ok if r.get("pii")]
    quality_flagged = [r["review_id"] for r in ok if _document_quality_errors(r["body"])]
    print(f"등급 낱말 노출 {len(grade_hits)} · 자기진술 {len(decl_hits)} · PII {len(pii_hits)} · "
          f"품질 하한 미달(표시만) {len(quality_flagged)}")

    # 게이트 걸린 것은 검수자용 파일에서 뺀다(등급 낱말·자기진술·PII — 학습 전 안전장치와 동일 기준).
    excluded = set(grade_hits) | set(decl_hits) | set(pii_hits)
    reviewer_rows = [r for r in ok if r["review_id"] not in excluded]

    with open(os.path.join(OUT_DIR, "internal_manifest.jsonl"), "w", encoding="utf-8") as f:
        for r in ok:
            f.write(json.dumps({
                "review_id": r["review_id"], "true_grade": r["true_grade"], "domain": r["domain"],
                "doc_type": r["doc_type"], "excluded_from_review": r["review_id"] in excluded,
            }, ensure_ascii=False) + "\n")

    with open(os.path.join(OUT_DIR, "reviewer_documents.jsonl"), "w", encoding="utf-8") as f:
        for r in reviewer_rows:
            f.write(json.dumps({"review_id": r["review_id"],
                                 "text": r["title"] + "\n\n" + r["body"]}, ensure_ascii=False) + "\n")

    with open(os.path.join(OUT_DIR, "reviewer_documents_ALL_readable.txt"), "w", encoding="utf-8") as f:
        for r in reviewer_rows:
            f.write(f"{'='*70}\n[{r['review_id']}]\n{'='*70}\n{r['title']}\n\n{r['body']}\n\n\n")

    print(f"\n검수자 전달용 {len(reviewer_rows)}건 -> {os.path.join(OUT_DIR, 'reviewer_documents.jsonl')}")
    print(f"등급 분포: {dict(collections.Counter(r['true_grade'] for r in reviewer_rows))}")
    print(f"게이트로 제외된 건수: {len(excluded)} (등급낱말·자기진술·PII — internal_manifest 에 표시만, 본문은 보존)")
    print("BUILD1000_DONE", flush=True)


if __name__ == "__main__":
    main()
