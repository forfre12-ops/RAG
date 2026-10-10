# 합성 문서 반입 품질 감사

`scripts/audit_synthetic_documents.py`는 생성기 내부의 실시간 게이트를 대체하지 않는다.
이미 만들어진 KOIPA 합성 문서와 외부 DataGen JSON/JSONL을 **읽기 전용**으로 전수
감사해 검수·학습 반입 전의 결정을 재현 가능하게 남기는 도구다. LLM을 호출하거나 입력
문서를 수정·생성·삭제하지 않는다.

```powershell
python scripts/audit_synthetic_documents.py <json-or-directory> `
  --report <run-dir>/qa-report.json `
  --manifest <run-dir>/qa-manifest.json
```

검수자용 파일이 `review_id + text`만 포함하고 별도의 내부 manifest에 `true_grade`와
`doc_format`이 있는 경우, 아래처럼 서버측에서만 결합한다. 원본 검수자 파일에 정답은
절대 쓰지 않는다.

```powershell
python scripts/audit_synthetic_documents.py reviewer_documents.jsonl `
  --metadata-manifest internal_manifest.jsonl `
  --report qa-report.json --manifest qa-manifest.json
```

외부 생성기가 사용한 고유 프롬프트 문구는 다음처럼 grade-to-phrase JSON으로 별도 주입할
수 있다. 기본 어휘는 KOIPA에서 실제로 관찰한 좁은 상황문 메아리만 포함한다.

```json
{
  "TS": ["인수협상"],
  "S1": ["팀장급", "핵심 공정 노하우"],
  "S2": ["협상력 약화"],
  "S3": []
}
```

```powershell
python scripts/audit_synthetic_documents.py supplier.jsonl `
  --prompt-echo-lexicon observed-prompt-phrases.json `
  --report qa-report.json --manifest qa-manifest.json
```

## 결정값

- `keep`: 직접적인 문서 단위 위반이 없음. 이것만으로 실문서 성능이나 전문가 승인까지
  증명하지는 않는다.
- `replace`: 등급 자기진술/금지등급어, PII, 한국식 실명+직책, 상황문 메아리, 중복·반복
  템플릿이 있어 검수·학습 투입 전에 교체해야 한다.
- `exclude`: 본문·등급·문서유형 메타데이터가 없어 원본에서 복구하거나 재확인해야 한다.

`release_blockers`는 문서별 판정과 별도로 표시한다. 예를 들어 80% 이상의 문서가 같은
번호 매김 구조라면 각 문서는 `keep`일 수 있어도 배치 전체는 출고하지 않는다. 보고서에는
등급·문서유형·교차분포·구조분포, 문서별 원본 경로/행, 사유와 `report_sha256`이 남고,
manifest에는 입력 파일 SHA-256과 감사 보고서 SHA-256이 남는다.

한국식 실명+직책은 외부 NER 없이 일반 정규식만 쓰면 `구매팀 대리` 같은 역할 문구를
사람 이름으로 과탐한다. 따라서 이 감사기는 `김민수 부장`처럼 성+흔한 두 글자 이름+직책
형태만 교체 사유로 잡는다. 이보다 넓은 식별은 사람 검수 또는 별도 NER 검증으로 다뤄야
하며, 넓은 정규식으로 배치를 자동 폐기해서는 안 된다.

## 현재 한계

이 도구는 텍스트·메타데이터 정합성 검사다. 텍스트에서 실제 문서의 사실성이 충분한지,
정책 라벨이 옳은지, 합성 문서가 실문서 성능을 대표하는지는 판정하지 않는다. 이 세 항목은
독립 전문가 검수와 잠금 홀드아웃 평가에서 별도로 확인해야 한다.
