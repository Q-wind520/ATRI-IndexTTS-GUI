import asyncio
import logging
import os
import subprocess
import sys
import time
from playsound3 import playsound
from pathlib import Path
from threading import Thread
from tkinter import filedialog

import flet as ft
from dotenv import load_dotenv

from gui.api_client import (  # noqa: F401
    AstraFlowClient,
    AstraFlowError,
    CustomVoice,
    SynthesizeRequest,
)
from gui.voice_presets import BUILTIN_VOICES, VOICE_LABEL_MAP  # noqa: F401

logger = logging.getLogger(__name__)

EMOTION_DIMS: list[tuple[str, str]] = [
    ("Happy", "快乐"), ("Angry", "愤怒"), ("Sad", "悲伤"),
    ("Scared", "恐惧"), ("Disgusted", "厌恶"), ("Depressed", "抑郁"),
    ("Surprised", "惊讶"), ("Calm", "平静"),
]


class TtsApp:
    def __init__(self, page: ft.Page):
        self.page = page

        # Load .env and API key
        _env = Path(__file__).resolve().parent.parent / ".env"
        if _env.exists():
            load_dotenv(_env)
        self._api_key = os.environ.get("MODELVERSE_API_KEY", "")

        # Initialize client (will be None if no API key)
        self.client: AstraFlowClient | None = None
        if self._api_key:
            try:
                self.client = AstraFlowClient(self._api_key)
            except Exception:
                self._api_key = ""
                self.client = None

        self._current_audio: str | None = None
        self._is_playing = False

        self._build_ui()
        self._load_data()

    def _safe_page_update(self):
        async def _update():
            self.page.update()
        asyncio.run_coroutine_threadsafe(_update(), self.page.loop)

    def _run_on_ui_thread(self, fn):
        async def _wrapper():
            fn()
        asyncio.run_coroutine_threadsafe(_wrapper(), self.page.loop)

    # ── UI ────────────────────────────────────────────────────

    def _build_ui(self):
        p = self.page
        p.title = "ATRI-IndexTTS 语音合成"
        p.padding = 0
        p.spacing = 0
        p.window_width = 800
        p.window_min_width = 370

        p.theme = ft.Theme(
            color_scheme=ft.ColorScheme(primary=ft.Colors.INDIGO),
            use_material3=True,
        )
        p.dark_theme = ft.Theme(
            color_scheme=ft.ColorScheme(primary=ft.Colors.INDIGO_200, outline=ft.Colors.GREY_400),
            use_material3=True,
        )
        p.theme_mode = ft.ThemeMode.LIGHT

        # ---- .env warning ----
        self._txt_env_warn = ft.Text("", color=ft.Colors.ORANGE_600, size=13, visible=False)

        # ---- Required: 合成文本 ----
        self._txt_text = ft.TextField(
            label="合成文本", hint_text="请输入要合成语音的文本…",
            multiline=True, min_lines=3, max_lines=8,
            border=ft.InputBorder.OUTLINE, expand=True,
        )

        # ---- Required: 音色 ----
        self._dd_voice = ft.Dropdown(
            label="音色", border=ft.InputBorder.OUTLINE, expand=True,
            on_select=self._on_voice_change,
        )
        self._btn_manage_voices = ft.TextButton(
            content=ft.Row([
                ft.Icon(ft.Icons.MANAGE_ACCOUNTS, size=18),
                ft.Text("管理音色"),
            ], spacing=4),
            on_click=self._open_voice_manager,
        )

        # ---- Emotion ----
        self._ck_emo = ft.Checkbox(
            label="启用情感控制", value=False, on_change=self._on_emo_toggle,
        )

        self._dd_emo_method = ft.Dropdown(
            label="控制方式",
            options=[
                ft.dropdown.Option("0", "无"),
                ft.dropdown.Option("1", "情感音频文件"),
                ft.dropdown.Option("2", "情感向量"),
                ft.dropdown.Option("3", "情感文本"),
            ],
            value="0",
            border=ft.InputBorder.OUTLINE,
            expand=True,
            visible=False,
            on_select=self._on_emo_method_change,
        )

        # ---- Emotion: 情感文本 (method=3) ----
        self._txt_emo_text = ft.TextField(
            label="情感文本",
            border=ft.InputBorder.OUTLINE, expand=True, visible=False,
        )

        # ---- Emotion: 情感向量 (method=2) ----
        self._emo_sliders: list[ft.Slider] = []
        self._emo_val_texts: list[ft.Text] = []
        self._txt_emo_vec_sum = ft.Text("合计: 0.00", size=12)
        emo_vec_rows: list[ft.Row] = []
        for _i, (_en, cn) in enumerate(EMOTION_DIMS):
            s = ft.Slider(
                min=0, max=1.2, value=0, divisions=24,
                expand=True, on_change=self._on_emo_vec_change,
            )
            vt = ft.Text("0.00", size=12, width=42, text_align=ft.TextAlign.END)
            self._emo_sliders.append(s)
            self._emo_val_texts.append(vt)
            emo_vec_rows.append(ft.Row(
                [ft.Text(cn, size=12, width=48), s, vt],
                spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ))
        emo_vec_rows.append(ft.Row(
            [ft.Container(expand=True), self._txt_emo_vec_sum],
            spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ))
        self._container_emo_vec = ft.Container(
            content=ft.Column(emo_vec_rows, spacing=4), visible=False,
        )

        # ---- Emotion: 情感音频文件 (method=1) ----
        self._txt_emo_audio_file = ft.TextField(
            label="情感音频文件路径", border=ft.InputBorder.OUTLINE,
            expand=True, visible=False, read_only=True,
        )
        self._btn_emo_audio_file = ft.FilledTonalButton(
            content="选择音频文件", icon=ft.Icons.AUDIO_FILE,
            visible=False, on_click=self._on_pick_emo_audio,
        )

        # ---- Emotion: shared controls ----
        self._txt_emo_weight_val = ft.Text("0.60", size=14, width=44, text_align=ft.TextAlign.END)
        self._sl_emo_weight = ft.Slider(
            min=0, max=1, value=0.6, width=200,
            on_change=lambda e: (setattr(self._txt_emo_weight_val, 'value', f"{e.control.value:.2f}"), self.page.update()),
        )
        self._row_emo_intensity = ft.Row(
            [ft.Text("情感强度", size=14), self._sl_emo_weight, self._txt_emo_weight_val],
            spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, visible=False,
        )
        self._ck_emo_random = ft.Checkbox(label="随机化情感", value=False, visible=False)

        # ---- Audio parameters ----
        self._txt_speed_val = ft.Text("1.00", size=14, width=44, text_align=ft.TextAlign.END)
        self._sl_speed = ft.Slider(
            min=0.25, max=4.0, value=1.0, divisions=75, width=140,
            on_change=lambda e: (setattr(self._txt_speed_val, 'value', f"{e.control.value:.2f}"), self.page.update()),
        )
        self._txt_gain_val = ft.Text("1.00", size=14, width=44, text_align=ft.TextAlign.END)
        self._sl_gain = ft.Slider(
            min=0.1, max=10.0, value=1.0, divisions=99, width=140,
            on_change=lambda e: (setattr(self._txt_gain_val, 'value', f"{e.control.value:.1f}"), self.page.update()),
        )
        self._dd_sample_rate = ft.Dropdown(
            label="采样率",
            options=[
                ft.dropdown.Option("16000", "16000 Hz"),
                ft.dropdown.Option("22050", "22050 Hz"),
                ft.dropdown.Option("24000", "24000 Hz"),
                ft.dropdown.Option("44100", "44100 Hz"),
            ],
            value="24000",
            border=ft.InputBorder.OUTLINE,
            width=150,
        )

        # ---- Advanced parameters (collapsible) ----
        self._txt_interval_silence = ft.TextField(
            label="句间静音 (ms)", value="200",
            border=ft.InputBorder.OUTLINE, width=180,
            keyboard_type=ft.KeyboardType.NUMBER,
        )
        self._txt_max_tokens = ft.TextField(
            label="分句长度 (tokens)", value="120",
            border=ft.InputBorder.OUTLINE, width=180,
            keyboard_type=ft.KeyboardType.NUMBER,
        )
        self._container_advanced = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Text("句间静音 (ms)", size=13, width=120),
                    self._txt_interval_silence,
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Row([
                    ft.Text("分句长度 (tokens)", size=13, width=120),
                    self._txt_max_tokens,
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ], spacing=8),
            visible=False,
        )
        self._btn_advanced_toggle = ft.TextButton(
            "高级参数 ▸", on_click=self._on_advanced_toggle,
        )

        # ---- Output ----
        self._txt_output = ft.TextField(
            label="输出目录", value="temp/output",
            border=ft.InputBorder.OUTLINE, expand=True,
        )
        self._btn_open_output = ft.IconButton(
            icon=ft.Icons.FOLDER_OPEN, tooltip="打开输出目录",
            on_click=self._on_open_output,
        )

        # ---- Synthesize ----
        self._synth_status = ft.Text("", size=13, color=ft.Colors.GREY_600)
        self._btn_synth = ft.FilledButton(
            content="合成", icon=ft.Icons.VOICE_CHAT, on_click=self._on_synthesize,
        )

        # ---- Status ----
        self._txt_status = ft.Text("", selectable=True, size=13)

        # ---- Playback ----
        self._txt_file_info = ft.Text("", size=12, color=ft.Colors.GREY_600)
        self._btn_play = ft.FilledTonalButton(
            content="播放", icon=ft.Icons.PLAY_ARROW,
            on_click=self._on_play, visible=False,
        )

        # ---- File list ----
        self._file_list_container = ft.Container(
            content=ft.Column([ft.Text("（暂无文件）", size=13, color=ft.Colors.GREY_500)], spacing=4),
            padding=ft.Padding(left=0, top=4, right=0, bottom=4),
        )

        # ── Assemble ──────────────────────────────────────────

        p.add(
            ft.Column([
                ft.Container(
                    content=ft.Column([
                        ft.Row([
                            ft.Text("ATRI-IndexTTS", size=22, weight=ft.FontWeight.BOLD),
                            ft.Container(expand=True),
                            ft.IconButton(
                                icon=ft.Icons.DARK_MODE, tooltip="切换深色模式",
                                on_click=self._toggle_theme,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.SETTINGS, tooltip="编辑配置",
                                on_click=self._open_config_dialog,
                            ),
                        ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        self._txt_env_warn,
                        ft.Divider(),
                        ft.Card(
                            content=ft.Container(
                                content=ft.Column([
                                    ft.Text("必填参数", size=15, weight=ft.FontWeight.W_600),
                                    self._txt_text,
                                    ft.ResponsiveRow([
                                        ft.Column([self._dd_voice], col={"sm": 12, "md": 12}),
                                    ], spacing=12),
                                    self._btn_manage_voices,
                                ], spacing=12),
                                padding=16,
                            ),
                        ),
                        ft.Divider(height=8),
                        ft.Text("情感控制", size=15, weight=ft.FontWeight.W_600),
                        self._ck_emo,
                        self._dd_emo_method,
                        self._txt_emo_text,
                        self._container_emo_vec,
                        ft.Row([
                            self._btn_emo_audio_file,
                            self._txt_emo_audio_file,
                        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        self._row_emo_intensity,
                        self._ck_emo_random,
                        ft.Divider(height=8),
                        ft.Text("音频参数", size=15, weight=ft.FontWeight.W_600),
                        ft.ResponsiveRow([
                            ft.Column([
                                ft.Text("语速", size=13),
                                ft.Row([self._sl_speed, self._txt_speed_val], spacing=4),
                            ], col={"sm": 12, "md": 4}),
                            ft.Column([
                                ft.Text("音量", size=13),
                                ft.Row([self._sl_gain, self._txt_gain_val], spacing=4),
                            ], col={"sm": 12, "md": 4}),
                            ft.Column([
                                ft.Text("采样率", size=13),
                                self._dd_sample_rate,
                            ], col={"sm": 12, "md": 4}),
                        ], spacing=16),
                        self._btn_advanced_toggle,
                        self._container_advanced,
                        ft.Divider(height=8),
                        ft.ResponsiveRow([
                            ft.Column([ft.Row([self._txt_output, self._btn_open_output], spacing=4)], col={"sm": 12, "md": 8}),
                            ft.Column(
                                [ft.Row([self._btn_synth, self._synth_status], spacing=8)],
                                col={"sm": 12, "md": 4},
                            ),
                        ], spacing=12),
                        self._txt_status,
                        ft.Divider(height=8),
                        ft.Row(
                            [self._btn_play, self._txt_file_info],
                            spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        ft.Divider(height=8),
                        ft.Row([
                            ft.Text("已生成文件", size=15, weight=ft.FontWeight.W_600),
                            ft.Container(expand=True),
                            ft.TextButton(
                                content="刷新", icon=ft.Icons.REFRESH,
                                on_click=lambda _: (self._refresh_files(), self.page.update()),
                            ),
                        ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        self._file_list_container,
                        ft.Divider(),
                    ], spacing=12),
                    padding=24,
                ),
            ], scroll=ft.ScrollMode.AUTO, expand=True)
        )

    # ── Theme toggle ──────────────────────────────────────────

    def _toggle_theme(self, e):
        self.page.theme_mode = (
            ft.ThemeMode.DARK if self.page.theme_mode == ft.ThemeMode.LIGHT
            else ft.ThemeMode.LIGHT
        )
        self.page.update()

    # ── Data loading ──────────────────────────────────────────

    def _load_data(self):
        if not self.client:
            self._txt_env_warn.value = "⚠ 未配置 MODELVERSE_API_KEY，请在 .env 中设置 MODELVERSE_API_KEY"
            self._txt_env_warn.visible = True
            self._txt_status.value = "未连接 API"
        else:
            # Load builtin voices
            self._dd_voice.options = [
                ft.dropdown.Option(v.id, f"{v.label} ({v.id})")
                for v in BUILTIN_VOICES
            ]
            self._dd_voice.value = "jack_cheng"

            # Load custom voices
            try:
                custom = self.client.list_custom_voices()
                for cv in custom:
                    self._dd_voice.options.append(
                        ft.dropdown.Option(cv.id, f"📤 {cv.name} ({cv.id})")
                    )
            except Exception as ex:
                logger.warning("Failed to load custom voices: %s", ex)

            self._txt_env_warn.visible = False

        self._refresh_files()
        self.page.update()

    def _refresh_files(self):
        out_dir = Path(self._txt_output.value.strip() or "temp/output")
        rows: list[ft.Row | ft.Text] = []
        if out_dir.exists():
            files = sorted(out_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
            for f in files:
                if f.suffix.lower() not in (".wav", ".mp3", ".flac", ".ogg"):
                    continue
                size_kb = f.stat().st_size / 1024
                rows.append(
                    ft.Row([
                        ft.IconButton(
                            icon=ft.Icons.PLAY_ARROW, tooltip="播放", icon_size=18,
                            on_click=lambda _, path=str(f): self._play_file(path),
                        ),
                        ft.Text(f.name, size=13, expand=True),
                        ft.Text(f"{size_kb:.1f} KB", size=12, color=ft.Colors.GREY_600, width=70, text_align=ft.TextAlign.END),
                    ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                )
        if not rows:
            rows.append(ft.Text("（暂无文件）", size=13, color=ft.Colors.GREY_500))
        self._file_list_container.content = ft.Column(rows, spacing=4)

    def _play_file(self, path: str):
        self._current_audio = path
        self._on_play(None)

    def _on_voice_change(self, e):
        pass  # Voice change no longer needs to load prompts

    # ── Emotion toggle ────────────────────────────────────────

    def _on_emo_toggle(self, e):
        enabled = self._ck_emo.value
        self._dd_emo_method.visible = enabled
        self._row_emo_intensity.visible = enabled
        self._ck_emo_random.visible = enabled
        if enabled:
            self._on_emo_method_change(None)
        else:
            self._txt_emo_text.visible = False
            self._container_emo_vec.visible = False
            self._btn_emo_audio_file.visible = False
            self._txt_emo_audio_file.visible = False
        self.page.update()

    def _on_emo_method_change(self, e):
        method = int(self._dd_emo_method.value or "0")
        self._txt_emo_text.visible = method == 3
        self._container_emo_vec.visible = method == 2
        self._btn_emo_audio_file.visible = method == 1
        self._txt_emo_audio_file.visible = method == 1
        self.page.update()

    def _on_emo_vec_change(self, e):
        total = sum(s.value for s in self._emo_sliders)
        for i, s in enumerate(self._emo_sliders):
            self._emo_val_texts[i].value = f"{s.value:.2f}"
        self._txt_emo_vec_sum.value = f"合计: {total:.2f}"
        self._txt_emo_vec_sum.color = ft.Colors.RED if total > 1.5 else None
        self.page.update()

    def _on_pick_emo_audio(self, e):
        path = filedialog.askopenfilename(
            title="选择情感音频文件",
            filetypes=[
                ("音频文件", "*.wav *.mp3 *.flac *.ogg"),
                ("所有文件", "*.*"),
            ],
        )
        if path:
            self._txt_emo_audio_file.value = path
            self.page.update()

    # ── Advanced toggle ───────────────────────────────────────

    def _on_advanced_toggle(self, e):
        expanded = not self._container_advanced.visible
        self._container_advanced.visible = expanded
        self._btn_advanced_toggle.content = "高级参数 ▾" if expanded else "高级参数 ▸"
        self.page.update()

    # ── Synthesis ─────────────────────────────────────────────

    def _on_synthesize(self, e):
        text = self._txt_text.value
        if not text or not text.strip():
            self._snack("请输入合成文本")
            return
        if not self.client:
            self._snack("请先配置 API Key")
            return
        if not self._dd_voice.value:
            self._snack("请选择音色")
            return

        self._btn_synth.disabled = True
        self._btn_synth.content = "合成中…"
        self._txt_status.value = "正在合成…"
        self._btn_play.visible = False
        self._txt_file_info.value = ""
        if self._is_playing:
            self._is_playing = False
        self.page.update()

        # Build emotion params
        emo_vec: list[float] | None = None
        emo_text: str | None = None
        emo_control_method = 0

        if self._ck_emo.value:
            emo_control_method = int(self._dd_emo_method.value or "0")
            if emo_control_method == 3:  # text
                emo_text = self._txt_emo_text.value.strip() or None
            elif emo_control_method == 2:  # vector
                emo_vec = [
                    self._emo_sliders[i].value for i in range(8)
                ]
            # method=1 (audio) and method=0 (none) use defaults

        try:
            req = SynthesizeRequest(
                input=text.strip(),
                voice=self._dd_voice.value,
                sample_rate=int(self._dd_sample_rate.value or "24000"),
                speed=self._sl_speed.value,
                gain=self._sl_gain.value,
                emo_control_method=emo_control_method,
                emo_weight=self._sl_emo_weight.value if self._ck_emo.value else 0.6,
                emo_text=emo_text,
                emo_vec=emo_vec,
                emo_random=self._ck_emo_random.value if self._ck_emo.value else False,
                interval_silence=int(self._txt_interval_silence.value or "200"),
                max_text_tokens_per_sentence=int(self._txt_max_tokens.value or "120"),
            )
        except ValueError as ex:
            self._snack(f"参数错误: {ex}")
            self._btn_synth.disabled = False
            self._btn_synth.content = "合成"
            self.page.update()
            return

        _client = self.client  # type narrowed by the early-return above

        def _run():
            # 2-second cooldown, then re-enable button
            time.sleep(2)
            self._btn_synth.disabled = False
            self._btn_synth.content = "合成"
            self._safe_page_update()

            try:
                out_dir = Path(self._txt_output.value.strip() or "temp/output")
                out_dir.mkdir(parents=True, exist_ok=True)

                audio_bytes = _client.synthesize(req)

                ts = time.strftime("%Y%m%d_%H%M%S")
                voice_name = req.voice.replace(":", "_").replace("-", "_")[:20]
                out_path = out_dir / f"synthesis_{voice_name}_{ts}.wav"
                out_path.write_bytes(audio_bytes)

                self._current_audio = str(out_path)
                size_kb = len(audio_bytes) / 1024
                self._txt_status.value = f"✓ 已生成: {out_path.name}"
                self._txt_file_info.value = f"{size_kb:.1f} KB"
                self._btn_play.visible = True
                self._btn_play.content = "播放"
                self._btn_play.icon = ft.Icons.PLAY_ARROW
            except AstraFlowError as ex:
                self._txt_status.value = f"✗ API错误: {ex}"
                self._snack(f"合成失败: {ex}")
            except Exception as ex:
                self._txt_status.value = f"✗ {ex}"
                self._snack(f"合成失败: {ex}")
            finally:
                self._run_on_ui_thread(self._refresh_files)
                self._safe_page_update()

        Thread(target=_run, daemon=True).start()

    # ── Playback ──────────────────────────────────────────────

    def _on_play(self, e):
        if self._is_playing:
            self._is_playing = False
            self._btn_play.content = "播放"
            self._btn_play.icon = ft.Icons.PLAY_ARROW
            self.page.update()
            return
        # Play
        if not self._current_audio or not Path(self._current_audio).exists():
            self._snack("没有可播放的音频文件")
            return
        try:
            Thread(target=lambda: playsound(self._current_audio), daemon=True).start()
            self._is_playing = True
            self._btn_play.content = "停止"
            self._btn_play.icon = ft.Icons.STOP
            self._btn_play.visible = True
            self.page.update()
        except Exception as ex:
            self._snack(f"播放失败: {ex}")

    def _on_open_output(self, e):
        out_dir = Path(self._txt_output.value.strip() or "temp/output")
        if not out_dir.exists():
            out_dir.mkdir(parents=True, exist_ok=True)
        _p = str(out_dir)
        if sys.platform == "win32":
            os.startfile(_p)
        elif sys.platform == "darwin":
            subprocess.run(["open", _p])
        else:
            subprocess.run(["xdg-open", _p])

    # ── Config dialog ─────────────────────────────────────────

    def _open_config_dialog(self, e):
        txt_key = ft.TextField(
            label="MODELVERSE_API_KEY",
            password=True,
            value=self._api_key if self._api_key else "",
            hint_text="输入 AstraFlow API Key",
            border=ft.InputBorder.OUTLINE,
            expand=True,
        )

        info_text = ft.Text(
            "前往 https://astraflow.ucloud.cn/ 注册并获取 API Key",
            size=12, color=ft.Colors.GREY_600,
        )

        dlg = ft.AlertDialog(
            title=ft.Text("API 配置"),
            content=ft.Container(
                content=ft.Column([info_text, txt_key], tight=True, spacing=12),
                padding=ft.Padding(left=0, top=4, right=0, bottom=4),
            ),
        )

        def _on_save(_ev):
            new_key = txt_key.value.strip()
            if new_key:
                self._api_key = new_key
                os.environ["MODELVERSE_API_KEY"] = new_key
                # Write to .env file
                _env = Path(__file__).resolve().parent.parent / ".env"
                lines: list[str] = _env.read_text("utf-8").splitlines() if _env.exists() else []
                new_lines: list[str] = []
                found = False
                for line in lines:
                    if line.strip().startswith("MODELVERSE_API_KEY="):
                        new_lines.append(f"MODELVERSE_API_KEY={new_key}")
                        found = True
                    else:
                        new_lines.append(line)
                if not found:
                    new_lines.append(f"MODELVERSE_API_KEY={new_key}")
                _env.write_text("\n".join(new_lines) + "\n", "utf-8")

                # Recreate client
                try:
                    self.client = AstraFlowClient(new_key)
                except Exception:
                    self.client = None

            dlg.open = False
            self._load_data()
            self.page.update()

        dlg.actions = [
            ft.TextButton("取消", on_click=lambda _ev: setattr(dlg, 'open', False)),
            ft.FilledButton("保存", on_click=_on_save),
        ]

        self.page.show_dialog(dlg)

    # ── Helpers ──────────────────────────────────────────────

    def _snack(self, msg: str):
        self.page.snack_bar = ft.SnackBar(content=ft.Text(msg), open=True)
        self._safe_page_update()

    # ── Voice Manager ──────────────────────────────────────────

    def _open_voice_manager(self, e):
        if not self.client:
            self._snack("请先配置 API Key")
            return

        # Upload form fields
        self._txt_voice_name = ft.TextField(
            label="音色名称", hint_text="例如: 温柔女声",
            border=ft.InputBorder.OUTLINE, expand=True,
        )
        self._txt_speaker_path = ft.TextField(
            label="参考音频", read_only=True,
            border=ft.InputBorder.OUTLINE, expand=True,
        )
        self._txt_emotion_path = ft.TextField(
            label="情绪音频 (可选)", read_only=True,
            border=ft.InputBorder.OUTLINE, expand=True,
        )

        # Voice list container (will be populated dynamically)
        self._voice_list_column = ft.Column([], spacing=4)
        voice_list_container = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Text("已上传音色", size=14, weight=ft.FontWeight.W_600),
                    ft.Container(expand=True),
                    ft.TextButton("刷新", icon=ft.Icons.REFRESH, on_click=lambda _: self._refresh_voice_list(dlg)),
                ]),
                self._voice_list_column,
            ], spacing=8),
            padding=ft.Padding(top=8, bottom=4, left=0, right=0),
        )

        dlg = ft.AlertDialog(
            title=ft.Text("音色管理"),
            content=ft.Container(
                content=ft.Column([
                    ft.Text("上传新音色", size=14, weight=ft.FontWeight.W_600),
                    self._txt_voice_name,
                    ft.Row([
                        ft.FilledTonalButton(
                            "选择参考音频", icon=ft.Icons.AUDIO_FILE,
                            on_click=self._on_pick_speaker_file,
                        ),
                        self._txt_speaker_path,
                    ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ft.Row([
                        ft.FilledTonalButton(
                            "情绪音频(可选)", icon=ft.Icons.MUSIC_NOTE,
                            on_click=self._on_pick_emotion_file,
                        ),
                        self._txt_emotion_path,
                    ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ft.Text("限制: MP3/WAV, 5-30秒, ≤20MB, ≥16kHz", size=11, color=ft.Colors.GREY_500),
                    ft.FilledButton(
                        "上传音色", icon=ft.Icons.UPLOAD,
                        on_click=lambda _: self._on_upload_voice(dlg),
                    ),
                    ft.Divider(),
                    voice_list_container,
                ], spacing=12),
                width=600,
                padding=ft.Padding(left=4, top=8, right=4, bottom=8),
            ),
        )

        self.page.show_dialog(dlg)
        self._refresh_voice_list(dlg)

    def _on_pick_speaker_file(self, e):
        """Open file picker for speaker reference audio."""
        path = filedialog.askopenfilename(
            title="选择参考音频文件",
            filetypes=[
                ("音频文件", "*.wav *.mp3"),
                ("所有文件", "*.*"),
            ],
        )
        if path:
            self._txt_speaker_path.value = path
            self.page.update()

    def _on_pick_emotion_file(self, e):
        """Open file picker for emotion reference audio."""
        path = filedialog.askopenfilename(
            title="选择情绪音频文件",
            filetypes=[
                ("音频文件", "*.wav *.mp3"),
                ("所有文件", "*.*"),
            ],
        )
        if path:
            self._txt_emotion_path.value = path
            self.page.update()

    def _on_upload_voice(self, dlg: ft.AlertDialog):
        """Upload a custom voice via the API."""
        name = (self._txt_voice_name.value or "").strip()
        speaker_path = (self._txt_speaker_path.value or "").strip()

        if not name:
            self._snack("请输入音色名称")
            return
        if not speaker_path or not Path(speaker_path).exists():
            self._snack("请选择参考音频文件")
            return
        if not self.client:
            self._snack("未连接 API")
            return

        try:
            emotion_path = (self._txt_emotion_path.value or "").strip() or None
            voice_id = self.client.upload_voice(name, speaker_path, emotion_path)
            self._snack(f"上传成功: {voice_id}")

            # Reset form
            self._txt_voice_name.value = ""
            self._txt_speaker_path.value = ""
            self._txt_emotion_path.value = ""

            # Refresh the voice list in the dialog AND the main dropdown
            self._refresh_voice_list(dlg)
            self._load_data()
        except Exception as ex:
            logger.error("Voice upload failed: %s", ex)
            self._snack(f"上传失败: {ex}")

    def _on_delete_voice(self, voice_id: str, dlg: ft.AlertDialog):
        """Delete a custom voice with confirmation."""
        if not self.client:
            return

        confirm_dlg = ft.AlertDialog(
            title=ft.Text("确认删除"),
            content=ft.Text(f"确定要删除音色 {voice_id} 吗？\n删除后无法恢复。"),
            actions=[
                ft.TextButton("取消", on_click=lambda _: setattr(confirm_dlg, 'open', False)),
                ft.FilledButton("删除", on_click=lambda _: self._do_delete_voice(voice_id, confirm_dlg, dlg)),
            ],
        )
        self.page.show_dialog(confirm_dlg)

    def _do_delete_voice(self, voice_id: str, confirm_dlg: ft.AlertDialog, manager_dlg: ft.AlertDialog):
        """Perform the delete after confirmation."""
        confirm_dlg.open = False
        try:
            if self.client and self.client.delete_voice(voice_id):
                self._snack(f"已删除: {voice_id}")
                self._refresh_voice_list(manager_dlg)
                self._load_data()
        except Exception as ex:
            logger.error("Voice deletion failed: %s", ex)
            self._snack(f"删除失败: {ex}")

    def _refresh_voice_list(self, dlg: ft.AlertDialog):
        """Refresh the voice list inside the manager dialog."""
        if not self.client:
            return
        try:
            voices = self.client.list_custom_voices()
            rows = []
            for v in voices:
                rows.append(ft.Row([
                    ft.Text(f"📤 {v.name}", size=13, expand=True),
                    ft.Text(v.id, size=11, color=ft.Colors.GREY_500, font_family="monospace"),
                    ft.IconButton(
                        icon=ft.Icons.DELETE, icon_size=18, tooltip="删除",
                        on_click=lambda e, vid=v.id: self._on_delete_voice(vid, dlg),
                    ),
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER))
            if not rows:
                rows = [ft.Text("（暂无自定义音色）", size=13, color=ft.Colors.GREY_500)]
            self._voice_list_column.controls = rows
            self.page.update()
        except Exception as ex:
            logger.warning("Failed to refresh voice list: %s", ex)
            self._snack(f"刷新失败: {ex}")
