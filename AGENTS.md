# Repository Guidelines

## Project Structure & Module Organization
MiHome-Windows is a Python 3.10+ desktop application using PySide6. `run.py` starts the GUI. `app/core/` contains models, persistence, scheduling, and the Mi Home adapter; `app/ui/` contains windows, widgets, tray controls, and PNG/ICO assets. `app/siui/` bundles SiliconUI components. Regression scripts live in `tests/`; diagnostic utilities live in `tools/`.

## Build, Test, and Development Commands
Run these commands from the repository root in PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe run.py
.venv\Scripts\python.exe -m tests.smoke_test
.venv\Scripts\python.exe -m tests.theme_test
.\build.ps1
```

The first three commands create the environment, install dependencies, and launch the app. `start.bat` automates this setup and launch. The smoke test checks upstream API compatibility; the theme test checks offscreen rendering. `build.ps1` builds `dist\MiHome-Windows.exe` with Nuitka and checks packaged files for private data. Building requires MSVC, a Windows SDK, and an ASCII-only project path; distribute the entire `dist/` directory.

## Coding Style & Architecture
Follow existing Python style: four-space indentation, `snake_case` functions/modules, `PascalCase` classes, and `UPPER_SNAKE_CASE` constants. Use type hints and retain GPL SPDX headers. No formatter or linter is configured in `pyproject.toml`.

Keep production `mijiaAPI` imports inside `app/core/service.py`. Route Mi Home network operations through the serialized `JobExecutor` in `app/core/jobs.py`; keep GUI updates on the Qt thread. Centralize persistence in the existing store modules and shared `_json_store.py` helpers.

## Testing Guidelines
Tests are standalone assertion-based modules, including Qt offscreen regressions, rather than a configured pytest suite. Name tests `tests/<feature>_test.py` and run them with `-m tests.<feature>_test`. Run regressions relevant to the change; run `smoke_test` after dependency upgrades. No numerical coverage threshold is configured.

Call `tests._isolate.isolate()` before importing application modules in tests that access persistence. Use `QT_QPA_PLATFORM=offscreen` for GUI tests and fake services instead of controlling real devices.

## Commit & Pull Request Guidelines
Recent commits use `feat:`, `fix:`, and `chore:` prefixes, usually with Chinese descriptions. Keep commits focused. PRs should explain behavior changes, link relevant issues, list verification commands/results, and include screenshots for UI changes.

## Security & Agent Workflow
Never commit authentication files, device caches, personal settings, or logs. Use the installed `llm-wiki` skill for useful local context and verify claims against current code. Curate durable findings with `llm-wiki-sync` at completion; use its Windows launchers and local draft buffer for multi-turn work. Exclude secrets and raw transcripts; report pending synchronization accurately. Do not use `project-memory`.
