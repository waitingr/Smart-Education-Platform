@echo off
rem ------------------------------------------------------------
rem  Project-local Ollama entry point.
rem  Usage: models\ollama\ollama.cmd serve | list | ps | pull MODEL
rem  Why not run runtime\ollama.exe directly? It would look for
rem  models in %USERPROFILE%\.ollama\models instead of store\.
rem ------------------------------------------------------------
setlocal
set "OLLAMA_HOST=127.0.0.1:11434"
set "OLLAMA_MODELS=%~dp0store"
set "OLLAMA_KEEP_ALIVE=30m"
set "OLLAMA_MAX_LOADED_MODELS=1"
set "OLLAMA_NUM_PARALLEL=1"
set "OLLAMA_FLASH_ATTENTION=1"
"%~dp0runtime\ollama.exe" %*
endlocal