# 별칭 제거 v1: 미채택 진단뷰

2026-09-15. 병렬 작업 A. 이 산출물은 `diagnostic_view_only`이며 **의미 보존 교정의 합격본이 아니다.**

```text
target_binding_not_certified = true
adoption_allowed = false
authoritative_parent_replaced = false
authoring_quality_status = AUTHORING_QUALITY_HOLD_TARGET_BINDING
```

원본260개가 계속 정본이다. 이번260개는 같은 문서 계열을 비교하기 위한 진단뷰이며 신규 원고도 채택된 학습자료도 아니다.
원본 기준 정책0.1·조건부 답안·출처·HOLD와 모든 학습/평가/GOLD 권한 제한을 보존했다.

## 1. 왜 만들었고 왜 채택하지 않는가

원본 batch06의64개에서 익명 영문 대상 코드가 TS 쪽에 집중되는 작성 신호가 발견됐다.
그 신호의 영향을 분리하기 위해 같은64개 부모에서 대상 별칭 문자만 제한적으로 제거했다.
변환 함수는 `transform(family_id, text)`이며 등급·답안·정책 점수를 인자로 받지 않는다.
대상별 정적 검토표와 원본 본문 해시를 먼저 고정하고 모델 점수를 보기 전에 변환을 동결했다.

그러나 별칭의 역할을 외형만으로 확정할 수 없다는 문제가 독립 검토에서 드러났다.
예를 들어 “이번 결과는 기질 배치 M에 한정”을 “기질 배치에 한정”으로 바꾸면 숫자·부정·제한 표현이 모두 남아도
**어느 배치인지 식별하는 값은 사라진다.** “분말 R은”, “구역 D는”의 제거도 문서 안의 특정 대상에서 일반 명사로 읽히는 위험이 있다.

따라서 본문 바깥에 부모 이력을 남겼다는 사실, 저장된 맥락·답안이 같다는 사실, 산술이 맞다는 사실로
새 본문의 대상 결합·전체 의미·등급 타당성이 유지됐다고 인증할 수 없다.
독립 검토 결과에 따라 현재 변환은 추가로 고쳐 점수를 맞추지 않고 **진단 전용으로 제한**했다.
원본을 대체하려면 별칭과 실제 대상·판본의 결합을 유지하는 별도의 설계와 검증이 필요하다.

## 2. 작성된 진단뷰와 변경 범위

| 항목 | 수량 |
| --- | ---: |
| 원본 정본 | 260 |
| 조사한 부모 패널 | 64 |
| 본문이 달라진 부모 | 16 |
| 패널 안에서 완전히 동일한 부모 | 48 |
| 패널 밖 그대로인 문서 | 196 |
| 제거한 별칭 발생 위치 | 18 |
| 보존한 단독 Latin 단위 문자 발생 위치 | 10 |
| 비교용 진단뷰 전체 | 260 |
| 신규 원고 / 학습·평가 채택 | 0 / 0 |

`표면 P`는 같은 문서에서2회 나타나 모두 결합해서 처리했다. 효소 문서의 `효소 E`와 `기질 배치 M`도 각각 조사했다.
다른 명사는 그대로 유지하며 두 대상을 하나의 명사로 합치지 않았다. `μL`, `μs`, `g`, `m` 안의 단독 Latin 문자는 단위로 보존한다.
규칙은 새로운 “해당/대상/첫째” 문구를 추가하지 않는다. 필요한 한국어 조사만 명사에 맞춰 바꾼다.

예:

| 원본 스팬 | 진단뷰 스팬 |
| --- | --- |
| 분말 R은 | 분말은 |
| 표면 P에 / 표면 P는 | 표면에 / 표면은 |
| 분말 W가 | 분말이 |
| 효소 E는 | 효소는 |
| 기질 배치 M에 | 기질 배치에 |
| 저장 경로 M은 | 저장 경로는 |
| 분할 K는 / 구역 D는 | 분할은 / 구역은 |

이 표는 허용된 문자열 실험의 범위이지 그 변경이 의미적으로 안전하다는 인증이 아니다.
미검토 본문·알 수 없는 Latin 스팬·단위 제거·명사 이외 문구 추가·같은 명사의 A/B 비교·변경 스팬 내부를 자르는 근거 경계는 거절한다.

## 3. ID·답안·근거 결합

변경된16뷰만 새 본문/맥락 해시로 결정론적 새 문서 ID를 받는다. 동일48뷰는 원래 ID·입력·답안·근거 전체를 그대로 유지한다.
패널 밖196개도 원본과 같다. 가족·시나리오·템플릿 ID는 바꾸지 않는다.

- 문서: 본문, 문서 ID, 입력 해시, 본문 인용/오프셋/해시만 변경 가능하다.
- 답안: 문서 ID와 입력 해시만 다시 연결한다. 등급·규칙·다른 등급 배제 설명을 재작성하지 않는다.
- 상세 근거: 문서 ID·입력 해시·본문 해시만 변경한다. 최초 작성 원고를 가리키는 `parent_draft_id`는 출처로 보존한다.
- 맥락·가상 사실·정책·해설은 원본 그대로다. 해설은 답안 sidecar이며 모델 입력이 아니다.

`audit/lineage.jsonl`에는 모든64개 부모를 기록한다. 필드는24개다.
FLAGS5개, 부모/자식 ID, 부모/자식 입력·본문·답안·상세 근거 해시8개, 정책/맥락 해시2개,
가족/시나리오/템플릿, 변경 여부, `repair_kind`, `edits`, `new_document_count`로 구성한다.
각 edit에는 원본·변경 스팬 위치와 `before/after/noun/alias/reason`이 있다.

진단뷰의 새 ID가 원본 노출 이력을 숨기지 않도록 같은 부모·가족으로 계속 연결해야 한다.
원본과 진단뷰가 서로 다른 학습/검증 fold에 들어가는 분할을 허용하지 않는다.

## 4. 자동 검증과 남은 경계

최종 신규 테스트 **162개 통과(28.39초)**, 신규 Python3개 ruff 통과.
테스트는64개 전체의 스팬 결합·스팬 밖 UTF-8 바이트·숫자/부정/조건 표현·기존 답안 필드·맥락/계열 보존과
64개 산술 결과 변조, 단위 보존, 조사, 다른 대상 비교 거절, 미검토 스팬, 입력/답안/lineage 재해시 변조,
채택 권한 조작·정본 교체 주장, 동결팩 내부 출력과 부모/소스 변경을 검사한다.

변환 본문에 대응하는 산술64개는 통과했다. 기존 산술 나열207개에 진단뷰64개를 더해 새 원고 수로 세지 않는다.
누적520개 본문 근거와3,640개 맥락 항목을 결합했다. 비교용260뷰 내부에서 기존 중복 검사 기준의 일치쌍은0개다.
외부 풀·의미 계열 전체 독립성·고객 문서 정확도를 증명한 결과는 아니다.

로컬 tokenizer로 본문/본문+맥락520뷰가 모두512토큰 이내이며 최대491토큰이다.
모델 추론·학습·실제 운영 연결은 이 작업에서 하지 않았다. 짝지은 진단과 독립 검산 결과는 통합 담당의 별도 산출물로 보고한다.
그 진단 점수가 좋아져도 현재 `target_binding_not_certified`는 자동으로 해제되지 않는다.

생성 시작의 소스 해시를 고정하고 쓰기 전·manifest 전·완료 때 다시 확인한다.
부모팩도 시작·쓰기 전·payload 뒤·최종 검증 뒤 확인한다. 기존 manifest 아래나 부모팩/tokenizer 디렉터리 안에는 쓰지 않는다.
부분 쓰기 후 부모/소스가 바뀌면 성공을 반환하거나 유효한 완성 manifest를 만드는 경로를 차단한다.

초기 whitelist 전달 과정에서 터미널 인코딩 손상이 발생했으나 원본 스팬 일치 검사에서 실패했다.
동결 전에 ASCII escaped JSON 전달과 명시적 UTF-8 출력으로 새 whitelist만 재구성했고 U+FFFD0을 확인했다.
손상된 상태의 팩은 생성하지 않았으며 기존 소스·문서·정책은 변경하지 않았다.

## 5. 파일·해시·재현

신규 파일:

- `poc/scripts/customer_guide_alias_repair_v1.py`
- `poc/scripts/build_customer_guide_alias_repair_v1.py`
- `poc/tests/data_quality/test_customer_guide_alias_repair_v1.py`

집필 자체 진단팩은 `poc/reports/CUSTOMER_GUIDE_REPAIR_20260915/authoring/diagnostic_views_v1`이다.
payload278개+manifest1개이며 manifest SHA256:

`8f5fd54580c49e285702b38ada8aa0c2c2c6774797981ff35ff880ba5a9983c0`

부모 `CUSTOMER_GUIDE_PARALLEL02_20260915/reference_v0_6`의 manifest SHA256:

`7e8ea7552e272f5391f1d95113176f955f8828352d38f45cfc24b979eb3c8f95`

변환 소스 SHA256: `cfb7912be5838bcd83bec914801986c3ce2e2c7a25ff0b7d43a3692a12192024`

빌더 소스 SHA256: `22bf9640e502862fb952996ccbb9d1618ecbfcdf82ab4be77df4687f1dc7e18f`

최종 시험 XML은 `authoring/tests-alias-final.xml`이다.

주요 payload 경로:

- `authoring/documents.jsonl`: 비교용 진단260뷰.
- `answers/answers.candidate.jsonl`, `answers/evidence.jsonl`: 원본 필드를 보존한 진단 참조.
- `inputs/body_context.jsonl`: 답안 필드가 없는 진단 입력.
- `parents/documents.jsonl`, `parents/answers.candidate.jsonl`, `parents/evidence.jsonl`: 원본64개.
- `audit/lineage.jsonl`, `audit/reviewed_aliases.json`, `audit/unchanged_segments.jsonl`: 부모·스팬·출처 연결.
- `COMPARISON.md`: 원본64개와 변경16개를 비교하는 읽기용 문서.

작업 위치 `F:\antigravity\rag\poc`:

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
.\.venv\Scripts\python.exe -B -m pytest --confcutdir=tests/data_quality tests/data_quality/test_customer_guide_alias_repair_v1.py -q
.\.venv\Scripts\python.exe -B scripts/build_customer_guide_alias_repair_v1.py verify --pack reports/CUSTOMER_GUIDE_REPAIR_20260915/authoring/diagnostic_views_v1
.\.venv\Scripts\python.exe -B scripts/build_customer_guide_alias_repair_v1.py prepare --out reports/CUSTOMER_GUIDE_REPAIR_20260915/authoring/reproduce_v1 --parent-pack reports/CUSTOMER_GUIDE_PARALLEL02_20260915/reference_v0_6 --tokenizer artifacts/classifier_p1_v5_clean/v-fe4b386b/tokenizer.json
```

생성 경로는 미존재 경로여야 한다. `core_payload()`는 `(docs, answers, payload)`를 반환하고,
`prepare(out, *, parent_pack=None, tokenizer=None)`·`verify(root)`로 생성·재검증한다. CLI 생성에는 부모팩이 필수다.
운영 API·생산 모델·원본 정본·정책·메모는 이 A 작업에서 변경하지 않았다.
