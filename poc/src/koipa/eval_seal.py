"""평가셋 봉인 — 봉인 → 1회 개봉 측정 → 재봉인을 **기계가 강제한다.**

왜(2026-09-09). 지금까지 "봉인"은 관행이었다. 파일 이름에 `sealed` 를 넣고 주석에
`⛔ business_sealed 는 봉인이므로 쓰지 않는다`(audit_factor_zero_sample.py:22)라고 적는
방식이다. 사람이 그 주석을 못 보면 그냥 쓰인다. 실제로 봉인 평가셋이 길이만으로 96% 를
맞히는 것을 뒤늦게 발견한 적이 있다(proxy_corpus.py:200).

감리 회신 5(6)이 "평가셋 사전 봉인 → 1회 개봉 측정 → 재측정 시 재봉인" 절차를 약속했다.
절차를 문서로만 두면 같은 일이 반복된다 — 여기서 강제한다.

■ 상태 기계 (경로마다 독립)

    (없음) ──seal──> 봉인됨 ──open──> 개봉됨 ──measure──> 소모됨
                       ↑                                    │
                       └──────────── seal(재봉인) ───────────┘

    봉인됨   측정 거부. "아직 열지 않았다"
    개봉됨   측정 1회 허용
    소모됨   측정 거부. "이미 한 번 쟀다 — 다시 재려면 재봉인하라"

  **개봉을 안 한 셋은 애초에 봉인 대상이 아니다.** 원장에 없는 경로는 자유롭게 쓸 수
  있다 — 봉인은 선언한 것에만 걸린다. 모든 파일을 막으면 아무도 안 쓰고, 안 쓰이는
  게이트는 없는 것과 같다.

■ 내용이 바뀌면 봉인이 깨진다

  봉인 시각의 sha256 을 기록한다. 개봉·측정 시점에 파일이 다르면 거부한다. 봉인해 놓고
  내용을 고치면 "봉인된 셋으로 쟀다"는 말이 거짓이 되기 때문이다.

■ 원장은 append-only 이고 **커밋된다**

  `poc/evidence/eval_seals.jsonl`. 감리 증적이라 datasets/·reports/ 처럼 gitignore 되는
  자리에 두지 않는다. 누가·언제·왜 열었는지가 남아야 "블라인드로 쟀다"를 증명할 수 있다.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

_POC = Path(__file__).resolve().parents[2]
LEDGER = _POC / "evidence" / "eval_seals.jsonl"

SEALED = "sealed"
OPENED = "opened"
CONSUMED = "consumed"
UNSEALED = "unsealed"          # 원장에 없다 = 봉인 대상이 아니다


class SealViolation(RuntimeError):
    """봉인 규약 위반 — 왜 막혔는지와 무엇을 해야 하는지를 문장으로 담는다."""


@dataclass(frozen=True)
class SealState:
    path: str
    state: str
    sha256: str | None
    events: int
    last_at: str | None
    last_owner: str | None


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _key(path: str | Path) -> str:
    """원장 키 — 리포 기준 상대경로. 절대경로로 적으면 기계마다 달라져 대조가 안 된다."""
    p = Path(path).resolve()
    try:
        return p.relative_to(_POC).as_posix()
    except ValueError:
        return p.as_posix()


def _read(ledger: Path | None = None) -> list[dict]:
    p = ledger or LEDGER
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _append(event: dict, ledger: Path | None = None) -> None:
    p = ledger or LEDGER
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def state_of(path: str | Path, *, ledger: Path | None = None) -> SealState:
    """이 경로의 현재 봉인 상태."""
    key = _key(path)
    events = [e for e in _read(ledger) if e.get("path") == key]
    if not events:
        return SealState(key, UNSEALED, None, 0, None, None)
    last = events[-1]
    state = {"seal": SEALED, "open": OPENED, "measure": CONSUMED}.get(
        str(last.get("event")), UNSEALED
    )
    sealed = [e for e in events if e.get("event") == "seal"]
    return SealState(
        key, state,
        sealed[-1].get("sha256") if sealed else None,
        len(events), last.get("at"), last.get("owner"),
    )


def seal(path: str | Path, *, reason: str, owner: str, ledger: Path | None = None) -> SealState:
    """평가셋을 봉인한다. 재봉인도 같은 함수다 — 원장에 계속 쌓인다."""
    if not reason.strip() or not owner.strip():
        raise SealViolation("봉인에는 사유와 담당자가 필요합니다 — 나중에 '왜 열었나'를 답해야 합니다.")
    p = Path(path)
    if not p.is_file():
        raise SealViolation(f"봉인할 파일이 없습니다: {p}")
    _append({
        "event": "seal", "path": _key(p), "sha256": file_sha256(p),
        "bytes": p.stat().st_size, "reason": reason.strip(), "owner": owner.strip(),
        "at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }, ledger)
    return state_of(p, ledger=ledger)


def open_seal(path: str | Path, *, reason: str, owner: str, ledger: Path | None = None) -> SealState:
    """봉인을 연다. **측정은 아직이다** — 여는 것과 재는 것을 갈라 두어야 기록이 남는다."""
    if not reason.strip() or not owner.strip():
        raise SealViolation("개봉에는 사유와 담당자가 필요합니다.")
    st = state_of(path, ledger=ledger)
    if st.state == UNSEALED:
        raise SealViolation(f"봉인된 적이 없는 경로입니다: {st.path}. 먼저 seal 하십시오.")
    if st.state == OPENED:
        raise SealViolation(f"이미 열려 있습니다: {st.path}. 측정하거나 재봉인하십시오.")
    if st.state == CONSUMED:
        raise SealViolation(
            f"이미 한 번 측정에 쓰였습니다: {st.path}. 다시 재려면 **재봉인**하십시오 — "
            "같은 셋을 여러 번 재면 그 수치는 블라인드가 아닙니다."
        )
    _verify_unchanged(path, st)
    _append({
        "event": "open", "path": st.path, "reason": reason.strip(), "owner": owner.strip(),
        "at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }, ledger)
    return state_of(path, ledger=ledger)


def _verify_unchanged(path: str | Path, st: SealState) -> None:
    if not st.sha256:
        return
    now = file_sha256(path)
    if now != st.sha256:
        raise SealViolation(
            f"봉인 이후 내용이 바뀌었습니다: {st.path}\n"
            f"  봉인 당시 {st.sha256[:16]}… / 지금 {now[:16]}…\n"
            "  바뀐 셋으로 잰 값은 '봉인된 셋으로 쟀다'가 아닙니다. 재봉인하십시오."
        )


def require_measurable(
    path: str | Path, *, owner: str = "", note: str = "", ledger: Path | None = None
) -> SealState:
    """이 셋으로 지금 측정해도 되는가 — 되면 **소모 기록을 남기고** 통과시킨다.

    ⚠ 한 번 부르면 소모된다. 측정이 중간에 죽어도 마찬가지다 — 블라인드는 한 번이고,
      "실패했으니 다시"를 허용하면 사실상 여러 번 재는 것이 된다. 다시 재려면 재봉인한다.

    봉인된 적 없는 경로(UNSEALED)는 그냥 통과한다. 봉인은 선언한 것에만 걸린다.
    """
    st = state_of(path, ledger=ledger)
    if st.state == UNSEALED:
        return st
    if st.state == SEALED:
        raise SealViolation(
            f"봉인된 평가셋입니다: {st.path}. 측정 전에 개봉 기록을 남기십시오 "
            "(scripts/eval_seal.py open)."
        )
    if st.state == CONSUMED:
        raise SealViolation(
            f"이미 한 번 측정에 쓰인 평가셋입니다: {st.path}. 재봉인 후 다시 여십시오."
        )
    _verify_unchanged(path, st)
    _append({
        "event": "measure", "path": st.path, "owner": (owner or "-").strip(),
        "note": note.strip(), "at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }, ledger)
    return state_of(path, ledger=ledger)


def all_states(*, ledger: Path | None = None) -> list[SealState]:
    keys = []
    for e in _read(ledger):
        k = e.get("path")
        if k and k not in keys:
            keys.append(k)
    return [state_of(_POC / k, ledger=ledger) for k in keys]
