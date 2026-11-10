@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
call "%USERPROFILE%\micromamba\condabin\micromamba.bat" run -n ontologysim python -m scripts.launch start %*
if errorlevel 1 pause
