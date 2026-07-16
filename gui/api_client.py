"""AstraFlow IndexTTS-2 API client.

Wraps AstraFlow's OpenAI-compatible TTS endpoints using httpx.
Provides rate limiting, retry with exponential backoff, and typed
request/response dataclasses.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from gui.voice_presets import BUILTIN_VOICE_IDS

logger = logging.getLogger("atri_gui.api_client")


# ── Exceptions ──────────────────────────────────────────────────────


class AstraFlowError(Exception):
    """Raised when the AstraFlow API returns an error."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        super().__init__(message)


# ── Request dataclasses ─────────────────────────────────────────────


@dataclass
class SynthesizeRequest:
    """Payload for POST /v1/audio/speech."""

    input: str  # Text to synthesize.
    voice: str = "jack_cheng"  # Built-in name or "uspeech:xxx"
    model: str = "IndexTeam/IndexTTS-2"
    sample_rate: int = 24000  # 16000, 22050, 24000
    gain: float = 1.0  # (0, 10]
    speed: float = 1.0  # 0.25–4.0
    emo_control_method: int = 0  # 0=none, 1=audio, 2=vector, 3=text
    emo_weight: float = 0.6  # 0.0–1.0
    emo_text: str | None = None
    emo_vec: list[float] | None = None  # 8-dim emotion vector
    emo_random: bool = False
    interval_silence: int = 200  # ms
    max_text_tokens_per_sentence: int = 120
    response_format: str = "wav"
    instructions: str = ""

    def __post_init__(self) -> None:
        if self.emo_vec is not None:
            if len(self.emo_vec) != 8:
                raise ValueError(
                    f"emo_vec must have 8 elements, got {len(self.emo_vec)}"
                )
            total = sum(self.emo_vec)
            if total > 1.5:
                raise ValueError(
                    f"emo_vec sum must be ≤ 1.5, got {total}"
                )

    def to_api_dict(self) -> dict[str, Any]:
        """Build the JSON body for the API, filtering out ``None`` values."""
        body: dict[str, Any] = {
            "input": self.input,
            "voice": self.voice,
            "model": self.model,
            "sample_rate": self.sample_rate,
            "gain": self.gain,
            "speed": self.speed,
            "emo_control_method": self.emo_control_method,
            "emo_weight": self.emo_weight,
            "emo_random": self.emo_random,
            "interval_silence": self.interval_silence,
            "max_text_tokens_per_sentence": self.max_text_tokens_per_sentence,
            "response_format": self.response_format,
            "instructions": self.instructions,
        }
        if self.emo_text is not None:
            body["emo_text"] = self.emo_text
        if self.emo_vec is not None:
            body["emo_vec"] = self.emo_vec
        return body


# ── Response dataclasses ────────────────────────────────────────────


@dataclass
class CustomVoice:
    """A custom voice returned by the voice list API."""

    id: str  # "uspeech:xxx"
    name: str


@dataclass
class EmotionAnalysisResult:
    """Result from LLM-based text emotion analysis."""

    emotion_text: str  # Chinese emotion label, e.g. "愉悦兴奋"
    emotion_intensity: float  # 0.0 – 1.0
    emotion_vector: list[float]  # 8-dim, sum ≤ 0.9 (to preserve voice timbre)

    def __post_init__(self) -> None:
        if len(self.emotion_vector) != 8:
            raise ValueError(
                f"emotion_vector must have 8 elements, got {len(self.emotion_vector)}"
            )
        total = sum(self.emotion_vector)
        if total > 0.9:
            ratio = 0.9 / total
            self.emotion_vector = [round(v * ratio, 4) for v in self.emotion_vector]
        self.emotion_intensity = max(0.0, min(1.0, self.emotion_intensity))


# ── Client ──────────────────────────────────────────────────────────


class AstraFlowClient:
    """HTTP client for the AstraFlow IndexTTS-2 API.

    Enforces 10 RPM rate limiting (≥6 s between requests) and retries
    5xx errors with exponential backoff.
    """

    BASE_URL = "https://api.modelverse.cn/v1"
    MIN_REQUEST_INTERVAL = 6.0  # seconds – 10 RPM
    BUILTIN_VOICES: list[str] = BUILTIN_VOICE_IDS

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = (base_url or self.BASE_URL).rstrip("/")
        self._last_request_time: float = 0.0

        transport = httpx.HTTPTransport(retries=0)
        self._client = httpx.Client(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
            },
            timeout=httpx.Timeout(
                connect=30.0,
                read=300.0,
                write=30.0,
                pool=30.0,
            ),
            transport=transport,
        )

    # ── Public API ──────────────────────────────────────────────

    def synthesize(self, req: SynthesizeRequest) -> bytes:
        """Synthesize audio from text.

        Returns raw WAV bytes.
        """
        logger.info(
            "Synthesizing %d chars with voice=%s model=%s",
            len(req.input),
            req.voice,
            req.model,
        )
        body = req.to_api_dict()
        resp = self._request("POST", "/audio/speech", json=body)
        return resp.content

    def analyze_emotion(
        self,
        text: str,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> EmotionAnalysisResult:
        """Analyze the emotion of given text using an LLM.

        Calls an OpenAI-compatible chat/completions endpoint with a
        structured JSON output prompt.

        When ``api_key`` and ``base_url`` are provided, uses those for
        the LLM provider (e.g. separate OpenAI key).  Otherwise falls
        back to this client's own AstraFlow credentials.

        Returns an ``EmotionAnalysisResult`` with emotion text label,
        intensity (0–1), and 8-dimension emotion vector.
        """
        logger.info("Analyzing emotion for %d chars with model=%s", len(text), model)

        system_prompt = (
            "你是一个专业的情感分析助手。请分析给定中文文本的情感特征，返回严格的JSON格式。\n\n"
            "分析规则：\n"
            "1. emotion_text: 用1-3个中文词描述主要情感，如「愉悦兴奋」「悲伤忧郁」「愤怒不满」\n"
            "2. emotion_intensity: 情感强度，0.0（平静）到1.0（非常强烈）\n"
            "3. emotion_vector: 8维向量，依次为 [快乐, 愤怒, 悲伤, 恐惧, 厌恶, 抑郁, 惊讶, 平静]，"
            "每维0.0-1.0，所有维度总和必须≤0.9（情感总和过高会导致音色失真）\n\n"
            "返回格式必须为：\n"
            '{"emotion_text": "愉悦", "emotion_intensity": 0.6, '
            '"emotion_vector": [0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.1, 0.2]}'
        )

        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        }

        # Use provided LLM credentials, or fallback to this client's own
        _api_key = api_key or self._api_key
        _base_url = (base_url or self._base_url).rstrip("/")
        # OpenAI-compatible APIs require /v1 prefix before /chat/completions
        if not _base_url.endswith("/v1"):
            _base_url += "/v1"

        if base_url is not None or api_key is not None:
            # Different LLM provider — make a direct request
            _client = httpx.Client(
                base_url=_base_url,
                headers={"Authorization": f"Bearer {_api_key}"},
                timeout=httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0),
            )
            try:
                resp = _client.post("/chat/completions", json=body)
                if not resp.is_success:
                    try:
                        err_data = resp.json()
                        msg = err_data.get("message") or err_data.get("error", {}).get("message") or resp.text
                    except ValueError:
                        msg = resp.text
                    raise AstraFlowError(msg, status_code=resp.status_code)
                data = resp.json()
            finally:
                _client.close()
        else:
            resp = self._request("POST", "/chat/completions", json=body)
            data = resp.json()

        try:
            content_str: str = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AstraFlowError(
                f"LLM returned unexpected response structure: {exc}",
            ) from exc

        try:
            parsed: dict[str, Any] = json.loads(content_str)
        except json.JSONDecodeError as exc:
            logger.error("LLM returned invalid JSON: %s", content_str)
            raise AstraFlowError(f"LLM returned invalid JSON: {exc}") from exc

        emotion_text: str | None = parsed.get("emotion_text")
        emotion_intensity: Any = parsed.get("emotion_intensity")
        emotion_vector: Any = parsed.get("emotion_vector")

        if not emotion_text or not isinstance(emotion_text, str):
            raise AstraFlowError(
                "LLM analysis missing or invalid 'emotion_text' field",
            )
        if emotion_intensity is None or not isinstance(emotion_intensity, (int, float)):
            raise AstraFlowError(
                "LLM analysis missing or invalid 'emotion_intensity' field",
            )
        if not emotion_vector or not isinstance(emotion_vector, list):
            raise AstraFlowError(
                "LLM analysis missing or invalid 'emotion_vector' field",
            )

        try:
            result = EmotionAnalysisResult(
                emotion_text=emotion_text,
                emotion_intensity=float(emotion_intensity),
                emotion_vector=[float(v) for v in emotion_vector],
            )
        except (ValueError, TypeError) as exc:
            raise AstraFlowError(
                f"LLM returned invalid emotion values: {exc}",
            ) from exc

        logger.info(
            "Emotion analysis result: text=%s intensity=%.2f vector=%s",
            result.emotion_text,
            result.emotion_intensity,
            result.emotion_vector,
        )
        return result

    def list_custom_voices(self) -> list[CustomVoice]:
        """List all custom uploaded voices."""
        logger.info("Listing custom voices")
        resp = self._request("GET", "/audio/voice/list")
        data: dict[str, Any] = resp.json()
        items: list[dict[str, Any]] = data.get("list", [])
        return [CustomVoice(id=item["id"], name=item["name"]) for item in items]

    def upload_voice(
        self,
        name: str,
        audio_path: str | Path,
        emotion_path: str | Path | None = None,
        model: str = "IndexTeam/IndexTTS-2",
    ) -> str:
        """Upload a custom voice clone.

        Returns the voice ID (``uspeech:xxx``).
        """
        ap = Path(audio_path)
        logger.info("Uploading voice name=%s audio=%s", name, ap.name)

        with open(ap, "rb") as audio_fh:
            files: dict[str, Any] = {
                "name": (None, name),
                "model": (None, model),
                "speaker_file": (ap.name, audio_fh, "audio/wav"),
            }
            emo_fh = None
            if emotion_path is not None:
                ep = Path(emotion_path)
                emo_fh = open(ep, "rb")
                files["emotion_file"] = (ep.name, emo_fh, "audio/wav")

            try:
                resp = self._request("POST", "/audio/voice/upload", files=files)
            finally:
                if emo_fh is not None:
                    emo_fh.close()

        data: dict[str, Any] = resp.json()
        voice_id: str = data["id"]
        logger.info("Uploaded voice id=%s", voice_id)
        return voice_id

    def delete_voice(self, voice_id: str) -> bool:
        """Delete a custom voice by ID.

        Returns ``True`` on success.
        """
        logger.info("Deleting voice id=%s", voice_id)
        resp = self._request(
            "POST", "/audio/voice/delete", json={"id": voice_id}
        )
        data: dict[str, Any] = resp.json()
        success: bool = data.get("success", False)
        return success

    # ── Internal helpers ────────────────────────────────────────

    def _rate_limit_wait(self) -> None:
        """Block until the minimum request interval has elapsed."""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        wait = self.MIN_REQUEST_INTERVAL - elapsed
        if wait > 0:
            logger.debug("Rate limit: sleeping %.2fs", wait)
            time.sleep(wait)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Issue an HTTP request with rate limiting and retry on 5xx."""
        backoffs = (0.5, 1.0, 2.0)
        last_exc: Exception | None = None

        for attempt in range(1 + len(backoffs)):  # 1 + 3 = 4 total
            self._rate_limit_wait()

            try:
                method_upper = method.upper()
                if method_upper == "GET":
                    resp = self._client.get(path)
                elif method_upper == "POST":
                    if files is not None:
                        resp = self._client.post(path, files=files)
                    else:
                        resp = self._client.post(path, json=json)
                else:
                    resp = self._client.request(method_upper, path, json=json)
            except httpx.TimeoutException as exc:
                last_exc = exc
                self._last_request_time = time.monotonic()
                if attempt < len(backoffs):
                    delay = backoffs[attempt]
                    logger.warning(
                        "Request timeout (attempt %d/%d), retrying in %.1fs",
                        attempt + 1,
                        1 + len(backoffs),
                        delay,
                    )
                    time.sleep(delay)
                    continue
                raise AstraFlowError(
                    "Request timed out after all retries",
                ) from exc
            except httpx.RequestError as exc:
                last_exc = exc
                self._last_request_time = time.monotonic()
                if attempt < len(backoffs):
                    delay = backoffs[attempt]
                    logger.warning(
                        "Request error (attempt %d/%d): %s, retrying in %.1fs",
                        attempt + 1,
                        1 + len(backoffs),
                        exc,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                raise AstraFlowError(
                    f"Request failed after all retries: {exc}",
                ) from exc

            self._last_request_time = time.monotonic()

            # Success (2xx) — return immediately.
            if resp.is_success:
                return resp

            # 5xx — retry with backoff.
            if 500 <= resp.status_code < 600 and attempt < len(backoffs):
                delay = backoffs[attempt]
                logger.warning(
                    "Server error %d (attempt %d/%d), retrying in %.1fs",
                    resp.status_code,
                    attempt + 1,
                    1 + len(backoffs),
                    delay,
                )
                time.sleep(delay)
                continue

            # Client error (4xx) or exhausted retries — parse and raise.
            self._raise_for_error(resp)

        # Exhausted retries.
        msg = f"Request failed after {1 + len(backoffs)} attempts"
        if last_exc is not None:
            raise AstraFlowError(msg) from last_exc
        raise AstraFlowError(msg)

    def _raise_for_error(self, resp: httpx.Response) -> None:
        """Parse the error response body and raise ``AstraFlowError``."""
        try:
            data = resp.json()
        except ValueError:
            data = {}

        code: str | None = data.get("code")
        message: str = data.get("message") or data.get("detail") or resp.text
        if isinstance(code, str) and not code:
            code = None

        raise AstraFlowError(
            message=message,
            status_code=resp.status_code,
            code=code,
        )

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> AstraFlowClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
