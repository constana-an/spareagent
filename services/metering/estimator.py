"""Integer-cent budget arithmetic and deterministic token estimates. Never uses floats."""

from __future__ import annotations


def estimate_tokens(text: str) -> int:
    """Deterministic rough estimate (~4 characters per token); at least 1 token."""
    return max(1, (len(text) + 3) // 4)


def required_reserve_cents(estimate_cents: int, safety_margin_percent: int) -> int:
    """Estimate plus a safety margin, rounded up, because provider accounting may lag."""
    if estimate_cents < 0 or safety_margin_percent < 0:
        raise ValueError("estimate and margin must be non-negative")
    return estimate_cents + (estimate_cents * safety_margin_percent + 99) // 100


def has_budget_headroom(
    *, max_budget_cents: int, spent_cents: int, estimate_cents: int, safety_margin_percent: int
) -> bool:
    """True only when the reserved estimate fits within the remaining authorised budget."""
    remaining = max_budget_cents - spent_cents
    return remaining > 0 and required_reserve_cents(estimate_cents, safety_margin_percent) <= (
        remaining
    )
