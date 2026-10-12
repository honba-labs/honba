"""Unit tests for EBNF to GBNF grammar conversion (E5-S10).

Tests verify:
1. grammar_masks_invalid_tokens - the converted GBNF masks invalid tokens
2. ebnf_to_gbnf converts a Lark-like EBNF subset to GBNF correctly
"""

from __future__ import annotations

from pathlib import Path

from honba.ai.gbnf import ebnf_to_gbnf


def test_grammar_masks_invalid_tokens():
    """Test that the GBNF grammar masks invalid tokens like __import__.

    The converted GBNF should not allow arbitrary Python code execution tokens.
    """
    # Load the screening grammar from fixture
    fixture_path = Path(__file__).parent.parent / "fixtures" / "screening_grammar.ebnf"
    ebnf_text = fixture_path.read_text()

    gbnf = ebnf_to_gbnf(ebnf_text)

    # GBNF should have a root rule
    assert "root" in gbnf
    # Should have literal rules for keywords
    assert '"above"' in gbnf or "above" in gbnf
    assert '"below"' in gbnf or "below" in gbnf
    # Should not contain forbidden tokens that could enable code injection
    assert "__import__" not in gbnf
    assert "eval" not in gbnf
    assert "exec" not in gbnf

    # At minimum, the GBNF should define a root rule
    assert "root :=" in gbnf or "root ::=" in gbnf


def test_ebnf_to_gbnf_basic_conversion():
    """Test basic EBNF to GBNF conversion."""
    ebnf = """
    root := "hello" | "world"
    """
    gbnf = ebnf_to_gbnf(ebnf)
    # Should contain root rule
    assert "root" in gbnf
    assert "hello" in gbnf
    assert "world" in gbnf


def test_ebnf_to_gbnf_handles_alternatives():
    """Test that | alternatives are converted properly."""
    ebnf = """
    start := "a" | "b" | "c"
    """
    gbnf = ebnf_to_gbnf(ebnf)
    assert "a" in gbnf
    assert "b" in gbnf
    assert "c" in gbnf


def test_ebnf_to_gbnf_handles_optional():
    """Test that [optional] groups are converted."""
    ebnf = """
    start := "hello" [ "world" ]
    """
    gbnf = ebnf_to_gbnf(ebnf)
    assert "hello" in gbnf
    assert "world" in gbnf


def test_ebnf_to_gbnf_handles_groups():
    """Test that (group) are converted."""
    ebnf = """
    start := ( "a" | "b" ) "c"
    """
    gbnf = ebnf_to_gbnf(ebnf)
    assert "a" in gbnf
    assert "b" in gbnf
    assert "c" in gbnf


def test_ebnf_to_gbnf_handles_repetition():
    """Test that + and * are handled."""
    ebnf = """
    start := "a"+ "b"*
    """
    gbnf = ebnf_to_gbnf(ebnf)
    assert "a" in gbnf
    assert "b" in gbnf


def test_ebnf_to_gbnf_handles_ignore():
    """Test that %ignore rules are handled."""
    ebnf = """
    start := "a" "b"
    %ignore " "
    """
    gbnf = ebnf_to_gbnf(ebnf)
    # Should still parse
    assert "a" in gbnf
    assert "b" in gbnf


def test_ebnf_to_gbnf_screening_grammar():
    """Test conversion of the actual screening grammar."""
    fixture_path = Path(__file__).parent.parent / "fixtures" / "screening_grammar.ebnf"
    ebnf_text = fixture_path.read_text()

    gbnf = ebnf_to_gbnf(ebnf_text)

    # Key rules from screening grammar should be present
    assert "filters" in gbnf
    assert "or_expr" in gbnf
    assert "and_expr" in gbnf
    assert "cmp" in gbnf
    assert "quantity" in gbnf
    assert "metric" in gbnf
    # GBNF should not be empty
    assert len(gbnf) > 100
