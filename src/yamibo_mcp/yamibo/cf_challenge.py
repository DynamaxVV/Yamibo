"""Cloudflare acw_sc__v2 JS challenge solver.

The acw_sc__v2 challenge is a simple XOR-based obfuscation — no browser
fingerprinting or JS engine needed.  We extract the ``arg1`` hex string from
the challenge <script> tag, unscramble it with a fixed permutation table, then
XOR against the fixed key to produce the cookie value.
"""

from __future__ import annotations

import re

# Permutation table — constant for this version of the acw_sc__v2 challenge.
_PERM = [
    0x0F, 0x23, 0x1D, 0x18, 0x21, 0x10, 0x01, 0x26, 0x0A, 0x09,
    0x13, 0x1F, 0x28, 0x1B, 0x16, 0x17, 0x19, 0x0D, 0x06, 0x0B,
    0x27, 0x12, 0x14, 0x08, 0x0E, 0x15, 0x20, 0x1A, 0x02, 0x1E,
    0x07, 0x04, 0x11, 0x05, 0x03, 0x1C, 0x22, 0x25, 0x0C, 0x24,
]

# XOR key — constant for this version.
_KEY = "3000176000856006061501533003690027800375"

_ARG1_RE = re.compile(r"var arg1='([0-9A-Fa-f]+)'")


def _solve_acw_sc__v2(arg1: str) -> str:
    n = len(arg1)
    q: list[str] = [""] * len(_PERM)
    for i in range(n):
        for j in range(len(_PERM)):
            if _PERM[j] == i + 1:
                q[j] = arg1[i]
    u = "".join(q)
    v_chars: list[str] = []
    for i in range(0, min(len(u), len(_KEY)), 2):
        a = int(u[i : i + 2], 16) ^ int(_KEY[i : i + 2], 16)
        v_chars.append(f"{a:02x}")
    return "".join(v_chars)


def solve_acw_sc__v2_if_present(html: str) -> str | None:
    """If *html* is an acw_sc__v2 challenge page, return the cookie value.

    Returns ``None`` when the page is not a challenge.
    """
    if "var arg1=" not in html or "acw_sc__v2" not in html:
        return None
    m = _ARG1_RE.search(html)
    if m is None:
        return None
    return _solve_acw_sc__v2(m.group(1))
