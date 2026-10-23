@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"

echo ==========================================
echo  Whisper Transcription (Radeon / Vulkan)
echo  GPU: AMD Radeon (Vulkan Acceleration)
echo  Model: large-v3-turbo (Smart 2-Chunks)
echo ==========================================
echo.
echo Starting batch transcription...
python transcribe_amd.py

echo.
echo All tasks finished!
pause
