"""Filter language parser (Design.md Section 5).

Grammar:
  filters    := or_expr
  or_expr    := and_expr { "or" and_expr }
  and_expr   := term { "and" term }
  term       := "either" or_expr "end" | "(" or_expr ")" | filter
  filter     := metric [ "on" TIMEFRAME ] [ "for" PERIOD ] predicate
  predicate  := cmp quantity
              | "between" quantity "and" quantity
              | ("in" | "not in") value { "," value }
              | ("contains" | "like") text
              | ("crosses above" | "crosses below") operand
              | ("at" | "near") preset_target
              | "within" quantity "of" preset_target
  operand    := quantity | metric
"""

from __future__ import annotations

from typing import Any

from honba.wire.screener import (
    FilterOp,
    MetricDefinition,
    MetricPeriod,
    MetricRef,
    ScreenerFilterGroup,
    ScreenerFilterPredicate,
    Timeframe,
)
from honba.data.query.quantity import QuantityError, parse_quantity, validate_quantity_for_metric
from honba.screener.catalog import MetricCatalog, MetricResolutionError


class FilterParseError(ValueError):
    """Raised when parsing fails, formatted with caret positioning."""

    def __init__(self, message: str, text: str = "", position: int = 0) -> None:
        self.message = message
        self.text = text
        self.position = position
        if text:
            # Format caret line
            pos = max(0, min(position, len(text)))
            caret_line = " " * pos + "^"
            super().__init__(f"{message}\n  {text}\n  {caret_line}")
        else:
            super().__init__(message)


_TIMEFRAME_MAP = {
    "1m": Timeframe.M1,
    "1 min": Timeframe.M1,
    "5m": Timeframe.M5,
    "5 min": Timeframe.M5,
    "15m": Timeframe.M15,
    "15 min": Timeframe.M15,
    "30m": Timeframe.M30,
    "30 min": Timeframe.M30,
    "1h": Timeframe.H1,
    "60m": Timeframe.H1,
    "1d": Timeframe.D1,
    "1 day": Timeframe.D1,
    "daily": Timeframe.D1,
    "d1": Timeframe.D1,
    "1w": Timeframe.W1,
    "1 week": Timeframe.W1,
    "weekly": Timeframe.W1,
    "w1": Timeframe.W1,
    "1mth": Timeframe.MONTH1,
    "1 month": Timeframe.MONTH1,
    "monthly": Timeframe.MONTH1,
}

_PERIOD_MAP = {
    "snapshot": MetricPeriod.SNAPSHOT,
    "ttm": MetricPeriod.TTM,
    "trailing twelve months": MetricPeriod.TTM,
    "fy": MetricPeriod.FY,
    "full year": MetricPeriod.FY,
    "fq": MetricPeriod.FQ,
    "quarterly": MetricPeriod.FQ,
    "last quarter": MetricPeriod.FQ,
    "h1": MetricPeriod.H1,
    "current": MetricPeriod.CURRENT,
}


class Token:

    def __init__(self, kind: str, value: str, pos: int) -> None:
        self.kind = kind
        self.value = value
        self.pos = pos

    def __repr__(self) -> str:
        return f"Token({self.kind}, {self.value!r}, {self.pos})"


def tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i].isspace():
            i += 1
            continue

        # Check two-char symbols first
        two = text[i : i + 2]
        if two in (">=", "<=", "!="):
            tokens.append(Token("SYMBOL", two, i))
            i += 2
            continue

        one = text[i]
        if one in (">", "<", "=", "(", ")", ","):
            tokens.append(Token("SYMBOL", one, i))
            i += 1
            continue

        # Match word or number or punctuation chunk
        # If quoted string:
        if one in ('"', "'"):
            quote = one
            start = i
            i += 1
            val_chars = []
            while i < n and text[i] != quote:
                val_chars.append(text[i])
                i += 1
            if i < n and text[i] == quote:
                i += 1
            tokens.append(Token("WORD", "".join(val_chars), start))
            continue

        start = i
        while (
            i < n
            and not text[i].isspace()
            and text[i] not in (">", "<", "=", "(", ")", ",", '"', "'")
        ):
            # Special check: stop if >=, <=, != is ahead
            if text[i : i + 2] in (">=", "<=", "!="):
                break
            i += 1

        val = text[start:i]
        tokens.append(Token("WORD", val, start))

    tokens.append(Token("EOF", "", n))
    return tokens


class Parser:

    def __init__(self, text: str, catalog: MetricCatalog, market: str | None = None) -> None:
        self.text = text
        self.catalog = catalog
        self.market = market
        self.tokens = tokenize(text)
        self.idx = 0

    @property
    def current(self) -> Token:
        return self.tokens[self.idx]

    def peek(self, offset: int = 0) -> Token:
        pos = self.idx + offset
        if pos < len(self.tokens):
            return self.tokens[pos]
        return self.tokens[-1]

    def advance(self) -> Token:
        tok = self.current
        if self.idx < len(self.tokens) - 1:
            self.idx += 1
        return tok

    def error(self, msg: str, tok: Token | None = None) -> FilterParseError:
        t = tok if tok is not None else self.current
        return FilterParseError(msg, self.text, t.pos)

    def parse(self) -> ScreenerFilterGroup:
        if self.current.kind == "EOF":
            return ScreenerFilterGroup(operator="AND", items=[])
        res = self.parse_or_expr()
        if self.current.kind != "EOF":
            raise self.error(f"unexpected token {self.current.value!r}")
        if isinstance(res, ScreenerFilterGroup):
            return res
        return ScreenerFilterGroup(operator="AND", items=[res])

    def parse_or_expr(self) -> Any:
        items = [self.parse_and_expr()]
        while self.current.value.lower() == "or":
            self.advance()  # consume 'or'
            items.append(self.parse_and_expr())

        if len(items) == 1:
            return items[0]
        return ScreenerFilterGroup(operator="OR", items=items)

    def parse_and_expr(self) -> Any:
        items = [self.parse_term()]
        while self.current.value.lower() == "and":
            # Note: between X and Y consumes 'and' inside predicate parsing,
            # so this 'and' is strictly a logical conjunction.
            self.advance()  # consume 'and'
            items.append(self.parse_term())

        if len(items) == 1:
            return items[0]
        return ScreenerFilterGroup(operator="AND", items=items)

    def parse_term(self) -> Any:
        # Check for explicit grouping: "either" or_expr "end" or "(" or_expr ")"
        if self.current.value.lower() == "either":
            self.advance()
            node = self.parse_or_expr()
            if self.current.value.lower() != "end":
                raise self.error("expected 'end' after 'either' block")
            self.advance()
            return node
        if self.current.value == "(":
            self.advance()
            node = self.parse_or_expr()
            if self.current.value != ")":
                raise self.error("expected ')'")
            self.advance()
            return node

        return self.parse_filter()

    def parse_filter(self) -> ScreenerFilterPredicate:
        # Match longest metric alias starting at current token
        metric_def, tokens_consumed = self._match_longest_metric()
        if metric_def is None:
            raise self.error(f"expected metric name, got {self.current.value!r}")

        for _ in range(tokens_consumed):
            self.advance()

        timeframe: Timeframe | None = None
        period: MetricPeriod | None = None

        # Optional [ "on" TIMEFRAME ] and [ "for" PERIOD ] in any order
        while True:
            val_lower = self.current.value.lower()
            if val_lower == "on":
                self.advance()
                tf_tok = self.current
                tf_val = self._consume_timeframe()
                if not metric_def.has_timeframe:
                    raise self.error(
                        f"metric {metric_def.key!r} does not support timeframe", tf_tok
                    )
                timeframe = tf_val
            elif val_lower == "for":
                self.advance()
                p_tok = self.current
                p_val = self._consume_period()
                if not metric_def.has_period:
                    raise self.error(f"metric {metric_def.key!r} does not support period", p_tok)
                period = p_val
            else:
                break

        # Ignore filler words 'is' or 'are' if followed by comparison, EXCEPT when part of "is not"
        if self.current.value.lower() in ("is", "are"):
            next_tok = self.peek(1)
            next_val = next_tok.value.lower()
            if next_val == "not":
                # "is not" will be matched as comparison operator NEQ
                pass
            elif next_val in (
                "above",
                "over",
                "greater",
                "more",
                "below",
                "under",
                "less",
                "at",
                "no",
                "between",
                "in",
                "like",
                "contains",
                "near",
                ">",
                ">=",
                "<",
                "<=",
                "=",
                "!=",
            ):
                self.advance()

        # Parse predicate
        op, val = self.parse_predicate(metric_def)

        return ScreenerFilterPredicate(
            key=metric_def.key,
            op=op,
            value=val,
            timeframe=timeframe,
            period=period,
        )

    def _match_longest_metric(self) -> tuple[MetricDefinition | None, int]:
        # Try slices of words from longest possible to 1
        max_lookahead = min(6, len(self.tokens) - self.idx)
        for count in range(max_lookahead, 0, -1):
            chunk_tokens = self.tokens[self.idx : self.idx + count]
            if any(t.kind == "EOF" for t in chunk_tokens):
                continue
            phrase = " ".join(t.value for t in chunk_tokens)
            try:
                metric = self.catalog.resolve(phrase)
                return metric, count
            except MetricResolutionError:
                continue
        return None, 0

    def _consume_timeframe(self) -> Timeframe:
        start_tok = self.current
        # Check two words e.g. "15 min"
        two_words = f"{self.current.value} {self.peek(1).value}".lower()
        if two_words in _TIMEFRAME_MAP:
            self.advance()
            self.advance()
            return _TIMEFRAME_MAP[two_words]

        one_word = self.current.value.lower()
        if one_word in _TIMEFRAME_MAP:
            self.advance()
            return _TIMEFRAME_MAP[one_word]

        raise self.error(f"unknown timeframe {self.current.value!r}", start_tok)

    def _consume_period(self) -> MetricPeriod:
        start_tok = self.current
        # Check up to 3 words
        three_words = (
            f"{self.current.value} {self.peek(1).value} {self.peek(2).value}".lower()
        )
        if three_words in _PERIOD_MAP:
            self.advance()
            self.advance()
            self.advance()
            return _PERIOD_MAP[three_words]

        two_words = f"{self.current.value} {self.peek(1).value}".lower()
        if two_words in _PERIOD_MAP:
            self.advance()
            self.advance()
            return _PERIOD_MAP[two_words]

        one_word = self.current.value.lower()
        if one_word in _PERIOD_MAP:
            self.advance()
            return _PERIOD_MAP[one_word]

        raise self.error(f"unknown period {self.current.value!r}", start_tok)

    def parse_predicate(self, metric: MetricDefinition) -> tuple[FilterOp, Any]:
        phrase = self.current.value.lower()

        # Between X and Y
        if phrase == "between":
            self.advance()
            q1 = self._parse_quantity_token(metric)
            if self.current.value.lower() != "and":
                raise self.error("expected 'and' in between clause")
            self.advance()
            q2 = self._parse_quantity_token(metric)
            return FilterOp.BETWEEN, [q1, q2]

        # IN or NOT IN
        if phrase == "not" and self.peek(1).value.lower() == "in":
            self.advance()
            self.advance()
            vals = self._parse_comma_values()
            return FilterOp.NOT_IN, vals
        if phrase == "in":
            self.advance()
            vals = self._parse_comma_values()
            return FilterOp.IN, vals

        # LIKE / CONTAINS
        if phrase in ("like", "contains"):
            self.advance()
            text_val = self.current.value
            self.advance()
            return FilterOp.LIKE, text_val

        # Presets: ("at" | "near") preset_target | "within" quantity "of" preset_target
        if phrase in ("at", "near"):
            kind = phrase
            # Check if followed by "least" or "most" (handled by comparisons)
            if self.peek(1).value.lower() not in ("least", "most"):
                self.advance()
                preset_phrase_tokens = []
                while self.current.kind != "EOF" and self.current.value.lower() not in ("and", "or", "end", ")"):
                    preset_phrase_tokens.append(self.current.value)
                    self.advance()
                preset_str = " ".join(preset_phrase_tokens)
                from honba.screener.presets import expand_preset, resolve_preset_key
                p_def = resolve_preset_key(preset_str)
                if p_def is None:
                    raise self.error(f"unknown preset {preset_str!r}")
                expanded = expand_preset(kind, p_def.key)
                return expanded[0].op, expanded[0].value

        # CROSSES ABOVE / CROSSES BELOW
        if phrase == "crosses":
            self.advance()
            direction = self.current.value.lower()
            if direction not in ("above", "below"):
                raise self.error("expected 'above' or 'below' after 'crosses'")
            self.advance()
            op = FilterOp.CROSSES_ABOVE if direction == "above" else FilterOp.CROSSES_BELOW
            operand = self.parse_operand(metric)
            return op, operand

        # Comparisons
        op = self._match_comparison_op()
        if op is not None:
            # Check if right operand is metric or quantity
            val = self._parse_quantity_token(metric)
            return op, val

        raise self.error(f"unexpected predicate token {self.current.value!r}")

    def _match_comparison_op(self) -> FilterOp | None:
        curr = self.current.value.lower()
        peek1 = self.peek(1).value.lower()
        peek2 = self.peek(2).value.lower()

        # Three words:
        three = f"{curr} {peek1} {peek2}"
        if three in ("no more than",):
            self.advance()
            self.advance()
            self.advance()
            return FilterOp.LTE
        if three in ("no less than",):
            self.advance()
            self.advance()
            self.advance()
            return FilterOp.GTE
        if three in ("not equal to",):
            self.advance()
            self.advance()
            self.advance()
            return FilterOp.NEQ

        # Two words:
        two = f"{curr} {peek1}"
        if two in ("greater than", "more than"):
            self.advance()
            self.advance()
            return FilterOp.GT
        if two in ("at least",):
            self.advance()
            self.advance()
            return FilterOp.GTE
        if two == "less than":
            self.advance()
            self.advance()
            return FilterOp.LT
        if two in ("at most",):
            self.advance()
            self.advance()
            return FilterOp.LTE
        if two in ("is not",):
            self.advance()
            self.advance()
            return FilterOp.NEQ

        if curr in ("above", "over", ">"):
            self.advance()
            return FilterOp.GT
        if curr == ">=":
            self.advance()
            return FilterOp.GTE
        if curr in ("below", "under", "<"):
            self.advance()
            return FilterOp.LT
        if curr == "<=":
            self.advance()
            return FilterOp.LTE
        if curr in ("equals", "is", "="):
            self.advance()
            return FilterOp.EQ
        if curr == "!=":
            self.advance()
            return FilterOp.NEQ

        return None

    def _parse_quantity_token(self, metric: MetricDefinition) -> float:
        # Collect tokens that form a quantity (e.g. "5000", "Cr" or "5000 Cr" or "10%")
        start_tok = self.current
        # Look ahead up to 3 tokens for quantity (e.g. "Rs", "10", "Cr")
        q_text_parts = [self.current.value]
        self.advance()

        # Check if next token is a multiplier suffix or unit or currency
        while self.current.kind == "WORD" and self.current.value.lower() not in (
            "and",
            "or",
            "end",
            ")",
            "on",
            "for",
        ):
            # If next token is a comparison or keyword, stop
            if self.current.value.lower() in (
                "above",
                "below",
                "over",
                "under",
                "at",
                "between",
                "in",
                "crosses",
            ):
                break
            q_text_parts.append(self.current.value)
            self.advance()
            break  # typically at most 2 parts e.g. '5000' 'Cr'

        raw_q_text = " ".join(q_text_parts)
        try:
            qty = parse_quantity(raw_q_text, market=self.market)
            validate_quantity_for_metric(qty, metric)
            return qty.value
        except QuantityError as exc:
            raise self.error(str(exc), start_tok)

    def _parse_comma_values(self) -> list[str]:
        vals: list[str] = []
        while self.current.kind != "EOF":
            val = self.current.value
            self.advance()
            vals.append(val.strip(","))
            if self.current.value == ",":
                self.advance()
            elif not val.endswith(","):
                # Check if next word is another item in list or a conjunction
                if self.current.value.lower() in ("and", "or", "end", ")"):
                    break
                # If there's another token without comma and not keyword:
                if self.current.kind == "EOF":
                    break
                # In shell: IT, Banks might be 2 tokens: "IT," and "Banks"
        return vals

    def parse_operand(self, left_metric: MetricDefinition) -> Any:
        # Check if operand is a metric
        right_metric, tokens = self._match_longest_metric()
        if right_metric is not None:
            for _ in range(tokens):
                self.advance()
            return MetricRef(key=right_metric.key)

        return self._parse_quantity_token(left_metric)


def parse_filters(
    text: str, catalog: MetricCatalog, market: str | None = "india"
) -> ScreenerFilterGroup:
    """Parse trailing filter text into a ScreenerFilterGroup."""
    parser = Parser(text, catalog, market=market)
    return parser.parse()
