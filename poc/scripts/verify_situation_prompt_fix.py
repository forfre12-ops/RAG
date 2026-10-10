# -*- coding: utf-8 -*-
"""가설 검증 2단계: 등급별 상황문(GRADE_SITUATION_PROMPTS) 고정 어휘가 지름길의 원인인가.

배경(2026-10-01): 1단계(verify_domain_grade_decorrelation.py)에서 도메인을 등급과
분리해도 통계적 지름길이 그대로였다(백분위 1.000, 둘 다 미달). 외부 리뷰 두 건이
"남는 곳은 규칙이 아니라 GRADE_SITUATION_PROMPTS 문구 자체"라고 짚었고, 1단계 산출물을
직접 세어 보니 맞았다 — "팀장급"(S1 전용 6/20) · "노하우"(S1 전용 4/20) · "협상력"(S2
전용 3/20) · "인수협상"(TS 다수) 이 상황문 원문 그대로 본문에 새고 있었다.

이 스크립트는 세 가지를 같이 적용해 다시 잰다:
  (a) 생성규칙 1번을 좁힌다 — "무엇을·누가·어떤 절차로" → "무엇을"만, "누가 보는지·
      어디 두는지는 쓰지 않는다"를 명시.
  (b) GRADE_SITUATION_PROMPTS 를 등급당 3버전으로 늘리고 요청마다 무작위로 하나를 쓴다
      (고정 어휘 지문을 흐린다).
  (c) 생성 직후 "상황문 메아리 검사"를 추가한다 — 상황문 전용 어휘가 본문에 그대로
      옮았는지 자동으로 센다(게이트가 아니라 측정용, 이 스크립트에서는 보고만 한다).

1단계와 **완전히 같은 요청 구성**(등급당 20건, 같은 도메인 순열, 같은 시드)을 재사용해
직접 비교 가능하게 한다. 코드 파일(generator.py·generation_playbook.py)은 바꾸지
않는다 — 모듈 속성을 테스트 범위 안에서만 바꿔치기한다(monkeypatch).
"""
from __future__ import annotations

import collections
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

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import make_pipeline

from koipa.adapters.llm.local_openai_provider import LocalOpenAIProvider  # noqa: E402
from koipa.modules.m1_synthesis import generator as gen_mod  # noqa: E402
from koipa.modules.m1_synthesis.generator import SyntheticDocGenerator, SynthRequest  # noqa: E402
from koipa.services.synth_quality import (  # noqa: E402
    _document_quality_errors,
    _exposes_grade_token,
    _exposes_self_grade_declaration,
)

# 1단계와 동일 — 직접 비교를 위해 그대로 가져온다.
from verify_domain_grade_decorrelation import DOMAINS, GRADES, N_PER_GRADE, build_requests  # noqa: E402

OUT_DIR = os.path.join(_POC, "reports", "verify_situation_prompt_fix_20261001")

# ── (a) 좁힌 규칙 1 ──────────────────────────────────────────────────────────
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


# ── (b) 등급당 3버전 상황문 — 같은 심각도·같은 의도, 다른 어휘·다른 예시 ──────────────
_VARIANTS = {
    "TS": [
        {
            "situation": ("유출 시 회사 존립 또는 국가 안보에 심각한 피해를 줄 수 있는 최고 전략 자료. "
                          "예: 아직 공개되지 않은 핵심 원천기술 내용, 진행 중인 비공개 M&A 실사, "
                          "암호 키·취약점 등 보안 운영 정보, 정부 방산 기밀에 준하는 기술 명세."),
            "disclosure_scope": "극소수 C-level 임원 및 특정 업무 담당자만 접근 가능. 외부 공유 절대 불가.",
            "harm_potential": "경쟁사 기술 복제, 진행 중인 인수협상 무력화, 보안 인프라 침해 가능.",
        },
        {
            "situation": ("새어 나가면 회사의 존속 자체를 위협하거나 국가 차원의 피해로 이어질 수 있는 "
                          "자료. 예: 양산 직전 단계의 미공개 제조 공정, 비공개 지분 거래 추진 상황, "
                          "시스템 침투 경로·암호화 설계 문서, 국가 지정 전략기술에 준하는 사양서."),
            "disclosure_scope": "대표이사와 극히 일부 책임자만 열람. 조직 밖으로는 절대 전달되지 않음.",
            "harm_potential": "모방 제품 출시, 추진 중이던 지분 거래 무산, 시스템 전체 침해 가능성.",
        },
        {
            "situation": ("외부에 알려지는 순간 사업 전체가 흔들릴 수 있는 최상위 수준의 자료. "
                          "예: 공개 전인 차세대 설계도, 극비 제휴 협상 진행 사항, 핵심 인증키·보안 체계 "
                          "설계, 정부 보안 등급에 준하는 산업기술 자료."),
            "disclosure_scope": "소수의 최고위 책임자만 접근. 조직 경계를 넘는 전달은 일어나지 않음.",
            "harm_potential": "기술 복제로 인한 시장 선점권 상실, 추진 중인 제휴 백지화, 보안 체계 붕괴.",
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
            "harm_potential": "확정 전 수치가 바깥에 알려져 생기는 혼선, 협상 중인 조건의 사전 노출.",
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
            "harm_potential": "없음 — 애초에 공개를 전제로 작성돼 유출 개념이 성립하지 않는다."
        },
    ],
}


def patched_situation_prompts(seed: int = 20261001):
    """요청마다 무작위로 등급당 한 버전을 고르는 dict-like 를 만든다.

    GRADE_SITUATION_PROMPTS 는 generator._build_user_prompt 안에서 매번 .get(grade_code,...)
    로 조회된다 — __getitem__/get 을 재정의한 래퍼를 만들어 호출마다 다른 버전을 돌려준다.
    """
    rng = __import__("random").Random(seed)

    class _RotatingPrompts(dict):
        def get(self, key, default=None):
            variants = _VARIANTS.get(key)
            if not variants:
                return super().get(key, default)
            return rng.choice(variants)

        def __getitem__(self, key):
            variants = _VARIANTS.get(key)
            if variants is None:
                raise KeyError(key)
            return rng.choice(variants)

    return _RotatingPrompts()


# ── (c) 상황문 메아리 검사 — 세 버전 전부에서 그 등급에만 나오는 고유 낱말을 뽑는다 ──
_WORD_RE = re.compile(r"[가-힣]{2,}")
_STOPWORDS = {"가능", "자료", "예시", "회사", "경우", "이상", "이하", "없음", "내부", "외부",
              "공개", "확인", "진행", "관련", "포함", "수준", "원래", "제한", "작성", "체계"}


def build_echo_terms() -> dict[str, set[str]]:
    all_words_by_grade: dict[str, collections.Counter] = {g: collections.Counter() for g in GRADES}
    for g, variants in _VARIANTS.items():
        for v in variants:
            text = v["situation"] + " " + v["disclosure_scope"] + " " + v["harm_potential"]
            for w in set(_WORD_RE.findall(text)) - _STOPWORDS:
                all_words_by_grade[g][w] += 1
    # 그 등급의 "모든 버전"에 걸쳐 2회 이상 반복되고 다른 등급엔 전혀 안 나오는 낱말만
    # "그 등급 고유 지문 후보"로 본다(어느 한 버전에만 쓴 1회성 낱말은 애초에 안전하다).
    echo_terms: dict[str, set[str]] = {}
    for g in GRADES:
        own = {w for w, c in all_words_by_grade[g].items() if c >= 2}
        others = set()
        for og in GRADES:
            if og != g:
                others |= set(all_words_by_grade[og].keys())
        echo_terms[g] = own - others
    return echo_terms


def generate(out_dir: str, model: str = "qwen3:14b") -> list[dict]:
    os.makedirs(out_dir, exist_ok=True)
    llm = LocalOpenAIProvider(base_url="http://localhost:11434/v1", api_key="ollama", model=model, provider_label="ollama")

    # (a) 규칙 좁히기 — generation_playbook.playbook_text() 가 부르는 별칭만 바꾼다.
    patched_rules = _patched_generation_rules()

    def _patched_playbook_text():
        from koipa.modules.m1_synthesis.generation_playbook import AVOID_PHRASES  # noqa: PLC0415

        lines = ["[작성 지침 — 앞선 생성 시행에서 얻은 규칙. 아래를 반드시 지킨다]"]
        lines.extend(f"{i}. {r}" for i, r in enumerate(patched_rules, start=1))
        avoid = ", ".join(f"'{w}'" for w in AVOID_PHRASES)
        lines.append(f"{len(patched_rules) + 1}. 아래 표현은 이전 문서에서 너무 반복돼 쓰지 않는다: {avoid}.")
        return "\n".join(lines)

    gen_mod._playbook_text = _patched_playbook_text
    # (b) 상황문 로테이션.
    gen_mod.GRADE_SITUATION_PROMPTS = patched_situation_prompts()

    gen = SyntheticDocGenerator(llm)
    reqs = build_requests()  # 1단계와 완전히 같은 (등급, 도메인) 80개, 같은 시드.
    rows: list[dict] = []
    out_path = os.path.join(out_dir, "docs.jsonl")
    with open(out_path, "w", encoding="utf-8") as f:
        for i, (grade, domain) in enumerate(reqs):
            t0 = time.time()
            try:
                d = gen.generate(SynthRequest(target_grade=grade, domain=domain, count=1))[0]
                row = dict(
                    review_id=f"SP-{i:04d}", true_grade=grade, domain=domain,
                    title=d.title, body=d.body, doc_type=d.document_type,
                    parse_error=d.parse_error, label_source=d.label_source,
                    pii=d.pii_violations,
                )
            except Exception as exc:  # noqa: BLE001
                row = dict(review_id=f"SP-{i:04d}", true_grade=grade, domain=domain,
                           title="", body="", error=f"{type(exc).__name__}: {str(exc)[:160]}")
            rows.append(row)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            print(f"[{i + 1}/{len(reqs)}] {grade}/{domain} {time.time() - t0:.0f}초 "
                  f"본문 {len(row.get('body', ''))}자", flush=True)
    return rows


def gate_check(rows: list[dict]) -> dict:
    ok = [r for r in rows if r.get("body") and not r.get("parse_error") and not r.get("label_source")]
    grade_hits = [r["review_id"] for r in ok if _exposes_grade_token(r["title"] + "\n" + r["body"])]
    decl_hits = [r["review_id"] for r in ok if _exposes_self_grade_declaration(r["body"])]
    pii_hits = [r["review_id"] for r in ok if r.get("pii")]
    quality_flagged = [r["review_id"] for r in ok if _document_quality_errors(r["body"])]
    return dict(n_generated=len(rows), n_ok=len(ok), grade_token_exposed=grade_hits,
                self_grade_declaration=decl_hits, pii_detected=pii_hits,
                quality_flagged_count=len(quality_flagged))


def echo_check(rows: list[dict], echo_terms: dict[str, set[str]]) -> dict:
    ok = [r for r in rows if r.get("body")]
    hit_counts = {g: 0 for g in GRADES}
    detail = collections.defaultdict(list)
    for r in ok:
        g = r["true_grade"]
        hits = [t for t in echo_terms.get(g, set()) if t in r["body"]]
        if hits:
            hit_counts[g] += 1
            detail[g].append((r["review_id"], hits))
    return dict(hit_counts=hit_counts, n_per_grade=N_PER_GRADE, detail={k: v for k, v in detail.items()})


def _make_clf():
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=1, max_features=4000)
    clf = LogisticRegression(max_iter=2000, class_weight="balanced")
    return make_pipeline(vec, clf)


def _cv_accuracy(texts, labels, seed: int, n_splits: int = 3) -> float:
    y = np.array(labels)
    skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=seed)
    correct = total = 0
    for train_idx, test_idx in skf.split(texts, y):
        model = _make_clf()
        model.fit([texts[i] for i in train_idx], y[train_idx])
        pred = model.predict([texts[i] for i in test_idx])
        correct += int((pred == y[test_idx]).sum())
        total += len(test_idx)
    return correct / total


def audit(rows: list[dict], out_dir: str) -> dict:
    ok = [r for r in rows if r.get("body") and not r.get("parse_error") and not r.get("label_source")]
    texts = [r["title"] + "\n" + r["body"] for r in ok]
    grades = [r["true_grade"] for r in ok]

    n_repeats, n_perms = 30, 300
    real_accs = [_cv_accuracy(texts, grades, seed=s) for s in range(n_repeats)]
    real_mean = float(np.mean(real_accs))

    rng = np.random.default_rng(20261001)
    perm_accs = []
    for p in range(n_perms):
        shuffled = list(grades)
        rng.shuffle(shuffled)
        perm_accs.append(_cv_accuracy(texts, shuffled, seed=p))
    perm_arr = np.array(perm_accs)
    percentile = float((perm_arr < real_mean).mean())

    result = dict(
        n_docs=len(ok), class_counts=dict(collections.Counter(grades)),
        ngram_classifier=dict(
            real_accuracy_mean=real_mean, real_accuracy_std=float(np.std(real_accs)),
            permutation_mean=float(perm_arr.mean()), permutation_max=float(perm_arr.max()),
            percentile_rank_of_real=percentile,
            verdict="통과" if percentile <= 0.95 else "미달(잔여 지름길 의심)",
        ),
        comparison=dict(
            step1_decorrelation_only_percentile=1.000, step1_verdict="미달",
            guide40_percentile=1.000, guide40_verdict="미달",
        ),
    )
    with open(os.path.join(out_dir, "audit_result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


def main() -> None:
    print("=== 0단계: 등급별 메아리 후보 낱말(3버전 공통, 다른 등급엔 없음) ===", flush=True)
    echo_terms = build_echo_terms()
    for g in GRADES:
        print(f"  {g}: {sorted(echo_terms[g])}")

    print("\n=== 1단계: 생성(규칙 좁히기+상황문 로테이션 적용) ===", flush=True)
    rows = generate(OUT_DIR)

    print("\n=== 2단계: 규칙 게이트 ===", flush=True)
    gates = gate_check(rows)
    print(json.dumps({k: v for k, v in gates.items() if not isinstance(v, list)}, ensure_ascii=False, indent=1))

    print("\n=== 3단계: 상황문 메아리 검사(적용 전/후 비교의 핵심) ===", flush=True)
    echo = echo_check(rows, echo_terms)
    print(f"등급별 메아리 검출 문서 수: {echo['hit_counts']} (각 등급 분모 {N_PER_GRADE})")
    for g, items in echo["detail"].items():
        for rid, hits in items[:3]:
            print(f"  {g} {rid}: {hits}")

    print("\n=== 4단계: 통계적 지름길 감사 ===", flush=True)
    result = audit(rows, OUT_DIR)
    ng = result["ngram_classifier"]
    print(f"문서 {result['n_docs']}건 · {result['class_counts']}")
    print(f"[글자 n-gram] 실제 {ng['real_accuracy_mean']:.3f} · 무작위 평균 {ng['permutation_mean']:.3f}"
          f"(최댓값 {ng['permutation_max']:.3f}) · 백분위 {ng['percentile_rank_of_real']:.3f} · {ng['verdict']}")
    print(f"\n[1단계(도메인분리만) 대비] 1단계 백분위 1.000(미달) vs 이번(규칙+상황문까지) "
          f"{ng['percentile_rank_of_real']:.3f}({ng['verdict']})")
    with open(os.path.join(OUT_DIR, "echo_check.json"), "w", encoding="utf-8") as f:
        json.dump({"hit_counts": echo["hit_counts"], "n_per_grade": N_PER_GRADE}, f, ensure_ascii=False, indent=1)
    print("VERIFY2_DONE", flush=True)


if __name__ == "__main__":
    main()
