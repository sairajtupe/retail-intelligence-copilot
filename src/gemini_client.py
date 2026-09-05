"""Gemini client wrapper.

Only reads the API key from GEMINI_API_KEY (src/config). All calls fail fast and
gracefully so the deterministic engine always works without a key.
"""
from __future__ import annotations
import json
import re
from typing import Any

from src.config import GEMINI_API_KEY, GEMINI_MODEL, EMBED_MODEL, log


class GeminiUnavailable(Exception):
    pass


_client = None


def _get_client():
    global _client
    if not GEMINI_API_KEY:
        raise GeminiUnavailable("GEMINI_API_KEY is not set")
    if _client is None:
        try:
            from google import genai
            _client = genai.Client(api_key=GEMINI_API_KEY)
        except Exception as exc:  # noqa: BLE001
            raise GeminiUnavailable(f"gemini client init failed: {exc}") from exc
    return _client


def _extract_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()
    try:
        data = json.loads(t)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    # Best-effort: find the first balanced JSON object
    start = t.find("{")
    end = t.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(t[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Model returned non-JSON: {text[:200]!r}")


def generate_json(system_prompt: str, user_prompt: str, temperature: float = 0.2) -> dict:
    """Ask the model for a JSON response."""
    client = _get_client()
    try:
        from google.genai import types
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                temperature=temperature,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        raise GeminiUnavailable(f"Generation failed: {exc}") from exc
    if not resp.text:
        raise GeminiUnavailable("Empty model response")
    return _extract_json(resp.text)


def embed_text(text: str) -> Any:
    """Return an embedding vector (used only when the committed index uses gemini)."""
    client = _get_client()
    try:
        result = client.models.embed_content(
            model=EMBED_MODEL,
            contents=text,
        )
        return _vec(result)
    except Exception as exc:  # noqa: BLE001
        raise GeminiUnavailable(f"Embedding failed: {exc}") from exc


def _vec(result: Any) -> Any:
    emb = getattr(result, "embeddings", None)
    if not emb:
        raise ValueError("No embeddings in response")
    return list(emb[0].values) if hasattr(emb[0], "values") else list(emb[0])