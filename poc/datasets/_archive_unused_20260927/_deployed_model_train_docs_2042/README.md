# 현재 배포본(v-fe4b386b) 학습 문서 2,042건

원본: `poc/datasets/labeled_p1_v5_clean/train.jsonl` (2026-09-19 복사, 사용자 요청)

이 2,042건은 지금 실제로 서비스되는 분류기 모델(v-fe4b386b)의 **가중치를 직접 학습시킨** 문서입니다.
같은 `labeled_p1_v5_clean` 폴더의 val.jsonl(256건)·test.jsonl(256건)은 여기 포함되지 않습니다
(학습 중 성능 확인·홀드아웃 목적이라 가중치 학습에는 안 씀).

## 확인된 사실 (2026-09-19)

- 이 문서들로 다시 분류를 돌리면 정확도 94.9%가 나온다(등급별 90.9~99.0%) — 다만 이는
  모델이 학습 중 이미 본 문서라서 나오는 **암기 성능**이지, 새 문서에 대한 실력이 아니다.
- 실제 실력은 학습에 안 쓴 별도 평가셋(golden100·holdout109)으로 재야 하며, 거기서는
  등급별 재현율이 16.0~96.0%로 훨씬 낮고 들쭉날쭉하다.

## 구성 (train.jsonl 의 `origin_dataset` 을 직접 센 값, 2026-09-20 정정)

- labeled_oss_v1: 1,093건
- rag_corpus_v2: 604건
- gold_real(classification_gold.jsonl): 255건
- bilingual_en: 90건

(정정: 이 절은 처음에 매니페스트의 `origin_dataset_distribution` — 1,417·720·297·120 — 을 그대로 옮겨 적었다.
그 값은 train+val+test 합 2,554건의 구성이라 이 2,042건의 구성이 아니었다.)
