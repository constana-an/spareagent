import pytest

from services.metering.estimator import estimate_tokens, has_budget_headroom, required_reserve_cents


def test_reserve_rounds_up_in_integer_cents():
    assert required_reserve_cents(10, 10) == 11
    assert required_reserve_cents(11, 10) == 13  # 1.1 rounds up to 2
    assert required_reserve_cents(0, 10) == 0
    assert isinstance(required_reserve_cents(7, 33), int)


def test_negative_inputs_rejected():
    with pytest.raises(ValueError):
        required_reserve_cents(-1, 10)


def test_bill_002_insufficient_budget_blocks_start():
    assert not has_budget_headroom(
        max_budget_cents=1, spent_cents=0, estimate_cents=10, safety_margin_percent=10
    )
    # exactly enough including margin
    assert has_budget_headroom(
        max_budget_cents=11, spent_cents=0, estimate_cents=10, safety_margin_percent=10
    )
    # one cent short
    assert not has_budget_headroom(
        max_budget_cents=10, spent_cents=0, estimate_cents=10, safety_margin_percent=10
    )


def test_prior_spend_counts_against_budget():
    assert not has_budget_headroom(
        max_budget_cents=20, spent_cents=10, estimate_cents=10, safety_margin_percent=10
    )


def test_exhausted_budget_never_has_headroom():
    assert not has_budget_headroom(
        max_budget_cents=10, spent_cents=10, estimate_cents=0, safety_margin_percent=0
    )


def test_token_estimate_deterministic():
    assert estimate_tokens("") == 1
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2
