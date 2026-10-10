# 전문가 검수용 모의문서 1,000건

이 패키지는 원본 1,001건을 수정하지 않고 만든 별도 검수 배치다. 검수자에게는 `reviewer_documents.jsonl`만 제공하며, `internal_manifest.jsonl`과 `CURATION_MANIFEST.json`은 관리자 전용이다.

- 배치 수: 1,000건(20명 × 50건)
- 초과 1건: `MK-1001`은 review_id 안정 정렬 기준 마지막 행으로 제외했다. 이 선택은 등급·본문·품질과 무관하다.
- 교체 4건: 고정 상황문 메아리만 문맥 보존 편집했다. 상세는 `CURATION_MANIFEST.json` 참조.
- 품질 게이트: `QUALITY_AUDIT.json`의 `release_ready=true` 확인.

합성 문서는 생성기 일관성·회귀시험 및 전문가 검수용이다. 독립 검토와 잠금 홀드아웃 평가 전에는 실문서 정확도의 근거로 사용하지 않는다.
