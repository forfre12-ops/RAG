# OSS 라이선스 — GPL-3.0 전문 (✅ 2026-08-12 해소)

작성 2026-08-12. 이미 해소된 것을 다시 적지 않는다(그건
`오픈소스_라이선스_다중배포_검토서.html` 참조).

> **✅ 해소됨.** `poc/licenses/GPL-3.0.txt` 추가 완료 (674줄 ·
> sha256 `3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986`,
> 출처 https://www.gnu.org/licenses/gpl-3.0.txt).
> `poc/licenses/` 가 gitignore 대상이라 `git add -f` 로 명시 추적한다.
> 아래는 **왜 필요했는지**의 기록이다 — 같은 판단을 다시 하지 않기 위해 남긴다.

---

## 1. 잔여 — `GPL-3.0.txt` 전문 미동봉

### 사실관계

- `psycopg[binary]>=3.2` 는 **핵심 의존성**이다(`pyproject.toml` line 27, `[project.optional-dependencies]`
  가 아니라 `dependencies`). 즉 **모든 배포본에 들어간다.**
- psycopg / psycopg-binary 의 선언 라이선스는 **LGPL-3.0-only** 다.
- LGPL-3.0 전문은 **wheel 에 이미 동봉되어 있다** — 실측
  `psycopg-3.3.4.dist-info/licenses/LICENSE.txt` (165줄, "GNU LESSER GENERAL PUBLIC LICENSE
  Version 3"). dist-info 는 wheel 설치 시 함께 배포되므로 별도 조치가 필요 없다.
  ⚠ `licenses/third-party-licenses.txt` 만 보면 "전문 없음"으로 **오판**하기 쉽다.
  그 파일은 목록이지 전문 모음이 아니다.
- **LGPL-3.0 본문은 GPL-3.0 을 참조로 편입한다** — 첫 조항이
  "This version of the GNU Lesser General Public License incorporates the terms and
  conditions of version 3 of the GNU General Public License, supplemented by the
  additional permissions listed below." 이다.
  따라서 LGPL-3.0 전문만으로는 조건 전체가 제시되지 않는다.

### 판단

- psycopg 상류(upstream)는 LGPL 전문만 동봉하고 GPL-3.0 전문은 넣지 않는다. 상류 관행을
  그대로 따르는 것도 방어 가능한 입장이다.
- 다만 **폐쇄망 오프라인 배포**에서는 참조된 URL 을 열 수 없다. 수령자가 조건 전체를
  확인할 수단이 물리적으로 없으므로, **GPL-3.0 전문을 함께 동봉하는 쪽이 안전**하다.

### 조치 (완료)

```bash
curl -o poc/licenses/GPL-3.0.txt https://www.gnu.org/licenses/gpl-3.0.txt
git add -f poc/licenses/GPL-3.0.txt     # licenses/ 는 gitignore 대상(자동 산출물 폴더)
```

검증: 674줄 · 첫 줄 `GNU GENERAL PUBLIC LICENSE` · 둘째 줄 `Version 3, 29 June 2007` ·
sha256 `3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986`.

> ⛔ **이 파일은 지어내지 않았다.** 법률 문서라 한 글자도 달라지면 안 되고 요약·의역은
> 라이선스 텍스트로 성립하지 않는다. 저장소·venv 어디에도 verbatim 원문이 없어
> 상류에서 받아 왔다. 재생성이 필요하면 위 URL 에서 다시 받고 sha256 을 대조할 것.

**남은 배선**: 폐쇄망 배포 아카이브 빌드가 `poc/licenses/` 를 포함하는지 확인.
포함하지 않으면 저장소에만 있고 수령자에게는 가지 않는다.

---

## 2. 오해하기 쉬운 것 두 가지 (재확인용)

### 2.1 dev venv 의 AGPL 패키지는 배포 대상이 아니다

`.venv/Lib/site-packages/hwp5/` (구 pyhwp, **AGPL-3.0**) 가 개발 venv 에 남아 있다.
그러나 `pyproject.toml` 의 선언 의존성이 **아니다** — 2026-08-01 에 `unhwp`(MIT) 로
교체하면서 AGPL 옵트인 경로 자체를 없앴고(`pyproject.toml` line 89), 현행 HWP 경로는
`rhwp-python>=0.8.1` + `unhwp` 다.

즉 **개발 환경에 설치돼 있을 뿐 배포본에 들어가지 않는다.** venv 를 스캔하는 도구는
이 잔재를 AGPL 위반처럼 보고하므로, 라이선스 점검은 **`pyproject.toml` 선언 의존성 기준**
으로 하고 venv 스캔 결과는 그대로 인용하지 않는다.

정리하려면: `pip uninstall pyhwp` (기능 영향 없음 — 어느 코드도 import 하지 않는다).

### 2.2 PyMuPDF(AGPL-3.0/Artifex)도 선언 의존성이 아니다

`third-party-licenses.txt` 에 `AGPL-3.0 / Artifex Commercial` 로 잡히지만 마찬가지로
선언 의존성이 아니다. 배포 extras 에 포함되는지가 유일한 판단 기준이다.

---

## 3. 원칙 한 줄

**카피레프트는 배포에 걸리지 소스 트리에 이름이 있다고 걸리지 않는다.**
판단 기준은 "무엇이 배포 아카이브에 들어가는가" 하나이며, venv 스캔이나 목록 파일의
문자열 매칭은 근거가 아니다.

---

관련: `doc/result/KL_AI자료_2026-08/오픈소스_라이선스_다중배포_검토서.html` ·
`poc/pyproject.toml` · `poc/scripts/dump_licenses.py`
