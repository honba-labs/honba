"""EBNF to GBNF grammar converter for llama.cpp constrained sampling (E5-S10).

Converts a Lark-like EBNF subset to GBNF (llama.cpp grammar format) for
zero-syntax-hallucination LLM output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class EbnfRule:
    """Parsed EBNF rule."""

    name: str
    expression: str


def parse_ebnf(ebnf: str) -> list[EbnfRule]:
    """Parse EBNF text into a list of rules.

    Handles a subset of Lark-like EBNF:
    - rule := expression
    - | for alternatives
    - "literal" for terminals
    - [optional] for optional groups
    - (group) for grouping
    - + and * for repetition
    - %ignore for ignored patterns
    """
    rules = []

    # Remove comments
    ebnf = re.sub(r"#.*", "", ebnf)

    # Split by lines and join continuation lines
    lines = ebnf.strip().split("\n")
    joined_lines = []
    current = ""

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("%ignore"):
            if current:
                joined_lines.append(current)
                current = ""
            joined_lines.append(stripped)
            continue

        if current:
            current += " " + stripped
        else:
            current = stripped

        if ":=" in stripped:
            joined_lines.append(current)
            current = ""

    if current:
        joined_lines.append(current)

    # Parse each rule
    for line in joined_lines:
        line = line.strip()
        if not line or line.startswith("%ignore"):
            continue

        if ":=" in line:
            name, expr = line.split(":=", 1)
            rules.append(EbnfRule(name=name.strip(), expression=expr.strip()))

    return rules


def convert_expression(expr: str) -> str:
    """Convert an EBNF expression to GBNF.

    Handles:
    - "literal" -> "literal"
    - [optional] -> optional?
    - (group) -> (group)
    - a | b -> a | b
    - a+ -> a+
    - a* -> a*
    - rule_name -> rule_name (reference)
    """
    # First, handle string literals - they need special treatment
    # Find all "..." literals and temporarily replace them
    literals = []

    def replace_literal(match):
        literals.append(match.group(0))
        return f"__LIT_{len(literals) - 1}__"

    expr = re.sub(r'"([^"\\]|\\.)*"', replace_literal, expr)
    expr = re.sub(r"'([^'\\]|\\.)*'", replace_literal, expr)

    # Convert optional [ ... ] to ( ... )?
    # This is a simple regex-based approach for non-nested brackets
    while "[" in expr and "]" in expr:
        # Find innermost [ ... ]
        match = re.search(r"\[([^\[\]]*)\]", expr)
        if not match:
            break
        inner = match.group(1).strip()
        replacement = f"( {inner} )?"
        expr = expr[: match.start()] + replacement + expr[match.end() :]

    # Convert ( ... ) groups - keep as is for GBNF
    # GBNF uses ( ... ) for grouping too

    # Convert + and * - keep as is (GBNF uses same syntax)
    # But ensure they apply to the preceding element
    expr = re.sub(r"(\w|\))\+", r"\1+", expr)
    expr = re.sub(r"(\w|\))\*", r"\1*", expr)

    # Convert | alternatives - keep as is
    # GBNF uses | for alternatives

    # Restore literals
    for i, lit in enumerate(literals):
        expr = expr.replace(f"__LIT_{i}__", lit)

    # Clean up whitespace
    expr = re.sub(r"\s+", " ", expr)
    expr = expr.strip()

    return expr


def ebnf_to_gbnf(ebnf: str) -> str:
    """Convert Lark-like EBNF to GBNF format for llama.cpp.

    Args:
        ebnf: EBNF grammar string

    Returns:
        GBNF grammar string
    """
    rules = parse_ebnf(ebnf)

    gbnf_lines = []

    for rule in rules:
        gbnf_expr = convert_expression(rule.expression)
        # GBNF uses ::= for rule definitions
        gbnf_lines.append(f"{rule.name} ::= {gbnf_expr}")

    # Ensure there's a root rule - if not, use the first rule as root
    has_root = any(r.name == "root" or r.name == "start" for r in rules)
    if not has_root and rules:
        # Alias the first rule as root
        gbnf_lines.insert(0, f"root ::= {rules[0].name}")

    return "\n".join(gbnf_lines)


def build_grammar(ebnf: str) -> str:
    """Build a GBNF grammar from EBNF source.

    This is a convenience function that wraps ebnf_to_gbnf.

    Args:
        ebnf: EBNF grammar string

    Returns:
        GBNF grammar string
    """
    return ebnf_to_gbnf(ebnf)
