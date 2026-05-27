"""PSH KPI 결과를 Prometheus pushgateway로 전송.

설계:
- 별도 의존성 없이 stdlib urllib만 사용 (폐쇄망 안전, prometheus_client 회피)
- Prometheus exposition 텍스트 포맷 v0.0.4
- 게이지 메트릭:
    lloydk_psh_kpi_measured{kpi_id,scenario,unit,compare,mode,git_sha} = measured 값
    lloydk_psh_kpi_threshold{...} = threshold
    lloydk_psh_kpi_passed{kpi_id,scenario,mode,git_sha} = 1/0/-1
    lloydk_psh_summary_{pass,fail,skip,total} / lloydk_psh_pass_rate
- pushgateway URL: 환경변수 PROM_PUSHGATEWAY_URL 또는 --pushgateway-url

실패 정책: 전송 실패는 silent warning — PSH 자체는 영향 없음.
"""

from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger("lloydk.perf.pushgateway")

_LABEL_SANITIZE = re.compile(r"[^A-Za-z0-9_]")


def _sanitize_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def build_exposition(report: dict[str, Any]) -> str:
    mode = _sanitize_label(report.get("mode", "dryrun"))
    env = report.get("env", {}) or {}
    git_sha = _sanitize_label(env.get("git_sha", "unknown"))
    common_labels = f'mode="{mode}",git_sha="{git_sha}"'

    lines: list[str] = []
    summ = report.get("summary", {}) or {}
    lines.append("# HELP lloydk_psh_summary_pass total PASS KPI count")
    lines.append("# TYPE lloydk_psh_summary_pass gauge")
    lines.append(f'lloydk_psh_summary_pass{{{common_labels}}} {summ.get("pass", 0)}')

    lines.append("# HELP lloydk_psh_summary_fail total FAIL KPI count")
    lines.append("# TYPE lloydk_psh_summary_fail gauge")
    lines.append(f'lloydk_psh_summary_fail{{{common_labels}}} {summ.get("fail", 0)}')

    lines.append("# HELP lloydk_psh_summary_skip total SKIP KPI count")
    lines.append("# TYPE lloydk_psh_summary_skip gauge")
    lines.append(f'lloydk_psh_summary_skip{{{common_labels}}} {summ.get("skip", 0)}')

    lines.append("# HELP lloydk_psh_summary_total total KPI count")
    lines.append("# TYPE lloydk_psh_summary_total gauge")
    lines.append(f'lloydk_psh_summary_total{{{common_labels}}} {summ.get("total_kpis", 0)}')

    lines.append("# HELP lloydk_psh_pass_rate PASS / total ratio")
    lines.append("# TYPE lloydk_psh_pass_rate gauge")
    lines.append(f'lloydk_psh_pass_rate{{{common_labels}}} {summ.get("pass_rate", 0)}')

    lines.append("# HELP lloydk_psh_kpi_measured KPI measured value")
    lines.append("# TYPE lloydk_psh_kpi_measured gauge")
    lines.append("# HELP lloydk_psh_kpi_threshold KPI threshold value")
    lines.append("# TYPE lloydk_psh_kpi_threshold gauge")
    lines.append("# HELP lloydk_psh_kpi_passed KPI status (1=PASS, 0=FAIL, -1=SKIP, -2=ERROR)")
    lines.append("# TYPE lloydk_psh_kpi_passed gauge")

    status_to_value = {"PASS": 1, "FAIL": 0, "SKIP": -1, "ERROR": -2}
    for scen in report.get("scenarios", []) or []:
        sc_id = _sanitize_label(scen.get("scenario", ""))
        for k in scen.get("kpis", []) or []:
            kid = _sanitize_label(k.get("kpi_id", ""))
            unit = _sanitize_label(k.get("unit", ""))
            compare = _sanitize_label(k.get("compare", ""))
            status = k.get("status", "SKIP")
            measured = float(k.get("measured", 0) or 0)
            threshold_raw = k.get("threshold")
            try:
                if isinstance(threshold_raw, bool):
                    threshold = 1.0 if threshold_raw else 0.0
                else:
                    threshold = float(threshold_raw) if threshold_raw is not None else 0.0
            except (TypeError, ValueError):
                threshold = 0.0
            kpi_labels = (
                f'kpi_id="{kid}",scenario="{sc_id}",unit="{unit}",compare="{compare}",{common_labels}'
            )
            lines.append(f"lloydk_psh_kpi_measured{{{kpi_labels}}} {measured}")
            lines.append(f"lloydk_psh_kpi_threshold{{{kpi_labels}}} {threshold}")
            lines.append(
                f'lloydk_psh_kpi_passed{{kpi_id="{kid}",scenario="{sc_id}",{common_labels}}} '
                f"{status_to_value.get(status, -1)}"
            )

    return "\n".join(lines) + "\n"


def push(
    report: dict[str, Any],
    *,
    url: str,
    job: str = "lloydk_psh",
    timeout: float = 10.0,
) -> bool:
    """exposition payload를 Prometheus pushgateway PUT."""
    if not url:
        return False
    payload = build_exposition(report)
    full_url = url.rstrip("/") + f"/metrics/job/{job}"

    req = urllib.request.Request(
        full_url,
        data=payload.encode("utf-8"),
        method="PUT",
        headers={"Content-Type": "text/plain; version=0.0.4"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ok = 200 <= resp.status < 300
            if not ok:
                logger.warning("pushgateway non-2xx response: %s", resp.status)
            return ok
    except urllib.error.URLError as exc:
        logger.warning("pushgateway URL error: %s", exc)
        return False
    except Exception as exc:
        logger.warning("pushgateway unexpected error: %s", exc)
        return False
