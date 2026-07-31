"""Bounded, credential-safe Gemini REST client used by CloudMind.

The client deliberately keeps provider failures out of incident records.  Callers
receive a small error category and can fall back to deterministic local rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import os
import random
import time
from typing import Any, Callable, Dict, Optional

try:
    import requests
except ImportError:  # pragma: no cover - exercised through the safe fallback
    requests = None  # type: ignore


DEFAULT_MODEL = "gemini-3.6-flash"
DEFAULT_API_VERSION = "v1beta"
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


@dataclass(frozen=True)
class GeminiResult:
    text: Optional[str]
    error: Optional[str]
    attempts: int


def endpoint(
    model: Optional[str] = None,
    api_version: Optional[str] = None,
    base_url: Optional[str] = None,
) -> str:
    selected_model = (model or os.getenv("GEMINI_MODEL") or DEFAULT_MODEL).strip()
    selected_version = (api_version or os.getenv("GEMINI_API_VERSION") or DEFAULT_API_VERSION).strip()
    selected_base = (base_url or os.getenv("GEMINI_API_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    if not selected_model or "/" in selected_model or "?" in selected_model:
        selected_model = DEFAULT_MODEL
    if selected_version not in {"v1", "v1beta"}:
        selected_version = DEFAULT_API_VERSION
    if selected_base != DEFAULT_BASE_URL:
        selected_base = DEFAULT_BASE_URL
    return f"{selected_base}/{selected_version}/models/{selected_model}:generateContent"


def _retry_delay(response: Any, attempt: int, random_func: Callable[[], float]) -> float:
    retry_after = (getattr(response, "headers", None) or {}).get("Retry-After")
    if retry_after:
        try:
            return min(4.0, max(0.0, float(retry_after)))
        except (TypeError, ValueError):
            try:
                parsed = parsedate_to_datetime(retry_after)
                return min(4.0, max(0.0, parsed.timestamp() - time.time()))
            except Exception:
                return min(4.0, (0.25 * (2 ** (attempt - 1))) + (0.1 * random_func()))
    return min(4.0, (0.25 * (2 ** (attempt - 1))) + (0.1 * random_func()))


def _response_text(response: Any) -> GeminiResult:
    try:
        data = response.json()
    except Exception:
        return GeminiResult(None, "malformed_response", 1)
    candidates = data.get("candidates", []) if isinstance(data, dict) else []
    if not candidates:
        return GeminiResult(None, "empty_response", 1)
    parts = candidates[0].get("content", {}).get("parts", [])
    if not parts or not isinstance(parts[0].get("text"), str) or not parts[0]["text"].strip():
        return GeminiResult(None, "empty_response", 1)
    return GeminiResult(parts[0]["text"].strip(), None, 1)


def generate_text(
    prompt: str,
    api_key: str,
    *,
    timeout: float = 8.0,
    max_attempts: int = 3,
    max_output_tokens: int = 800,
    response_schema: Optional[Dict[str, Any]] = None,
    post_func: Optional[Callable[..., Any]] = None,
    sleep_func: Callable[[float], None] = time.sleep,
    random_func: Callable[[], float] = random.random,
) -> GeminiResult:
    if requests is None and post_func is None:
        return GeminiResult(None, "requests_unavailable", 0)
    if not api_key:
        return GeminiResult(None, "authentication_failure", 0)

    attempts = max(1, min(int(max_attempts), 3))
    request_timeout = max(0.5, min(float(timeout), 30.0))
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
    generation_config: Dict[str, Any] = {
        "temperature": 0.1,
        "maxOutputTokens": max(1, min(int(max_output_tokens), 4096)),
    }
    if response_schema:
        generation_config["responseMimeType"] = "application/json"
        generation_config["responseJsonSchema"] = response_schema
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": generation_config,
    }
    post = post_func or requests.post

    for attempt in range(1, attempts + 1):
        try:
            response = post(endpoint(), headers=headers, json=payload, timeout=request_timeout)
        except Exception as exc:
            timeout_type = getattr(requests, "Timeout", ()) if requests is not None else ()
            if timeout_type and isinstance(exc, timeout_type):
                return GeminiResult(None, "timeout", attempt)
            return GeminiResult(None, "server_error", attempt)

        status = int(getattr(response, "status_code", 0))
        if status == 200:
            parsed = _response_text(response)
            return GeminiResult(parsed.text, parsed.error, attempt)
        if status in {401, 403}:
            return GeminiResult(None, "authentication_failure", attempt)
        if status not in RETRYABLE_STATUS:
            return GeminiResult(None, "server_error", attempt)
        if attempt < attempts:
            sleep_func(_retry_delay(response, attempt, random_func))

    category = "rate_limited" if status == 429 else "server_error"
    return GeminiResult(None, category, attempts)


def call_gemini_text(
    prompt: str,
    api_key: str,
    *,
    timeout: float = 8.0,
    max_output_tokens: int = 800,
    response_schema: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    return generate_text(
        prompt,
        api_key,
        timeout=timeout,
        max_output_tokens=max_output_tokens,
        response_schema=response_schema,
    ).text
