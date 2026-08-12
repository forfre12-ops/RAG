"""KL 검수용 골든셋 후보 풀 재구성.

기존 777건 패키지의 문제:
  · TS 55 / S1 56 이 172~573자 시나리오 카드 — 판정 근거가 본문에 없다
  · 판정 이력(rule/llm/rationale) 전부 None
  · review_status 가 전건 accepted (검수 요청인데 이미 승인 표시)
  · S3 74% 편중

이 스크립트가 하는 것:
  · 어제 저작한 1,000건에서 검수 스캐폴딩(등급 제안 사유·검수 지시)을 제거해 업무문서 본체만 남김
  · 자기 등급 문자열이 본문에 남은 문서는 제외 (정답 노출)
  · 시나리오 계열당 1건만 선별 (길이 클러스터·중복 제거)
  · gold_real 의 실문서 고등급(S1)·S2·S3 와 조합해 등급 균형을 맞춤
  · review_status=pending 으로 발행
"""
import json
import re
import sys
from pathlib import Path
from collections import Counter, defaultdict

ROOT = Path(r"f:\antigravity\rag\poc")
CAND = ROOT / "datasets/proxy_gold/single_document_candidates"
GOLD = ROOT / "datasets/gold_real/classification_gold.jsonl"
OUT = ROOT / "datasets/golden_review/_pool_20260809.jsonl"

_N = int(sys.argv[1]) if len(sys.argv) > 1 else 30
PER_GRADE = {"TS": _N, "S1": _N, "S2": _N, "S3": _N}

DROP_H2 = ("확인 질문과 답변 기록", "세부 검토 경과", "후속 조치와 종료 조건")
DROP_H3 = ("검수 전 확인 목록",)


def strip_scaffolding(md: str) -> str:
    out, skip = [], False
    for ln in md.splitlines():
        m2 = re.match(r"^##\s+(?!#)(.*)$", ln)
        if m2:
            t = m2.group(1).strip()
            skip = t.startswith("등급 제안 사유") or any(t.startswith(d) for d in DROP_H2)
            if skip:
                continue
        m3 = re.match(r"^###\s+(.*)$", ln)
        if m3 and any(m3.group(1).strip().startswith(d) for d in DROP_H3):
            skip = True
            continue
        if not skip:
            out.append(ln)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


GRADE_TOK = re.compile(r"\b(TS|S1|S2|S3)\b")


def drop_grade_sentences(text: str) -> str:
    """등급 문자열이 든 문장만 제거한다 — 문서를 통째로 버리지 않는다.

    본문에 'S3가 적절하다' 같은 문장이 남으면 검수자가 본문을 읽기 전에 정답을 본다.
    문장 단위로 떼어내면 나머지 사실 서술은 그대로 판단 재료로 남는다.
    """
    kept_paras = []
    for para in text.split("\n"):
        if not para.strip():
            kept_paras.append(para)
            continue
        if para.lstrip().startswith("#"):
            kept_paras.append(GRADE_TOK.sub("", para).rstrip())
            continue
        parts = re.split(r"(?<=다\.)\s+|(?<=[.!?])\s+", para)
        keep = [s for s in parts if s.strip() and not GRADE_TOK.search(s)]
        if keep:
            kept_paras.append(" ".join(keep))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept_paras)).strip()


def stratified(items, want, keyfn):
    """길이 분포 전 구간에서 고르게 뽑는다 — 최장만 뽑으면 길이가 등급 단서가 된다."""
    items = sorted(items, key=keyfn)
    if len(items) <= want:
        return items
    step = len(items) / want
    return [items[min(int(i * step), len(items) - 1)] for i in range(want)]


SCEN = re.compile(r"\b([A-Z]\d)-(\d{2})-(\d{2})\b")


def scenario_family(text: str, fallback: str) -> str:
    m = SCEN.search(text)
    return f"{m.group(1)}-{m.group(2)}" if m else fallback


# ── 1) 합성 후보 정제 ────────────────────────────────────────────────────────
pool = defaultdict(list)
stats = Counter()
for md in sorted(CAND.glob("GOLD-*.md")):
    parts = md.stem.split("-")
    if len(parts) < 3 or parts[2] not in PER_GRADE:
        stats["대상외"] += 1
        continue
    grade = parts[2]
    raw = strip_scaffolding(md.read_text(encoding="utf-8", errors="ignore"))
    text = drop_grade_sentences(raw)
    stats["정제"] += 1
    if GRADE_TOK.search(text):
        stats["등급문자열잔존_제외"] += 1
        continue
    if len(text) < 900:
        stats["너무짧아_제외"] += 1
        continue
    pool[grade].append({
        "doc_id": md.stem,
        "text": text,
        "label": grade,
        "family": scenario_family(text, md.stem),
        "meta": json.loads((md.parent / f"{md.stem}.metadata.json").read_text(encoding="utf-8"))
        if (md.parent / f"{md.stem}.metadata.json").is_file() else {},
    })

print("합성 후보 정제:", dict(stats))
for g in PER_GRADE:
    fams = len({d["family"] for d in pool[g]})
    print(f"  {g}: 정제통과 {len(pool[g]):>3}건 · 시나리오계열 {fams}개")

# 시나리오 계열당 1건 (가장 긴 것)
picked_syn = {}
for g in PER_GRADE:
    seen = {}
    for d in sorted(pool[g], key=lambda x: -len(x["text"])):
        if d["family"] not in seen:
            seen[d["family"]] = d
    picked_syn[g] = list(seen.values())
    print(f"  {g}: 계열 중복 제거 후 {len(picked_syn[g])}건")

# ── 2) gold_real 실문서 ─────────────────────────────────────────────────────
sys.path.insert(0, str(ROOT / "src"))
from koipa.hygiene import text_hash  # noqa: E402

# 배포본 v-fe4b386b 의 실학습 코퍼스에 들어간 문서는 검수해도 평가 정답으로 쓸 수 없다
# (train-on-test). 발행 전에 빼서 검수자 시간을 낭비하지 않는다.
TRAIN_HASHES = set()
for f in ("train.jsonl", "val.jsonl", "test.jsonl"):
    p = ROOT / "datasets/labeled_p1_v5_clean" / f
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                t = json.loads(line).get("text")
                if t:
                    TRAIN_HASHES.add(text_hash(t))

real = [json.loads(l) for l in GOLD.read_text(encoding="utf-8").splitlines() if l.strip()]
REAL_SOURCES = {"판례", "판례(3000+)", "판례(2000+)", "판례_공개문서", "금융보고서"}
real_by = defaultdict(list)
dropped_train = 0
for r in real:
    g = r.get("label")
    if g in PER_GRADE and str(r.get("source")) in REAL_SOURCES and len(r.get("text") or "") >= 900:
        if text_hash(r.get("text") or "") in TRAIN_HASHES:
            dropped_train += 1
            continue
        real_by[g].append(r)
print(f"\n학습셋 포함으로 제외한 실문서 = {dropped_train}건")
print("\ngold_real 실문서(900자 이상):", {g: len(v) for g, v in sorted(real_by.items())})

# ── 3) 조합 ────────────────────────────────────────────────────────────────
# ── 등급별 길이 분포 정합 ───────────────────────────────────────────────────
# 등급마다 "같은 길이 목표점"에 가장 가까운 문서를 뽑는다. 최장순·층화만으로는 등급별
# 가용 길이대가 달라(S2 실문서는 1,060~1,398자뿐) 길이가 등급 단서로 남는다.
_all_len = sorted(
    [len(r.get("text") or "") for v in real_by.values() for r in v]
    + [len(d["text"]) for v in picked_syn.values() for d in v]
)


def _q(p: float) -> int:
    return _all_len[min(int(p * (len(_all_len) - 1)), len(_all_len) - 1)]


TARGETS = [_q(i / (max(PER_GRADE.values()) - 1)) for i in range(max(PER_GRADE.values()))]

rows = []
mix = {}
for g, want in PER_GRADE.items():
    avail = (
        [("실문서", r, len(r.get("text") or "")) for r in real_by.get(g, [])]
        + [("합성정제", d, len(d["text"])) for d in picked_syn[g]]
    )
    # 길이 목표에 맞추되, 허용 오차(±25%) 안에서는 실문서를 우선한다 —
    # 같은 길이라면 합성보다 실문서가 검수 가치가 높다.
    chosen, used = [], set()
    for t in TARGETS[:want]:
        tol = max(200, int(t * 0.25))
        band = [i for i, (k, _o, ln) in enumerate(avail)
                if i not in used and abs(ln - t) <= tol and k == "실문서"]
        if band:
            best = min(band, key=lambda i: abs(avail[i][2] - t))
        else:
            rest = [i for i in range(len(avail)) if i not in used]
            best = min(rest, key=lambda i: abs(avail[i][2] - t)) if rest else None
        if best is not None:
            used.add(best)
            chosen.append(avail[best])
    take_real = [o for k, o, _ in chosen if k == "실문서"]
    take_syn = [o for k, o, _ in chosen if k == "합성정제"]
    mix[g] = {"실문서": len(take_real), "합성정제": len(take_syn)}
    for r in take_real:
        rows.append({
            "doc_id": str(r.get("doc_id")),
            "text": r.get("text") or "",
            "label": g,
            "label_source": str(r.get("label_source") or "unknown"),
            "source": str(r.get("source") or ""),
            "domain": str(r.get("domain") or ""),
            "original_file": r.get("original_file"),
            "rule_grade": r.get("rule_grade"),
            "llm_grade": r.get("llm_grade"),
            "llm_confidence": r.get("llm_confidence"),
            "llm_rationale": r.get("llm_rationale"),
            "agreement": r.get("agreement"),
            "review_status": "pending",
            "reviewer_id": None,
        })
    for d in take_syn:
        m = d.get("meta") or {}
        rows.append({
            "doc_id": d["doc_id"],
            "text": d["text"],
            "label": g,
            "label_source": "proxy_gold_authored",
            "source": "proxy_gold_authored",
            "domain": str(m.get("industry") or m.get("domain") or "합성저작"),
            "review_status": "pending",
            "reviewer_id": None,
            "authoring_note": "합성 저작 후보 — 검수 스캐폴딩 제거본. 외부 권위 근거 없음.",
        })

# ── 누출 게이트 ────────────────────────────────────────────────────────────
# 사람이 읽고 등급을 정할 후보다. 본문에 답이 적혀 있거나 길이가 등급을 알려주면
# 검수가 검증이 아니라 확인 절차가 되고, 그 라벨로 잰 정확도는 부풀려진다.
# 발행 **전에** 막는다 — 내보낸 뒤 알면 검수자 시간이 이미 버려진 뒤다.
from koipa.dataset_leakage import check_or_raise  # noqa: E402

leak = check_or_raise(
    [(r["label"], r["text"]) for r in rows],
    label=f"KL 검수 후보 {len(rows)}건",
    allow_grade_token=False,
)
print("\n=== 누출 검사 통과 ===")
print(f"  길이-only 1NN {leak['length_only_1nn']} (무작위 {leak['length_only_random']})"
      f" · 등급 전용 문장 {leak['tell_coverage']:.1%} · 본문 등급 노출 {leak['grade_token_exposed']}건")

OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w", encoding="utf-8") as fh:
    for r in rows:
        fh.write(json.dumps(r, ensure_ascii=False) + "\n")

print("\n=== 최종 후보 풀 ===")
print(f"  총 {len(rows)}건 → {OUT}")
for g in PER_GRADE:
    v = [len(r["text"]) for r in rows if r["label"] == g]
    v.sort()
    print(f"  {g:<3} {len(v):>3}건  구성={mix[g]}  길이 {v[0]}~{v[-1]} (중앙 {v[len(v)//2]})")

# 누출 재검사
pairs = sorted((len(r["text"]), r["label"]) for r in rows)
ok = 0
for i, (ln, lab) in enumerate(pairs):
    b, bd = None, None
    for j in (i - 1, i + 1):
        if 0 <= j < len(pairs):
            dd = abs(pairs[j][0] - ln)
            if bd is None or dd < bd:
                bd, b = dd, pairs[j][1]
    ok += b == lab
print(f"\n  길이-only 1NN = {ok/len(pairs):.3f} (무작위 0.25)")
selfleak = sum(1 for r in rows if re.search(r"\b" + r["label"] + r"\b", r["text"]))
print(f"  본문에 자기 등급 문자열 = {selfleak}/{len(rows)}")
