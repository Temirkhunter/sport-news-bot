"""LLM Rewriter через OpenRouter с pydantic-валидацией.

Трёхуровневый каскад моделей:
  1) PRIMARY      — :free, основной (быстрый)
  2) FALLBACK     — :free, резерв если primary упал/рейтлимит
  3) PAID FALLBACK — платный safety-net (deepseek-chat), вызывается только если оба :free упали

Все usage-данные пишутся через usage_tracker — после каждого цикла видно
сколько токенов и денег ушло.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional

import httpx
import yaml
from loguru import logger
from pydantic import BaseModel, Field, ValidationError

from config.settings import ROOT_DIR, settings
from src.processors import usage_tracker

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

_PROMPTS_CACHE: Optional[dict] = None


def _load_prompts() -> dict:
    global _PROMPTS_CACHE
    if _PROMPTS_CACHE is None:
        path = ROOT_DIR / "config" / "prompts.yaml"
        with open(path, "r", encoding="utf-8") as f:
            _PROMPTS_CACHE = yaml.safe_load(f)
    return _PROMPTS_CACHE


class RewriteResult(BaseModel):
    text: str = Field(min_length=80, max_length=1024)
    image_prompt: str = Field(min_length=5, max_length=500)
    teams: List[str] = Field(default_factory=list)
    category: str = Field(default="football")
    is_breaking: bool = Field(default=False)
    event_key: str = Field(default="", max_length=120)


class RewriterError(Exception):
    pass


# Допустимая доля латиницы в опубликованном русском тексте.
# Считаем что HTML-теги <b>, <i> и имена собственных могут давать до ~12% латиницы.
MAX_LATIN_RATIO = 0.18


def _latin_ratio(text: str) -> float:
    """Доля латинских букв среди всех букв в тексте."""
    # Уберём HTML-теги перед оценкой
    cleaned = re.sub(r"<[^>]+>", "", text)
    cyr = sum(1 for c in cleaned if "а" <= c.lower() <= "я" or c.lower() == "ё")
    lat = sum(1 for c in cleaned if "a" <= c.lower() <= "z")
    total = cyr + lat
    if total < 50:
        return 0.0
    return lat / total


def _looks_english(text: str) -> bool:
    return _latin_ratio(text) > MAX_LATIN_RATIO


def _extract_json_block(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)
    m = re.search(r"\{[\s\S]*\}", raw)
    return m.group(0) if m else raw


async def _call_model(
    client: httpx.AsyncClient,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> tuple[str, dict]:
    """Возвращает (content, usage). Бросает RewriterError при любой беде."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 900,
    }
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://t.me/sportednews",
        "X-Title": "Sport News Bot",
    }
    try:
        r = await client.post(OPENROUTER_URL, json=payload, headers=headers, timeout=35.0)
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        raise RewriterError(f"network: {e}") from e
    if r.status_code != 200:
        raise RewriterError(f"http {r.status_code}: {r.text[:200]}")
    data = r.json()
    if "choices" not in data or not data["choices"]:
        raise RewriterError(f"empty choices: {str(data)[:200]}")
    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage") or {}
    return content, usage


async def _try_one_model(
    client: httpx.AsyncClient,
    model: str,
    original_text: str,
    source: str,
) -> RewriteResult:
    prompts = _load_prompts()["rewrite"]
    system_prompt = prompts["system"]
    user_prompt = prompts["user"].format(original_text=original_text, source=source)

    raw, usage = await _call_model(client, model, system_prompt, user_prompt)
    if usage:
        rec = usage_tracker.record(model, usage)
        logger.debug("LLM {} usage: {} tokens, ${:.5f}", model, rec.total_tokens, rec.cost_usd)

    js = _extract_json_block(raw)
    try:
        parsed = json.loads(js)
    except json.JSONDecodeError as e:
        raise RewriterError(f"invalid json from {model}: {e}; raw={raw[:160]}") from e
    try:
        result = RewriteResult.model_validate(parsed)
    except ValidationError as e:
        raise RewriterError(f"validation failed for {model}: {e}") from e

    # Russian-only: если модель скатилась в английский — отбрасываем
    if _looks_english(result.text):
        ratio = _latin_ratio(result.text)
        raise RewriterError(
            f"output too english (latin_ratio={ratio:.2f}) from {model}; "
            f"sample={result.text[:120]}"
        )

    return result


async def rewrite(original_text: str, source: str) -> RewriteResult:
    if not settings.openrouter_api_key:
        raise RewriterError("OPENROUTER_API_KEY не задан")

    # Каскад: primary → fallback → paid_fallback. Дубликатов в списке нет.
    chain = []
    for m in (
        settings.openrouter_model,
        settings.openrouter_fallback_model,
        settings.openrouter_paid_fallback_model,
    ):
        if m and m not in chain:
            chain.append(m)

    last_error: Optional[Exception] = None
    async with httpx.AsyncClient() as client:
        for i, model in enumerate(chain):
            tier = ["primary", "fallback", "paid-fallback"][i] if i < 3 else f"tier-{i}"
            try:
                result = await _try_one_model(client, model, original_text, source)
                if i > 0:
                    logger.info("✓ rewrite via {} ({})", model, tier)
                return result
            except Exception as e:
                last_error = e
                logger.warning("✗ {} ({}) failed: {}", tier, model, e)
                continue

    raise RewriterError(f"all models failed; last: {last_error}")
