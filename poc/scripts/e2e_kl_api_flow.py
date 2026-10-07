#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""KL 연동 e2e — 안내서의 호출 순서(IF-01·02·03·05·06)를 배포된 서버에 그대로 호출해 본다.

■ 무엇을 보는가
  안내서(doc/result/KL_API_연동안내_*/KL_API_연동_안내서.html)가 KL 에 약속한 동작을 **실제 서버**가
  그대로 하는지. 규약서↔코드 필드 대조(test_kl_openapi_schema_matches_code)는 스키마만 보고,
  이 도구는 호출 순서·상태 코드·오류 본문·저장 동작까지 본다.

    IF-01  GET  /healthz                 인증 없음
    IF-02  POST /documents               등록(원본 파일) — 같은 파일 재등록은 같은 doc_id
    IF-03  POST /classify/async          doc_id 만 보낸다 → 202
    IF-05  GET  /classify/jobs/{job_id}  done 이면 results[0] 에 결과
    IF-06  GET  /classify/{doc_id}       저장값으로 만든 최근 결과(확정 등급은 confirmed_* 로 따로)
    + 오류: 키 없음/틀림 401 · 빈 파일 422 · 미지원 형식 201(char_count 0) · doc_id 형식 오류 422 ·
      분류 이력 없음 404 · 미등록 doc_id 는 오류가 아니라 TS+needs_review(fail-secure)

■ 사용
    BASE_URL=http://127.0.0.1:8000 API_KEY=<키> python scripts/e2e_kl_api_flow.py
    python scripts/e2e_kl_api_flow.py --base-url http://호스트:8000 --api-key <키> --json out.json
    --callback-url http://수신처/cb   IF-03 에 callback_url 을 함께 보낸다(전달 확인은 수신처 로그로 한다)
    --confirm                         검수 확정(POST /confirm)까지 태워 IF-06 의 confirmed_* 를 본다

■ 주의: 서버에 문서·분류 이력·감사 기록이 **남는다**(운영 서버에는 돌리지 말 것). 표본 본문은 이 파일 안의
  가상 문서이며 실제 업무 문서가 아니다. 종료 코드 0 = 전부 통과, 1 = 하나라도 실패.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid

import requests

# 한글 콘솔(cp949)에서 이모지·특수문자 때문에 출력이 죽지 않게 한다.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

GRADES = {"TS", "S1", "S2", "S3"}

# 가상 표본 — 등급을 말하는 낱말은 넣지 않는다(본문만으로 판정되는지 보는 것이 아니라 흐름을 보는 도구다).
SAMPLE_TEXT = (
    "[내부 검토] 신규 열처리로 온도 균일도 평가 결과 공유\n\n"
    "생산기술팀은 신규 열처리로의 온도 균일도 평가를 마쳤다. 노 내 21개 지점 실측에서 균일도는 ±3.1도로 "
    "기존 로의 ±5.8도보다 좋아졌고, 이 값을 끌어낸 것은 배치 위치별 히터 출력 보정 계수 세트다. "
    "계수는 4개월 동안 3교대 상황에서 실측해 얻은 것이며 평가 한 건에 든 계측·시편·분석 비용은 2억 4,590만 원, "
    "투입 시간은 233인시다. 측정 기록 원본은 계측 PC 에만 있고 재평가는 다음 달 첫 주에 잡혀 있다.\n"
).encode("utf-8")


class Run:
    def __init__(self) -> None:
        self.results: list[dict] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append({"name": name, "ok": bool(ok), "detail": detail})
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
        return bool(ok)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r["ok"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=os.environ.get("BASE_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--api-key", default=os.environ.get("API_KEY", ""))
    ap.add_argument("--callback-url", default="", help="IF-03 에 함께 보낼 callback_url(선택)")
    ap.add_argument("--confirm", action="store_true", help="검수 확정까지 태워 IF-06 의 confirmed_* 를 본다")
    ap.add_argument("--confirm-token", default=os.environ.get("CONFIRM_TOKEN", ""),
                    help="검수 확정(POST /confirm)에 쓸 JWT(admin·reviewer·kl_backend). 공유 API 키(system)는 확정 권한이 "
                         "없어 403 이다 — 확정은 우리 콘솔에서 사람이 하고 KL 은 IF-06 으로 읽기만 한다")
    ap.add_argument("--poll-timeout", type=int, default=180, help="비동기 결과 대기 최대 초")
    ap.add_argument("--json", default="", help="결과를 JSON 파일로도 남긴다")
    args = ap.parse_args()

    if not args.api_key:
        print("API_KEY 가 없다 — --api-key 또는 환경변수 API_KEY 로 준다.", file=sys.stderr)
        return 2

    base = args.base_url.rstrip("/") + "/api/v1"
    hdr = {"X-API-Key": args.api_key}
    run = Run()
    actor = json.dumps({"user_id": "kl-e2e", "role": "kl_backend"})
    tag = uuid.uuid4().hex[:8]

    def upload(name: str, data: bytes, *, extra: dict | None = None, key: str | None = None):
        # key=None → 정상 키, key="" → 헤더 없음, 그 밖 → 그 값을 키로 보낸다(인증 시험용)
        use = args.api_key if key is None else key
        h = {"X-API-Key": use} if use else {}
        form = {"actor": actor, "source_type": "internal", "access_scope": "department"}
        form.update(extra or {})
        return requests.post(f"{base}/documents", headers=h, data=form,
                             files={"file": (name, data)}, timeout=120)

    # ── IF-01 ───────────────────────────────────────────────
    print("\n[IF-01] GET /healthz")
    r = requests.get(f"{base}/healthz", timeout=30)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    run.check("healthz 200 · 인증 없이 열림", r.status_code == 200, f"status={r.status_code}")
    run.check("healthz status=ok", body.get("status") == "ok", f"status={body.get('status')}")
    run.check("모델 적재됨(checks.model=loaded)", (body.get("checks") or {}).get("model") == "loaded",
              f"checks.model={(body.get('checks') or {}).get('model')}")
    build = (body.get("build") or {}).get("git_sha", "?")
    print(f"        build.git_sha={build} · deploy_profile={body.get('deploy_profile')} · model_version={body.get('model_version')}")

    # ── 인증 ────────────────────────────────────────────────
    print("\n[인증] X-API-Key")
    r = upload("auth_probe.txt", SAMPLE_TEXT, key="")
    run.check("키 없이 POST /documents → 401", r.status_code == 401, f"status={r.status_code} body={r.text[:80]}")
    r = upload("auth_probe.txt", SAMPLE_TEXT, key="wrong-key-" + tag)
    run.check("틀린 키로 POST /documents → 401", r.status_code == 401, f"status={r.status_code}")

    # ── IF-02 ───────────────────────────────────────────────
    print("\n[IF-02] POST /documents")
    body_text = SAMPLE_TEXT + f"\n(e2e 표식 {tag})\n".encode("utf-8")
    r = upload(f"e2e_{tag}.txt", body_text)
    run.check("등록 201", r.status_code == 201, f"status={r.status_code} body={r.text[:120]}")
    d = r.json() if r.status_code == 201 else {}
    doc_id = d.get("doc_id", "")
    try:
        uuid.UUID(doc_id)
        uuid_ok = True
    except Exception:  # noqa: BLE001
        uuid_ok = False
    run.check("doc_id 가 UUID", uuid_ok, doc_id)
    run.check("본문이 추출됨(char_count>0)", (d.get("char_count") or 0) > 0, f"char_count={d.get('char_count')}")
    run.check("저장됨(persisted=true)", d.get("persisted") is True, f"persisted={d.get('persisted')}")
    for gone in ("ocr_used", "extraction_complete", "pages_processed"):
        if gone in d:
            run.check(f"삭제된 응답 필드가 없다: {gone}", False, "응답에 아직 있다 — 안내서와 어긋남")
    r2 = upload(f"e2e_{tag}_again.txt", body_text)
    d2 = r2.json() if r2.status_code == 201 else {}
    run.check("같은 내용 재등록 → 같은 doc_id(SHA-256 중복 제거)", d2.get("doc_id") == doc_id,
              f"first={doc_id[:8]} second={str(d2.get('doc_id'))[:8]}")
    r = upload("empty.txt", b"")
    run.check("빈 파일 → 422", r.status_code == 422, f"status={r.status_code}")
    r = upload(f"binary_{tag}.exe", b"MZ\x90\x00\x03\x00\x00\x00" + os.urandom(64))
    dd = r.json() if r.status_code == 201 else {}
    run.check("미지원 형식(.exe)도 201 로 등록, char_count 0", r.status_code == 201 and (dd.get("char_count") == 0),
              f"status={r.status_code} char_count={dd.get('char_count')} warnings={dd.get('warnings')}")

    # ── IF-03 / IF-05 ───────────────────────────────────────
    print("\n[IF-03] POST /classify/async  →  [IF-05] GET /classify/jobs/{job_id}")
    payload = {"doc_id": doc_id}
    if args.callback_url:
        payload["callback_url"] = args.callback_url
    r = requests.post(f"{base}/classify/async", headers=hdr, json=payload, timeout=60)
    run.check("큐 등록 202", r.status_code == 202, f"status={r.status_code} body={r.text[:120]}")
    j = r.json() if r.status_code == 202 else {}
    job_id = j.get("job_id", "")
    run.check("job_id·status_url 이 온다", bool(job_id) and bool(j.get("status_url")), f"job_id={job_id[:8]}")

    def wait_job(jid: str) -> dict:
        t0 = time.time()
        last: dict = {}
        while time.time() - t0 < args.poll_timeout:
            rr = requests.get(f"{base}/classify/jobs/{jid}", headers=hdr, timeout=30)
            if rr.status_code != 200:
                return {"_http": rr.status_code}
            last = rr.json()
            if last.get("status") in ("done", "failed"):
                last["_elapsed_s"] = round(time.time() - t0, 1)
                return last
            time.sleep(1.5)
        last["_timeout"] = True
        return last

    job = wait_job(job_id) if job_id else {}
    run.check("작업이 done 으로 끝남", job.get("status") == "done",
              f"status={job.get('status')} elapsed={job.get('_elapsed_s')}s error={job.get('error')}")
    res = (job.get("results") or [{}])[0]
    label = res.get("label")
    run.check("결과 등급이 TS/S1/S2/S3", label in GRADES, f"label={label} confidence={res.get('confidence')}")
    run.check("모델 버전이 온다", bool(res.get("model_version")), f"model_version={res.get('model_version')}")
    run.check("신뢰도가 0~1", isinstance(res.get("confidence"), (int, float)) and 0 <= res["confidence"] <= 1,
              f"confidence={res.get('confidence')}")
    r = requests.get(f"{base}/classify/jobs/{uuid.uuid4()}", headers=hdr, timeout=30)
    run.check("없는 job_id → 404", r.status_code == 404, f"status={r.status_code}")

    # ── IF-06 ───────────────────────────────────────────────
    print("\n[IF-06] GET /classify/{doc_id}")
    r = requests.get(f"{base}/classify/{doc_id}", headers=hdr, timeout=30)
    s = r.json() if r.status_code == 200 else {}
    run.check("최근 결과 200", r.status_code == 200, f"status={r.status_code} body={r.text[:100]}")
    run.check("IF-05 와 같은 등급", s.get("label") == label, f"if06={s.get('label')} if05={label}")
    run.check("확정 전에는 confirmed_label 이 null", s.get("confirmed_label") in (None, ""),
              f"confirmed_label={s.get('confirmed_label')}")
    r = requests.get(f"{base}/classify/not-a-uuid", headers=hdr, timeout=30)
    run.check("doc_id 가 UUID 가 아니면 422", r.status_code == 422, f"status={r.status_code}")
    r = requests.get(f"{base}/classify/{uuid.uuid4()}", headers=hdr, timeout=30)
    run.check("분류 이력이 없는 UUID → 404", r.status_code == 404, f"status={r.status_code}")

    # ── 등록과 동시에 분류(enqueue_classification) ────────────
    print("\n[IF-02+03] enqueue_classification=true")
    r = upload(f"e2e_{tag}_enq.txt", SAMPLE_TEXT + f"\n(e2e enqueue {tag})\n".encode("utf-8"),
               extra={"enqueue_classification": "true"})
    de = r.json() if r.status_code == 201 else {}
    run.check("등록 201 + classification_job_id", r.status_code == 201 and bool(de.get("classification_job_id")),
              f"status={r.status_code} job={str(de.get('classification_job_id'))[:8]}")
    if de.get("classification_job_id"):
        je = wait_job(de["classification_job_id"])
        run.check("동시 요청한 분류도 done", je.get("status") == "done", f"status={je.get('status')}")

    # ── fail-secure: 등록 안 한 doc_id ───────────────────────
    print("\n[fail-secure] 미등록 doc_id 로 분류 요청")
    ghost = str(uuid.uuid4())
    r = requests.post(f"{base}/classify/async", headers=hdr, json={"doc_id": ghost}, timeout=60)
    jg = wait_job(r.json().get("job_id", "")) if r.status_code == 202 else {}
    rg = (jg.get("results") or [{}])[0]
    run.check("오류가 아니라 202→done", r.status_code == 202 and jg.get("status") == "done",
              f"status={r.status_code} job={jg.get('status')}")
    run.check("결과는 TS + 검수 필요(미탐 방향으로 막는다)", rg.get("label") == "TS" and rg.get("status") == "needs_review",
              f"label={rg.get('label')} status={rg.get('status')}")

    # ── 검수 확정 → IF-06 confirmed_* (선택) ──────────────────
    if args.confirm and not args.confirm_token:
        print("\n[확정] --confirm-token 이 없어 건너뜀 — 공유 API 키는 확정 권한이 없다(403). "
              "확정은 콘솔에서 사람이 하고 KL 은 IF-06 으로 읽기만 한다.")
        rc = requests.post(f"{base}/confirm", headers=hdr, timeout=30, json={
            "doc_id": doc_id, "confirmed_label": label or "S2",
            "actor": {"user_id": "kl-e2e", "role": "kl_backend"}})
        run.check("공유 API 키로는 확정할 수 없다(403 — 권한 분리 유지)", rc.status_code == 403,
                  f"status={rc.status_code}")
    elif args.confirm:
        print("\n[확정] POST /confirm(JWT) → IF-06 confirmed_*")
        rc = requests.post(f"{base}/confirm", headers={"Authorization": f"Bearer {args.confirm_token}"},
                           timeout=30, json={
            "doc_id": doc_id, "confirmed_label": label or "S2",
            "actor": {"user_id": "kl-e2e-reviewer", "role": "reviewer"}, "note": "e2e"})
        run.check("확정 200", rc.status_code == 200, f"status={rc.status_code} body={rc.text[:120]}")
        s2 = requests.get(f"{base}/classify/{doc_id}", headers=hdr, timeout=30).json()
        run.check("IF-06 에 confirmed_label 이 채워진다", s2.get("confirmed_label") == (label or "S2"),
                  f"confirmed_label={s2.get('confirmed_label')} by={s2.get('confirmed_by')}")
        run.check("label(예측 등급)은 그대로", s2.get("label") == label, f"label={s2.get('label')}")

    total = len(run.results)
    print(f"\n== KL 연동 e2e: {total - run.failed}/{total} 통과 · 실패 {run.failed} · 대상 {base} (build {build}) ==")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"base": base, "build": build, "total": total, "failed": run.failed,
                       "results": run.results}, f, ensure_ascii=False, indent=2)
    return 1 if run.failed else 0


if __name__ == "__main__":
    sys.exit(main())
