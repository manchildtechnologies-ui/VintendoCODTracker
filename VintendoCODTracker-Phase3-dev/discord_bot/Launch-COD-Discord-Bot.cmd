@echo off
setlocal
cd /d "%~dp0"
if not exist .env (
  echo Missing .env. Copy .env.example to .env and set the Discord application values.
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv || exit /b 1
)
.venv\Scripts\python.exe -c "import discord, PIL, dotenv" >nul 2>nul
if errorlevel 1 (
  .venv\Scripts\python.exe -c "import pip" >nul 2>nul
  if errorlevel 1 (
    rem Some managed Python installs omit ensurepip; system pip still targets this isolated venv.
    py -3 -m pip --python .venv\Scripts\python.exe install --disable-pip-version-check --timeout 15 --retries 1 -r requirements.txt || exit /b 1
  ) else (
    .venv\Scripts\python.exe -m pip install --disable-pip-version-check --timeout 15 --retries 1 -r requirements.txt || exit /b 1
  )
)
.venv\Scripts\python.exe -m codmunity_bot
if errorlevel 1 pause
