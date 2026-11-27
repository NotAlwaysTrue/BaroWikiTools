@echo off
setlocal
pushd "%~dp0" || exit /b 1
python -B export_sprites.py --gui
set "export_status=%errorlevel%"
popd
if not "%export_status%"=="0" pause
exit /b %export_status%
