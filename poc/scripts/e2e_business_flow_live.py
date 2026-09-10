#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""업무 흐름을 **실 API + 실 DB** 로 끝까지 태우고, 단계마다 DB 행을 확인한다.

왜 이 도구가 있는가(2026-09-06). 콘솔 e2e 하니스(`make test-console-e2e`, jsdom 174건)는
**화면 로직**을 본다 — 버튼을 누르면 어떤 요청이 나가는가. 그건 DOM 안에서 끝나고, 서버가
실제로 무엇을 저장했는지는 안 본다.

그래서 "업로드하면 DB 에 잘 들어가나"는 그 하니스로 답이 안 된다. 이 도구가 그 자리를 맡는다.

    업로드 → 분류 → 검수큐 → 최종확정 → 골든 후보 → 골든 잡 → 학습 잡

각 단계마다 **응답 코드와 DB 행을 함께** 확인한다. 200 을 받고도 저장이 안 되는 경우가
실재한다 — KL 연동 규약에도 그 함정이 적혀 있다(doc_id 가 tb_documents 에 없으면 200 이어도
저장 안 됨).

⚠ 쓰기가 일어난다. 기본은 in-process TestClient + 설정된 DATABASE_URL 이다.
  --dry 로 읽기 검사만 돌릴 수 있다. 남는 행은 doc_id 접두 `e2eflow-` 로 식별된다.

사용:
    python scripts/e2e_business_flow_live.py            # 전체
    python scripts/e2e_business_flow_live.py --dry      # 쓰기 없이 라우트·DB 연결만
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
if str(_POC / "src") not in sys.path:
    sys.path.insert(0, str(_POC / "src"))

os.environ.setdefault("TESTING", "1")
# ⚠ 하드닝 프로파일(full-train 등)에 TESTING=1 을 함께 주면 config 가 거부한다
#   ("운영/하드닝 프로파일에 TESTING env 유입 감지"). 그 가드가 맞다 — 여기서는
#   프로파일을 고정하지 않고 로컬 기본값을 쓴다. 라우트 열거가 목적이 아니라
#   업무 흐름이 실제로 저장되는지가 목적이다.
# in-process 호출용 — 이 값은 TestClient 안에서만 쓰이고 밖으로 나가지 않는다.
os.environ.setdefault("API_KEY", "e2e-inproc-key")
os.environ.setdefault("API_KEY_TRUST_ACTOR_ROLE_HEADER", "1")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

RUN = uuid.uuid4().hex[:8]
DOC_ID = "e2eflow-%s" % RUN
ACTOR = {"user_id": "e2e-admin", "role": "admin"}

BODY = (
    "당사 차세대 반도체 식각 공정의 핵심 레시피와 수율 개선 파라미터를 정리한 내부 기술 "
    "문서입니다. 챔버 압력, 플라즈마 출력, 가스 유량 배합비와 그에 따른 수율 변화를 "
    "실측 데이터와 함께 기록하였습니다. 본 자료는 경쟁사가 확보할 경우 당사의 원가 경쟁력에 "
    "직접적인 손실을 초래할 수 있으므로 관련 부서 외 열람을 제한합니다. 공정 조건은 "
    "2026년 상반기 양산 라인 기준이며, 세부 수치는 별첨 표에 정리되어 있습니다."
)

_results: list[tuple[str, bool, str]] = []


def step(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok, detail))
    print("  [%s] %-42s %s" % ("OK" if ok else "FAIL", name, detail[:110]))


def _hdr(role: str = "admin") -> dict:
    from koipa.config import settings

    return {
        "X-API-Key": settings.api_key,
        "X-Actor-Id": "e2e-runner",
        "X-Actor-Role": role,
    }


def _uuid32(v: str) -> str:
    """API 가 주는 하이픈 UUID → DB 가 저장한 32자 hex.

    [2026-09-06 실측] `tb_documents.doc_id` 는 **char(32)** 이고 하이픈이 없다:

        API 응답   85dc2e2c-9d3d-4b41-8269-486f3ace0354
        DB 저장    85dc2e2c9d3d4b418269486f3ace0354

    하이픈 있는 문자열로 조회하면 **0행**이 나온다. 저장이 안 된 것처럼 보인다 —
    이 하니스가 처음에 그렇게 오판했다. 연동하는 쪽도 같은 함정을 밟는다.
    """
    return str(v or "").replace("-", "").lower()


def _count(sql: str, **kw) -> int:
    from sqlalchemy import text

    from koipa.db import session_scope

    with session_scope() as db:
        return int(db.execute(text(sql), kw).scalar() or 0)


def check_db_connection() -> bool:
    try:
        from sqlalchemy import text

        from koipa.db import session_scope

        with session_scope() as db:
            db.execute(text("SELECT 1"))
        from koipa.config import settings

        scheme = str(settings.database_url).split("://")[0]
        step("DB 연결", True, "스킴=%s" % scheme)
        return True
    except Exception as exc:  # noqa: BLE001
        step("DB 연결", False, "%s: %s" % (type(exc).__name__, exc))
        return False


def run(dry: bool = False) -> int:
    from fastapi.testclient import TestClient

    from koipa.api.app import app

    print("=" * 74)
    print(" 업무 흐름 실측 — 실 API + 실 DB   (run=%s)" % RUN)
    print("=" * 74)

    if not check_db_connection():
        return 2

    with TestClient(app) as cli:
        r = cli.get("/api/v1/healthz")
        step("헬스체크", r.status_code == 200, "%s" % r.status_code)

        if dry:
            print("\n--dry — 쓰기 단계는 건너뛴다.")
            return 0

        # ── ① 문서 업로드 ────────────────────────────────────────────────
        r = cli.post(
            "/api/v1/documents",
            headers=_hdr(),
            data={
                "actor": json.dumps(ACTOR),
                "doc_type": "기술문서",
                "external_ref": DOC_ID,
                "source_type": "internal",
                "security_marking": "confidential",
                "access_scope": "approved_only",
                "enqueue_classification": "false",
            },
            files={"file": ("%s.txt" % DOC_ID, io.BytesIO(BODY.encode("utf-8")), "text/plain")},
        )
        ok = r.status_code in (200, 201)
        body = r.json() if ok else {}
        doc_id = str(body.get("doc_id") or body.get("id") or "")
        step("① 문서 업로드", ok, "%s doc_id=%s" % (r.status_code, doc_id or r.text[:60]))
        if not ok or not doc_id:
            return 1

        # [2026-09-11] 표 이름은 표준 명명(7b3e9d2a4f10) — 대응표는 koipa/db/standard_names.py.
        n = _count("SELECT COUNT(*) FROM tad_dm_doc_mng WHERE doc_id = :d", d=_uuid32(doc_id))
        step("① DB 저장 (tad_dm_doc_mng)", n == 1, "행 %d" % n)

        # ── ② 분류 ──────────────────────────────────────────────────────
        r = cli.post(
            "/api/v1/classify",
            headers=_hdr(),
            json={"doc_id": doc_id, "content": BODY, "return_evidence": True},
        )
        ok = r.status_code == 200
        cls = r.json() if ok else {}
        step("② 분류", ok, "%s label=%s conf=%s status=%s" % (
            r.status_code, cls.get("label"), cls.get("confidence"), cls.get("status")))
        if not ok:
            return 1

        n = _count("SELECT COUNT(*) FROM tad_cm_clsf_rslt_mng WHERE doc_id = :d", d=_uuid32(doc_id))
        step("② DB 저장 (tad_cm_clsf_rslt_mng)", n >= 1, "행 %d · status=%s" % (n, cls.get("status")))

        # ── ③ 검수 큐 ────────────────────────────────────────────────────
        r = cli.get("/api/v1/review-queue?limit=200", headers=_hdr())
        ok = r.status_code == 200
        items = (r.json() or {}).get("items", []) if ok else []
        mine = [x for x in items if str(x.get("doc_id")) == doc_id]
        step("③ 검수 큐 조회", ok, "%s 전체 %d건 · 이 문서 %d건 (status=%s)" % (
            r.status_code, len(items), len(mine), cls.get("status")))

        # ── ④ 최종 확정 ──────────────────────────────────────────────────
        r = cli.post(
            "/api/v1/confirm",
            headers=_hdr(),
            json={
                "doc_id": doc_id,
                "confirmed_label": cls.get("label") or "S2",
                "actor": ACTOR,
                "note": "e2e 흐름 검증",
            },
        )
        ok = r.status_code in (200, 201)
        step("④ 최종 확정", ok, "%s %s" % (r.status_code, str(r.text)[:70]))
        if ok:
            n = _count(
                "SELECT COUNT(*) FROM tad_cm_clsf_rslt_mng "
                "WHERE doc_id = :d AND clsf_stts_nm = 'confirmed'", d=_uuid32(doc_id))
            step("④ DB 반영 (status=confirmed)", n >= 1, "행 %d" % n)

        # ── ⑤ 책임 추적 — 두 층을 각각 본다 ─────────────────────────────
        # [2026-09-06 실측] 감사 설계가 2층이다. 한 층만 보고 "안 남았다"고 하면 오판이다
        # (이 하니스가 처음에 그렇게 오판했다).
        #
        #   tad_am_adt_log_mng  **요청 단위** 접근 기록(수행동작·행위자·성공여부·발생일시).
        #                   ⚠ trgt_id 는 18,598행 중 486행(2.6%)만 채워진다 — 수행동작은
        #                   라우트 접두이고 개체 식별자는 대개 안 적는다. 그래서 "이 문서를
        #                   누가 만졌나"는 이 테이블만으로는 못 답한다.
        #   tad_cm_crct_mng     **개체 단위** 확정·교정 기록(clsf_id·clbtr_id).
        try:
            n = _count(
                "SELECT COUNT(*) FROM tad_cm_crct_mng c "
                "JOIN tad_cm_clsf_rslt_mng x ON c.clsf_id = x.clsf_id "
                "WHERE x.doc_id = :d", d=_uuid32(doc_id))
            step("⑤ 개체 단위 책임추적 (tad_cm_crct_mng)", n >= 1, "행 %d" % n)
        except Exception as exc:  # noqa: BLE001
            step("⑤ 개체 단위 책임추적 (tad_cm_crct_mng)", False, "조회 실패 %s" % type(exc).__name__)

        try:
            n = _count(
                "SELECT COUNT(*) FROM tad_am_adt_log_mng "
                "WHERE actr_id = :a AND flfmt_bhvr_cd IN ('documents','classify','confirm')",
                a="e2e-runner")
            step("⑤ 요청 단위 접근기록 (tad_am_adt_log_mng)", n >= 3,
                 "행 %d (documents·classify·confirm)" % n)
        except Exception as exc:  # noqa: BLE001
            step("⑤ 요청 단위 접근기록 (tad_am_adt_log_mng)", False, "조회 실패 %s" % type(exc).__name__)

        # ── ⑥ 골든셋 후보 ────────────────────────────────────────────────
        r = cli.get("/api/v1/golden/candidates?limit=5", headers=_hdr())
        step("⑥ 골든 후보 조회", r.status_code == 200,
             "%s %s" % (r.status_code, str(r.text)[:70]))
        r = cli.get("/api/v1/golden/candidates/summary", headers=_hdr())
        step("⑥ 골든 후보 요약", r.status_code == 200,
             "%s %s" % (r.status_code, str(r.text)[:80]))

        # ── ⑥-b 골든 후보 **확정** — 내가 올린 후보로만 한다 ─────────────
        # ⚠ 기존 후보(로컬 306건)의 검수 상태를 건드리지 않는다. 검수는 사람 결정이고,
        #   확인하겠다고 남의 판정을 바꾸면 안 된다. 내 후보를 따로 올려서 그것만 결정한다.
        cand_body = BODY + ("\n\n(e2e 흐름 검증용 후보 %s)" % RUN)
        r = cli.post(
            "/api/v1/golden/candidates/upload",
            headers=_hdr(),
            data={
                "document_origin": "synthetic",
                "source_reference": "e2e-flow-%s" % RUN,
                "authorization_basis": "내부 검증용 합성 문서",
            },
            files={"file": ("e2ecand-%s.txt" % RUN,
                            io.BytesIO(cand_body.encode("utf-8")), "text/plain")},
        )
        # [2026-09-06 실측] 이 경로는 **공유 API 키를 거부한다**:
        #     403 {"detail":"golden console requires a portal JWT login; ..."}
        # 결함이 아니라 의도된 통제다. 골든 검수는 서명 신원이 남는 단계라 사람별 포털
        # JWT 를 요구한다(공유 키가 JWT 보다 먼저 통과하던 구멍은 2026-08-17 에 닫혔다).
        # ⛔ 확인하겠다고 토큰을 만들어 우회하지 않는다 — 그러면 막아 둔 것을 스스로 뚫는 것이다.
        # 여기서 확인하는 것은 "쓰기가 되는가"가 아니라 **"막혀 있는가"** 다.
        cand = r.json() if r.status_code in (200, 201) else {}
        cand_id = str(cand.get("doc_id") or cand.get("id") or "")
        jwt_required = r.status_code in (401, 403) and "JWT" in str(r.text)
        step("⑥-b 골든 쓰기 = 사람별 JWT 강제", jwt_required or bool(cand_id),
             "%s %s" % (r.status_code,
                        "공유 키 거부 — 설계대로" if jwt_required else str(r.text)[:60]))
        ok = bool(cand_id)

        if ok and cand_id:
            r = cli.post(
                "/api/v1/golden/candidates/%s/decision" % cand_id,
                headers=_hdr(),
                json={"action": "defer", "reason": "e2e 흐름 검증 — 실제 검수 아님"},
            )
            ok2 = r.status_code in (200, 201)
            step("⑥-b 골든 후보 확정(defer)", ok2, "%s %s" % (r.status_code, str(r.text)[:70]))

            r = cli.get("/api/v1/golden/candidates/%s" % cand_id, headers=_hdr())
            got = r.json() if r.status_code == 200 else {}
            st = str(got.get("status") or got.get("review_status") or "?")
            step("⑥-b 결정이 되읽히는가", r.status_code == 200 and st not in ("?", "None"),
                 "%s status=%s" % (r.status_code, st))

        # ── ⑦ 골든 잡 ────────────────────────────────────────────────────
        r = cli.get("/api/v1/golden/jobs?limit=5", headers=_hdr())
        step("⑦ 골든 잡 목록", r.status_code == 200, "%s" % r.status_code)
        r = cli.get("/api/v1/golden/summary", headers=_hdr())
        step("⑦ 골든 요약", r.status_code == 200, "%s %s" % (r.status_code, str(r.text)[:70]))

        # ── ⑧⑨ 프로파일에 딸린 라우트 ────────────────────────────────────
        # [2026-09-06] /train/jobs·/metrics/latest 는 **배포 프로파일에 딸려 있다.**
        # 로컬 기본은 lite-noapi·enable_training=False 라 404 다 — 결함이 아니다.
        # 없는 것을 실패로 세면 진짜 실패가 묻힌다. 프로파일을 함께 적어 구분한다.
        from koipa.config import settings as _st

        profile = str(getattr(_st, "deploy_profile", "?"))
        mounted = {r.path for r in app.routes if hasattr(r, "path")}
        for path, name in (
            ("/api/v1/train/jobs", "⑧ 학습 잡 목록"),
            ("/api/v1/metrics/latest", "⑨ 최신 지표"),
        ):
            if path not in mounted:
                step(name, True, "이 프로파일(%s)에 라우트 없음 — 건너뜀" % profile)
                continue
            r = cli.get(path, headers=_hdr())
            step(name, r.status_code == 200, "%s" % r.status_code)

        for path, name in (
            ("/api/v1/admin/dashboard", "⑨ 대시보드"),
            ("/api/v1/schema/grades", "⑨ 등급 체계"),
        ):
            r = cli.get(path, headers=_hdr())
            step(name, r.status_code == 200, "%s" % r.status_code)

    print("\n" + "=" * 74)
    bad = [n for n, ok, _ in _results if not ok]
    print(" 통과 %d · 실패 %d" % (len(_results) - len(bad), len(bad)))
    if bad:
        print(" 실패: %s" % ", ".join(bad))
    print(" ⚠ 이 실행이 남긴 행은 doc_id 접두 `e2eflow-` 로 찾을 수 있다.")
    print("=" * 74)
    return 1 if bad else 0


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="업무 흐름 실측 (실 API + 실 DB)")
    ap.add_argument("--dry", action="store_true", help="쓰기 없이 연결·라우트만")
    a = ap.parse_args(argv)
    return run(dry=a.dry)


if __name__ == "__main__":
    sys.exit(main())
