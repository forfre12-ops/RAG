# Phase 5 — Qwen3 vs Solar 한국어 합성 비교

5070 Ti 풀가동 PoC의 Phase 5. 야간 무중단 진행으로 Qwen3-14B-Q4 + Solar-10.7B-Q4 각 200건 합성 후 라벨 일치도·FNR·품질 비교.

- 일자: 2026-05-30 야간 ~ 새벽
- 환경: onprem-local + Ollama
- 합성 스크립트: `poc/scripts/p3_generate_synthetic.py`
- 자동화 스크립트: `scripts/overnight_solar_after_qwen3.sh`

## 측정 결과 (자동 채워질 부분)

### Qwen3-14B-Q4

| 지표 | 값 | 합격선 |
|---|---|---|
| 합성 건수 | TBD/200 | — |
| 라벨 일치도 | TBD | ≥ 90% |
| FNR (TS·S1 미탐) | TBD | ≤ 5% |
| PII 위반 | TBD | 0 |
| JSON 파싱 실패 | TBD | 0 |
| 평균 생성 시간 | TBD | — |
| 등급별 분포 | TBD | — |
| 도메인별 분포 | TBD | — |

### Solar-10.7B-Q4

| 지표 | 값 | 합격선 |
|---|---|---|
| 합성 건수 | TBD/200 | — |
| 라벨 일치도 | TBD | ≥ 90% |
| FNR (TS·S1 미탐) | TBD | ≤ 5% |
| PII 위반 | TBD | 0 |
| JSON 파싱 실패 | TBD | 0 |
| 평균 생성 시간 | TBD | — |

### 1순위 자동 확정 (사전 합의 결정 1)

```
if abs(qwen3_kappa - solar_kappa) <= 0.05:
    winner = "Qwen3" (라이선스 더 명확)
elif solar_kappa > qwen3_kappa + 0.05:
    winner = "Solar"
else:
    winner = "Qwen3"
```

**1순위 모델**: TBD
**사유**: TBD

## 핵심 발견 (자동 분석 후 작성)

TBD

## 데모 갱신

`poc/src/lloydk/api/static/app.js` §5 capability stats:
- "P3 Qwen3 라벨 일치도 X%" src=measured
- "P3 Solar 라벨 일치도 X%" src=measured

## 회귀

```
poc/tests/test_demo_page.py: TBD
```

## Phase 7 진입

다음: 데모 §5 최종 정리 + doc/17 갱신 + 발주처 1페이지 갱신.
