# scripts/archive/

폐기하지 않고 이력 보존용으로 격리한 **일회성·실험용 옛 스크립트** 모음 (2026-05 ~ 2026-06).

공통점:
- Makefile / CI(`.github/workflows`) / 런북 어디에도 배선되지 않음 (UNREF).
- 특정 시점 실험·오버나이트 런·데모용 원샷이거나, 상위 버전으로 대체됨.
- 서로만 참조하는 죽은 클러스터(예: `run_overnight_master.py` → `run_p1_retrain_v2.py`/`run_phase4_pdf_upload.py`/`run_phase5_p5_update.py`).

정본 대체 경로(참고):
- 합성 생성: `p3_generate_synthetic.py` (← `synthesize_v2_diverse.py`, `resume_synthesize_qwen3.py`)
- P1 재학습: `build_p1_retrain_dataset.py` (← `run_p1_retrain_v2.py`)
- 데모 E2E: `demo_e2e_8010.py` (← `demo_al_loop_8010.py`, `demo_content_8010.py`)

필요하면 `git mv scripts/archive/<파일> scripts/`로 되살릴 수 있음.

## 2026-09-05 — 세대 중복 스크립트 11개 이관

`build_direct_authored_catalog_training_corpus` 8판 · `build_direct_authored_proxy_eval_v2` 1판
(합 9판 · 2,146줄).

**지우지 않고 옮긴 이유.** 이 스크립트들이 배포본 `v-fe4b386b` 의 학습셋을 만들었다.
지우면 그 데이터셋을 어떻게 만들었는지 재현할 길이 없어진다. 작업 폴더에서만 뺐다.

⚠ **참조 검사를 한 번 틀렸다.** 처음에는 파일명(`....py`)으로 찾아 "11개 전부 참조 0"으로
읽고 11개를 옮겼는데, `from scripts import build_direct_authored_proxy_eval` 처럼
**확장자 없이 모듈명으로 import** 하는 자리를 못 봤다. 전체 시험이 수집 단계에서 멈춰
드러났고 두 개(`build_direct_authored_proxy_eval` · `_v2_2`)를 되돌렸다.
파이썬 모듈은 파일명이 아니라 **모듈명**으로 참조된다.
