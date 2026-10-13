# 원장 밖 문서군·모델 입력 적합성 점검 결과

2026-09-15. 사용자 후속 '다음 진행'에 따른 내부 실험. 운영 모델/정책/원본/서명 변경 없음.

## 결론

**기존40건은 내부 정책의 고정 참조 답이지만 본문만 보는 모델의4등급 평가 정답으로 바로 사용할 수 없다.**
S2/S3 맥락 대조쌍10개는 본문 입력이 같아, 해당40행에 대한 본문 전용 결정형 분류기의 최대 정답률도75%다.
첫512토큰만 제시하면 다른 등급도 같은 입력으로 겹쳐 상한은52.5%로 내려간다.
이는 모델을 돌린 정확도가 아니라 **같은 입력별 가장 많은 정답 수를 합산한 이 유한 참조표의 상한**이다.
실제 고객 정확도, 현행 전체 서비스 정확도, 일반 모델 성능의 상한이 아니다.

따라서 다음 권고는 라벨 수를 먼저 늘리는 것이 아니라 **본문 사실추출 정답과 정책/맥락 적용 정답을 분리**하는 것이다.
고객 정책/관리 정보를 본문에 없는 사실로 만들어 넣거나, 가상 상황의 등급을 본문 모델에 강요하지 않는다.

## 구현과 산출물

- [문서군 확대·조건·제외·후속 게이트 설계](REFERENCE_DOMAIN_EXPANSION_V0_2_DRAFT.md).
- `poc/scripts/short_reference_probes.py`: 짧은 기술 설명/거래 메모/업무 운영 원고9종, 맥락 결합21사례.
- `poc/scripts/measure_reference_input_fit.py`: 근거/맥락 바인딩, 실제 토큰·잘림·노출 범위·동일 입력의 라벨 충돌 측정.
- `poc/tests/data_quality/test_reference_input_fit.py`: 신규48개 검사.
- 결과: `poc/reports/REFERENCE_INPUT_FIT_20260915/measurement_v0_2/`.
  `inputs.draft.jsonl`, `annotations.draft.jsonl`, `visibility.jsonl`, `report.json`, 문서21개와 manifest.

새21건은 예상 등급18건(TS1/S1 2/S2 4/S3 11)+HOLD 예상3건이다.
본문9종×공개/비공개 맥락18건에 범위unknown3건을 더했다. 독립 문서21종/균형 평가셋으로 세지 않는다.
`design_hypothesis_not_fixed`, `grade_oracle_implemented=false`. **새 고정 정답 추가0건**, 기존40건은 그대로다.
이유는 외부 검수자의 부재가 아니라 새 자연어 문서군의 의미/정책 oracle이 아직 구현되지 않았기 때문이다.

## 측정 조건

로컬 과거 ablation 아티팩트의 토크나이저 `artifacts/classifier_p1_v5_clean/v-fe4b386b/tokenizer.json`을 선택했다.
활성 운영 모델이라고 확인한 것은 아니다. 가중치를 읽거나 forward를 실행하지 않았다.
tokenizers 0.22.2, 실제 저장된 vocabulary/normalizer/postprocessor 사용, max_length512, 특수토큰 포함.
현행 normalizer 기본값(PII 마스킹 없음)과 현행 v1 청크 분할을 파일에서 직접 재사용했다.
문자 청크1536/문자 겹침64, 토큰 오버플로 stride64를 명시했다. 설정/구현의 현재 지문을 저장했다.

현재 서비스는 본문 정규화 뒤 `inference.run(text=cleaned, metadata=eff_meta)`를 호출하지만,
모델 본체 `_run_model(text, return_evidence)`에는 본문만 전달된다.
외부 메타데이터에 의한 별도 정책/보정 및 기존 확인 라벨 경로는 이 측정 대상이 아니다.
추론은 청크 확률 평균/상위등급 max를 결합한다. 이 집계가 문서 전체의 인원 JOIN을 재현하는지는 미측정이다.
학습기는 옵션에 따라 train을 청크 확장하되 val/test 문서 단위 토큰화는 max_length로 자른다.

측정 프로필은 아래와 같다. **현행 서비스 전체 실행이나 특정 학습 데이터셋 재생이 아니다.**

| 프로필 | 이번에 측정한 입력 |
|---|---|
| `train_body_first` | 정규화한 진단용 본문의 앞512토큰. 특정 학습 실행의 입력이었다고 주장하지 않음 |
| `serving_body_truncated` | 현행 문자 청크마다512토큰, 오버플로 미지원/실패 시나리오 |
| `serving_body_overflow` | 현행 문자 청크를512토큰/stride64 오버플로 창으로 분할 |
| `proposed_body_context_first` | 본문 뒤에 가상 맥락 JSON을 붙인 **새 직렬화 제안**의 앞512토큰. 운영에 적용하지 않음 |

AutoTokenizer(local_files_only=True)와 tokenizers backend의 입력ID도23개 텍스트에서 비교했다.
짧은21건+긴 원장1건+그 원장 앞1536자에서 전체/첫512/오버플로512·stride64가 모두 일치했다.
HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE을 설정했으며, tokenizer 길이 경고는 forward 실행을 뜻하지 않는다.
배치 패딩, 실제 GPU 실행, fallback 예외, 실제 운영 설정/현재 모델의 동일성까지 검증한 것은 아니다.

## 기존 고정40건 결과

원장 본문은 선택한 tokenizer에서 **81,461~81,690토큰**이었다.

| 입력 | 본문 토큰 위치 전체 노출 | 필요한 맥락 포함 | 동일 입력 충돌로 계산한 상한 |
|---|---:|---:|---:|
| 앞512토큰 | 0/40 | 0/40 | 21/40 = 52.5% |
| 청크별512, 오버플로 없음 | 0/40 | 0/40 | 29/40 = 72.5% |
| 청크+오버플로 | 40/40 | 0/40 | 30/40 = 75.0% |
| 본문 뒤 맥락 추가, 앞512 | 0/40 | 0/40 | 21/40 = 52.5% |

오버플로는241~242창, 청크별 잘림은81창이었다.
전체 위치 노출은 각 창의 합집합 기준이다. 한 창에 전체 근거가 들어간다는 뜻은 아니며 의미 이해도 증명하지 않는다.
긴 본문 뒤에 맥락을 붙이는 것만으로는 맥락 잘림 문제가 해결되지 않는다.
모든 표 행이 노출되어도 다른 창의 인물키를 연결하고 고유 인원을 세는 일은 별도 검산/집계가 필요하다.

## 새 짧은21사례 결과

- 본문만53~75토큰, 본문+가상 맥락149~171토큰. 모두512토큰 한 창에 들어갔다.
- 본문만 입력하면 전체 본문은 보이지만 필수 맥락은 빠진다: 필요한 입력 모두 노출0/21.
- 가상 맥락까지 포함한 제안 관점에서는21/21의 기재된 입력 요건이 노출됐다. UNK 없음.
- 3건은 범위unknown이 **그대로 제시된 HOLD 예상**이다. unknown이 입력됐다고 등급을 확정하지 않는다.
- 예상 등급18행 중 본문만 같고 예상 등급이 다른7쌍이 있다. 본문 전용 진단 상한은11/18=61.11%다.
  이 수치는 **미확정 예상 라벨에 대한 입력 충돌 진단**이지 참조 정답/고객 정확도 측정이 아니다.
- 맥락 포함 입력은 등급18행에서 충돌0이다. 모든 입력이 구별된다는 뜻일 뿐 모델 정확도100%가 아니다.

정규화로 비공백 내용이 사라지거나 UNK 토큰이 있으면 단순 offset coverage만으로 관찰 완료라고 하지 않는다.
근거 문자열의 위치가 모호해지거나 청크의 원문 위치를 정확히 연결하지 못해도 완료로 처리하지 않는다.
이 역시 의미 이해·완전한 누설 검증을 대신하지 않는다. 원고의 장르/고정 표현에 따른 지름길은 아직 평가하지 않았다.

## 검증·보존

- 신규48 포함 확대 **1,134 passed**, 독립 경량 **776 passed**, 실패/오류/건너뜀0. 포함 관계이며 합산하지 않는다.
- 경량 시험은 sqlalchemy/psycopg/transformers/torch import를 차단하고 작은 문자 tokenizer로 계약 동작을 검사했다.
  실제 토큰 수는 별도 실행 산출물이며 경량 테스트가 실제 모델 tokenizer를 검증했다고 하지 않는다.
- ruff 통과. 초기 작은 max_length 테스트3건은 기본 overlap64와 충돌해 실패했고 시험 인자를8로 고쳤다.
  경계 검사는 유지하고 잘못된 overlap 입력이 실패하는 테스트를 추가했다.
- 이번 시작 기준 핵심 소스·정책·토크나이저45개 해시 유지. 원본JSONL406개와 기존 팩13/91/45개 파일 유지.
- 새 팩25개 파일 manifest 해시 일치. 최종 증거 `poc/reports/REFERENCE_INPUT_FIT_20260915/evidence-final.json`.
- 9월14일 baseline의 `policy_engine.py` 과거 변경은 계속 CHANGED_REQUIRES_REVIEW다. 이번 변경으로 오인하거나 baseline을 덮어쓰지 않았다.
- 학습·운영/API·모델·DB·원라벨·고객 정책/서명·deny registry는 변경하지 않았다. 커밋/푸시 없음.

새 자료와 기존40건은 policy_fixture·학습/모델 평가 금지다. 새 복사본을 메타데이터까지 제거했을 때의
전수 사용 차단, 전체 학습풀 중복/근접중복, 전체 본문 지름길 검사와 문서군 일반화는 여전히 남아 있다.
reports는 Git ignore 상태이므로 로컬 결과 저장을 외부 백업으로 보지 않는다.

## 다음 작업

1. **본문 사실추출 정답**: 기술의 입력/상수/연산/검증값, 거래의 제시가/하한/지급 조건 관계,
   업무의 장소/배정/시각/빈 칸을 구조화하고 본문 위치와 결합한다. 확신도/등급에서 역산하지 않는다.
2. **각 문서군의 검산기**: 산술 전수검산·불완전 반례, 가격과 조건의 관계, 빈 서식과 누락 구별.
   사실과 근거 정답이 먼저 일치해야 조건부 등급 참조 답을 고정한다. 외부 사람 서명 대기를 선행조건으로 두지 않는다.
3. **정책/맥락 대조검산**: 본문 사실은 그대로 두고 가상 맥락/정책을 바꿀 때만 등급/HOLD가 바뀌는지 확인한다.
   장문 인원 집계는 정책 검산용으로 분리하며 기존 분류기에 입력만 늘리는 방식을 우선하지 않는다.
4. **누설·중복·확대**: 전체 내용 지름길과 학습풀 중복을 검사하고, 조건 충족 사례만250건씩 목표에 채택한다.

즉 새21건은 1,000건 정답지의 완성이 아니라, 그 정답지를 모델과 정책에 맞게 만들기 위한 입력/문서군 시험이다.
이 단계의 완료를 실제 문서 분류 품질 향상이나 사업 방향 승인으로 해석하지 않는다.

## 재현 명령

`poc`에서 새 출력 경로로 실행한다. 이미 존재하는 출력은 거절한다.

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
.\.venv\Scripts\python.exe -B scripts/measure_reference_input_fit.py --ledger-pack reports/INTERNAL_FIXED_REFERENCE_20260915/pilot_v0_1_final --tokenizer artifacts/classifier_p1_v5_clean/v-fe4b386b/tokenizer.json --out reports/REFERENCE_INPUT_FIT_20260915/replay_new
.\.venv\Scripts\python.exe -B -m pytest --confcutdir=tests/data_quality tests/data_quality/test_reference_input_fit.py -q
.\.venv\Scripts\python.exe -B scripts/build_internal_reference.py --verify reports/INTERNAL_FIXED_REFERENCE_20260915/pilot_v0_1_final
```

확대 회귀 범위는 이전 내부고정40건 회귀 명령과 같고 `tests/data_quality`에 신규48개가 포함됐다.
현재 생성 팩 manifest SHA256: `3d6640dc63d66868b988a028af6d37343d0ab86279db2ef6b8839c5555b1a349`.
소스/토크나이저 지문은 report/manifest, 검사·보존 지문과 수치는 상위 evidence-final.json에 저장했다.
