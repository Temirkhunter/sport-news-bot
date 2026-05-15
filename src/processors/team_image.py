"""Поиск тематических картинок по названию команды через Wikipedia REST API.

Никаких ключей API: используется публичный endpoint
https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}

Возвращает URL миниатюры (логотип/фото) — обычно квадрат 320–800 px.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

import httpx
from loguru import logger

WIKI_TIMEOUT = 10.0
WIKI_UA = "SportNewsBot/1.0 (https://t.me/sportednews)"

# Алиасы: русский/английский варианты → каноническое название статьи Wikipedia (англ)
TEAM_ALIASES = {
    # English giants
    "real madrid": "Real Madrid CF",
    "реал мадрид": "Real Madrid CF",
    "реал": "Real Madrid CF",
    "barcelona": "FC Barcelona",
    "barca": "FC Barcelona",
    "барселона": "FC Barcelona",
    "manchester united": "Manchester United F.C.",
    "ман юнайтед": "Manchester United F.C.",
    "манчестер юнайтед": "Manchester United F.C.",
    "ман юн": "Manchester United F.C.",
    "manchester city": "Manchester City F.C.",
    "манчестер сити": "Manchester City F.C.",
    "ман сити": "Manchester City F.C.",
    "liverpool": "Liverpool F.C.",
    "ливерпуль": "Liverpool F.C.",
    "arsenal": "Arsenal F.C.",
    "арсенал": "Arsenal F.C.",
    "chelsea": "Chelsea F.C.",
    "челси": "Chelsea F.C.",
    "tottenham": "Tottenham Hotspur F.C.",
    "тоттенхэм": "Tottenham Hotspur F.C.",
    "newcastle": "Newcastle United F.C.",
    "ньюкасл": "Newcastle United F.C.",
    "aston villa": "Aston Villa F.C.",
    "астон вилла": "Aston Villa F.C.",
    "psg": "Paris Saint-Germain F.C.",
    "пcж": "Paris Saint-Germain F.C.",
    "псж": "Paris Saint-Germain F.C.",
    "paris saint-germain": "Paris Saint-Germain F.C.",
    "bayern": "FC Bayern Munich",
    "bayern munich": "FC Bayern Munich",
    "бавария": "FC Bayern Munich",
    "borussia dortmund": "Borussia Dortmund",
    "боруссия": "Borussia Dortmund",
    "leverkusen": "Bayer 04 Leverkusen",
    "байер": "Bayer 04 Leverkusen",
    "leipzig": "RB Leipzig",
    "лейпциг": "RB Leipzig",
    "juventus": "Juventus F.C.",
    "ювентус": "Juventus F.C.",
    "ac milan": "AC Milan",
    "милан": "AC Milan",
    "inter": "Inter Milan",
    "inter milan": "Inter Milan",
    "интер": "Inter Milan",
    "napoli": "S.S.C. Napoli",
    "наполи": "S.S.C. Napoli",
    "atletico": "Atlético Madrid",
    "атлетико": "Atlético Madrid",
    "sevilla": "Sevilla FC",
    "севилья": "Sevilla FC",
    "valencia": "Valencia CF",
    "валенсия": "Valencia CF",
    "porto": "FC Porto",
    "порту": "FC Porto",
    "benfica": "S.L. Benfica",
    "бенфика": "S.L. Benfica",
    "ajax": "AFC Ajax",
    "аякс": "AFC Ajax",
    # RPL
    "zenit": "FC Zenit Saint Petersburg",
    "зенит": "FC Zenit Saint Petersburg",
    "spartak": "FC Spartak Moscow",
    "спартак": "FC Spartak Moscow",
    "cska": "PFC CSKA Moscow",
    "цска": "PFC CSKA Moscow",
    "lokomotiv": "FC Lokomotiv Moscow",
    "локомотив": "FC Lokomotiv Moscow",
    "krasnodar": "FC Krasnodar",
    "краснодар": "FC Krasnodar",
    "dynamo moscow": "FC Dynamo Moscow",
    "динамо": "FC Dynamo Moscow",
    "rubin": "FC Rubin Kazan",
    "рубин": "FC Rubin Kazan",
    # KPL
    "kairat": "FC Kairat",
    "кайрат": "FC Kairat",
    "astana": "FC Astana",
    "астана": "FC Astana",
    "tobol": "FC Tobol",
    "тобол": "FC Tobol",
    "ordabasy": "FC Ordabasy",
    "ордабасы": "FC Ordabasy",
}

_PRESERVE_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ\- ]+")


def normalize_team(name: str) -> str:
    name = name.strip().lower()
    name = "".join(c for c in name if c.isalnum() or c in (" ", "-"))
    return re.sub(r"\s+", " ", name).strip()


def resolve_team(name: str) -> Optional[str]:
    """Возвращает каноническое название статьи Wikipedia для команды, если известно."""
    return TEAM_ALIASES.get(normalize_team(name))


async def fetch_wikipedia_image(title: str, lang: str = "en") -> Optional[bytes]:
    """Достаёт thumbnail из Wikipedia REST API. Возвращает bytes изображения или None."""
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title.replace(' ', '_')}"
    try:
        async with httpx.AsyncClient(timeout=WIKI_TIMEOUT, headers={"User-Agent": WIKI_UA}) as c:
            r = await c.get(url, follow_redirects=True)
            if r.status_code != 200:
                return None
            data = r.json()
            thumb = (data.get("originalimage") or data.get("thumbnail") or {}).get("source")
            if not thumb:
                return None
            img = await c.get(thumb)
            if img.status_code == 200 and len(img.content) > 3000:
                return img.content
    except Exception as e:
        logger.debug("Wikipedia fetch failed for '{}': {}", title, e)
    return None


async def get_team_image(teams: Iterable[str]) -> Optional[bytes]:
    """Перебирает команды → возвращает первое успешное изображение."""
    seen: set[str] = set()
    for raw in teams or []:
        canonical = resolve_team(raw)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        img = await fetch_wikipedia_image(canonical, lang="en")
        if img:
            logger.info("Wikipedia image for team='{}' → {}", raw, canonical)
            return img
    return None
