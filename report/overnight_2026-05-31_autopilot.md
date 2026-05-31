# 야간 오토파일럿 보고서 — 2026-05-31

- 시작: (야간 자동 실행)
- 완료: 2026-05-31 04:10:12
- 총 소요: 61.3분
- 결과: 3/9 PASS

---

## 단계별 결과

| Phase | 작업 | 결과 | 소요 |
|---|---|:---:|---|
| 1 | git commit + .gitignore | ✅ | — |
| 1.5 | pytest 베이스라인 | ❌ | 1800.0s |
| 2 | labeled dataset 재빌드 | ✅ | 2.8s |
| 3 | P1 GPU 재학습 | ❌ | 0.1s |
| 3.5 | pytest 회귀 확인 | ❌ | 1800.0s |
| 4 | P2 reranker 측정 | ❌ | 65.5s |
| 4.5 | PSH 시나리오 | ❌ | 1.2s |

---

## 핵심 수치

| 지표 | 이전 | 야간 결과 | 합격선 |
|---|---|---|---|
| pytest | 540+ PASS | 알 수 없음 | 회귀 0 |
| P1 F1-macro | 1.000 (합성 자기참조) | **N/A** (OSS 실측) | ≥ 0.75 |
| P1 FNR | 0.0% (합성 자기참조) | **N/A** (OSS 실측) | ≤ 5% |
| P2 Recall@5 (base) | 0.623 (KURE hybrid) | — | ≥ 0.80 |
| P2 Recall@5 (reranker) | — | **N/A** | ≥ 0.80 |

---

## labeled dataset 현황

| split | 건수 |
|---|---|
| train | 3187 |
| val | 680 |
| test | 687 |
| **합계** | **4554** |

소스: OSS corpus 3,754건 + synthetic_qwen3_800 800건 = 4,554건

---

## 실패 항목
- git add
- pytest 베이스라인
- P1 GPU 재학습 (3 epoch)
- pytest 회귀 확인
- P2 KURE-v1 + BGE reranker
- PSH dryrun 시나리오

---

## 내일 할 일

1. P1 F1/FNR 수치 확인 → 합격선 통과 여부 판정
2. P2 reranker Recall@5 확인 → 0.80 근접 여부
3. doc/15 v12 갱신 (야간 결과 반영)
4. 발주처 회신 대기 항목 체크
