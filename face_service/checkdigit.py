"""Check digits — detect typos in badge, card, and reference numbers.

Numbers people key in (badge IDs, credential serials, membership numbers) get mistyped.
A check digit appended to the number catches the common errors — a single wrong digit, or
two adjacent digits swapped — before the number is looked up. This subsystem implements two
standard schemes: Luhn (mod-10, as used on payment cards) and Damm (a mod-10 quasigroup
that also catches all adjacent transpositions, which Luhn misses). Pure and stateless.

  * ``luhn_generate`` / ``luhn_validate`` — append/verify a Luhn check digit.
  * ``damm_generate`` / ``damm_validate`` — append/verify a Damm check digit.
  * ``generate`` / ``validate`` — dispatch by scheme name.

Damm is the stronger default: it detects every single-digit error and every adjacent
transposition with one appended digit, whereas Luhn misses the ``09``↔``90`` transposition.
Inputs are digit strings; non-digits raise.
"""

from __future__ import annotations

# Damm operation table (weakly totally anti-symmetric quasigroup, order 10)
_DAMM = [
    [0, 3, 1, 7, 5, 9, 8, 6, 4, 2],
    [7, 0, 9, 2, 1, 5, 4, 8, 6, 3],
    [4, 2, 0, 6, 8, 7, 1, 3, 5, 9],
    [1, 7, 5, 0, 9, 8, 3, 4, 2, 6],
    [6, 1, 2, 3, 0, 4, 5, 9, 7, 8],
    [3, 6, 7, 4, 2, 0, 9, 5, 8, 1],
    [5, 8, 6, 9, 7, 2, 0, 1, 3, 4],
    [8, 9, 4, 5, 3, 6, 2, 0, 1, 7],
    [9, 4, 3, 8, 6, 1, 7, 2, 0, 5],
    [2, 5, 8, 1, 4, 3, 6, 7, 9, 0],
]


def _digits(number: str):
    s = (number or "").strip()
    if not s or not s.isdigit():
        raise ValueError("number must be a non-empty digit string.")
    return [int(c) for c in s]


def luhn_check_digit(number: str) -> int:
    digits = _digits(number)
    total = 0
    # the appended check digit sits at an even position from the right;
    # so double every digit starting from the rightmost of the base number
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - (total % 10)) % 10


def luhn_generate(number: str) -> str:
    return (number or "").strip() + str(luhn_check_digit(number))


def luhn_validate(number: str) -> bool:
    s = (number or "").strip()
    if len(s) < 2 or not s.isdigit():
        return False
    return luhn_check_digit(s[:-1]) == int(s[-1])


def damm_check_digit(number: str) -> int:
    interim = 0
    for d in _digits(number):
        interim = _DAMM[interim][d]
    return interim


def damm_generate(number: str) -> str:
    return (number or "").strip() + str(damm_check_digit(number))


def damm_validate(number: str) -> bool:
    s = (number or "").strip()
    if not s or not s.isdigit():
        return False
    interim = 0
    for d in s:
        interim = _DAMM[interim][int(d)]
    return interim == 0


def generate(number: str, scheme: str = "damm") -> str:
    scheme = (scheme or "").strip().lower()
    if scheme == "luhn":
        return luhn_generate(number)
    if scheme == "damm":
        return damm_generate(number)
    raise ValueError("scheme must be 'luhn' or 'damm'.")


def validate(number: str, scheme: str = "damm") -> bool:
    scheme = (scheme or "").strip().lower()
    if scheme == "luhn":
        return luhn_validate(number)
    if scheme == "damm":
        return damm_validate(number)
    raise ValueError("scheme must be 'luhn' or 'damm'.")
