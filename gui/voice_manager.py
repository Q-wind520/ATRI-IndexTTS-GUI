"""Voice manager dialog for ViewIndexTTS."""

import logging
from pathlib import Path
from tkinter import filedialog

import flet as ft

from gui.api_client import AstraFlowClient, CustomVoice

logger = logging.getLogger(__name__)


class VoiceManagerDialog:
    """Dialog for managing custom voices (upload, delete, list)."""

    def __init__(
        self,
        page: ft.Page,
        client: AstraFlowClient,
        on_refresh_callback=None,
    ):
        self.page = page
        self.client = client
        self.on_refresh_callback = on_refresh_callback

        # Cache
        self._custom_voices_cache: list[CustomVoice] | None = None

        # UI controls (created when dialog opens)
        self._txt_voice_name: ft.TextField | None = None
        self._txt_speaker_path: ft.TextField | None = None
        self._txt_emotion_path: ft.TextField | None = None
        self._voice_list_column: ft.Column | None = None
        self._dlg: ft.AlertDialog | None = None

    def open(self):
        """Open the voice manager dialog."""
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
                    ft.TextButton("刷新", icon=ft.Icons.REFRESH, on_click=lambda _: self._refresh_voice_list(force_refresh=True)),
                ]),
                self._voice_list_column,
            ], spacing=8),
            padding=ft.Padding(top=8, bottom=4, left=0, right=0),
        )

        self._dlg = ft.AlertDialog(
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
                        on_click=lambda _: self._on_upload_voice(),
                    ),
                    ft.Divider(),
                    voice_list_container,
                ], spacing=12),
                width=600,
                padding=ft.Padding(left=4, top=8, right=4, bottom=8),
            ),
        )

        self.page.show_dialog(self._dlg)
        self._refresh_voice_list()

    def _snack(self, msg: str):
        self.page.snack_bar = ft.SnackBar(content=ft.Text(msg), open=True)
        self.page.update()

    def _on_pick_speaker_file(self, e):
        """Open file picker for speaker reference audio."""
        path = filedialog.askopenfilename(
            title="选择参考音频文件",
            filetypes=[
                ("音频文件", "*.wav *.mp3"),
                ("所有文件", "*.*"),
            ],
        )
        if path and self._txt_speaker_path:
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
        if path and self._txt_emotion_path:
            self._txt_emotion_path.value = path
            self.page.update()

    def _on_upload_voice(self):
        """Upload a custom voice via the API."""
        if not self._txt_voice_name or not self._txt_speaker_path:
            return

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
            emotion_path = (self._txt_emotion_path.value or "").strip() or None if self._txt_emotion_path else None
            voice_id = self.client.upload_voice(name, speaker_path, emotion_path)
            self._snack(f"上传成功: {voice_id}")

            # Reset form
            if self._txt_voice_name:
                self._txt_voice_name.value = ""
            if self._txt_speaker_path:
                self._txt_speaker_path.value = ""
            if self._txt_emotion_path:
                self._txt_emotion_path.value = ""

            # Refresh the voice list and notify parent
            self._custom_voices_cache = None  # Invalidate cache
            self._refresh_voice_list()
            if self.on_refresh_callback:
                self.on_refresh_callback()
        except Exception as ex:
            logger.error("Voice upload failed: %s", ex)
            self._snack(f"上传失败: {ex}")

    def _on_delete_voice(self, voice_id: str):
        """Delete a custom voice with confirmation."""
        if not self.client:
            return

        confirm_dlg = ft.AlertDialog(
            title=ft.Text("确认删除"),
            content=ft.Text(f"确定要删除音色 {voice_id} 吗？\n删除后无法恢复。"),
            actions=[
                ft.TextButton("取消", on_click=lambda _: setattr(confirm_dlg, 'open', False)),
                ft.FilledButton("删除", on_click=lambda _: self._do_delete_voice(voice_id, confirm_dlg)),
            ],
        )
        self.page.show_dialog(confirm_dlg)

    def _do_delete_voice(self, voice_id: str, confirm_dlg: ft.AlertDialog):
        """Perform the delete after confirmation."""
        confirm_dlg.open = False
        try:
            if self.client and self.client.delete_voice(voice_id):
                self._snack(f"已删除: {voice_id}")
                self._custom_voices_cache = None  # Invalidate cache
                self._refresh_voice_list()
                if self.on_refresh_callback:
                    self.on_refresh_callback()
        except Exception as ex:
            logger.error("Voice deletion failed: %s", ex)
            self._snack(f"删除失败: {ex}")

    def _refresh_voice_list(self, force_refresh: bool = False):
        """Refresh the voice list inside the dialog.

        Args:
            force_refresh: If True, always fetch from server. If False, use cache if available.
        """
        if not self.client or not self._voice_list_column:
            return

        # Use cache if available and not forcing refresh
        if not force_refresh and self._custom_voices_cache is not None:
            voices = self._custom_voices_cache
        else:
            try:
                voices = self.client.list_custom_voices()
                self._custom_voices_cache = voices
            except Exception as ex:
                logger.warning("Failed to refresh voice list: %s", ex)
                self._snack(f"刷新失败: {ex}")
                return

        rows = []
        for v in voices:
            rows.append(ft.Row([
                ft.Text(f"📤 {v.name}", size=13, expand=True),
                ft.Text(v.id, size=11, color=ft.Colors.GREY_500, font_family="monospace"),
                ft.IconButton(
                    icon=ft.Icons.DELETE, icon_size=18, tooltip="删除",
                    on_click=lambda e, vid=v.id: self._on_delete_voice(vid),
                ),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER))
        if not rows:
            rows = [ft.Text("（暂无自定义音色）", size=13, color=ft.Colors.GREY_500)]
        self._voice_list_column.controls = rows
        self.page.update()
