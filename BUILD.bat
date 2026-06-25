rmdir /s /q build dist 2>nul
python -m PyInstaller --noconfirm --clean Atelier.spec
if %ERRORLEVEL% NEQ 0 exit /b %ERRORLEVEL%
xcopy /E /I /Y Tools dist\Atelier\Tools
powershell -Command "Compress-Archive -Path 'dist\Atelier' -DestinationPath 'dist\Atelier.zip' -Force"
