"""PSH 결과 비교 파싱."""
import json
import sys
from pathlib import Path

report_dir = sys.argv[1] if len(sys.argv) > 1 else "reports/psh_full_2026-06-01b"
d = json.loads(Path(f"{report_dir}/perf_latest_full.json").read_text("utf-8"))
s = d["summary"]
print(f"KPI: PASS={s['pass']} FAIL={s['fail']} SKIP={s['skip']} / {s['total_kpis']} ({s['pass_rate']*100:.1f}%)")
print(f"시나리오: PASS={s['scenarios']['pass']} FAIL={s['scenarios']['fail']} / 18\n")

for sc in d["scenarios"]:
    sid    = sc["scenario"]
    stitle = sc["title"][:28]
    sstatus = sc["status"]
    kpis   = sc.get("kpis", [])
    p  = sum(1 for k in kpis if k["status"] == "PASS")
    f  = sum(1 for k in kpis if k["status"] == "FAIL")
    sk = sum(1 for k in kpis if k["status"] == "SKIP")
    print(f"  {sid}: {stitle:<28} [{sstatus}] P={p} F={f} S={sk}")
    for k in kpis:
        if k["status"] == "FAIL":
            print(f"       FAIL {k['kpi_id']}: {k['name']} = {k['measured']:.1f} (goal {k['compare']} {k['threshold']})")
