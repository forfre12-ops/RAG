# -*- coding: utf-8 -*-
"""테이블정의서의 사람 서술 부분 — 논리명·용도·컬럼 설명.

스키마 사실(컬럼·타입·NULL·기본값·키·인덱스)은 여기 적지 않는다. 그것은
poc/src/koipa/db/models.py 와 alembic 마이그레이션에서 build_table_spec.py 가
직접 읽는다. 이 파일은 코드가 알려줄 수 없는 것 — 한국어 이름과 뜻 — 만 담는다.

컬럼을 추가하고 여기에 설명을 안 적으면 build_table_spec.py 가 미기술로 세어
경고한다. 문서가 코드보다 뒤처지는 것을 막는 장치다.
"""

# 그룹 구분은 models.py 모듈 docstring 의 도메인 구분과 같다.
GROUPS = [
    ("A", "등급체계", "영업비밀 등급 정의와 판정 요건·키워드 사전."),
    ("B", "문서", "업로드 원문 메타와 청킹 결과."),
    ("C", "라벨링", "문서별 확정 등급."),
    ("D", "추론", "분류 결과와 그 판단 근거."),
    ("E", "학습", "모델 버전 레지스트리와 학습 실행 이력."),
    ("F", "보정", "검수자 확정·교정 결과. 운영 재학습의 진실 소스."),
    ("G", "합성", "학습용 합성 문서 생성 이력과 프롬프트 버전."),
    ("H", "운영", "비용·감사 이력."),
]

# ── 제약 없는 참조 (DB FK 없이 애플리케이션이 정합을 보증) ──────────────────
#
# 정본은 여기 하나다. build_table_spec.py 의 캡션·§05 와 poc/scripts/build_erd.py 의 점선이
# **모두 이 목록을 본다.** 2026-09-03 이전에는 두 생성기가 각각 상수를 들고 있어 도식 1건,
# 캡션·본문 2건으로 갈렸다.
#
# 형식: (자식표, 자식칼럼, 부모표)
SOFT_REFS = [
    ("tb_chunks", "doc_id", "tb_documents"),
    ("tb_classification_evidence", "chunk_id", "tb_chunks"),
    ("tb_model_versions", "training_run_id", "tb_training_runs"),
]
# ── 정의서에서 빼는 것 ──────────────────────────────────────────────────────
#
# 표는 비어 있다. 2026-09-03 에는 유사문서 조회 표·칼럼을 소스보다 먼저 정의서에서 뺐고
# (EXCLUDED_*), 2026-09-05 에 소스에서 걷으면서 칼럼 목록을 비웠다. 2026-09-26 에는 요건 점수·
# 학습 에폭·학습 문서·가이드 표 4개(EXCLUDED_TABLES 에 있던 요건 점수 표 포함)와 안 쓰는 칼럼 18개를
# **ORM 과 DB 에서 실제로 지웠다**(migration b7d3f5a19c24) — 정의서만 앞서 빼 둘 것이 없다.
# 생성기가 --check 에서 "제외 목록이 코드보다 뒤처졌다"로 이 상태를 잡아 준다.
EXCLUDED_TABLES: dict[str, str] = {}

# [2026-09-05] 비웠다. 여기 있던 9개 칼럼(rag_used·rag_top_k·rag_ref_doc_id·
# rag_similarity·indexed·embedding_vector_count·index_name·alias·model)은
# "정의서가 코드보다 앞선 상태"를 담아 둔 것이었는데, 커밋 319069b9 가 소스와
# ORM 에서 실제로 걷었고 마이그레이션 a3b4c5d6e7f8 이 DB 에서 떨궜다.
# 이제 코드에 없으므로 제외할 것도 없다. 생성기가 --check 에서
# "제외 목록이 코드보다 뒤처졌다"로 이 상태를 잡아 준다.
EXCLUDED_COLUMNS: dict[str, dict[str, str]] = {}
# ── 배치 — 이 표를 어느 서버에 두는가 (2026-09-02 조사) ─────────────────────
#
# 근거는 `poc/scripts/audit_table_placement.py` 전수 조사다(표 19개 · src 203파일).
# 판단축은 config.py 의 배포 플래그 실측이다.
#
#     enable_training              full-train   에서만 True  -> 지재원 모델공장
#     enable_incremental_retrain   onprem-local 에서만 True  -> 고객사 야간 증분 재학습
#
# 학습 실행 표(runs)를 고객사에도 두는 이유가 여기 있다(에폭·학습 문서 표는 2026-09-26 에 지웠다). 고객사는 야간
# 증분 재학습을 돌리므로 그 이력이 고객사 DB 에 남아야 한다. 반대로 합성 2표는
# 합성 라우터가 enable_training 에서만 등록돼 고객사에는 열리지 않는다.
#
# ⚠ 표 구조는 두 서버가 같다. 다른 것은 **채워지는 규모와 용도**다 — 실측 2026-09-02
#   지재원 시험서버(223): 문서 20 · 분류 33 · 청크 193 행. 시연·검증 규모이지
#   운영 적재가 아니다. 회원사 실적재는 고객사 서버에서 일어난다.
PLACEMENT = {
    # 표: (배치, 근거)
    "tb_classification_levels":   ("둘 다", "등급 기준 시드. 양쪽이 읽는다"),
    "tb_evaluation_factors":      ("둘 다", "판정 요건 시드 — 런타임 쓰기 0. 값은 alembic 이 채우고 양쪽이 읽는다"),
    "tb_level_keywords":          ("둘 다", "룰 1차 판정 키워드 사전"),
    "tb_documents":               ("둘 다", "업로드·분류 운영의 중심 표"),
    "tb_chunks":                  ("둘 다", "분류가 저장하고 근거·청크학습이 읽는다"),
    "tb_document_labels":         ("둘 다", "문서 확정 등급"),
    "tb_classifications":         ("둘 다", "추론 결과"),
    "tb_classification_evidence": ("둘 다", "판단 근거"),
    "tb_model_versions":          ("둘 다", "배포 모델 레지스트리"),
    "tb_document_vectors":        ("둘 다", "유사문서 조회 — 문서가 있는 서버마다 색인"),
    "tb_training_runs":           ("둘 다", "고객사도 야간 증분 재학습 이력을 남긴다"),
    "tb_corrections":             ("둘 다", "회원사 검수 교정이 재학습 입력이 된다"),
    "tb_prompt_versions":         ("지재원", "합성 프롬프트 버전. 합성은 지재원 전용"),
    "tb_sample_documents":        ("지재원", "합성 문서. 합성 라우터가 고객사에는 안 열린다"),
    "tb_sample_dataset_membership": ("지재원", "합성 문서가 어느 학습셋 판에 들어갔는지. 합성이 지재원 전용이라 이 표도 그렇다"),
    "tb_llm_usage":               ("지재원", "유사문서 조회 폐기 후 LLM 을 부르는 경로는 골든셋 빌드뿐이고 그것은 지재원 작업이다. 고객사 프로파일에도 로컬 LLM(ollama)이 설정돼 있으나 부르는 자리가 없다"),
    "tb_audit_log":               ("둘 다", "감사 로그. 양쪽 모두 필수"),
    "tb_advisory_locks":          ("둘 다", "감사 해시체인·모델 활성화의 동시성 잠금. 행 자체가 잠금 대상이라 데이터가 아니다"),
}

TABLES = {
    "tb_classification_levels": ("A", "분류 등급", "영업비밀 등급(TS·S1·S2·S3) 정의. 등급을 참조하는 모든 테이블의 부모."),
    "tb_evaluation_factors": ("A", "평가 요건", "등급 판정 요건. 정본은 3요건 — 비공지성(S)·경제적 유용성(V)·비밀관리성(M)."),
    "tb_level_keywords": ("A", "등급 키워드", "룰 기반 1차 판정에 쓰는 등급별 키워드 사전."),
    "tb_documents": ("B", "문서", "업로드 문서의 메타·추출 텍스트 위치·처리 상태. 청크·분류·라벨·학습셋이 모두 참조하는 중심 허브."),
    "tb_document_vectors": ("B", "문서 벡터", "문서 1건의 대표 벡터(청크 임베딩 평균) 1행 — 유사문서 조회용. 등급은 이 표에 복제하지 않고 문서 라벨과 조인해 읽습니다."),
    "tb_chunks": ("B", "청크", "문서를 512 토큰 단위로 자른 조각."),
    "tb_document_labels": ("C", "문서 라벨", "문서 1건의 확정 등급 1행. 라벨 주체(사람·LLM·룰)를 함께 기록합니다."),
    "tb_classifications": ("D", "분류 결과", "모델 추론 1회의 결과. 등급·확신도·게이트 판정을 남긴다."),
    "tb_classification_evidence": ("D", "분류 근거", "분류 1건이 어느 청크의 어느 구간을 근거로 삼았는지."),
    "tb_model_versions": ("E", "모델 버전", "학습된 분류기 버전 레지스트리. 활성 버전은 항상 1개."),
    "tb_training_runs": ("E", "학습 실행", "학습 잡 1회의 설정·자원·결과."),
    "tb_corrections": ("F", "보정", "검수자의 확정·교정 기록. 재학습에 반영된 행만 소비 표시가 붙는다."),
    "tb_prompt_versions": ("G", "프롬프트 버전", "합성 문서 생성에 쓴 프롬프트 템플릿 버전."),
    "tb_sample_documents": ("G", "합성 문서", "LLM 이 생성한 학습용 문서와 그 검수 상태."),
    "tb_sample_dataset_membership": ("G", "학습셋 편입 이력", "합성 문서 1건이 어느 학습셋 판(dataset_version)에 들어갔는지. 한 문서가 여러 판에 들어가므로 행을 더하기만 하고 고치지 않는다 — 칼럼 하나로 두면 재방출할 때 앞선 판 기록을 잃는다."),
    "tb_llm_usage": ("H", "LLM 사용량", "LLM 호출별 토큰·비용·지연."),
    "tb_audit_log": ("H", "감사 로그", "전 API 호출 기록. 보존기간(기본 730일)이 지난 행은 오래된 달부터 지운다 — 해시 체인이 끊기지 않도록 앞에서부터 이어서 지운다."),
    "tb_advisory_locks": ("H", "동시성 잠금", "이름 하나에 행 하나. 임계구역에 드는 쪽이 그 행을 SELECT ... FOR UPDATE 로 잡는다. 행 잠금은 트랜잭션이 끝나면(commit·rollback) 자동으로 풀리므로 잠금 수명이 호출부가 전제한 트랜잭션 단위와 같다."),
}

# 여러 테이블에 같은 뜻으로 나오는 컬럼 — 테이블별 설명이 없을 때만 쓴다.
COMMON = {
    "created_at": "생성 시각",
    "updated_at": "수정 시각",
    "created_by": "생성자 ID",
    "is_active": "활성 여부. FALSE 는 삭제가 아니라 이력 보존",
    "description": "설명(자유 서술)",
}

COLS = {
    "tb_classification_levels": {
        "level_id": "등급 PK",
        "level_code": "등급 코드 — TS · S1 · S2 · S3",
        "level_name": "등급 표시명",
        "level_order": "정렬 순서. 숫자가 작을수록 상위 등급",
        "description": "등급 정의 문구",
        "color_hex": "화면 표시색",
        "loss_weight": "학습 손실 가중치. 상위 등급 미탐에 더 큰 벌점을 주기 위한 값",
        "is_active": "활성 여부. FALSE 는 삭제가 아니라 이력 보존",
    },
    "tb_evaluation_factors": {
        "factor_id": "요건 PK",
        "factor_code": "요건 코드 — 정본 SECRECY · VALUE · MANAGEMENT. 레거시 4요소는 is_active=FALSE 로 보존",
        "description": "요건 정의 문구",
        "weight": "미사용. 요건 가중치는 등급 산정에 쓰지 않습니다. 등급은 키워드 룰이 정하며 S×V×M 은 상향 교차 확인용입니다. 하위호환 기본 1.0",
        "is_active": "정본 3요건 TRUE · 레거시 4요소 FALSE",
    },
    "tb_level_keywords": {
        "keyword_id": "키워드 PK",
        "level_id": "이 키워드가 가리키는 등급",
        "keyword": "키워드 문자열",
        "pattern_type": "매칭 방식 — exact · regex 등",
        "factor_id": "이 키워드가 근거로 삼는 요건. NULL 이면 요건 무관",
        "weight": "키워드 가중치. NULL 이면 1.0 으로 계산합니다(요건 표의 가중치와 달리 선택값)",
        "source": "출처 — manual(사람 등록) · seed(초기 시드) 등",
        "is_active": "활성 여부. FALSE 면 사전에서 빼되 이력은 남긴다",
    },
    "tb_document_vectors": {
        "doc_id": "문서 PK 이자 FK — 문서 1건에 벡터 1행",
        "embedding": "문서 대표 벡터 1024차원(청크 임베딩 평균). HNSW 코사인 인덱스 대상",
        "model": "벡터를 만든 임베딩 모델 — 모델 교체 시 재색인 대상 선택 기준",
        "chunk_count": "평균에 들어간 청크 수. 0 이면 본문 추출 실패로 보고 색인 실패 처리",
        "content_sha256": "본문 해시. 같은 문서 재업로드 시 재색인 필요 여부 판단",
        "created_at": "생성 시각",
    },
    "tb_documents": {
        "doc_id": "문서 PK",
        "filename": "원본 파일명",
        "source_format": "원본 포맷 — hwp · hwpx · pdf · docx · xlsx · txt 등",
        "file_size_bytes": "원본 파일 크기(바이트)",
        "file_hash": "원본 SHA-256. 같은 파일 재업로드 판별",
        "metadata": "문서 속성 JSON. 출처(source_type)·보안표시·접근범위 등 등급 판정 입력을 담습니다",
        "raw_text_uri": "추출 원문 텍스트 저장 위치",
        "normalized_text_uri": "정규화 텍스트 저장 위치",
        "text_preview": "본문 앞부분 미리보기(최대 2,000자)",
        "char_count": "추출 본문 글자 수. 등록 때 추출 결과가 없으면 NULL(청크 표의 글자 수는 청크마다 반드시 있다)",
        "extraction_method": "추출 경로 — parser(전자문서 파싱)",
        "extraction_quality": "추출 품질 점수(0~1)",
        "processing_status": "처리 상태 — pending · processing · done · failed",
        "error_message": "처리 실패 사유",
        "uploaded_at": "업로드 시각",
        "processed_at": "처리 완료 시각",
        "created_by": "업로드 주체 ID",
        "deleted_at": "논리 삭제 시각. NULL 이면 활성이며 조회는 NULL 행만 노출합니다",
    },
    "tb_chunks": {
        "chunk_id": "청크 PK",
        "doc_id": "원문 문서. FK 제약 없이 애플리케이션이 정합을 보증합니다",
        "chunk_index": "문서 내 청크 순번(0-base)",
        "content": "청크 본문",
        "token_count": "토큰 수(최대 512)",
        "char_count": "글자 수",
        "section_path": "계층 섹션 경로 — 예: {3장, 3.1절}",
        "overlap_prev": "앞 청크와 겹친 토큰 수(기본 64)",
        "overlap_next": "뒤 청크와 겹친 토큰 수(기본 64)",
        "created_at": "생성 시각",
    },
    "tb_document_labels": {
        "doc_id": "대상 문서(PK)",
        "level_id": "확정 등급",
        "labeled_by": "라벨 주체 — human · llm · rule",
        "labeler_id": "라벨 작성자 계정 ID",
        "confidence": "라벨 확신도(0~1). 확신도가 없는 라벨 출처(사람 확정 등)는 NULL — 분류결과 표의 신뢰점수는 모델이 늘 낸다",
        "notes": "라벨 메모",
        "is_verified": "검증(서명) 완료 여부",
        "verified_by": "검증자 계정 ID",
        "labeled_at": "라벨 시각",
        "verified_at": "검증 시각",
    },
    "tb_classifications": {
        "classification_id": "분류 PK",
        "doc_id": "대상 문서",
        "model_version": "판정에 쓴 모델 버전 라벨",
        "predicted_level_id": "예측 등급",
        "confidence": "예측 확신도(0~1). 온도 보정 후 값",
        "alternatives": "차순위 등급과 점수 JSON 배열",
        "automation_assessment": "자동확정 게이트가 결정 시점에 본 신호 스냅샷 JSON. 이 컬럼 도입 전 행은 NULL",
        "chunk_count": "판정에 사용한 청크 수",
        "status": "현재 상태 — staging · confirmed · needs_review",
        "initial_status": "게이트의 최초 판정을 생성 시점에 동결한 값. 이후 검수·보정이 건드리지 않는다. 도입 전 행은 NULL",
        "classified_at": "분류 시각",
    },
    "tb_classification_evidence": {
        "evidence_id": "근거 PK",
        "classification_id": "대상 분류",
        "chunk_id": "근거가 된 청크",
        "evidence_type": "근거 종류 — keyword · attention 등",
        "excerpt": "근거 발췌 원문",
        "excerpt_start": "발췌 시작 위치(청크 내 문자 오프셋)",
        "excerpt_end": "발췌 끝 위치",
        "contribution": "이 근거의 기여도",
    },
    "tb_model_versions": {
        "version_id": "모델 버전 PK",
        "version_label": "버전 라벨 — v-fe4b386b 형식(학습 산출물 해시)",
        "base_model": "기반 사전학습 모델 이름",
        "model_type": "모델 종류. 코드가 넣는 값은 classifier 하나이며 기본값이다",
        "trained_at": "학습 완료 시각",
        "training_run_id": "이 모델을 만든 학습 실행 ID",
        "training_data_count": "학습 샘플 수",
        "metrics": "최종 지표 JSON — F1 · FNR · Recall 등",
        "model_uri": "모델 저장 경로",
        "is_active": "활성 여부. 생성 칼럼 active_key 의 UNIQUE 제약으로 활성 1건만 허용합니다",
        "active_key": "is_active 에서 DB 가 계산하는 칼럼 — 활성이면 1, 아니면 NULL. 여기 걸린 UNIQUE 인덱스가 활성 1건 제약을 만든다(UNIQUE 는 NULL 을 여러 개 허용하므로 비활성 행은 제한이 없다). 애플리케이션은 쓰지 않는다",
        "activated_at": "활성화 시각",
        "deactivated_at": "비활성화 시각",
        "rolled_back_from": "자동 롤백 출처 버전(자기 참조)",
        "rollback_reason": "롤백 사유",
    },
    "tb_training_runs": {
        "run_id": "학습 실행 PK",
        "model_version_id": "이 실행이 만든 모델 버전(tb_model_versions 참조). 실행을 만들 때는 NULL 이고 학습이 끝나 버전이 등록되면 채운다(진행 중·실패는 NULL)",
        "status": "실행 상태 — queued · running · done · failed",
        "started_at": "시작 시각",
        "completed_at": "종료 시각",
        "duration_sec": "소요 초",
        "total_samples": "전체 샘플 수",
        "hyperparameters": "하이퍼파라미터 JSON — epochs · lr · batch_size 등",
        "final_metrics": "최종 지표 JSON. 배포 게이트 판정을 포함합니다",
        "trigger_type": "기동 방식 — manual · scheduled · active_learning",
        "error_message": "실패 사유. 학습 잡 조회 API 가 이 값을 그대로 내보낸다",
        "created_by": "요청자 ID",
    },
    "tb_corrections": {
        "correction_id": "보정 PK",
        "classification_id": "대상 분류",
        "original_level_id": "AI 예측 등급",
        "corrected_level_id": "검수자 확정 등급. 확정만 하고 고치지 않았으면 예측 등급과 같다",
        "direction": "보정 방향 — underclass(미탐 보정) · overclass(과분류 보정) · lateral(동위 변경) · confirm(확정)",
        "reason": "보정 사유",
        "corrected_by": "검수자 계정 ID",
        "corrected_at": "보정 시각",
        "consumed_in_run": "이 보정을 반영한 재학습 실행. NULL 이면 미반영",
        "consumed_at": "재학습 반영 시각",
    },
    "tb_prompt_versions": {
        "prompt_version": "프롬프트 버전 PK",
        "chain_stage": "생성 단계 — outline · body · qc",
        "template": "프롬프트 템플릿 원문",
        "created_by": "등록자 ID",
        "notes": "메모",
    },
    "tb_sample_dataset_membership": {
        "membership_id": "편입 기록 PK",
        "sample_id": "편입된 합성 문서. tb_sample_documents 참조",
        "dataset_version": "학습셋 판 이름. 내용 해시라 같은 승인 집합이면 같은 값이 나온다 — 시각이 아니라 내용으로 판을 가른다",
        "synth_job_id": "그 문서를 만든 생성 작업. 없으면 NULL",
    },
    "tb_sample_documents": {
        "sample_id": "합성 문서 PK",
        "target_level_id": "생성 때 요구한 등급",
        "corrected_level_id": "검수자가 고친 등급. NULL 이면 교정 없음. 학습행 라벨은 이 값을 우선합니다",
        "doc_type": "문서 종류",
        "outline_prompt_version": "개요 생성에 쓴 프롬프트 버전. 프롬프트 **내용 해시**(v2-<sha8>)이며 tb_prompt_versions 를 참조합니다 — 사람이 번호를 올리지 않아도 프롬프트가 바뀌면 값이 바뀝니다",
        "body_prompt_version": "본문 생성에 쓴 프롬프트 버전. 개요와 한 호출이라 같은 값입니다",
        "qc_prompt_version": "품질검사 게이트 판. 현행 검사는 LLM 프롬프트가 아니라 계량 지표라 metric-gate-v1 입니다(등급명 노출·길이 누출·tell 커버)",
        "llm_provider": "생성 LLM 제공자",
        "llm_model": "생성 LLM 모델명",
        "generated_outline": "생성된 개요",
        "generated_content": "생성된 본문",
        "quality_score": "품질 게이트 통과 점수. 1.0=코퍼스 지표까지 통과, 0.5=표본이 모자라 코퍼스 지표는 판정하지 않음. 걸린 문서는 애초에 적재되지 않습니다",
        "quality_report": "품질검사 상세 JSON — 게이트 판정과 지표(길이 누출·tell 커버·등급명 노출 건수)",
        "label_source": "본문 출처 마커 — NULL(정상 생성) · noop_fallback(자리표시 본문·학습 편입 금지) · llm_nonjson(비-JSON 응답)",
        "parse_error": "생성 응답 파싱 실패 사유",
        "review_status": "검수 상태 — pending_review · approved · rejected",
        "reviewed_by": "검수자 계정 ID",
        "reviewed_at": "검수 시각",
        "rejection_reason": "반려 사유",
    },
    "tb_llm_usage": {
        "usage_id": "사용 기록 PK",
        "provider": "LLM 제공자",
        "model": "모델명",
        "purpose": "호출 목적 — 합성 · 라벨링 · 질의응답 등",
        "reference_type": "연관 대상 종류",
        "reference_id": "연관 대상 식별자",
        "input_tokens": "입력 토큰 수",
        "output_tokens": "출력 토큰 수",
        "cost_usd": "비용(USD)",
        "billing_phase": "과금 구분 — development · operation",
        "latency_ms": "응답 지연(밀리초)",
        "success": "호출 성공 여부",
        "error_code": "실패 코드",
        "called_at": "호출 시각",
    },
    "tb_audit_log": {
        "audit_id": "감사 PK. 파티션을 두던 시절 파티션 키를 기본키에 넣어야 해 복합키가 됐고, 파티션을 걷은 뒤에도 복합키는 그대로 둔다 — 바꾸면 기존 행의 키가 흔들린다",
        "request_id": "요청 추적 ID. 같은 요청에서 나온 여러 기록을 잇는다",
        "actor_id": "행위자 계정 ID",
        "actor_role": "행위자 역할",
        "action": "수행한 동작",
        "target_type": "대상 종류",
        "target_id": "대상 식별자",
        "payload_hash": "요청 본문 SHA-256. 본문 원문은 남기지 않고 해시만 남긴다",
        "ip_address": "요청 IP(INET 타입)",
        "user_agent": "요청 User-Agent",
        "success": "처리 성공 여부",
        "error_code": "실패 코드",
        "occurred_at": "발생 시각. 기본키의 뒷자리이자 시간축 인덱스 idx_audit_occurred 의 선두 칼럼이다",
    },
    "tb_advisory_locks": {
        "name": "잠금 이름. 값은 audit_chain(감사 해시체인) · model_activation(모델 활성화) 둘이며 마이그레이션이 미리 넣어 둔다 — 런타임에 만들면 처음 쓰는 둘이 서로의 미커밋 행을 못 봐 같이 들어간다",
    },
}

# ── 표준 명명 (2026-09-11) ────────────────────────────────────────────────
# 위 사전들은 옛 물리명(tb_*)으로 적혀 있다. 사람이 쓴 설명이라 그대로 두고, 생성기가 찾는
# 이름(models.py 의 표준 물리명)으로 여기서 한 번 옮긴다. 대응표 정본은
# poc/src/koipa/db/standard_names.py(migration 7b3e9d2a4f10) — 여기서 따로 대응을 들지 않는다.
import sys as _sys  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

_sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / "poc" / "src"))
from koipa.db.standard_names import COLUMNS as _STD_COLS, TABLES as _STD_TABLES  # noqa: E402

RENAMED_TABLES = {old: new for old, (new, _) in _STD_TABLES.items()}
_RENAMED_COLS = {old: {a: b for a, b, _ in cols} for old, cols in _STD_COLS.items()}


def _t(name: str) -> str:
    return RENAMED_TABLES.get(name, name)


def _c(table: str, col: str) -> str:
    return _RENAMED_COLS.get(table, {}).get(col, col)


def _uniform(col: str) -> str:
    """모든 표에서 같은 표준 이름으로 바뀌는 칼럼만 공통 사전에 옮긴다(description 은 표마다 다르다)."""
    news = {m[col] for m in _RENAMED_COLS.values() if col in m}
    return news.pop() if len(news) == 1 else col


SOFT_REFS = [(_t(ch), _c(ch, col), _t(pa)) for ch, col, pa in SOFT_REFS]
EXCLUDED_TABLES = {_t(k): v for k, v in EXCLUDED_TABLES.items()}
EXCLUDED_COLUMNS = {_t(t): {_c(t, c): v for c, v in cols.items()} for t, cols in EXCLUDED_COLUMNS.items()}
PLACEMENT = {_t(k): v for k, v in PLACEMENT.items()}
TABLES = {_t(k): v for k, v in TABLES.items()}
COMMON = {_uniform(k): v for k, v in COMMON.items()}
COLS = {_t(t): {_c(t, c): d for c, d in cols.items()} for t, cols in COLS.items()}

# ── 규정 참고 표시 (2026-09-25) ────────────────────────────────────────────
# 옛 이름(tb_*)이 없는 표라 위 변환을 거치지 않고 표준 물리명으로 바로 적는다.
# 정본 = poc/src/koipa/db/standard_names.py 의 POST_BASE_TABLES/COLUMNS. 이름은 표준용어집 대조 전 후보다.
GROUPS.append(("I", "규정", "회원사 규정 원문·조항·문장. 검수 화면이 문서와 관련된 규정 원문 문장을 참고로 보여 준다(등급 판정과 무관)."))
PLACEMENT.update({
    "tad_rm_rgltn_mng":       ("둘 다", "회원사 규정 한 판. 규정을 올리는 곳은 고객사 서버다"),
    "tad_rm_rgltn_artcl_mng": ("둘 다", "규정의 조항. 표시 대상 조항만 임베딩이 채워진다"),
    "tad_rm_rgltn_stc_mng":   ("둘 다", "조항의 문장(원문 그대로). 검수 화면이 이 중 하나를 보여 준다"),
})
TABLES.update({
    "tad_rm_rgltn_mng": ("I", "규정", "회원사 규정 한 판(版). 같은 규정명의 판들 중 활성은 하나뿐이다. 등급을 바꾸지 않는 참고 표시용이다."),
    "tad_rm_rgltn_artcl_mng": ("I", "규정 조항", "규정의 조항 한 행. 종류(총칙·절차·등급정의·취급기준)와 표시 대상 여부를 가진다. 벡터는 BYTEA 다(pgvector 아님)."),
    "tad_rm_rgltn_stc_mng": ("I", "규정 문장", "조항의 문장 한 행. 서두 문장은 선택 후보에서 빼고, 등급별 목록의 줄은 같은 묶음으로 표시한다."),
})
COLS.update({
    "tad_rm_rgltn_mng": {
        "rgltn_id": "규정 판 PK(UUID)",
        "rgltn_nm": "규정명. 같은 규정명의 판들이 한 계열이다",
        "ver_lbl_nm": "판 표기(예 v3.1)",
        "enfc_dt": "시행일(ISO 날짜 문자열). 선택",
        "prcs_stts_cd": "상태 — indexing · ready · active · archived · failed. 같은 규정명의 active 는 하나뿐이다",
        "file_hash_nm": "원본 파일 SHA-256. 삭제하지 않은 판끼리 부분 UNIQUE(중복 등록 방지)",
        "orgtxt_path_nm": "원본 파일 저장 위치(암호화 버킷 regulations-raw)",
        "orgnl_frmat_nm": "원본 파일 형식(확장자)",
        "file_nm": "원본 파일명",
        "prttn_mth_cd": "분할 방식 — article(제N조) · numbered(번호 제목) · paragraph(문단, 정확도 낮음)",
        "artcl_cnt": "조항 수",
        "stc_cnt": "문장 수",
        "embd_mdl_nm": "임베딩 모델명. 문서 대표 벡터의 모델과 다르면 표시하지 않는다",
        "embd_trgt_cnt": "임베딩할 조항·문장 수(진행률의 분모)",
        "embd_cmptn_cnt": "임베딩을 마친 수(진행률의 분자)",
        "aplcn_trgt_dscrp_cn": "관리자가 적은 적용 대상 한 줄 설명",
        "aplcn_trgt_cnfrm_yn": "적용 대상 확인 여부. 활성화의 조건이다",
        "wrn_stts_msg_cn": "분할 경고(예: 조 단위 구분이 없어 정확도가 낮을 수 있다)",
        "err_stts_msg_cn": "색인 실패 사유",
        "creatr_id": "등록한 사람(JWT sub)",
        "crt_dt": "생성 시각",
        "mdfcn_dt": "수정 시각",
        "vtlz_dt": "활성화 시각",
        "dsbl_dt": "보관(비활성화) 시각",
        "del_dt": "삭제 시각. NULL 이면 삭제 안 됨",
    },
    "tad_rm_rgltn_artcl_mng": {
        "artcl_id": "조항 PK(UUID)",
        "rgltn_id": "소속 규정 판(FK, 규정 삭제 시 함께 삭제)",
        "artcl_sn": "규정 안 순번. (규정, 순번) UNIQUE",
        "artcl_no_nm": "조 번호 표기(예 제34조, 부칙 제2조, 문단 12)",
        "artcl_ttl_nm": "조 제목",
        "chpt_nm": "장 제목",
        "artcl_cn": "조항 원문",
        "artcl_knd_cd": "종류 — general(총칙·부칙) · procedure(절차) · grade_def(등급 정의) · handling(취급 기준) · other",
        "artcl_knd_src_cd": "종류를 정한 주체 — auto(규칙) · admin(관리자)",
        "dsply_yn": "표시 대상 여부. 기본은 취급 기준만 true",
        "embd_vctr_cn": "조항 임베딩(float32 little-endian). 표시 대상 조항만 채운다",
    },
    "tad_rm_rgltn_stc_mng": {
        "stc_id": "문장 PK(UUID)",
        "artcl_id": "소속 조항(FK, 조항 삭제 시 함께 삭제)",
        "stc_sn": "조항 안 순번. (조항, 순번) UNIQUE",
        "stc_cn": "문장 원문",
        "lead_yn": "서두 문장 여부(\"다음 각 호…\" 류). 선택 후보에서 제외한다",
        "list_grp_sn": "등급별 목록 묶음 번호. 같은 번호의 줄은 목록 전체를 함께 보여 준다",
        "embd_vctr_cn": "문장 임베딩(float32 little-endian)",
    },
})
