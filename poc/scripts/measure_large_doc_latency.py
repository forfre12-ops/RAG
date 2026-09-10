#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""대용량 문서 한 건의 판별 시간과 그동안의 CPU·메모리를 잰다 — PER-002 "문서 등록 또는 판별 30초".

왜 이 도구가 있는가(2026-09-11). 성능 하니스(run_perf_scenarios.py)의 판별 지연은 한 줄짜리 짧은
문서로만 잰다. 품질관리계획서의 처리시간 기준은 "100 Page 미만 문서 한 건"인데 그 크기를 잰
적이 없다. 이 도구는 문서 하나를 동기(/classify)와 비동기(/classify/async → 작업 조회) 두 경로로
보내 걸린 시간을 재고, 그동안 기계 전체의 CPU·메모리를 0.1초마다 표본으로 남긴다.

⚠ 하니스와 같이 **앱을 프로세스 안에서 부른다**(TestClient). 서버 api 컨테이너 안에서 돌려야
  서버의 값이 나온다. 비동기 경로는 브로커가 있으면 실제 워커가 처리한다.
⚠ '쪽'은 이 도구가 정하지 않는다 — 넘겨 준 본문의 글자 수를 그대로 적는다.

사용(서버 api 컨테이너 안):
    python scripts/measure_large_doc_latency.py --text /tmp/large_doc.txt [--skip-sync]
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_POC / "src"))

_TERMINAL = {"done", "completed", "succeeded", "success", "failed", "error", "cancelled", "canceled"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="대용량 문서 판별 시간 실측")
    ap.add_argument("--text", required=True)
    ap.add_argument("--doc-prefix", default="psh-large")
    ap.add_argument("--skip-sync", action="store_true")
    ap.add_argument("--timeout", type=float, default=900)
    a = ap.parse_args(argv)

    import psutil  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from koipa.api.app import app  # noqa: PLC0415
    from koipa.perf.scenarios import _hdr  # noqa: PLC0415

    text = Path(a.text).read_text(encoding="utf-8")
    samples: list[tuple[float, float]] = []
    stop = threading.Event()

    def _sample() -> None:
        psutil.cpu_percent(interval=None)
        while not stop.is_set():
            samples.append((psutil.cpu_percent(interval=None), psutil.virtual_memory().percent))
            stop.wait(0.1)

    th = threading.Thread(target=_sample, daemon=True)
    th.start()
    res: dict = {"chars": len(text)}
    stamp = time.strftime("%H%M%S")
    with TestClient(app) as cli:
        if not a.skip_sync:
            t = time.perf_counter()
            r = cli.post("/api/v1/classify", headers=_hdr(),
                         json={"doc_id": f"{a.doc_prefix}-sync-{stamp}", "content": text})
            res["sync_ms"] = round((time.perf_counter() - t) * 1000, 1)
            res["sync_status"] = r.status_code
            try:
                body = r.json()
                res["sync_keys"] = sorted(body)[:20]
                res["sync_grade"] = body.get("grade") or body.get("predicted_grade") or body.get("level")
                res["sync_chunks"] = body.get("chunk_count")
            except ValueError:
                res["sync_body"] = r.text[:300]
        t = time.perf_counter()
        r = cli.post("/api/v1/classify/async", headers=_hdr(),
                     json={"doc_id": f"{a.doc_prefix}-async-{stamp}", "content": text})
        res["async_accept_ms"] = round((time.perf_counter() - t) * 1000, 1)
        res["async_status"] = r.status_code
        job = {}
        try:
            job = r.json()
        except ValueError:
            res["async_body"] = r.text[:300]
        jid = job.get("job_id")
        last = None
        while jid and time.perf_counter() - t < a.timeout:
            rj = cli.get(f"/api/v1/classify/jobs/{jid}", headers=_hdr())
            try:
                last = rj.json()
            except ValueError:
                last = {"raw": rj.text[:200]}
            if str(last.get("status", "")).lower() in _TERMINAL:
                break
            time.sleep(1.0)
        res["async_total_ms"] = round((time.perf_counter() - t) * 1000, 1)
        res["async_final_status"] = (last or {}).get("status")
        if isinstance(last, dict):
            # 작업 결과는 results 목록(async_classify_service: results=[result_json])에 담긴다.
            rr = last.get("result") or ((last.get("results") or [None])[0]) or {}
            if isinstance(rr, str):
                try:
                    rr = json.loads(rr)
                except ValueError:
                    rr = {}
            res["async_grade"] = rr.get("grade") or rr.get("predicted_grade") if isinstance(rr, dict) else None
            res["async_chunks"] = rr.get("chunk_count") if isinstance(rr, dict) else None
    stop.set()
    th.join(timeout=1)
    if samples:
        res["cpu_peak"] = max(s[0] for s in samples)
        res["mem_peak"] = max(s[1] for s in samples)
        res["cpu_mean"] = round(sum(s[0] for s in samples) / len(samples), 1)
    res["n_samples"] = len(samples)
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
