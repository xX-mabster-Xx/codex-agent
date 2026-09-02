"""Small OpenRouter client used solely to transcribe Telegram audio."""

import base64
import logging
from typing import Any

from aiohttp import ClientSession, ClientTimeout
from aiohttp_socks import ProxyConnector


log = logging.getLogger(__name__)


class OpenRouterError(RuntimeError):
    pass


class OpenRouterClient:
    def __init__(self, api_key: str | None, model: str, proxy_url: str | None) -> None:
        self.api_key = api_key
        self.model = model
        self.proxy_url = proxy_url
        self._session: ClientSession | None = None

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def transcribe_wav(self, audio: bytes) -> str:
        if not self.api_key:
            raise OpenRouterError("OPENROUTER_API_KEY is missing in .env")
        session = self._get_session()
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Accurately transcribe this speech. Preserve its "
                                "original language. Return only the transcript."
                            ),
                        },
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": base64.b64encode(audio).decode("ascii"),
                                "format": "wav",
                            },
                        },
                    ],
                }
            ],
            "temperature": 0,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Title": "Telegram Codex",
        }
        try:
            async with session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=payload,
                headers=headers,
            ) as response:
                body: dict[str, Any] = await response.json(content_type=None)
                if response.status >= 400:
                    message = body.get("error", {}).get("message", "unknown error")
                    raise OpenRouterError(f"OpenRouter error {response.status}: {message}")
        except OpenRouterError:
            raise
        except Exception as error:
            raise OpenRouterError(f"OpenRouter request failed: {error}") from error

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise OpenRouterError("OpenRouter returned no transcription") from error
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            text = "\n".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") in {"text", "output_text"}
            ).strip()
        else:
            text = ""
        if not text:
            raise OpenRouterError("OpenRouter returned an empty transcription")
        log.info("Audio transcribed model=%s chars=%s", self.model, len(text))
        return text

    def _get_session(self) -> ClientSession:
        if self._session is None or self._session.closed:
            connector = (
                ProxyConnector.from_url(self.proxy_url) if self.proxy_url else None
            )
            self._session = ClientSession(
                connector=connector,
                timeout=ClientTimeout(total=120),
            )
        return self._session
