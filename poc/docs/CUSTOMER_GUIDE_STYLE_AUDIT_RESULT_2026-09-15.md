# 기존 48개 원고의 문체 민감도 진단 결과

2026-09-15. 결론: **종결어만 바꿔도 진단 분류기의 예측이 흔들렸고, 문체 균형 적합은 이를 줄였지만 해결하지 못했다.**
신규 문서 0개, 누적 164개(TS30/S1 48/S2 41/S3 45), 부족 836개 그대로다.
고객 모델 정확도·고객 GOLD·학습 800/평가 200 완성이나 배포 승인이 아니다.

## 범위와 구현

- 기존 batch04 전체 48개(등급별 12개)를 선택했다. 점수가 나쁜 일부만 고른 실험이 아니다.
- 원문은 불변. 평서/정중 96뷰 중 실제 변경 57개, 원문과 같은 대조 39개다. 신규 원고로 합산하지 않는다.
- 평서 변환은 13뷰/종결어 39곳, 정중 변환은 44뷰/281곳을 바꿨다.
- 단어 단위 종결어 목록만 변환하며 요청형 `주세요`, 제목, 숫자열, 조건, 부정, 가상 관리 맥락은 유지했다.
- 주장 근거 192곳의 인용문·위치를 다시 결합하고 산술 96회, 원본 조건부 정책 판단과 동일함을 검증했다.
- 일반적인 의역 도구가 아니며 전체 의미/화용 동등성은 인증하지 않는다. 해당 플래그는 false다.

구현: `poc/scripts/customer_guide_style_variants.py`, `poc/scripts/audit_customer_guide_style.py`.
회귀: `poc/tests/data_quality/test_customer_guide_style.py`.
작성 원칙: [문체·주제 교차 작성 명세](CUSTOMER_GUIDE_AUTHORING_CROSS_DESIGN_V0_1.md).

## 실험 설계

char 2~5-gram TF-IDF(max_features=30000) + LinearSVC(max_iter=5000), seed 0~4, 5-fold.
분할 모집단은 기존 164개이고, 의미 계열 후보 4묶음/18개를 반영한 150개 연결 그룹을 사용했다.
이는 선언된 후보 묶음이며 의미 독립성 전수 인증이 아니다.
평가 대상은 그중 48개 부모 문서다. 각 조건의 분모 240은 **48개 × 5seed**이며 독립 문서 240개가 아니다.
본문/본문+맥락 × 세 군 × 25fold = 진단 적합 150회. 생산 분류 모델은 학습하거나 교체하지 않았다.

세 군 모두 같은 부모/그룹 fold·라벨·seed를 사용한다. 원문군은 부모당 1뷰다.
복제 대조군은 모든 부모의 원문 2개, 균형군은 대상 부모의 두 말투와 나머지 부모의 원문 2개를 사용한다.
두 군은 행 수가 같고 각 행 가중치 0.5로 부모당 총 가중치 1을 유지한다.
IDF는 각 군의 학습 fold 안에서만 계산한다. 따라서 어휘 변화는 실험 변수지만 단순 부모 빈도 증가는 통제했다.
검증 부모의 다른 말투 또는 같은 선언 계열이 학습 fold로 넘어가지 않는다.

## 예측 변경 결과

아래는 각 군의 **원문 예측과 정중체 뷰 예측이 달라진 횟수**다. 오답률이 아니다.
제목·맥락·조건부 답은 같고 제한된 종결어만 달라진 비교다. 4개 정중체 동일 뷰도 전체 분모에 포함한다.

| 입력 | 원문 적합 | 복제 대조 적합 | 문체 균형 적합 | 복제 대조 대비 |
| --- | ---: | ---: | ---: | ---: |
| 본문 | 98/240 (40.83%) | 97/240 (40.42%) | 50/240 (20.83%) | 47회 감소, 19.58%p |
| 본문+맥락 | 37/240 (15.42%) | 36/240 (15.00%) | 25/240 (10.42%) | 11회 감소, 4.58%p |

평서↔정중 예측 불일치도 본문에서 복제 대조 100회→균형 52회, 결합에서 42회→31회였다.
원문→평서 변경은 본문 3회→2회, 결합 8회→6회였다. 변환 가능한 원문 수/변경량이 두 말투에서 다르다.
정중 변환의 조건부 참조 정답→오답은 본문 51회→19회, 결합 16회→7회였지만 오답→정답도 함께 발생했다.
단순 변경 수 감소만으로 정답 품질이나 고객 성능 개선을 주장하지 않는다.

## 조건부 참조 일치도와 부작용

다음은 진단 분류기의 조건부 답 일치도이며 고객 정확도가 아니다.
특히 본문 단독으로는 공개/관리 맥락을 알 수 없어 해당 라벨이 식별 가능하지 않다.

| 입력 | 적합 군 | 원문 | 평서 뷰 | 정중 뷰 |
| --- | --- | ---: | ---: | ---: |
| 본문 | 원문 | 65.42% | 66.25% | 50.00% |
| 본문 | 복제 대조 | 65.83% | 66.67% | 50.83% |
| 본문 | 문체 균형 | 65.83% | 66.67% | 66.67% |
| 본문+맥락 | 원문 | 71.25% | 70.00% | 70.83% |
| 본문+맥락 | 복제 대조 | 70.83% | 69.58% | 70.83% |
| 본문+맥락 | 문체 균형 | 70.00% | 67.92% | 74.58% |

**모든 지표가 좋아진 것은 아니다.** 결합 입력 원문/평서 일치도는 복제 대조보다 균형군에서 소폭 낮아졌다.
변형본을 다 넣으면 해결된다는 결론을 내리지 않는다. 주제·기술어·장문·요청형은 이번 실험 범위 밖이다.
이전 전체164개 CV 또는 새48개만 학습한 CV와 이번 164학습모집단/48평가 패널 수치는 직접 비교하지 않는다.
반복 seed는 독립 표본이 아니므로 신뢰구간이나 유의성·실고객 일반화 주장을 만들지 않았다.

## 검증과 보존

- 신규 189개 포함 관련 **1,857 passed**, 선택 의존성 차단 독립 **1,499 passed**. 부분집합이므로 합산하지 않는다.
- 독립 실행은 SQLAlchemy/psycopg/torch/transformers import를 차단했다. 실제 모델·DB 통합 검증은 아니다.
- 새 Python 3개와 결과 수집기 Ruff, 편집한 추적 메모의 `git diff --check` 확인.
- 기존 보호 파일 85개, 이전 동결 10팩/664 payload와 manifest 보존 검증은 `evidence-final.json`에 기록한다.
- 원본 406 JSONL 변화 없음. 9/14 보존 기준의 과거 `policy_engine.py` 변경은 exit 1로 그대로 남긴다.
- 새 96뷰의 본문 117~144/본문+맥락 382~410토큰. 각 96뷰, 총 192측정 모두 선택 tokenizer의 512 안이다.
  이는 전체164개 길이 분포나 활성 서빙 입력 검증이 아니다. 신규 외부 풀 중복 검사는 실행하지 않았다.
  이전 풀 검사의 미지원/파싱 실패로 전체 coverage=false라는 한계도 유지한다.
- 정책/원고/답안/운영 API·검수 경로·생산 모델 변경 없음. 커밋/푸시 없음. reports는 ignore이며 외부 백업은 미확인이다.

팩: `poc/reports/CUSTOMER_GUIDE_STYLE_AUDIT_20260915/paired_v0_1/` (10 payload + manifest).
manifest SHA256: `f4c057114977a9f8c46be0120055ded625888c5ca99e65ef68fe6dfcc05d3645`.
부모 manifest: `dcc8ea80ad4e3ff6fadf21a4debe20212ac015a2892ebd444cdee73917a07f41`.
정책 SHA256: `e3aa2854120397342574c2487a46c83a4b74b9108c706bbc3c4b47c8dfbe1ac9`.
tokenizer SHA256: `f33819f6e8544c27450ebe253b3a882d9c2a7148d4f0ce15129425712a9993be`.
소스 지문은 manifest/진단 JSON, 실행 환경 버전·fold·부모별 예측은 `paired_diagnostic.json`에 들어 있다.
`COMPARISON.md`는 48개 원문/두 말투를 비교한다. 이 파일의 답안/태그를 모델 입력으로 쓰지 않는다.

## 재현

작업 디렉터리 `F:\antigravity\rag\poc`. 출력은 기존 파일을 덮어쓰지 않고 새 경로를 사용한다.

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -B scripts/audit_customer_guide_style.py verify --pack reports/CUSTOMER_GUIDE_STYLE_AUDIT_20260915/paired_v0_1
.\.venv\Scripts\python.exe -B scripts/audit_customer_guide_style.py prepare --out reports/CUSTOMER_GUIDE_STYLE_REPLAY/paired_v0_1 --parent-pack reports/CUSTOMER_GUIDE_BATCH04_20260915/reference_v0_4 --tokenizer artifacts/classifier_p1_v5_clean/v-fe4b386b/tokenizer.json
.\.venv\Scripts\python.exe -B scripts/audit_customer_guide_style.py audit --pack reports/CUSTOMER_GUIDE_STYLE_REPLAY/paired_v0_1 --out reports/CUSTOMER_GUIDE_STYLE_REPLAY/paired_diagnostic.json --seeds 5
.\.venv\Scripts\python.exe -B -m pytest tests/data_quality/test_customer_guide_style.py -q
```

전체 관련 테스트의 범위·3종 XML·보존 검사·출력 지문은 상위 결과 폴더의 `evidence-final.json`을 참조한다.

## 다음 작업

1. 작성 명세에 따라 4분야×2말투×4사례, 32개 독립 원고의 소규모 교차 집필. 아직 작성 건수에 포함하지 않는다.
2. 원고 의미 계열 후보 확장과 사실/근거/산술 검산. 같은 부모 변형은 항상 같은 분할에 묶는다.
3. 품질 확인 후 부족836개 확장, 본문+맥락 시험 입력 연결·장문 계약을 준비한다.
4. 현재164개는 진단·작성 방향 선택에 이미 사용했으므로 미노출 최종200개로 전용하지 않는다.
   최종200개는 새 독립 계열에서 별도 제작·봉인하고, 그 예측으로 모델이나 원고를 고르지 않는다.

학습/평가 채택0, 본문 단독 등급 채점가능0, 모든 GOLD·허가 플래그false를 유지한다.
