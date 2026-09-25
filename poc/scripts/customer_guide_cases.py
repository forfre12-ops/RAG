"""Eight new manuscripts and twenty separately authored hypothetical settings.

No target grade is supplied to the writer or policy evaluator. Cost/ACL/public
facts below are explicitly stipulated fiction, NOT extracted customer metadata.
"""
from __future__ import annotations

from customer_benchmark_drafts import build_drafts
from koipa.customer_benchmark import FLAGS
from koipa.customer_guide_reference import build_case
from koipa.policy_facts import require, text_digest, value_digest

NEW_DRAFTS = (
    ("제조품질", "thermal-window", "접합 시편 열처리 구간표",
     "진공 접합 시험판 R의 세 구간을 기록한다. 1구간은 80도에서 12분 유지하고, 2구간은 135도에서 18분 유지한다. "
     "3구간은 65도에서 10분 유지한 뒤 시편을 꺼낸다. 세 유지 구간의 합은 40분이며 승온과 냉각 시간은 이 합에 넣지 않는다.\n"
     "2구간에서 시편 중앙과 가장자리의 온도 차이가 4도를 넘으면 다음 구간 진입을 보류한다. "
     "차이가 4도 이하인 상태를 연속 90초 확인한 경우에만 냉각을 시작한다. 시간이 지났다는 이유만으로 단계를 넘기지 않는다.\n"
     "직전 시험판은 가장자리 들뜸이 있어 치구 접촉면을 3mm 넓혔다. 이번 비교는 변경 치구를 쓴 시편 여섯 장과 "
     "이전 치구를 쓴 시편 여섯 장을 구분한다. 판정표에는 들뜸 위치와 길이를 적고 두 묶음의 결과를 합치지 않는다.",
     [("hold-total", "유지 시간만 합하면 40분이다.", "세 유지 구간의 합은 40분이며 승온과 냉각 시간은 이 합에 넣지 않는다."),
      ("transition-gate", "온도 차이의 연속 충족이 단계 전환 조건이다.", "차이가 4도 이하인 상태를 연속 90초 확인한 경우에만 냉각을 시작한다.")]),
    ("제품개발", "alignment-fit", "광학 지그 영점 보정 계산서",
     "지그 L의 기준 위치를 바꾸기 전 네 지점의 잔차를 확인했다. 왼쪽에서 오른쪽 순서로 잔차는 0.12, 0.08, -0.04, 0.00mm였다. "
     "네 값의 평균은 0.04mm다. 다음 판의 공통 영점에는 -0.04mm를 적용한다. 부호는 측정값에 더하는 보정값을 기준으로 적는다.\n"
     "공통 영점을 적용한 뒤의 잔차는 0.08, 0.04, -0.08, -0.04mm가 된다. 이 조치는 지점별 기울기를 없애는 조정은 아니다. "
     "허용 범위는 각 지점에서 절댓값 0.10mm 이내이며 평균값만으로 합격을 정하지 않는다.\n"
     "치구를 재장착할 때에는 같은 네 지점과 같은 측정 방향을 유지한다. 이전 영점과 새 영점을 동시에 더하지 않도록 "
     "제어표의 적용 열을 한 곳만 선택한다. 다른 렌즈를 조립한 시편에는 이번 보정값을 그대로 복사하지 않는다.",
     [("residual-mean", "네 지점 잔차의 평균은 0.04mm다.", "네 값의 평균은 0.04mm다."),
      ("pointwise-limit", "평균이 아니라 모든 지점에서 허용 범위를 검사한다.", "허용 범위는 각 지점에서 절댓값 0.10mm 이내이며 평균값만으로 합격을 정하지 않는다.")]),
    ("영업사업", "capacity-allocation", "차기 분기 생산능력 배정 시나리오",
     "가상 사업부의 다음 분기 가용 시간은 1,200시간이다. 납기 확정 주문에 720시간, 시험 주문에 180시간을 배정하고 "
     "잔여 300시간은 수주 전환 시나리오 비교용으로 남긴다. 시험 주문은 확정 주문으로 집계하지 않는다.\n"
     "후보 가람은 추가 160시간에 기여액 2,400만원을 제안했고, 후보 누리는 220시간에 기여액 3,080만원을 제안했다. "
     "둘 다 받으면 380시간이 필요하므로 잔여 시간을 80시간 넘는다. 동시에 수락하는 안은 현재 표에서 제외한다.\n"
     "추가 교대는 아직 승인되지 않았다. 비교표에는 두 후보 각각을 단독 수락했을 때의 유휴 시간과 기여액을 따로 둔다. "
     "담당은 설비 정비 일정이 바뀌면 가용 시간부터 다시 산정하고, 이 시나리오를 실제 수주 완료 실적으로 보고하지 않는다.",
     [("remaining-capacity", "기배정 900시간을 빼면 300시간이 남는다.", "잔여 300시간은 수주 전환 시나리오 비교용으로 남긴다."),
      ("combined-overflow", "두 제안을 모두 받으면 잔여를 80시간 초과한다.", "둘 다 받으면 380시간이 필요하므로 잔여 시간을 80시간 넘는다.")]),
    ("회계재무", "financing-window", "설비 투자 지급구간 조정안",
     "가상 설비 도입안의 계약 금액은 12억원이다. 계약 시 20%, 반입 시 50%, 검수 완료 시 30%를 지급하면 "
     "각 지급액은 2.4억원, 6억원, 3.6억원이다. 반입 구간의 자금 집중을 줄이기 위해 중도금을 두 차례로 나누는 안을 비교한다.\n"
     "제안안은 계약 20%, 반입 30%, 설치 완료 20%, 검수 완료 30%로 한다. 총액과 계약 지급액은 같지만 반입 시점의 "
     "지급액은 3.6억원으로 줄어든다. 설치 완료 조건은 가동 시험 48시간 종료이며 단순 반입과 구분한다.\n"
     "상대방의 수락은 아직 없다. 두 안은 현금 계획용 비교안이며 발주서 변경을 뜻하지 않는다. 금융비용과 환율 효과는 "
     "이번 계산에서 제외했으므로 절감액으로 보고하지 않는다. 자금 담당은 시점별 보유 현금 부족 여부를 별도 표에서 확인한다.",
     [("payment-sum", "지급 비율의 합은 두 안 모두 100%다.", "제안안은 계약 20%, 반입 30%, 설치 완료 20%, 검수 완료 30%로 한다."),
      ("no-acceptance", "상대방 수락은 미확정이다.", "상대방의 수락은 아직 없다.")]),
    ("연구시험", "energy-load", "건조기 부하별 전력 관측 요약",
     "같은 건조기를 빈 상태, 절반 적재, 전체 적재로 각각 20분 운전했다. 적산 전력은 순서대로 1.2kWh, 1.8kWh, 2.4kWh였다. "
     "운전 시간이 같으므로 평균 전력은 각각 3.6kW, 5.4kW, 7.2kW로 계산한다. 시작 예열의 소비량도 적산값에 포함됐다.\n"
     "시험실 습도는 세 운전 모두 45%였고 문을 여는 횟수는 한 번으로 통일했다. 적재량과 전력의 관계를 다른 습도에서도 "
     "동일하다고 가정하지 않는다. 이번 요약은 원시 계측 파일에서 종료 시각까지의 누적값을 옮긴 것이다.\n"
     "다음 비교에서는 예열 구간과 정상 운전 구간을 분리한다. 현재 숫자에서 임의의 예열 상수를 빼지 않는다. "
     "계측기 교체가 필요하면 기존 파일을 덮어쓰지 말고 측정 장비 식별자와 적용 시각을 함께 남긴다.",
     [("mean-power", "20분은 1/3시간이므로 평균 전력은 적산 전력의 세 배다.", "운전 시간이 같으므로 평균 전력은 각각 3.6kW, 5.4kW, 7.2kW로 계산한다."),
      ("warmup-included", "적산값에는 예열이 포함되어 있다.", "시작 예열의 소비량도 적산값에 포함됐다.")]),
    ("물류운영", "dispatch-planner", "상차 작업 순서 계산안",
     "두 상차 구역에서 출발 대기 차량 세 대를 처리한다. 차량 A는 25분, B는 40분, C는 15분의 상차 시간이 필요하다. "
     "첫 구역에서 B를 처리하고 둘째 구역에서 A 다음 C를 처리하면 두 구역 모두 40분에 끝난다.\n"
     "C의 도착이 시작 후 30분으로 늦어지면 둘째 구역은 A를 마친 뒤 5분 대기하고 C를 처리해 45분에 끝난다. "
     "따라서 도착 지연을 반영한 전체 완료 시점은 45분이다. 상차 시간 자체가 바뀐 것은 아니다.\n"
     "이번 안은 구역 간 장비 이동 시간이 없다는 실험 조건을 사용한다. 차량의 출발 승인이나 기사 휴식시간을 대체하지 않는다. "
     "새 차량을 끼워 넣을 때에는 남은 구역별 시간과 도착 가능 시점을 모두 갱신하고, 완료 차량의 작업량을 다시 더하지 않는다.",
     [("parallel-makespan", "기본안의 병렬 완료 시점은 40분이다.", "첫 구역에서 B를 처리하고 둘째 구역에서 A 다음 C를 처리하면 두 구역 모두 40분에 끝난다."),
      ("late-arrival", "C의 도착 지연을 반영하면 전체 완료는 45분이다.", "따라서 도착 지연을 반영한 전체 완료 시점은 45분이다.")]),
    ("고객지원", "return-triage", "반품 용기 점검 항목 변경",
     "반품 용기의 뚜껑이 닫히지 않는다는 접수 건을 점검할 때에는 빈 용기를 먼저 확인한다. 내용물이 있는 상태의 "
     "닫힘 시험과 혼합하지 않는다. 몸체 휨, 뚜껑 홈의 이물질, 결합 방향을 각기 다른 칸에 기록한다.\n"
     "이번 접수 여덟 건 중 세 건은 홈의 이물질을 제거한 뒤 닫힘을 확인했고, 두 건은 결합 방향을 바꾼 뒤 확인했다. "
     "나머지 세 건은 몸체 휨이 의심돼 별도 측정으로 넘겼다. 의심 단계의 세 건을 소재 불량 확정으로 집계하지 않는다.\n"
     "반송 여부는 접수 상태와 계약 조건을 함께 보고 결정한다. 점검자는 원래 상태 사진과 조치 후 사진을 구분하고, "
     "고객의 연락처는 이 점검표에 옮기지 않는다. 추가 측정 결과가 들어오면 기존 접수 번호에 후속 기록으로 연결한다.",
     [("case-reconciliation", "세 처리 구분의 합은 여덟 건이다.", "나머지 세 건은 몸체 휨이 의심돼 별도 측정으로 넘겼다."),
      ("not-confirmed-defect", "의심을 확정 불량으로 집계하지 않는다.", "의심 단계의 세 건을 소재 불량 확정으로 집계하지 않는다.")]),
    ("인사운영", "visitor-orientation", "체험장 방문 순서 안내",
     "체험장은 안내 데스크에서 방문 시간을 확인한 뒤 들어갑니다. 가방은 입구 보관함에 두고 시연대 위에는 안내된 "
     "재료만 올려놓습니다. 시연이 끝나면 사용한 도구를 처음 놓인 칸으로 돌려놓아 주세요.\n"
     "원형 스티커가 붙은 상자는 체험용 빈 상자이고, 삼각형 스티커가 붙은 상자는 사용 후 회수용입니다. 두 종류를 "
     "겹쳐 쌓지 않도록 부탁드립니다. 안내 담당이 교체되더라도 시연 순서는 입구의 번호표를 따릅니다.\n"
     "체험을 잠시 쉬고 싶으면 대기 의자로 이동해 주세요. 이 안내는 방문자의 이동과 정리 순서만 설명하며, 실제 생산 "
     "작업의 절차나 시설의 출입 권한을 부여하지 않습니다. 잃어버린 물건은 출구 안내 데스크에 문의해 주세요.",
     [("bin-symbols", "빈 상자와 회수 상자의 기호가 다르다.", "원형 스티커가 붙은 상자는 체험용 빈 상자이고, 삼각형 스티커가 붙은 상자는 사용 후 회수용입니다."),
      ("no-access-grant", "안내문이 시설 출입 권한을 부여하지 않는다.", "실제 생산 작업의 절차나 시설의 출입 권한을 부여하지 않습니다.")]),
)


def new_drafts():
    out = []
    for domain, key, title, body, claims in NEW_DRAFTS:
        text = title + "\n\n" + body + "\n"
        inp = {"doc_id": "doc-" + value_digest({"text": text, "context": []})[:24], "text": text, "context": []}
        evidence = []
        for name, claim, quote in claims:
            require(text.count(quote) == 1, "guide_body_quote_ambiguous")
            start = text.index(quote)
            evidence.append({"name": name, "claim": claim, "quote": quote, "start": start,
                             "end": start + len(quote), "sha256": text_digest(quote), "status": "authored_binding_only"})
        out.append({**FLAGS, "schema_version": "customer-synthetic-draft-v1", "document_origin": "synthetic",
                    "input": inp, "input_sha256": value_digest(inp), "domain": domain,
                    "family_id": "family-" + key, "scenario_id": "scenario-" + key,
                    "template_family_id": "template-" + key, "claims": evidence})
    return out


# Shorthand describes access conditions, never grade or factor numbers.
# Values are direct hypothetical acquisition/investment/access records. They do
# not assert that real paperwork with these titles has the same circumstances.
SETTINGS = {
    "control-spec": ("custodian", 320000, 16, "named", False,
        "가상 시험장의 설정값과 변경 이력은 보유 부서에서만 취득할 수 있다. 해당 보정 정보에 귀속된 작업은 16인시, 32만원이며 승인 명단의 계정만 시험 저장소를 읽는다. 단순한 수식이라는 이유로 비용을 부풀리거나 고가치로 판정하지 않는다."),
    "assembly-log": ("custodian", 180000, 8, "need", False,
        "작성 부서가 보관하는 이번 조립 관찰 기록은 밖에 제공하지 않았다. 귀속된 조립 관찰 정리 비용과 8인시만 계상하며, 설계 전체의 개발 비용은 합산하지 않는다. 조립 업무 역할을 가진 직원에게만 저장소 열람이 적용된다."),
    "sensor-observation": ("difficult", 450000, 24, "named", False,
        "동일 측정 요약은 유료 시험자료 서비스에서도 신청 심사 후 받을 수 있으나 일반 공개는 아니다. 이번 아홉 관측값에 직접 든 투입은 24인시, 45만원이다. 해당 시험 명단에 개별 등록된 사람만 원본 저장소를 읽는다."),
    "test-protocol": ("public", 60000, 4, "open", True,
        "가상 교육기관이 바로 이 판본 전체를 공개 수업 자료로 게시했고 별도 미공개 첨부는 없다. 절차 작성에 4인시가 들었다. 방문자도 신청 없이 전문을 읽을 수 있으며 게시된 판본은 외부 배포가 허가되어 있다."),
    "mixture-record": ("custodian", 120000, 8, "need", False,
        "이번 배합 관찰은 가상 실험반의 미게시 기록이며 원본은 실험반만 보관한다. 한 차례 시험과 정리에 직접 든 8인시, 12만원만 귀속했다. 실험 업무 담당 역할만 열람하며 개별 인명 승인을 별도로 두지는 않는다."),
    "quality-cause": ("difficult", 200000, 12, "need", False,
        "동일 조사 요약을 계약된 설비 점검업체에서도 요청 심사 후 얻을 수 있지만 불특정 다수 게시판에는 없다. 이번 재측정의 투입은 12인시, 20만원이다. 사내 점검 담당 역할에만 기록 접근을 허용하는 설정이 적용되어 있다."),
    "supplier-terms": ("difficult", 80000, 4, "named", False,
        "동일 견적 조건은 공급업체가 자격을 확인한 구매 담당에게 별도로 제공하므로 해당 구매 부서를 거치지 않아도 제한적으로 취득할 수 있다. 비교 정보의 취득 정리는 4인시이며, 회사 저장소에서는 개별 승인된 구매 계정만 본다."),
    "receipt-route": ("difficult", 20000, 2, "staff", False,
        "입고 일정은 계약 운송사의 배차 담당에게 확인해야 알 수 있고 공개 사이트에는 게시하지 않았다. 이번 일정 작성에 2인시가 들었다. 사내에서는 전 직원이 알아야 하는 게시판에 올려 직무별이나 인명별 열람 제한을 두지 않는다. 외부 게시 허가는 없다."),
    "renewal-pricing": ("custodian", 150000, 6, "named", False,
        "제안 수치는 발송 전 작성 부서만 보관하며 다른 취득 경로가 없다는 가상 설정이다. 해당 제안 산출에 6인시와 15만원이 들었다. 승인된 계약 담당자 목록에 등록된 계정만 원본을 열람한다. 고객의 실제 선택이나 수락은 가정하지 않는다."),
    "product-help": ("public", 40000, 3, "open", True,
        "전시 운영자가 동일 문답 전체를 누구나 읽는 안내 페이지에 배포했다. 문답 작성의 투입은 3인시이며 안내 문구 자체의 서비스 활용은 있다. 숨겨진 기술 부록은 없고 이 판본의 외부 배포가 허가되어 있다."),
    "shift-assignment": ("difficult", 30000, 2, "staff", False,
        "담당표는 계약된 행사 운영사에 요청해야 받을 수 있고 인터넷 게시물은 아니다. 이번 편성 작업은 2인시다. 사내 모든 직원이 알아야 하는 안내로 공유되어 직무와 개인별 열람 제한은 없다. 외부 공개는 별도로 허가하지 않았다."),
    "onboarding-form": ("public", 50000, 3, "open", True,
        "가상 기관이 이 빈 양식을 외부 다운로드 자료로 제공한다. 양식 구성에 3인시가 들었으며 작성된 장비 번호나 개인 정보는 포함하지 않는다. 외부 배포 허가는 빈 판본에 한정되며 이후 작성된 인수 기록에는 자동으로 이어지지 않는다."),
    "thermal-window": ("custodian", 36000000, 640, "named", False,
        "이 열처리 구간과 치구 비교 조건은 가상 보유 기업의 반복 접합 시험에서만 얻은 결과다. 해당 조건을 도출한 시험 장부에 640인시와 3,600만원이 귀속되어 있으며 다른 제품 개발비는 제외한다. 개별 승인된 공정 개발 인원만 저장소를 열람한다."),
    "alignment-fit": ("custodian", 31000000, 520, "named", False,
        "이 지그와 네 측정 위치의 잔차를 확정하기 위한 가상 정밀 계측에 520인시, 3,100만원이 직접 귀속됐다. 계산식 자체에 그 비용이 들었다는 뜻은 아니다. 해당 측정 정보는 보유자를 통하지 않고 얻을 수 없고 이름별 승인 목록으로 접근을 제한한다."),
    "capacity-allocation": ("custodian", 42000000, 720, "named", False,
        "수주 후보의 기여액과 가용 시간 조합은 가상 기업 내부의 설비 검증 및 원가 조사 결과이며 720인시와 4,200만원이 이 정보에 귀속된다. 거래 금액을 개발 비용으로 셈하지 않는다. 명시적으로 승인된 사업계획 담당 계정 외에는 원본 열람이 차단되어 있다."),
    "financing-window": ("custodian", 33000000, 560, "named", False,
        "가상 투자 협상에 사용할 지급 조건을 도출한 실사와 대안 검증의 직접 투입은 560인시, 3,300만원이다. 설비 가격 12억원은 정보 취득개발 비용에 포함하지 않는다. 제안은 외부 발송 전이며 보유 부서에서만 취득 가능하고 개별 승인 계정만 읽는다."),
    "energy-load": ("difficult", 32000000, 500, "named", False,
        "해당 적산값은 가상 공동시험기관이 제한된 신청자에게도 제공하므로 보유 부서만이 유일한 취득 경로는 아니다. 계측 결과를 확보한 환경 조성과 시험 비용 3,200만원, 500인시가 귀속된다. 사내 원본은 개별 승인된 시험 명단 계정만 열람할 수 있다."),
    "dispatch-planner": ("custodian", 35000000, 600, "need", False,
        "표에 쓰인 작업 시간과 가용 구역 조건을 도출한 가상 현장 조사의 직접 투입은 600인시, 3,500만원이다. 산술 계산 몇 줄에 이 비용을 부과한 것이 아니다. 보유 부서 외 취득 경로는 없고 배차 업무 역할에게만 접근을 허용하되 개인별 추가 승인은 없다."),
    "return-triage": ("difficult", 160000, 8, "need", False,
        "가상 협력 수리점에도 같은 익명 점검 요약이 제공돼 계약 확인을 거치면 취득 가능하지만 일반 게시 자료는 아니다. 여덟 건의 점검 정리 투입은 8인시, 16만원이다. 사내에서는 반품 점검 업무 역할에만 읽기 권한을 부여했다."),
    "visitor-orientation": ("public", 30000, 2, "open", True,
        "체험 운영자가 동일 안내문을 예약 없이 접근할 수 있는 페이지와 입구 안내판에 게시했다. 안내 작성에 2인시가 들었다. 별도 부록 없이 공개한 전문이며 외부 배포가 허가되어 있지만 생산 시설의 출입 권한은 부여하지 않는다."),
}


def premises(setting):
    access, cost, hours, readers, release, _ = setting
    require(access in {"public", "custodian", "difficult"} and readers in {"open", "staff", "need", "named"}, "guide_setting_invalid")
    restricted = readers in {"need", "named"}
    return {"public_exact_body": access == "public", "obtainable_without_holder": access != "custodian",
            "ordinary_access_difficult": access != "public", "cost_krw": cost, "person_hours": hours,
            "economic_utility": True, "investment_scope_exact": True, "secrecy_manageable": readers != "open",
            "all_staff_knows": not restricted, "business_need_only": restricted,
            "individual_approval": readers == "named", "access_enforced": restricted,
            "release_authorized": release, "other_risk_present": False}


def build_reference():
    drafts = build_drafts() + new_drafts()
    require({d["family_id"].removeprefix("family-") for d in drafts} == set(SETTINGS), "guide_setting_coverage_invalid")
    docs, answers, details = [], [], []
    for draft in drafts:
        setting = SETTINGS[draft["family_id"].removeprefix("family-")]
        doc, answer, detail = build_case(draft, premises(setting), setting[-1])
        require(answer is not None, "guide_authored_setting_unresolved")
        docs.append(doc)
        answers.append(answer)
        details.append(detail)
    return docs, answers, details
