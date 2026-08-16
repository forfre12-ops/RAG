# 데이터셋 라이선스 리포트 — KOIPA AI 영업비밀관리시스템

작성일: 2026-05-30
대상: 사업단 + KOIPA 검수단
근거: [`dataset_v1.0.yaml`](dataset_v1.0.yaml) + 모델 라이선스 + 자체 생성 정책

## 1. 데이터셋별 라이선스 매트릭스

| 데이터셋 | 라이선스 | 출처 | 운영 사용 | 비고 |
|---|---|---|---|---|
| `synthetic_5k` | **MIT** (자체 생성) | noop provider (결정론적 mock) | ✅ | 본문은 키워드 boilerplate, 외부 IP 무관 |
| `synthetic_qwen3` | **Apache 2.0** | Ollama Qwen3 14B Q4_K_M 출력 | ✅ | Qwen3 Apache 2.0 → 출력 상업 사용 가능 |
| `synthetic_solar` | **CC-BY-NC-4.0** | Ollama Solar 10.7B 출력 | ⚠ 비상업만 | **운영 코퍼스 제외**, 비교 측정 한정 |
| `synthetic` | MIT | noop provider | ✅ | PoC dryrun 전용 |
| `labeled_5k` | MIT | `synthetic_5k` 파생 | ✅ | 룰 라벨러 검증 |
| `labeled_5k_plus_qwen3` | MIT + Apache 2.0 혼합 | `synthetic_5k` + `synthetic_qwen3` | ✅ | Apache 2.0이 가장 제한적 → 그 조건 준수 |
| `adversarial/golden_100.jsonl` | MIT | 사업단 자체 큐레이션 | ✅ | S9 적대적 FNR 게이트 |
| `p4_corpus` | MIT | 사업단 자체 생성 보일러플레이트 | ✅ | 발주처 실문서 도착 시 교체 |

## 2. 모델 라이선스 (참고)

| 모델 | 라이선스 | 출력물 사용 | 비고 |
|---|---|---|---|
| Qwen3 14B | Apache 2.0 | 상업 가능 | Tongyi Qianwen 약관 미적용 (Qwen3는 Apache) |
| Solar 10.7B | CC-BY-NC-4.0 | 비상업 한정 | 출력물도 동일 적용 → 운영 제외 결정 |
| KF-DeBERTa-base | MIT (kakaobank) | 가중치 재배포 가능 | 한국어 분류 1순위 |
| KURE-v1 | Apache 2.0 (NLP&AI Lab) | 상업 가능 | 임베딩 |
| BGE-M3 | MIT (BAAI) | 상업 가능 | 임베딩 1순위 |
| arctic-embed-l-ko | Apache 2.0 (Snowflake) | 상업 가능 | 비교 측정용 |
| dragonkue/bge-reranker-v2-m3-ko | MIT | 상업 가능 | reranker |
| KoBigBird-large | Apache 2.0 | 상업 가능 | 비교 학습 |

## 3. 공개 코퍼스 (D1~D6) — 미신청 상태

본 사업은 발주처 실문서 미제공 가정 자체결정으로, 공개 코퍼스 D1~D6 미신청 상태 유지.
필요 시 LloydK 명의로 신청 절차 자체 진행.

| ID | 코퍼스 | 라이선스 | 신청 상태 |
|---|---|---|---|
| D1 | AI Hub 한국어 문서분류 | CC-BY-NC | 미신청 |
| D2 | 국립국어원 모두의말뭉치 | 자체 라이선스 | 미신청 |
| D3 | KOIPA 가이드 v1 (자체 작성) | 공개 법령 기반 | ✅ doc/22 자체 작성 완료 |
| D4 | 공공행정 문서 (data.go.kr) | 공공누리 1유형 | 미신청 |
| D5 | 한국어 위키 (Wikipedia ko) | CC-BY-SA-3.0 | 미신청 |
| D6 | 영업비밀보호법 시행령 | 공개 법령 | ✅ 자체 수집 (doc/22) |

## 4. 운영 라이선스 정책

1. **Solar 출력 (`synthetic_solar`) 운영 코퍼스 제외**: CC-BY-NC-4.0 비상업 조건으로 KOIPA AI 영업비밀관리시스템(상업) 운영 사용 차단. 비교 측정용으로만 유지.
2. **Apache 2.0 모델 출력 운영 사용 시 NOTICE 동봉**: Qwen3·KURE-v1·arctic·KoBigBird 출력 활용 시 NOTICE 파일 동봉 의무 준수.
3. **합성 데이터 IP 보호**: 사업단 자체 생성분(`synthetic_5k`·`labeled_5k`·`golden_100` 등)은 MIT 공개, 단 KOIPA 사업 IP는 사업 계약 별도 규정 우선.
4. **발주처 실문서 도착 시 NDA 준수**: 발주처 실문서는 NDA 체결 후 `datasets/raw/koipa/`로 적재. 절대 git 커밋 금지, 별도 git-secret/암호화 storage 사용.

## 5. 검수자 체크리스트

- [ ] `dataset_v1.0.yaml`의 `license` 필드와 본 문서 §1 일치 검증
- [ ] `synthetic_solar` 운영 코퍼스 제외 정책 코드 반영 확인 (rag_indexer 인덱스 패턴에서 제외)
- [ ] Apache 2.0 모델 NOTICE 동봉 (`poc/licenses/NOTICE` 검증)
- [ ] 발주처 실문서 도착 시 NDA + storage 분리 절차 가동
- [ ] D1·D2·D4·D5 공개 코퍼스 추가 신청 의사 결정 (운영 전환 직전)

## 6. 변경 이력

- 2026-05-30 v1.0: 정본 신설 (B3 정본화 묶음)
