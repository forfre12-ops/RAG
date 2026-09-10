# -*- coding: utf-8 -*-
"""국정원 AI 보안대책 체크리스트(부록1) 제출본 HTML 생성.

왜 생성기인가(2026-09-08). 체크리스트는 57개 항목이고 근거는 코드에 있다. 손으로 적으면
코드가 바뀌어도 문서가 그대로 남는다 - 이 리포에서 이미 겪은 문제다(문서-코드 어긋남).
근거의 유무는 audit_nis_ai_security.py 가 실제로 파일을 열어 판정하고, 이 스크립트는
그 결과에 사람이 쓴 설명과 대안을 붙여 문서로 만든다.

    audit_nis_ai_security.py   근거가 있는가 (기계 판정)
    build_nis_checklist_doc.py 그것을 어떻게 설명하고 무엇이 부족한가 (사람 작성)

서식은 감리문서 정본(KL_질의사항_회신서.html)의 style·nav 를 그대로 읽어 쓴다.
자체 클래스를 만들지 않는다 - 2026-08-29 지시.

사용:
    python scripts/build_nis_checklist_doc.py
    python scripts/build_nis_checklist_doc.py --check   # 다시 생성해 현재 파일과 같은지만 본다
"""
from __future__ import annotations

import argparse
import html
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

try:
    from _cli_io import force_utf8_stdio
except ImportError:
    from scripts._cli_io import force_utf8_stdio

force_utf8_stdio()

from audit_nis_ai_security import audit  # noqa: E402

_POC = Path(__file__).resolve().parents[1]
_REPO = _POC.parent
_TEMPLATE = _REPO / "doc" / "result" / "KL_회신_2026-08-28" / "KL_질의사항_회신서.html"
_OUT = _REPO / "doc" / "result" / "KL_AI자료_2026-08_미첨부문서" / "AI시스템_보안대책_체크리스트.html"

# ── 항목별 설명과 대안 ───────────────────────────────────────────────────────
#
# evidence: 무엇으로 그 대책을 세웠는가 (사람이 읽는 한 문장)
# gap:      부족한 것이 있으면 무엇이 어떻게 부족한가
# remedy:   그 부족을 어떻게 메울 것인가 (구체적으로 - 이름만 적지 않는다)
NOTES: dict[str, dict] = {
    # [2026-09-08 보완] 종전 문안은 내보내는 방향(반출 허용목록)만 적었다. 그런데 이 항목은
    # 유형③(외부 인터넷 자료를 수집해 학습)의 중점 항목이기도 해서, 수집하는 자료의 출처를
    # 어떻게 확인하는지가 비어 있었다. 수집측 근거를 앞에 붙인다.
    "M01": {"evidence": "학습 · 평가에 쓰는 외부 수집 자료는 수집처 주소 · 라이선스 · 수집일을 기재한 출처 명세에 등재된 공개 자료만 사용합니다(수집원 8종, 공개 판례는 CC BY 4.0 공개 데이터셋). 적재분은 출처별 건수와 라이선스를 대조하여 확인하고, 학습 행에는 출처 구분을 기록하여 공개 실문서와 합성문서를 구분합니다. 외부 생성형 AI 로 데이터를 전송할 때는 출처가 확인된 구분만 허용하는 허용목록을 적용하며, 출처가 확인되지 않은 자료는 자동 차단하고 전송 대상에 하나라도 포함되면 해당 실행 전체를 외부 전송 없이 처리합니다."},
    # [2026-09-08 정정] 두 군데가 사실과 달랐다.
    # (1) "CI 가 잠금본으로만 설치" — CI 시험·빌드 잡 8개는 `pip install -e` 로 pyproject
    #     범위를 재해석해 설치한다. 잠금본으로만 설치하는 것은 CI 가 아니라 운영 이미지다.
    # (2) "컨테이너 이미지는 다이제스트로 고정" — 전칭이나, 납품하는 폐쇄망 배포 구성
    #     (docker-compose.airgap.yml)의 이미지 참조 6개는 전부 태그다. 고정된 것은
    #     Dockerfile 기반 이미지(python:3.11-slim)뿐이다.
    "M02": {"evidence": "의존 패키지는 잠금 파일(uv.lock)로 버전을 고정하며, 배포 이미지는 이 잠금 파일이 고정한 버전으로 설치합니다. CI 는 잠금 파일이 패키지 정의와 일치하는지 검사하고, 잠금 파일 기준 목록에 대해 알려진 취약점을 점검합니다. 컨테이너 기반 이미지는 태그가 아닌 다이제스트(SHA-256)로 고정합니다. 폐쇄망 배포본은 레지스트리를 거치지 않고 반입 매체로 이미지를 적재하므로, 이미지 · 모델 · 설정 파일 전체의 SHA-256 목록을 배포 절차가 대조하여 불일치 시 배포를 중단합니다. 오픈소스 구성요소는 라이선스 대장으로 관리합니다."},
    # [2026-09-08 정정] "통과한 것만 학습에 사용" 은 학습 진입에 강제 게이트가 있다는 뜻으로
    # 읽히나, 학습 경로에는 그 검사가 없다(training_service · trainer · p1_train_classifier
    # 전부 호출 0건). 검사가 실제로 도는 자리는 학습셋·평가셋 생성 단계다.
    "M03": {"evidence": "수집 · 전처리 단계에서 개인정보를 검출 · 마스킹합니다. 학습셋 · 평가셋을 생성하는 단계에 품질 기준 검사와 등급 정보 노출 검사(문서 길이만으로 등급이 맞혀지는 비율 · 등급 전용 문장 커버리지)를 두어, 기준을 넘으면 산출물을 기록하지 않고 중단합니다. 합성 문서는 본문에 등급 표현이 남은 것을 검수 적재 대상에서 제외합니다."},
    "M04": {"evidence": "원본 문서는 AES-256-GCM 으로 암호화하여 저장합니다. 운영 배포 구성에서 강제 적용되며, 암호화 키가 설정되지 않으면 기동을 거부하여 평문 저장을 차단합니다."},
    "M05": {"evidence": "역할 기반 접근통제를 적용하고, 관리 화면은 공유 인증키를 거부하고 개인 계정 기반 토큰 인증만 허용합니다. 등급 결정 행위를 실계정으로 식별하기 위한 조치입니다."},
    "M06": {"evidence": "기관 내부 보고 · 승인 절차에 해당합니다. 시스템은 문서 보안등급과 처리 이력을 기록하여 승인 판단에 필요한 근거를 제공합니다."},
    "M07": {"evidence": "데이터를 4개 보안등급(TS · S1 · S2 · S3)으로 구분하여 구성 · 활용하며, 등급별 · 분야별 구성 현황을 확인할 수 있습니다. 평가 기준 데이터는 검수자 서명을 거친 것만 편입합니다."},
    "M08": {"evidence": "데이터 접근 · 변경 기록을 해시 연결 구조의 감사 기록으로 남겨 변조를 탐지합니다. 학습 수행 이력은 별도 이력으로 보관합니다."},
    # [2026-09-08 정정] 두 군데가 실제보다 강했다. (1) '모든 요청'이 아니다 - 상태 프로브 ·
    # API 문서 · 정적 자원 · 지표 경로는 제외된다(api/middleware.py:74-82 · 90-98 · 187-188).
    # (2) '입·출력 정보'가 아니다 - 남는 것은 요청 본문의 SHA-256 해시와 행위 정보이지
    # 본문 자체가 아니다(middleware.py:200 · 300-310).
    "M09": {"evidence": "API 요청은 감사 기록 계층을 거치며, 행위 코드 · 행위자 · 요청 본문 해시(SHA-256) · 접속 주소 · 성공 여부가 해시 연결 구조로 기록되어 변조 · 삭제 · 순서 변경을 검출합니다. 상태 프로브 · API 문서 · 정적 자원 · 지표 수집 경로는 상태를 바꾸지 않으므로 기록 대상에서 제외합니다. 판정 결과와 근거 문장은 별도 표에 보존합니다. 운영 지표는 표준 모니터링 규격(Prometheus)으로 수집합니다."},
    # [2026-09-08 정정] '파일마다'는 전칭인데, 실측하면 데이터셋 명세 101종 중 87종이다.
    # 해시가 없는 14종에 현행 배포 모델의 학습셋 명세(labeled_p1_v5_clean)가 들어 있어
    # 전칭을 그대로 두면 감리에서 그 파일 하나로 반증된다.
    # {mf_*} 는 render() 가 실제로 세어 넣는다. 수치를 상수로 적으면 재현할 방법이
    # 없는 값이 되고, 데이터셋이 늘어도 문서만 옛 숫자로 남는다.
    "M10": {"evidence": "학습 · 평가 데이터는 데이터셋 단위 명세 파일로 출처 · 구성 · 분할 이력을 관리하며, 명세에 데이터 파일별 SHA-256 해시를 기록합니다(명세 {mf_total}종 중 {mf_hash}종, 해시 항목이 없는 초기 명세 {mf_gap}종은 순차 보완). 서버 이전 시에는 반출 파일 전체에 대해 파일별 SHA-256 매니페스트를 별도 생성하고, 도착지에서 재대조하여 전송 중 손상을 검출합니다."},
    "M11": {"evidence": "시스템 구성요소 명세(SBOM)를 산출하고, 릴리스 단위로 구성 · 버전 · 변경 이력을 기록합니다."},
    # [2026-09-08 정정] 종전 문안은 "배포본 계약 해시가 일치하지 않으면 모델 적재를 거부한다"
    # 였으나, 그 대조는 모델 디렉터리에 operating_point.json 이 있을 때만 켜지고
    # (m5_inference/pipeline.py:385-386 조기 return) 배포하는 릴리스 모델에는 그 파일이 없다
    # (artifacts 전체 0건). 배포본에서 한 번도 발동하지 않는 장치를 강제 장치로 적었던 것이라
    # 실제로 발동하는 세 가지로 교체했다.
    "M12": {"evidence": "빌드 식별자를 이미지에 각인하고 배포 시 기동된 컨테이너의 값과 대조합니다. 배포 번들은 빌드 단계에서 동봉 모델의 지문을 릴리스 모델과 대조하여 예전 모델이 실린 채 배포되는 것을 차단하고, 설치 단계에서는 반입 파일 전체의 체크섬 목록을 대조하여 불일치 시 배포를 중단합니다. 추론 파이프라인은 모델에 각인된 등급 매핑을 그대로 사용하며, 운영 등급 체계와 맞지 않으면 적재를 거부합니다."},
    "M13": {"evidence": "입력 문서는 개인정보 마스킹을 거칩니다. 외부 생성형 AI 로의 출력 전송은 출처 허용목록으로 통제하며, 확인되지 않은 자료는 차단합니다."},
    "M14": {"evidence": "요청 본문 크기 상한을 문서 해석 이전 단계에서 적용하여 과대 요청을 차단합니다. 업로드 파일의 형식과 크기도 제한합니다."},
    "M15": {"evidence": "모델 추론과 규칙 판정을 중첩 적용하고 두 결과의 합치 여부를 검수 라우팅에 반영합니다. 안전 게이트가 비활성화된 상태로는 기동되지 않도록 하여 보호장치가 조용히 해제되는 것을 방지합니다."},
    "M16": {"evidence": "모델 저장 영역을 읽기 전용으로 마운트하고 폐쇄망 내부에 배치합니다. 모델 활성 전환 · 회수는 관리자 권한으로만 수행할 수 있습니다."},
    "M17": {"caveat": "배포 검증 항목이므로 <strong>차기 서버 구축 시 수행</strong>합니다.",
            # [2026-09-08 정정] 종전 문안은 실 컨테이너 점검이 노출을 "막는다"고 읽혔으나,
            # 그 점검(verify_deploy_live.sh)은 결과를 출력만 하고 종료코드를 바꾸지 않으며
            # 어떤 배포 스크립트도 부르지 않는다. 실제 차단 게이트는 설정 파일을 보는
            # CI 시험(test_compose_api_bind_loopback.py)이다. 강제력을 그 자리로 옮겼다.
            "evidence": "데이터베이스 · 캐시 · 응용 서버를 호스트 내부 주소에만 바인딩하고, 외부 연계 구간은 역방향 프록시에서 종단합니다. 배포 구성의 바인딩 기본값은 상시 검사 항목으로 두어, 응용 서버가 외부에 직접 열린 구성이 배포본에 포함되지 않도록 합니다. 기동된 컨테이너의 실제 포트 바인딩은 배포 후 점검 절차에서 별도로 확인하며, 이 점검은 노출 여부를 드러내는 확인 절차로 배포를 자동으로 중단시키지는 않습니다."},
    # [2026-09-08 정정] 종전 문안은 "인증 토큰은 스크립트 접근이 차단된 쿠키로 전달한다"
    # 였으나 거짓이다. 이 쿠키는 로그인 화면이 document.cookie 로 심으므로(api/golden.py:778)
    # HttpOnly 가 붙을 수 없고 Secure 도 없다. 소스가 2026-08-28 에 이미 같은 취지로
    # 정정해 둔 사실을(api/_jwt_auth.py:323-328) 이 문서가 되살려 내보내고 있었다.
    # 실제로 붙는 속성(SameSite=Lax)만 적고, 미적용 사실은 단서로 밝힌다.
    # [2026-09-08 정정] 종전 단서는 바인딩 설정이 아직 반영 전인 것처럼 읽혔다. 설정은
    # 이미 배포 구성에 들어 있고(docker-compose.airgap.yml:112), 남은 것은 운영 서버에서의
    # 적용·실측 확인이다. M17 단서와 같은 기준으로 맞춘다.
    "M18": {"caveat": "바인딩 설정은 배포 구성에 반영되어 있으며, <strong>운영 서버에서의 적용 · 실측 확인은 차기 서버 구축 시 수행</strong>합니다. mTLS <strong>운영 인증서(PKI)는 발주기관 발급 사항</strong>이며, 발급 후 적용합니다. 인증 토큰 쿠키의 <strong>HttpOnly &middot; Secure 속성은 아직 적용하지 않았습니다</strong> &mdash; 현재는 로그인 화면이 스크립트로 쿠키를 심는 구조이며, HTTPS 종단을 확보하고 서버가 발급하도록 전환할 때 함께 적용합니다.",
            "evidence": "연계 구간은 상호 TLS(mTLS)로 보호하며 클라이언트 인증서를 검증하고 TLS 1.3 만 허용합니다. 인증 토큰 쿠키는 SameSite=Lax 로 교차 사이트 전송을 제한합니다. 응용 서버는 호스트 내부 주소에만 바인딩하며 외부 노출은 프록시를 통해서만 이루어집니다."},
    "M19": {"evidence": "역할에 따라 접근 가능한 기능과 데이터를 제한합니다. 컨테이너는 관리자 권한이 아닌 전용 계정으로 실행하며, 호스트 사전점검 절차가 권한 불일치를 배포 전에 확인합니다."},
    "M20": {"evidence": "모델 활성 전환 · 회수는 관리자 권한 API 로만 수행되며 동시 실행이 직렬화됩니다. 평가 기준 데이터 편입은 검수자 서명 절차를 거치지 않으면 성립하지 않습니다."},
    # [2026-09-08 정정] 두 군데를 좁혔다. (1) 무재기동 회수는 회수를 처리한 프로세스에만
    # 적용된다 - 모델 리로드 팬아웃이 미구현이라(NFR-OPS-01 부분구현) 다중 워커에서는
    # 나머지가 옛 모델을 계속 서빙한다. [2026-09-11] 이제 나머지 프로세스도
    # serving_model_refresh_seconds(기본 30초) 안에 스스로 따라간다 — 아래 M21 단서
    # ('재기동으로 갱신')는 더 보수적인 절차라 그대로 둔다(문안 변경은 요청 시에). (2) '모델 백업'에 근거가 없다 - 백업 도구가 다루는
    # 대상은 PostgreSQL 덤프와 원문 스토리지뿐이고 모델 가중치는 대상이 아니다.
    "M21": {"caveat": "다중 워커 구성에서는 회수 후 <strong>재기동</strong>으로 전 프로세스를 갱신합니다.",
            "evidence": "오동작이 확인된 모델은 관리자 권한 API 한 번으로 이전 배포본으로 되돌립니다. 회수를 처리한 응용 프로세스는 재기동 없이 즉시 이전 모델을 서빙하며, 폐쇄망 · 운영 배포본은 응용 워커를 1개로 고정해 두었습니다. 데이터베이스와 원본 스토리지는 정기 백업하며 복구 절차를 별도로 운영합니다."},
    "M22": {"evidence": "판정 결과에 근거 문장과 요인별 판단 근거를 함께 제시합니다. 검수 화면은 신뢰도 수치가 아니라 처리 결정(자동확정 · 검수 대상 및 사유)을 우선 표시합니다."},
    # [2026-09-08 정정] '상시'가 근거를 넘었다. 모델을 실제로 돌리는 적대 게이트 CI 잡은
    # 수동 실행에 입력값을 켜야만 도는 조건부 잡이다(poc-ci.yml:816). 상시 도는 것은
    # 모델 없는 lite 시험이다. 기준치도 문서에 없어 발주기관이 검증할 수 없었다.
    "M23": {"evidence": "적대적 시나리오 평가 집합 100건을 릴리스 게이트에 포함합니다. 고보안 등급(TS · S1 · S2) 문서를 공개 등급(S3)으로 판정하는 미탐이 허용 상한 6건(기준선 4건)을 넘으면 게이트가 실패하여 해당 모델은 배포 후보에서 제외됩니다. 상시 검사에서는 이 평가 집합의 적재와 미탐 판정 정의를 확인하고, 모델을 실제로 실행하는 적대 평가는 릴리스 게이트에서 수행합니다."},
    # [2026-09-08 정정] '학습 경로에 유지'에 근거가 없다. 이 집합을 읽는 학습셋 빌더가
    # 0건이고, 쓰임은 전부 평가·승격 게이트다. 평가 집합을 학습에 넣으면 같은 집합으로
    # 회귀를 못 재므로 넣지 않는 것이 옳고, 그 사실을 단서로 밝힌다.
    "M24": {"caveat": "이 집합은 <strong>평가 · 승격 게이트 전용</strong>이며 학습 데이터로 편입하지 않습니다 &mdash; 편입하면 같은 집합으로 회귀를 측정할 수 없습니다.",
            "evidence": "용어 혼동 · 출처 오인 등 오판단 유도 유형 100건을 적대 평가 집합으로 유지합니다. 재학습 후보는 승격 게이트에서 이 집합을 통과해야 하며, 미통과 후보는 모델 활성화 단계에서 차단됩니다."},
    "M25": {"evidence": "CI 가 잠금된 의존성 목록에 대해 알려진 취약점 점검을 수행합니다. 예외 처리 항목은 별도 목록으로 명시 관리합니다."},
    # [2026-09-08 정정] '모델 백업'에 근거가 없다(백업 대상은 DB·원문 스토리지뿐).
    # 모델 복원의 실제 기제는 버전 등록과 활성 버전 전환이므로 그것을 앞세운다.
    "M26": {"evidence": "학습된 모델은 버전 단위로 등록 · 보관하며, 관리자 권한 API 로 활성 버전을 이전 배포본으로 되돌려 복원합니다. 각 버전의 지표와 승격 이력을 함께 보관하여 어느 판으로 되돌릴지 판단할 수 있습니다. 데이터베이스와 원본 스토리지는 정기 백업하며 복구 절차를 별도로 운영합니다."},
    "M27": {"evidence": "요청 속도 제한을 적용하고 초과 시 표준 오류 응답으로 차단합니다. 요청 본문 크기 상한을 함께 적용합니다."},
    "M28": {"caveat": "시스템 폐기 시점에 수행하는 절차입니다.",
            "evidence": (
        "폐기 전용 도구로 구성요소를 삭제한 뒤 <strong>삭제되었음을 재확인</strong>하고 폐기 확인서를 산출합니다. "
        "원본 문서는 <strong>암호 소거</strong> 방식으로 처리합니다 &mdash; AES-256-GCM 으로 암호화 저장되어 있어 "
        "암호화 키를 파기하면 잔존 암호문을 복호할 수 없으므로, 대용량 원본을 물리적으로 덮어쓸 필요가 없습니다."
    ),
            "detail": (
        "삭제 대상은 폐쇄망 배포 구성이 만드는 데이터 볼륨 6종 &middot; 모델 &middot; 학습 데이터 &middot; 컨테이너입니다. "
        "되돌릴 수 없는 작업이므로 기본 동작은 모의 실행이며, 실제 삭제에는 대상 재확인과 키 파기 확인이 필요합니다. "
        "키 파기는 백업 &middot; 인수인계본 등 도구가 관할하지 않는 사본이 있을 수 있어 운영자 확인 절차로 뒀습니다."
    )},
    "M29": {"evidence": "발주기관이 수급인의 보안관리 실태를 점검하는 항목으로, 수급인 자체 점검으로 갈음할 수 없습니다. 점검 요청 시 필요한 자료를 제출합니다."},
    "M30": {"evidence": "기관 사용자 대상 교육 및 내부 보안정책 수립에 해당합니다. 시스템 사용 방법과 검수 절차는 관리자 사용 매뉴얼로 제공합니다."},
}

_AGENTIC_WHY = (
    "이 시스템은 문서 등급 분류기입니다. 스스로 목표를 세우거나 도구를 자율 호출하지 않고, "
    "에이전트끼리 통신하지도 않습니다. 합성문서 생성에서 외부 상용 LLM 을 부르는 구간이 있으나 "
    "그것은 에이전틱 AI 가 아니라 가이드북 제2장 <strong>유형②(내부 시스템의 외부 AI 연계)</strong>에 "
    "해당하며, 중점 항목 M09 · M13 · M14 · M24 · M27 로 위 공통 표에서 이미 점검했습니다."
)
_PHYSICAL_WHY = (
    "구동기 · 센서 · 로봇을 제어하지 않습니다. 문서를 입력받아 등급을 내는 서버 소프트웨어이며 "
    "물리 세계에 작용하는 출력이 없습니다."
)

# 유형별 중점 항목(가이드북 제2장). 아래 '시스템 구성 유형' 표와 같은 자료를 쓰므로
# 표와 설명 문장이 어긋날 수 없다.
_TYPES: list[tuple[str, str, str, list[str]]] = [
    ("①", "기관 내부망 단독 구축 · 운영", "회원사 폐쇄망 배포",
     ["M05", "M16", "M17", "M19"]),
    ("②", "내부망 시스템을 외부 AI와 연계", "합성문서 생성 시 상용 LLM 호출",
     ["M09", "M13", "M14", "M24", "M27"]),
    ("③", "내부망 AI가 외부 인터넷 자료 수집 · 학습", "공개 판례 수집 · 학습",
     ["M01", "M02", "M03", "M04", "M10", "M17"]),
]


def _template_parts() -> tuple[str, str]:
    """정본에서 style 과 nav 를 그대로 가져온다. 서식을 새로 만들지 않는다."""
    text = _TEMPLATE.read_text(encoding="utf-8")
    style = re.search(r"<style>.*?</style>", text, re.S)
    nav = re.search(r"<nav class=\"nav\">.*?</nav>", text, re.S)
    if not style or not nav:
        raise SystemExit(f"정본에서 style/nav 를 찾지 못했다: {_TEMPLATE}")
    nav_html = nav.group(0).replace("KL 질의사항 회신서", "AI시스템 보안대책 체크리스트")
    nav_html = nav_html.replace(">감리 산출물<", ">보안 점검<")
    return style.group(0), nav_html


def _esc(s: str) -> str:
    return html.escape(s, quote=False)


def count_manifest_hashes() -> tuple[int, int]:
    """데이터셋 명세 중 파일별 SHA-256 을 기록한 것이 몇 개인지 **센다**.

    왜 여기서 세는가(2026-09-08). 이 수치를 손으로 적으면 리포에만 있고 재현할 방법이
    없는 값이 된다 - 감리에서 "어떻게 나온 숫자냐"고 물으면 댈 것이 없다. 그래서
    상수로 적지 않고 문서를 만들 때마다 실제로 센다. 생성기를 돌리는 것이 곧 재현이다.

    **git 이 추적하는 파일만 센다.** 처음엔 datasets/ 를 그냥 훑었는데 두 가지가 났다:

        느리다        rglob 이 거대한 트리를 걸어 116초가 걸렸다(CI 시험에 들어가는 값이다)
        안 정해진다   미추적 작업물(다른 세션이 만드는 datasets/ab_synth 등)이 계수를 움직인다.
                      그러면 문서가 사람 손을 안 탔는데도 --check 가 어긋났다 붙었다 한다.

    추적본만 세면 빠르고, 리포에 실제로 담긴 것을 세게 되어 뜻도 맞는다.

    ⚠ `git ls-files` 는 무옵션이면 한글 경로를 8진 이스케이프로 감싸 내보낸다.
       그러면 그 경로가 조용히 빠진다 - `-z` 를 반드시 쓴다(2026-09-08, rag-fb 세션 제보).

    반환: (명세 총 개수, 그중 sha256 을 담은 개수)
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--", "datasets"],
            cwd=str(_POC), capture_output=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(
            f"명세 개수를 셀 수 없다(git 실패): {exc}\n"
            "  이 수치는 제출 문서에 실린다. 셀 수 없으면 문서를 만들지 않는다."
        ) from exc

    total = with_hash = 0
    for raw in out.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", errors="replace")
        name = rel.rsplit("/", 1)[-1].lower()
        if "manifest" not in name or not name.endswith((".json", ".yaml", ".yml")):
            continue
        total += 1
        p = _POC / rel
        try:
            if "sha256" in p.read_text(encoding="utf-8", errors="replace"):
                with_hash += 1
        except OSError:
            pass
    return (total, with_hash)


def _rows(controls: list[dict]) -> str:
    out: list[str] = []
    for c in controls:
        if c["scope"] == "na":
            continue
        note = NOTES.get(c["id"], {})
        if c["scope"] == "org":
            mark, cell = "발주기관", "기관 수행 항목"
        elif c["verdict"] == "근거있음":
            mark, cell = "예", "적용"
        else:
            mark, cell = "아니오", "미적용"

        # evidence · caveat · remedy 는 이 파일에서 사람이 직접 쓰는 문안이라
        # 의도한 태그(<strong> 등)를 그대로 내보낸다. 여기서 escape 하면 태그 글자가
        # 화면에 그대로 보인다 - M28 에서 실제로 그렇게 나갔다(2026-09-08).
        # 기계가 만든 값(c["title"])만 escape 한다.
        body = note.get("evidence", "")
        if "{mf_" in body:
            mf_total, mf_hash = count_manifest_hashes()
            body = body.format(mf_total=mf_total, mf_hash=mf_hash,
                               mf_gap=mf_total - mf_hash)
        if note.get("caveat"):
            body += f'<br /><span class="part">단서</span> {note["caveat"]}'
        if note.get("detail"):
            body += f'<br /><span class="meta">상세는 아래 &lsquo;{c["id"]} 보충&rsquo; 참조</span>'
        if note.get("gap"):
            body += (
                f'<br /><span class="gap">부족: {_esc(note["gap"])}</span>'
                f'<br /><strong>대안</strong> {note["remedy"]}'
            )

        cls = {"예": "ok", "아니오": "gap", "발주기관": "part"}[mark]
        out.append(
            f'<tr><td><code>{c["id"]}</code></td>'
            f'<td>{_esc(c["title"])}</td>'
            f'<td><span class="{cls}">{mark}</span><br /><span class="meta">{cell}</span></td>'
            f"<td>{body}</td></tr>"
        )
    return "\n".join(out)


def _na_line(controls: list[dict], prefix: str) -> str:
    """해당없음 항목은 한 줄로 모아 적는다.

    전부 같은 판정('해당없음')이라 항목마다 한 행씩 표를 만들면 27행이 늘어서고
    보고서에서 읽을 것이 없는 자리가 두 쪽을 차지한다. 항목 번호와 이름은
    누락 여부를 확인할 수 있어야 하므로 그대로 남긴다.
    """
    items = [c for c in controls if c["id"].startswith(prefix)]
    if not items:
        return ""
    names = " &middot; ".join(_esc(c["title"]) for c in items)
    return (
        f'<p class="meta"><code>{items[0]["id"]}</code>~<code>{items[-1]["id"]}</code> '
        f"{names} &mdash; 모두 해당없음.</p>"
    )


def _na_count(controls: list[dict], prefix: str) -> int:
    return sum(1 for c in controls if c["id"].startswith(prefix))


def render() -> str:
    style, nav = _template_parts()
    data = audit()
    controls = data["controls"]

    common = [c for c in controls if c["scope"] != "na"]
    yes = sum(1 for c in common if c["scope"] == "code" and c["verdict"] == "근거있음")
    no = sum(1 for c in common if c["scope"] == "code" and c["verdict"] != "근거있음")
    org = sum(1 for c in common if c["scope"] == "org")
    na = sum(1 for c in controls if c["scope"] == "na")

    gaps = [(c["id"], NOTES[c["id"]]) for c in common
            if c["id"] in NOTES and NOTES[c["id"]].get("gap")]

    gap_html = "\n".join(
        f'<div class="note"><h3>{cid} · {_esc(next(c["title"] for c in common if c["id"] == cid))}</h3>'
        f'<p class="gap">{_esc(n["gap"])}</p>'
        f'<p><strong>대안</strong> {n["remedy"]}</p></div>'
        for cid, n in gaps
    )

    agentic_n = _na_count(controls, "A-M")
    physical_n = _na_count(controls, "P-M")
    org_ids = " &middot; ".join(c["id"] for c in common if c["scope"] == "org")

    # 결론 문장의 숫자를 상수로 적으면 판정이 바뀔 때 요약표와 어긋난다.
    # 2026-09-08 이전 판에 '27개'가 박혀 있었다. 전부 계산값으로 만든다.
    if no:
        conclusion = (
            f'<p class="gap">공통 {yes + no + org}개 항목 중 {no}개 항목에 보안대책이 갖춰져 '
            f"있지 않습니다. 부족한 내용과 대안은 아래에 적었습니다.</p>"
        )
    else:
        conclusion = (
            f'<p class="ok">공통 {yes + no + org}개 항목 중 시스템이 담당하는 {yes}개 항목에 '
            f"보안대책이 모두 갖춰져 있습니다. 미적용 항목은 없습니다.</p>"
            f"<p>나머지 {org}개({org_ids})는 기관 내부 절차 &middot; 수급인 점검 &middot; "
            f"사용자 교육에 해당하여 발주기관이 수행합니다.</p>"
        )
    conclusion += (
        f"<p>에이전틱 AI({agentic_n}개) &middot; 피지컬 AI({physical_n}개)는 시스템 특성상 "
        f"해당하지 않으며, 사유는 각 절에 적었습니다.</p>"
    )

    # 표 안에 넣기에는 긴 설명은 본문 아래로 뺀다(표 한 칸만 문단이 되면 보고서가 읽히지 않는다).
    detail_html = "\n".join(
        f'<div class="note"><h3>{cid} 보충 &middot; '
        f'{_esc(next(c["title"] for c in common if c["id"] == cid))}</h3>'
        f'<p>{NOTES[cid]["detail"]}</p></div>'
        for cid in (c["id"] for c in common)
        if cid in NOTES and NOTES[cid].get("detail")
    )

    types_rows = "\n".join(
        f"      <tr><td>{num} {_esc(name)}</td><td>{_esc(where)}</td>"
        f'<td><code>{" &middot; ".join(ids)}</code></td></tr>'
        for num, name, where, ids in _TYPES
    )
    # 단서가 붙은 중점 항목을 NOTES 에서 직접 뽑는다. 손으로 적으면 단서가 바뀔 때
    # 이 문장만 옛말로 남는다 - 2026-09-08 이전 판에서 실제로 그렇게 어긋나 있었다.
    focus_caveat = sorted({i for _, _, _, ids in _TYPES for i in ids if NOTES.get(i, {}).get("caveat")})
    if focus_caveat:
        types_note = (
            "<p>중점 항목 중 대책이 비어 있는 것은 <strong>없습니다.</strong> 다만 "
            f'{" &middot; ".join(focus_caveat)} 에는 위 표에 적은 단서가 붙습니다.</p>'
        )
    else:
        types_note = "<p>중점 항목 중 대책이 비어 있는 것은 <strong>없습니다.</strong></p>"

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>AI시스템 보안대책 체크리스트</title>
{style}
<style>
/* 정본 서식의 .meta 는 12.5px 절대값이라, 표 안에서 쓰면 표 본문(화면 12px ·
   인쇄 8.5pt=11.3px)보다 커진다. 보조 라벨('적용')이 판정('예')보다 크게 찍히던
   것을 표 안에서만 상속으로 되돌린다(2026-09-08 인쇄 검토).
   정본 style 은 여러 문서가 함께 쓰므로 건드리지 않고 여기서만 좁힌다. */
td .meta, th .meta, td.meta {{ font-size: inherit; }}
</style>
</head>
<body>
{nav}
<main class="wrap">

<header>
  <p class="eyebrow">국가정보원 「AI 보안 가이드북」(2025.12) 부록1</p>
  <h1>AI시스템 보안대책 체크리스트</h1>
  <p>「AI 보안 가이드북」 부록1 체크리스트 57개 항목에 대한 점검 결과입니다.</p>
  <table class="docinfo">
    <tbody>
      <tr><th>대상 시스템</th><td>AI 영업비밀 관리시스템 &mdash; 문서 보안등급 자동분류</td></tr>
      <tr><th>준거 문서</th><td>국가정보원 「AI 보안 가이드북」(2025.12) 부록1 &lsquo;AI시스템 보안대책 체크리스트&rsquo;</td></tr>
      <tr><th>점검 범위</th><td>시스템 구성요소 &middot; 배포 구성 &middot; 운영 절차 (공통 30 &middot; 에이전틱 17 &middot; 피지컬 10 = 57개 항목)</td></tr>
      <tr><th>점검 방법</th><td>항목별 보안대책의 구현 근거를 소스 &middot; 설정 &middot; 절차 문서에서 직접 확인</td></tr>
      <tr><th>점검 일자</th><td>2026. 9. 8.</td></tr>
      <tr><th>작성</th><td>주식회사 로이드케이</td></tr>
    </tbody>
  </table>
  <div class="note">
    <p><strong>이 표가 뜻하는 것.</strong> 체크리스트가 묻는 것은 &ldquo;대책을 <strong>마련</strong>하였는가&rdquo;이며,
    &lsquo;예&rsquo;는 그 대책이 구현되어 근거 파일을 제시할 수 있다는 뜻입니다.
    <strong>운영 서버에서의 실측 검증과는 다릅니다.</strong> 이 구분이 필요한 항목에는 &lsquo;단서&rsquo;를 함께 적었습니다.</p>
    <p>근거 자료(항목별 구현 위치 &middot; 설정값 &middot; 검증 결과)는 별도 보유하며,
    요청 시 제출합니다.</p>
  </div>
</header>

<section>
  <h2>점검 결과 요약</h2>
  <table>
    <thead><tr><th>구분</th><th>항목 수</th><th>내용</th></tr></thead>
    <tbody>
      <tr><td>가. 공통 &mdash; 예</td><td><strong>{yes}</strong></td><td>대책이 구현되어 있고 근거 파일을 제시할 수 있음</td></tr>
      <tr><td>가. 공통 &mdash; 아니오</td><td><strong>{no}</strong></td><td>보안대책이 마련되지 않은 항목</td></tr>
      <tr><td>가. 공통 &mdash; 발주기관 수행</td><td><strong>{org}</strong></td><td>기관 내부 절차 &middot; 수급인 점검 &middot; 사용자 교육. 해당하는 항목이며 발주기관이 수행</td></tr>
      <tr><td>나 &middot; 다. 에이전틱 &middot; 피지컬</td><td><strong>{na}</strong></td><td>해당없음. 사유는 각 절에 기재</td></tr>
    </tbody>
  </table>
  <p class="meta">전체 {yes + no + org + na}개 항목. 항목별로 해당 보안대책이 구현된 위치를 지정하고
  그 구현이 실재하는지 확인하는 방식으로 판정했습니다. 명칭이 유사하나 목적이 다른 구성요소를
  근거로 계상하지 않도록, 항목마다 근거를 개별 지정하여 대조했습니다.</p>
</section>

<section>
  <h2>점검 결론</h2>
  {gap_html or conclusion}
</section>

<section>
  <h2>가. 보안대책 체크리스트 (공통)</h2>
  <table>
    <thead><tr><th style="width:64px">순번</th><th style="width:210px">항목</th>
    <th style="width:88px">적용여부</th><th>대책 내용 · 근거</th></tr></thead>
    <tbody>
{_rows(controls)}
    </tbody>
  </table>
{detail_html}
</section>

<section>
  <h2>나. 에이전틱 AI (자율형 AI) &mdash; {agentic_n}개 항목 전부 해당없음</h2>
  <p>{_AGENTIC_WHY}</p>
{_na_line(controls, "A-M")}
</section>

<section>
  <h2>다. 피지컬 AI (로봇 · 기계 등) &mdash; {physical_n}개 항목 전부 해당없음</h2>
  <p>{_PHYSICAL_WHY}</p>
{_na_line(controls, "P-M")}
</section>

<section>
  <h2>시스템 구성 유형</h2>
  <p>가이드북 제2장은 시스템을 네 유형으로 나누고 유형별 중점 항목을 지정합니다.
  이 시스템은 <strong>{len(_TYPES)}개 유형에 걸칩니다.</strong></p>
  <table>
    <thead><tr><th style="width:280px">유형</th><th style="width:200px">이 시스템에서</th><th>중점 항목</th></tr></thead>
    <tbody>
{types_rows}
      <tr><td>④ 클라우드 등 외부망 구축 · 운영</td><td class="meta">해당없음</td><td class="meta">-</td></tr>
    </tbody>
  </table>
  {types_note}
</section>

<section>
  <h2>발주기관 수행 사항</h2>
  <p>체크리스트 판정이 &lsquo;발주기관&rsquo;인 {org}개 항목입니다. 시스템으로 대체할 수 없습니다.</p>
  <table>
    <thead><tr><th style="width:64px">항목</th><th style="width:230px">내용</th><th>비고</th></tr></thead>
    <tbody>
      <tr><td><code>M06</code></td><td>민감정보 사용 사전 승인</td><td>기관 내부 보고 &middot; 승인 절차</td></tr>
      <tr><td><code>M29</code></td><td>용역업체 보안관리</td><td>발주기관이 수급인을 점검하는 항목이므로 수급인 자체 점검으로 갈음할 수 없습니다</td></tr>
      <tr><td><code>M30</code></td><td>사용자 교육 및 보안정책 수립</td><td>기관 사용자 대상 교육 &middot; 내부 보안정책</td></tr>
    </tbody>
  </table>
  <h3>참고 &mdash; 판정은 &lsquo;예&rsquo;이나 기관 조치가 함께 필요한 항목</h3>
  <table>
    <thead><tr><th style="width:64px">항목</th><th style="width:230px">기관 조치</th><th>비고</th></tr></thead>
    <tbody>
      <tr><td><code>M18</code></td><td>mTLS 운영 인증서(PKI) 발급</td><td>기관 PKI 정책에 따르는 사항입니다. 시스템 측 설정과 검증 절차는 준비되어 있으며, 인증서 발급 후 적용합니다</td></tr>
    </tbody>
  </table>
  <p class="meta">본 체크리스트 양식의 작성 &middot; 서명 주체는 발주기관(사업담당자 &middot; 정보화사업담당관)이며,
  본 문서는 그 작성에 사용하는 근거 자료입니다.</p>
</section>

</main>
</body>
</html>
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AI 보안대책 체크리스트 문서 생성")
    ap.add_argument("--check", action="store_true", help="생성 결과가 현재 파일과 같은지만 확인")
    args = ap.parse_args(argv)

    rendered = render()
    if args.check:
        if not _OUT.exists():
            print(f"[check] 문서가 없다: {_OUT}")
            return 1
        if _OUT.read_text(encoding="utf-8") != rendered:
            print("[check] 문서가 코드와 어긋난다. 다시 생성할 것.")
            return 1
        print("[check] 문서와 코드가 일치한다.")
        return 0

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(rendered, encoding="utf-8")
    print(f"[생성] {_OUT}")
    print(f"[크기] {len(rendered):,}자")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
