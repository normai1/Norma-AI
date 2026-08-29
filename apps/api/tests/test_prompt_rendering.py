import pytest

from app.core.exceptions import PromptRenderError
from app.services.prompt_rendering import render_prompt


def test_renders_a_single_placeholder() -> None:
    result = render_prompt(
        "Hello, {{workspace.name}}!",
        {"workspace": {"name": "Acme Dental"}},
    )

    assert result == "Hello, Acme Dental!"


def test_renders_multiple_placeholders_across_namespaces() -> None:
    result = render_prompt(
        "Thanks for calling {{workspace.name}}, this is {{assistant.name}}.",
        {
            "workspace": {"name": "Acme Dental"},
            "assistant": {"name": "Riley"},
        },
    )

    assert result == "Thanks for calling Acme Dental, this is Riley."


def test_repeated_placeholder_is_substituted_every_time() -> None:
    result = render_prompt(
        "{{assistant.name}}, {{assistant.name}}, {{assistant.name}}!",
        {"assistant": {"name": "Riley"}},
    )

    assert result == "Riley, Riley, Riley!"


def test_none_value_renders_as_empty_string() -> None:
    result = render_prompt(
        "Hi{{caller.name}}, how can I help?",
        {"caller": {"name": None}},
    )

    assert result == "Hi, how can I help?"


def test_text_with_no_placeholders_is_returned_unchanged() -> None:
    result = render_prompt("Thanks for calling!", {})

    assert result == "Thanks for calling!"


def test_a_malformed_single_brace_token_is_left_untouched() -> None:
    result = render_prompt("Use {this} literally.", {})

    assert result == "Use {this} literally."


def test_unknown_namespace_raises() -> None:
    with pytest.raises(PromptRenderError):
        render_prompt("{{caller.name}}", {"workspace": {"name": "Acme"}})


def test_unknown_field_in_a_known_namespace_raises() -> None:
    with pytest.raises(PromptRenderError):
        render_prompt("{{workspace.slogan}}", {"workspace": {"name": "Acme"}})
