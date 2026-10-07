# labeled_p1_v6_court — 판례 검출 수정본 (2026-09-05)

`datasets/labeled_p1_v5_clean` 을 **덮지 않고** 따로 냈다. 배포본 분류기
`artifacts/classifier_p1_v5_clean/v-fe4b386b` 가 v5 로 학습됐고, 덮으면 그 모델을 다시
만들 수 없다.

## 무엇이 다른가

빌더의 공개 판결문 검출기가 띄어 쓴 표제(`【원 고】`·`【주 문】`·`【신 청 인】`)를 못 잡고
있었다. 그 하나만 고친 결과다 — 표제 인식을 끄면 v5 와 **정확히 같은 2,554행**이 나온다.

| | v5 (배포본) | v6 |
|---|---|---|
| 행 | 2,554 | 2,481 |
| 판례 → S3 재라벨 | 2,620 | **2,673** (+53) |
| 미검출 판례 | 62 (그중 TS/S1 39) | **0** |
| 등급 TS | 501 | 468 (−33) |
| doc_id 없는 행 | 840 | **0** |
| document_origin=unknown | 88.4% | **0%** |

문서 단위로 보면: 공통 1,642건 · v5 에만 73건(판례 상한 15%에 걸려 빠짐) · v6 에만 0건.
라벨이 바뀐 공통 문서는 9건(TS→S3 7 · S2→S3 2).

## 아직 남은 것

- `origin_text_mismatch: 351` — `labeled_oss_v1` 에서 온 행이 `document_origin=synthetic`
  인데 본문은 공개 판결문이다(한국방송공사·법무법인 삼흥 등 실명 포함). **등급은 전부
  S3 로 맞다.** 출처 칸만 상류가 잘못 준 값이라 여기서 덮어쓰지 않고 매니페스트가 센다.
- **홀드아웃은 여전히 비교 불가다.** 문장 공유는 18.8%→16.5% 로 내렸지만 Theil's U 가
  0.283→**0.328 로 올랐다** — 판례(긴 문서)를 전부 S3 로 모으면 길이가 등급을 더 잘
  알려준다. 라벨을 바로잡는 것과 홀드아웃을 쓸 수 있게 만드는 것이 서로 당긴다.

## 이 셋으로 학습하려면

배포 결정이다. 이 폴더는 **만들어만 두었고 학습·승격은 하지 않았다.**

    python scripts/p1_train_classifier.py --mode full \
        --train-path datasets/labeled_p1_v6_court/train.jsonl \
        --val-path   datasets/labeled_p1_v6_court/val.jsonl \
        --test-path  datasets/labeled_p1_v6_court/test.jsonl \
        --epochs 5 --max-seq-len 512 --output-dir artifacts/classifier_p1_v6_court

⚠ 학습 전에 홀드아웃 독립성을 먼저 볼 것 — 지금 상태로는 `usable_for_comparison=false` 라
이 셋의 test 로 낸 수치는 "새 모델이 더 좋다"의 근거가 되지 못한다.

    python scripts/report_holdout_independence.py \
        --train datasets/labeled_p1_v6_court/train.jsonl \
        --holdout datasets/labeled_p1_v6_court/test.jsonl --strict
