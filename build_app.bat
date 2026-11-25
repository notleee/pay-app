@echo off
where py >nul 2>nul && (set PY=py) || (set PY=python)
echo Using: %PY%
%PY% --version
if errorlevel 1 goto fail
echo.
echo Installing libraries...
%PY% -m pip install --upgrade telethon tzdata pillow pytesseract pyinstaller
if errorlevel 1 goto fail
echo.
echo Building pay_app.exe...
%PY% -m PyInstaller --onefile --windowed --name pay_app --icon=pay_app.ico --collect-data tzdata pay_app.py
if errorlevel 1 goto fail
if not exist dist\pay_app.exe goto fail
echo.
echo DONE. Program: dist\pay_app.exe
pause
exit /b 0
:fail
echo.
echo FAILED. Send the text above to Claude.
pause
exit /b 1