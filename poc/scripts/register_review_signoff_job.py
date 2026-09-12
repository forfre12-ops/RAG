"""검수 전달본을 **서명 경로**에 등록한다 — 배포 후 반드시 한 번 돌린다.

왜 필요한가. 검수 경로가 둘인데 서로 다른 것을 만든다.

    콘솔 후보 검수  /golden/candidates/{id}/decision -> approved_proxy
                   (proxy_gold_candidate_service 주석 원문:
                    "it never creates a locked evaluation record")
    골든 서명      /golden/jobs/{job_id}/signoff    -> locked_gold_eval

릴리스 게이트(`eval_readiness`)가 세는 것은 뒤쪽뿐이다. **배포된 콘솔에서 120건을 전부
검수해도 locked_gold_eval 은 0 건 그대로다.** 서명 화면은 "골든 빌드 잡" 에 붙은 후보를
보여 주므로, 전달본 파일을 잡으로 등록해 두지 않으면 검수자에게 서명할 대상이 없다.

⚠ **반드시 HTTP API 로 등록한다.** GoldenBuildService 를 직접 부르면 그 잡은 이 스크립트
   프로세스의 JobStore 에 들어가고, API 서버는 자기 프로세스의 store 를 보므로 **영영 못
   본다**(JobStore 가 in-memory 폴백일 때). 실측 2026-08-15 에 그렇게 만들었다가 잡았다.

⚠ JobStore 가 in-memory 면 **API 재시작에 잡이 사라진다.** 그때는 이 스크립트를 다시
   돌리면 된다 — 같은 파일을 가리키는 잡이 이미 있으면 그것을 재사용한다(멱등).

⚠ 이 스크립트는 **서명하지 않는다.** 서명은 사람 단계이고 머신 reviewer 는 서비스가
   거부한다(rejected_reasons={'machine_reviewer': N}).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

DEFAULT_POOL = "datasets/golden_review/ff5a822c/candidates.jsonl"


def _pool_count(path: str) -> int:
    """로컬 사본이 있으면 건수를 센다 — 멱등 판정에 쓴다."""
    p = Path(path)
    if not p.exists():
        return 0
    return sum(1 for l in p.read_text("utf-8").splitlines() if l.strip())


def _headers(api_key: str, token: str) -> dict:
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    elif api_key:
        h["X-API-Key"] = api_key
    return h


def _abs(api: str, url: str) -> str:
    """서버가 주는 review_url/signoff_url 은 절대경로(/api/v1/...)다 — 호스트를 붙인다."""
    if url.startswith(("http://", "https://")):
        return url
    root = api[: -len("/api/v1")] if api.endswith("/api/v1") else api.rstrip("/")
    return root + url


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="검수 전달본을 서명 잡으로 등록(HTTP)")
    ap.add_argument("--pool", default=DEFAULT_POOL, help="전달본 jsonl (서버의 datasets/ 하위 경로)")
    ap.add_argument("--base-url", required=True, help="예: http://223.130.156.134:8000")
    ap.add_argument("--actor", required=True, help="등록자 실계정 id (감사 신원)")
    ap.add_argument("--api-key", default="", help="X-API-Key")
    ap.add_argument("--token", default="", help="Bearer 토큰(있으면 우선)")
    ap.add_argument("--min-per-grade", type=int, default=5,
                    help="게이트가 요구하는 등급별 최소 서명 수(참고 출력)")
    ap.add_argument("--local-pool-check", default="",
                    help="로컬에도 같은 파일이 있으면 등급 균형을 미리 본다")
    args = ap.parse_args(argv)

    import httpx

    base = args.base_url.rstrip("/")
    api = f"{base}/api/v1"
    hdr = _headers(args.api_key, args.token)

    # 0) 로컬 사본이 있으면 등급 균형을 먼저 본다(서버 파일은 못 읽으므로 참고용).
    lp = Path(args.local_pool_check or args.pool)
    if lp.exists():
        rows = [json.loads(l) for l in lp.read_text("utf-8").splitlines() if l.strip()]
        by = Counter(r.get("label") for r in rows)
        print(f"[pool·로컬참고] {lp} — {len(rows)}건 · 등급 {dict(by)}")
        low = [g for g in ("TS", "S1", "S2", "S3") if by.get(g, 0) < args.min_per_grade]
        if low:
            print(f"[warn] 등급별 {args.min_per_grade}건 미달: {low} — readiness 가 안 열린다")
    else:
        print(f"[pool] 로컬 사본 없음({lp}) — 서버 경로만 사용한다")

    with httpx.Client(timeout=30.0) as cli:
        # 1) 이미 같은 파일을 가리키는 잡이 있는가 — 멱등
        job_id = None
        try:
            r = cli.get(f"{api}/golden/jobs", params={"limit": 100}, headers=hdr)
            if r.status_code == 200:
                # 잡 목록에 gold_path 가 없다(실측 2026-08-15) — 건수와 kind 로 고른다.
                # 같은 전달본이면 gold_count 가 같고 kind 가 golden_register 다.
                want = _pool_count(args.local_pool_check or args.pool)
                for j in (r.json().get("jobs") or r.json().get("items") or []):
                    if j.get("kind") != "golden_register" or j.get("status") != "done":
                        continue
                    if want and int(j.get("gold_count") or 0) == want:
                        job_id = j.get("job_id")
                        print(f"[reuse] 같은 건수({want})의 등록 잡이 있다: {job_id}")
                        break
            elif r.status_code in (401, 403):
                print(f"[error] 인증 실패({r.status_code}) — --api-key 또는 --token 이 필요하다")
                return 2
        except Exception as exc:  # noqa: BLE001 — 목록 조회 실패는 새로 만들면 된다
            print(f"[info] 기존 잡 조회 건너뜀: {type(exc).__name__}")

        # 2) 없으면 등록
        if not job_id:
            body = {"build_path": args.pool, "actor": {"user_id": args.actor, "role": "admin"}}
            r = cli.post(f"{api}/golden/jobs/register", json=body, headers=hdr)
            if r.status_code == 404:
                print("[error] 404 — 서버에 그 경로가 없거나 datasets/ 밖이다(샌드박스 거부)")
                print(f"        서버에 {args.pool} 이 있는지 먼저 확인할 것")
                return 2
            if r.status_code >= 400:
                print(f"[error] 등록 실패 {r.status_code}: {r.text[:300]}")
                return 2
            # 응답 필드는 golden_job_id 다(job_id 아님 — 실측 2026-08-15).
            body_out = r.json()
            job_id = body_out.get("golden_job_id") or body_out.get("job_id")
            if not job_id:
                print(f"[error] 응답에 job_id 가 없다: {json.dumps(body_out, ensure_ascii=False)[:200]}")
                return 2
            print(f"[registered] job_id = {job_id}")

        # 3) 등록 결과가 실제로 조회되는가 — 여기까지 확인해야 검수자가 열 수 있다
        r = cli.get(f"{api}/golden/jobs/{job_id}", headers=hdr)
        if r.status_code != 200:
            print(f"[error] 등록은 됐는데 조회가 안 된다({r.status_code}) — 검수 화면이 안 열린다")
            return 2
        info = r.json()
        print(f"[verify] status={info.get('status')} · "
              f"gold_count={info.get('gold_count')} · error={info.get('error')}")
        # 서명 URL 은 **서버가 준 것**을 쓴다. 직접 조립하면 안 된다 —
        # GOLDEN_HTML_URL_SECRET 이 설정된 서버에서는 ?t= HMAC 토큰이 없는 링크가 403 이다
        # (golden.py:762). 223 은 실제로 설정돼 있어서(실측 64자), 종전 출력은 검수자에게
        # **열리지 않는 링크**를 주고 있었다. 응답의 signoff_url 은 golden.py:187 이 채운다.
        signoff_url = info.get("signoff_url") or f"/api/v1/golden/jobs/{job_id}/signoff.html"
        review_url = info.get("review_url") or f"/api/v1/golden/jobs/{job_id}/review.html"
        signed = "?t=" in signoff_url
        if not signed:
            print("[verify] ⚠ 서명 URL 에 ?t= 가 없다 — 서버에 GOLDEN_HTML_URL_SECRET 이 "
                  "설정돼 있으면 검수자가 403 을 본다")

        # 화면이 실제로 열리는지까지 본다 — 여기까지 200 이어야 검수자가 쓸 수 있다.
        r2 = cli.get(_abs(api, signoff_url), headers=hdr)
        print(f"[verify] signoff.html -> {r2.status_code}"
              + ("" if r2.status_code == 200 else "  ⚠ 검수자가 못 연다"))

    print("\n검수자에게 줄 것")
    print(f"  서명 화면   {_abs(api, signoff_url)}")
    print(f"  검토 화면   {_abs(api, review_url)}")
    print(f"  서명 API    POST {api}/golden/jobs/{job_id}/signoff")
    if signed:
        print("  ⚠ 주소의 ?t= 까지 통째로 전달할 것 — 잘라내면 403 이다(job 단위 서명 토큰)")
    print("\n반드시 확인할 것")
    print("  1. 검수 계정이 **실계정** 이어야 한다. ai_assist · system · demo-console 은 거부된다")
    print("     (거부되면 응답에 rejected_reasons={'machine_reviewer': N} 이 뜬다)")
    print("  2. 서명 요청에 **publish=true** 를 넣어야 게이트가 움직인다")
    print("     기본값 false 는 미리보기라 정본·라이브 readiness 를 안 건드린다")
    print(f"  3. 등급별 {args.min_per_grade}건을 채워야 readiness ready=True 가 된다")
    print("\n⚠ API 재시작 뒤 서명 화면이 404 면 이 스크립트를 다시 돌린다(멱등).")
    print("⚠ 이 스크립트는 서명하지 않는다. 서명은 사람 단계다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
