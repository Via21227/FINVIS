@echo off
setlocal
cd /d "%~dp0"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONUTF8=1"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -B -m finvis.competition_launcher %*
  goto finished
)
where python >nul 2>nul
if errorlevel 1 (
  echo FINVIS needs Python 3.9 or newer with a supported SQLite build.
  echo Install Python from python.org and include the Python launcher.
  pause
  exit /b 1
)
python -B -m finvis.competition_launcher %*
:finished
if errorlevel 1 (
  echo FINVIS stopped safely. Saved competition data was not reset.
  pause
  exit /b 1
)
endlocal
