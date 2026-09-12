<#
.SYNOPSIS
  Windows entry point for verify-all -- offline full verification without `make`
  (mirrors the Makefile `verify-all` target). ASCII-only on purpose: Windows
  PowerShell 5.1 reads .ps1 as ANSI without a BOM, so non-ASCII would corrupt.

.DESCRIPTION
  1) pytest lite  2) data quality  3) PoC 5 dryrun  4) airgap bundle dry-run
  5) license check  6) synthetic analysis. Prints each step; exit 1 on any failure.

.EXAMPLE
  cd poc; pwsh scripts/verify_all.ps1
  cd poc; powershell -ExecutionPolicy Bypass -File scripts/verify_all.ps1
#>

# Native exe non-zero exit does not throw in PS, so check $LASTEXITCODE directly.
$ErrorActionPreference = "Continue"
$PY = if ($env:PY) { $env:PY } else { "python" }

# Always run from poc/ root (this script lives in poc/scripts/).
Set-Location (Join-Path $PSScriptRoot "..")

$steps = @(
  @{ n = "[1/6] pytest lite (excl slow/fullstack/model_download)";
     c = { & $PY -m pytest -q -m "not fullstack and not model_download and not slow" } },
  @{ n = "[2/6] data quality check";
     c = { & $PY scripts/check_data_quality.py --train datasets/labeled_v2_balanced/train.jsonl --test datasets/gold/classification_gold.jsonl } },
  @{ n = "[3/6] PoC 5 dryrun";
     c = { & $PY scripts/run_all_pocs.py } },
  @{ n = "[4/6] airgap bundle manifest dry-run";
     c = { & $PY scripts/build_offline_bundle.py --version 1.0.0-rc1 --dry-run --output dist/koipa-airgap-bundle } },
  @{ n = "[5/6] OSS license risk (allowlist)";
     c = { & $PY scripts/dump_licenses.py --check --scope=direct } },
  @{ n = "[6/6] synthetic analysis";
     c = { & $PY scripts/analyze_synthetic.py } }
)

$failed = @()
foreach ($s in $steps) {
  Write-Host ""
  Write-Host "== $($s.n) ==" -ForegroundColor Cyan
  & $s.c
  if ($LASTEXITCODE -ne 0) {
    Write-Host "  FAILED (exit $LASTEXITCODE)" -ForegroundColor Red
    $failed += $s.n
  }
}

Write-Host ""
if ($failed.Count -eq 0) {
  Write-Host "[verify-all] ALL GREEN" -ForegroundColor Green
  exit 0
} else {
  Write-Host "[verify-all] FAILED: $($failed -join '; ')" -ForegroundColor Red
  exit 1
}
