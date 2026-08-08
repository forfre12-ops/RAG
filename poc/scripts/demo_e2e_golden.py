"""시나리오 B — 골든셋 → 검수·서명 → 재학습 → 배포 → 메트릭 E2E 드라이버 (터미널판).

관리자 콘솔 [모델] 탭의 루프 B 를 순서대로 한 번에 돌린다. 각 단계마다 대응하는
콘솔 카드를 함께 찍어, 터미널 시연과 화면 시연이 같은 대본이 되게 한다.

  G1 골든셋 후보 생성   POST /golden/build            ← [모델] G1 골든셋 후보 빌더
  G2 검수 · 서명        POST /golden/jobs/{id}/signoff ← [모델] 검수/서명 HTML
  3  재학습             POST /train                    ← [모델] 3 재학습 트리거
  4  배포               POST /admin/model/activate     ← [모델] 4 모델 서빙 · 배포
  5  메트릭             GET  /metrics/latest           ← [모델] 5 모델 메트릭

루프 A(문서 업로드 → 분류 → 검수)는 scripts/demo_e2e_8010.py 가 담당한다.

사용:
  cd poc
  .venv/Scripts/python.exe scripts/demo_e2e_golden.py               # G1·G2 까지(기본)
  .venv/Scripts/python.exe scripts/demo_e2e_golden.py --train       # 재학습까지
  .venv/Scripts/python.exe scripts/demo_e2e_golden.py --train --activate   # 배포·메트릭까지

플래그:
  --register   G1 을 LLM 재라벨링 대신 '기존 슬레이트 등록'(POST /golden/jobs/register)으로
               수행한다. LLM 이 없는 환경(고객사 CPU 런타임·CI)에서 유일하게 도는 경로다.
  --slate P    --register 가 등록할 파일 경로(기본 DEFAULT_SLATE). datasets/ 하위만 허용.
  --train      G2 이후 재학습(incremental)을 실행하고 완료까지 폴링한다.
  --activate   재학습 산출 모델을 배포한다(deploy gate 적용). --train 과 함께 쓴다.
  --publish    서명 결과를 라이브 locked_gold_eval 경로에 병합한다. 기본은 미병합
               (run-스코프 미리보기)이라 정본·라이브 평가경로를 건드리지 않는다.
  --reviewer ID  G2 서명을 이 스크립트가 대신 수행한다. **실계정 ID 가 필요하다.**

G2 서명에 관하여 (2026-08-08 실서버 실측으로 정정):
  기본은 서명하지 않고 검수·서명 HTML 주소만 출력하고 멈춘다. 서명은 사람이 하는 단계이고,
  서버가 reviewer_id 를 실계정으로 강제하기 때문이다(golden_tiers.is_human_reviewer).
  종전에는 이 스크립트가 actor 'demo-console' 로 자동 서명했는데, 그 값은 시연 마커라
  403 으로 거부된다 — 그리고 그게 옳다. 통과시키려고 가드를 피하는 이름을 고르면
  시연 산출물이 '사람 서명 평가정답(locked_gold_eval)'으로 집계되고, 배포 게이트가
  검증되지 않은 모델을 검증된 것으로 오판한다.
  리허설에서 서명까지 자동으로 밟아야 하면 --reviewer 에 실계정을 주되, 그 서명은
  실제로 그 사람 이름으로 기록된다는 점을 알고 쓸 것.

provider 주의 (2026-08-08 실측):
  llm_provider=noop 으로는 gold 후보가 절대 나오지 않는다. noop 은 등급 라벨러가 아니라
  합성 문서 생성기(title/body 반환)라, 골든 빌드의 라벨 파싱이 S3/0.5 로 떨어지고 룰과
  합의가 되지 않아 전건이 needs_review 로 간다(자체 docstring: label_match_rate ~25%).
  실측 결과 8건 중 gold 0 · uncertain 8. 따라서
    - LLM 이 있는 지재원 GPU 서버  → --train 없이 기본 경로(vllm_qwen 등)
    - LLM 이 없는 환경·시연 리허설 → --register
  두 경로 모두 G2 이후(서명·재학습·배포)는 동일하다.

서명 주체:
  평가 정답의 서명 주체는 지재원 관리자(실계정)다. 서버가 이를 강제하므로 이 스크립트는
  기본적으로 서명하지 않고 검수·서명 HTML 주소만 내고 멈춘다 — 위 "G2 서명에 관하여" 참조.

환경변수(미설정 시 로컬 개발 기본값):
  DEMO_BASE_URL   기본 http://localhost:8010
  DEMO_API_KEY    기본 lloydk_dev_apikey
  DEMO_ACTOR      기본 demo-console   (서명·학습 요청의 actor.user_id)

전제: API 가동 + API_KEY_ROLE=admin (train·model/activate 는 admin 역할 필요).
"""
import io
import json
import os
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import httpx

BASE = os.getenv("DEMO_BASE_URL", "http://localhost:8010").rstrip("/")
API = f"{BASE}/api/v1"
HEADERS = {"X-API-Key": os.getenv("DEMO_API_KEY", "lloydk_dev_apikey")}
ACTOR = {"user_id": os.getenv("DEMO_ACTOR", "demo-console"), "role": "admin"}

# --register 기본 슬레이트 — 정본에서 등급별 2건씩 뽑은 시연용 소형 슬레이트.
DEFAULT_SLATE = "datasets/gold_real/builds/demo_slate_v1.jsonl"

# 콘솔 G1 카드의 '데모 샘플'과 같은 성격의 후보 본문. 스크립트를 자기완결로 두어
# 데이터셋 파일 유무와 무관하게 어느 서버에서나 그대로 돈다.
DEMO_DOCS = [
    {"doc_id": "DEMO-TS-01", "title": "차세대 공정 핵심 파라미터 원본",
     "text": "당사 반도체연구소의 차세대 공정 핵심 파라미터 원본. 식각 레시피 전체 조건표와 "
             "수율 보정 계수를 포함한다. 사외 반출 금지, 열람은 연구소장 승인자에 한한다.",
     "label_hint": "TS", "source": "demo_console"},
    {"doc_id": "DEMO-TS-02", "title": "핵심 알고리즘 설계 원본 및 검증 결과",
     "text": "당사 AI연구소가 자체 개발한 핵심 예측 알고리즘의 설계 원본과 내부 검증 결과. "
             "모델 구조와 학습 하이퍼파라미터 전체를 기재한다. 유출 시 기술 우위가 즉시 소멸한다.",
     "label_hint": "TS", "source": "demo_console"},
    {"doc_id": "DEMO-S1-01", "title": "주력 제품 배합비 및 제조 공정 명세",
     "text": "당사 제품개발팀의 주력 제품 핵심 배합비(재료 15종, 비율 0.01% 단위)와 숙성 공정 "
             "조건을 담은 명세서. 경쟁사 확보 시 동등 품질 재현이 가능하다.",
     "label_hint": "S1", "source": "demo_console"},
    {"doc_id": "DEMO-S1-02", "title": "미공개 신제품 출시 전략 및 원가 구조",
     "text": "당사 전략기획팀의 미공개 신제품 출시 전략. 목표 단가, 원가 구조, 채널별 마진율을 "
             "포함한다. 출시 전 유출 시 협상력이 훼손된다.",
     "label_hint": "S1", "source": "demo_console"},
    {"doc_id": "DEMO-S2-01", "title": "상반기 조직 개편 계획안 (대외비)",
     "text": "당사 인사기획팀의 상반기 조직 개편 계획안. 사업부 통폐합 범위와 재배치 인원 규모를 "
             "다룬다. 확정 전 외부 공유를 금한다.",
     "label_hint": "S2", "source": "demo_console"},
    {"doc_id": "DEMO-S2-02", "title": "본사 이전 후보지 비교 및 비용 분석 초안",
     "text": "당사 총무팀의 본사 이전 계획 초안. 후보지 3곳의 임차 조건과 이전 비용을 비교한다. "
             "협상 진행 중이라 대외 공개 시 임차 협상에 불리하다.",
     "label_hint": "S2", "source": "demo_console"},
    {"doc_id": "DEMO-S3-01", "title": "기술 웨비나 발표 자료 (외부 공개용)",
     "text": "당사 기술마케팅팀이 외부 고객 대상 웨비나에서 발표한 자료. 이미 공개된 제품 소개와 "
             "일반적인 산업 동향만 담고 있다.",
     "label_hint": "S3", "source": "demo_console"},
    {"doc_id": "DEMO-S3-02", "title": "연간 사회공헌 활동 결과 보고서 (공개본)",
     "text": "당사 CSR팀이 발간한 연간 사회공헌 활동 결과 공개 보고서. 수혜 규모와 프로그램 개요를 "
             "담은 대외 배포본이다.",
     "label_hint": "S3", "source": "demo_console"},
]


def line(c="─"):
    print(c * 72)


def step(no: str, title: str, card: str):
    print(f"\n[{no}] {title}")
    print(f"     콘솔 대응: {card}")


def fail(msg: str, r=None) -> int:
    print(f"  [실패] {msg}")
    if r is not None:
        print(f"         HTTP {r.status_code} {r.text[:300]}")
        if r.status_code == 403:
            print("         → 서버 .env 의 API_KEY_ROLE 이 admin 인지 확인하세요"
                  " (system 이면 train·activate 가 403).")
    return 1


def poll(cli, url: str, done: set[str], label: str, timeout_s: int = 900, every: float = 3.0):
    """상태가 done/failed 가 될 때까지 폴링. 마지막 응답 dict 를 돌려준다."""
    waited = 0.0
    last = None
    while waited < timeout_s:
        r = cli.get(url, headers=HEADERS)
        if r.status_code >= 400:
            print(f"  [상태조회 실패] HTTP {r.status_code} {r.text[:200]}")
            return None
        last = r.json()
        st = str(last.get("status") or "")
        prog = last.get("progress")
        tail = f" · {prog:.0%}" if isinstance(prog, (int, float)) and prog else ""
        print(f"  · {label}: {st}{tail}  ({int(waited)}s)")
        if st in done:
            return last
        time.sleep(every)
        waited += every
    print(f"  [시간초과] {label} 이(가) {timeout_s}s 내에 끝나지 않았습니다.")
    return last


def _local_candidates(gold_path: str) -> list[Path]:
    """서버가 준 경로를 이 머신에서 찾아볼 후보들.

    서버가 컨테이너 안에서 돌면 gold_path 는 /app/datasets/... 같은 컨테이너 절대경로라
    호스트에서 그대로는 열리지 않는다. datasets/ 이후를 잘라 cwd(=poc/) 기준으로도 찾는다.
    """
    out = [Path(gold_path)]
    norm = gold_path.replace("\\", "/")
    idx = norm.find("datasets/")
    if idx >= 0:
        out.append(Path(norm[idx:]))
    return out


def read_candidates(gold_path: str | None, *, local_hint: str | None = None):
    """서명 대상 후보(doc_id)를 읽는다.

    gold_path 는 서버 파일시스템 경로다. 서버와 같은 머신(또는 datasets/ 를 바인드 마운트한
    컨테이너)이면 읽히고, 진짜 원격이면 읽지 못한다 — 그 경우 화면 서명 경로로 안내한다.
    local_hint 는 --register 로 우리가 직접 지정한 경로(이미 로컬 상대경로).
    """
    if not gold_path and not local_hint:
        return None
    tries: list[Path] = []
    if local_hint:
        tries.append(Path(local_hint))
    if gold_path:
        tries.extend(_local_candidates(gold_path))
    p = next((t for t in tries if t.exists() and t.is_file()), None)
    if p is None:
        return None
    docs = []
    with p.open(encoding="utf-8") as f:
        for row in f:
            row = row.strip()
            if not row:
                continue
            try:
                d = json.loads(row)
            except json.JSONDecodeError:
                continue
            if d.get("doc_id"):
                docs.append(d)
    return docs


def _arg_value(argv: list[str], name: str, default: str) -> str:
    """--slate PATH / --slate=PATH 둘 다 받는다(표준 라이브러리만 쓰는 최소 파싱)."""
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return default


def main() -> int:
    argv = sys.argv[1:]
    args = set(argv)
    do_train = "--train" in args
    do_activate = "--activate" in args
    publish = "--publish" in args
    use_register = "--register" in args or "--slate" in args or any(a.startswith("--slate=") for a in argv)
    slate = _arg_value(argv, "--slate", DEFAULT_SLATE)
    # 서명은 기본으로 하지 않는다 — 사람이 하는 단계다. --reviewer 에 실계정을 주면 대행.
    reviewer = _arg_value(argv, "--reviewer", "").strip()

    line("═")
    print("  시나리오 B — 골든셋 → 검수·서명 → 재학습 → 배포 → 메트릭 (루프 B · 모델 갱신)")
    print(f"  대상 서버: {BASE}")
    print(f"  G1 방식  : {'기존 슬레이트 등록(LLM 불필요)' if use_register else 'LLM 재라벨링 빌드'}")
    print(f"  단계 범위: G1·G2" + (" → 재학습" if do_train else "") + (" → 배포·메트릭" if do_activate else ""))
    if publish:
        print("  ⚠ --publish — 서명 결과를 라이브 locked_gold_eval 경로에 병합합니다.")
    line("═")

    with httpx.Client(timeout=300.0) as cli:
        # ── G1. 골든셋 후보 확보 ──────────────────────────────────────────────
        if use_register:
            step("G1", f"기존 슬레이트 등록 — 재라벨링 없이 검수·서명만 ({slate})",
                 "[모델] 탭 → G1 골든셋 후보 빌더 · 하단 [기존 슬레이트 등록]")
            r = cli.post(f"{API}/golden/jobs/register", headers=HEADERS,
                         json={"build_path": slate, "actor": ACTOR})
            if r.status_code == 404:
                return fail(f"슬레이트를 찾을 수 없습니다: {slate}\n"
                            f"         서버 파일시스템 기준이며 datasets/ 하위만 허용됩니다.", r)
            if r.status_code >= 400:
                return fail("슬레이트 등록 거부", r)
            job_id = r.json()["golden_job_id"]
            print(f"  • job_id            : {job_id}")
            st = cli.get(f"{API}/golden/jobs/{job_id}", headers=HEADERS).json()
        else:
            step("G1", "골든셋 후보 생성 (데모 샘플 · noop · require_evidence)",
                 "[모델] 탭 → G1 골든셋 후보 빌더 · [기본값으로 시작]")
            r = cli.post(f"{API}/golden/build", headers=HEADERS, json={
                "source_type": "inline",
                "docs": DEMO_DOCS,
                "n": len(DEMO_DOCS),
                "llm_provider": "noop",
                "require_evidence": True,
                "actor": ACTOR,
            })
            if r.status_code >= 400:
                return fail("골든셋 후보 생성 요청 거부", r)
            job_id = r.json()["golden_job_id"]
            print(f"  • job_id            : {job_id}")

            st = poll(cli, f"{API}/golden/jobs/{job_id}", {"done", "failed"}, "골든 빌드", timeout_s=600)
            if not st or st.get("status") != "done":
                return fail(f"골든 빌드 미완료 (status={st.get('status') if st else '?'} error={st.get('error') if st else ''})")

        gold = st.get("gold_count") or 0
        unc = st.get("uncertain_count") or 0
        print(f"  • 후보(gold)        : {gold}건   보류(uncertain): {unc}건")
        print(f"  • gold_path         : {st.get('gold_path')}")

        if gold == 0 and not use_register:
            line("═")
            print("  gold 후보가 0건입니다. llm_provider=noop 의 구조적 한계입니다.")
            print("  noop 은 등급 라벨러가 아니라 합성 문서 생성기라, 라벨 파싱이 S3/0.5 로 떨어져")
            print("  룰과 합의되지 않고 전건이 needs_review 로 갑니다(2026-08-08 실측: gold 0 · uncertain 8).")
            print("")
            print("  둘 중 하나로 진행하세요:")
            print("    · LLM 이 있는 서버   → 콘솔 G1 카드의 provider 를 vllm_qwen 등으로 바꿔 실행")
            print("    · LLM 이 없는 환경   → 이 스크립트를 --register 로 다시 실행")
            print(f"        .venv/Scripts/python.exe scripts/demo_e2e_golden.py --register{' --train' if do_train else ''}")
            line("═")
            return 1

        review_url = st.get("review_url") or f"/api/v1/golden/jobs/{job_id}/review.html"
        signoff_url = st.get("signoff_url") or f"/api/v1/golden/jobs/{job_id}/signoff.html"

        # ── G2. 검수 · 서명 ───────────────────────────────────────────────────
        step("G2", "검수 · 서명", "[모델] 탭 → [검수 HTML 열기] · [서명 HTML 열기]")
        print(f"  • 검수 화면          : {BASE}{review_url}")
        print(f"  • 서명 화면          : {BASE}{signoff_url}")

        if not reviewer:
            print("  • 서명               : 하지 않음 — 사람이 하는 단계입니다.")
            print("    서버가 reviewer_id 를 실계정으로 강제하므로(시연 마커 'demo-console' 은 403)")
            print("    위 서명 화면에서 처리하거나, 리허설이라면 --reviewer <실계정> 을 주십시오.")
            if not do_train:
                line("═")
                print("  G1 완료. 서명 후 이어가려면 --train (배포까지는 --train --activate).")
                line("═")
                return 0
            print("    (--train 이 지정돼 재학습으로 계속 진행합니다 — 서명 없이도 학습은 돕니다.)")

        elif gold <= 0:
            return fail("서명할 gold 후보가 0건입니다 — require_evidence 를 낮추거나 후보를 늘리세요.")

        else:
            cands = read_candidates(st.get("gold_path"), local_hint=slate if use_register else None)
            if cands is None:
                line("═")
                print("  후보 파일을 이 머신에서 읽지 못했습니다(원격 서버 경로).")
                print("  → 위 서명 화면에서 사람이 서명한 뒤 --train 으로 다시 실행하세요.")
                line("═")
                return 0

            print(f"  • 서명 대상          : {len(cands)}건 (전건 approve)")
            print(f"  • 서명자             : {reviewer}  ⚠ 이 이름으로 실제 기록됩니다")
            print(f"  • publish            : {publish} " + ("(라이브 평가경로 병합)" if publish else "(run-스코프 미리보기 · 정본 무변경)"))
            r = cli.post(f"{API}/golden/jobs/{job_id}/signoff", headers=HEADERS, json={
                "decisions": [{"doc_id": d["doc_id"], "decision": "approve",
                               "note": "리허설 서명"} for d in cands],
                "actor": {"user_id": reviewer, "role": "reviewer"},
                "publish": publish,
            })
            if r.status_code == 403:
                return fail(
                    f"서명 거부 — reviewer_id '{reviewer}' 가 실계정으로 인정되지 않았습니다.\n"
                    f"         머신·플레이스홀더 접두(ai_·llm_·demo·auto·bot 등)는 거부됩니다.", r)
            if r.status_code >= 400:
                return fail("서명 요청 거부", r)
            sg = r.json()
            print(f"  • 서명 결과          : locked={sg.get('locked')} rejected={sg.get('rejected')}")
            print(f"  • 서명자(서버 기록)  : {sg.get('reviewer_id')}"
                  + ("  ⚠ 클라 actor 가 인증 신원으로 덮어써짐" if sg.get("overridden") else ""))
            if sg.get("publish_note"):
                print(f"  • publish 비고       : {sg['publish_note']}")

            if not do_train:
                line("═")
                print("  G1·G2 완료. 재학습부터 이어가려면 --train (배포까지는 --train --activate).")
                line("═")
                return 0

        # ── 3. 재학습 ─────────────────────────────────────────────────────────
        step("3", "재학습 트리거 (incremental)", "[모델] 탭 → 3 재학습 트리거")
        r = cli.post(f"{API}/train", headers=HEADERS, json={
            "training_type": "incremental",
            "actor": ACTOR,
        })
        if r.status_code >= 400:
            return fail("재학습 요청 거부", r)
        tj = r.json()["train_job_id"]
        print(f"  • train_job_id      : {tj}")

        ts = poll(cli, f"{API}/train/jobs/{tj}", {"done", "failed"}, "재학습", timeout_s=3600, every=5.0)
        if not ts or ts.get("status") != "done":
            return fail(f"재학습 미완료 (status={ts.get('status') if ts else '?'} error={ts.get('error') if ts else ''})")
        version = ts.get("model_version")
        print(f"  • 산출 모델 버전     : {version}")
        if ts.get("metrics_so_far"):
            print(f"  • 학습 지표          : {json.dumps(ts['metrics_so_far'], ensure_ascii=False)}")

        if not do_activate:
            line("═")
            print(f"  재학습 완료. 배포까지 보려면 --activate 를 함께 주세요 (version={version}).")
            line("═")
            return 0

        # ── 4. 배포 ───────────────────────────────────────────────────────────
        step("4", "모델 배포 (deploy gate 적용)", "[모델] 탭 → 4 모델 서빙 · 배포 게이트")
        if not version:
            return fail("배포할 model_version 을 재학습 결과에서 받지 못했습니다.")
        r = cli.post(f"{API}/admin/model/activate", headers=HEADERS,
                     json={"version_label": version, "force": False})
        if r.status_code >= 400:
            return fail("배포 요청 거부", r)
        av = r.json()
        if av.get("activated"):
            print(f"  • 배포 완료          : {av.get('model_version')} (리로드={av.get('reloaded')})")
        elif av.get("blocked"):
            print(f"  • 배포 차단(정상 동작): {av.get('reason')}")
            print("    deploy gate 가 고등급 미탐(FNR)·F1 회귀를 막았습니다. 게이트가 살아 있다는 증거입니다.")
        else:
            print(f"  • 미활성             : {av.get('reason')}")

        # ── 5. 메트릭 ─────────────────────────────────────────────────────────
        step("5", "모델 메트릭 확인", "[모델] 탭 → 5 모델 메트릭 · FNR 게이트")
        r = cli.get(f"{API}/metrics/latest", headers=HEADERS)
        if r.status_code == 404:
            # 활성 모델이 없으면 404 "no active model" — 배포가 게이트에 막힌 경우의 정상 귀결이라
            # 실패로 다루지 않는다(위 4단계에서 이미 차단 사유를 출력했다).
            print("  • 활성 모델 없음     : 메트릭을 산출할 대상이 아직 없습니다(정상 — 배포 미완료).")
        elif r.status_code >= 400:
            return fail("메트릭 조회 실패", r)
        else:
            m = r.json()
            # 필드명은 schemas/metrics.py MetricsReport 기준(2026-08-08 실측 정정).
            print(f"  • model_version     : {m.get('model_version')}")
            for key, label in (("f1_macro", "F1(macro)"), ("accuracy", "정확도"),
                               ("fnr_overall", "미탐률(전체)"), ("sample_count", "평가 건수")):
                if m.get(key) is not None:
                    print(f"  • {label:<16}: {m[key]}")
            fnr = m.get("fnr_by_grade") or {}
            if fnr:
                # 고등급 미탐이 이 시스템의 핵심 KPI — 등급별로 펼쳐 보인다.
                print("  • 등급별 미탐률     : "
                      + "  ".join(f"{g}={v}" for g, v in fnr.items()))

    line("═")
    print("  시나리오 B 완료 — 골든셋에서 배포까지 한 바퀴 돌았습니다.")
    print(f"  화면으로 같은 흐름 보기: {BASE}/demo/admin.html  → [모델] 탭")
    line("═")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
