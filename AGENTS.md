# AGENTS.md — ViewIndexTTS

## Architecture

Single-page Flet desktop app that talks to AstraFlow's hosted IndexTTS-2 API via `httpx`.
No local TTS engine, no submodules, no backend server.

```
main.py          # entry point — ft.run(main=TtsApp)
gui/
  app.py         # TtsApp class — all UI + synthesis orchestration (~880 LOC)
  api_client.py  # AstraFlowClient — httpx wrapper with rate limiting + retry
  voice_presets.py # 9 built-in voice definitions
```

## Run

```powershell
python main.py
```

Virtual env at `.venv/` (Python 3.12), managed with `uv`. Install deps:

```powershell
uv sync
# or: pip install "flet[desktop]" httpx playsound3 python-dotenv
```

## Build (Nuitka)

```powershell
python build.py           # standalone folder → dist/
python build.py --onefile # single .exe
```

`build.py` auto-downloads the Flet Flutter engine bundle to `flet_client/` before invoking Nuitka.
CI (`.github/workflows/build.yml`) builds all 3 platforms on tag push + manual dispatch.

## Config

- API key: `MODELVERSE_API_KEY` in `%APPDATA%/ViewIndexTTS/.env` (Windows) or `~/.config/ViewIndexTTS/.env` (Linux) or `~/Library/Application Support/ViewIndexTTS/.env` (macOS)
- Also loads from repo-root `.env` as fallback (via `load_dotenv`)
- API base URL: `https://api.modelverse.cn/v1` (hardcoded in `AstraFlowClient`)
- Auth: `Bearer` token in `Authorization` header

## Key gotchas

- **No tests.** Zero test files exist. Any changes must be verified manually.
- **GUI is single-file.** `gui/app.py` is ~880 lines — `_build_ui()` + event handlers + synthesis thread. No component decomposition.
- **Thread safety.** Synthesis runs in `daemon=True` thread. UI updates from background threads must use `_safe_page_update()` which wraps `asyncio.run_coroutine_threadsafe`. Never touch Flet controls from bg threads directly.
- **Rate limiting.** `AstraFlowClient` enforces ≥6s between requests (10 RPM). 5xx errors retry 3× with exponential backoff (0.5s, 1s, 2s).
- **Audio playback** uses `playsound3` library (cross-platform). Stop is state-based (sets `_is_playing = False`), not a real audio interrupt.
- **Output dir** defaults to `%APPDATA%/ViewIndexTTS/output/` (Windows), `~/Library/Application Support/ViewIndexTTS/output/` (macOS), or `~/.config/ViewIndexTTS/output/` (Linux). WAV files only.
- **Emotion vector** must be 8-dim, sum ≤ 1.5, validated in `SynthesizeRequest.__post_init__`.
- **No formatter/linter config** in the project. No `ruff`, no `pyproject.toml` tool settings beyond `[tool.uv]`.
- `.opencode/skills/` —— `project-setup.md` 和 `tts-service.md` 仍为旧架构内容，已过时。`flet-gui.md` 已更新为 Flet 0.85+ API 并记录了常见兼容性问题。

## Useful refs

- AstraFlow API docs: https://astraflow.ucloud.cn/
- Flet docs (0.25+): uses `ft` namespace, Material 3, `ResponsiveRow`, `ThemeMode.LIGHT/DARK`
- Nuitka plugin: `--enable-plugin=tk-inter` required for `filedialog` imports
