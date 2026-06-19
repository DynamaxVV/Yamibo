from __future__ import annotations

import re
import unicodedata


_TRAD_TO_SIMP = str.maketrans(
    {
        "靈": "灵",
        "應": "应",
        "戀": "恋",
        "愛": "爱",
        "與": "与",
        "門": "门",
        "風": "风",
        "漢": "汉",
        "傳": "传",
        "轉": "转",
        "載": "载",
        "畫": "画",
        "區": "区",
        "會": "会",
        "體": "体",
        "穗": "穗",
    }
)

_PUNCT_RE = re.compile(r"[\s\u200b\u200c\u200d\ufeff　]+")
_WRAPPER_RE = re.compile(r"[《》「」『』【】\[\]（）(){}<>〈〉“”\"']")
_NOISE_RE = re.compile(
    r"(第\s*\d+(?:[.\-－]\d+)?\s*[话話回章])|"
    r"(\d+(?:[.\-－]\d+)?\s*$)|"
    r"(前篇|后篇|後篇|上篇|中篇|下篇|番外|特典)\s*$",
    re.IGNORECASE,
)


def normalize_display_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("／", "/").replace("｜", "|")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def normalize_series_key(value: str) -> str:
    value = normalize_display_title(value)
    value = value.translate(_TRAD_TO_SIMP).lower()
    value = _NOISE_RE.sub("", value)
    value = _WRAPPER_RE.sub("", value)
    value = value.replace("！", "!").replace("？", "?")
    value = value.replace("～", "~").replace("・", "·")
    value = value.replace("×", "x")
    value = value.replace("/", "")
    value = value.replace("|", "")
    value = value.replace("♡", "")
    value = _PUNCT_RE.sub("", value)
    value = re.sub(r"[!?,，。:：;；~\-－_※·.]", "", value)
    return value.strip()


def strip_discuz_suffix(value: str) -> str:
    value = normalize_display_title(value)
    return re.sub(r"\s*-\s*中文百合漫画区\s*-\s*百合会\s*-\s*Powered by Discuz!\s*$", "", value)
