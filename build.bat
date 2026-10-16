@echo off
echo ===============================================
echo   Sozlar Ilovasi - .exe fayl yasash
echo ===============================================
echo.

pip install pyinstaller

echo.
echo .exe yasalyapti, biroz kuting (1-2 daqiqa)...
echo.

pyinstaller --onefile --windowed --name SozlarIlovasi --add-data "app;app" main.py

echo.
echo ===============================================
echo   Tayyor! Fayl shu yerda: dist\SozlarIlovasi.exe
echo   Shu faylni istalgan joyga ko'chirib, do'stingizga
echo   jo'natishingiz mumkin - unga Python kerak emas.
echo ===============================================
pause
