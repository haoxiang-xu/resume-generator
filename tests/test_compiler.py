import json
from pathlib import Path

from resume_builder.compiler import latex_escape, latex_inline, render_latex


ROOT = Path(__file__).resolve().parent.parent


def test_latex_escape_handles_reserved_characters() -> None:
    assert latex_escape("R&D_100%") == r"R\&D\_100\%"


def test_inline_markdown_bold_is_converted() -> None:
    assert latex_inline("Built **Python & Go** APIs") == r"Built \textbf{Python \& Go} APIs"


def test_inline_urls_become_clickable() -> None:
    rendered = latex_inline("Paper: https://example.com/a_b")
    assert rendered == r"Paper: \href{https://example.com/a\_b}{https://example.com/a\_b}"


def test_example_renders_without_markdown_tokens() -> None:
    data = json.loads((ROOT / "example_resume.json").read_text(encoding="utf-8"))
    tex = render_latex(data)
    assert "Haoxiang Xu" in tex
    assert "**" not in tex
    assert r"\textbf{Tech Stack:}" in tex
    assert "built-in method" not in tex
