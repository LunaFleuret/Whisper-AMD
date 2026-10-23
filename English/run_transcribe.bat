@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"

echo ==========================================
echo  Whisper Transcription (English / Vulkan)
echo  GPU: AMD Radeon RX 9060 XT
echo ==========================================
echo.
echo Select Whisper Model:
echo  [1] large-v3-turbo (FP16)        1.6GB [Standard Fast]
echo  [2] large-v3-turbo-q5_0 (Q5)     550MB [Recommended / Ultra Fast]
echo  [3] large-v3 (FP16)              3.1GB [Best Accuracy / Slower]
echo.

:select
set CHOICE=1
set /p CHOICE="Enter choice (1-3, Default=1): "

if "%CHOICE%"=="1" (
    set MODEL=large-v3-turbo
    goto run
)
if "%CHOICE%"=="2" (
    set MODEL=large-v3-turbo-q5_0
    goto run
)
if "%CHOICE%"=="3" (
    set MODEL=large-v3
    goto run
)

echo Invalid choice. Please enter 1 to 3.
goto select

:run
echo.
echo Selected Model: %MODEL%
echo Starting batch transcription (English Mode / Vulkan / RX 9060 XT)...
python transcribe_amd.py --model %MODEL%

echo.
echo All tasks finished!
pause
