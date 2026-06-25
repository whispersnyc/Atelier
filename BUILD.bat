@echo off
timeout 3
set /p VERSION=Version (e.g. 0.0.3):
if "%VERSION%"=="" (echo Version cannot be empty & exit /b 1)
rmdir /s /q build dist 2>nul
python -m PyInstaller --noconfirm --clean Atelier.spec
if %ERRORLEVEL% NEQ 0 exit /b %ERRORLEVEL%
xcopy /E /I /Y Tools dist\Atelier\Tools
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" Atelier.iss /DAppVersion=%VERSION%
if %ERRORLEVEL% NEQ 0 exit /b %ERRORLEVEL%
