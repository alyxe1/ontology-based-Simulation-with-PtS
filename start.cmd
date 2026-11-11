@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
call "%USERPROFILE%\micromamba\condabin\micromamba.bat" run -n ontologysim-v02 python -m scripts.v2_launch start %*
if errorlevel 1 pause
