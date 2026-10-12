"""Financial domain alignment for tiny models (E5-S11).

Scaffolds for:
- SyntheticDataset: deterministic synthetic Indian market NL queries (3k)
- QLoRATrainer: QLoRA SFT trainer (stub when deps missing)
- GRPOReward: Compiler-reward RLVR via screener parser (GRPO-style)
"""

from __future__ import annotations

import logging
import random
from typing import Any, ClassVar

from honba.data.query.parser import FilterParseError, parse_filters

logger = logging.getLogger(__name__)


class SyntheticDataset:
    """Deterministic synthetic dataset for Indian market NL queries.

    Generates prompt/completion pairs covering:
    - Screener filters (PE, market cap, close price, etc.)
    - Factor signals (SMA, RSI, MACD crosses)
    - Backtest configurations
    - Symbol lookups
    """

    _SCREENER_TEMPLATES: ClassVar[list[str]] = [
        "close > {price}",
        "close < {price}",
        "price_earnings_ttm < {pe}",
        "price_earnings_ttm > {pe}",
        "market_cap_basic > {mcap} Cr",
        "market_cap_basic < {mcap} Cr",
        "price_book_fq < {pb}",
        "dividends_yield_current > {dy}%",
        "return_on_equity_fq > {roe}%",
        "debt_to_equity_fq < {de}",
        "total_revenue_yoy_growth_ttm > {rg}%",
        "earnings_per_share_diluted_yoy_growth_ttm > {pg}%",
    ]

    _FACTOR_TEMPLATES: ClassVar[list[str]] = [
        "SMA{fast} on daily crosses above SMA{slow}",
        "SMA{fast} on daily crosses below SMA{slow}",
        "RSI on daily > {upper}",
        "RSI on daily < {lower}",
        "RSI on daily between {lower} and {upper}",
        "Recommend.All on daily > {value}",
        "Recommend.All on daily < {value}",
    ]

    _BACKTEST_TEMPLATES: ClassVar[list[str]] = [
        "close > 1000 and price_earnings_ttm < 20",
        "market_cap_basic > 1000 Cr and return_on_equity_fq > 15%",
        "SMA50 on daily crosses above SMA200 and RSI on daily < 70",
        "price_book_fq < 3 and dividends_yield_current > 2%",
        "debt_to_equity_fq < 0.5 and total_revenue_yoy_growth_ttm > 10%",
        "earnings_per_share_diluted_yoy_growth_ttm > 15%",
        "Recommend.All on daily > 0.5 and close > 500",
    ]

    _SYMBOL_TEMPLATES: ClassVar[list[str]] = [
        "ticker contains RELIANCE",
        "ticker contains TCS",
        "ticker contains HDFCBANK",
        "ticker contains INFY",
        "ticker contains ICICIBANK",
        "ticker contains HINDUNILVR",
        "ticker contains ITC",
        "ticker contains SBIN",
        "ticker contains NIFTY50",
        "ticker contains BANKNIFTY",
        "ticker contains NIFTY100",
        "ticker contains NIFTYMIDCAP100",
    ]

    _INDIAN_STOCKS: ClassVar[list[str]] = [
        "RELIANCE",
        "TCS",
        "HDFCBANK",
        "INFY",
        "ICICIBANK",
        "HINDUNILVR",
        "ITC",
        "SBIN",
        "BHARTIARTL",
        "KOTAKBANK",
        "LT",
        "AXISBANK",
        "ASIANPAINT",
        "MARUTI",
        "SUNPHARMA",
        "TITAN",
        "ULTRACEMCO",
        "NESTLEIND",
        "POWERGRID",
        "NTPC",
    ]

    _SECTORS: ClassVar[list[str]] = [
        "IT",
        "Banks",
        "Pharma",
        "Auto",
        "FMCG",
        "Energy",
        "Metals",
        "Cement",
        "Telecom",
        "Realty",
    ]

    @classmethod
    def generate(cls, n: int = 3000, seed: int = 42) -> list[dict[str, str]]:
        """Generate deterministic synthetic dataset.

        Args:
            n: Number of samples to generate
            seed: Random seed for reproducibility

        Returns:
            List of dicts with 'prompt' and 'completion' keys
        """
        rng = random.Random(seed)
        dataset = []

        templates = (
            cls._SCREENER_TEMPLATES * 3
            + cls._FACTOR_TEMPLATES * 2
            + cls._BACKTEST_TEMPLATES
            + cls._SYMBOL_TEMPLATES * 2
        )

        for i in range(n):
            template = rng.choice(templates)
            completion = cls._fill_template(template, rng, i)
            prompt = cls._make_prompt(completion, rng)

            dataset.append({"prompt": prompt, "completion": completion})

        return dataset

    @classmethod
    def _fill_template(cls, template: str, rng: random.Random, idx: int) -> str:
        """Fill template with realistic Indian market values."""
        import re

        def repl(match):
            key = match.group(1)
            if key == "price":
                return str(rng.choice([100, 500, 1000, 1500, 2000, 2500, 3000]))
            elif key == "pe":
                return str(rng.choice([10, 15, 20, 25, 30, 35, 40, 50]))
            elif key == "mcap":
                return str(rng.choice([100, 500, 1000, 5000, 10000, 50000, 100000]))
            elif key == "pb":
                return str(rng.choice([1, 2, 3, 4, 5, 6, 8, 10]))
            elif key == "dy":
                return str(rng.choice([1, 2, 3, 4, 5]))
            elif key == "roe":
                return str(rng.choice([10, 15, 20, 25, 30, 35, 40]))
            elif key == "de":
                return str(rng.choice([0.1, 0.2, 0.5, 1.0, 1.5, 2.0]))
            elif key == "rg" or key == "pg":
                return str(rng.choice([5, 10, 15, 20, 25, 30]))
            elif key == "fast":
                return str(rng.choice([20, 50]))  # Valid SMA keys: SMA20, SMA50
            elif key == "slow":
                return str(rng.choice([50, 200]))  # Valid SMA keys: SMA50, SMA200
            elif key == "period":
                return str(rng.choice([7, 14, 21, 28]))
            elif key == "upper":
                return str(rng.choice([70, 75, 80]))
            elif key == "lower":
                return str(rng.choice([20, 25, 30]))
            elif key == "value":
                return str(rng.choice([0.5, 1.0, 1.5, 2.0, 2.5]))
            elif key == "vol":
                return str(rng.choice([10, 50, 100, 500, 1000]))
            return match.group(0)

        return re.sub(r"\{(\w+)\}", repl, template)

    @classmethod
    def _make_prompt(cls, completion: str, rng: random.Random) -> str:
        """Generate a natural language prompt for the completion."""
        prefixes = [
            "Find stocks where ",
            "Screen for ",
            "Show me companies with ",
            "Filter for ",
            "Get symbols matching ",
            "List stocks that have ",
            "Query: ",
            "Find: ",
        ]
        return rng.choice(prefixes) + completion


class QLoRATrainer:
    """QLoRA SFT trainer scaffold.

    When peft/bitsandbytes/transformers are available, runs a training step.
    Otherwise, logs a stub message and returns None.
    """

    @staticmethod
    def train(model_path: str, dataset: list[dict[str, str]], output_dir: str) -> Any:
        """Run QLoRA training or log stub.

        Args:
            model_path: Path to base model (GGUF or HF format)
            dataset: Training data from SyntheticDataset.generate()
            output_dir: Directory to save LoRA adapters

        Returns:
            None (stub) or trained model info when deps available
        """
        try:
            import accelerate  # noqa: F401  # type: ignore[import-not-found]
            import bitsandbytes  # noqa: F401  # type: ignore[import-not-found]
            import peft  # noqa: F401  # type: ignore[import-not-found]
            import transformers  # noqa: F401  # type: ignore[import-not-found]
        except ImportError:
            logger.info(
                "QLoRA deps (peft, bitsandbytes, transformers, accelerate) not installed; "
                "skipping training. Install with: pip install -e '.[alignment]'"
            )
            return None

        # TODO: Implement actual QLoRA training when deps are available
        logger.info("QLoRA training would run here (deps available)")
        logger.info(f"  model_path: {model_path}")
        logger.info(f"  dataset_size: {len(dataset)}")
        logger.info(f"  output_dir: {output_dir}")
        return {"status": "training_not_implemented"}


class GRPOReward:
    """GRPO-style compiler reward for RLVR.

    Runs LLM completion through the screener parser and returns:
    - 1.0 if it parses and executes without error (compiler pass)
    - 0.0 otherwise (compiler fail)
    """

    def __init__(self, market: str = "india"):
        self.market = market
        self._catalog = None

    @property
    def catalog(self):
        """Lazy-load metric catalog."""
        if self._catalog is None:
            from honba.screener.catalog import load_catalog

            self._catalog = load_catalog()
        return self._catalog

    def reward(self, completion: str, context: dict | None = None) -> float:
        """Compute compiler reward for a single completion.

        Args:
            completion: The LLM-generated completion string
            context: Optional context (unused in scaffold)

        Returns:
            1.0 if completion parses without error, 0.0 otherwise
        """
        if not completion or not completion.strip():
            return 0.0

        try:
            parse_filters(completion.strip(), catalog=self.catalog, market=self.market)
            return 1.0
        except FilterParseError:
            return 0.0

    def compiler_pass_rate(self, completions: list[str]) -> float:
        """Compute fraction of completions that pass compiler check.

        Args:
            completions: List of completion strings

        Returns:
            Fraction of valid completions (0.0 to 1.0)
        """
        if not completions:
            return 0.0

        passes = sum(1 for c in completions if self.reward(c, {}) == 1.0)
        return passes / len(completions)
