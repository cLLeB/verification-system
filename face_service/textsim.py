"""String similarity — measure how close two strings are.

Fuzzy matching underlies dedupe, [[sanctions]] screening, and typo-tolerant lookup. Beyond
the token/phonetic approaches already here ([[phonetics]]), character-level metrics quantify
*how* similar two strings are: edit distance for the count of changes, and Jaro-Winkler for a
0–1 score that rewards common prefixes (well suited to names). This subsystem provides both,
dependency-free, so callers can pick the right notion of "close".

  * ``levenshtein``     the minimum single-character edits (insert/delete/substitute).
  * ``levenshtein_ratio`` a 0–1 similarity from edit distance and length.
  * ``jaro`` / ``jaro_winkler`` — the Jaro and prefix-boosted Jaro-Winkler scores.
  * ``similarity``      dispatch to a named metric returning a 0–1 score.

Jaro-Winkler is the recommended default for personal names; ``levenshtein_ratio`` is better
for codes and short identifiers. All comparisons are case-insensitive by default.
"""

from __future__ import annotations


def _prep(s: str, case_insensitive: bool) -> str:
    s = s or ""
    return s.lower() if case_insensitive else s


def levenshtein(a: str, b: str, case_insensitive: bool = True) -> int:
    a, b = _prep(a, case_insensitive), _prep(b, case_insensitive)
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[-1]


def levenshtein_ratio(a: str, b: str, case_insensitive: bool = True) -> float:
    a2, b2 = _prep(a, case_insensitive), _prep(b, case_insensitive)
    if not a2 and not b2:
        return 1.0
    dist = levenshtein(a2, b2, case_insensitive=False)
    return round(1 - dist / max(len(a2), len(b2)), 6)


def jaro(a: str, b: str, case_insensitive: bool = True) -> float:
    a, b = _prep(a, case_insensitive), _prep(b, case_insensitive)
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    match_dist = max(len(a), len(b)) // 2 - 1
    match_dist = max(0, match_dist)
    a_matches = [False] * len(a)
    b_matches = [False] * len(b)
    matches = 0
    for i, ca in enumerate(a):
        lo = max(0, i - match_dist)
        hi = min(i + match_dist + 1, len(b))
        for j in range(lo, hi):
            if not b_matches[j] and b[j] == ca:
                a_matches[i] = b_matches[j] = True
                matches += 1
                break
    if matches == 0:
        return 0.0
    # count transpositions
    t = 0
    k = 0
    for i in range(len(a)):
        if a_matches[i]:
            while not b_matches[k]:
                k += 1
            if a[i] != b[k]:
                t += 1
            k += 1
    t //= 2
    m = matches
    return round((m / len(a) + m / len(b) + (m - t) / m) / 3, 6)


def jaro_winkler(a: str, b: str, prefix_weight: float = 0.1,
                 case_insensitive: bool = True) -> float:
    j = jaro(a, b, case_insensitive)
    a2, b2 = _prep(a, case_insensitive), _prep(b, case_insensitive)
    prefix = 0
    for ca, cb in zip(a2, b2):
        if ca == cb and prefix < 4:
            prefix += 1
        else:
            break
    return round(j + prefix * prefix_weight * (1 - j), 6)


def similarity(a: str, b: str, algorithm: str = "jaro_winkler") -> float:
    algorithm = (algorithm or "").strip().lower()
    if algorithm == "jaro_winkler":
        return jaro_winkler(a, b)
    if algorithm == "jaro":
        return jaro(a, b)
    if algorithm == "levenshtein":
        return levenshtein_ratio(a, b)
    raise ValueError("algorithm must be jaro_winkler, jaro, or levenshtein.")
