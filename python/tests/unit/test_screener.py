"""Unit tests for honba.entities.screener models."""

from __future__ import annotations

import unittest

from honba.entities.screener import (
    FilterOp,
    MetricKeySpec,
    MetricPeriod,
    ScreenerFilterPredicate,
    ScreenerScanRequest,
    Timeframe,
)


class ScreenerModelTests(unittest.TestCase):
    def test_metric_key_spec_valid(self):
        spec = MetricKeySpec(key="RSI", timeframe=Timeframe.D1, period=MetricPeriod.SNAPSHOT)
        self.assertEqual(spec.key, "RSI")
        self.assertEqual(spec.timeframe, Timeframe.D1)
        self.assertEqual(spec.period, MetricPeriod.SNAPSHOT)

    def test_filter_predicate_valid(self):
        pred = ScreenerFilterPredicate(
            key="market_cap_basic",
            op=FilterOp.GTE,
            value=10000000000,
        )
        self.assertEqual(pred.key, "market_cap_basic")
        self.assertEqual(pred.op, FilterOp.GTE)
        self.assertEqual(pred.value, 10000000000)

    def test_screener_scan_request_serialization(self):
        req = ScreenerScanRequest(
            market="india",
            columns=[MetricKeySpec(key="close"), MetricKeySpec(key="RSI", timeframe=Timeframe.D1)],
            range=(0, 25),
        )
        dumped = req.model_dump(by_alias=True)
        self.assertEqual(dumped["market"], "india")
        self.assertEqual(len(dumped["columns"]), 2)
        self.assertTrue(dumped["primaryOnly"])
        self.assertEqual(dumped["range"], (0, 25))


if __name__ == "__main__":
    unittest.main()
