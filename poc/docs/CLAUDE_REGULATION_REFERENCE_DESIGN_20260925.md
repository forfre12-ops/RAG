# 규정 참고 표시 — 기획·설계서 (초안 v0.1)

작성 2026-09-25 · 대상 독자 = 구현 담당 개발자, 검수·배포 담당 · 상태 = **초안(결정 12건 대기, §1.11)**

> 이 문서는 "회원사가 올려 둔 사내 규정에서, 검수 중인 문서와 관련된 **규정 원문 문장**을 참고로 보여 주는" 기능을 넣기로 한 뒤의 상세 기획과 설계다.
> 기능이 **등급을 바꾸지 않는다**는 것이 모든 설계의 출발점이다.

---

## 0. 한눈에 보기

| 항목 | 내용 |
|---|---|
| 무엇을 | 검수 화면에서 문서 옆에 "관련 규정(참고)" — 규정 조항 번호와 **원문 문장 1개**(등급별 목록이면 목록 전체) |
| 무엇을 안 하나 | 등급 판정·자동 확정·검수 라우팅에 관여하지 않는다. LLM 요약을 만들지 않는다. 점수·신뢰도 숫자를 화면에 띄우지 않는다 |
| 어떻게 | 규정 파일 업로드 → 조항·문장 분할 → 조항 종류 태깅 → 임베딩(운영 임베더) → 활성화. 조회는 문서 대표 벡터(이미 색인됨)로 결정형 계산 |
| 요건 근거 | FUN-004 「RAG 규정 보완(선택 옵션)」 — 현재 서빙 배선 0건인 부분충족 항목을 이 기능이 채운다 |
| 기본 상태 | **꺼짐**(`regulation_reference_enabled=false`). 꺼져 있으면 라우트·화면·작업 모두 없다(무동작) |
| 실측 효과 | 시연용 규정 기준 **문서 3건 중 1건(35%)** 에서 직접 적용되는 문장이 뜨고, 표본 20건에서 오도 0건(95% 상한 약 14%). 회원사 실제 규정은 **아직 안 쟀다** |
| 가장 큰 미해결 | 규정이 문서에 **적용되는지**를 점수로 못 가른다 → 관리자 확인 절차 + 활성화 전 미리보기로 막는다(§1.5 F-06·F-07) |
| 선행 작업 | ① 표준명 등록 구조 확장 ② 운영 임베더(KURE-v1)로 재측정 ③ 조항 종류 규칙을 다른 규정으로 재검증 ④ 협의 미확정 안건과의 저촉 확인 |

---

# 제1부 기획

## 1.1 배경과 요건 근거

1. **요건**: FUN-004 「등급분류 학습」 중 "RAG 규정 보완"은 **선택 옵션**이다. 2026-09-22 요건 재점검에서 이 항목은 "서빙 배선 0건"인 **부분충족**이었다 (`policy_engine.py`·`evidence_collection.py`·`policy_shadow.py`는 서빙 경로에서 호출되지 않는다 — `policy_shadow.py:1` "Never wired to production classification").
2. **왜 등급 판정이 아닌가**: 규정 원문으로 등급 매핑표를 채우는 시험이 두 규정에서 모두 실패했다(공개 규정 3건 핵심 두 열 0/11, 기록물관리 지침 23/50). 벡터로 등급 품질을 올리는 길도 닫혔다(AUROC 0.49). 그래서 규정은 **판정 입력이 아니라 근거 표시 보조**로 내려갔다(`org_mapping.py:12`).
3. **왜 LLM 요약이 아닌가**: 고객사 대부분은 GPU가 없다. qwen3:14b 요약은 CPU에서 문서당 중앙 95초였고, 요약 방식은 도움 33%·오도 24%였다(개선해도 44%/0%로 LLM 없는 추출식 35%/0%와 표본 오차 안).
4. **협의 저촉 가능성**: 2026-09-20 협의의 미확정 항목에 "기업 배포 시 LLM/RAG 방식"이 있고 "임의 적용 금지" 조건이 붙었다. 이 기능은 LLM이 없고 등급을 바꾸지 않는 **선택 옵션(기본 꺼짐)** 이라 "적용"이 아니라 "제공"으로 두었으나, 협의 결론과 충돌하면 기본 꺼짐을 유지한다(→ D-01).

## 1.2 목표와 비목표

| 구분 | 내용 |
|---|---|
| 목표 G1 | 검수자가 문서를 열었을 때, 회원사 규정 중 그 문서에 **직접 적용되는 문장**을 한 번에 본다 |
| 목표 G2 | 규정 관리자가 파일을 올리고, 무엇이 어떻게 나뉘었는지 확인하고, 활성화·교체·보관한다 |
| 목표 G3 | 기능을 꺼도 기존 분류·검수 동작과 응답이 **바이트 단위로 같다** |
| 목표 G4 | 온프렘·CPU 전용 환경에서 동작한다(외부 호출·LLM 없음) |
| 비목표 N1 | 등급 판정·자동 확정·검수 라우팅에 규정을 쓰지 않는다 |
| 비목표 N2 | 규정 내용에 대한 질의응답(`/answer`)·요약 생성을 하지 않는다 (2026-06-27 요건 외 결정) |
| 비목표 N3 | 규정에서 등급 매핑표를 자동으로 채우지 않는다(담당자 입력물) |
| 비목표 N4 | 다중 회원사 격리(테넌트)를 만들지 않는다 — 배포 하나 = 회원사 하나 |
| 비목표 N5 | 골든셋 검수 화면에는 표시하지 않는다(독립 판정 보호) |

## 1.3 시험으로 확인된 사실 (설계의 근거)

전부 시연용 규정(우리가 쓴 ○○전자 문서보안 규정 53조)과 합성 업무문서 71건 기준이다. **회원사 규정은 재지 않았다.** 근거 산출물은 `poc/reports/CLAUDE_REGULATION_RAG_20260925/`(git 무시)이며 도구는 `scripts/measure_regulation_{evidence,summary,extract}.py`다.

| 사실 | 수치 | 한계 |
|---|---|---|
| 규정이 매핑표를 채우는 정도 | 시연규정 16/20(표기→우리 값 ③ 0/4), 기록물관리 지침 23/50(③ 0/10) | 규정에 등급별 문서종류·열람범위·표기가 적혀야 채워진다 |
| 조각 조회 적중(질의를 조각에서 만든 시험) | 하이브리드 1위 36/37 | 실문서 적중률이 아님 |
| 직접 적용 조항이 조회에 드는 문서 | 1위 28/71(39%) · 상위3 36/71(51%) · 무작위 13~21% | 정답 조항 집합은 내가 조회 전에 정함, 한 사람 |
| 표시 방식별 도움/무용/오도(표본 20, 그룹 가중) | LLM 요약 기본 33/43/24 · LLM 개선 44/56/0 · 추출식 S1 41/45/15 · **추출식 S1c(1개) 35/65/0** | 읽은 사람 1명, 0/20의 95% 상한 약 14% |
| 표시 개수별 정밀도 | 1번째 39% · 2번째 13% · 3번째 7% | 3개를 보이면 잡음 80% |
| 항목 점수로 거르기 | AUROC 0.68 — 소용없음 | |
| **규정 적용성** | 같은 문서 71건의 최고 문장 점수 시연규정 0.617 · 안 맞는 지침 0.627 (AUROC 0.332) | 점수로 못 가른다 |
| 종류가 전혀 다른 문서(판례·금융) | 점수로 걸러진다(AUROC 0.97~1.0) | |
| 문서 대표 벡터(평균)로 조회 | 1위 30/71 · 상위3 38/71 (문서 앞 1,500자 임베딩은 28/36) | bge-m3 기준 — 운영 임베더 재측정 필요 |
| 문장 선택을 문서 대표 벡터만으로 | 시험 방식과 같은 문장 54/71(76%), 낱말 방식 27% | 품질 우열은 미검증 |
| 조항 종류 자동 태깅 | 제목 키워드 규칙이 손으로 뺀 21개와 21/21 일치 | **규칙을 이 규정에 맞춰 만들었다** — 다른 규정으로 재검증 필요 |
| 같은 조건 재실행 | (LLM 방식) 17/20 일치 | 추출식은 결정형이라 문제 없음 |

## 1.4 사용자와 시나리오

| 사용자 | 역할 | 하는 일 |
|---|---|---|
| 규정 관리자 | `admin`(포털이 대신 호출할 때 `kl_backend`) | 규정 업로드·분할 결과 확인·표시 대상 조정·미리보기·활성화·보관 |
| 검수자 | `reviewer` · `admin` · `kl_backend` | 문서 검토 대기·확정 대기 화면에서 관련 규정을 읽는다 |
| 운영자 | `admin` | 플래그 켜기·색인 실패 확인·감사 로그 조회 |

- **S1 규정 등록**: 관리자가 파일과 규정명·버전을 올린다 → 색인 진행 표시 → 조항 표에서 종류·표시 대상을 확인 → 「미리보기」로 최근 문서 몇 건에 어떻게 보이는지 본다 → 적용 대상을 확인하고 「활성화」.
- **S2 검수 중 참고**: 검수자가 「왜 이 등급?」을 펼치면 아래에 「관련 규정(참고)」이 나타난다. 규정 조항 번호와 원문 문장. 없으면 아무것도 나타나지 않는다.
- **S3 규정 개정**: 새 판을 올려 활성화하면 같은 규정명의 이전 활성 판은 보관으로 바뀐다. 이전 판으로 되돌리려면 보관 판을 다시 활성화한다.
- **S4 규정 삭제**: 보관·실패 판만 삭제한다(원본·조항·문장·벡터가 함께 지워진다).

## 1.5 기능 목록

| ID | 기능 | 우선 | 비고 |
|---|---|---|---|
| F-01 | 규정 파일 업로드(docx·pdf·hwp·hwpx·txt·md), 파일 해시 중복 검사 | 필수 | 기존 추출기(FUN-022) 재사용 |
| F-02 | 조항 분할(제N조 · 번호 제목 · 문단 대체 모드) + 문장 분할 | 필수 | §2.4 |
| F-03 | 조항 종류 태깅(총칙·절차·등급정의·취급기준) + 표시 대상 기본값 | 필수 | 관리자가 수정 가능 |
| F-04 | 비동기 색인(임베딩) + 진행 표시 + 실패 사유 | 필수 | 큐 `index` 재사용 |
| F-05 | 활성화·보관·삭제, 같은 규정명 이전 판 자동 보관 | 필수 | |
| F-06 | 활성화 전 미리보기(문서 최대 20건) | 필수 | 적용성 문제의 완화책 |
| F-07 | 적용 대상 확인(체크 + 한 줄 설명) | 필수 | 활성화 조건 |
| F-08 | 문서별 관련 규정 조회 API | 필수 | `GET /documents/{doc_id}/regulation-evidence` |
| F-09 | 검수 화면 「관련 규정(참고)」 표시 | 필수 | 문서 검토 대기·확정 대기 |
| F-10 | 조항 표시 대상 토글·종류 수정 | 필수 | |
| F-11 | 감사 기록(업로드·활성화·보관·삭제·조항 수정) | 필수 | |
| F-12 | 규정 작성 가이드 + 시연용 규정 | 권장 | 회원사에 주는 산출물 — 형태는 D-11 |
| F-13 | 검수자 1-click 피드백(「도움이 됨」/「관련 없음」) | 선택 | **요건 외** — 운영 중 효과를 재는 유일한 방법(D-07) |

## 1.6 화면 기획

**관리자 콘솔 · 설정 탭 · 「사내 규정(참고 표시)」 카드** (등급체계·키워드 카드 아래, 기능이 꺼져 있으면 카드 자체가 없다)

```
┌ 사내 규정(참고 표시) ─────────────────────────── FUN-004 ┐
│ [규정 목록]                                              │
│  규정명            판    상태      조항   등록일    동작   │
│  문서보안 규정     v3.1  사용 중   53     09-25   「보관」 │
│  문서보안 규정     v3.0  보관      51     08-02   「활성화」「삭제」│
│ 「규정 올리기」                                            │
│ ─ 올리기 ─ 파일 [선택] · 규정명 · 판 · 시행일(선택)         │
│ ─ 선택한 규정 ─ 색인 진행(n/N) · 조항 표                    │
│    번호   제목            종류      표시  「원문」            │
│    제12조 극비 문서의 종류 취급기준  ☑                       │
│    제10조 등급의 구분      등급정의  ☐                       │
│ 「미리보기」(문서 고르기 → 문서별 표시 결과)                    │
│ 「활성화」 ← [적용 대상 확인] ☐ 이 규정은 검수 대상 문서의     │
│              취급 기준입니다.  설명: ____                    │
└────────────────────────────────────────────────────────┘
```

**검수 화면 · 「관련 규정(참고)」** (문서 검토 대기·확정 대기 행의 「왜 이 등급?」 아래, 지연 로드)

```
관련 규정(참고)
 문서보안 규정 v3.1 · 제41조(설계·공정 문서)
 “① 도면, 회로도, 레이아웃 데이터, 부품 명세는 개발 초기 단계부터 등급을 정하며,
   차세대 제품은 극비, 양산 중인 제품은 기밀로 취급한다.”
 이 내용은 참고용이며 등급 판정 근거가 아닙니다.
```

화면 규칙(메모리·시험에서 확인된 것): 신뢰도·유사도 **숫자를 띄우지 않는다** · 쓰이지 않는 입력칸을 두지 않는다 · 구현 정보 문자열(`.jsonl`, `scripts/`, `use_rag` 등) 금지 · 버튼 문구는 「」 · 「불러오는 중…」 · 외부 폰트·CDN 금지(§2.7).

## 1.7 규정 작성 가이드 (회원사에 주는 산출물)

시험에서 규정 작성 방식이 결과를 좌우했다. 매핑표 채움(16/20 대 23/50)과 조회 정확도 모두 **규정이 무엇을 적었는가**에 달렸다. 가이드의 요지:

1. **등급별 문서 종류 예시**를 구체적으로 적는다(등급마다 6개 이상). "누설 시 피해가 크다"만 적으면 문서와 이어지지 않는다.
2. **등급별 열람 범위**를 조직 단위로 적는다(승인자 한함·지정 열람자·부서·전 임직원).
3. **등급별 표기 문구**와 표기 위치를 적는다. (표기가 우리 값 `top_secret` 등 중 무엇인지는 규정에 없으므로 담당자가 매핑표 4줄로 확인한다 — 규정을 잘 써도 이 4칸은 남는다.)
4. **한 조항에 한 등급만** 담는다. 여러 등급 문장이 한 조항에 섞이면 표시가 엉뚱한 등급 문장을 고를 수 있다(시험에서 제40조로 확인). 등급별로 한 줄씩 나열하는 목록은 목록 전체가 함께 표시된다.
5. 최하위 등급이 **대외 공개인지 사내 전원 열람인지** 한 문장으로 정한다.
6. 조항은 `제N조(제목)` 형식을 쓴다(형식이 다르면 문단 대체 모드로 나뉘어 정확도가 낮다).

시연용 규정(`reports/.../sample_org_regulation.md`)을 예시로 줄 수 있다. 다만 **우리가 쓴 규정이라 효과는 상한선**이라는 점을 함께 적어야 한다.

## 1.8 성공 기준과 측정 계획

**이 기능은 등급 정확도를 올리지 않으므로 정확도 지표를 쓰지 않는다.** 재는 것은 두 가지다.

| 지표 | 정의 | 측정 시점 | 제안 기준(결정 D-10) |
|---|---|---|---|
| 도움 비율 | 표본 20건을 사람이 판정(도움/무용/오도), 그룹 가중 | Phase 3 파일럿 | 시험값(35%) 이상 |
| 오도 | 잘못된 문장이거나 문서와 다른 등급 방향으로 끄는 표시 | 같음 | 표본 20건에서 **0건** |
| 검수 소요 | 문서당 검수 시간 전후 비교 | 파일럿 후 | 측정만(기준 없음) |
| 운영 피드백 | F-13 「도움이 됨」 비율 | 운영 중(F-13 도입 시) | 참고 |

파일럿 절차: 회원사 규정 1부 + 회원사 문서 표본 → `measure_regulation_extract.py`와 같은 방식으로 조회 → **판정을 가린 채** 표본을 두 사람이 읽는다(이번 시험은 한 사람이었다).

## 1.9 단계별 계획

| 단계 | 내용 | 종료 조건 |
|---|---|---|
| Phase 0 선행 | 표준명 구조 확장·용어 대조 / 운영 임베더 재측정 / 태깅 규칙 재검증 / 협의 저촉 확인 / 결정 D-01~D-12 | 결정 문서화, 재측정 결과가 시험값과 같은 방향 |
| Phase 1 백엔드 | 표·마이그레이션·분할기·색인 워커·조회 엔진·API·ICD·플래그 | 시험 통과, **분류 응답 불변 스냅샷 동일**, 플래그 끄면 라우트 없음 |
| Phase 2 화면 | 관리자 카드·검수 표시·콘솔 시험 | 콘솔 e2e·금지 문자열·대비 시험 통과 |
| Phase 3 파일럿 | 회원사 규정 재측정·미리보기 운영·표본 판정 | §1.8 기준 판정 |
| Phase 4 배포 준비 | 번들 확인·INSTALL 체크리스트·플래그 안내 | **배포는 사용자 지시가 있을 때만** |

작업 규모는 §2.12에 S/M/L로만 적는다(일정 추정의 근거가 없다).

## 1.10 위험과 대응

| ID | 위험 | 대응 |
|---|---|---|
| R-01 | 회원사 규정에서 시험값보다 낮게 나온다 | Phase 0/3에서 재측정, 낮으면 표시 개수 1 유지·문턱 상향·기능을 선택 옵션으로만 둔다 |
| R-02 | **규정이 문서에 안 맞는데 활성화된다** — LLM 요약 시험에서 안 맞는 지침을 올리자 문서의 54%에 무관 요약이 나왔고, 추출식은 문턱이 없으면 항상 1개를 보이므로 그보다 나쁠 수 있다 | 적용 대상 확인 필수 + 활성화 전 미리보기 + 활성 후 피드백(F-13) |
| R-03 | 규정 형식이 달라 조항 분할이 실패한다 | 분할 모드·경고를 화면에 보이고, 조항 표에서 관리자가 표시 대상을 조정. 문단 대체 모드는 정확도 낮음을 경고 |
| R-04 | 운영 임베더(KURE-v1)가 시험(bge-m3)과 다르게 동작한다 | Phase 0 재측정. 문서 대표 벡터와 규정의 임베딩 모델이 다르면 표시를 끈다(§2.5) |
| R-05 | 표시된 규정 문장이 등급 방향으로 검수자를 끈다(앵커링) | 등급 정의·절차 조항을 표시에서 제외, 등급별 목록은 전체 표시, 고정 문구. **근거 조항의 등급이 정답 등급과 같은지는 미측정** |
| R-06 | 큰 규정의 색인이 느리다(문장 수천 개 × 임베딩) | 색인 상한·배치·작업 제한 조정(§2.10) |
| R-07 | 새 표 이름이 표준용어집에 없다 → 감리 지적 재발 | 표준명 구조 확장과 용어 대조를 Phase 0에 둔다 |
| R-08 | 콘솔 규칙 시험이 많아 화면 작업이 예상보다 크다 | §2.7 체크리스트로 처음부터 맞춘다 |
| R-09 | 규정 원문이 기밀이다 | 원본 암호화 버킷·접근 권한·삭제 API·감사(§2.8) |
| R-10 | 오프라인 번들에 임베더가 없다 | **이미 있는 위험, 직접 확인함(2026-09-25)**: `poc/dist/`에서 `KURE`·`nlpai`·`models--*` 이름의 디렉터리가 0건이다. lloydk 번들(12GB)의 `models/`에는 `classifier-trained`뿐이고, rocky 번들(2026-09-24)은 manifest·체크섬만 17KB로 manifest에 KURE-v1이 적혀 있다. 이 번들로는 운영 임베더가 없어 **이 기능뿐 아니라 이미 있는 유사 문서 색인도 오프라인에서 동작하지 않는다.** 이 번들들이 검증용 미완성 산출물인지는 확인하지 못했다. 배포 전 번들 빌드(`build_offline_bundle.py`, KURE 캐시 스테이징은 fail-closed)에서 임베더가 실리는지 확인 |
| R-11 | 협의 미확정 안건과 저촉 | D-01 |

## 1.11 결정이 필요한 것

| ID | 결정 | 권고 | 이유 |
|---|---|---|---|
| D-01 | 발주처 협의 안건("기업 배포 시 LLM/RAG 방식")과의 관계 — 이 기능을 그 답으로 제시할지, 결론이 나기 전에는 꺼 둘지 | **기본 꺼짐 유지, 협의 시 제안 자료로 사용** | "임의 적용 금지" 조건 |
| D-02 | 규정 등록 경로 | 관리자 콘솔 + API(쓰기는 `admin`·`kl_backend`) | 포털이 회원사 화면을 일원 구축하므로 API도 열어 둔다 |
| D-03 | 규정 여러 개 동시 활성 | 허용(상한 5) | 규정·약관이 여러 문서다. 같은 규정명은 1개 판만 활성 |
| D-04 | 지원 형식 | docx·pdf·hwp·hwpx·txt·md | 기존 추출기 지원 범위. 표 위주 규정은 경고 |
| D-05 | 검수 화면 표시 개수 | 1(설정으로 1~3) | 2번째부터 정밀도 13% |
| D-06 | 공개 출처 문서(공개 판결문 등)에 표시할지 | 표시 안 함 | 판례·금융에서 18~26%가 무관 표시. `source_type` 값 목록은 ICD로 확인 필요 |
| D-07 | F-13 피드백 버튼 | Phase 3에서 도입(요건 외 승인 필요) | 운영 중 효과를 재는 유일한 수단 |
| D-08 | 표준명 등록 구조 확장 + 영역 코드 `rm` 신설 + 용어 등재 | 승인 | 새 표를 넣을 수 없는 구조(§2.3) |
| D-09 | 규정 원본 보관 | 암호화 보관 + 삭제 API | 재색인·감사 vs 무반출 원칙. 가이드 API는 파일을 버렸지만 규정은 재색인이 필요하다 |
| D-10 | 파일럿 합격 기준 | §1.8 제안 | |
| D-11 | 시연용 규정·작성 가이드를 회원사에 주는 형태 | 사용자 지시 필요 | 발주처 문서 서식(정본)으로 낼지 |
| D-12 | 활성화 시 적용 대상 확인 문구 | §2.7 제안문 | |

---

# 제2부 설계

## 2.1 설계 원칙

| ID | 원칙 | 이유·검증 |
|---|---|---|
| P1 | **등급을 바꾸지 않는다** — 분류·검수 라우팅 코드에 새 분기를 넣지 않는다 | 벡터로 등급 품질 AUROC 0.49, 정책 엔진의 "확률적 요소를 판정 경로에 넣지 않는다" 원칙. 시험: 플래그 꺼짐·켜짐 모두 `/classify` 응답 동일 |
| P2 | **결정형** — 같은 입력이면 같은 출력. 동점은 조항 순번으로 깬다 | LLM 없음, 정렬 규칙 명시 |
| P3 | **원문 그대로** — 생성·요약·재서술 없음. 표시는 규정 문장의 글자 그대로 | 요약 방식의 오도 24% |
| P4 | **기본 꺼짐·무동작** — 라우트·워커·화면이 플래그로만 나타난다 | 기존 기본 OFF 게이트 관례 |
| P5 | **온프렘·CPU** — 외부 호출 없음, 임베딩은 운영 임베더 | 고객사 대부분 GPU 없음 |
| P6 | **추가 전용 API** — 기존 응답 스키마를 바꾸지 않는다 | 연동 계약 5개 오퍼레이션 보호 |
| P7 | **블라인드 보호** — 골든셋 검수 화면·API에 규정 표시를 넣지 않는다 | 독립 판정 |
| P8 | **실패는 조용히 비운다, 그러나 숨기지 않는다** — 표시 실패는 검수를 막지 않고 비어 있음 이유를 응답에 싣는다 | 참고 기능이 검수 흐름을 막으면 안 된다 |

## 2.2 전체 구조

```
[관리자 콘솔]                                    [검수 콘솔]
  │ 업로드(multipart)                              │ 행 펼침 → GET /documents/{doc_id}/regulation-evidence
  ▼                                                ▼
POST /regulations ──▶ RegulationService ◀── RegulationEvidenceService
  │  검증·해시·중복                 │                 │
  │  원본 저장(암호화 버킷)         │ DB: 규정·조항·문장  │ 조회: 문서 대표 벡터(tad_dm_doc_vctr_mng)
  ▼                                │                 │      + 청크 텍스트(tad_cm_chnk_mng)
큐 index ──▶ 워커 koipa.index_regulation             ▼
              추출(FUN-022) → 조항분할 → 문장분할      RegulationIndexCache (프로세스 메모리, 30초 TTL)
              → 종류 태깅 → 임베딩(운영 임베더)          활성 규정의 조항·문장 벡터, 낱말 색인
              → 조항·문장 벡터 저장 → status=ready        │
                                                       ▼
                                          조항 하이브리드(밀집+낱말 순위합산) → 1위 조항에서 문장 선택
                                          → 등급별 목록이면 목록 전체 → 응답(원문 문장·조항 번호)
```

### 신규·수정 파일

| 구분 | 파일 | 내용 |
|---|---|---|
| 신규 | `src/koipa/regulation/splitter.py` | 조항·문장 분할(순수 함수) |
| 신규 | `src/koipa/regulation/tagger.py` | 조항 종류 태깅 규칙 |
| 신규 | `src/koipa/regulation/index.py` | 프로세스 내 색인(밀집·낱말)·캐시 |
| 신규 | `src/koipa/regulation/selector.py` | 조항·문장 선택, 목록 확장 |
| 신규 | `src/koipa/services/regulation_service.py` | 등록·상태·활성화·삭제·미리보기 |
| 신규 | `src/koipa/services/regulation_evidence_service.py` | 문서별 조회 |
| 신규 | `src/koipa/repositories/regulation_repo.py` | 표 3개 접근 |
| 신규 | `src/koipa/api/regulation.py` | 라우터 |
| 신규 | `src/koipa/schemas/regulation.py` | 요청·응답 |
| 신규 | `alembic/versions/<새 id>_regulation_reference.py` | 표 3개 |
| 수정 | `src/koipa/db/models.py` | ORM 3개 |
| 수정 | `src/koipa/db/standard_names.py` | 표준명 사후 추가 구조(§2.3) |
| 수정 | `src/koipa/adapters/vectorstore/document_vectors.py` | `get(doc_id)` 추가(대표 벡터 읽기) |
| 수정 | `src/koipa/workers/tasks.py` · `celery_app.py` | 태스크·작업 제한 |
| 수정 | `src/koipa/config.py` | 플래그 |
| 수정 | `src/koipa/api/app.py` | 라우터 조건 등록 |
| 수정 | `src/koipa/api/static/admin.html` | 관리자 카드·검수 표시 |
| 수정 | `F:\antigravity\rag\doc\03_openapi_koipa_kl.yaml` | 새 경로 `x-audience: internal` |
| 수정 | `.env.example` · `.env.onprem-local` · `docs/INSTALL.md` | 플래그 안내 |
| 수정 | 시험 다수(§2.11) | |

## 2.3 데이터 모델

### 벡터 저장 방식 — pgvector를 쓰지 않는다

규정은 작다(조항 수십~수백, 문장 수백~수천). **프로세스 메모리에서 정확 검색**하는 것이 단순하고 결정형이다(10만 벡터 9.3ms 실측 선례). 벡터는 `BYTEA`(float32 1024개 = 4KB)로 저장한다. 이렇게 하면:
- `vector(1024)` 칼럼이 없어 SQLAlchemy가 autogenerate에서 DROP을 내는 문제(`alembic/env.py:58-65`)와 `_MIGRATION_ONLY_TABLES` 예외가 필요 없다.
- 표 3개가 모두 일반 ORM 표라 `alembic check`·감사 도구(R1~R7)가 그대로 적용된다.
- ANN 인덱스가 없어 색인 재현성 문제가 없다(정확 검색).

### 표 3개 (**물리 이름은 후보** — 표준용어집 대조 전)

영역 코드는 새로 `rm`(규정관리)을 두는 안이다(`standard_names.py:18-19`의 "영역 첫 글자 + m" 관례, `sy`도 새로 둔 선례). 아래 이름은 표준용어집(13,704 용어)과 대조하지 않았고, **용어집 파일이 저장소에 없다**. 미등재 단어는 자체표준 용어로 등록해야 한다(감리 지적 재발 방지).

**① `tad_rm_reg_mng` 규정관리** — 규정 한 판(版) 한 행

| ORM 속성 | 표준명 후보 | 논리명 | 타입 | 비고 |
|---|---|---|---|---|
| reg_id | reg_id | 규정아이디 | UUID PK | |
| reg_name | reg_nm | 규정명 | TEXT NOT NULL | 같은 규정명의 판들은 한 계열 |
| version_label | reg_ver_nm | 규정버전명 | TEXT NOT NULL | 예 "v3.1" |
| effective_date | enfrc_ymd | 시행일자 | DATE NULL | 선택 |
| status | prcs_sttus_cd | 처리상태코드 | TEXT NOT NULL | `indexing`·`ready`·`active`·`archived`·`failed` |
| file_hash | file_hash_cn | 파일해시내용 | TEXT NOT NULL | SHA-256, 부분 UNIQUE(삭제 제외) |
| raw_uri | orgtxt_path_nm | 원문경로명 | TEXT | 기존 칼럼명 재사용(`tad_dm_doc_mng`) |
| file_ext | file_extn_nm | 파일확장자명 | TEXT | |
| split_mode | split_mode_cd | 분할방식코드 | TEXT | `article`·`numbered`·`paragraph` |
| clause_count | artcl_cnt | 조항수 | INT | |
| sentence_count | stc_cnt | 문장수 | INT | |
| embed_model | embd_mdl_nm | 임베딩모델명 | TEXT | `tad_dm_doc_vctr_mng.embd_mdl_nm`과 같은 이름 |
| embedded_count | embd_cmptn_cnt | 임베딩완료수 | INT | 진행 표시 |
| scope_note | aplcn_trgt_dscrp_cn | 적용대상설명내용 | TEXT | 관리자가 적는 한 줄 |
| scope_confirmed | aplcn_trgt_cnfrm_yn | 적용대상확인여부 | BOOL NOT NULL default false | 활성화 조건 |
| error_note | err_cn | 오류내용 | TEXT | 실패 사유 |
| created_by / created_at | crt_id / crt_dt | 생성자아이디 / 생성일시 | | JWT sub |
| activated_at / archived_at | actvtn_dt / arcv_dt | 활성화일시 / 보관일시 | TIMESTAMPTZ NULL | |
| deleted_at | del_dt | 삭제일시 | TIMESTAMPTZ NULL | 소프트 삭제(문서 표와 같은 관례) |

**② `tad_rm_reg_artcl_mng` 규정조항관리** — 조항 한 행

| ORM 속성 | 표준명 후보 | 논리명 | 타입 | 비고 |
|---|---|---|---|---|
| clause_id | artcl_id | 조항아이디 | UUID PK | |
| reg_id | reg_id | 규정아이디 | UUID FK ON DELETE CASCADE | |
| seq | artcl_sn | 조항일련번호 | INT NOT NULL | UNIQUE(reg_id, seq) |
| article_no | artcl_no_nm | 조항번호명 | TEXT | "제34조", 문단 모드는 "문단 12" |
| title | artcl_ttl_nm | 조항제목명 | TEXT | |
| chapter | chpt_nm | 장명 | TEXT | |
| text | artcl_cn | 조항내용 | TEXT NOT NULL | 원문 |
| kind | artcl_kind_cd | 조항종류코드 | TEXT NOT NULL | `general`·`procedure`·`grade_def`·`handling`·`other` |
| kind_source | kind_src_cd | 종류출처코드 | TEXT | `auto`·`admin` |
| display | dsply_yn | 표시여부 | BOOL NOT NULL | 기본: `handling`만 true |
| embedding | embd_vctr_cn | 임베딩벡터내용 | BYTEA NULL | 표시 대상 조항만 채운다 |

**③ `tad_rm_reg_stc_mng` 규정문장관리** — 문장 한 행(표시 대상 조항만 색인)

| ORM 속성 | 표준명 후보 | 논리명 | 타입 | 비고 |
|---|---|---|---|---|
| sentence_id | stc_id | 문장아이디 | UUID PK | |
| clause_id | artcl_id | 조항아이디 | UUID FK ON DELETE CASCADE | |
| seq | stc_sn | 문장일련번호 | INT NOT NULL | 조항 안 순번 |
| text | stc_cn | 문장내용 | TEXT NOT NULL | 원문 그대로 |
| is_lead | lead_yn | 서두문장여부 | BOOL NOT NULL | 선택 후보에서 제외 |
| list_group | list_grp_sn | 목록묶음일련번호 | INT NULL | 등급별 목록의 같은 묶음 |
| embedding | embd_vctr_cn | 임베딩벡터내용 | BYTEA NULL | |

**용량**: 시연규정 기준 조항 53·문장 171 → 벡터 약 0.9MB. 큰 규정(문장 3,000)도 벡터 12MB 안팎이라 프로세스당 메모리 문제가 없다.

### 표준명 등록 구조 확장 (선행 작업 W-01)

**현재 구조로는 새 표를 넣을 수 없다.** `tests/test_standard_names.py`의 `test_migration_copy_matches_standard_names`가 `mig.TABLES == {standard_names.TABLES 전부}`를 단언하는데, 그 마이그레이션 `7b3e9d2a4f10`은 "이미 서버에서 돈 판이라 고치지 않는다"(`standard_names.py:340-343`). 새 표를 `TABLES`에 넣으면 이 시험이 실패한다. 사후 변경용 구조는 `POST_BASE_RENAMES`(개명 전용)뿐이다.

설계안:
1. `standard_names.py`에 `POST_BASE_TABLES: dict[str, tuple[str, str, str]]` — 표 이름 → (표준 표 이름, 논리명, 추가한 마이그레이션 id) 와 `POST_BASE_COLUMNS`를 신설한다.
2. `test_migration_copy_matches_standard_names`는 **기준판 표만**(`TABLES`) 사본과 대조하고, ORM 표 집합은 `TABLES ∪ POST_BASE_TABLES − MIGRATION_ONLY`와 대조하도록 고친다.
3. `logical_names()`(정의서·ERD 생성기 입력)가 사후 표를 포함하게 한다. 리포 루트의 `scripts/table_spec_meta.py`(PLACEMENT·TABLES·COLS)와 `build_table_spec.py --check`, `audit_schema_consistency.py`(R6)가 새 표를 알게 한다.
4. `tests/test_db_models.py`(ORM 표 21개 정확 일치)의 기대 목록을 갱신한다.
5. 새 시험: "사후 추가 표에는 마이그레이션 id와 논리명이 있어야 한다"(`POST_BASE_RENAMES` 시험의 선례와 같다).

### 마이그레이션

- 현재 head `9c4e1f7a2b58` 다음에 새 리비전. `op.create_table` 3개 + 인덱스(`idx_rmreg_status`, `uq_rmreg_hash_live`(부분 UNIQUE), `uq_rmartcl_seq`, `idx_rmartcl_reg`, `idx_rmstc_clause`).
- 인덱스·제약 이름은 `idx_<약어>_…`/`uq_<약어>_…` 관례이며 표준명 적용 대상이 아니다(`standard_names.py:23-28`).
- 재적용 안전: 표 존재 검사. downgrade는 인덱스→표 순서로 지운다. **이미 돈 판은 고치지 않는다.**
- 검증: `alembic check` 무드리프트, 왕복(upgrade→downgrade→upgrade), `audit_schema_consistency.py` R1~R7.

## 2.4 규정 등록 파이프라인

### 상태 전이

```
(업로드) → indexing ──성공──▶ ready ──활성화──▶ active ──보관/새 판 활성화──▶ archived
              │                 │                                              │
              └──실패──▶ failed  └──삭제──▶ (deleted)          archived ──재활성화──▶ active
```

- `active`로 갈 때 같은 `reg_name`의 다른 `active` 판은 **같은 트랜잭션에서** `archived`가 된다.
- 활성 규정 수 상한 `regulation_active_max`(기본 5, D-03).

### 처리 순서

1. **검증**(API): 크기 ≤ `max_upload_mb`(20MB, 그 앞단 본문 제한 25MB) · 빈 파일 422 · 확장자 지원 여부(추출기 `SUPPORTED_FORMAT_GROUPS`; 지원 밖은 422 — 문서 업로드와 달리 규정은 추출 실패를 **거절**한다. 골든 업로드가 추출 오류를 422로 거절하는 선례).
2. **중복**: 파일 SHA-256이 이미 있으면(삭제 제외) 새로 만들지 않고 기존 `reg_id`를 200으로 돌려준다(문서 업로드의 `file_hash` 관례와 같다. 메타는 갱신하지 않는다).
3. **원본 저장**: 버킷 `regulations-raw`, 키 `{sha256}/{safe_name}`(`_safe_key_name` 재사용). **암호화 버킷 목록에 추가**(`storage_encrypted_buckets`, 기본값은 `["documents-raw"]`뿐이다).
4. **큐 발사**: `koipa.index_regulation.delay(reg_id)` (큐 `index`). 브로커가 없으면 **503**(문서 벡터 색인처럼 조용히 건너뛰지 않는다 — 규정 등록은 사용자가 결과를 기다리는 동작이다).
5. **워커**(아래).

### 워커 `koipa.index_regulation`

```
1. 원본 로드(복호) → 임시 파일 → extract(path)   # FUN-022 추출기. PII 마스킹 파이프라인은 거치지 않는다
2. split(text) → 조항 목록(모드 자동 결정: article → numbered → paragraph)
3. 조항마다 문장 분할 + 서두 문장 표시 + 등급별 목록 묶음
4. tag(조항) → kind, display 기본값
5. 표시 대상 조항의 (조항 본문, 문장들)만 임베딩 — 32개씩 배치, 배치마다 커밋, embedded_count 갱신
6. status = ready
```

- **재시도·멱등**: `@celery_app.task(name="koipa.index_regulation", bind=True, max_retries=2, default_retry_delay=10)`. 재시도하면 `embedding IS NULL`인 행만 이어서 채운다. 상태가 `indexing`이 아니면 즉시 반환.
- **작업 제한**: 전역 제한이 soft 900초·hard 1200초(`config.py:379-380`)라 큰 규정은 걸린다. `celery_app.py` `task_annotations`에 이 태스크만 soft 3300·hard 3600초를 둔다(전역 validator는 soft<hard).
- **임베더**: `build_embedder()`(프로세스 싱글톤). **`hash` 폴백이면 실패 처리** — 해시 임베딩은 의미가 없다(`embedding_provider="hash"` 프로파일 또는 `require_real_embedder` 위반).
- **색인 상한**: `regulation_max_sentences`(기본 3,000). 넘으면 `failed` + "표시 대상 조항이 너무 많습니다. 규정을 나누어 올려 주십시오."
- **시간 산정**(외삽, 미실측): 운영 임베더 KURE-v1은 CPU 6스레드에서 청크당 0.51초(2026-09-09 실측). 시연규정(조항 53+문장 171) ≈ 2분, 상한(3,000) ≈ 26분. **문장 단위 임베딩 시간은 Phase 0에서 재야 한다.**
- **진행 표시**: 화면이 `GET /regulations/{id}`를 3초 간격으로 부른다(`embedded_count`/`sentence_count+clause_count`).

### 분할기 (`regulation/splitter.py`)

| 모드 | 감지 | 조항 경계 |
|---|---|---|
| `article` | `제N조(제목)`이 5개 이상(굵은 글씨 `**제N조(제목)**`·`제 N 조`·`제N조의M` 포함) | 조 제목 줄부터 다음 조 제목 줄 전까지. 장 제목(`제N장`, `## 제N장`)은 `chapter`. 부칙은 접두 "부칙"을 붙인다(본문 제1조와 id 충돌 방지 — 시험에서 실제로 겹쳤다). 1,200자 넘는 조는 항 경계에서 나눈다 |
| `numbered` | `1. 제목` / `1.1 제목` 형식 머리글이 5개 이상 | 머리글부터 다음 머리글 전까지 |
| `paragraph` | 위 둘 다 아님 | 줄 묶음(글머리 `ㅇ`·빈 줄) 기준 조각 + 직전 머리글. **화면에 "조 단위 구분이 없어 정확도가 낮을 수 있습니다" 경고** |

- 40자 미만 조각(쪽 번호·머리글 찌꺼기)은 버린다. 반복되는 머리글·꼬리말(같은 줄이 5회 이상)은 버린다. (쪽 경계를 추출기가 주는지는 미확인 — 확인 사항 U-02.)
- **문장 분할**: 줄 단위 → 한 줄 안에서 `다.` 뒤 공백으로 분할 → 12자 미만 제외. 번호 항목(`1.`, `①`)은 한 문장으로 둔다.
- **서두 문장**: `다음 각 호 / 다음 기준 / 다음과 같… / 다음 중 / 다음에 따른다`를 포함하는 문장은 `is_lead`로 표시하고 선택 후보에서 뺀다(내용이 없다 — 시험에서 213개 표시 중 20번 반복됐다).
- **등급별 목록 묶음**: 같은 조항 안에서 `^\d+\.\s*<짧은 라벨>\s*[:：]` 형식 줄이 **3개 이상 연속**이면 한 묶음(`list_group`)으로 본다. 시험은 라벨을 "극비·기밀·대외비·일반"으로 못 박았으나 **회원사마다 등급 이름이 다르므로 라벨 이름을 고정하지 않는다** — 이 일반화는 시험하지 않았다(확인 사항 U-04).

### 조항 종류 태깅 (`regulation/tagger.py`)

| 종류 | 규칙 | 표시 기본값 |
|---|---|---|
| `general`(총칙) | 장 제목에 "총칙" 또는 제목이 목적·적용 범위·용어의 정의·기본 원칙·다른 규정과의 관계·위원회·업무·책임·의무 | 표시 안 함 |
| `procedure`(절차) | 제목에 판단의 세부 기준·결정 절차·결정 기한·잠정 등급·재분류·하향·등급 대장·절차 | 표시 안 함 |
| `grade_def`(등급정의) | 제목이 `등급의 구분` 또는 `… 등급`으로 끝남 | 표시 안 함 |
| `handling`(취급기준) | 위에 안 걸린 것 | **표시** |
| `other` | 관리자가 지정 | 관리자 결정 |

- **검증 상태**: 시연규정 53조에서 손으로 뺀 21개와 **21/21 일치**(`reports/.../design_checks.py`). 그러나 규칙을 이 규정의 제목을 보고 만들었으므로 일치는 당연하다. **다른 규정으로 일치율을 재야 한다**(W-03). 규칙이 틀려도 관리자가 조항 표에서 표시 대상을 바꿀 수 있다(F-10).
- 왜 등급 정의·절차 조항을 표시에서 빼는가: 시험에서 "대외비 정의" 같은 조항이 문서 옆에 나열되어 등급 방향으로 끄는 오도(24%)의 주된 원인이었다. 빼자 0/20이 되었다.

## 2.5 조회 알고리즘 (`regulation/selector.py`, `services/regulation_evidence_service.py`)

### 입력과 사전 조건

| 입력 | 출처 |
|---|---|
| 문서 대표 벡터 + 모델명 | `DocumentVectorStore.get(doc_id)` — **신규 메서드**. 지금은 `similar()`(self-join)뿐이라 벡터를 꺼낼 수 없다 |
| 문서 본문 앞부분(≤ 6,000자) | `ChunkRepo` 청크 텍스트 |
| 활성 규정의 조항·문장 벡터, 낱말 색인 | `RegulationIndexCache` |

### 절차 (의사코드)

```python
def find(doc_id, max_items):
    sets = cache.active_sets()                      # 활성 규정 전체(캐시 30초)
    if not sets:                       return empty("no_active_regulation")
    dv = vector_store.get(doc_id)                   # (vec, model) 또는 None
    if dv is None:                     return empty("document_not_indexed", indexed=False)
    if dv.model != sets.embed_model:   return empty("embedder_mismatch")
    text = chunk_repo.head_text(doc_id, 6000)
    dense = sets.clause_vecs @ dv.vec                # 표시 대상 조항만 색인에 있다
    lex   = sets.lexical.scores(text)                # 글자 2-gram TF-IDF
    rank  = rrf([dense, lex], k=60)                  # 동점은 (규정, 조항 순번)으로
    out = []
    for clause in top(rank, max_items):
        cand = [s for s in clause.sentences if not s.is_lead]
        best = argmax(cand, key=lambda s: sets.sent_vecs[s] @ dv.vec)   # 문서 대표 벡터로 선택
        shown = clause.list_group_of(best) or [best]                    # 등급별 목록이면 목록 전체
        out.append(item(clause, shown))
    return out
```

### 파라미터

| 이름 | 값 | 근거 |
|---|---|---|
| `max_items` | 1 (설정 1~3) | 1번째 정밀도 39%, 2번째 13%, 3번째 7% |
| 순위합산 k | 60 | 시험에서 쓴 값 |
| 문서 본문 길이 | 6,000자 | 낱말 색인 질의. 시험은 문서 전체를 썼다(길이 영향 미검증) |
| 최소 유사도 `regulation_min_similarity` | 0(끔) | 판례류를 거르는 용도이나 값은 파일럿에서 정한다. **업무문서 안에서는 걸러지지 않는다(AUROC 0.68)** |

### 응답이 비는 경우(모두 `items: []` + `reason`)

`no_active_regulation` · `document_not_indexed` · `embedder_mismatch` · `below_floor` · `source_excluded`(D-06). **오류가 아니라 정상 응답**이다(유사 문서 조회의 `indexed:false` 관례). 임베더·DB 실패만 503.

### 검증된 것과 아닌 것

| 항목 | 상태 |
|---|---|
| 조항 조회(문서 대표 벡터 = 문단 임베딩 평균) | 시험(bge-m3): 1위 30/71, 상위3 38/71 — **운영 임베더로 재측정 필요** |
| 문장 선택(문서 대표 벡터만) | 시험 방식과 같은 문장 54/71(76%) — **품질 우열 미검증**, 파일럿에서 사람이 재판독 |
| 등급별 목록 확장, 서두 제외, 1개 표시 | 시험(S1c): 도움 35%·오도 0/20 — 시험은 라벨을 "극비" 등으로 고정했음 |
| 성능 | 미측정. 예산 제안: p95 200ms(DB 2회 + 내적 수십 회). 임베딩 호출이 없다 |

### 캐시 (`regulation/index.py`)

- 프로세스 메모리에 활성 규정의 조항 벡터(N×1024)·문장 벡터·낱말 색인(글자 2-gram TF-IDF)을 올린다. 첫 요청 때 로드.
- **무효화**: 활성화·보관·삭제·표시 토글이 일어난 프로세스는 즉시 비우고, 다른 프로세스는 **30초 TTL**로 수렴한다(다중 워커 한계를 화면에 적는다 — 키워드 관리 화면의 선례 `noteKeywordReload`).
- 활성 규정 변경 판별용 `epoch`(활성 규정 id+`updated_at`의 해시)를 요청마다 싼 쿼리 1회로 확인할지 TTL만 쓸지는 구현 때 정한다(성능 측정 후).

## 2.6 API

모든 경로는 `/api/v1` 아래이며 `x-audience: internal`이다(연동 계약 5개는 바뀌지 않는다). 오류는 **HTTP 상태만**으로 표현한다(이 시스템은 심볼릭 오류코드를 운영하지 않는다).

| 메서드·경로 | 역할 | 요청 | 성공 | 오류 |
|---|---|---|---|---|
| `POST /regulations` | 업로드 | multipart: `file`, `name`, `version_label`, `effective_date?`, `actor`(JSON) | 202 `{reg_id, status:"indexing", duplicate:false}` / 중복이면 200 `{…, duplicate:true}` | 413·422·503 |
| `GET /regulations` | 목록 | `status?`, `limit`, `offset` | 200 `{items[], total}` | |
| `GET /regulations/{reg_id}` | 상세·진행 | | 200 `{…, embedded_count, split_mode, warnings[]}` | 404 |
| `GET /regulations/{reg_id}/clauses` | 조항 표 | `kind?`, `display?`, `limit`, `offset` | 200 `{items[], total}` | 404 |
| `PATCH /regulations/{reg_id}/clauses/{clause_id}` | 표시·종류 수정 | `{display?, kind?}` | 200 | 404·409(활성 판 수정은 캐시 무효화 후 허용, `indexing` 중은 409) |
| `POST /regulations/{reg_id}/preview` | 미리보기 | `{doc_ids:[≤20]}` | 200 `{results:[{doc_id, items[], reason?}]}` | 404·409(`ready` 아님)·422 |
| `POST /regulations/{reg_id}/activate` | 활성화 | `{scope_confirmed:true, scope_note}` | 200 | 404·409(`ready` 아님·활성 상한)·422(확인 누락) |
| `POST /regulations/{reg_id}/archive` | 보관 | | 200 | 404·409 |
| `DELETE /regulations/{reg_id}` | 삭제(보관·실패 판만) | | 204 | 404·409 |
| `GET /documents/{doc_id}/regulation-evidence` | 문서별 참고 규정 | `max_items?` | 200 `RegulationEvidenceResponse` | 404(문서 없음)·503 |

권한: 쓰기 `admin`·`kl_backend`(`require_role("admin","kl_backend")`, 가이드 API 선례), 읽기 `admin`·`reviewer`·`kl_backend`(검수 근거 API 선례). 본문 `actor`는 `bind_authenticated_actor`로 JWT sub에 덮어쓴다(기존 관례). `doc_id` 하이픈 정규화는 `/similar`와 같은 처리를 쓴다(하이픈 UUID vs `char(32)` 함정).

`RegulationEvidenceResponse` 예:

```json
{
  "doc_id": "85dc2e2c-9d3d-4b41-8269-486f3ace0354",
  "indexed": true,
  "reason": null,
  "items": [
    {
      "regulation": {"reg_id": "…", "name": "문서보안 규정", "version_label": "v3.1"},
      "clause": {"clause_id": "…", "article_no": "제41조", "title": "설계·공정 문서"},
      "sentences": ["① 도면, 회로도, 레이아웃 데이터, 부품 명세는 개발 초기 단계부터 등급을 정하며, 차세대 제품은 극비, 양산 중인 제품은 기밀로 취급한다."],
      "is_grade_list": false
    }
  ]
}
```

- **점수는 응답에 넣지 않는다.** 화면 금지 규칙과 별개로, 소비자가 신뢰도로 오용하는 것을 막는다. 진단이 필요하면 감사 로그가 아닌 개발용 도구(`measure_*`)를 쓴다.
- **ICD**: 위 9개 경로를 `F:\antigravity\rag\doc\03_openapi_koipa_kl.yaml`에 `x-audience: internal`로 같은 커밋에서 추가한다. `test_openapi_contract_matches_routes.py`가 코드 경로가 ICD에 없으면 실패시키고, `test_kl_openapi_schema_matches_code.py`가 kl 오퍼레이션이 정확히 5개여야 한다고 잠근다. OpenAPI 3.0.3 유효성(nullable 사용, 3.1식 type 배열 금지)을 지킨다.
- **레이트리밋·멱등**: 업로드는 기본 멱등 미들웨어(`Idempotency-Key` 헤더가 있을 때만 동작)와 파일 해시 중복으로 이중 처리된다. 조회는 `@limiter.limit("120/minute")`을 둔다(검수 화면이 행 펼침마다 부른다).
- **라우터 등록**: `app.py`에서 `if settings.regulation_reference_enabled:` 조건으로 `include_router`(학습·합성 라우터 선례). **정적 경로를 가변 경로(`/regulations/{id}`)보다 먼저** 등록한다(`manage.html`이 doc_id로 삼켜진 사고).

## 2.7 콘솔

### 관리자 카드 (`admin.html`, 설정 탭)

- 카드 마크업은 `<section class="card col-span" data-pane="config">` + `.card-head` + `.card-body`(등급체계·키워드 카드 선례). 기능이 꺼져 있으면(`GET /regulations`가 404) 카드를 만들지 않는다.
- 호출은 `api(method, path, body)`, 서버 텍스트는 `esc()`(규정 텍스트는 **사용자 입력**이라 `innerHTML`에 원문을 넣지 않는다 — 검수 배정 패널 선례의 저장형 XSS 시험).
- 쓰기 버튼(`규정 올리기`·`활성화`·`보관`·`삭제`·`표시 토글`)은 `guardWrite('…')`(Safe Mode)와 `confirm` 창을 쓰고, **e2e `09_safety.mjs`의 WRITES 표에 손으로 등록**한다.
- 업로드는 `postDocument`(`admin.html:1461-1480`)의 FormData 패턴(file + actor JSON).
- 색인 중에는 3초 폴링, 완료·실패에서 멈춘다.

### 검수 표시

- 기존 「왜 이 등급?」 확장(`toggleWhy`·`renderWhy`, `GET /review-queue/{id}/evidence`)이 열릴 때 **별도 요청**으로 `GET /documents/{doc_id}/regulation-evidence`를 부른다(`WHY_CACHE`와 같은 방식으로 캐시). 기존 근거 응답은 건드리지 않는다.
- 결과가 비면 **블록 자체를 그리지 않는다**(404·`items:[]`·`indexed:false` 모두). 503만 흐린 한 줄 "규정 참고를 불러오지 못했습니다".
- 검수 큐 항목이 `doc_id`를 싣는지는 **확인하지 못했다**(U-05). 없으면 큐 응답에 추가 전용 필드로 더한다.
- 골든셋 검수 화면(`manage.html`·`review.html`·`signoff.html`)에는 **넣지 않는다**(P7).

### 문구 (초안 — 형식 시험 준수)

| 자리 | 문구 |
|---|---|
| 카드 제목 | 사내 규정(참고 표시) |
| 안내 | 올려 둔 규정에서 검수 중인 문서와 관련된 원문 문장을 참고로 보여 줍니다. 등급을 바꾸지 않습니다. |
| 업로드 진행 | 규정을 분석하는 중… |
| 분할 경고 | 조 단위 구분이 없어 정확도가 낮을 수 있습니다. |
| 활성화 확인(D-12) | 이 규정은 검수 대상 문서의 취급 기준입니다. |
| 검수 블록 제목 | 관련 규정(참고) |
| 고정 문구 | 이 내용은 참고용이며 등급 판정 근거가 아닙니다. |

### 콘솔 시험이 요구하는 것 (체크리스트)

1. **신뢰도·유사도 수치 미표시**: e2e에 "숫자가 안 뜬다" 시나리오. `test_golden_assignment_panel.py`처럼 패널에 "신뢰도"·"confidence" 금지.
2. **구현 정보 금지 문자열**(`test_console_forbidden_strings.py`·e2e `16_forbidden_strings.mjs`): `version_label`·`.jsonl`·`scripts/` 등. 카드 안 화면 문구에 필드 이름을 노출하지 않는다.
3. **쓰이지 않는 입력칸·prompt 창 금지**: 시행일은 저장·표시에 쓰이므로 두되, 쓰이지 않는 칸은 두지 않는다.
4. **문구 형식**(`test_console_wording.py`): 「불러오는 중…」, 버튼은 「」.
5. **회색 글자 대비 ≥ #71717a**(`test_console_contrast.py`), 외부 폰트·CDN 금지.
6. **`onclick` 함수 실재·문법**(`test_static_console_screens_run.py`), 전 버튼 클릭 스윕(`10_sweep.mjs`).
7. **API 계약**(`test_e2e_console_api_contract.py`): 콘솔이 부르는 METHOD·경로가 실제 라우트, `tests/e2e_console/lib/fixtures.json`에 응답 본보기, 응답은 `response_model`로 검증(response_model 없는 엔드포인트 상한 11 — **새 엔드포인트는 모두 response_model을 둔다**). 새 JS 파일을 만들면 `_CONSOLE_SOURCES`·wording·contrast 목록에 손으로 추가해야 검사 대상이 된다(가능하면 새 파일 없이 `admin.html` 인라인로).
8. 새 e2e 시나리오 `18_regulation.mjs`: 카드 표시/숨김, 업로드→진행→활성화, 검수 블록 표시/빈 경우, 503 고장 주입, 저장형 XSS 문자열.

## 2.8 권한·보안·감사

| 항목 | 설계 |
|---|---|
| 권한 | §2.6 표. 공유 API 키(`system` 역할)는 규정 쓰기·검수 읽기 모두 불허(역할 검사로 자연히 막힘) |
| 원문 보관 | 버킷 `regulations-raw` 암호화(AES-256-GCM, `storage_encrypted_buckets`에 추가). **다운로드 경로를 만들지 않는다.** 삭제하면 원본·조항·문장·벡터가 함께 지워진다 |
| 규정 본문 | 조항·문장 텍스트는 DB에 평문(청크 표와 같은 수준). PII 마스킹 파이프라인은 거치지 않는다(규정 원문 왜곡 방지) |
| 저장형 XSS | 규정 텍스트·규정명·설명은 모두 `textContent`/`esc()`. 시험에 `<img onerror>`·`<script>`·NFD 한글 |
| 감사 | 명시 기록 `audit_repo.record(action=…, target_type="regulation", target_id=reg_id, …)`: `regulation.upload`·`regulation.activate`·`regulation.archive`·`regulation.delete`·`regulation.clause_update`. (요청 단위 자동 기록은 미들웨어가 하며 URL 첫 세그먼트가 action이다. 등급체계·키워드 변경은 명시 기록이 없었던 전례라, 이번에는 넣는다) |
| 골든셋 | 규정 표시는 운영 검수 화면에만. `golden_review_blind_enforced`가 켜져도 이 기능은 영향이 없다(운영 검수자도 `reviewer` 역할이므로 역할로 구분하지 않는다) |
| 반출 | 외부 호출 없음. 임베딩은 로컬 운영 임베더 |

## 2.9 설정 플래그와 배포

| 이름 | 기본 | 뜻 |
|---|---|---|
| `regulation_reference_enabled` | **False** | 라우터·워커 태스크·화면 노출. 꺼져 있으면 무동작 |
| `regulation_evidence_max_items` | 1 | 표시 개수(1~3, validator) |
| `regulation_min_similarity` | 0.0 | 0이면 끔(값은 파일럿에서 결정) |
| `regulation_max_sentences` | 3000 | 색인 상한 |
| `regulation_active_max` | 5 | 동시 활성 규정 수 |

- 파일 크기는 기존 `max_upload_mb`를 재사용한다.
- 켜는 방식: 기본은 프로파일에서 켜지 않는다. `.env`에 `REGULATION_REFERENCE_ENABLED=1`. `.env.example`·`.env.onprem-local`·`.env.full-train`에 **주석 처리된 예시**와 설명(두 프로파일 env 파일에는 다른 작업의 미커밋 변경이 있다 — 병합 주의), `docs/INSTALL.md`의 표준 `.env` 체크리스트에 항목을 추가한다(블라인드 손잡이가 체크리스트에 없어 배포 전 수동 추가가 필요했던 전례).
- **전제 조건 검사**: 켰는데 `embedding_provider="hash"`이거나 `require_real_embedder=false`이면 기동 경고(또는 config 검증 거절)를 낸다.
- **시험**: `test_deploy_profile.py`에 라우터 게이트 프로파일 매트릭스 추가, `test_env_templates_*` 계열에 "코드 기본값 False" 잠금, `scripts/audit_flag_cost.py`·`audit_wiring.py`가 새 플래그·죽은 정의로 잡지 않는지 확인.
- **롤백**: 플래그를 끄면 라우트·화면이 사라진다(데이터는 남는다). 마이그레이션은 downgrade를 준비한다.
- **배포는 사용자 지시가 있을 때만**이다. 이 문서의 어떤 단계도 서버 반영을 포함하지 않는다.

## 2.10 성능·용량·운영

| 항목 | 추정(미측정) | 확인 방법 |
|---|---|---|
| 색인 시간 | (조항+문장) × 0.51초 — 시연규정 ≈ 2분, 상한 3,000문장 ≈ 26분 | Phase 0에서 KURE-v1 문장 임베딩 시간 실측 |
| 색인 중 워커 점유 | 동시성 2(`worker_concurrency`)라 큐 `index`의 문서 벡터 색인과 경쟁 | 색인 중 문서 업로드 지연 확인 |
| 메모리 | 프로세스당 규정 벡터 ≤ 15MB | 캐시 로드 후 RSS 확인 |
| 조회 지연 | DB 2회 + 행렬곱, p95 200ms 예산 | Phase 1 성능 시험 |
| 관측 | 메트릭 `regulation_evidence_requests_total{result}`(빈 이유별), `regulation_index_seconds` | prom_metrics 관례 |

## 2.11 시험 계획

| 종류 | 대상 | 비고 |
|---|---|---|
| 단위 | 분할기(제N조·굵은 글씨·번호 제목·문단 모드·부칙 id·머리글 반복 제거), 문장 분할, 서두 판정, 목록 묶음(라벨 고정 아님), 태깅 규칙 | 형식별 픽스처(시연규정, 지침 발췌, 번호 제목 규정, 문단 규정) |
| 통합 | 업로드→색인→활성화→조회(가짜 임베더·in-memory 저장소, DB 없이 도는 시험) | 서비스가 DB 예외를 삼키는 관례와 다르게 **실패를 상태로 남긴다** |
| API | 권한 매트릭스, 상태 전이, 중복, 413/422/503, 활성 상한, 같은 규정명 이전 판 자동 보관 | `TestClient` + `X-Actor-Role` 헤더 선례 |
| PG 필요(fullstack) | 마이그레이션 왕복, `alembic check` 무드리프트, 표 3개 제약 | `make test-db-up` → `make test-full` |
| 표준명·감사 도구 | `test_standard_names.py`(구조 확장 반영), `test_db_models.py` 기대 갱신, `audit_schema_consistency.py` R1~R7, 루트 `build_table_spec.py --check` | W-01 |
| ICD | `test_openapi_contract_matches_routes.py`, `test_kl_openapi_schema_matches_code.py` | 같은 커밋 |
| 콘솔 | §2.7 체크리스트, e2e `18_regulation.mjs`, 대비·금지 문자열·API 계약 | `make test-console-e2e` |
| **회귀** | 플래그 꺼짐·켜짐 모두 분류 응답·검수 라우팅 불변. **고치기 전에 `regression_gate.py --snapshot`으로 스냅샷** | P1의 직접 시험 |
| 품질 | 시험 도구(`measure_regulation_extract.py`)를 운영 임베더로 재실행, 회원사 규정 파일럿 | Phase 0/3 |
| 보안 | 저장형 XSS, 경로 탈출 파일명, 역할별 접근, 삭제 후 원본 잔존 없음 | |
| 결정성 | 같은 입력 두 번 → 같은 응답, 동점 정렬 | |

## 2.12 구현 작업 분해

규모: S(반나절 이내로 보이는 것) · M · L. **일정 추정은 하지 않는다**(근거 없음).

| ID | 작업 | 선행 | 규모 | 검증 |
|---|---|---|---|---|
| W-01 | 표준명 구조 확장 + 용어집 대조·자체표준 용어 목록 + 관련 시험·도구 갱신 | D-08 | M | 표준명·감사 시험 |
| W-02 | 운영 임베더(KURE-v1)로 시험 재실행(조회 적중·문장 선택·문장 임베딩 시간) — 시험 도구의 임베더 부분을 교체 가능하게 | | M | 시험값과 같은 방향 |
| W-03 | 태깅·분할 규칙을 **다른 규정 2건 이상**으로 재검증(회원사 규정, 없으면 두 번째 시연규정을 다른 저자로) | | M | 일치율 |
| W-04 | D-01 확인(협의 저촉) | | S | 결정 기록 |
| W-05 | ORM·마이그레이션·저장소 | W-01 | M | fullstack |
| W-06 | 분할기·태깅기·문장 분할 | | M | 단위 |
| W-07 | 색인 서비스·워커·버킷 암호화 설정·작업 제한 | W-05, W-06 | L | 통합 |
| W-08 | `DocumentVectorStore.get` + 조회 엔진·캐시 | W-05 | M | 단위·결정성 |
| W-09 | API·스키마·RBAC·감사·ICD·라우터 등록 | W-07, W-08 | L | API·ICD 시험 |
| W-10 | 플래그·프로파일·env 템플릿·INSTALL | W-09 | S | 배포 프로파일 시험 |
| W-11 | 관리자 카드 | W-09 | L | 콘솔 시험 |
| W-12 | 검수 표시(+큐 `doc_id` 확인) | W-09 | M | 콘솔 시험 |
| W-13 | 콘솔 시험 일체(e2e·safety·fixtures·목록) | W-11, W-12 | M | `make test-console-e2e` |
| W-14 | 회귀 스냅샷·성능 시험 | W-09 | S | 불변·p95 |
| W-15 | 파일럿·표본 판정(두 사람)·문턱 결정 | W-13, 회원사 규정 | M | §1.8 |
| W-16 | (선택) F-13 피드백 | D-07 | M | |
| W-17 | 배포 준비: 번들 임베더 확인(R-10)·플래그 안내 | W-10 | S | **배포는 지시 시** |

## 2.13 검토했으나 채택하지 않은 안

| 안 | 기각 사유 |
|---|---|
| LLM으로 조항 요약 | CPU 문서당 95초, 고객사 대부분 GPU 없음. 개선해도 44/0으로 추출식 35/0과 표본 오차 안(이득이 작다) |
| 규정 전체를 화면에 올려 두고 검색만 제공 | 검수 중 맥락을 벗어난다(검수자가 찾아야 한다). 이 기능의 가치는 "문서에 맞춰 골라 주는 것" |
| pgvector 표에 벡터 저장 | 규모가 작아 인덱스 이득이 없고, `vector` 칼럼은 alembic 예외·감사 도구 예외를 늘린다 |
| 규정에서 등급 매핑표를 자동 생성 | 두 규정에서 실패(0/11, 3/10). 담당자 입력물 |
| 표시 점수로 관련 없는 문서를 거름 | 업무문서 안에서 AUROC 0.68, 규정 적용성은 0.33 — 소용없다 |
| 검수 큐 라우팅에 규정 사용 | 정책 엔진의 원칙(확률적 요소 배제)과 ACL 공급 70% 하한. 별개 과제 |

---

# 부록

## A. 확인하지 못한 것 (착수 시 확인)

| ID | 내용 |
|---|---|
| U-01 | 표준용어집 파일이 저장소에 없다 — 표·칼럼 표준명 후보 전부 대조 필요, 영역 코드 목록·뜻을 적은 문서를 못 찾음 |
| U-02 | 추출기가 쪽 경계를 주는지(머리글·꼬리말 반복 제거 방식). PDF 머리글·꼬리말 별도 처리는 못 찾음 |
| U-03 | 문장 단위 임베딩 시간(KURE-v1) — 청크당 0.51초는 청크 기준 실측 |
| U-04 | 등급별 목록 묶음의 일반화(라벨 이름 비고정) — 시험은 라벨 고정 |
| U-05 | 검수 큐 항목이 `doc_id`를 싣는지 |
| U-06 | ICD의 `source_type` 값 목록(D-06 판정에 필요) |
| U-07 | 여러 규정 동시 활성 시의 조회 품질(시험은 규정 1개) |
| U-08 | 표시 근거 조항의 등급이 문서 정답 등급과 같은지(앵커링 위험, R-05) |
| U-09 | HWP·표 위주 규정에서의 분할 품질(시험 규정은 서술형·PDF/마크다운) |
| U-10 | 회원사 규정으로는 어떤 수치도 재지 않았다 |

## B. 시험 산출물 (재현 경로)

| 무엇 | 경로 |
|---|---|
| 규정 텍스트 추출·조각·조회 | `poc/scripts/measure_regulation_evidence.py` (`--format pages|article`) |
| LLM 요약 시험 | `poc/scripts/measure_regulation_summary.py` |
| 추출식 표시 시험 | `poc/scripts/measure_regulation_extract.py` (`--modes S1,S2,S1c`) |
| 설계 가정 확인 | `poc/reports/CLAUDE_REGULATION_RAG_20260925/design_checks.py`, `design_checks2.py` |
| 시연용 규정 | `poc/reports/CLAUDE_REGULATION_RAG_20260925/sample_org_regulation.md` |
| 정답 조항 집합 | `.../sample_org/labels_71.json` |
| 매핑표 채움 결과 | `.../out_A_mapping_fill.md`, `.../out_A2_sample_org_mapping_fill.md` |

## C. 이 설계가 기대는 기존 코드(조사 결과, 착수 시 재확인)

| 관례 | 위치 |
|---|---|
| 라우터 등록·조건 등록 | `api/app.py:392-438`(training `:403`, synthesis `:416`) |
| 역할·`require_role` | `api/_jwt_auth.py:232`, `api/_rbac.py:16-37` |
| 3층 최소 예 | `api/guide.py`·`services/guide_service.py`·`schemas/guide.py` |
| 파일 업로드·크기·중복 | `api/documents.py:61-127`, `services/document_ingestion_service.py:212-223,481-503` |
| 유사 문서 조회·색인 워커 | `api/documents.py:561-607`, `workers/tasks.py:1198-1224`, `services/document_vector_service.py` |
| 임베더 | `adapters/embedding/__init__.py:105`(`build_embedder`), `config.py:853-858,666` |
| 저장소·암호화 | `adapters/storage/{local_store,encrypted_store}.py`, `config.py:268-270` |
| 추출기 | `modules/m2_preprocess/extractor.py:44-52,168` |
| 감사 | `db/models.py:584-616`, `repositories/audit_repo.py:25-69` |
| 표준명 | `db/standard_names.py:34-61,340-352`, `tests/test_standard_names.py` |
| 검수 화면 | `api/static/admin.html:1726-1864`(검토 대기·근거 패널), `api/confirm.py:133-148` |
| 콘솔 시험 | `tests/e2e_console/`, `tests/test_e2e_console_api_contract.py:32-38,263-329` |
| ICD 시험 | `tests/test_openapi_contract_matches_routes.py:91-98`, `tests/test_kl_openapi_schema_matches_code.py:129-156` |
