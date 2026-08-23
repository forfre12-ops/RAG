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
