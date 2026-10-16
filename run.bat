@echo off
cd /d "%~dp0"
echo Kutubxonalar tekshirilmoqda (birinchi marta biroz vaqt oladi)...
python -m pip install -r requirements.txt -q
if errorlevel 1 (
  echo.
  echo XATO: pip ishlamadi. Python o'rnatilganini tekshiring.
  pause
  exit /b 1
)
echo Ilova ishga tushmoqda...
python main.py
if errorlevel 1 (
  echo.
  echo XATO yuz berdi. Yuqoridagi matnni menga yuboring.
  pause
)
