@echo off
echo =======================================================
echo Signalpost V1 - Final Qualification Audit
echo =======================================================
echo.

set PYTHON=.\.venv\Scripts\python.exe

if not exist %PYTHON% (
    echo [ERROR] Virtual environment not found at .venv
    exit /b 1
)

if not exist "out\nav-feed-snapshot.json.gz" (
    echo [ERROR] NAV feed snapshot is missing. Waiting for fetch to complete...
    exit /b 1
)

echo [1/3] Running final submission runner...
%PYTHON% scripts\run_signalpost_submission.py ^
    --organisations out\smoke-profiles.jsonl ^
    --bulk false ^
    --expected-count 100 ^
    --output out\submission-envelopes-final.jsonl ^
    --report out\submission-report-final.json ^
    --viewer out\submission-viewer-final.html ^
    --run-id submission-final ^
    --nav-snapshot out\nav-feed-snapshot.json.gz

if %errorlevel% neq 0 (
    echo [ERROR] Submission runner failed.
    exit /b %errorlevel%
)

echo.
echo [2/3] Simulating Builderr qualification gate...
%PYTHON% scripts\score_competition_v3.py ^
    --profiles out\submission-run\base-profiles.jsonl ^
    --external-report out\submission-run\nav-report.json ^
    --batch-report out\submission-report-final.json ^
    --refresh-report out\submission-report-final.json ^
    --research-report out\submission-report-final.json ^
    --ux-report out\submission-report-final.json ^
    --output out\simulated-score.json

echo.
echo [3/3] Creating submission archive...
powershell -Command "Compress-Archive -Path 'out\submission-envelopes-final.jsonl', 'out\submission-viewer-final.html', 'out\submission-report-final.json' -DestinationPath 'out\Signalpost_Submission.zip' -Force"

echo.
echo =======================================================
echo SUCCESS! 
echo Your submission archive is ready at: out\Signalpost_Submission.zip
echo =======================================================
