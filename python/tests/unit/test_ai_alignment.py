"""Unit tests for Financial domain alignment (E5-S11).

Tests verify:
1. benchmark_screener_accuracy_gt_90pct - synthetic dataset parses with >=90% accuracy
2. rlvr_rewards_compiler_pass - GRPOReward gives 1.0 for valid, 0.0 for invalid completions
"""

from __future__ import annotations

from honba.ai.alignment import GRPOReward, QLoRATrainer, SyntheticDataset
from honba.data.query.parser import FilterParseError, parse_filters
from honba.screener.catalog import load_catalog


class TestSyntheticDataset:
    """Tests for the synthetic financial dataset generator."""

    def test_generate_deterministic(self):
        """Dataset generation is deterministic with fixed seed."""
        ds1 = SyntheticDataset.generate(n=100, seed=42)
        ds2 = SyntheticDataset.generate(n=100, seed=42)
        assert ds1 == ds2

    def test_generate_different_seeds(self):
        """Different seeds produce different datasets."""
        ds1 = SyntheticDataset.generate(n=100, seed=42)
        ds2 = SyntheticDataset.generate(n=100, seed=43)
        assert ds1 != ds2

    def test_generate_format(self):
        """Generated items have correct format with prompt and completion."""
        ds = SyntheticDataset.generate(n=10, seed=42)
        assert len(ds) == 10
        for item in ds:
            assert "prompt" in item
            assert "completion" in item
            assert isinstance(item["prompt"], str)
            assert isinstance(item["completion"], str)
            assert len(item["prompt"]) > 0
            assert len(item["completion"]) > 0

    def test_generate_indian_market_queries(self):
        """Generated completions cover Indian market query types."""
        ds = SyntheticDataset.generate(n=100, seed=42)
        completions = [item["completion"] for item in ds]

        # Should have variety of query types
        has_screener = any(
            "close" in c.lower() or "price_earnings" in c.lower() or "market_cap" in c.lower()
            for c in completions
        )
        has_factor = any(
            "sma" in c.lower() or "rsi" in c.lower() or "recommend" in c.lower()
            for c in completions
        )
        has_backtest = any(
            "crosses above" in c.lower() or "crosses below" in c.lower() or "and" in c.lower()
            for c in completions
        )
        has_symbol = any("ticker contains" in c.lower() for c in completions)

        assert has_screener, "Should have screener filter queries"
        assert has_factor, "Should have factor signal queries"
        assert has_backtest, "Should have multi-condition queries"
        assert has_symbol, "Should have symbol lookup queries"


class TestBenchmarkScreenerAccuracy:
    """Tests for benchmark_screener_accuracy_gt_90pct acceptance criterion."""

    def test_benchmark_screener_accuracy_gt_90pct(self):
        """Synthetic dataset parses with >=90% accuracy using screener parser."""
        catalog = load_catalog()
        dataset = SyntheticDataset.generate(n=100, seed=42)

        parseable = 0
        total = len(dataset)

        for item in dataset:
            completion = item["completion"]
            try:
                parse_filters(completion, catalog=catalog, market="india")
                parseable += 1
            except FilterParseError:
                pass

        accuracy = parseable / total
        assert accuracy >= 0.90, (
            f"Parser accuracy {accuracy:.2%} < 90% (parsed {parseable}/{total})"
        )


class TestQLoRATrainer:
    """Tests for QLoRA trainer scaffold."""

    def test_train_stub_when_deps_missing(self, caplog):
        """train() logs stub message when peft/bitsandbytes/transformers not available."""
        # This test should not fail when deps are missing
        # It verifies the stub behavior
        result = QLoRATrainer.train(
            model_path="/fake/model/path",
            dataset=[{"prompt": "test", "completion": "test"}],
            output_dir="/fake/output",
        )
        assert result is None

    def test_train_returns_none_always(self):
        """train() returns None in current scaffold (no actual training)."""
        result = QLoRATrainer.train(
            model_path="/fake/model/path",
            dataset=[{"prompt": "test", "completion": "test"}],
            output_dir="/fake/output",
        )
        assert result is None


class TestGRPOReward:
    """Tests for GRPO-style compiler reward."""

    def test_reward_valid_completion(self):
        """Valid screener DSL completion scores 1.0."""
        reward_fn = GRPOReward()
        valid_completions = [
            "close > 1000",
            "price_earnings_ttm < 20 and market_cap_basic > 1000 Cr",
            "SMA50 on daily crosses above SMA200",
            "RSI on daily between 30 and 70",
            "Recommend.All on daily > 0.5",
        ]

        for completion in valid_completions:
            score = reward_fn.reward(completion, context={})
            assert score == 1.0, f"Valid completion '{completion}' should score 1.0, got {score}"

    def test_reward_invalid_completion(self):
        """Invalid screener DSL completion scores 0.0."""
        reward_fn = GRPOReward()
        invalid_completions = [
            "close >",  # incomplete
            "price_earnings_ttm < twenty",  # non-numeric
            "SMA50 on daily crosses above",  # missing operand
            "invalid_metric > 100",  # unknown metric
            "close > 100 and",  # incomplete conjunction
            "__import__('os').system('rm -rf /')",  # code injection attempt
        ]

        for completion in invalid_completions:
            score = reward_fn.reward(completion, context={})
            assert score == 0.0, f"Invalid completion '{completion}' should score 0.0, got {score}"

    def test_compiler_pass_rate(self):
        """compiler_pass_rate returns fraction of valid completions."""
        reward_fn = GRPOReward()
        completions = [
            "close > 1000",  # valid
            "pe_ratio < 20",  # valid
            "close >",  # invalid
            "invalid_metric",  # invalid
            "rsi_14 < 30",  # valid
        ]
        rate = reward_fn.compiler_pass_rate(completions)
        assert rate == 0.6, f"Expected 0.6 (3/5 valid), got {rate}"

    def test_compiler_pass_rate_empty(self):
        """compiler_pass_rate returns 0.0 for empty list."""
        reward_fn = GRPOReward()
        rate = reward_fn.compiler_pass_rate([])
        assert rate == 0.0


class TestRLVRRewardsCompilerPass:
    """Test for rlvr_rewards_compiler_pass acceptance criterion."""

    def test_rlvr_rewards_compiler_pass(self):
        """GRPOReward distinguishes valid vs invalid completions correctly."""
        reward_fn = GRPOReward()

        # Valid completions should score 1.0
        valid = [
            "close > 1000",
            "price_earnings_ttm < 25 and market_cap_basic > 500 Cr",
            "SMA50 on daily crosses above SMA200",
        ]
        for c in valid:
            assert reward_fn.reward(c, {}) == 1.0, f"Valid: {c}"

        # Invalid completions should score 0.0
        invalid = [
            "close >",  # syntax error
            "price_earnings_ttm < twenty",  # type error
            "foo > 100",  # unknown metric
            "",  # empty
        ]
        for c in invalid:
            assert reward_fn.reward(c, {}) == 0.0, f"Invalid: {c}"

        # Compiler pass rate on mixed batch
        mixed = valid + invalid
        rate = reward_fn.compiler_pass_rate(mixed)
        assert abs(rate - 0.428) < 0.01, f"Expected ~0.428 (3/7), got {rate}"
