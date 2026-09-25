# 고객사 합성 문서 제작 — 네 번째 묶음 결과

2026-09-15. 새 메모·안내·절차48건을 추가해 고유 본문과 조건부 참조 답이164건이다.
기존116건의 본문/맥락/답/근거와 정책0.1을 보존했다. 팩 버전만0.4다.
외부 검수자 대기는 내부 집필의 전제가 아니다. 다만 **본문+명시한 합성 전제+내부 기준 아래의 답**을
실제 고객 문서 정답·사람 서명·GOLD200·고객 정책 승인으로 바꾸지는 않는다.

## 1. 수량

| 등급 | 기존 | 신규 | 누적 | 목표250까지 부족 |
|---|---:|---:|---:|---:|
| TS | 18 | 12 | 30 | 220 |
| S1 | 36 | 12 | 48 | 202 |
| S2 | 29 | 12 | 41 | 209 |
| S3 | 33 | 12 | 45 | 205 |
| 합계 | 116 | 48 | 164 | 836 |

메모16·안내16·절차16, 각각 네 등급4건씩을 별도로 집필했다. 원고를 숫자나 제목만 바꾸어 복제하지 않았다.
구체적인 공정/계측 조건, 사업 실험 결과, 소규모 상태 대조, 공개 기술 설명을 세 형식 모두에 배치했다.
비용은 해당 정보의 직접 취득·개발 투입으로 설정했으며 계약액·매출·설비가격·전체 사업비를 대신 넣지 않았다.
그 비용·공개·취득 경로·실제 접근 범위는 **명시적인 가상 설정**이지 실제 고객사 관측 사실이 아니다.
고비용 공개 결과/S3, 저비용 개별 승인/S1, 제3자 제한 경로 사례도 포함한다.
신규 본문248~288자는 아직 고객사 장문·복합 문서 분포를 대표하지 않는다.

## 2. 근거와 안전 경계

- 본문 근거328곳, 가상 맥락 사실 결합2,296곳. ID·해시·정책·규칙·인용 위치를 검증했다.
- 신규48건마다 실제 본문의 피연산자와 기재 결과를 읽어 검산했다. 기존63+새48=산술111항목이다.
  각 결과값을 바꾸면 실패한다. 이 검산은 전체 의미·실험 타당성·현실의 비밀성이나 권한을 인증하지 않는다.
- 산식27조합, HOLD4, 같은 본문에 다른 맥락을 주면 답이 달라지는164반례는 별도 진단이며 신규 문서가 아니다.
- S/V/M은 버전0.1 정책으로 계산한다. 운영v22·공개 API·분류 경로는 변경하지 않았다.
  V의 내부 임계치는 가이드 원문 수치나 고객 승인 수치가 아니며 이 버전에 한정한다.
- 입력에는 본문+가상 사실 맥락만 제공한다. 답/점수/규칙/해설/형식/계열 정보는 별도 파일이다.
- 본문만으로 등급 채점 가능한 문서는0건이다. 이 정답을 현 본문 전용 경로의 정확도 분모로 쓰지 않는다.
- `training_allowed/model_evaluation_allowed/gold_eligible/customer_accuracy_measured/human_signoff_created=false`.
  학습/평가 채택0, 모델 교체0. 문자 진단기만 교차검증 fold 안에서 적합했다.
- 전 직원용 S3 사례도 외부 배포 허가는false로 남겼다. 등급 산식 결과는 공개 허가가 아니다.

## 3. 형식별 등급의 빈칸 보완

형식 태그가 있는 누적144건은 다음과 같다. 초기20건의 태그/계열을 소급 수정하지 않았다.

| 형식 | TS | S1 | S2 | S3 |
|---|---:|---:|---:|---:|
| email | 3 | 7 | 5 | 6 |
| qa | 3 | 5 | 3 | 7 |
| log | 3 | 6 | 9 | 3 |
| table | 3 | 4 | 2 | 3 |
| memo | 6 | 12 | 4 | 4 |
| notice | 4 | 4 | 7 | 9 |
| procedure | 4 | 6 | 5 | 7 |

7형식 모두에 네 등급이 존재한다. 하지만 수량이 같아진 것은 아니며 내용·문체 연관성도 남는다.
새48건의 형식 최빈 대입은25%(전체 최빈 기준선25%), 누적144건은55/144=38.19%(기준선30.56%)다.
이전96건의43/96=44.79%와는 분모·등급 분포가 달라 **통제된 개선 수치가 아니다**.
`all_annotated_formats_span_four_grades=true`와 `format_bias_resolved=false`를 함께 기록했다.
말머리/안내 대상/1·2·3단계 표지 확인은 장르 검사일 뿐 자연스러움이나 계열 독립성 보증이 아니다.

## 4. 의미상 비슷한 사례의 별도 묶음 후보

문장/숫자 중복이 없다는 사실만으로 모든 문서가 독립이라는 결론을 내리지 않는다.
이번에는 본문에서 공통 설계·업무 흐름을 읽고 네 묶음18건을 별도 후보로 기록했다.

| 묶음 후보 | 건수 | 이유 |
|---|---:|---|
| 두 군 비교 실험 | 7 | 제안·교육 순서를 달리 배정하고 중간 결과를 비교 |
| 두 목록의 합집합 | 4 | 공통 식별자를 한 번 제외하고 고유 수와 횟수를 구분 |
| 사진 식별·재촬영 대응 | 2 | 원본·이름 변경·재촬영을 연결하고 서로 구별 |
| 공용 공간·물품 배치 | 5 | 위치·수량·이동·이용 가능 상태를 대조 |

동일 계정을 두 번 면담한 기존 `renewal-interview`는 두 군 실험이 아니므로 첫 묶음에서 제외했다.
이는 네 개의 선택된 업무/추론 패턴만 보는 **작성자 제안**이다. 중복 확정·독립성 인증·최종 분할 규칙이 아니다.
원고 메타데이터를 수정하지 않고 `audit/semantic_family_proposals.json`에 문서ID/본문해시/이유를 결합했다.
기존 연결 성분을 나누지 않고 후보 관계를 추가로 합치기만 한다. 현재164성분은 이 후보에서150성분이 된다.
누락 멤버/중복 멤버/빈 묶음/중복 이름을 거절하고, 전이 연결 및 기존 연결 보존을 테스트했다.
본문과 라벨을 그대로 두고 이 후보 묶음만 반영한 계열 CV도 별도로 측정한다.

char2~5 TF-IDF/LinearSVC,5seed(0~4),5fold로 측정했다. 어휘/IDF는 각 학습 fold 안에서만 적합했다.

| 입력 뷰 | 층화 CV | 라벨 섞기 | 계열 CV | 계열 라벨 섞기 | 20%p 초과 경고 |
|---|---:|---:|---:|---:|---|
| 본문 | 62.32% | 30.49% | 61.71% | 28.90% | 예 |
| 제목 | 47.80% | 28.17% | 48.17% | 29.76% | 아니오 |
| 맥락 | 72.20% | 27.56% | 70.98% | 27.80% | 예 |
| 본문+맥락 | 72.20% | 30.00% | 72.93% | 30.12% | 예 |

18건의 업무/추론 패턴 후보를 합친 본문 계열 CV는58.29%, 라벨 섞기30.37%이며 경고가 남았다.
이는 묶음 제안에 대한 민감도이지 진짜 독립 평가 성능이 아니다. 임의의 묶음으로 점수를 낮춘 것을 개선으로 발표하지 않는다.
이전116건과 비교하면 본문 경고가 다시 발생했다. 서로 다른 분모이므로 수치 차이 자체는 통제된 악화량이 아니지만,
**형식별 네 등급 존재만으로 단어·문체·업무 주제 연관성이 해소되지 않았다는 경고**다.
맥락은 필요한 정책 입력이므로 높은 점수가 곧 금지된 답 누설은 아니며, 제목의 경고 미초과도 누설 없음 증명은 아니다.
어떤 값도 고객 모델 정확도나800/200 예상 일치율이 아니다. 경고선은 진단 기준이지 승인된 성능 합격선이 아니다.

추가 원인 탐색으로 `inspect_body_cues.py`를 실행했다. 새48건만의 본문 CV72.92%, 라벨 섞기28.75%였다.
전체164건에 맞춘 선형 진단기의 양의 계수 상위 단어 조각에는 S3의 `니다.`, S2의 `번호`, TS의 `구간` 등이 있었다.
`니다.`는 S3의32/45건, TS의10/30건, S1의17/48건, S2의14/41건에 등장했다.
이 계수/출현 수는 전체 자료에 대한 탐색적 연관성으로, 교차검증 설명이나 인과적 원인 확정이 아니다.
실질 내용의 단어도 등급과 상관될 수 있으므로 점수를 낮추려고 사실 단어를 삭제하거나 답/가정을 조작하지 않는다.
현재48건을 품질 합격 학습셋으로 승격하지 않고, 다음 집필보다 먼저 말투·업무 주제 교차 설계와 원본 보존 진단을 보완한다.

## 5. 현재 검증 범위

- 누적164건의 정확/숫자 정규화/문자5-gram Jaccard≥0.85 중복0, 알려진fixture 본문해시 일치0.
- 기존404JSONL의 지원 본문451,663행과 정확/숫자 정규화 일치0. 행 수는 고유·학습 적격 문서 수가 아니다.
- 미지원 본문1,850행/파싱 실패14행 때문에 `coverage_complete=false`다.
  오류는 기존 `datasets/gold_real/uncertain_cases.jsonl`에 있으며 원본을 고치지 않았다. 외부 풀 전체 의미 중복 검사는 아니다.
- 빈 외부 풀/지원 본문0/외부 중복/토큰 초과는 팩 생성 전에 실패한다.
- 저장된 토크나이저 기준 본문104~225, 본문+맥락368~491토큰으로164개 모두512안. 원본 입력을 자르지 않았다.
  활성 서빙모델 확인이나 실제 서빙 입력 경로 검증은 아니다.
- 신규93개, 관련1,668개, DB/torch/transformers import를 막은 독립1,310개 테스트 통과. 부분집합이므로 수를 합산하지 않는다.
- 신규 소스/시험3개와 결과 폴더의 두 진단·수집기 Ruff 통과. 테스트 통과와 데이터 편향 해소를 혼동하지 않는다.
- 원본406JSONL 변화0.9/14 보존 검사에는 이전 `policy_engine.py` 변경이 여전히 남아 exit1이다.
  이번 실패를 숨기거나 과거 baseline을 다시 설정하지 않았다. 전체 저장소/운영 E2E 통과 주장은 하지 않는다.

## 6. 산출물과 재현

원고 `poc/scripts/customer_guide_batch04.py`, 제작/검증/진단 `poc/scripts/build_customer_guide_batch04.py`,
회귀검사 `poc/tests/data_quality/test_customer_guide_batch04.py`.
동결된 이전 제작기/정책의 소스 지문을 보존하기 위해 이전 파일은 리팩터링하지 않았다.
새 제작기는 기존 원고 컴파일 계약을 재사용하며 버전별 검증기를 별도로 둔다.

팩: `poc/reports/CUSTOMER_GUIDE_BATCH04_20260915/reference_v0_4/` —185payload+manifest.
본문 `documents/*.txt`, 입력 `inputs/body_context.jsonl`, 답 `answers/answers.candidate.jsonl`,
근거 `answers/evidence.jsonl`, 읽기용 `answers/REFERENCE_ANSWERS.md`, 검사를 분리했다.
같은 상위 폴더에 baseline/세 테스트XML/문자진단/보존검사/증거수집기/최종증거 요약을 둔다.

- 정책 SHA256: `e3aa2854120397342574c2487a46c83a4b74b9108c706bbc3c4b47c8dfbe1ac9`.
- manifest SHA256: `dcc8ea80ad4e3ff6fadf21a4debe20212ac015a2892ebd444cdee73917a07f41`.
- 토크나이저 SHA256: `f33819f6e8544c27450ebe253b3a882d9c2a7148d4f0ce15129425712a9993be`.
- HEAD: `83c88e9f5833c61d01d36276e415be5bc24987df`. 추가 커밋/푸시는 하지 않았다.

다음 명령은 `F:\antigravity\rag\poc`에서 실행한다. 기존 출력은 덮어쓰지 않으므로 재생성에는 새 경로를 사용한다.

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -B scripts/build_customer_guide_batch04.py prepare --out reports/CUSTOMER_GUIDE_BATCH04_20260915/replay_new --parent-pack reports/CUSTOMER_GUIDE_BATCH03_20260915/reference_v0_3 --corpus-root datasets --tokenizer artifacts/classifier_p1_v5_clean/v-fe4b386b/tokenizer.json
.\.venv\Scripts\python.exe -B scripts/build_customer_guide_batch04.py verify --pack reports/CUSTOMER_GUIDE_BATCH04_20260915/reference_v0_4
.\.venv\Scripts\python.exe -B scripts/build_customer_guide_batch04.py audit --pack reports/CUSTOMER_GUIDE_BATCH04_20260915/reference_v0_4 --out reports/CUSTOMER_GUIDE_BATCH04_20260915/shortcut_replay_new.json --seeds 5
.\.venv\Scripts\python.exe -B -m pytest --confcutdir=tests/data_quality tests/data_quality/test_customer_guide_batch04.py -q
.\.venv\Scripts\python.exe -B -m ruff check scripts/customer_guide_batch04.py scripts/build_customer_guide_batch04.py tests/data_quality/test_customer_guide_batch04.py
```

관련 검증 명령:

```powershell
$env:TESTING='1'
$env:DATABASE_URL='postgresql+psycopg://test:test@127.0.0.1:9/test'
.\.venv\Scripts\python.exe -B -m pytest tests/data_quality tests/test_content_reference.py tests/test_content_reference_revision.py tests/test_content_reference_review.py tests/test_classification_review_workflow.py tests/test_classification_pilot_intake.py tests/test_trainer_sample_weights.py tests/test_trainer_fnr_metrics.py tests/test_eval_p1_model_gold_source_prior.py tests/test_proxy_training_finalization.py tests/test_proxy_model_comparison.py tests/test_serving_eval.py tests/test_policy_engine.py tests/test_policy_engine_defects.py tests/test_policy_rule_checker.py tests/test_org_mapping.py -q --tb=short
```

`collect_evidence.py`는 이 실행 결과/이전 팩/보호 파일을 읽어 `evidence-final.json`을 새로 생성하며 덮어쓰지 않는다.
`inspect_body_cues.py`도 동일 방식으로 검증된 팩을 읽어 `body_cue_diagnostic.json`을 생성한다.
재현 명령은 `.\.venv\Scripts\python.exe -B reports/CUSTOMER_GUIDE_BATCH04_20260915/inspect_body_cues.py`이며
이미 있는 결과를 덮어쓰지 않는다. 원본 팩과 결합된 지문/실행 결과를 그대로 보존한다.
팩 검증의 소스 재생성·해시 일치는 과거 관측 보고서의 현실 진위를 인증하는 전자서명이 아니다.
`reports/`는 ignore 대상이고 로컬 저장은 외부 백업이 아니다. 사용자 홈의 전역 메모리는 변경하지 않았다.

## 7. 다음 작업

1. 이번에 다시 발생한 본문 경고부터 보완한다. 문체/주제 교차 작성 명세, 같은 사실을 유지하는 별도 진단 변형본,
   더 넓은 의미 계열 후보를 준비한다. 변형본은 신규 문서 수에 더하지 않으며 원본/답/기준을 성능에 맞춰 바꾸지 않는다.
2. 이 진단을 바탕으로 부족836건 집필·선별을 이어간다: TS220/S1 202/S2 209/S3 205.
   형식 빈칸 이후에는 주제·문체·길이 편향과 의미상 비슷한 사례가 우선이며, 이번18건만으로 계열 검토가 완료된 것은 아니다.
3. 고객 시험의 본문+맥락 입력 연결과 장문 처리 계약을 별도로 준비한다. 장문을 앞부분만 잘라 정답과 대조하지 않는다.
4. 충분한 원고·근거 검산 후 의미 계열/근접 중복을 포함한 분할 규칙으로 등급별200/50, 총800/200을 한 번 분리·동결한다.
   현재 묶음 후보를 승인된 계열로 간주하거나 유리한 평가 분할을 반복 선택하지 않는다.
5. 전체200건 분모로 일치율·등급별 재현율·심각한 하향분류·보류·오류·누락을 분리 보고한다.
   합성 내부 기준 일치율과 실제 고객 문서 정확도는 구별하며 운영 교체는 별도 결정이다.
