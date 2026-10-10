#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""KL 인수인계 매뉴얼 네 권을 만든다 — 안내서 · 설치 · 운영 · 학습(재학습).

서식 정본 = F:\\antigravity\\lib\\docs\\DOC_STANDARD.md (kit.doc / kit.chart 에서 뽑은 것). 새로 짜지 않는다.
둘 곳 = doc/result/KL_인수인계_2026-09-29/  (KL_API_연동안내_2026-09-25 · KL_보고_2026-09-20 과 같은 층)
번들에는 싣지 않는다 — 2026-09-25 사용자 지시("문서는 따로 줄 것, 배포본에서는 뺀다").

수치·동작의 출처(2026-09-29 이 리포와 번들 실물에서 직접 확인):
  · 번들 실크기·구성      poc/dist/rocky-koipa-bundle-20260929  (du -sh · ls · manifest.yaml)
  · 설치기 단계·옵션      poc/scripts/setup.sh · preflight_host.sh · deploy_airgap.sh · verify_install.sh
  · 노드 프로파일         poc/src/koipa/config.py (full-train · onprem-local)
  · 학습 경로             poc/src/koipa/workers/tasks.py · services/training_service.py · api/training.py · api/admin.py
  · 정기 작업             poc/src/koipa/workers/celery_app.py
  · 학습 실행 시험        워커 이미지 koipa-worker:rocky-koipa-20260929 + 번들 models/hf 로 소량 학습(32행·1에폭)
  · 백업·복구 시험        일회용 pgvector/pgvector:pg16 컨테이너 · 일회용 볼륨(koipa-api 이미지의 tar)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, r"F:\antigravity\lib\src")
from kit import chart, doc  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "result" / "KL_인수인계_2026-09-29"
PRODUCT = "AI 영업비밀 관리시스템"
DATE = "2026-09-29"
BUNDLE_VER = "rocky-koipa-20260929"
SRV = "&lt;서버주소&gt;"

esc = doc.esc


def C(s: str) -> str:
    return "<code>%s</code>" % esc(s)


PRE_MAX = 86          # A4 인쇄 실폭에서 11.5px mono 가 안 잘리는 글자 수(인쇄 미리보기에서 92자 줄이 잘렸다)
LONG_PRE: list[str] = []


def PRE(s: str) -> str:
    body = s.strip("\n")
    for ln in body.splitlines():
        if len(ln) > PRE_MAX:
            LONG_PRE.append("%d자: %s" % (len(ln), ln[:60]))
    return "<pre>%s</pre>" % esc(body)


# 인쇄 보정(정본에 없는 추가): 긴 매뉴얼 표가 통째로 다음 쪽으로 밀려 빈 면이 생기는 것을 막고,
# 머리글이 좁은 열에서 글자 단위로 꺾이지 않게 한다. 행은 그대로 쪼개지지 않는다(tr avoid).
EXTRA_CSS = "th{white-space:nowrap}\n@media print{.tw{break-inside:auto;page-break-inside:auto}}"


def N(s: str) -> str:
    """좁은 번호·단계 칸이 두 줄로 꺾이지 않게 한다."""
    return '<span style="white-space:nowrap">%s</span>' % s


def info(reader: str, basis: str, scope: str) -> str:
    return doc.docinfo([
        ("대상 독자", reader),
        ("대상 번들", "%s (번들 안 %s 의 %s 값)입니다. 번들을 다시 빌드하면 이 값이 바뀝니다"
         % (C(BUNDLE_VER), C("manifest.yaml"), C("version"))),
        ("근거", basis),
        ("검증 범위", scope),
        ("작성일", DATE),
    ])


def assemble(sub: str, eyebrow: str, title: str, meta: str, *parts: str, foot_left: str) -> str:
    body = doc.nav(PRODUCT, sub, "인수인계") + doc.wrap(
        doc.top(eyebrow, title, meta), *parts, doc.foot(foot_left, DATE))
    return doc.page(title, body, EXTRA_CSS)


COMPOSE_ALIAS = (
    'export COMPOSE="docker compose --env-file .env -f infra-config/docker-compose.airgap.yml"'
)
COMPOSE_ALIAS_PRE = (
    'export COMPOSE="docker compose --env-file .env \\\n'
    '  -f infra-config/docker-compose.airgap.yml"'
)

# ═════════════════════════════════════════════════════════════════════════
# 00 안내서
# ═════════════════════════════════════════════════════════════════════════


def build_00() -> str:
    steps = chart.steps([
        ("1", "안내서 확인", "납품 구성과 인수 전 유의 사항을 읽습니다", "본 문서 4·6절", ""),
        ("2", "설치", "서버를 점검하고 setup.sh 로 설치합니다", "설치 매뉴얼", ""),
        ("3", "설치 확인", "인수 체크리스트 13항목을 확인합니다", "본 문서 5절", ""),
        ("4", "운영 인수", "계정·검수·백업·모니터링 절차를 인수합니다", "운영 매뉴얼", ""),
        ("5", "재학습 인수", "학습셋 준비·학습 실행·활성화·롤백을 인수합니다", "학습 매뉴얼", ""),
    ], title="인수 순서")

    body = [
        info("인수 담당 엔지니어와 운영 관리자입니다",
             "번들 실물과 소스를 직접 열어 확인하였습니다. 확인하지 못한 항목은 그렇게 기재하였습니다",
             "8절에 확인한 범위와 확인하지 못한 범위를 나누어 적었습니다"),

        doc.h2(1, "이 문서 세트"),
        doc.p("이 세트는 번들을 인수하는 담당자가 설치·운영·재학습을 직접 수행할 수 있도록 정리한 매뉴얼 "
              "네 권입니다. 각 문서는 독립적으로 읽을 수 있습니다. 명령은 모두 번들 폴더에서 실행하는 것을 "
              "전제로 합니다."),
        doc.table(
            ["문서", "읽는 사람", "다루는 내용", "파일"],
            [["인수인계 안내서(본 문서)", "전원",
              "납품 구성 · 시스템 구조 · 인수 체크리스트 · 인수 전 유의 사항", C("00_인수인계_안내서.html")],
             ["설치 매뉴얼", "설치 담당",
              "서버 준비 → 설치 → 설치 확인 → 외부 접속 설정 → 재설치·업그레이드",
              '<a href="01_설치_매뉴얼.html">%s</a>' % C("01_설치_매뉴얼.html")],
             ["운영 매뉴얼", "운영 담당 · 관리자",
              "기동·정지 · 계정·토큰 · 검수 · 백업·복구 · 모니터링 · 장애 대응",
              '<a href="02_운영_매뉴얼.html">%s</a>' % C("02_운영_매뉴얼.html")],
             ["학습·재학습 매뉴얼", "모델 담당",
              "학습셋 준비 · 학습 실행 · 게이트 · 활성화 · 롤백 · 문제 해결",
              '<a href="03_학습_재학습_매뉴얼.html">%s</a>' % C("03_학습_재학습_매뉴얼.html")]],
        ),
        doc.fig(steps, "번호 순서로 진행합니다. 3번은 설치 직후 한 번, 4·5번은 인수 기간 동안 담당자별로 "
                       "병행할 수 있습니다."),

        doc.h3("이 세트 밖의 기존 문서"),
        doc.table(
            ["문서", "위치(리포 기준)", "용도"],
            [["관리자 콘솔 사용 설명서", C("poc/docs/관리자콘솔_사용설명서.html"), "콘솔 화면별 사용법"],
             ["골든셋 관리 콘솔 사용 매뉴얼", C("doc/result/KL_AI자료_2026-08/골든셋_관리콘솔_사용매뉴얼.html"),
              "골든셋 검수 화면 사용법"],
             ["사용자 매뉴얼(검수 관리자)", C("doc/result/감리정본/사용자매뉴얼_검수관리자.html"),
              "검수 관리자용 안내"],
             ["KL API 연동 안내서", C("doc/result/KL_API_연동안내_2026-09-25/KL_API_연동_안내서.html"),
              "KL 포털 연동 API 규격과 예제"],
             ["학습 데이터 입력 경로 명세서", C("doc/result/KL_회신_2026-08-28/첨부/학습_데이터_입력_경로_명세서.html"),
              "학습 데이터가 들어오는 경로 명세"]],
        ),
        doc.p("위 문서와 번들에 동봉된 %s · %s · %s · %s 는 이번에 고치지 않았습니다. 이 세트와 다른 곳은 "
              "6절에 적었습니다." % (C("README.md"), C("docs/INSTALL.md"), C("docs/OPERATION.md"),
                                    C("docs/TROUBLESHOOTING.md"))),
        doc.act("미확인", "위 기존 문서 다섯 건의 내용이 현재 번들과 일치하는지는 이번에 대조하지 않아 "
                        "확인하지 못하였습니다"),

        doc.h2(2, "시스템 구조"),
        doc.p("이 시스템은 문서를 받아 등급(TS · S1 · S2 · S3)을 판정합니다. 판정 신뢰도가 낮은 문서는 자동 "
              "확정하지 않고 검수 대기열로 보내며, 사람이 바로잡은 판정(교정)은 재학습 데이터가 됩니다. "
              "구성 요소는 모두 컨테이너이고 인터넷 연결 없이 설치됩니다."),
        doc.table(
            ["구성 요소", "이미지", "역할", "외부 노출(기본)"],
            [["api", C("koipa-api:" + BUNDLE_VER), "REST API · 관리 콘솔 · 검수 화면", C("127.0.0.1:8000")],
             ["worker", C("koipa-worker:" + BUNDLE_VER),
              "비동기 분류 · 색인 · 학습(큐 classify · index · synthesis · learning · celery)", "없음"],
             ["beat", "worker 와 같은 이미지", "정기 작업 발행. 반드시 1개만 기동합니다", "없음"],
             ["postgres", C("pgvector/pgvector:pg16"), "DB · 벡터 색인", C("127.0.0.1:5432")],
             ["redis", C("redis:7.2-alpine"), "작업 대기열 · 작업 상태", C("127.0.0.1:6379")],
             ["nginx-mtls (선택)", C("nginx:1.27-alpine"), "상호 TLS 종단 프록시. 프로파일 mtls 로만 기동", "443"],
             ["관측성 6종 (선택)", "prometheus · alertmanager · grafana · loki · promtail · postgres-exporter",
              "지표 · 알림 · 대시보드", "127.0.0.1:9090 · 9093 · 3000 · 3100"]],
        ),
        doc.table(
            ["모델", "번들 위치", "크기", "용도"],
            [["분류기 v-fe4b386b", C("models/classifier-trained/"), "713MB", "등급 판정(서빙)"],
             ["kakaobank/kf-deberta-base", C("models/hf/hub/models--kakaobank--kf-deberta-base/"), "717MB",
              "재학습의 시작 모델"],
             ["nlpai-lab/KURE-v1", C("models/hf/hub/models--nlpai-lab--KURE-v1/"), "2.2GB",
              "임베딩(검색 · 유사 문서 · 규정 참고 표시)"]],
        ),
        doc.p("%s 의 models 목록에는 %s 가 적혀 있으나 가중치는 번들에 없습니다(%s 의 실물은 위 두 모델입니다). "
              "설치기가 만드는 %s 의 %s 는 %s 이므로 LLM 을 쓰는 기능(규정 LLM 고르기 · 합성 문서 생성)은 "
              "LLM 서버를 따로 준비한 경우에만 동작합니다."
              % (C("manifest.yaml"), C("Qwen/Qwen3-14B"), C("models/hf/hub"), C(".env"),
                 C("LLM_PROVIDER"), C("noop"))),

        doc.h2(3, "설치 대상 두 노드"),
        doc.p("번들은 하나이고 두 대상이 같은 번들을 씁니다. 설치할 때 %s 값만 다릅니다." % C("NODE")),
        doc.table(
            ["항목", "지재원 노드", "고객사 노드"],
            [["설치 명령", C("sudo NODE=jjw bash setup.sh"), C("sudo NODE=customer bash setup.sh")],
             ["프로파일", C("full-train"), C("onprem-local")],
             ["망 · 설치 주체", "공공망 · KL 이 원격 접속해 설치", "폐쇄망 · 매체 반입 후 직접 설치"],
             ["성격", "학습 · 전문가 검수 노드", "운영 추론 노드"],
             ["GPU", "있으면 오버레이 적용. 단 번들의 워커 이미지는 CPU 용입니다(6절 3번)", "불요(CPU 전용)"],
             ["학습 API(/train)", "등록됨", "등록됨"],
             ["자동 재학습",
              "교정이 쌓이면 자동으로 학습이 대기열에 들어갈 수 있습니다(30분마다 점검)",
              "없음. 야간 스케줄은 기본 꺼짐이고 수동으로 시작합니다"],
             ["전문가 검수 문서", "설치 중 1,711건을 자동 적재", "적재하지 않음"],
             ["자동 로그인 계정", C("expert-01") + " (reviewer 전용)", C("admin") + " (admin · reviewer)"],
             ["모델 수동 활성화 조건", "회귀 게이트", "회귀 게이트 + 평가 정답(locked eval) 준비도"]],
        ),
        doc.src("근거: scripts/setup.sh 69~70행 · src/koipa/config.py 프로파일 정의 · workers/celery_app.py"),

        doc.h2(4, "납품 구성"),
        doc.p("번들 폴더 전체 크기는 5.8GB 입니다(2026-09-29 빌드본을 %s 로 측정)." % C("du -sh")),
        doc.table(
            ["폴더 · 파일", "크기", "내용"],
            [[C("docker-images/"), "2.1GB",
              "이미지 tar. api 800MB · worker 788MB · postgres 156MB · redis 16MB · nginx-mtls 22MB · 관측성 6종 합 약 390MB"],
             [C("models/"), "약 3.6GB", "분류기 713MB · kf-deberta-base 717MB · KURE-v1 2.2GB"],
             [C("rpms/"), "124MB", "컨테이너 런타임 RPM 30개. Rocky 8(el8) 전용입니다"],
             [C("golden_review_batch/"), "28MB", "전문가 검수 문서 1,711건(문서·메타데이터 파일 3,422개)"],
             [C("infra-config/"), "—", "docker-compose.airgap.yml · docker-compose.gpu.yml · mtls/nginx.mtls.conf · .env.template"],
             [C("acceptance/"), "952KB", "인수 샘플 문서와 판정 기준(run_acceptance.sh)"],
             [C("observability/"), "82KB", "관측성 스택 설정"],
             [C("docs/ · licenses/"), "—", "INSTALL · OPERATION · TROUBLESHOOTING · 오픈소스 라이선스"],
             ["루트 스크립트", "—",
              "setup.sh · preflight_host.sh · verify.sh · install.sh · deploy.sh · deploy_airgap.sh · "
              "deploy_rollback.sh · db_probe.sh · verify_install.sh"],
             ["무결성", "—", "manifest.yaml · manifest.json · CHECKSUMS.sha256"]],
        ),
        doc.src("근거: dist/rocky-koipa-bundle-20260929 의 du · ls 실측"),

        doc.h2(5, "인수 체크리스트"),
        doc.p("설치 직후 담당자가 순서대로 확인합니다. %s 는 %s 를 뜻합니다."
              % (C("$COMPOSE"), C(COMPOSE_ALIAS))),
        doc.table(
            ["번호", "확인 항목", "방법", "기대 결과", "확인"],
            [["1", "번들 무결성", C("bash verify.sh"), "Checksums OK", "□"],
             ["2", "서버 사전 점검", C("bash preflight_host.sh"), "차단 0건(경고는 내용 확인)", "□"],
             ["3", "설치", C("sudo bash setup.sh"), "완료 안내 출력 · 8단계 설치 검증 통과", "□"],
             ["4", "컨테이너 상태", C("$COMPOSE ps"), "postgres · redis · api · worker · beat 실행 중", "□"],
             ["5", "준비도", C("curl -s http://127.0.0.1:8000/api/v1/healthz/ready"), "HTTP 200", "□"],
             ["6", "인수 샘플", "설치 매뉴얼 4-3", "[acceptance] PASS · 0 veto", "□"],
             ["7", "인증",
              C("curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:8000/api/v1/classify "
                "-H 'Content-Type: application/json' -d '{\"doc_id\":\"t\",\"content\":\"x\"}'"),
              "401(키 없이는 거부)", "□"],
             ["8", "콘솔 접속", "콘솔 주소를 브라우저로 엽니다", "로그인 입력 없이 열림(지재원 노드는 후보 1,711건)", "□"],
             ["9", "외부 접속",
              "KL 포털 서버에서 " + C("curl -s -o /dev/null -w '%{http_code}' http://<서버주소>:8000/api/v1/healthz/live"),
              "200(설치 매뉴얼 5절 설정 후)", "□"],
             ["10", "백업·복구 시험", "운영 매뉴얼 7절", "덤프 복원 뒤 건수 일치", "□"],
             ["11", "토큰 만료일 기록", "설치 완료 안내의 만료일", "갱신 일정 등록(365일)", "□"],
             ["12", "학습셋 준비", "학습 매뉴얼 3절", "기본 경로 3개 존재(2,042 · 256 · 256행)", "□"],
             ["13", "소량 학습 시험", "학습 매뉴얼 4절", "학습 작업이 completed 로 종료", "□"]],
        ),

        doc.h2(6, "인수 전에 알아 둘 사항"),
        doc.p("아래 항목은 번들 실물과 소스를 확인하여 얻은 사실입니다. 결정이 필요한 항목은 마지막 열에 "
              "결정 주체를 적었습니다."),
        doc.table(
            ["번호", "사항", "내용과 근거", "조치 · 결정"],
            [["1", "외부 접속",
              "API 는 기본으로 127.0.0.1 에만 열립니다(API_BIND). 번들 README 와 설치 완료 안내는 "
              + SRV + ":8000 으로 접속하라고 적지만 기본 설치 상태에서는 다른 PC 와 KL 포털이 접속하지 못합니다. "
              "번들 문서 네 건에는 API_BIND 설명이 없습니다. 근거: docker-compose.airgap.yml 의 api ports 줄 · "
              ".env.template 40행",
              "설치 매뉴얼 5절에서 방식을 고릅니다. 결정: KL · 발주처"],
             ["2", "학습셋 미동봉",
              "번들에는 학습셋(train · val · test)이 없어 그대로는 재학습이 시작 직후 실패합니다. 근거: 번들 폴더 "
              "검색 0건 · 워커 이미지에서 기본 경로 3개가 없음을 확인",
              "학습 매뉴얼 3절로 준비합니다. 학습셋을 번들에 넣을지 별도로 전달할지는 결정: KL · 개발 측"],
             ["3", "GPU 학습 이미지 미동봉",
              "번들의 워커 이미지는 CPU 용 torch 입니다. 지재원 노드의 GPU 로 학습하려면 CUDA 이미지를 따로 "
              "빌드해야 하며 그 이미지와 docker-compose.gpu-train.yml 이 번들에 없습니다. 근거: Dockerfile.worker "
              "의 TORCH_INDEX 기본값 · 번들 infra-config 목록",
              "학습 매뉴얼 10절. 결정: KL"],
             ["4", "자동 재학습 동작",
              "지재원 노드는 30분마다 교정 누적을 점검해 조건이 되면 학습을 대기열에 넣습니다. 고객사 노드의 야간 "
              "스케줄은 기본 꺼져 있습니다. 근거: celery_app.py · config.py enable_nightly_retrain_schedule=False",
              "리허설 데이터 정리(운영 매뉴얼 10절). 결정: 운영 담당"],
             ["5", "실서버 미검증",
              "SELinux enforcing 인 실제 Rocky 8.10 호스트의 전체 리허설은 번들 INSTALL.md 0-1절이 미실시로 고지합니다. "
              "podman 경로와 별도 커널의 실서버 설치도 이번에 확인하지 못하였습니다",
              "인수 서버에서 최초 1회 실행 후 결과 공유. 결정: KL"],
             ["6", "mTLS 인증서 미동봉",
              "nginx-mtls 프로파일은 server.crt · server.key · ca.crt 를 요구하며 번들에 인증서가 없습니다. "
              "설정 파일의 server_name 은 예시값(api.koipa.example.com)입니다",
              "mTLS 를 쓸 때 KL 이 발급. 설치 매뉴얼 5절"],
             ["7", "토큰 유효 365일",
              "자동 로그인 토큰은 365일 뒤 만료되어 로그인이 풀립니다",
              "만료 전 setup.sh 재실행. 운영 매뉴얼 4절"],
             ["8", "자동 로그인 = 접근 통제 없음",
              "콘솔 주소에 닿는 누구나 해당 계정 권한으로 들어갑니다(설치기의 기본 동작입니다)",
              "폐쇄망 · 방화벽 · VPN 으로 접속을 제한. 끄려면 CONSOLE_AUTOLOGIN=0"],
             ["9", "번들 문서의 옛 수치",
              "INSTALL.md · README 의 '약 19.6GB' 는 추정치이고 실물은 5.8GB 입니다. preflight 안내문의 "
              "'번들 12GB' 도 실물과 다릅니다",
              "본 세트의 값을 따릅니다"],
             ["10", "재실행 · 업그레이드 함정",
              "번들 20260929 의 preflight_host.sh 는 스택이 떠 있으면 포트 사용 중으로 차단합니다. 또 기존 .env 가 "
              "있으면 IMAGE_TAG 를 갱신하지 않습니다. 개발 소스에는 재실행 판정이 고쳐져 있으나 이 번들에는 들어 있지 "
              "않습니다",
              "설치 매뉴얼 7절"],
             ["11", "LLM 미동봉",
              "Qwen3-14B 가중치가 번들에 없고 LLM_PROVIDER 는 noop 입니다",
              "LLM 기능이 필요하면 LLM 서버를 별도 준비. 결정: 발주처"]],
        ),
        doc.act("유의", "위 1번과 2번은 설치 직후 KL 포털 연동과 재학습에 바로 영향을 주므로 인수 첫날 "
                        "처리하도록 유의하여 주시기 바랍니다"),

        doc.h2(7, "용어"),
        doc.table(
            ["용어", "정의"],
            [["번들", "인터넷이 없는 서버에 설치하기 위해 컨테이너 이미지 · 모델 · 설치 스크립트를 한 폴더에 담은 설치 묶음입니다"],
             ["노드", "설치 대상 서버 한 대입니다. 지재원 노드와 고객사 노드가 있습니다"],
             ["프로파일", "노드 성격에 따라 기능 스위치 묶음을 정한 설정입니다. full-train 과 onprem-local 이 있습니다"],
             ["교정", "검수자가 시스템의 판정 등급을 사람이 바로잡은 기록입니다"],
             ["골든셋", "사람이 검수 · 서명하여 정답으로 확정한 문서 모음입니다"],
             ["평가 정답(locked eval)", "사람이 서명해 잠근 평가 전용 정답셋입니다. 학습에 쓰지 않은 문서만 넣습니다"],
             ["미탐", "정답이 TS 또는 S1 인 문서를 정답보다 낮은 등급으로 판정한 것입니다"],
             ["모델 버전(ModelVersion)", "학습 결과를 DB 에 등록한 행입니다. 활성 상태인 하나만 서빙에 쓰입니다"],
             ["배포 게이트", "새 모델을 활성화하기 전에 현재 활성 모델과 비교해 고등급 미탐 악화나 성능 붕괴를 막는 판정입니다"],
             ["핫리로드", "컨테이너를 다시 띄우지 않고 서빙 모델을 바꿔 올리는 동작입니다"]],
        ),

        doc.h2(8, "이 세트의 검증 범위"),
        doc.table(
            ["구분", "내용"],
            [["확인한 것",
              "번들 폴더의 구성과 크기 · 설치기와 사전점검 소스 · 노드별 설정 · 학습 코드 경로 · "
              "학습셋 복사 절차와 기본 경로 해석(워커 이미지) · 소량 학습 실행(32행 · 1에폭 · 33초, 번들 기반 모델을 "
              "오프라인으로 읽음) · DB 덤프와 복원 · 볼륨 백업과 복원(일회용 컨테이너와 볼륨)"],
             ["확인하지 못한 것",
              "별도 커널의 Rocky 8.10 실서버 설치 · SELinux enforcing · podman · API_BIND 변경 뒤 외부 호스트 접속 · "
              "mTLS 종단 · GPU 학습 · 스택이 떠 있는 상태에서 POST /train 이 등록 · 게이트까지 완주하는 것 · "
              "새 번들로의 업그레이드 · 알림 통지 채널"]],
        ),
        doc.act("미확인", "위 '확인하지 못한 것' 항목은 실제 인수 서버에서 수행하기 전에는 동작 여부를 "
                        "확인하지 못하였습니다"),
    ]
    return assemble("인수인계 안내서", "인수인계 · 안내서", "인수인계 안내서",
                    "AI 영업비밀 관리시스템 · 설치 · 운영 · 재학습 · " + DATE, *body,
                    foot_left="근거: 번들 실물 · scripts/setup.sh · config.py · celery_app.py · Dockerfile.worker")


# ═════════════════════════════════════════════════════════════════════════
# 01 설치 매뉴얼
# ═════════════════════════════════════════════════════════════════════════


def build_01() -> str:
    setup_stages = doc.table(
        ["단계", "이름", "하는 일"],
        [["0", "호스트 점검", "preflight_host.sh 를 실행합니다. 차단 항목이 있으면 여기서 멈춥니다"],
         ["1", "번들 무결성", "verify.sh 로 CHECKSUMS.sha256 을 대조합니다"],
         ["2", "컨테이너 런타임", "docker → podman 순으로 찾고 compose 를 고릅니다. 둘 다 없고 rpms/ 가 있으면 오프라인 설치합니다"],
         ["3", "이미지 적재", "docker-images/*.tar 를 docker load 합니다"],
         ["4", "설정 생성", ".env 를 만들고 비밀값을 자동 생성합니다. 이미 있으면 보존합니다"],
         ["5", "방화벽", "firewalld 가 켜져 있으면 개방 명령을 안내합니다. OPEN_FIREWALL=1 이면 API 포트를 엽니다"],
         ["6", "스택 기동", "deploy_airgap.sh 가 7단계(사전 요건 · 무결성 · 이미지 확인 · 모델 확인 · DB 기동 · 마이그레이션 · 앱 기동 · 준비도 대기)를 수행합니다"],
         ["6-1", "검수 문서 적재", "지재원 노드만. golden_review_batch 를 golden_data 볼륨에 복사합니다"],
         ["6-2", "모델 등록", "배포된 분류기를 모델 버전 표에 등록합니다. 활성화는 하지 않습니다"],
         ["7", "콘솔 로그인", "서명키와 토큰을 만들고 자동 로그인을 켭니다. 스택을 한 번 더 올립니다"],
         ["8", "설치 검증", "verify_install.sh 를 실행합니다"]],
    )
    options = doc.table(
        ["환경변수", "기본값", "의미"],
        [[C("NODE"), "자동 판별", "jjw 또는 customer. 자동 판별은 nvidia-smi 가 GPU 를 보면 jjw 입니다"],
         [C("API_PORT"), C("8000"), "API 를 노출하는 포트"],
         [C("DRY_RUN"), C("0"), "1 이면 할 일만 출력하고 아무것도 바꾸지 않습니다"],
         [C("OPEN_FIREWALL"), C("0"), "1 이면 firewalld 에 API 포트를 엽니다"],
         [C("ENV_FILE"), C(".env"), "생성하거나 쓸 env 파일"],
         [C("FORCE_ENV"), C("0"), "1 이면 기존 .env 를 덮어씁니다. 비밀값이 새로 만들어집니다(주의)"],
         [C("REVIEWER_ID"), C("expert-01"), "검수자 토큰의 계정 이름(지재원 노드). 검수 기록에 이 이름이 남습니다"],
         [C("CONSOLE_AUTOLOGIN"), C("1"), "0 이면 자동 로그인을 끄고 토큰을 붙여넣게 합니다"],
         [C("CONSOLE_TOKEN_DAYS"), C("365"), "콘솔 토큰 유효기간(일)"]],
    )
    env_table = doc.table(
        ["키", "값 · 의미", "비고"],
        [[C("DEPLOY_PROFILE"), "full-train 또는 onprem-local", "NODE 값에서 정해집니다"],
         [C("IMAGE_TAG"), "manifest.yaml 의 version", ".env 가 새로 만들어질 때만 채워집니다(7절)"],
         [C("API_PORT"), "API 포트", ""],
         [C("POSTGRES_PASSWORD"), "DB 비밀번호(자동 생성)", "기존 DB 볼륨이 있으면 바꾸지 않습니다"],
         [C("API_KEY"), "X-API-Key 인증 키(자동 생성)", "KL 포털이 호출할 때 씁니다"],
         [C("KOIPA_AUDIT_CHAIN_SECRET"), "감사체인 HMAC 키(자동 생성)", "분실하면 과거 감사 행을 재검증할 수 없습니다"],
         [C("STORAGE_ENCRYPTION_KEY"), "원본 암호화 키(자동 생성)", "분실하면 저장된 원본을 복호화할 수 없습니다. 별도 보관합니다"],
         [C("GOLDEN_HTML_URL_SECRET"), "검수 화면 서명 URL 키(자동 생성)", "비면 검수 화면이 무인증으로 열립니다"],
         [C("GOLDEN_REVIEW_BLIND_ENFORCED"), C("1"), "검수자 화면에서 제안 등급을 가립니다"],
         [C("GOLDEN_DEFAULT_REVIEW_BATCH"), "expert_review_1731_20260924", "후보 관리 화면이 처음 좁혀 보는 배치"],
         [C("AUTH_MODE"), C("both"), "API 키와 JWT 를 둘 다 받습니다"],
         [C("JWT_JWKS_PATH · JWT_ISSUER · JWT_AUDIENCE"), "콘솔 JWT 검증 설정", "검증키는 golden_data 볼륨에 있습니다"],
         [C("STORAGE_ENCRYPTION_ENABLED"), C("1"), "하드닝 프로파일이 요구합니다"],
         [C("STORAGE_BACKEND · VECTOR_BACKEND"), C("local · pg"), "로컬 파일시스템 저장 · pgvector 검색"],
         [C("LLM_PROVIDER"), C("noop"), "LLM 을 쓰지 않습니다"],
         [C("CORS_ALLOW_ORIGINS"), C('["http://127.0.0.1:포트"]'), "다른 PC 의 브라우저로 콘솔을 열면 실제 주소로 바꿉니다(5절)"],
         [C("CONSOLE_LOGIN_PREFILL_TOKEN · _ALLOW_UNSAFE"), "7단계가 추가", "자동 로그인 설정입니다"],
         ["선택 추가", C("API_BIND · PG_PORT · REDIS_PORT · GRAFANA_PASSWORD · WORKER_CPU_LIMIT"), "필요한 현장만 .env 에 한 줄 추가합니다"]],
    )
    trouble = doc.table(
        ["증상", "원인", "조치"],
        [["preflight 가 '컨테이너 런타임이 없다' 로 차단(번들 20260929)", "런타임이 없는 서버에서 이 번들의 점검이 차단으로 처리합니다",
          "3-2 의 rpms 설치를 먼저 실행한 뒤 다시 점검합니다"],
         ["'포트 8000 이 이미 사용 중이다'", "다른 프로세스가 쓰거나 이미 설치된 스택이 쓰는 경우입니다(번들 20260929)",
          "다른 프로세스이면 " + C("sudo API_PORT=18000 bash setup.sh") + ". 자기 스택이면 " + C("$COMPOSE stop") + " 후 재실행"],
         ["'docker 데몬에 접근할 수 없다'", "서비스 미기동 또는 권한 부족", C("sudo systemctl enable --now docker") + " 를 실행하고 사용자를 docker 그룹에 추가합니다"],
         ["'여유 디스크 …GB'", "사전점검 최소 40GB 미달", "디스크를 늘리거나 번들을 여유 있는 볼륨에 둡니다"],
         [C("/healthz/ready") + " 가 503", "의존성 미기동", C("$COMPOSE ps") + " 로 postgres 가 healthy 인지 확인합니다"],
         ["api 가 재시작을 반복", ".env 의 필수 비밀값 누락", C("$COMPOSE logs api") + " 의 첫 오류를 확인합니다. 신규 설치라면 .env 를 지우고 다시 설치합니다"],
         ["로그인 화면이 '토큰이 잘못됐다'", "토큰을 손으로 발급해 검증키가 교체된 경우입니다", C("sudo bash setup.sh") + " 를 다시 실행해 토큰을 새로 맞춥니다"],
         ["다른 PC 에서 접속되지 않음", "API_BIND 기본값이 127.0.0.1", "5절"],
         ["콘솔은 열리는데 화면의 API 호출이 실패", "CORS_ALLOW_ORIGINS 가 127.0.0.1 로 고정", ".env 의 값을 실제 접속 주소로 바꾸고 api 를 재생성합니다(5절)"],
         ["모델 적재에서 접근 거부(RHEL 계열)", "SELinux 라벨", C("sudo ausearch -m avc -ts recent") + " 로 확인합니다. 동봉 compose 는 :z 라벨을 이미 적용했습니다"],
         ["검수 문서가 0건(지재원 노드)", "6-1 단계 적재 실패", C("sudo bash setup.sh") + " 를 다시 실행합니다. 이미 있는 후보는 덮어쓰지 않습니다"],
         ["지표 카드와 모델 활성화 화면이 비어 있음", "6-2 단계 모델 등록 실패", C("sudo bash setup.sh") + " 를 다시 실행합니다(멱등)"]],
    )

    body = [
        info("설치 담당 엔지니어입니다",
             "scripts/setup.sh · preflight_host.sh · deploy_airgap.sh · verify_install.sh · docker-compose.airgap.yml 을 직접 읽고 "
             "번들 폴더의 실물과 대조하였습니다",
             "설치기를 실행해 본 결과가 아니라 소스와 번들 구성을 읽은 결과입니다. 별도 커널의 Rocky 8.10 실서버 설치는 확인하지 못하였습니다"),

        doc.h2(1, "설치 개요"),
        doc.p("설치는 세 줄입니다. 나머지(이미지 적재 · 비밀값 생성 · DB 마이그레이션 · 기동 · 자동 로그인 · 검증)는 "
              "%s 가 처리합니다. %s 는 손으로 편집하지 않습니다." % (C("setup.sh"), C(".env"))),
        doc.table(
            ["순서", "명령", "하는 일"],
            [["1", C("bash preflight_host.sh"), "서버가 설치 조건을 갖췄는지 점검합니다. 아무것도 바꾸지 않습니다"],
             ["2", C("bash verify.sh"), "반입한 파일이 온전한지 확인합니다. Checksums OK 가 나와야 합니다"],
             ["3", C("sudo bash setup.sh"), "이미지 적재부터 설치 검증까지 자동으로 진행합니다"]],
        ),
        doc.p("지재원 노드는 %s, 고객사 노드는 %s 로 실행합니다. 지정하지 않으면 GPU 유무로 판별합니다."
              % (C("sudo NODE=jjw bash setup.sh"), C("sudo NODE=customer bash setup.sh"))),

        doc.h2(2, "서버 준비"),
        doc.table(
            ["항목", "기준", "근거"],
            [["OS", "Rocky Linux 8.10 (운영 확정)", "INSTALL.md 부록 · " + C("cat /etc/os-release")],
             ["CPU", "4코어 이상. 미만이면 경고", "preflight 7절"],
             ["메모리", "16GB 이상 권고. 미만이면 경고", "preflight 7절. 학습에 필요한 메모리는 확인하지 못하였습니다"],
             ["디스크", "여유 40GB 이상(사전점검 최소). INSTALL.md 는 데이터 디스크 80GB 를 권고합니다",
              "번들 5.8GB + 적재 이미지(api · worker 각 약 3.5GB) + 데이터"],
             ["컨테이너 런타임", "docker(Engine 24 이상) + compose v2. 없으면 번들 rpms/(el8 30개)로 설치", "preflight 2절 · setup.sh 2단계"],
             ["포트", "8000(API_PORT). 5432 · 6379 는 루프백. 선택: 443(mTLS) · 9090 · 9093 · 3000 · 3100(루프백)", "docker-compose.airgap.yml"],
             ["방화벽", "firewalld 가 켜져 있으면 외부 접속 포트를 열어야 합니다", "preflight 4절"],
             ["SELinux", "Enforcing 이면 경고가 나옵니다. bind mount 에 :z 라벨이 적용되어 있습니다", "preflight 3절"],
             ["시간", "시간 동기 필요(감사 로그 · 토큰 유효기간에 영향)", "preflight 9절"],
             ["필수 명령", "tar · curl · sha256sum", "preflight 8절"],
             ["권한", "root 또는 sudo", "setup.sh"],
             ["GPU(지재원)", "NVIDIA 드라이버 + Container Toolkit. nvidia 런타임이 없으면 CPU 로 기동하고 경고합니다", "setup.sh 6단계"]],
        ),

        doc.h2(3, "설치 절차"),
        doc.h3("3-1 번들 반입"),
        doc.p("매체(USB 등)로 번들 폴더를 서버의 여유 있는 볼륨에 복사합니다. 이후 모든 명령은 번들 폴더(%s 가 있는 "
              "폴더)에서 실행합니다. 설치기는 %s · 토큰 파일을 이 폴더에 씁니다." % (C("setup.sh"), C(".env"))),
        doc.h3("3-2 사전 점검"),
        doc.p("런타임이 없는 서버라면 먼저 번들의 RPM 으로 설치합니다. 런타임이 이미 있으면 이 명령은 건너뜁니다."),
        PRE("""
sudo dnf -y --disablerepo='*' localinstall rpms/*.rpm
sudo systemctl enable --now docker
"""),
        PRE("bash preflight_host.sh"),
        doc.p("점검 항목은 아홉 가지입니다: 운영체제 · 컨테이너 런타임 · SELinux · 방화벽과 포트 · 사용자 uid/gid · "
              "cgroup · 자원(CPU · 메모리 · 디스크) · 필수 명령 · 시간 동기. 결과는 [ OK ] · [WARN] · [FAIL] 로 나오며 "
              "[FAIL] 이 하나라도 있으면 종료 코드 1 입니다. [WARN] 은 내용을 읽고 진행 여부를 정합니다."),
        doc.h3("3-3 무결성 검증"),
        PRE("bash verify.sh"),
        doc.p("Checksums OK 가 나오면 다음으로 넘어갑니다. 실패하면 반입 매체를 다시 확인합니다."),
        doc.h3("3-4 설치 실행"),
        PRE("""
sudo NODE=jjw bash setup.sh        # jjw node
sudo NODE=customer bash setup.sh   # customer node
"""),
        doc.p("설치기가 수행하는 단계는 다음과 같습니다."),
        setup_stages,
        doc.p("환경변수로 동작을 바꿀 수 있습니다."),
        options,
        doc.h3("3-5 설치 완료 화면"),
        doc.p("완료 안내에 다음이 출력됩니다: 접속 확인 명령 · 관리 콘솔 주소 · (지재원 노드) 검수 화면 주소 · 상태·로그 명령 · "
              "KL 포털 연동 값(호출 주소 · 인증 헤더 · API 키 위치) · 콘솔 로그인 계정과 토큰 만료일. "
              "완료 안내를 그대로 보관합니다."),
        doc.act("유의", "완료 안내의 주소 표기(" + SRV + ":8000)는 5절의 외부 접속 설정을 마치기 전에는 "
                        "다른 PC 에서 열리지 않으므로 유의하여 주시기 바랍니다"),

        doc.h2(4, "설치 확인"),
        doc.h3("4-1 준비도와 컨테이너 상태"),
        PRE("""
export COMPOSE="docker compose --env-file .env \\
  -f infra-config/docker-compose.airgap.yml"
$COMPOSE ps
curl -s http://127.0.0.1:8000/api/v1/healthz/ready
curl -s http://127.0.0.1:8000/api/v1/healthz/deep
"""),
        doc.p("%s 는 의존성(DB · 모델 · 저장소)이 준비되면 200 이고 미준비면 503 입니다. %s 는 파서 · 모델 · 임베더를 "
              "항목별로 보여 줍니다. 포트를 바꿨다면 8000 대신 그 값을 씁니다." % (C("/healthz/ready"), C("/healthz/deep"))),
        doc.h3("4-2 문서 한 건 분류(설치 검증 스크립트)"),
        PRE("API_PORT=8000 bash verify_install.sh"),
        doc.p("이 스크립트는 세 가지를 확인합니다: 준비도(200) · 컴포넌트 상태(모델 · DB · 벡터 저장소 · 추출기) · "
              "문서를 올려 파싱과 분류가 되는지(핵심 E2E). setup.sh 8단계가 이미 한 번 실행합니다."),
        doc.h3("4-3 인수 샘플 검증"),
        PRE("""
API_KEY="$(sudo sed -n 's/^API_KEY=//p' .env)" \\
BASE_URL=http://127.0.0.1:8000 bash acceptance/run_acceptance.sh
"""),
        doc.p("여러 형식(TXT · PDF · DOCX · XLSX · XLS · PPTX · HWPX) × 등급의 샘플을 올려 파싱과 분류를 확인합니다. "
              "합격 기준은 정확한 등급 일치가 아니라 (1) 파싱 성공과 (2) 고등급 미탐 없음입니다. "
              "기대 결과는 %s 입니다. 서빙은 의도적으로 안전한 방향으로 과분류하므로 등급이 정답보다 높게 나오는 "
              "것은 실패가 아닙니다." % C("[acceptance] PASS: N docs, 0 veto")),
        doc.h3("4-4 콘솔 접속"),
        doc.table(
            ["노드", "주소", "자동 로그인 계정", "확인"],
            [["지재원", C("http://%s:8000/api/v1/golden/candidates/manage.html" % "<서버주소>"), C("expert-01") + " (reviewer 전용)", "검수 후보 1,711건이 보입니다"],
             ["고객사", C("http://%s:8000/console/admin.html" % "<서버주소>"), C("admin") + " (admin · reviewer)", "관리 콘솔이 열립니다"]],
        ),
        doc.p("지재원 노드의 자동 로그인 계정은 reviewer 전용이라 제안 등급이 가려집니다. 관리자 기능(모델 활성화 · 배정 등)은 "
              "관리자 토큰이 필요하며 토큰 파일 위치는 운영 매뉴얼 4절에 적었습니다."),

        doc.h2(5, "외부 접속 설정(KL 포털 연동)"),
        doc.p("기본 설치에서 API 는 서버 자신(127.0.0.1)에서만 열립니다. KL 포털이나 다른 PC 가 접속하려면 두 방식 중 "
              "하나를 선택합니다. 두 방식은 병행하지 않고 하나만 씁니다."),
        doc.table(
            ["항목", "방식 A — 포트 직접 개방", "방식 B — mTLS 프록시"],
            [["설정", C("API_BIND=0.0.0.0") + " 을 .env 에 추가", "인증서를 두고 프로파일 mtls 로 nginx 기동"],
             ["통신", "평문 HTTP(포트 8000)", "TLS 1.3 + 클라이언트 인증서 필수(포트 443)"],
             ["API 바인드", "0.0.0.0", "127.0.0.1 유지. nginx 만 외부에 노출"],
             ["준비물", "방화벽 개방 · CORS 설정", "server.crt · server.key · ca.crt · 클라이언트 인증서"],
             ["위험", "자동 로그인 주소가 그대로 노출됩니다. 방화벽 · VPN 으로 접속원을 제한해야 합니다", "인증서 발급 · 배포 절차가 필요합니다"]],
        ),
        doc.h3("방식 A"),
        PRE("""
# .env (bundle folder)
API_BIND=0.0.0.0
CORS_ALLOW_ORIGINS=["http://SERVER_ADDRESS:8000"]
"""),
        PRE("""
$COMPOSE up -d api
sudo firewall-cmd --add-port=8000/tcp --permanent && sudo firewall-cmd --reload
"""),
        doc.p("%s 를 바꾸면 컨테이너를 다시 만들어야 적용됩니다(위 %s). %s 는 브라우저로 콘솔을 여는 경우에 "
              "필요하고, 서버 간 호출(KL 포털)에는 영향이 없습니다."
              % (C("API_BIND"), C("up -d api"), C("CORS_ALLOW_ORIGINS"))),
        doc.h3("방식 B"),
        PRE("""
infra-config/mtls/certs/server.crt
infra-config/mtls/certs/server.key
infra-config/mtls/certs/ca.crt
"""),
        doc.p("위 세 파일을 배치하고 %s 의 %s 를 실제 도메인으로 바꾼 뒤 기동합니다." % (C("infra-config/mtls/nginx.mtls.conf"), C("server_name"))),
        PRE("$COMPOSE --profile mtls up -d nginx-mtls"),
        doc.p("연동 측에는 클라이언트 인증서와 %s 를 매체로 미리 배포합니다. 검증은 다음 명령으로 합니다." % C("ca.crt")),
        PRE("curl -v https://SERVER_DOMAIN --cacert ca.crt --cert client.crt --key client.key"),
        doc.act("미확인", "API_BIND 변경 뒤 외부 호스트에서의 접속과 mTLS 종단은 실제 서버에서 시험하지 "
                        "않아 확인하지 못하였습니다"),
        doc.p("KL 포털이 호출할 값은 다음과 같습니다: 호출 주소 %s · 인증 헤더 %s · API 키는 번들 폴더 %s 의 %s 줄입니다. "
              "키를 화면에 붙여넣는 곳은 없습니다."
              % (C("http://%s:8000/api/v1" % "<서버주소>"), C("X-API-Key"), C(".env"), C("API_KEY="))),

        doc.h2(6, ".env 항목"),
        doc.p("%s 는 권한 600 으로 생성되며 자동 생성된 비밀값이 들어 있습니다. 백업하되 외부에 공유하지 않습니다." % C(".env")),
        env_table,
        doc.act("유의", "STORAGE_ENCRYPTION_KEY 와 KOIPA_AUDIT_CHAIN_SECRET 은 분실하면 복구할 수 없으므로 "
                        ".env 를 별도 매체에 백업하도록 유의하여 주시기 바랍니다"),

        doc.h2(7, "재설치 · 업그레이드 · 제거"),
        doc.h3("재실행"),
        doc.p("%s 는 다시 실행해도 안전하도록 만들어져 있습니다. 기존 %s 를 보존하고, 이미 적재된 검수 후보와 검수 원장은 "
              "덮어쓰지 않으며, 콘솔 토큰은 새로 발급합니다. 데이터(DB · 저장소 · 검수 원장)는 볼륨에 있어 그대로 유지됩니다."
              % (C("setup.sh"), C(".env"))),
        doc.p("번들 20260929 의 %s 는 스택이 떠 있으면 포트 사용 중으로 차단합니다. 이 번들에서 재실행하려면 먼저 "
              "%s 로 멈춘 뒤 실행합니다. 개발 소스에는 이 판정이 고쳐져 있으나 이 번들에는 들어 있지 않으며, 고친 소스를 "
              "읽어 확인했을 뿐 실행 시험은 하지 못하였습니다." % (C("preflight_host.sh"), C("$COMPOSE stop"))),
        doc.h3("새 번들로 업그레이드"),
        doc.table(
            ["순서", "작업", "비고"],
            [["1", "백업합니다", "운영 매뉴얼 7절(DB 덤프 · 볼륨 · .env)"],
             ["2", "새 번들을 새 폴더에 반입하고 verify.sh 를 실행합니다", "기존 번들 폴더는 그대로 둡니다"],
             ["3", "기존 .env 를 새 폴더로 복사하고 IMAGE_TAG 를 새 번들의 manifest.yaml version 으로 바꿉니다",
              "기존 .env 가 있으면 setup.sh 4단계가 .env 를 보존하므로 IMAGE_TAG 가 갱신되지 않아 옛 이미지를 계속 가리킵니다"],
             ["4", "새 폴더에서 " + C("sudo NODE=… bash setup.sh"), "이미지 적재 · 마이그레이션이 자동으로 진행됩니다"],
             ["5", "설치 확인(4절)과 인수 샘플을 다시 실행합니다", ""]],
        ),
        doc.act("미확인", "새 번들로의 업그레이드와 마이그레이션 뒤 이전 버전 복귀는 실행해 보지 않아 "
                        "확인하지 못하였습니다"),
        doc.h3("중지와 제거"),
        PRE("""
$COMPOSE stop            # stop apps, keep data
$COMPOSE down            # remove containers, keep volumes
$COMPOSE down -v         # remove containers AND volumes (data deleted)
"""),
        doc.p("%s 는 DB · 저장소 · 검수 원장을 모두 삭제합니다. 백업을 확인한 뒤에만 실행합니다." % C("down -v")),
        doc.act("유의", "FORCE_ENV=1 은 비밀값을 새로 만들어 기존 볼륨의 DB 비밀번호와 암호화 키가 어긋나므로 "
                        "신규 설치가 아닌 서버에서는 쓰지 않도록 유의하여 주시기 바랍니다"),

        doc.h2(8, "문제 해결"),
        trouble,
        doc.p("운영 중 장애는 운영 매뉴얼 11절을 봅니다. 해결되지 않으면 %s 와 %s 출력을 첨부해 에스컬레이션합니다."
              % (C("$COMPOSE logs api worker beat"), C("/api/v1/healthz/deep"))),
    ]
    return assemble("설치 매뉴얼", "인수인계 · 설치", "설치 매뉴얼",
                    "번들 %s · Rocky Linux 8.10 · %s" % (BUNDLE_VER, DATE), *body,
                    foot_left="근거: scripts/setup.sh · preflight_host.sh · deploy_airgap.sh · verify_install.sh · docker-compose.airgap.yml · infra/mtls/nginx.mtls.conf")


# ═════════════════════════════════════════════════════════════════════════
# 02 운영 매뉴얼
# ═════════════════════════════════════════════════════════════════════════


def build_02() -> str:
    beat = doc.table(
        ["작업", "주기", "역할"],
        [["active_learning_tick(auto)", "30분", "교정 누적을 점검합니다. 지재원 노드는 조건이 되면 학습을 대기열에 넣습니다(학습 매뉴얼 4절)"],
         ["active_learning_tick(snapshot)", "매일 03:00", "학습 필요 상태를 기록합니다"],
         ["drift_tick", "15분", "운영 임베딩 drift 를 점검합니다"],
         ["auto_rollback_tick", "60분", "활성 모델의 미탐 회귀를 점검합니다. 실제 롤백은 auto_rollback_enabled 일 때만 합니다"],
         ["deliver_outbox_tick", "60초", "KL 웹훅 콜백을 송신합니다(실패 시 지수 백오프 후 DLQ)"],
         ["beat_heartbeat_tick", "60초", "beat 동작 표시"],
         ["ensure_partitions_tick", "매일 02:10", "향후 3개월 월 파티션을 보장합니다"],
         ["retention_purge_tick", "매일 02:40", "보존기간이 지난 운영 로그를 지웁니다. retention_enabled 기본값이 False 라 기본은 삭제하지 않습니다"],
         ["verify_audit_chain_tick", "매일 03:30", "감사체인 무결성을 검증합니다. 파손이 있으면 P0 알림입니다"],
         ["nightly_incremental_retrain_tick", "기본 미등록", "enable_nightly_retrain_schedule 를 켠 경우에만 매일 02:00 에 등록됩니다"]],
    )
    cards = doc.table(
        ["카드", "용도"],
        [["공통 연결 · 인증", "API 주소와 인증 설정"],
         ["1 분류 실행", "문서를 올려 분류합니다"],
         ["2 문서 검토 대기 · 등급 확정/수정", "검수 대기열에서 등급을 확정하거나 교정합니다"],
         ["2 확정 대기", "자동 확정된 문서의 최종 확정"],
         ["조회 검증문서 현황", "검증 문서의 구성과 서명 현황"],
         ["데이터 학습 후보 생성 · 검토 → 학습셋 편입", "합성 문서 생성과 검토(FUN-003). LLM 공급자가 있어야 동작합니다"],
         ["3 재학습 트리거", "학습을 시작합니다(학습 매뉴얼 4절)"],
         ["4 모델 서빙 · 배포 게이트 · 핫리로드", "모델 활성화 · 롤백 · 재적재(학습 매뉴얼 6절)"],
         ["5 모델 메트릭 · FNR 게이트", "모델 지표와 미탐 게이트 상태"],
         ["조회 운영 관제 대시보드", "검수 대기 건수 · 킬게이트 상태 등 운영 지표"],
         ["조회 감사 로그", "누가 · 언제 · 무엇을 했는지"],
         ["G2 검증문서 검수 준비", "검수 준비 상태"],
         ["설정 등급체계 관리", "등급 추가 · 변경 · 삭제"],
         ["설정 라벨링 · 태깅 규칙 관리", "키워드 규칙 관리"],
         ["설정 사내 규정(참고 표시)", "규정 등록 · 활성화 · 켬/끔 체크박스"]],
    )
    trouble = doc.table(
        ["증상", "진단", "조치"],
        [["/healthz/ready 503 · 분류가 100초가량 멈춤", C("$COMPOSE ps postgres") + " · " + C("$COMPOSE exec postgres pg_isready -U koipa"),
          "postgres 를 " + C("$COMPOSE up -d postgres") + " 로 재기동합니다. 무응답이면 DATABASE_URL 에 connect_timeout=3 을 붙여 빨리 실패하게 합니다"],
         ["redis 연결 실패 경고", "로그의 'in-memory 폴백'", "redis 를 기동합니다. 다중 워커(WEB_CONCURRENCY>1)에서 redis 가 없으면 기동이 거부됩니다"],
         ["기동 시 모델 경로 오류", "/healthz/deep 의 model 프로브 · " + C("ls models/classifier-trained"), "models/classifier-trained 에 가중치를 두고 CLASSIFIER_MODEL_DIR 와 일치시킵니다"],
         ["confidence 가 1.0 에 몰림", "모델 폴더의 temperature.json 유무", "temperature.json 을 동봉합니다. 프로파일 값(2.03)이 우선하는 점에 유의합니다(학습 매뉴얼 6-4)"],
         ["검색 품질 급락 · 임베더 경고", "로그의 HashEmbedding", "models/hf 에 KURE-v1 캐시가 있는지 확인합니다. 폐쇄망은 HF_HUB_OFFLINE=1 이라 없으면 기동이 실패합니다"],
         ["LLM 기능이 스텁 응답", "LLM_PROVIDER · LOCAL_LLM_BASE_URL", "기본은 noop 입니다. LLM 서버를 준비하고 .env 를 설정합니다"],
         ["모델 활성화가 차단됨", "응답의 gate · locked_readiness", "학습 매뉴얼 6절"],
         ["감사체인 파손 알림", C("verify_audit_chain_tick") + " 수동 실행", "파손 구간을 특정하고 키 회전 이력과 백업을 대조합니다. 데이터 무결성 사건으로 보안 절차에 따라 보고합니다"],
         ["비동기 작업이 진행되지 않음", "worker 큐 구독 · beat 기동", "worker 가 전 큐를 구독하고 beat 가 정확히 1개인지 확인합니다"],
         ["알림이 발화하지 않음", "관측성 스택 기동 여부", "8절로 기동하고 http://127.0.0.1:9090/alerts 에서 규칙 로드를 확인합니다"]],
    )

    body = [
        info("운영 담당자와 관리자입니다",
             "번들의 OPERATION.md · TROUBLESHOOTING.md 를 소스(celery_app.py · config.py · api/*.py · docker-compose.airgap.yml)와 "
             "대조하였고, 백업·복구 명령은 일회용 컨테이너에서 실행해 확인하였습니다",
             "스택이 떠 있는 서버에서 각 절차를 실행한 결과가 아닙니다. 확인 범위는 절마다 밝혔습니다"),

        doc.h2(1, "운영 개요"),
        doc.p("설치가 끝난 시스템의 일상 운영(기동 · 정지 · 계정 · 검수 · 백업 · 모니터링 · 장애 대응)을 다룹니다. "
              "모든 명령은 번들 폴더에서 실행하며 다음 별칭을 먼저 정합니다."),
        PRE(COMPOSE_ALIAS_PRE),
        doc.p("API 경로는 모두 %s 접두사를 가집니다." % C("/api/v1")),
        doc.act("권고", "매일 준비도(/healthz/ready)와 컨테이너 상태를, 매주 백업 파일 생성 여부를 확인하도록 "
                        "권고드립니다"),

        doc.h2(2, "서비스와 데이터 볼륨"),
        doc.table(
            ["볼륨(호스트 이름)", "컨테이너 경로", "내용", "백업"],
            [[C("koipa-airgap_pgdata"), C("/var/lib/postgresql/data"), "DB · 벡터 색인 · 감사 로그", "필수(DB 덤프)"],
             [C("koipa-airgap_storagedata"), C("/app/.storage"), "업로드 원본(AES-256-GCM 암호화 저장)", "필수"],
             [C("koipa-airgap_golden_data"), C("/app/datasets"), "검수 후보 · 검수 원장 · 사람 서명 · 콘솔 JWT 검증키 · 학습셋", "필수"],
             [C("koipa-airgap_artifacts_out"), C("/app/artifacts_out"), "재학습으로 만든 모델 산출물", "권고"],
             [C("koipa-airgap_redisdata"), C("/data"), "작업 대기열 · 작업 상태 · 규정 켬/끔 설정", "선택"]],
        ),
        doc.p("모든 서비스는 %s 로 설정되어 있어 서버 재부팅 뒤 docker 가 살아 있으면 자동으로 올라옵니다. "
              "docker 서비스는 %s 로 켜 둡니다." % (C("restart: unless-stopped"), C("sudo systemctl enable docker"))),

        doc.h2(3, "기동 · 정지 · 상태 · 로그"),
        PRE("""
$COMPOSE up -d postgres redis      # dependencies first
$COMPOSE up -d api worker beat     # apps
$COMPOSE ps                        # status
$COMPOSE stop api worker beat      # stop apps only (data kept)
$COMPOSE logs -f --tail=100 api worker
"""),
        doc.table(
            ["헬스 확인", "명령", "의미"],
            [["live", C("curl -s http://127.0.0.1:8000/api/v1/healthz/live"), "프로세스 생존(컨테이너 헬스체크가 씁니다)"],
             ["ready", C("curl -s http://127.0.0.1:8000/api/v1/healthz/ready"), "의존성 실측. 200 이면 준비, 503 이면 미준비"],
             ["deep", C("curl -s http://127.0.0.1:8000/api/v1/healthz/deep"), "파서 · 모델 · 임베더 · 벡터 저장소 프로브"],
             ["인프라 일괄", C("$COMPOSE exec api python scripts/verify_infra.py"), "인프라 점검 스크립트"]],
        ),
        doc.p("%s 는 정확히 1개만 기동합니다. beat 가 없으면 정기 작업(학습 점검 · drift · 아웃박스 · 파티션 · 감사체인 검증)이 "
              "모두 멈춥니다. %s 는 %s 전 큐를 구독해야 합니다." % (C("beat"), C("worker"), C("classify,index,synthesis,learning,celery"))),

        doc.h2(4, "계정 · 권한 · 토큰"),
        doc.table(
            ["역할", "누가 씁니까", "할 수 있는 일"],
            [[C("admin"), "관리자", "관리 기능 전체(모델 활성화 · 롤백 · 규정 켬/끔 · 재학습 등)"],
             [C("reviewer"), "검수자(전문가)", "검수 화면에서 결정 저장. 지재원 노드에서는 제안 등급이 가려집니다"],
             [C("kl_backend"), "KL 백엔드", "학습 시작과 규정 켬/끔 등 관리 API 일부"],
             [C("system"), "공유 API 키(X-API-Key)의 주체", "API 호출. 결정 확정(POST /confirm)은 거부됩니다(403)"]],
        ),
        doc.src("근거: schemas/common.py Actor 역할 패턴 · api/training.py · api/admin.py · e2e 시험 기록(2026-09-27)"),
        doc.table(
            ["항목", "내용"],
            [["자동 로그인", "설치 7단계가 토큰을 만들어 로그인 화면에 미리 채웁니다. 주소만 열면 로그인됩니다"],
             ["토큰 파일", C("console_admin_token.txt") + " (관리자) · " + C("console_reviewer_token.txt") + " (지재원 노드 검수자). 번들 폴더 · 권한 600"],
             ["유효기간", "365일(CONSOLE_TOKEN_DAYS). 만료되면 로그인 화면이 토큰 입력 칸으로 돌아옵니다"],
             ["갱신", C("sudo bash setup.sh") + " 를 다시 실행하면 새로 발급하고 .env 를 갱신합니다. 데이터는 유지됩니다"],
             ["끄기", C("CONSOLE_AUTOLOGIN=0 sudo bash setup.sh")]],
        ),
        doc.p("관리자 API 를 스크립트로 부를 때는 토큰을 화면에 출력하지 않고 파일에서 읽어 변수로만 씁니다."),
        PRE("""
ADMIN_TOKEN="$(cat console_admin_token.txt)"
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \\
  http://127.0.0.1:8000/api/v1/admin/dashboard
"""),
        doc.act("유의", "토큰을 손으로 새로 발급하면 콘솔 검증키가 통째로 교체되어 이미 나눠 준 토큰과 자동 로그인 "
                        "토큰이 모두 무효가 되므로, 손으로 발급한 뒤에는 반드시 setup.sh 를 다시 실행하도록 "
                        "유의하여 주시기 바랍니다"),

        doc.h2(5, "관리 콘솔"),
        doc.p("관리 콘솔 주소는 두 노드 모두 %s 입니다(setup.sh 완료 안내). 지재원 노드의 자동 로그인 계정은 reviewer "
              "전용이라 관리자 기능에는 관리자 토큰이 필요합니다. 화면 카드는 다음과 같고 화면별 사용법은 관리자 콘솔 "
              "사용 설명서를 봅니다." % C("/console/admin.html")),
        cards,
        doc.act("미확인", "지재원 노드에서 관리자 토큰으로 콘솔에 로그인하는 화면 절차는 이번에 실행해 "
                        "확인하지 못하였습니다"),

        doc.h2(6, "검수와 교정"),
        doc.table(
            ["구분", "지재원 노드(전문가 검수)", "고객사 노드(운영 검수)"],
            [["대상", "번들의 검수 문서 1,711건", "운영 중 자동 확정되지 않은 문서"],
             ["화면", C("/api/v1/golden/candidates/manage.html"), "관리 콘솔 카드 2"],
             ["로그인", C("expert-01") + " 자동 로그인(reviewer)", "admin 자동 로그인"],
             ["결정 방식", "검수자가 등급을 결정해 저장하고 원장에 남습니다. 제안 등급과 근거는 가려집니다(블라인드)", "대기열에서 확정 또는 교정합니다"],
             ["API", "골든 후보 API", C("GET /review-queue") + " · " + C("POST /confirm") + " · " + C("POST /relabel")],
             ["그 다음", "평가 정답 승격(학습에 쓰지 않은 문서만 대상)", "교정이 쌓이면 재학습 데이터가 됩니다(학습 매뉴얼)"]],
        ),
        doc.p("검수 결과를 CSV 로 반입하는 스크립트(%s)는 검수자 식별이 %s · %s · 공란 등이면 반입을 거부합니다. "
              "사람의 서명만 인정합니다. 옵션 %s 는 회원사 반입을 후보로만 격리해 평가 오염을 막습니다."
              % (C("scripts/import_review_corrections.py"), C("ai_assist"), C("llm_*"), C("--as-candidate"))),
        doc.act("유의", "AI 나 LLM 의 판정을 사람 검수 결과로 반입하면 평가 정답이 오염되므로 검수자 "
                        "식별은 실제 계정으로 기록하도록 유의하여 주시기 바랍니다"),

        doc.h2(7, "백업 · 복구"),
        doc.p("번들 루트에는 백업 스크립트가 없습니다(루트 스크립트는 setup · preflight · verify · install · deploy · "
              "deploy_airgap · deploy_rollback · db_probe · verify_install 입니다). 아래 명령을 씁니다. "
              "pg_dump · pg_restore · tar 명령은 일회용 pgvector 컨테이너와 일회용 볼륨에서 실행해 백업과 복원이 되는 것을 "
              "확인하였습니다. " + C("$COMPOSE exec") + " 로 감싼 형태는 실행해 보지 않았습니다."),
        doc.table(
            ["대상", "방법"],
            [["DB", C("pg_dump") + " 사용자 정의 형식(-Fc)"],
             ["저장소 · 검수 원장 · 산출 모델", "볼륨을 tar 로 묶기(api 이미지의 tar 사용)"],
             [".env · 토큰 파일", "번들 폴더의 파일을 별도 매체에 복사(권한 600 유지)"]],
        ),
        doc.h3("7-1 DB 백업"),
        PRE("""
mkdir -p backup
$COMPOSE exec -T postgres pg_dump -U koipa -Fc koipa \\
  > backup/koipa-$(date +%Y%m%d-%H%M%S).dump
"""),
        doc.h3("7-2 볼륨 백업"),
        PRE("""
IMAGE_TAG="$(sed -n 's/^IMAGE_TAG=//p' .env)"
for v in storagedata golden_data artifacts_out; do
  docker run --rm --user 0 -v koipa-airgap_$v:/data:ro -v "$PWD/backup":/out:z \\
    --entrypoint tar koipa-api:$IMAGE_TAG czf /out/$v-$(date +%Y%m%d).tgz -C /data .
done
"""),
        doc.h3("7-3 복구"),
        PRE("""
$COMPOSE stop api worker beat
$COMPOSE exec -T postgres pg_restore -U koipa -d koipa --clean --if-exists \\
  < backup/koipa-YYYYMMDD-HHMMSS.dump
docker run --rm --user 0 -v koipa-airgap_storagedata:/data -v "$PWD/backup":/in:ro,z \\
  --entrypoint tar koipa-api:$IMAGE_TAG xzf /in/storagedata-YYYYMMDD.tgz -C /data
$COMPOSE up -d api worker beat
"""),
        doc.p("볼륨 복원 시 파일 소유자(uid 1000)는 유지됩니다. 복구 뒤 마이그레이션 상태는 %s 로 확인합니다."
              % C("$COMPOSE run --rm api alembic current")),
        doc.act("유의", "PostgreSQL 파티션 스키마에서 pg_restore --clean 은 자식 파티션의 상속 PK 삭제에 실패하며 "
                        "종료 코드 1 을 내는 경우가 있으나, 오류가 이 종류뿐이면 복원은 성공한 것이므로 오류 "
                        "내용을 읽고 판단하도록 유의하여 주시기 바랍니다"),
        doc.act("미확인", "실제 운영 DB 규모에서의 백업 소요 시간과 목표 복구 시간(RTO)은 측정하지 않아 "
                        "확인하지 못하였습니다"),

        doc.h2(8, "모니터링 · 알림"),
        doc.p("안전 신호(미탐 급증 · 감사체인 파손 · 킬게이트 발동 · rule-fallback 서빙 등)는 관측성 스택이 떠 있어야 "
              "소비됩니다. 설치기(setup.sh)는 관측성 스택을 기동하지 않으므로 별도로 올립니다."),
        PRE("""
echo "GRAFANA_PASSWORD=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \\n')" >> .env
export OBS="docker compose --env-file $PWD/.env \\
  -f observability/docker-compose.observability.airgap.yml"
$OBS up -d
$OBS ps
curl -s http://127.0.0.1:9090/-/ready
"""),
        doc.table(
            ["대상", "주소(서버 자신에서)", "비고"],
            [["Prometheus 알림 규칙", C("http://127.0.0.1:9090/alerts"), "규칙이 로드되어 있어야 합니다"],
             ["Grafana", C("http://127.0.0.1:3000"), "GRAFANA_PASSWORD 가 .env 에 있어야 기동합니다"],
             ["Alertmanager", C("http://127.0.0.1:9093"), "알림 통지 채널은 기본 미설정입니다. observability/alertmanager.yml 의 receivers 에 SMTP 나 웹훅을 추가합니다"],
             ["앱 지표", C("curl -s http://127.0.0.1:8000/api/v1/metrics-prom"), "관측성 스택이 스크랩합니다"]],
        ),
        doc.p("관측성 포트는 모두 127.0.0.1 에만 열립니다. 메인 스택이 먼저 떠 있어 %s 네트워크가 있어야 합니다."
              % C("koipa-airgap_default")),
        doc.act("미확인", "알림 통지 채널(SMTP · 웹훅)을 연결한 상태의 실제 통지는 시험하지 않아 확인하지 못하였습니다"),

        doc.h2(9, "정기 작업"),
        doc.p("beat 가 발행하는 정기 작업입니다. 시각은 Asia/Seoul 기준입니다."),
        beat,
        doc.src("근거: src/koipa/workers/celery_app.py 의 beat_schedule · config.py"),

        doc.h2(10, "시연 · 리허설 데이터 정리"),
        doc.p("시연 스크립트나 콘솔의 분류 화면으로 만든 데이터는 문서 소유자 %s 로 식별됩니다. 고객사 · 지재원 배포에서는 "
              "삭제 API(%s)가 404 이므로 자동으로 지워지지 않습니다." % (C("creatr_id='demo-console'"), C("POST /admin/demo/purge"))),
        doc.p("방치하면 안 되는 이유가 있습니다. 시연의 상향 교정(S1 → TS)은 미탐 방향 교정으로 집계되고, 누적이 10건에 "
              "이르면 지재원 노드의 30분 점검이 재학습을 자동으로 대기열에 넣습니다. 가짜 교정이 실제 재학습을 "
              "일으킬 수 있습니다."),
        PRE("""
$COMPOSE exec postgres psql -U koipa -d koipa \\
  -c "SELECT count(*) FROM tad_dm_doc_mng WHERE creatr_id='demo-console';"
"""),
        doc.p("건수가 예상 리허설 횟수보다 크게 많으면 실데이터가 오태깅된 신호이므로 삭제하지 않고 원인을 먼저 "
              "확인합니다. 정상이면 분류 결과 → 청크 → 문서 순서(외래키 순서)로 지웁니다."),
        PRE("""
$COMPOSE exec postgres psql -U koipa -d koipa <<'SQL'
BEGIN;
DELETE FROM tad_cm_clsf_rslt_mng WHERE doc_id IN
  (SELECT doc_id FROM tad_dm_doc_mng WHERE creatr_id='demo-console');
DELETE FROM tad_cm_chnk_mng WHERE doc_id IN
  (SELECT doc_id FROM tad_dm_doc_mng WHERE creatr_id='demo-console');
DELETE FROM tad_dm_doc_mng WHERE creatr_id='demo-console';
COMMIT;
SQL
"""),
        doc.p("배포 검증 스크립트(%s)가 남기는 문서는 %s 로 적재되므로 위 문장의 마커만 바꿔 같은 순서로 지웁니다."
              % (C("verify_deploy_live.sh"), C("creatr_id='verify'"))),
        doc.src("근거: 번들 docs/OPERATION.md 8절 · api 이미지의 scripts/ 구성. 위 SQL 은 이번에 실행해 보지 않았습니다"),

        doc.h2(11, "장애 대응"),
        trouble,
        doc.p("여기서 해결되지 않으면 %s 와 %s 출력을 첨부해 에스컬레이션합니다."
              % (C("$COMPOSE logs api worker beat"), C("/api/v1/healthz/deep"))),

        doc.h2(12, "보안 유의"),
        doc.table(
            ["항목", "내용"],
            [["네트워크", "API · DB · redis · 관측성은 기본으로 127.0.0.1 에만 열려 있습니다. 외부 노출은 설치 매뉴얼 5절의 결정입니다"],
             ["자동 로그인", "콘솔 주소에 닿는 누구나 해당 권한을 얻습니다. 폐쇄망 · 방화벽 · VPN 으로 접속원을 제한합니다"],
             ["비밀 파일", ".env · console_*_token.txt 는 권한 600 이며 외부에 공유하지 않습니다"],
             ["감사", "모든 쓰기 요청은 감사 로그에 남고, 감사체인은 매일 03:30 에 검증됩니다"],
             ["키 보관", "STORAGE_ENCRYPTION_KEY · KOIPA_AUDIT_CHAIN_SECRET 은 분실하면 복구할 수 없습니다. 별도 보관합니다"]],
        ),
    ]
    return assemble("운영 매뉴얼", "인수인계 · 운영", "운영 매뉴얼",
                    "Day-2 운영 · 고객사 · 지재원 노드 공통 · " + DATE, *body,
                    foot_left="근거: docs/OPERATION.md · TROUBLESHOOTING.md · workers/celery_app.py · config.py · docker-compose.airgap.yml · 일회용 컨테이너 시험")


# ═════════════════════════════════════════════════════════════════════════
# 03 학습·재학습 매뉴얼
# ═════════════════════════════════════════════════════════════════════════


def build_03() -> str:
    flow = chart.steps([
        ("1", "교정 수집", "검수자가 바로잡은 등급이 교정으로 쌓입니다", "POST /relabel", ""),
        ("2", "학습셋 병합", "교정을 기존 학습셋에 합칩니다. 평가용 문서와 겹치는 행은 뺍니다", "누수 가드", ""),
        ("3", "학습", "kf-deberta-base 에서 시작해 분류기를 학습합니다", "POST /train", ""),
        ("4", "등록 · 게이트", "모델 버전으로 등록하고 배포 게이트를 판정합니다. 자동 활성화는 하지 않습니다", "기본: 등록만", ""),
        ("5", "수동 활성화", "관리자가 게이트를 거쳐 활성화하고 서빙에 핫리로드합니다", "POST /admin/model/activate", ""),
        ("6", "롤백", "문제가 있으면 직전 활성 모델로 되돌립니다", "POST /admin/model/rollback", ""),
    ], title="재학습 흐름")

    body = [
        info("모델 담당자입니다",
             "src/koipa/workers/tasks.py · services/training_service.py · api/training.py · api/admin.py · modules/m4_training/trainer.py · "
             "modules/m6_evaluation/deploy_gate.py · config.py 를 읽었고, 학습 실행은 번들의 워커 이미지로 직접 실행해 확인하였습니다",
             "학습셋 복사 · 기본 경로 해석 · 소량 학습(32행 · 1에폭)은 확인하였습니다. 스택이 떠 있는 상태에서 POST /train 이 등록과 게이트까지 완주하는 것은 확인하지 못하였습니다"),

        doc.h2(1, "개요"),
        doc.p("학습 대상은 4등급(TS · S1 · S2 · S3) 분류기입니다. 시작 모델은 %s 이고 현재 서빙 모델은 %s 입니다. "
              "재학습은 사람이 바로잡은 판정(교정)을 학습 데이터에 더해 새 모델을 만드는 절차이며, 새 모델은 후보로만 "
              "등록되고 사람이 활성화해야 서빙에 쓰입니다." % (C("kakaobank/kf-deberta-base"), C("v-fe4b386b"))),
        doc.fig(flow, "1~2번은 코드가 자동으로 수행하고, 3번은 콘솔 · API · 자동 점검 중 하나로 시작합니다. 4번까지는 서빙에 "
                      "영향이 없고 5번에서 서빙 모델이 바뀝니다."),
        doc.table(
            ["기준값", "값", "근거"],
            [["시작 모델", C("kakaobank/kf-deberta-base") + " (번들 HF 캐시에 동봉)", "trainer.py DEFAULT_BASE_MODEL"],
             ["기본 학습 조건", "배치 8 · 5에폭 · lr 2e-5 · 최대 길이 512 · 시드 42 · 등급 가중 사용", "trainer.py TrainSpec"],
             ["기본 학습셋", C("datasets/labeled_p1_v5_clean/{train,val,test}.jsonl") + " (2,042 · 256 · 256행)", "config.py training_dataset_dir"],
             ["학습셋 등급 분포(train)", "S3 813 · S2 463 · TS 401 · S1 365", "train.jsonl 직접 집계"],
             ["산출 위치", C("artifacts_out/classifier/<모델버전>/"), "config.py training_output_dir"]],
        ),

        doc.h2(2, "노드별 차이"),
        doc.table(
            ["항목", "지재원 노드(full-train)", "고객사 노드(onprem-local)"],
            [["학습 API · 콘솔 카드 3", "사용 가능", "사용 가능"],
             ["자동 시작", "30분마다 교정 누적을 점검하고, 미탐 방향 교정이 기본 임계 10건 이상이면 학습을 대기열에 넣습니다(URGENT). 주간 권고 상태이면 월요일 03~05시에 넣습니다. 이 시각은 코드가 UTC 로 판정하므로 한국 시각으로는 12~14시입니다",
              "없음. 야간 스케줄(매일 02:00)은 기본 꺼져 있습니다"],
             ["수동 활성화 조건", "회귀 게이트", "회귀 게이트 + 평가 정답 준비도(locked eval). 우회는 force + 사유이며 감사에 남습니다"],
             ["자동 활성화", "꺼짐(retrain_auto_activate=False)", "꺼짐"],
             ["GPU", "gpu.yml 오버레이는 장치 예약만 합니다. 실제 GPU 학습은 CUDA 이미지가 별도로 필요합니다(10절)", "불요"]],
        ),
        doc.src("근거: config.py 프로파일 정의 · workers/tasks.py active_learning_tick · celery_app.py"),

        doc.h2(3, "사전 준비"),
        doc.h3("3-1 학습셋을 볼륨에 넣기"),
        doc.p("번들에는 학습셋이 없고 컨테이너의 %s 는 %s 볼륨입니다. 학습셋을 그 볼륨의 %s 에 넣어야 기본 경로가 "
              "성립합니다. 준비하지 않으면 학습이 시작 직후 %s 로 실패하고 학습 작업 목록의 오류 칸에 그 사유가 남습니다."
              % (C("/app/datasets"), C("golden_data"), C("labeled_p1_v5_clean/"), C("FileNotFoundError"))),
        doc.p("학습셋 폴더(예: %s)를 번들 폴더 옆에 둔 뒤 다음을 실행합니다. 검수 문서 적재와 같은 방식이며 기존 파일은 "
              "덮어쓰지 않습니다." % C("./training_set/")),
        PRE("""
$COMPOSE run --rm --no-deps -T --entrypoint sh \\
  -v "$PWD/training_set":/incoming:ro,z api -c '
    d=/app/datasets/labeled_p1_v5_clean
    mkdir -p "$d"
    cp -n /incoming/train.jsonl /incoming/val.jsonl /incoming/test.jsonl "$d"/
    ls -ln "$d"'
"""),
        doc.act("보고", "이 복사 절차를 번들 워커 이미지와 새 볼륨에서 실행해 파일 소유자가 uid 1000 으로 "
                        "만들어지고, 기본 경로 3개가 2,042 · 256 · 256행으로 해석되는 것을 확인하였습니다"),
        doc.p("학습 행의 형식은 한 줄에 JSON 하나이며 %s 와 %s 필드를 가집니다(등급은 TS · S1 · S2 · S3). 학습셋의 출처와 "
              "전달 방식은 결정 사항이며 안내서 6절 2번에 적었습니다." % (C("text"), C("label"))),
        doc.h3("3-2 자원"),
        doc.table(
            ["항목", "내용"],
            [["스레드", C("WORKER_CPU_LIMIT") + " (기본 4)이 OMP_NUM_THREADS · MKL_NUM_THREADS 를 정합니다. 서버 코어를 더 쓰려면 .env 에 값을 올립니다"],
             ["메모리", "확인하지 못하였습니다. 4GiB 제한 환경에서 기본 조건 학습이 메모리 부족으로 종료된 이력이 config 주석에 있습니다(celery_app.py 194행 부근)"],
             ["디스크", "학습 1회마다 체크포인트 폴더가 남습니다. 소량 시험에서 산출 폴더에 checkpoint-N(optimizer.pt 포함)과 최종 모델 폴더가 함께 생겼습니다"],
             ["기반 모델", "번들 HF 캐시에서 오프라인으로 읽습니다. 소량 시험에서 인터넷 없이 로드되는 것을 확인하였습니다"]],
        ),

        doc.h2(4, "학습 실행"),
        doc.table(
            ["방식", "방법", "비고"],
            [["A 콘솔", "관리 콘솔 카드 3 「재학습 트리거」", "학습 조건 입력 없이 누르면 기본 조건으로 시작합니다"],
             ["B API", C("POST /api/v1/train"), "역할 admin 또는 kl_backend. 시간당 10회 제한. 202 와 train_job_id 를 돌려줍니다"],
             ["C 자동", "지재원 노드의 30분 점검", "조건은 2절. 원치 않으면 시연 · 리허설 데이터를 정리합니다(운영 매뉴얼 10절)"],
             ["D 야간 스케줄", "enable_nightly_retrain_schedule 를 켠 경우 매일 02:00", "기본 꺼짐. 기본 조건 학습은 CPU 에서 수 시간이라 업무 시간에 걸칠 수 있습니다"]],
        ),
        doc.h3("4-1 API 로 시작"),
        PRE("""
ADMIN_TOKEN="$(cat console_admin_token.txt)"
curl -s -X POST http://127.0.0.1:8000/api/v1/train \\
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \\
  -d '{"training_type":"full","hyperparams":{"epochs":3},
       "actor":{"user_id":"admin","role":"admin"}}'
"""),
        doc.p("%s 는 %s 에 그대로 전달됩니다. 따라서 %s · %s · %s · %s · %s 같은 필드를 지정할 수 있고, 비워 두면 "
              "기본 조건(5에폭)입니다. 요청의 %s 와 %s 는 감사 기록에만 남고 학습 동작을 바꾸지 않습니다."
              % (C("hyperparams"), C("TrainSpec(**hyperparams)"), C("epochs"), C("batch_size"), C("lr"), C("max_seq_len"), C("train_path"),
                 C("training_type"), C("base_model"))),
        doc.act("유의", "학습 조건은 hyperparams 로만 바뀌며 training_type 을 incremental 로 보내도 기본 경로는 "
                        "매번 kf-deberta-base 에서 처음부터 학습하므로 소요 시간이 전체 학습과 같음에 유의하여 "
                        "주시기 바랍니다"),
        doc.h3("4-2 진행 확인"),
        PRE("""
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \\
  "http://127.0.0.1:8000/api/v1/train/jobs?limit=5"
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \\
  http://127.0.0.1:8000/api/v1/train/jobs/TRAIN_JOB_ID
$COMPOSE logs -f --tail=50 worker
"""),
        doc.p("상태 값은 queued · preprocessing · training · evaluating · registering · done · failed 입니다. 진행률 · 현재 "
              "에폭 · 예상 완료 시각 · 완료 후 모델 버전 · 실패 사유(error)가 응답에 포함됩니다."),
        doc.h3("4-3 소요 시간"),
        doc.p("스텝 수는 %s 이며 기본값은 배치 8 · 5에폭입니다. 2,042행이면 1,280스텝입니다." % C("ceil(행수 / 배치) × 에폭")),
        doc.table(
            ["환경", "스텝당", "1,000행(625스텝)", "2,042행(1,280스텝)", "출처"],
            [["AVX-512 · 8스레드", "10.9~11.9초", "약 2시간", "약 4.5시간", "2026-08-30 개발 PC 실측"],
             ["AVX-512 · 4스레드", "15.4초", "약 2.7시간", "—", "같은 날 실측"],
             ["AVX 없음(가상화) · 4스레드", "37.24초", "약 6.5시간", "약 13시간", "2026-08-08 로그"],
             ["Docker Desktop · 4스레드(이 시험)", "—", "—", "—", "32행 · 1에폭 · 최대 길이 128 이 33초. 스텝당 시간은 별도로 재지 않았습니다"]],
        ),
        doc.p("위 수치는 학습 구간만이며 에폭별 평가와 체크포인트 저장은 빠져 있어 실제 소요는 더 깁니다. 인수 서버의 "
              "실제 값은 %s 로 30스텝만 재어 환산할 수 있으나 이 스크립트는 번들 이미지에 들어 있지 않습니다."
              % C("scripts/measure_train_step_rate.py")),
        doc.src("출처: 2026-08-30 · 2026-08-08 작업 기록과 celery_app.py 주석(2,042행 약 13시간). 측정 산출 파일은 현재 리포에 없어 이번에 재확인하지 못하였습니다"),
        doc.act("미확인", "인수 서버(고객사 · 지재원)의 CPU 로 학습했을 때의 실제 소요 시간은 측정하지 "
                        "못하여 확인하지 못하였습니다"),

        doc.h2(5, "학습 결과 읽기"),
        doc.p("학습이 끝나면 산출 폴더 %s 에 다음 파일이 생깁니다." % C("artifacts_out/classifier/v-xxxxxxxx/")),
        doc.table(
            ["파일", "내용"],
            [[C("model.safetensors · config.json · tokenizer*"), "모델 가중치와 설정(서빙이 읽는 것)"],
             [C("report.json"), "정확도 · macro F1 · 등급별 미탐율(fnr_by_grade) · 고등급 미탐율(fnr_high) · 혼동행렬 · 학습 조건"],
             [C("temperature.json"), "확신도 보정값. 학습 직후 자동 산출됩니다"],
             [C("val_logits.jsonl"), "검증 로짓"],
             [C("checkpoint-N/"), "학습 중 체크포인트(optimizer.pt 포함). 서빙에 필요하지 않습니다"]],
        ),
        doc.p("소량 시험 결과 보고서의 %s 는 %s · %s 는 %s 였고, 학습 조건에 시드 42 · lr 2e-5 · 등급 가중 사용이 기록되었습니다."
              % (C("artifact_status"), C("legacy_trained_model"), C("deployable"), C("true"))),
        doc.act("유의", "report.json 의 성능은 학습셋과 같은 세대의 홀드아웃(test.jsonl) 기준이므로 운영 성능으로 "
                        "인용하지 않도록 유의하여 주시기 바랍니다"),

        doc.h2(6, "등록 · 게이트 · 활성화 · 롤백"),
        doc.h3("6-1 등록과 게이트(자동)"),
        doc.p("학습이 끝나면 워커가 모델을 모델 버전으로 등록하고 배포 게이트를 판정합니다. 자동 활성화는 꺼져 있어 "
              "합격해도 등록으로 끝납니다. 등록 후에도 학습 작업 상태가 failed 로 남는 경우는 등록 과정이 예외로 종료된 "
              "때뿐이며, 원인은 워커 로그의 register_and_gate_model 오류입니다."),
        doc.table(
            ["게이트 검사", "기준", "근거"],
            [["고등급 미탐율(fnr_high)", "현재 활성 모델 대비 +0.02 이내", "deploy_gate.py DEFAULT_FNR_HIGH_TOLERANCE"],
             ["macro F1", "현재 활성 모델 대비 −0.05 이내", "DEFAULT_F1_DROP_TOLERANCE"],
             ["한 등급 쏠림", "혼동행렬 한 열이 99% 이상이면 거부", "DEFAULT_DEGENERATE_COL_SHARE"],
             ["지표 누락", "fnr_high 가 없으면 거부", "fail-closed"],
             ["최초 배포", "활성 모델이 없을 때 고등급 미탐율 0.10 이하", "config.py deploy_gate_first_deploy_fnr_high_max"],
             ["메타모픽 회귀", "순방향(고등급이 낮게 판정) 회귀가 하나라도 있으면 거부", "리포트가 설정된 경우"]],
        ),
        doc.h3("6-2 수동 활성화"),
        doc.p("관리 콘솔 카드 4 또는 API 로 합니다. 역할은 admin 입니다."),
        PRE("""
curl -s -X POST http://127.0.0.1:8000/api/v1/admin/model/activate \\
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \\
  -d '{"version_label":"v-xxxxxxxx"}'
"""),
        doc.p("응답의 %s · %s · %s · %s 로 결과를 읽습니다. 활성화되면 재기동 없이 서빙 모델이 바뀝니다(핫리로드). 게이트를 통과하지 "
              "못하면 %s 이고, 우회는 %s 와 %s 로만 가능하며 감사에 %s 로 남습니다."
              % (C("activated"), C("blocked"), C("gate"), C("locked_readiness"), C("blocked=true"), C("force=true"), C("reason"), C("forced=true"))),
        doc.p("고객사 노드는 회귀 게이트에 더해 평가 정답(locked eval)이 준비되어 있어야 합니다. 준비 상태는 %s 로 봅니다."
              % C("GET /api/v1/admin/locked-readiness")),
        doc.h3("6-3 롤백"),
        PRE("""
curl -s -X POST http://127.0.0.1:8000/api/v1/admin/model/rollback \\
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \\
  -d '{"reason":"describe the reason"}'
"""),
        doc.p("직전 활성 모델로 되돌리며 게이트는 없습니다(회귀를 되돌리는 방향이기 때문입니다). 사유(1~500자)는 필수이고 "
              "감사에 남습니다. 번들 루트의 %s 로 특정 버전으로 되돌릴 수도 있습니다. 이 스크립트의 기본 env 파일은 %s 이므로 "
              "%s 를 지정합니다." % (C("deploy_rollback.sh"), C(".env.cloud"), C("ENV_FILE=.env"))),
        doc.h3("6-4 온도 보정값 정합"),
        doc.p("프로파일의 %s(2.03)는 모델 폴더의 %s 보다 우선합니다(운영 절차서 기재). 새 모델을 활성화할 때는 "
              "그 모델의 %s 값을 확인하고 필요하면 .env 의 %s 를 맞춥니다. 맞추지 않으면 다른 값으로 보정된 모델도 계속 "
              "옛 값으로 서빙됩니다." % (C("classifier_temperature"), C("temperature.json"), C("temperature.json"), C("CLASSIFIER_TEMPERATURE"))),
        doc.act("미확인", "6-4 의 우선순위는 번들 OPERATION.md 의 기재를 따른 것이며 서빙 코드에서 직접 "
                        "확인하지 못하였습니다"),

        doc.h2(7, "지켜야 할 것"),
        doc.table(
            ["규칙", "이유"],
            [["학습에 쓴 문서를 평가 정답으로 승격하지 않습니다", "학습에 쓴 문제로 시험 보는 오염(train-on-test)입니다. 제외 목록(evidence/eval_independence_exclusions.jsonl, 1,180행)이 이미지에 들어 있어 승격 단계에서 자동으로 걸러집니다"],
             ["교정은 반영된 것만 소비됩니다", "학습에 실제로 편입된 교정만 소비 처리되고, 평가용 문서와 겹쳐 제외된 교정은 다음 학습에 다시 들어옵니다"],
             ["AI · LLM 판정을 사람 검수로 취급하지 않습니다", "반입 스크립트가 거부하며 평가 정답이 오염됩니다"],
             ["시연 · 리허설 교정을 정리합니다", "누적되면 지재원 노드의 자동 재학습을 일으킵니다"],
             ["학습 중에는 같은 워커의 다른 무거운 작업을 피합니다", "학습은 CPU 를 오래 점유하며 learning 큐를 공유하는 작업이 밀립니다"]],
        ),

        doc.h2(8, "이어 학습(warm-start)"),
        doc.p("기본 경로는 매번 시작 모델(kf-deberta-base)에서 학습합니다. 배포 모델에서 이어 학습하는 방법은 학습 조건의 "
              "%s 에 배포 모델 폴더를 지정하는 것입니다. 개발 PC 에서 2026-08-30 에 다음을 실측했습니다." % C("base_model")),
        doc.table(
            ["조건", "학습 데이터", "스텝", "시간", "예측이 바뀐 행"],
            [["배포 모델 그대로", "—", "—", "—", "기준"],
             ["교정만 이어 학습", "교정 100행 · 2에폭", "26", "9.9분", "5 / 256"],
             ["교정 + 기존 학습 300행 섞기", "400행 · 2에폭", "100", "24.7분", "0 / 256"]],
        ),
        doc.p("기존 데이터를 섞으면 홀드아웃 256행의 예측이 한 행도 바뀌지 않았습니다. 다만 이 실험의 교정 100행은 모델이 "
              "이미 아는 문서였기 때문에 새로운 것을 배우는지는 재지 못했습니다."),
        doc.act("유의", "이어 학습은 기본 경로에 배선되어 있지 않고 야간 · 자동 경로도 이를 쓰지 않으므로, "
                        "쓰려면 학습 요청의 hyperparams 에 base_model 과 낮은 epochs 를 직접 지정하도록 "
                        "유의하여 주시기 바랍니다"),
        doc.act("미확인", "이어 학습을 번들 설치본의 POST /train 으로 실행한 결과와, 위 표의 측정 산출 파일"
                        "(현재 리포에 없음)은 이번에 재확인하지 못하여 확인하지 못하였습니다"),

        doc.h2(9, "문제 해결"),
        doc.table(
            ["증상", "원인", "조치"],
            [["학습이 곧바로 failed. 오류에 FileNotFoundError 와 datasets 경로", "학습셋 미준비", "3-1 로 학습셋을 볼륨에 넣습니다"],
             ["failed. 오류에 read-only file system", "산출 경로에 쓸 수 없음", "artifacts_out 볼륨이 api · worker 에 rw 로 붙었는지 확인합니다"],
             ["skipped: training_disabled", "학습이 꺼진 프로파일", "프로파일이 onprem-local 또는 full-train 인지 확인합니다"],
             ["POST /train 이 404", "학습 라우터 미등록", "학습이 꺼진 프로파일입니다. DEPLOY_PROFILE 을 확인합니다"],
             ["POST /train 이 429", "시간당 10회 제한", "잠시 뒤 다시 시도합니다"],
             ["학습 중 프로세스가 갑자기 종료", "메모리 부족", "서버 메모리를 늘리거나 학습 조건의 배치와 최대 길이를 줄입니다"],
             ["로그에 mlflow 경고가 다수", "MLflow 서버가 없는 환경", "소량 시험에서 이 경고가 나왔으나 학습은 완주했습니다"],
             ["활성화가 blocked", "게이트 미통과 또는 평가 정답 미준비", "응답의 gate 와 locked_readiness 를 읽고 원인을 해소합니다"],
             ["활성화 뒤 서빙 모델이 안 바뀜", "reload 가 워커별", "POST /admin/model/reload 는 현재 워커만 새로고침합니다. 워커별로 호출하거나 롤링 재기동합니다"],
             ["reload 가 409", "등급 매핑 불일치", "모델의 config.id2label 이 현재 등급 레지스트리와 맞는지 확인합니다"]],
        ),

        doc.h2(10, "지재원 노드의 GPU 학습"),
        doc.p("번들의 워커 이미지는 CPU 용 torch 입니다. gpu.yml 오버레이만 얹으면 GPU 는 보이지만 연산은 CPU 로 돕니다. "
              "실제로 GPU 를 쓰려면 CUDA 용 torch 로 이미지를 따로 빌드하고 학습 전용 오버레이를 얹어야 합니다."),
        doc.table(
            ["순서", "작업", "비고"],
            [["1", "호스트에 NVIDIA Container Toolkit 을 설치하고 도커에 nvidia 런타임을 등록합니다", "docker run --gpus all … nvidia-smi 가 먼저 돼야 합니다"],
             ["2", "CUDA 이미지를 빌드합니다", "인터넷이 되는 빌드 환경에서 " + C("docker build -f Dockerfile.api.prod --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu130 -t koipa-gpu:cu130 .")],
             ["3", "docker-compose.gpu-train.yml 오버레이로 worker · beat 를 기동합니다", "소스 저장소 파일이며 번들에는 없습니다. 이미지가 있으면 setup.sh 가 이 오버레이를 우선합니다"]],
        ),
        doc.p("현재 락파일의 torch 버전에는 CUDA 판 cu126 · cu129 · cu130 만 존재합니다(gpu-train.yml 주석). 드라이버가 "
              "보고하는 CUDA 판에 맞춥니다."),
        doc.act("미확인", "번들 이미지 기준으로 GPU 학습을 실행해 보지 못하였고, 이 PC 의 시험은 CPU 로만 수행하여 "
                        "GPU 학습 속도는 확인하지 못하였습니다"),

        doc.h2(11, "소스 저장소가 있는 경우"),
        doc.p("소스 저장소를 함께 인수하면 번들 이미지에 없는 학습 도구를 쓸 수 있습니다. 예를 들어 %s 는 학습 데이터 "
              "구성을, %s 는 온도 보정을, %s 는 모델 소요 시간 측정을 맡습니다. 저장소 전달 경로와 범위는 "
              "정해지지 않았습니다." % (C("make p1-retrain-dataset"), C("make calibrate CAL_MODEL_DIR=<dir>"), C("scripts/measure_train_step_rate.py"))),
        doc.act("미확인", "소스 저장소의 전달 경로와 범위는 확인하지 못하였습니다"),
    ]
    return assemble("학습·재학습 매뉴얼", "인수인계 · 학습", "학습·재학습 매뉴얼",
                    "분류기 재학습 · 게이트 · 활성화 · 롤백 · " + DATE, *body,
                    foot_left="근거: workers/tasks.py · services/training_service.py · api/training.py · api/admin.py · trainer.py · deploy_gate.py · 워커 이미지 소량 학습 시험")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    docs = [("00_인수인계_안내서.html", build_00), ("01_설치_매뉴얼.html", build_01),
            ("02_운영_매뉴얼.html", build_02), ("03_학습_재학습_매뉴얼.html", build_03)]
    failed = False
    for name, fn in docs:
        html = fn()
        bad = doc.check(html)
        if bad:
            print("[막힘] %s: %s" % (name, bad))
            failed = True
            continue
        (OUT_DIR / name).write_text(html, encoding="utf-8", newline="\n")
        print("[만듦] %s (%d자)" % (name, len(html)))
        for w in doc.advise(html):
            print("   [알림] %s" % w)
    if LONG_PRE:
        print("[막힘] 코드 블록 줄이 너무 깁니다(인쇄에서 잘립니다):")
        for x in sorted(set(LONG_PRE)):
            print("   " + x)
        failed = True
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
