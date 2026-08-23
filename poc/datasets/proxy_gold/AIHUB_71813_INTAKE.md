# AI-Hub 71813 학습 전용 반입 절차

이 경로는 AI-Hub 71813 `멀티모달 정보검색 데이터`를 고객사 실문서에 가까운
공개·민간 문서 형식의 **학습 입력**으로만 쓰기 위한 오프라인 반입 경로다.
다운로드, 로그인, 승인 요청은 자동화하지 않는다.

공식 데이터 페이지는 PDF 20,123개와 페이지 단위 TXT·JSON 75,684개를 설명한다.
AI-Hub 이용정책은 데이터를 AI 모델 학습용으로만 사용하고, NIA 사업 결과임을
밝히며, 승인받지 않은 제3자 제공·양도·대여·판매와 별도 합의 없는 국외 반출을
금지한다. 실제 적용 범위는 승인 당시 약관과 수령자별 승인서를 우선한다.

공식 근거:

- 데이터셋: <https://www.aihub.or.kr/aihubdata/data/view.do?aihubDataSe=realm&currMenu=115&dataSetSn=71813&topMenu=100>
- 이용정책: <https://www.aihub.or.kr/intrcn/guid/usagepolicy.do>

## 선행 조건

1. 승인받은 담당자가 AI-Hub에서 직접 신청·승인·다운로드한다.
2. 압축 해제 원문은 폐쇄 저장소에 둔다. 계정·비밀번호·원문은 저장소에 커밋하지
   않는다.
3. 승인 화면 또는 승인 문서를 별도 증빙 파일로 저장한다.
4. `aihub_71813_approval_receipt.template.json`을 복사해 실제 값으로 채운다.
   템플릿은 의도적으로 승인값이 `false`이고 자리표시자를 포함하므로 그대로는
   통과하지 않는다.
5. 증빙 파일 SHA-256을 계산해 `receipt_evidence.sha256`에 기록한다. 증빙과
   영수증 JSON은 추출 원문 밖의 같은 디렉터리에 둔다.

PowerShell 예시:

```powershell
Get-FileHash -Algorithm SHA256 .\approval-evidence.pdf
python scripts/intake_aihub_71813.py `
  --source-root D:\secure\aihub-71813-extracted `
  --approval-receipt D:\secure\approval\receipt.json `
  --output-root D:\secure\aihub-training-runs `
  --run-id aihub-71813-v1-20260808
```

## 실행 결과와 안전장치

- 네트워크 호출 없이 로컬 PDF/TXT/JSON만 탐색한다.
- JSON의 `raw_data_info`, `source_data_info`, `learning_data_info`를 이용해 페이지와
  원문을 연결한다. 이름이 모호하거나 구조가 틀린 항목은 보류한다.
- PDF·TXT·JSON 원본 해시, 문서 계열 해시, 정규화 본문 해시와 페이지별 문자
  구간을 보존한다.
- 동일 페이지와 동일 후보 본문을 해시로 제거하고, 한 문서 계열을 유지한 채
  1,200~3,200자로 묶는다.
- 강한 주민·외국인등록번호 패턴이 발견된 문서 계열은 출력하지 않는다. 실제
  개인정보가 발견되면 이용정책에 따라 사용을 멈추고 AI-Hub 신고 및 원본 삭제
  절차를 수행해야 한다. 이 스크립트는 사용자 원본을 임의 삭제하지 않는다.
- 출력 레코드는 `training_use_permitted=true`이지만 `evaluation`, `golden set`,
  `redistribution`, `third-party access`, `foreign transfer`, `dataset sale`은 모두
  `false`다. 공통 코퍼스 검증기도 이 필드를 강제한다.
- `inventory.jsonl`, `records.jsonl`, `manifest.json`, `COMPLETE.json`을 새 run
  디렉터리에 기록한다. 같은 run ID는 덮어쓰지 않는다.

## 사용 한계

이 데이터는 실제 문서 형식 학습에는 유용하지만 S3 공개문서이므로 TS/S1 정답을
만들지 않는다. 고객사 실운영 정확도나 1,000건 골든셋의 근거로 사용할 수 없고,
학습 후 모델 평가에는 별도의 비중첩 평가셋을 사용해야 한다. 승인 증빙 검사는
필드와 파일 해시의 일치 여부를 확인할 뿐, 발급기관 진위 감정이나 법률 의견을
대체하지 않는다.
