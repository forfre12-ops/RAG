"""동기 분석 경로의 페이지 규모별 처리량 측정 — 용량 한계를 숫자로 고정한다.

배경(2026-08-02 테스트서버 실측) — 구형 .hwp 업로드 시 gunicorn 워커가 강제 재시작되는
증상을 추적한 결과, 파서가 아니라 **다운스트림 용량**이 원인이었다:

    추출(표 47개 회수 포함)  0.48s
    전처리 → 청크 155개      0.32s
    HTTP /documents/analyze  60s 초과 → WORKER TIMEOUT → SIGABRT

즉 파싱은 0.8초에 끝나고 나머지가 전부 청크 분류다. 표 회수가 정상 동작할수록 문서가
커져(10,439자 → 46,473자) 이 경로가 더 위험해진다 — 파서 수정과 용량이 맞물린다.

측정값(지재원 스택 · API 컨테이너 2 CPU 제한 · 호스트 8코어):
    1페이지(1,800자)   12.7s
    5페이지(9,000자)   45.3s      · OMP 스레드 2로 맞춰도 39.5s (13% 개선뿐)
    100페이지 청크 수  351개      · 선형 외삽 약 13~14분
    → gunicorn --timeout 60 기준 약 6~7페이지에서 죽는다.

스레드 오버서브스크립션(컨테이너 2 CPU인데 torch 8스레드)은 부차적이고, 병목은
**할당 CPU 자체**다. API_CPU_LIMIT 를 올린 뒤 이 스크립트를 다시 돌려 비례하는지 본다.

사용(컨테이너 안에서 — gunicorn 워커를 건드리지 않는다):
    docker cp scripts/probe_ingest_capacity.py <api>:/tmp/p.py
    docker exec <api> python /tmp/p.py
    docker exec -e OMP_NUM_THREADS=6 <api> python /tmp/p.py    # 스레드 변경 비교
"""

from __future__ import annotations

import os
import time

PARA = (
    "본 사업의 총 사업비는 15,000천원이며 지원 한도는 1,000천원이다. "
    "담당 부서는 연구기획팀이고 사업 기간은 2026년 3월부터 12월까지다. "
    "세부 과제별 배분과 평가 지표는 별표에 따른다. "
)
PAGE_CHARS = 1800     # 한글 A4 1페이지 ≈ 1,800자
GUNICORN_TIMEOUT = 60
STOP_AFTER_SEC = 180  # 이보다 오래 걸리면 이후 규모는 재지 않는다(서버 점유 최소화)


def main() -> int:
    import torch

    from lloydk.modules.m2_preprocess.pipeline import PreprocessPipeline
    from lloydk.schemas.classify import ClassifyRequest
    from lloydk.services.classify_service import ClassifyService

    print(f"torch threads={torch.get_num_threads()} "
          f"OMP={os.environ.get('OMP_NUM_THREADS')} "
          f"cpu_count={os.cpu_count()}")
    print(f"gunicorn --timeout 기준선: {GUNICORN_TIMEOUT}s\n")

    pre = PreprocessPipeline()
    svc = ClassifyService()
    # 워밍업 — 모델 로드 비용을 첫 케이스에 섞지 않는다.
    svc.classify(ClassifyRequest(doc_id="warmup", content=PARA))

    print("페이지 |  글자수 | 청크 |   소요 | 판정")
    print("-" * 46)
    for pages in (1, 5, 10, 25, 50, 100):
        text = (PARA * ((PAGE_CHARS * pages) // len(PARA) + 1))[: PAGE_CHARS * pages]
        chunks = len(getattr(pre.run_text_full(text), "chunks", None) or [])
        t0 = time.perf_counter()
        svc.classify(ClassifyRequest(doc_id=f"probe-{pages}p", content=text))
        el = time.perf_counter() - t0
        verdict = "OK" if el < GUNICORN_TIMEOUT else "동기경로 사망"
        print(f"{pages:>5}p | {len(text):>7} | {chunks:>4} | {el:>5.1f}s | {verdict}",
              flush=True)
        if el > STOP_AFTER_SEC:
            print(f"      ({STOP_AFTER_SEC}s 초과 — 이후 규모 생략, 선형 외삽할 것)")
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
