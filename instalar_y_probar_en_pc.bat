@echo off
chcp 65001 >nul
echo ============================================================================
echo   AGENTE CUANTITATIVO NBA PROPS - CANAL TELEGRAM: MUNETIPS (PLAYDOIT.MX)
echo ============================================================================
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [INFO] Python no esta instalado en tu PC. Instalando Python 3.12 automaticamente...
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    echo.
    echo [OK] Cierra esta ventana y vuelve a darle doble clic a este archivo.
    pause
    exit /b
)

echo [1/2] Ejecutando simulacion de alerta para el canal Munetips...
python main.py --demo
echo.
echo [2/2] Escaneando cartelera NBA y lesiones del dia de hoy...
python main.py --scan
echo.
pause
