"""Tests for shared.llm_budget input/output guardrails."""

import pytest

from shared.llm_budget import (
    MAX_OUTPUT_TOKENS_DEFAULT,
    MAX_OUTPUT_TOKENS_HARD_CAP,
    TokenBudgetExceeded,
    check_input_budget,
    clamp_max_tokens,
    estimate_messages_tokens,
    estimate_tokens,
)


class TestEstimate:
    def test_empty_returns_zero(self) -> None:
        assert estimate_tokens("") == 0

    def test_short_string_at_least_one_token(self) -> None:
        assert estimate_tokens("hi") >= 1

    def test_proportional_to_length(self) -> None:
        small = estimate_tokens("x" * 100)
        big = estimate_tokens("x" * 1000)
        assert big > small * 5  # rough scaling check


class TestCheckInputBudget:
    def test_small_input_passes(self) -> None:
        msgs = [{"role": "user", "content": "Hello"}]
        check_input_budget(msgs)  # no exception

    def test_oversized_input_raises(self) -> None:
        msgs = [{"role": "user", "content": "x" * 100_000}]  # ~25k tokens
        with pytest.raises(TokenBudgetExceeded):
            check_input_budget(msgs)

    def test_system_blocks_counted(self) -> None:
        msgs = [{"role": "user", "content": "hi"}]
        big_system = [{"type": "text", "text": "x" * 100_000}]
        with pytest.raises(TokenBudgetExceeded):
            check_input_budget(msgs, system=big_system)

    def test_custom_limit_overrides_default(self) -> None:
        msgs = [{"role": "user", "content": "x" * 800}]  # ~200 tokens
        with pytest.raises(TokenBudgetExceeded):
            check_input_budget(msgs, limit=50)


class TestClampMaxTokens:
    def test_default_when_falsy(self) -> None:
        assert clamp_max_tokens(0) == MAX_OUTPUT_TOKENS_DEFAULT
        assert clamp_max_tokens(None) == MAX_OUTPUT_TOKENS_DEFAULT

    def test_clamped_to_hard_cap(self) -> None:
        assert clamp_max_tokens(99_999) == MAX_OUTPUT_TOKENS_HARD_CAP

    def test_negative_clamped_to_one(self) -> None:
        assert clamp_max_tokens(-5) == 1

    def test_within_range_unchanged(self) -> None:
        assert clamp_max_tokens(2000) == 2000


class TestEstimateMessagesTokens:
    def test_string_content(self) -> None:
        msgs = [{"role": "user", "content": "hello world"}]
        assert estimate_messages_tokens(msgs) >= 1

    def test_block_content(self) -> None:
        msgs = [{"role": "user", "content": [{"type": "text", "text": "x" * 100}]}]
        assert estimate_messages_tokens(msgs) >= 25
