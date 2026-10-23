@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"

echo ==========================================
echo  Whisper Transcription (English / Vulkan)
echo  GPU: AMD Radeon (Vulkan Acceleration)
echo  Model: large-v3-turbo (Smart 4-Chunks)
echo ==========================================
echo.
echo Starting batch transcription (English Mode)...
python transcribe_amd.py

echo.
echo All tasks finished!
pause
