"""B — 외부 사실 앵커 코퍼스 어셈블러 (평가셋 앵커 중심 다층 평가, 2026-06-29).

앵커 = '사람 서명'이 아니라 **외부 사실에 등급이 그라운딩된** 문서. 사람 서명이 영구
불가능한 환경에서 평가 신뢰의 닻이 된다(설계서 §03 앵커 중심 다층 평가). 두 부류만
등급 앵커로 인정한다:

  - NKT 특허(patent_proxy): KIPO 국가핵심/중점기술 소분류 라벨에 등급이 묶임 → TS/S1.
    (키워드·IPC 휴리스틱이 아니라 공식 분류라벨 — 건조기를 TS로 오라벨한 그 휴리스틱이 아니다.)
  - 큐레이트 홀드아웃(gold_real): 누출 제거 + 근거(evidence_spans/legal_reference) 부착,
    전 등급(TS/S1/S2/S3). 공개 판결문은 '축자 함정'상 S3(비밀성 요건 미충족).

**의도적 제외**: trade_secret_cases/raw.jsonl(판례 원문)은 등급 필드가 없는 *원자료*다.
판시사항의 고등급 신호를 내부문서체로 **재작성(LLM)** 해야 비로소 S1/S2 앵커가 된다.
재작성 전 원문을 그대로 등급 앵커로 쓰면 오라벨(공개 원문=정의상 S3)이므로 코퍼스에서 뺀다.
(재작성 트랙은 D 패러프레이즈 생성 파이프라인과 함께 — 이 모듈은 등급이 이미 확정된 것만 싣는다.)

순수 — 모델·LLM 불요. 정규화/필수토큰 추출은 dict만 받는 순수 함수(단위테스트 가능).
로더(load_anchor_corpus)만 파일 IO. required_tokens는 D(메타모픽 사실보존)용 — 카드(C)는 불요.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

VALID_GRADES = ("TS", "S1", "S2", "S3")

# NKT 소분류명에서 TS(국가핵심기술 핵심분야) 신호 키워드 — kipris_aihub_nkt_ingest와 동일 집합.
# grade_basis가 'TS-kw:<kw>' 꼴이면 그 kw가 등급 결정 사실. (S1-default는 일반 NKT.)
_TS_KW_RE = re.compile(r"TS-kw:([^:|\s]+)")

# 본문 구체값(수치+단위) — 등급을 가르는 '특정 사실'의 결정적 후보. 단어 접두 포함 캡처.
# (역방향 적격 evidence@N 보강용. NKT 특허 추상은 수치가 드물어 주로 gold 구조화문서에서 잡힌다.)
_BODY_NUM_RE = re.compile(
    r"[가-힣A-Za-z][가-힣A-Za-z·\s]{0,12}?\s?"
    r"\d{1,4}(?:[.,]\d+)?\s?"
    r"(?:%|nm|fps|ms|μm|GHz|MHz|kW|W|V|℃|배|억원|억|만원|만|건|개월|분기|차)"
)
# 인용·강조로 묶인 특정 표현(고유 사실 신호): 「…」 『…』 '…' "…"
_BODY_QUOTE_RE = re.compile(r"[「『\"']([가-힣A-Za-z0-9][^「『\"'」』]{1,28})[」』\"']")


def extract_body_fact_tokens(text: str, *, max_tokens: int = 3, min_len: int = 3) -> list[str]:
    """본문(제목=첫 줄 제외)에서 **등급결정 구체값**(수치+단위·인용 특정표현)을 결정적 추출.

    역방향(사실 제거) 테스트의 적격 토큰(evidence@N급)을 보강한다 — 제목/도메인어와 달리
    이 구체값은 빼면 등급결정 사실이 실제로 사라질 수 있다. **구조적 한계**: NKT 특허 추상은
    수치 구체값이 드물어 거의 안 잡힌다(특허의 비밀=기술개시 전체, 분리 불가) → 주로 gold
    구조화문서에서 잡힘. 안 잡히면 빈 리스트(해당 앵커는 reverse 부적격 유지).
    """
    lines = text.split("\n", 1)
    body = lines[1] if len(lines) > 1 else text
    toks: list[str] = []
    for m in _BODY_NUM_RE.finditer(body):
        frag = m.group(0).strip()
        if len(frag) >= min_len:
            toks.append(frag)
    for m in _BODY_QUOTE_RE.finditer(body):
        frag = m.group(1).strip()
        if len(frag) >= min_len:
            toks.append(frag)
    seen: set[str] = set()
    uniq = [t for t in toks if not (t in seen or seen.add(t))]
    return uniq[:max_tokens]


@dataclass
class AnchorRecord:
    """등급이 외부 사실에 묶인 한 앵커 문서. C 카드의 slice=source, true=anchor_grade."""

    anchor_id: str
    text: str
    anchor_grade: str            # TS/S1/S2/S3 — 외부 사실 그라운딩 정답
    source: str                  # C 카드 slice (그라운딩 방식별 신뢰 묶음)
    grade_basis: str = ""        # 등급 근거(추적용) — nkt 분류코드 / 법령참조 등
    required_tokens: list[str] = field(default_factory=list)  # D 사실보존용(best-effort)
    token_sources: list[str] = field(default_factory=list)    # required_tokens 병렬 출처(역방향 적격성)
    origin: str = ""             # 원본 파일 경로(감사)

    def to_card_record(self) -> dict:
        """build_eval_cards가 먹는 형태 — slice_of=source, true_of=label(=anchor_grade)."""
        return {
            "anchor_id": self.anchor_id,
            "source": self.source,
            "label": self.anchor_grade,
            "text": self.text,
            "grade_basis": self.grade_basis,
        }


def _stable_id(*parts: str) -> str:
    h = hashlib.sha1("".join(parts).encode("utf-8"), usedforsecurity=False).hexdigest()
    return h[:16]


def extract_required_tokens_detailed(
    raw: dict, *, max_tokens: int = 4, min_len: int = 2, include_body_facts: bool = True
) -> list[tuple[str, str]]:
    """required 토큰을 **출처(provenance)와 함께** 추출. 반환 [(token, source), ...].

    source 값(역방향 적격성 판정에 쓰임 — paraphrase_gen.is_reverse_eligible):
      - "evidence@0"  : evidence_span이 본문 맨 앞(start=0) = 사실상 **제목**. 약함(빼도 본문 사실 잔존).
      - "evidence@N"  : 본문 중간 근거 구간(start>0) — 상대적으로 사실에 가까움(reverse 적격).
      - "body_fact"   : 본문 수치/인용 구체값 — 빼면 등급결정 사실이 사라질 수 있음(reverse 적격).
      - "ts_kw"       : NKT grade_basis의 단일 키워드(국핵기 신호) — 단어수준이라 약함.
      - "domain"      : 도메인 폴백(단일 도메인어) — 가장 약함.
    include_body_facts=False면 body_fact 미추출 — NKT 특허처럼 본문 수치가 부수적('3개'·'6개월'
    같은 비-등급결정 숫자)인 출처는 끈다(약토큰 노이즈가 reverse를 오염시키지 않도록).
    우선순위는 evidence > body_fact > ts_kw > domain. 빈 리스트면 D는 '검사 불가'로 처리.
    """
    out: list[tuple[str, str]] = []
    text = raw.get("text", "") or ""

    for span in raw.get("evidence_spans") or []:
        try:
            s, e = int(span.get("start", -1)), int(span.get("end", -1))
        except (TypeError, ValueError):
            continue
        if 0 <= s < e <= len(text):
            frag = text[s:e].strip()
            frag = re.split(r"[\n。.;:]", frag, maxsplit=1)[0].strip()
            if len(frag) > 40:
                frag = frag[:40].rsplit(" ", 1)[0].strip()
            if len(frag) >= min_len:
                out.append((frag, "evidence@0" if s == 0 else "evidence@N"))

    # 본문 구체값(수치/인용) — 역방향 적격 강토큰. NKT 추상은 부수숫자뿐이라 끔(include_body_facts).
    if include_body_facts:
        for frag in extract_body_fact_tokens(text):
            out.append((frag, "body_fact"))

    m = _TS_KW_RE.search(str(raw.get("grade_basis", "")))
    if m:
        out.append((m.group(1), "ts_kw"))

    if not out:
        dom = str(raw.get("domain", "")).strip()
        if dom and dom.lower() not in ("mixed", "?", "unknown") and len(dom) >= min_len:
            out.append((dom, "domain"))

    # 토큰 기준 중복 제거(순서 보존) + 상한
    seen: set[str] = set()
    uniq = [(t, src) for (t, src) in out if not (t in seen or seen.add(t))]
    return uniq[:max_tokens]


def extract_required_tokens(raw: dict, *, max_tokens: int = 4, min_len: int = 2) -> list[str]:
    """required 토큰만(출처 제외). 상세/출처는 extract_required_tokens_detailed 참조.

    D의 check_fact_preservation이 패러프레이즈에 이 토큰이 보존됐는지를 결정적으로 검사한다.
    *후보*일 뿐 완벽 보증이 아니다 — 약한 토큰(제목/도메인어)은 역방향 테스트에 부적격
    (paraphrase_gen.is_reverse_eligible로 거른다).
    """
    return [t for (t, _src) in extract_required_tokens_detailed(raw, max_tokens=max_tokens, min_len=min_len)]


def normalize_nkt(raw: dict, *, source: str = "anchor_nkt", origin: str = "") -> AnchorRecord | None:
    """patent_proxy(NKT 특허) 한 줄 → AnchorRecord. label∈{TS,S1}만 인정."""
    grade = str(raw.get("label", "")).strip()
    text = (raw.get("text") or "").strip()
    if grade not in VALID_GRADES or not text:
        return None
    app = str(raw.get("app_no") or "")
    # NKT 특허 본문 수치는 부수적('3개'·'6개월')이라 body_fact 끔 — 등급결정 사실이 아님.
    detailed = extract_required_tokens_detailed(raw, include_body_facts=False)
    return AnchorRecord(
        anchor_id="nkt:" + (app or _stable_id(text[:120])),
        text=text,
        anchor_grade=grade,
        source=source,
        grade_basis=str(raw.get("grade_basis", "")),
        required_tokens=[t for t, _ in detailed],
        token_sources=[s for _, s in detailed],
        origin=origin,
    )


def normalize_constructed_floor(
    raw: dict, *, source: str = "constructed_floor", origin: str = ""
) -> AnchorRecord | None:
    """constructed_floor admitted.jsonl 한 줄 → AnchorRecord (자체 slice로 격리).

    ⚠️ 라벨 의미론은 **floor(≥등급)**다. under-class FNR 카드에는 정확히 맞물린다:
    floor=S2 문서를 S3(더 공개)로 예측 = 위반(미탐), TS/S1(더 비밀)로 예측 = 위반 아님
    (floor 이상이므로). 즉 이 셀의 FAIL은 '명백 케이스에서조차 미탐' 신호(정당한 veto)다.
    단 카드의 recall(=정확일치) 컬럼은 floor 셀에서 과소평가된다(floor 초과 예측이 '정답'으로
    안 세짐) — verdict(FNR)만 의미 있고 recall은 참고용. eval_cards는 공유 순수모듈이라
    특례를 넣지 않고 여기 문서로 명시한다.

    admitted.jsonl 계약(build_constructed_floor_set): {doc_id, text, label(=floor grade),
    witnesses:[{token,basis}], tier, label_source}. witness 토큰은 등급결정 구체값이라
    required_tokens(D 역방향 적격 body_fact급)로 그대로 싣는다.
    """
    grade = str(raw.get("label") or raw.get("intended_grade") or "").strip()
    text = (raw.get("text") or "").strip()
    if grade not in VALID_GRADES or not text:
        return None
    did = str(raw.get("doc_id") or "")
    witnesses = raw.get("witnesses") or []
    tokens = [str(w.get("token") or "").strip() for w in witnesses if w.get("token")]
    bases = [str(w.get("basis") or "").strip() for w in witnesses if w.get("basis")]
    # 무근거 floor 주장 금지(constructed_floor 원칙) — witness 없는 레코드는 등급 앵커 불가.
    # 빌더는 항상 witness를 넣지만, 손상/외부 파일이 그 보장 없이 흘러들어도 안전-skip.
    if not tokens:
        return None
    return AnchorRecord(
        anchor_id="cf:" + (did or _stable_id(text[:120])),
        text=text,
        anchor_grade=grade,
        source=source,
        grade_basis=(bases[0][:160] if bases else str(raw.get("witness_id") or "")),
        required_tokens=[t for t in tokens if len(t) >= 2],
        token_sources=["body_fact"] * len([t for t in tokens if len(t) >= 2]),
        origin=origin,
    )


def normalize_patent_timeaxis_prepub(
    raw: dict, *, source: str = "patent_timeaxis_prepub", origin: str = ""
) -> AnchorRecord | None:
    """특허 시간축 prepub_floor_eval.jsonl 한 줄 → AnchorRecord (자체 slice로 격리).

    ⚠️ 라벨 의미론은 **floor(≥등급)**다 — constructed_floor와 동일하게 under-class FNR 카드에만
    맞물린다: floor=S2 문서를 S3(더 공개)로 예측 = 위반(미탐), TS/S1(더 비밀)로 예측 = 위반 아님.
    FNR verdict만 의미 있고 recall은 floor 셀에서 과소평가(참고용). 단일 F1 병합 금지(builder
    eval_contract) — 자체 slice(source)로만 소비한다.

    constructed_floor와 달리 witness(in-text 토큰)를 **요구하지 않는다** — floor 근거가 본문
    토큰이 아니라 **특허법 §64 공개일이라는 비인적 법적 사실**이기 때문이다
    (build_patent_timeaxis_pairs.MAPPING_RULE: 공개 전 = 비공지+법정 비밀유지 → 최소 S2).
    required_tokens는 D 메타모픽 best-effort(특허 초록엔 구체 수치가 드묾).

    prepub 계약(materialize_views): {doc_id, app_no, text, label(=floor grade S2),
    floor_label:true, label_source:'patent_timeaxis_prepub', open_date, ...}.
    """
    grade = str(raw.get("label") or "").strip()
    text = (raw.get("text") or "").strip()
    if grade not in VALID_GRADES or not text:
        return None
    app = str(raw.get("app_no") or "")
    did = str(raw.get("doc_id") or "")
    open_date = str(raw.get("open_date") or "")
    detailed = extract_required_tokens_detailed(raw)
    return AnchorRecord(
        anchor_id="ptx:" + (app or did or _stable_id(text[:120])),
        text=text,
        anchor_grade=grade,
        source=source,
        grade_basis="patent_timeaxis §64 prepub floor" + (f" (open_date={open_date})" if open_date else ""),
        required_tokens=[t for t, _ in detailed],
        token_sources=[s for _, s in detailed],
        origin=origin,
    )


def normalize_gold_real(raw: dict, *, source: str = "holdout_gold", origin: str = "") -> AnchorRecord | None:
    """gold_real 홀드아웃 한 줄 → AnchorRecord. 전 등급 인정, doc_id를 앵커 id로."""
    grade = str(raw.get("label") or raw.get("expected_grade") or "").strip()
    text = (raw.get("text") or "").strip()
    if grade not in VALID_GRADES or not text:
        return None
    did = str(raw.get("doc_id") or "")
    basis = str(raw.get("legal_reference") or raw.get("label_source") or "")
    detailed = extract_required_tokens_detailed(raw)
    return AnchorRecord(
        anchor_id="gold:" + (did or _stable_id(text[:120])),
        text=text,
        anchor_grade=grade,
        source=source,
        grade_basis=basis[:160],
        required_tokens=[t for t, _ in detailed],
        token_sources=[s for _, s in detailed],
        origin=origin,
    )


@dataclass
class AnchorSource:
    """앵커 코퍼스에 싣는 한 파일 — 경로 + 정규화 종류 + C 카드 slice 이름."""

    path: str
    kind: str            # "nkt" | "gold_real"
    source: str          # C 카드 slice 라벨

    _NORMALIZERS = {
        "nkt": normalize_nkt,
        "gold_real": normalize_gold_real,
        "constructed_floor": normalize_constructed_floor,
        "patent_timeaxis_prepub": normalize_patent_timeaxis_prepub,
    }

    def normalizer(self):
        fn = self._NORMALIZERS.get(self.kind)
        if fn is None:
            raise ValueError(f"unknown anchor source kind: {self.kind!r}")
        return fn


def constructed_floor_source(path: str | Path, *, slice_name: str = "constructed_floor") -> AnchorSource:
    """constructed_floor run 디렉터리(또는 admitted.jsonl 직접 경로) → AnchorSource.

    **기본 앵커에 미포함(opt-in)**: run 산출물은 run-스코프·gitignore라 DEFAULT_ANCHOR_SOURCES에
    넣지 않는다. eval_anchor_cards --constructed-floor 로 명시 편입할 때만 자체 slice로 붙는다
    (deploy gate 앵커 경로는 sources 미지정=기본만 쓰므로 자동 유입 없음).
    """
    p = Path(path)
    admitted = p / "admitted.jsonl" if p.is_dir() else p
    return AnchorSource(str(admitted), "constructed_floor", slice_name)


def patent_timeaxis_prepub_source(
    path: str | Path, *, slice_name: str = "patent_timeaxis_prepub"
) -> AnchorSource:
    """특허 시간축 prepub_floor_eval.jsonl(또는 그 run 디렉터리) → AnchorSource.

    **기본 앵커에 미포함(opt-in)**: 시간축 산출물은 datasets/patent_timeaxis/(빌드 산출물)이라
    DEFAULT_ANCHOR_SOURCES에 넣지 않는다 — eval_anchor_cards --patent-timeaxis-prepub 로 명시
    편입할 때만 자체 slice(floor 라벨)로 붙는다. 라벨=S2 floor: FNR verdict만 의미, recall 참고용.
    (published_public.jsonl(S3)은 publicness 인지 경로 전용이라 이 카드에 싣지 않는다.)
    """
    p = Path(path)
    prepub = p / "prepub_floor_eval.jsonl" if p.is_dir() else p
    return AnchorSource(str(prepub), "patent_timeaxis_prepub", slice_name)


# 기본 앵커 소스 — 저장소 로컬 데이터(외부 의존 0). 누출 제거(.clean) 우선.
# NKT는 holdout_eval_clean(누출 제거 1190) 사용 — raw 4800은 train 중복 위험.
# holdout_business.clean(35)은 holdout_eval.clean(42)의 business-domain 부분집합(doc_id 전부 포함)
# 이라 싣지 않는다(dedup이 걸러내지만 중복 소스 명시는 오해 소지).
DEFAULT_ANCHOR_SOURCES: tuple[AnchorSource, ...] = (
    AnchorSource("datasets/patent_proxy/holdout_eval_clean.jsonl", "nkt", "anchor_nkt"),
    AnchorSource("datasets/gold_real/holdout_eval.clean.jsonl", "gold_real", "holdout_gold"),
)


def _iter_jsonl(path: Path) -> Iterable[dict]:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def load_anchor_corpus(
    sources: Sequence[AnchorSource] = DEFAULT_ANCHOR_SOURCES,
    *,
    root: str | Path | None = None,
) -> list[AnchorRecord]:
    """앵커 소스들을 읽어 AnchorRecord 리스트로. 상대경로는 root(기본 poc/) 기준.

    anchor_id 중복(같은 특허/문서가 두 파일에 등장)은 첫 등장만 유지 — 평가 중복 방지.
    """
    base = Path(root) if root else Path(__file__).resolve().parents[4]  # .../poc (src/lloydk/modules/m6_evaluation/ → 4 up)
    out: list[AnchorRecord] = []
    seen: set[str] = set()
    for spec in sources:
        p = Path(spec.path)
        if not p.is_absolute():
            p = base / p
        if not p.exists():
            continue
        norm = spec.normalizer()
        for raw in _iter_jsonl(p):
            rec = norm(raw, source=spec.source, origin=str(spec.path))
            if rec is None or rec.anchor_id in seen:
                continue
            seen.add(rec.anchor_id)
            out.append(rec)
    return out


def corpus_summary(records: Sequence[AnchorRecord]) -> dict:
    """source×grade 분포 요약(카드 산출 전 표본 점검용)."""
    by: dict[str, dict[str, int]] = {}
    for r in records:
        by.setdefault(r.source, {}).setdefault(r.anchor_grade, 0)
        by[r.source][r.anchor_grade] += 1
    return {
        "total": len(records),
        "by_source_grade": {s: dict(sorted(g.items())) for s, g in sorted(by.items())},
        "with_required_tokens": sum(1 for r in records if r.required_tokens),
    }
