"""Money arithmetic - split and allocate amounts without losing or inventing cents.

Billing math ([[invoicing]], [[ratecards]], [[slacredits]]) must be exact: splitting a
charge across line items, applying a percentage discount, or dividing a credit among seats
can't leak or fabricate a cent through floating-point rounding. This subsystem works in
integer minor units (cents) and implements the classic Money patterns - allocate by ratios
and split evenly - using largest-remainder distribution so the parts always sum back to the
whole. Pure and stateless.

  * ``allocate``   split ``amount`` across ``ratios`` (e.g. tax shares) so parts sum
                   exactly to ``amount``; leftover cents go to the largest remainders.
  * ``split``      divide ``amount`` into ``n`` as-equal-as-possible parts summing to it.
  * ``percentage`` a percentage of an amount, rounded to the nearest cent (banker-safe).
  * ``format_cents`` a display string from minor units and a currency.

All inputs are integer cents; ``allocate``/``split`` guarantee ``sum(parts) == amount`` for
any amount and ratio set (including negative amounts, for refunds).
"""

from __future__ import annotations

from typing import List


def allocate(amount: int, ratios: List[float]) -> List[int]:
    amount = int(amount)
    ratios = [float(r) for r in (ratios or [])]
    if not ratios:
        raise ValueError("at least one ratio is required.")
    if any(r < 0 for r in ratios):
        raise ValueError("ratios must be non-negative.")
    total = sum(ratios)
    if total == 0:
        raise ValueError("ratios must not all be zero.")
    # floor each share, then hand out the remaining units to the largest remainders
    raw = [amount * r / total for r in ratios]
    floors = [int(x // 1) if amount >= 0 else -int((-x) // 1) for x in raw]
    remainder = amount - sum(floors)
    # order indices by fractional remainder, descending (ascending for negative leftover)
    fracs = sorted(range(len(ratios)),
                   key=lambda i: (raw[i] - floors[i]),
                   reverse=(remainder > 0))
    step = 1 if remainder > 0 else -1
    for k in range(abs(remainder)):
        floors[fracs[k % len(fracs)]] += step
    return floors


def split(amount: int, n: int) -> List[int]:
    n = int(n)
    if n < 1:
        raise ValueError("n must be >= 1.")
    return allocate(int(amount), [1] * n)


def percentage(amount: int, percent: float) -> int:
    return int(round(int(amount) * float(percent) / 100.0))


def format_cents(amount: int, currency: str = "USD") -> str:
    amount = int(amount)
    sign = "-" if amount < 0 else ""
    a = abs(amount)
    return f"{sign}{a // 100}.{a % 100:02d} {currency}"
