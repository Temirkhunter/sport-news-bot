"""Учёт расходов на OpenRouter: пишет каждый запрос в data/usage.jsonl
и считает агрегированную дневную статистику."""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Tuple

from loguru import logger

from config.settings import DATA_DIR

USAGE_FILE = DATA_DIR / "usage.jsonl"

# Цены OpenRouter в долларах за 1 миллион токенов (input, output).
# Источник: https://openrouter.ai/models (актуально на момент написания).
PRICES_PER_MTOK: Dict[str, Tuple[float, float]] = {
    # :free модели — всегда бесплатные
    "openai/gpt-oss-120b:free": (0.0, 0.0),
    "openai/gpt-oss-20b:free": (0.0, 0.0),
    "z-ai/glm-4.5-air:free": (0.0, 0.0),
    "meta-llama/llama-3.3-70b-instruct:free": (0.0, 0.0),
    "meta-llama/llama-3.2-3b-instruct:free": (0.0, 0.0),
    "deepseek/deepseek-v4-flash:free": (0.0, 0.0),
    "qwen/qwen3-next-80b-a3b-instruct:free": (0.0, 0.0),
    "google/gemma-4-31b-it:free": (0.0, 0.0),
    "nvidia/nemotron-3-super-120b-a12b:free": (0.0, 0.0),
    "minimax/minimax-m2.5:free": (0.0, 0.0),
    # Платные
    "deepseek/deepseek-chat": (0.14, 0.28),
    "deepseek/deepseek-chat-v3-0324": (0.14, 0.28),
    "openai/gpt-4o-mini": (0.15, 0.60),
    "openai/gpt-4o": (2.50, 10.00),
    "anthropic/claude-haiku-4.5": (1.00, 5.00),
    "meta-llama/llama-3.1-8b-instruct": (0.02, 0.05),
    "meta-llama/llama-3.1-70b-instruct": (0.30, 0.40),
}


@dataclass
class UsageRecord:
    timestamp: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    in_price, out_price = PRICES_PER_MTOK.get(model, (0.0, 0.0))
    return round(
        (prompt_tokens * in_price + completion_tokens * out_price) / 1_000_000,
        6,
    )


def record(model: str, usage: dict) -> UsageRecord:
    """Извлекает usage из ответа OpenRouter, считает стоимость и пишет в JSONL."""
    pt = int(usage.get("prompt_tokens", 0))
    ct = int(usage.get("completion_tokens", 0))
    tt = int(usage.get("total_tokens", pt + ct))
    cost = estimate_cost(model, pt, ct)
    rec = UsageRecord(
        timestamp=datetime.now(timezone.utc).isoformat(),
        model=model,
        prompt_tokens=pt,
        completion_tokens=ct,
        total_tokens=tt,
        cost_usd=cost,
    )
    try:
        with open(USAGE_FILE, "a", encoding="utf-8") as f:
            f.write(rec.to_json() + "\n")
    except OSError as e:
        logger.warning("Failed to write usage record: {}", e)
    return rec


def summary_today() -> dict:
    """Агрегат за текущие сутки (UTC). Возвращает dict для лога."""
    today = datetime.now(timezone.utc).date().isoformat()
    by_model: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {"requests": 0, "tokens": 0, "cost_usd": 0.0}
    )
    total_cost = 0.0
    total_requests = 0
    total_tokens = 0

    if not USAGE_FILE.exists():
        return {"date": today, "requests": 0, "tokens": 0, "cost_usd": 0.0, "by_model": {}}

    try:
        with open(USAGE_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    j = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not j.get("timestamp", "").startswith(today):
                    continue
                m = j.get("model", "unknown")
                by_model[m]["requests"] += 1
                by_model[m]["tokens"] += j.get("total_tokens", 0)
                by_model[m]["cost_usd"] += j.get("cost_usd", 0.0)
                total_requests += 1
                total_tokens += j.get("total_tokens", 0)
                total_cost += j.get("cost_usd", 0.0)
    except OSError:
        pass

    return {
        "date": today,
        "requests": total_requests,
        "tokens": total_tokens,
        "cost_usd": round(total_cost, 6),
        "by_model": {k: {**v, "cost_usd": round(v["cost_usd"], 6)} for k, v in by_model.items()},
    }


def log_summary() -> None:
    s = summary_today()
    logger.info(
        "💰 Usage today ({}): {} req, {} tokens, ${:.4f} | by model: {}",
        s["date"],
        s["requests"],
        s["tokens"],
        s["cost_usd"],
        {k: f"{v['requests']}x=${v['cost_usd']:.4f}" for k, v in s["by_model"].items()},
    )
