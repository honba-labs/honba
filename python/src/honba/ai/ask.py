"""Query translation pipeline for natural language to screener filters (Design.md Section 13.3).

Pipeline:
free text -> select knowledge slice -> LLM -> parse & validate
          -> on error: feed caret error message back (at most max_repairs)
          -> return validated filter sentence, request, and parse diagnostics
"""

from __future__ import annotations

from dataclasses import dataclass

from honba.ai.knowledge import KnowledgePack, build_knowledge_pack
from honba.ai.llm.provider import LlmPort
from honba.entities.screener import ScreenerFilterGroup
from honba.query.parser import FilterParseError, parse_filters
from honba.screener.catalog import MetricCatalog, MetricResolutionError, load_catalog


@dataclass(frozen=True)
class AskResult:
    query: str
    filter_text: str
    filter_group: ScreenerFilterGroup
    repair_count: int
    raw_responses: list[str]


class QueryTranslationError(Exception):
    """Raised when the LLM output cannot be parsed or validated within repair limit."""



def build_system_prompt(knowledge: KnowledgePack) -> str:
    """Build compact system prompt containing grammar, metric keys, presets, and units."""
    sample_metrics = ", ".join(m["key"] for m in knowledge.metrics[:25])
    preset_phrases = ", ".join(p["label"] for p in knowledge.presets)
    return (
        "You are an assistant that translates natural language stock screening queries into Honba filter syntax.\n"
        "Rules:\n"
        "1. Output ONLY the English filter sentence on a single line. No quotes, no markdown, no explanations.\n"
        "2. Valid comparisons: above, below, at least, at most, between X and Y, in A, B, crosses above, crosses below, near/at <preset>.\n"
        "3. Metrics include: " + sample_metrics + "...\n"
        "4. Presets include: " + preset_phrases + "\n"
        "5. Money units: Cr, Lk, Mn, Bn, Tn, %. For example: 'market cap above 5000 Cr and rsi below 30'\n"
    )


def translate_query(
    user_query: str,
    llm: LlmPort,
    catalog: MetricCatalog | None = None,
    max_repairs: int = 2,
) -> AskResult:
    """Translate natural language query into a validated ScreenerFilterGroup using the LLM.

    Performs up to max_repairs feedback iterations if parsing or metric resolution fails.
    """
    if catalog is None:
        catalog = load_catalog()

    knowledge = build_knowledge_pack()
    system_prompt = build_system_prompt(knowledge)

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Translate this screener query: {user_query}"},
    ]

    repair_count = 0
    raw_responses: list[str] = []

    while repair_count <= max_repairs:
        raw_output = llm.complete(messages=messages, temperature=0.0)
        raw_responses.append(raw_output)

        filter_sentence = raw_output.strip().strip('"').strip("'")
        words = filter_sentence.split()

        try:
            filter_group = parse_filters(filter_sentence, catalog)
            return AskResult(
                query=user_query,
                filter_text=filter_sentence,
                filter_group=filter_group,
                repair_count=repair_count,
                raw_responses=raw_responses,
            )
        except (FilterParseError, MetricResolutionError) as err:
            repair_count += 1
            if repair_count > max_repairs:
                raise QueryTranslationError(
                    f"Failed to translate query after {max_repairs} repair attempts: {err}\n"
                    f"Last output: {filter_sentence}"
                ) from err

            # Feed error and caret back to LLM for repair
            error_msg = str(err)
            messages.append({"role": "assistant", "content": filter_sentence})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"The filter sentence failed validation with error:\n{error_msg}\n"
                        "Please fix the filter sentence and output ONLY the corrected filter sentence on one line."
                    ),
                }
            )

    raise QueryTranslationError("Exceeded repair attempts")
