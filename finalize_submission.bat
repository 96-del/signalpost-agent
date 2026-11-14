@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo =======================================================
echo Signalpost - reproducible local submission smoke test
echo =======================================================
echo.

echo [1/3] Installing the locked default environment...
uv sync --locked
if errorlevel 1 goto :failed

echo.
echo [2/3] Running the test suite...
uv run --locked --with pytest pytest -q
if errorlevel 1 goto :failed

echo.
echo [3/3] Running the documented submission entrypoint...
uv run --locked python scripts\run_signalpost_submission.py ^
    --organisations smoke-companies.jsonl ^
    --bulk brreg-enheter.csv ^
    --output out\submission-envelopes.jsonl ^
    --report out\submission-report.json ^
    --viewer out\submission-viewer.html ^
    --run-id submission-smoke ^
    --out-dir out\submission-smoke-run
if errorlevel 1 goto :failed

echo.
echo SUCCESS: validation details are in out\submission-report.json
exit /b 0

:failed
echo.
echo ERROR: the smoke test failed. See the command output above.
exit /b 1
