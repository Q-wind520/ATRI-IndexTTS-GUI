import asyncio
import logging
import os
import subprocess
import sys
import time
from playsound3 import playsound
from playsound3.playsound3 import Sound
from pathlib import Path
from threading import Thread
from tkinter import filedialog

import flet as ft
from dotenv import load_dotenv

from gui.api_client import (  # noqa: F401
    AstraFlowClient,
    AstraFlowError,
    CustomVoice,
    EmotionAnalysisResult,
    SynthesizeRequest,
)
from gui.utils import EMOTION_DIMS, get_config_dir, get_default_output_dir, get_env_path
from gui.voice_manager import VoiceManagerDialog
from gui.voice_presets import BUILTIN_VOICES, VOICE_LABEL_MAP  # noqa: F401

logger = logging.getLogger(__name__)


class TtsApp:
    def __init__(self, page: ft.Page):
        self.page = page

        # Load .env
        _env = get_env_path()
        if _env.exists():
            load_dotenv(_env)

        # TTS API key
        self._api_key = os.environ.get("MODELVERSE_API_KEY", "")

        # Initialize client (will be None if no API key)
        self.client: AstraFlowClient | None = None
        if self._api_key:
            try:
                self.client = AstraFlowClient(self._api_key)
            except Exception:
                self._api_key = ""
                self.client = None

        # LLM config for smart emotion analysis (OpenAI-compatible only)
        self._llm_api_key: str = os.environ.get("LLM_API_KEY", "")
        self._llm_model: str = os.environ.get("LLM_MODEL", "gpt-4o-mini")
        self._llm_base_url: str = os.environ.get(
            "LLM_BASE_URL", "https://api.modelverse.cn/v1",
        )

        self._current_audio: str | None = None
        self._current_sound: Sound | None = None
        self._is_playing = False

        # Voice manager dialog
        self._voice_manager: VoiceManagerDialog | None = None

        # Smart emotion control (initialized in _build_ui)
        self._btn_intelligent: ft.FilledButton = None  # type: ignore[assignment]
        self._result_card: ft.Container = None  # type: ignore[assignment]
        self._emotion_text_display: ft.Text = None  # type: ignore[assignment]
        self._emotion_intensity_bar: ft.ProgressBar = None  # type: ignore[assignment]
        self._emotion_intensity_text: ft.Text = None  # type: ignore[assignment]
        self._vec_mini_bars: list[ft.ProgressBar] = []
        self._vec_mini_labels: list[ft.Text] = []

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
        p.title = "ViewIndexTTS 语音合成"
        p.padding = 0
        p.spacing = 0
        p.window.width = 800
        p.window.min_width = 370

        p.theme = ft.Theme(color_scheme_seed=ft.Colors.INDIGO)
        p.dark_theme = ft.Theme(color_scheme_seed=ft.Colors.INDIGO_200)
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

        # ---- Emotion: text (tab 2) ----
        self._txt_emo_text = ft.TextField(
            label="情感文本",
            border=ft.InputBorder.OUTLINE, expand=True,
        )
        self._emo_sliders: list[ft.Slider] = []
        self._emo_val_texts: list[ft.Text] = []
        self._txt_emo_vec_sum = ft.Text("合计: 0.00", size=12)
        emo_vec_cells: list[ft.Control] = []
        for _i, (_en, cn) in enumerate(EMOTION_DIMS):
            s = ft.Slider(
                min=0, max=1.2, value=0, divisions=24,
                expand=True, on_change=self._on_emo_vec_change,
            )
            vt = ft.Text("0.00", size=12, width=42, text_align=ft.TextAlign.END)
            self._emo_sliders.append(s)
            self._emo_val_texts.append(vt)
            emo_vec_cells.append(
                ft.Column([
                    ft.Row([ft.Text(cn, size=12), vt], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    s,
                ], spacing=2, col={"sm": 6}),
            )
        emo_vec_cells.append(
            ft.Column([ft.Row([ft.Container(expand=True), self._txt_emo_vec_sum], spacing=4)], col={"sm": 12}),
        )
        self._container_emo_vec = ft.Container(
            content=ft.ResponsiveRow(emo_vec_cells, spacing=4),
        )

        # ---- Emotion: audio (tab 0) ----
        self._txt_emo_audio_file = ft.TextField(
            label="情感音频文件路径", border=ft.InputBorder.OUTLINE,
            expand=True, read_only=True,
        )
        self._btn_emo_audio_file = ft.FilledTonalButton(
            content="选择音频文件", icon=ft.Icons.AUDIO_FILE,
            on_click=self._on_pick_emo_audio,
        )

        # ---- Emotion: method panels (SegmentedButton + visible toggle) ----
        self._emo_method_index = 1  # 0=audio, 1=vector (default), 2=text

        self._panel_emo_audio = ft.Container(
            content=ft.Row([self._btn_emo_audio_file, self._txt_emo_audio_file], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            visible=False,
        )
        self._panel_emo_vec = self._container_emo_vec  # default visible
        self._panel_emo_text = ft.Container(
            content=self._txt_emo_text,
            visible=False,
        )

        self._seg_emo_method = ft.SegmentedButton(
            segments=[
                ft.Segment(value="0", label="情感音频"),
                ft.Segment(value="1", label="情感向量"),
                ft.Segment(value="2", label="情感文本"),
            ],
            selected=["1"],
            show_selected_icon=False,
            on_change=self._on_emo_method_tab_change,
        )

        # ---- Emotion: shared controls ----
        self._txt_emo_weight_val = ft.Text("0.60", size=14, width=44, text_align=ft.TextAlign.END)
        self._sl_emo_weight = ft.Slider(
            min=0, max=1, value=0.6, width=200,
            on_change=lambda e: (setattr(self._txt_emo_weight_val, 'value', f"{e.control.value:.2f}"), self.page.update()),
        )
        self._row_emo_intensity = ft.Row(
            [ft.Text("情感强度", size=14), self._sl_emo_weight, self._txt_emo_weight_val],
            spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._ck_emo_random = ft.Checkbox(label="随机化情感", value=False)

        # ---- Smart Emotion Control (LLM-based) ----
        self._btn_intelligent = ft.FilledButton(
            content=ft.Row([
                ft.Icon(ft.Icons.AUTO_AWESOME, size=18),
                ft.Text("智能分析情感"),
            ], spacing=4),
            style=ft.ButtonStyle(
                color=ft.Colors.ON_PRIMARY_CONTAINER,
                bgcolor=ft.Colors.PRIMARY_CONTAINER,
            ),
            on_click=self._on_intelligent_control,
        )

        # (LLM model/provider configured in API config dialog)

        # ---- Result card (initially hidden) ----
        self._emotion_text_display = ft.Text(
            "", size=20, weight=ft.FontWeight.BOLD,
            color=ft.Colors.PRIMARY,
        )

        self._emotion_intensity_bar = ft.ProgressBar(
            value=0, width=200, color=ft.Colors.PRIMARY,
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
        )

        self._emotion_intensity_text = ft.Text("0.00", size=14, weight=ft.FontWeight.W_500)

        self._vec_mini_bars.clear()
        self._vec_mini_labels.clear()
        vec_bar_cells: list[ft.Control] = []
        for _i, (_en, cn) in enumerate(EMOTION_DIMS):
            label = ft.Text(cn, size=11, color=ft.Colors.ON_SURFACE_VARIANT)
            bar = ft.ProgressBar(
                value=0, color=ft.Colors.TERTIARY,
                bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
            )
            self._vec_mini_labels.append(label)
            self._vec_mini_bars.append(bar)
            vec_bar_cells.append(
                ft.Column([
                    ft.Row([
                        label,
                        ft.Text("0.00", size=10, color=ft.Colors.ON_SURFACE_VARIANT),
                    ], spacing=4),
                    bar,
                ], spacing=2, col={"sm": 3, "md": 3}),
            )

        self._result_card = ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon(ft.Icons.AUTO_AWESOME, color=ft.Colors.PRIMARY, size=24),
                    ft.Text("AI 情感分析结果", size=15, weight=ft.FontWeight.W_600),
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Divider(height=4),
                ft.Row([
                    ft.Text("情感标签：", size=13, weight=ft.FontWeight.W_500),
                    self._emotion_text_display,
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Row([
                    ft.Text("情感强度：", size=13, weight=ft.FontWeight.W_500),
                    self._emotion_intensity_bar,
                    self._emotion_intensity_text,
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Text("情感向量各维度：", size=13, weight=ft.FontWeight.W_500),
                ft.ResponsiveRow(vec_bar_cells, spacing=8),
            ], spacing=8),
            padding=16,
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
            border=ft.Border.all(width=1, color=ft.Colors.OUTLINE_VARIANT),
            border_radius=12,
            visible=False,
        )

        # ---- Advanced parameters (collapsible, includes audio) ----
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
                ft.Divider(height=8),
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
            label="输出目录", value=str(get_default_output_dir()),
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

        # ---- File list ----
        self._file_list_container = ft.Container(
            content=ft.Column([ft.Text("（暂无文件）", size=13, color=ft.Colors.GREY_500)], spacing=4),
            padding=ft.Padding(left=0, top=4, right=0, bottom=4),
        )

        # ── Assemble ──────────────────────────────────────────

        # Bottom page tabs
        p.navigation_bar = ft.NavigationBar(
            selected_index=0,
            destinations=[
                ft.NavigationBarDestination(icon=ft.Icons.TEXT_FIELDS, label="单句合成"),
                ft.NavigationBarDestination(icon=ft.Icons.QUEUE, label="批量合成"),
            ],
            on_change=self._on_page_change,
        )

        # Page: 单句合成
        self._page_single = ft.Container(
            expand=True,
            padding=ft.Padding(left=32, top=28, right=0, bottom=28),
            content=ft.Column([
                ft.Container(
                    padding=ft.Padding(left=0, top=0, right=28, bottom=0),
                    content=ft.Column([
                        ft.Row([
                            ft.Text("ViewIndexTTS", size=22, weight=ft.FontWeight.BOLD),
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
                        self._txt_text,
                        ft.ResponsiveRow([
                            ft.Column([self._dd_voice], col={"sm": 12, "md": 12}),
                        ], spacing=12),
                        self._btn_manage_voices,
                        ft.Text("情感控制", size=15, weight=ft.FontWeight.W_600),
                        self._btn_intelligent,
                        self._result_card,
                        self._seg_emo_method,
                        self._container_emo_vec,
                        self._panel_emo_audio,
                        self._panel_emo_text,
                        self._row_emo_intensity,
                        self._ck_emo_random,
                        self._btn_advanced_toggle,
                        self._container_advanced,
                        ft.ResponsiveRow([
                            ft.Column([ft.Row([self._txt_output, self._btn_open_output], spacing=4)], col={"sm": 12, "md": 12}),
                        ], spacing=12),
                        ft.Row([self._btn_synth, self._synth_status], spacing=8),
                        self._txt_status,
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
                    ], spacing=12),
                ),
            ], scroll=ft.ScrollMode.AUTO),
        )

        # Page: 批量合成 (placeholder)
        self._page_batch = ft.Container(
            content=ft.Column([
                ft.Container(expand=True),
                ft.Text("开发中，敬请期待", size=20, color=ft.Colors.GREY_500, text_align=ft.TextAlign.CENTER),
                ft.Container(expand=True),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, expand=True),
            padding=24,
            visible=False,
        )

        p.add(ft.Column([
            self._page_single,
            self._page_batch,
        ], expand=True, spacing=0))

    # ── Theme toggle ──────────────────────────────────────────

    def _toggle_theme(self, e):
        self.page.theme_mode = (
            ft.ThemeMode.DARK if self.page.theme_mode == ft.ThemeMode.LIGHT
            else ft.ThemeMode.LIGHT
        )
        self.page.update()

    def _on_page_change(self, e):
        idx = e.control.selected_index
        self._page_single.visible = (idx == 0)
        self._page_batch.visible = (idx == 1)
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
        out_dir = Path(self._txt_output.value.strip() or str(get_default_output_dir()))
        rows: list[ft.Row | ft.Text] = []
        if out_dir.exists():
            files = sorted(out_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
            for f in files:
                if f.suffix.lower() not in (".wav", ".mp3", ".flac", ".ogg"):
                    continue
                size_kb = f.stat().st_size / 1024
                is_current = self._is_playing and str(f) == self._current_audio
                icon = ft.Icons.STOP if is_current else ft.Icons.PLAY_ARROW
                tooltip = "停止" if is_current else "播放"
                rows.append(
                    ft.Row([
                        ft.IconButton(
                            icon=icon, tooltip=tooltip, icon_size=18,
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
        if self._is_playing and path == self._current_audio:
            # Clicking the currently playing file → stop
            if self._current_sound:
                self._current_sound.stop()
            self._is_playing = False
            self._current_sound = None
            self._refresh_files()
            self.page.update()
            return

        # Stop any current playback first
        if self._is_playing and self._current_sound:
            self._current_sound.stop()
            self._current_sound = None
            self._is_playing = False

        self._current_audio = path

        try:
            self._current_sound = playsound(path, block=False)
            self._is_playing = True
            self._refresh_files()
            self.page.update()
        except Exception as ex:
            self._snack(f"播放失败: {ex}")

    def _on_voice_change(self, e):
        pass  # Voice change no longer needs to load prompts



    def _on_emo_method_tab_change(self, e):
        self._emo_method_index = int(e.data[0])
        self._panel_emo_audio.visible = (self._emo_method_index == 0)
        self._panel_emo_vec.visible = (self._emo_method_index == 1)
        self._panel_emo_text.visible = (self._emo_method_index == 2)
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

    # ── Smart Emotion Control ─────────────────────────────────

    def _on_intelligent_control(self, e):
        """Analyze synthesis text emotion via LLM and auto-populate controls."""
        text = self._txt_text.value
        if not text or not text.strip():
            self._snack("请先输入合成文本")
            return
        if not self.client:
            self._snack("请先配置 TTS API Key")
            return

        llm_api_key = self._llm_api_key
        llm_base_url = self._llm_base_url
        if not llm_api_key:
            self._snack(
                "请先配置 LLM API Key（设置 → API 配置 → 情感分析 LLM 配置）",
            )
            return
        if not llm_base_url:
            self._snack(
                "请先配置 LLM API 地址（设置 → API 配置 → 情感分析 LLM 配置）",
            )
            return

        # Loading state (UI thread — safe)
        self._btn_intelligent.disabled = True
        self._btn_intelligent.content = ft.Row([
            ft.ProgressRing(width=16, height=16, stroke_width=2),
            ft.Text("正在分析…"),
        ], spacing=8)
        self._result_card.visible = False
        self.page.update()

        model = self._llm_model
        _client = self.client

        def _run():
            try:
                result = _client.analyze_emotion(
                    text.strip(), model=model,
                    api_key=llm_api_key,
                    base_url=llm_base_url,
                )

                # All UI updates MUST go through UI thread
                def _update_ui():
                    self._emotion_text_display.value = result.emotion_text
                    self._emotion_intensity_bar.value = result.emotion_intensity
                    self._emotion_intensity_text.value = f"{result.emotion_intensity:.2f}"
                    for i, val in enumerate(result.emotion_vector):
                        self._vec_mini_bars[i].value = val
                        self._vec_mini_labels[i].value = (
                            f"{EMOTION_DIMS[i][1]} {val:.2f}"
                        )
                    self._result_card.visible = True
                    self._auto_populate_emotion(result)

                self._run_on_ui_thread(_update_ui)

            except AstraFlowError as ex:
                self._run_on_ui_thread(lambda: self._snack(f"智能分析失败: {ex}"))

            except Exception as ex:
                logger.exception("Smart emotion analysis failed")
                self._run_on_ui_thread(lambda: self._snack(f"分析出错: {ex}"))

            finally:
                def _reset_btn():
                    self._btn_intelligent.disabled = False
                    self._btn_intelligent.content = ft.Row([
                        ft.Icon(ft.Icons.AUTO_AWESOME, size=18),
                        ft.Text("智能分析情感"),
                    ], spacing=4)
                    self.page.update()
                self._run_on_ui_thread(_reset_btn)

        Thread(target=_run, daemon=True).start()

    def _auto_populate_emotion(self, result: EmotionAnalysisResult) -> None:
        """Fill existing emotion controls with LLM analysis result.

        Must be called on UI thread (via _safe_page_update).
        """
        # Switch to vector mode so user sees the sliders
        self._emo_method_index = 1
        self._seg_emo_method.selected = ["1"]
        self._panel_emo_audio.visible = False
        self._panel_emo_vec.visible = True
        self._panel_emo_text.visible = False

        # Fill vector sliders
        for i, val in enumerate(result.emotion_vector):
            clipped = min(val, 1.2)  # slider max is 1.2
            self._emo_sliders[i].value = clipped
            self._emo_val_texts[i].value = f"{clipped:.2f}"

        # Update vector sum display
        total = sum(result.emotion_vector)
        self._txt_emo_vec_sum.value = f"合计: {total:.2f}"
        self._txt_emo_vec_sum.color = ft.Colors.RED if total > 1.5 else None

        # Set emotion intensity
        self._sl_emo_weight.value = result.emotion_intensity
        self._txt_emo_weight_val.value = f"{result.emotion_intensity:.2f}"

        # Fill emotion text field
        self._txt_emo_text.value = result.emotion_text

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
        if self._is_playing:
            if self._current_sound:
                self._current_sound.stop()
                self._current_sound = None
            self._is_playing = False
        self.page.update()

        # Build emotion params
        emo_vec: list[float] | None = None
        emo_text: str | None = None
        # SegmentedButton: "0"=audio(→1), "1"=vector(→2), "2"=text(→3)
        emo_control_method = self._emo_method_index + 1

        if emo_control_method == 3:  # text
            emo_text = self._txt_emo_text.value.strip() or None
        elif emo_control_method == 2:  # vector
            emo_vec = [
                self._emo_sliders[i].value for i in range(8)
            ]
            # method=1 (audio) uses defaults

        try:
            req = SynthesizeRequest(
                input=text.strip(),
                voice=self._dd_voice.value,
                sample_rate=int(self._dd_sample_rate.value or "24000"),
                speed=float(self._sl_speed.value or 1.0),
                gain=float(self._sl_gain.value or 1.0),
                emo_control_method=emo_control_method,
                emo_weight=float(self._sl_emo_weight.value or 0.6),
                emo_text=emo_text,
                emo_vec=emo_vec,
                emo_random=self._ck_emo_random.value,
                interval_silence=int(self._txt_interval_silence.value or "200"),
                max_text_tokens_per_sentence=int(self._txt_max_tokens.value or "120"),
            )
        except ValueError as ex:
            self._snack(f"参数错误: {ex}")
            self._btn_synth.disabled = False
            self._btn_synth.content = "合成"
            self.page.update()
            return

        _client = self.client

        def _run():
            time.sleep(2)
            self._btn_synth.disabled = False
            self._btn_synth.content = "合成"
            self._safe_page_update()

            try:
                out_dir = Path(self._txt_output.value.strip() or str(get_default_output_dir()))
                out_dir.mkdir(parents=True, exist_ok=True)

                audio_bytes = _client.synthesize(req)

                ts = time.strftime("%Y%m%d_%H%M%S")
                voice_name = req.voice.replace(":", "_").replace("-", "_")[:20]
                out_path = out_dir / f"synthesis_{voice_name}_{ts}.wav"
                out_path.write_bytes(audio_bytes)

                self._current_audio = str(out_path)
                self._txt_status.value = f"✓ 已生成: {out_path.name}"
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



    def _on_open_output(self, e):
        out_dir = Path(self._txt_output.value.strip() or str(get_default_output_dir()))
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
        # ── TTS section ──
        txt_key = ft.TextField(
            label="MODELVERSE_API_KEY",
            password=True,
            value=self._api_key if self._api_key else "",
            hint_text="输入 AstraFlow API Key",
            border=ft.InputBorder.OUTLINE,
            expand=True,
        )

        tts_info = ft.Text(
            "前往 https://astraflow.ucloud.cn/ 注册并获取 TTS API Key",
            size=12, color=ft.Colors.GREY_600,
        )

        # ── LLM section (OpenAI-compatible only) ──
        llm_section_title = ft.Text(
            "情感分析 LLM 配置（OpenAI 兼容）", size=14, weight=ft.FontWeight.W_600,
        )

        # Preset quick-select: fills the URL field
        dd_url_preset = ft.Dropdown(
            label="接口预设",
            options=[
                ft.dropdown.Option("openai", "OpenAI  —  api.openai.com"),
                ft.dropdown.Option("astraflow", "AstraFlow  —  api.modelverse.cn"),
                ft.dropdown.Option("custom", "自定义"),
            ],
            value="custom",
            border=ft.InputBorder.OUTLINE,
            width=280,
        )

        txt_llm_url = ft.TextField(
            label="API 地址",
            value=self._llm_base_url,
            hint_text="OpenAI 兼容接口的完整 URL",
            border=ft.InputBorder.OUTLINE,
            expand=True,
        )

        txt_llm_key = ft.TextField(
            label="LLM API Key",
            password=True,
            value=self._llm_api_key,
            hint_text="输入 LLM 服务商的 API Key",
            border=ft.InputBorder.OUTLINE,
            expand=True,
        )

        txt_llm_model = ft.TextField(
            label="模型名称",
            value=self._llm_model,
            hint_text="例: gpt-4o-mini, deepseek-chat, qwen-plus",
            border=ft.InputBorder.OUTLINE,
            expand=True,
        )

        def _apply_preset(preset: str):
            if preset == "openai":
                txt_llm_url.value = "https://api.openai.com/v1"
            elif preset == "astraflow":
                txt_llm_url.value = "https://api.modelverse.cn/v1"
            # "custom" — leave as-is

        def _on_url_preset_change(ev):
            _apply_preset(dd_url_preset.value)
            self.page.update()

        dd_url_preset.on_change = _on_url_preset_change

        # Set initial preset based on current URL
        _base = self._llm_base_url.rstrip("/")
        if "api.openai.com" in _base:
            dd_url_preset.value = "openai"
        elif "api.modelverse.cn" in _base:
            dd_url_preset.value = "astraflow"
        else:
            dd_url_preset.value = "custom"

        llm_hint = ft.Text(
            "选择预设快速填入接口地址，也可手动输入任意 OpenAI 兼容地址。",
            size=11, color=ft.Colors.GREY_600,
        )

        dlg = ft.AlertDialog(
            title=ft.Text("API 配置"),
            content=ft.Container(
                content=ft.Column([
                    tts_info,
                    txt_key,
                    ft.Divider(height=8),
                    llm_section_title,
                    ft.Row([dd_url_preset, txt_llm_url], spacing=8,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    txt_llm_key,
                    txt_llm_model,
                    llm_hint,
                ], tight=True, spacing=10, scroll=ft.ScrollMode.AUTO),
                padding=ft.Padding(left=0, top=4, right=0, bottom=4),
                width=540,
            ),
        )

        def _write_env_var(lines: list[str], key: str, value: str) -> list[str]:
            """Update or append a KEY=VALUE pair in env lines."""
            new_lines: list[str] = []
            found = False
            for line in lines:
                if line.strip().startswith(f"{key}="):
                    new_lines.append(f"{key}={value}")
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                new_lines.append(f"{key}={value}")
            return new_lines

        def _on_save(_ev):
            _env = get_env_path()
            lines: list[str] = _env.read_text("utf-8").splitlines() if _env.exists() else []

            # Save TTS key
            new_key = txt_key.value.strip()
            if new_key:
                self._api_key = new_key
                os.environ["MODELVERSE_API_KEY"] = new_key
                lines = _write_env_var(lines, "MODELVERSE_API_KEY", new_key)

                # Recreate client
                try:
                    self.client = AstraFlowClient(new_key)
                except Exception:
                    self.client = None

            # Save LLM config (always OpenAI-compatible, independent key)
            llm_key = txt_llm_key.value.strip()
            llm_model = txt_llm_model.value.strip() or "gpt-4o-mini"
            llm_url = txt_llm_url.value.strip()
            # Normalize: OpenAI-compatible APIs need /v1 path prefix
            if llm_url and not llm_url.rstrip("/").endswith("/v1"):
                llm_url = llm_url.rstrip("/") + "/v1"

            self._llm_api_key = llm_key
            self._llm_model = llm_model
            self._llm_base_url = llm_url

            os.environ["LLM_API_KEY"] = llm_key
            os.environ["LLM_MODEL"] = llm_model
            os.environ["LLM_BASE_URL"] = llm_url

            lines = _write_env_var(lines, "LLM_API_KEY", llm_key)
            lines = _write_env_var(lines, "LLM_MODEL", llm_model)
            lines = _write_env_var(lines, "LLM_BASE_URL", llm_url)

            _env.write_text("\n".join(lines) + "\n", "utf-8")

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
        self.page.update()

    # ── Voice Manager ──────────────────────────────────────────

    def _open_voice_manager(self, e):
        if not self.client:
            self._snack("请先配置 API Key")
            return

        # Create voice manager dialog if not exists
        if self._voice_manager is None:
            self._voice_manager = VoiceManagerDialog(
                page=self.page,
                client=self.client,
                on_refresh_callback=self._load_data,
            )

        self._voice_manager.open()
