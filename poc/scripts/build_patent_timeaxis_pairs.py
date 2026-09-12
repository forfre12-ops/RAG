"""특허 시간축 자연실험 페어 빌더 — 사람검수 0 S2-floor/S3 정답원 (2026-07-03 설계).

원리 (라벨 권위 = 사람 아닌 법정 사실)
--------------------------------------
특허출원은 출원 후 1년 6개월이 지나면 법으로 공개된다(특허법 §64). 그 전까지 동일
텍스트는 (a) 비공지 상태이고 (b) 특허청의 법정 비밀유지 하에 있으며(§226 계열 —
공개 전 출원 내용 누설은 처벌 대상) (c) 출원 행위 자체가 경제적 가치의 방증이다.
즉 **같은 텍스트가 공개일이라는 결정적·비인적 사실 하나로 등급이 반전**된다:

  - 공개 전 시점 라벨: S2 **floor** (보수 매핑 — 출원=공개 전제라 영업비밀 3요소
    완전충족이 아니므로 S1 주장 금지. '최소 S2'만 기계적으로 성립)
  - 공개 후 라벨: S3 확정 (공개성은 출처 사실 — publicness 인지 경로 전용)

이것이 external_authority S1/S2=0 갭(사람서명 없이 중간등급 실텍스트 정답 부재)을
메우는 유일한 '실텍스트 + 비인적 법적 사실' 소스다 (2026-07-03 5기둥 적대검증 생존안).

⚠️ 소비 계약 (오독 금지)
------------------------
1. prepub_floor_eval.jsonl (S2 floor): **텍스트 분류기** 평가용 — FNR 방향 카드만
   (S2보다 낮게 = 위반). exact-grade 정답 아님(floor_label=true). 자체 셀로 격리
   (eval_anchor_cards 원칙) — 다른 소스와 단일 F1로 뭉개지 마라.
2. published_public.jsonl (S3): 텍스트만으로 S3를 요구하지 **않는다**(동일 텍스트가
   공개 전엔 ≥S2였다!). source/publicness 메타데이터를 아는 경로(비공지성 source-prior
   게이트, eval_real_public_fpr류 과분류 FPR 측정) 전용. requires_publicness_metadata=true.
3. 학습 편입 금지 기본 — 평가 앵커. (편입하려면 pairs 단위로 train/eval 분리 필수.)
4. 명세서 문체 ≠ 고객사 내부문서 텍스처 — 실문서 성능 보증 아님(capstone F1 0.26 갭
   동일 적용). '실텍스트-프록시 분포'의 floor 회귀 신호로만.

매핑 규칙 (정책, 1회 승인 대상)
-------------------------------
mapping_rule v1: 공개 전 = S2 floor / 공개 후 = S3. 근거는 위 원리 절.
이 규칙의 권위는 법원/특허청이 아니라 '법정 사실 + 명문 규칙'이다 — 규칙 작성은
루브릭 제정과 동종의 정책 행위(문서 검수 아님).

위생 가드 (결정적)
------------------
- app_no가 기존 앵커/프록시(patent_proxy/raw.jsonl, holdout_eval_clean.jsonl)에
  있으면 배제 (근사중복으로 기존 셀 오염 방지).
- 정규화 텍스트가 학습셋(train_subset.jsonl 등 --train-guard)과 일치하면 배제.
- 공개일(openDate)이 없거나 --published-within-days 밖이면 배제 — '최근 공개'만이
  "당시 진짜 비공지였던 실텍스트" 주장을 지탱한다(오래된 공개특허는 유통 이력이 길어
  반사실 시점 주장이 약해짐).

사용
----
  # 1) 키 갱신 후 (plus.kipris.or.kr → API 신청 → poc/.env KIPRIS_SERVICE_KEY)
  python scripts/build_patent_timeaxis_pairs.py --probe          # 응답 필드 확인
  python scripts/build_patent_timeaxis_pairs.py --per-keyword 30 # 수집+페어 빌드
  # 2) 오프라인/테스트: 사전 수집 jsonl에서 빌드
  python scripts/build_patent_timeaxis_pairs.py --from-jsonl raw_items.jsonl --as-of 20260703
출력: datasets/patent_timeaxis/{pairs.jsonl, prepub_floor_eval.jsonl,
      published_public.jsonl, manifest.json}
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from kipris_collect import KiprisApiError, _call, _check_response, _service_key  # noqa: E402

MAPPING_RULE = {
    "version": "v1",
    "pre_publication": {"label": "S2", "kind": "floor",
                        "why": "비공지+§226 비밀유지+출원=경제가치 → 최소 S2. 출원=공개 전제라 S1 금지(보수)."},
    "post_publication": {"label": "S3", "kind": "definitive_by_publicness",
                         "why": "특허법 §64 법정 공개 — 공개성은 출처 사실(텍스트 속성 아님)."},
    "authority": "법정 사실(공개일) + 본 명문 규칙 — 사람 검수 아님, 규칙 제정은 루브릭과 동종 정책 행위",
    "approved": "2026-07-03 사용자 승인(정책 1회 승인 — 문서 검수 아님)",
}

# 시간축 수확용 키워드 — 등급 유도가 아니라 '기술 문서'를 모으는 필터일 뿐이다.
# (kipris_collect의 TS/S1 키워드→등급 부여와 달리, 여기서는 등급이 공개일 사실에서만 나온다.)
HARVEST_KEYWORDS = [
    "제조 방법", "제어 방법", "조성물", "공정 장치", "신호 처리",
    "배터리", "반도체", "촉매", "암호", "센서",
]


def norm_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def parse_kipris_date(raw: str) -> dt.date | None:
    """KIPRIS 날짜('YYYY.MM.DD'/'YYYYMMDD'/'YYYY-MM-DD') → date. 실패 시 None."""
    s = re.sub(r"[^0-9]", "", str(raw or ""))
    if len(s) != 8:
        return None
    try:
        return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def _parse_items_with_dates(xml_bytes: bytes) -> list[dict]:
    """getWordSearch 응답에서 title/abstract/app_no + 날짜 필드들을 추출."""
    import xml.etree.ElementTree as ET  # noqa: PLC0415

    _check_response(xml_bytes)
    out: list[dict] = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return out
    for item in root.iter("item"):
        def g(*tags: str) -> str:
            for t in tags:
                el = item.find(t)
                if el is not None and el.text and el.text.strip():
                    return el.text.strip()
            return ""
        title = g("inventionTitle", "inventionName")
        abstract = g("astrtCont")
        app_no = g("applicationNumber", "applicationNo")
        open_date = g("openDate", "laidOpenDate", "publicationDate", "pubDate")
        app_date = g("applicationDate", "appDate")
        if abstract and len(abstract) >= 120:
            out.append({
                "title": title, "abstract": abstract, "app_no": app_no,
                "open_date": open_date, "application_date": app_date,
            })
    return out


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def build_pairs(
    raw_items: list[dict],
    *,
    as_of: dt.date,
    published_within_days: int,
    known_app_nos: set[str],
    train_norm_texts: set[str],
) -> dict:
    """원시 아이템 → 시간축 페어 + 위생 리포트(결정적, 네트워크 불요)."""
    pairs: list[dict] = []
    dropped = Counter()
    seen_app: set[str] = set()
    for it in raw_items:
        app_no = str(it.get("app_no") or "").strip()
        text = f"{it.get('title', '')}\n\n{it.get('abstract', '')}".strip()
        if not app_no:
            dropped["no_app_no"] += 1
            continue
        if app_no in seen_app:
            dropped["dup_app_no"] += 1
            continue
        seen_app.add(app_no)
        open_d = parse_kipris_date(it.get("open_date", ""))
        if open_d is None:
            dropped["no_open_date"] += 1
            continue
        age = (as_of - open_d).days
        if age < 0:
            dropped["open_date_in_future"] += 1
            continue
        if age > published_within_days:
            dropped["published_too_long_ago"] += 1
            continue
        if app_no in known_app_nos:
            dropped["app_no_in_existing_anchor_or_proxy"] += 1
            continue
        if norm_text(text) in train_norm_texts:
            dropped["train_text_overlap"] += 1
            continue
        pairs.append({
            "doc_id": _sha1(f"{app_no}:{text}"),
            "app_no": app_no,
            "title": it.get("title", ""),
            "text": text,
            "open_date": open_d.isoformat(),
            "application_date": it.get("application_date", ""),
            "label_pre_publication": MAPPING_RULE["pre_publication"]["label"],
            "pre_publication_label_kind": MAPPING_RULE["pre_publication"]["kind"],
            "label_post_publication": MAPPING_RULE["post_publication"]["label"],
            "label_source": "patent_timeaxis",
            "mapping_rule_version": MAPPING_RULE["version"],
            "truth_basis": "특허법 §64 법정 공개일 — 공개 전 동일 텍스트는 비공지+법정 비밀유지 상태(비인적 사실)",
            "time_context": f"counterfactual: 공개일({open_d.isoformat()}) 전 시점 기준 라벨",
        })
    return {"pairs": pairs, "dropped": dict(dropped)}


def materialize_views(pairs: list[dict]) -> tuple[list[dict], list[dict]]:
    """소비자별 뷰 — 같은 텍스트에 모순 라벨 2행이 평평하게 돌아다니지 않도록
    용도를 필드로 강제한 분리 파일을 만든다."""
    prepub = [{
        "doc_id": p["doc_id"], "app_no": p["app_no"], "text": p["text"],
        "label": p["label_pre_publication"],
        "floor_label": True,  # exact 아님 — FNR 방향(이보다 낮게=위반) 카드만
        "label_source": "patent_timeaxis_prepub",
        "pair_ref": p["doc_id"],
        "eval_contract": "text-only FNR card, own cell — 단일 F1 병합 금지",
        "open_date": p["open_date"],
    } for p in pairs]
    published = [{
        "doc_id": p["doc_id"], "app_no": p["app_no"], "text": p["text"],
        "label": p["label_post_publication"],
        "label_source": "patent_timeaxis_published",
        "requires_publicness_metadata": True,  # 텍스트 단독 분류기에 S3를 요구하지 않는다
        "pair_ref": p["doc_id"],
        "eval_contract": "publicness-aware paths only (source-prior gate / over-class FPR)",
        "open_date": p["open_date"],
    } for p in pairs]
    return prepub, published


def _known_app_nos(paths: list[Path]) -> set[str]:
    out: set[str] = set()
    for p in paths:
        for r in load_jsonl(p):
            a = str(r.get("app_no") or "").strip()
            if a:
                out.add(a)
    return out


def harvest_live(per_keyword: int, key: str) -> list[dict]:
    import time  # noqa: PLC0415

    items: list[dict] = []
    for word in HARVEST_KEYWORDS:
        try:
            items.extend(_parse_items_with_dates(_call(word, key, per_keyword, 1)))
        except (KiprisApiError, OSError) as e:
            print(f"[timeaxis] '{word}' 호출 실패: {repr(e)[:120]}", file=sys.stderr)
        time.sleep(0.5)
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-jsonl", help="사전 수집 원시 아이템(jsonl: title/abstract/app_no/open_date) — 오프라인 빌드")
    ap.add_argument("--per-keyword", type=int, default=30)
    ap.add_argument("--published-within-days", type=int, default=180,
                    help="공개일이 이 기간 안인 것만 — '당시 진짜 비공지' 주장의 신선도")
    ap.add_argument("--as-of", help="YYYYMMDD (기본: 오늘) — 결정적 재현용")
    ap.add_argument("--out-dir", default="datasets/patent_timeaxis")
    ap.add_argument("--train-guard", nargs="*",
                    default=["datasets/gold_real/train_subset.jsonl"],
                    help="정규화 텍스트 일치 시 배제할 학습셋들")
    ap.add_argument("--anchor-guard", nargs="*",
                    default=["datasets/patent_proxy/raw.jsonl",
                             "datasets/patent_proxy/holdout_eval_clean.jsonl"],
                    help="app_no 일치 시 배제할 기존 앵커/프록시들")
    ap.add_argument("--probe", action="store_true", help="API 키+응답 날짜필드 확인만")
    args = ap.parse_args()

    as_of = (
        dt.date(int(args.as_of[:4]), int(args.as_of[4:6]), int(args.as_of[6:8]))
        if args.as_of else dt.date.today()
    )

    if args.probe:
        key = _service_key()
        if not key:
            print("[timeaxis] KIPRIS_SERVICE_KEY 미설정 — poc/.env에 추가.", file=sys.stderr)
            return 2
        try:
            items = _parse_items_with_dates(_call("반도체", key, 3, 1))
        except KiprisApiError as e:
            print(f"[timeaxis] PROBE 실패: {e} — 키 만료 시 plus.kipris.or.kr에서 갱신.", file=sys.stderr)
            return 1
        print(f"[timeaxis] PROBE OK — {len(items)}건. 날짜 필드 샘플:")
        for it in items[:3]:
            print(f"  app_no={it['app_no']} open_date={it['open_date']!r} app_date={it['application_date']!r}")
        if items and not any(it["open_date"] for it in items):
            print("  ⚠️ open_date 비어 있음 — getWordSearch 응답에 공개일 필드가 없으면 "
                  "서지상세(getBibliographyDetailInfoSearch) 보강 필요.", file=sys.stderr)
        return 0

    if args.from_jsonl:
        raw_items = load_jsonl(Path(args.from_jsonl))
    else:
        key = _service_key()
        if not key:
            print("[timeaxis] KIPRIS_SERVICE_KEY 미설정 — --from-jsonl 오프라인 모드를 쓰거나 키를 설정.", file=sys.stderr)
            return 2
        raw_items = harvest_live(args.per_keyword, key)
        if not raw_items:
            print("[timeaxis] 수집 0건 — 키 상태(--probe) 확인.", file=sys.stderr)
            return 1

    known = _known_app_nos([Path(p) for p in args.anchor_guard])
    train_texts: set[str] = set()
    for p in args.train_guard:
        for r in load_jsonl(Path(p)):
            if r.get("text"):
                train_texts.add(norm_text(r["text"]))

    result = build_pairs(
        raw_items, as_of=as_of, published_within_days=args.published_within_days,
        known_app_nos=known, train_norm_texts=train_texts,
    )
    pairs = result["pairs"]
    prepub, published = materialize_views(pairs)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def _write(name: str, rows: list[dict]) -> None:
        (out_dir / name).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""),
            encoding="utf-8",
        )

    _write("pairs.jsonl", pairs)
    _write("prepub_floor_eval.jsonl", prepub)
    _write("published_public.jsonl", published)

    manifest = {
        "built_as_of": as_of.isoformat(),
        "published_within_days": args.published_within_days,
        "mapping_rule": MAPPING_RULE,
        "pairs": len(pairs),
        "dropped": result["dropped"],
        "hygiene": {
            "anchor_guard_app_nos": len(known),
            "train_guard_texts": len(train_texts),
            "self_check_app_no_disjoint_from_guards": all(p["app_no"] not in known for p in pairs),
            "self_check_train_overlap_zero": all(norm_text(p["text"]) not in train_texts for p in pairs),
        },
        "consumption_contract": [
            "prepub_floor_eval = 텍스트 분류기 FNR 카드 전용(자체 셀, floor — exact 아님)",
            "published_public = publicness 인지 경로 전용(requires_publicness_metadata)",
            "학습 편입 금지 기본 — 편입 시 pair 단위 train/eval 분리 필수",
            "실문서 성능 보증 아님(명세서 문체 프록시) — floor 회귀 신호로만 인용",
        ],
        "truth_warning": "not_locked_gold_eval — 사람서명 아님. 라벨 권위=특허법 §64 공개일(비인적 사실)+명문 규칙 v1.",
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[timeaxis] pairs={len(pairs)} dropped={result['dropped']} → {out_dir}")
    assert manifest["hygiene"]["self_check_app_no_disjoint_from_guards"]
    assert manifest["hygiene"]["self_check_train_overlap_zero"]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
