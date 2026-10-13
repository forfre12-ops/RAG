#!/usr/bin/env bash
# Focused verification used before a UI/API-only prebuilt image is promoted.
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"
if [ -n "${PYTHON:-}" ]; then
  PYTHON_BIN="$PYTHON"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN=python
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN=python3
else
  echo "python interpreter not found; set PYTHON to the project interpreter" >&2
  exit 127
fi

if [ "${1:-}" = "--help" ]; then
  cat <<'EOF'
usage: verify_ui_fast_path.sh

Runs the UI/API static contracts plus the focused browser scenarios (boot, product
shell, configuration). It is intentionally not the full regression suite: CI owns
the full suite. Run deploy_prebuilt_images.sh afterwards for the target health/SHA check.
EOF
  exit 0
fi

# 이 경로는 배포 직전 수십 초 이내여야 한다. 전체 UI 정적 회귀(76건+API 라우터
# 대조)는 CI에 맡기고, 여기서는 개인정보/권한 경계와 이번 고정 등급·Product Shell 계약만
# 직접 확인한다.
"$PYTHON_BIN" -m pytest -q \
  tests/test_admin_workspace_guardrails.py \
  tests/test_admin_console_screens.py::test_console_does_not_offer_a_force_or_code_set_change \
  tests/test_admin_console_screens.py::test_grade_put_payload_matches_schema \
  tests/test_admin_console_screens.py::test_console_v2_product_shell_keeps_the_operational_views_and_real_api_boundaries

node tests/e2e_console/run.mjs --only boot,product,config --json > /tmp/koipa-ui-e2e.json
python - /tmp/koipa-ui-e2e.json <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
bad = [r for r in payload["results"] if r["status"] in {"fail", "crash"}]
if bad:
    raise SystemExit("focused E2E failed: " + ", ".join(r["id"] for r in bad))
print(f"[ui-fast-path] focused E2E passed: {len(payload['results'])} scenarios")
PY
