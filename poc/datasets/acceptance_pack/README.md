# Lloydk 인수(acceptance) 샘플팩

고객 폐쇄망 배포 후 parse -> classify -> gate 를 실문서 포맷으로 검증하는 자기완비 팩.

- 소스: 공개+합성 혼합. 실비밀 텍스트 0 (공개 판례/특허 텍스처 + [가상기업A] 합성).
- 포맷: TXT/PDF/DOCX/XLSX/XLS/PPTX/HWPX (HWP 바이너리는 파이썬 생성 불가 -> HWPX 로 대체).
- 판정: 정확 등급일치가 아니라 **severity floor**(고등급 미탐=veto) + 숫자 무손실 + 게이트 가시성.
  서빙은 의도적 안전방향 과분류 -> 정확도로 합격/불합격하지 않는다.

## 실행
- 폐쇄망 번들(호스트): `API_KEY=<키> BASE_URL=http://localhost:8000 bash run_acceptance.sh` (run_acceptance.sh 는 번들 빌드시 자동 동봉)
- 레포 보유(dev): `make acceptance-test` 또는 `python scripts/run_acceptance.py --mode inproc`

재생성: `make acceptance-pack` (PDF/XLS 는 reportlab/xlwt 설치 시 포함).
