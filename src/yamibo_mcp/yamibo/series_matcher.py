from __future__ import annotations

from dataclasses import dataclass

from yamibo_mcp.domain.models import TitleSnapshot
from yamibo_mcp.yamibo.title.normalizer import normalize_series_key


@dataclass(frozen=True)
class SeriesMatchDecision:
    base_key: str
    creator_key: str | None
    alias_keys: list[str]
    aliases: list[str]
    needs_review: bool


def build_series_match_decision(title: TitleSnapshot) -> SeriesMatchDecision:
    base_key = title.series_key
    creator_key = normalize_series_key(title.author_guess or "") or None
    alias_keys = [normalize_series_key(alias) for alias in title.title_aliases]
    alias_keys = [key for key in alias_keys if key and key != base_key]
    aliases = [alias for alias in title.title_aliases if alias and alias != title.core_title_guess]
    needs_review = title.needs_review or bool(aliases)
    return SeriesMatchDecision(
        base_key=base_key,
        creator_key=creator_key,
        alias_keys=alias_keys,
        aliases=aliases,
        needs_review=needs_review,
    )
