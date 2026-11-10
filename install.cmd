@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "MM=%USERPROFILE%\micromamba\condabin\micromamba.bat"
if not exist "%MM%" (
  echo micromamba was not found at the standard user installation path.
  echo Install micromamba, or update MM in this file to its existing location.
  exit /b 1
)
call "%MM%" run -n ontologysim python -c "import sys; assert sys.version_info[:2] == (3,12)" >nul 2>nul
if errorlevel 1 (
  call "%MM%" create -n ontologysim -c conda-forge python=3.12.15 pip -y
  if errorlevel 1 exit /b 1
)
call "%MM%" run -n ontologysim python -m pip install -r requirements.lock.txt
if errorlevel 1 exit /b 1
pushd frontend
call npm ci --no-audit --no-fund
if errorlevel 1 (popd & exit /b 1)
call npm run build
if errorlevel 1 (popd & exit /b 1)
popd
echo Installation complete. Double-click start.cmd to open the workbench.
