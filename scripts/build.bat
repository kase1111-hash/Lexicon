@echo off
REM Linguistic Stratigraphy - Windows Build Script
REM Usage: build.bat [command]
REM Commands: install, install-dev, test, lint, build, clean, help

setlocal enabledelayedexpansion

if "%1"=="" goto help
if "%1"=="help" goto help
if "%1"=="install" goto install
if "%1"=="install-dev" goto install-dev
if "%1"=="test" goto test
if "%1"=="lint" goto lint
if "%1"=="format" goto format
if "%1"=="type-check" goto type-check
if "%1"=="security-check" goto security-check
if "%1"=="build" goto build
if "%1"=="dist" goto dist
if "%1"=="clean" goto clean
if "%1"=="check" goto check
if "%1"=="run-api" goto run-api

echo Unknown command: %1
goto help

:help
echo.
echo Linguistic Stratigraphy - Build Script
echo =======================================
echo.
echo Usage: build.bat [command]
echo.
echo Commands:
echo   install        Install production dependencies
echo   install-dev    Install development dependencies
echo   test           Run all tests
echo   lint           Run linter (ruff)
echo   format         Format code (black)
echo   type-check     Run type checker (mypy)
echo   security-check Run security scanner (bandit)
echo   build          Build wheel package
echo   dist           Create distribution packages
echo   clean          Remove build artifacts
echo   check          Run all checks (lint, type, security)
echo   run-api        Start the API server
echo   help           Show this help message
echo.
goto end

:install
echo Installing production dependencies...
pip install -r requirements.txt
goto end

:install-dev
echo Installing development dependencies...
pip install -r requirements-dev.txt
if exist .pre-commit-config.yaml (
    pre-commit install
)
goto end

:test
echo Running tests...
pytest tests/ -v
goto end

:lint
echo Running linter...
ruff check src tests
goto end

:format
echo Formatting code...
black src tests
goto end

:type-check
echo Running type checker...
mypy src --ignore-missing-imports
goto end

:security-check
echo Running security scanner...
bandit -r src -c pyproject.toml
goto end

:build
echo Building wheel package...
pip install build
python -m build --wheel
goto end

:dist
echo Creating distribution packages...
pip install build
python -m build
goto end

:clean
echo Cleaning build artifacts...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist *.egg-info rmdir /s /q *.egg-info
if exist .pytest_cache rmdir /s /q .pytest_cache
if exist .mypy_cache rmdir /s /q .mypy_cache
if exist .ruff_cache rmdir /s /q .ruff_cache
if exist htmlcov rmdir /s /q htmlcov
for /d /r . %%d in (__pycache__) do @if exist "%%d" rmdir /s /q "%%d"
del /s /q *.pyc 2>nul
echo Done.
goto end

:check
echo Running all checks...
call :lint
call :type-check
call :security-check
echo.
echo All checks complete!
goto end

:run-api
echo Starting API server...
uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000
goto end

:end
endlocal
