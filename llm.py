"""Gemini client. If anything goes wrong, it returns None so the app can use its offline rules.

Setup: put GEMINI_API_KEY=... in a `.env` file (see .env.example) or in your environment.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.5-flash-lite"
TIMEOUT_SECONDS = 30
RETRY_CODES = {429, 500, 503, 504}  # temporary errors worth one more try
RETRY_DELAY_SECONDS = 3


def load_dotenv(path: Path = Path(__file__).parent / ".env") -> None:
    """Tiny .env reader (KEY=VALUE lines). Real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class GeminiClient:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        load_dotenv()
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.model = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
        self._client = None

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def generate_json(self, prompt: str) -> dict | None:
        """Ask Gemini for a JSON object. Returns None on a missing key, network error, or bad JSON."""
        if not self.available:
            log.info("No GEMINI_API_KEY set; using offline rules.")
            return None
        try:
            from google import genai
            from google.genai import types

            if self._client is None:
                self._client = genai.Client(
                    api_key=self.api_key,
                    http_options=types.HttpOptions(timeout=TIMEOUT_SECONDS * 1000),
                )
            config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0)
            for attempt in (1, 2):
                try:
                    response = self._client.models.generate_content(
                        model=self.model, contents=prompt, config=config)
                    break
                except Exception as exc:  # noqa: BLE001
                    if attempt == 2 or getattr(exc, "code", None) not in RETRY_CODES:
                        raise
                    log.warning("Gemini temporary error %s; retrying once.", getattr(exc, "code", "?"))
                    time.sleep(RETRY_DELAY_SECONDS)
            data = json.loads(response.text)
            if not isinstance(data, dict):
                raise ValueError("model did not return a JSON object")
            return data
        except Exception as exc:  # noqa: BLE001 - any failure must degrade to offline mode
            log.warning("Gemini call failed (%s: %s); falling back to offline rules.", type(exc).__name__, exc)
            return None
