# scripts/ 안내

이 디렉터리에는 **193개** 파일이 있습니다. 전부 볼 필요는 없습니다.
대부분은 데이터셋을 만들거나 한 번 쓰고 남겨둔 것이고, **실제로 손이 가는 것은 아래 표들뿐**입니다.

읽는 순서: **① 시연 → ② 배포·설치 → ③ 운영 → ④ 검증·게이트**.
그 밖은 필요할 때만 찾아보면 됩니다.

---

## ① 시연 — 감리·데모에서 실제로 쓰는 것

| 스크립트 | 하는 일 |
|---|---|
| `demo_e2e_8010.py` | **시나리오 A** — 문서 업로드 → 분류 → 검수 (루프 A) |
| `demo_e2e_golden.py` | **시나리오 B** — 골든셋 → 검수·서명 → 재학습 → 배포 → 메트릭 (루프 B) |
| `run_all_pocs.py` | PoC 5종 일괄 실행 (인프라 없이 ~2초) |
| `parse_probe.py` | 개별 문서 파싱 결과 확인 |

두 시나리오 스크립트는 단계마다 **대응하는 관리자 콘솔 카드**를 함께 출력합니다.
터미널 시연과 화면 시연이 같은 대본이 되도록 한 것입니다.

```bash
DEMO_BASE_URL=http://<서버>:8000  DEMO_API_KEY=<키>  python scripts/demo_e2e_golden.py --train --activate
```

---

## ② 배포 · 설치

| 스크립트 | 하는 일 | 참조 문서 |
|---|---|---|
| `build_offline_bundle.py` | 폐쇄망 오프라인 번들 생성 | [EXPORT_IMPORT_RUNBOOK](../docs/EXPORT_IMPORT_RUNBOOK.md) |
| `deploy_testserver_dual.sh` | 테스트서버 이중(지재원/고객사) 배포 | — |
| `deploy_rollback.sh` | 배포 롤백 | — |
| `verify_install.sh` | 설치 검증 | [INSTALL](../docs/INSTALL.md) |
| `verify_deploy_live.sh` | 배포 후 라이브 검증 | — |
| `verify_infra.py` | 인프라(PG·Redis) 헬스체크 | [INSTALL](../docs/INSTALL.md) |
| `cache_kure_v1.py` | 임베딩 모델 사전 캐시 (폐쇄망 필수) | [INSTALL](../docs/INSTALL.md) |
| `register_deployed_model.py`<br>`seed_active_model_version.py` | 배포 모델 등록·활성 버전 시드 | [INSTALL](../docs/INSTALL.md) |
| `seed_keywords.py` | 태깅 키워드 시드 DB 적재 | — |

---

## ③ 운영

| 스크립트 | 하는 일 | 참조 문서 |
|---|---|---|
| `import_review_corrections.py` | 고객사 검수 교정 반입 | [OPERATION](../docs/OPERATION.md) |
| `promote_golden_candidates.py` | 골든 후보 → 정본 승격 (명시적 게이트) | [OPERATION](../docs/OPERATION.md) |
| `calibrate_classifier.py` | temperature 보정 | [EXPORT_IMPORT_RUNBOOK](../docs/EXPORT_IMPORT_RUNBOOK.md) |
| `backup_dr.py` · `dr_drill.py`<br>`dr_restore.py` · `dr_restore_check.py` | 백업 · DR 훈련 · 복구 | [infra/systemd/README](../infra/systemd/README.md) |
| `run_acceptance.py` | 인수 시나리오 실행 | [CLOUD_DEPLOY_RUNBOOK](../docs/CLOUD_DEPLOY_RUNBOOK.md) |
| `p5_e2e_smoke.py` | 배포 후 E2E 스모크 | [CLOUD_DEPLOY_RUNBOOK](../docs/CLOUD_DEPLOY_RUNBOOK.md) |

---

## ④ 검증 · 게이트 (Makefile 타깃)

`make` 로 부르는 것들입니다. 릴리스 전 점검에 씁니다.

| 계열 | 스크립트 |
|---|---|
| **릴리스 게이트** | `check_release_gate.py` · `build_release_manifest.py` · `check_dataset_manifest.py` |
| **품질 게이트** | `check_data_quality.py` · `check_metamorphic_gate.py` · `check_active_learning.py` · `check_parser_support_matrix.py` |
| **평가** | `eval_p1_model_gold.py` · `eval_adversarial.py` · `eval_trusted_ci.py` |
| **성능** | `run_perf_scenarios.py` · `run_soak.py` |
| **계약** | `openapi_consistency.py` |
| **라이선스·SBOM** | `dump_licenses.py` · `render_sbom_html.py` |
| **CI** | `ci_alembic_drift_check.sh` (내부적으로 `_drift_probe_empty_check.py` 호출) |

전체 타깃은 [Makefile](../Makefile)에서 확인하십시오.

---

## ⑤ 데이터셋 구축 — 한 번 만들고 끝난 것들

`build_*` · `make_*` · `gen_*` · `p1~p5_*` · `depollute_*` · `mine_*` 계열 약 50개는
**현재 데이터셋을 만들 때 쓴 것**이고 일상 운영에서는 부르지 않습니다.
어떤 데이터가 어떤 스크립트에서 나왔는지는 각 데이터셋 폴더의 매니페스트에 적혀 있습니다
(예: `datasets/manifests/dataset_v1.0.yaml`, `datasets/labeled_p1_v5_clean/GATE_RESULTS.md`).

재현이 필요할 때만 매니페스트를 따라 역추적하십시오.

---

## ⑥ `_` 로 시작하는 파일 — 실험·일회성 (29개)

**읽지 않아도 됩니다.** 벤치마크 프로브, 일회성 패치, 임시 집계용입니다.
`_bench_*`(임베더·벡터스토어·하이브리드 비교)가 대부분이고,
결론은 이미 설계 문서와 소스 주석에 반영돼 있습니다.

다만 아래 둘은 **다른 곳에서 참조하므로 지우거나 옮기면 안 됩니다**:

| 파일 | 참조하는 곳 |
|---|---|
| `_drift_probe_empty_check.py` | `ci_alembic_drift_check.sh` — **CI가 실행합니다** |
| `_bench_pg_lexical_revalidation.py` | alembic 마이그레이션 주석 · `src/lloydk/adapters/vectorstore/pg_store.py` 설계 노트 · `infra/postgres/README.md` · `revalidate_pg_lexical.py` · `build_nl_revalidation_queries.py` — **실측 근거로 인용됩니다** |

---

## 요약

| 목적 | 보아야 할 것 |
|---|---|
| 시연을 돌리고 싶다 | ① 4개 |
| 배포·설치를 확인하고 싶다 | ② 9개 |
| 운영 절차를 확인하고 싶다 | ③ 8개 |
| 품질·릴리스 게이트를 확인하고 싶다 | ④ Makefile 타깃 |
| 데이터셋 출처를 역추적하고 싶다 | ⑤ 데이터셋 매니페스트부터 |
| 그 밖 | 볼 필요 없음 |

소스 코드 자체의 구조는 [docs/CODE_MAP.md](../docs/CODE_MAP.md)를 보십시오.
