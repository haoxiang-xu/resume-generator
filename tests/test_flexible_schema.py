import copy
import json

import pytest

from resume_builder.flexible_schema import (
    EXAMPLE_DOCUMENT,
    ResumeSchemaError,
    validate_document,
)
from resume_builder.mcp_server import (
    _output_directory,
    generate_payload,
    validate_payload,
)


def test_flexible_example_validates() -> None:
    normalized = validate_document(EXAMPLE_DOCUMENT)
    assert [section["title"] for section in normalized["sections"]] == [
        "Selected Impact",
        "Technical Foundation",
    ]


def test_unknown_top_level_field_fails_closed() -> None:
    document = copy.deepcopy(EXAMPLE_DOCUMENT)
    document["raw_latex"] = r"\input{/etc/passwd}"
    with pytest.raises(ResumeSchemaError, match="unknown keys: raw_latex"):
        validate_document(document)


def test_layout_rejects_mismatched_content_field() -> None:
    document = copy.deepcopy(EXAMPLE_DOCUMENT)
    section = document["sections"][0]
    section["layout"] = "bullet_list"
    with pytest.raises(ResumeSchemaError, match="requires only the 'bullets'"):
        validate_document(document)


def test_output_directory_cannot_escape_workspace(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESUME_MCP_WORKSPACE_ROOT", str(tmp_path))
    with pytest.raises(ResumeSchemaError, match="escapes the configured workspace"):
        _output_directory("../outside")


def test_generate_is_collision_safe(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESUME_MCP_WORKSPACE_ROOT", str(tmp_path))
    resume_json = json.dumps(EXAMPLE_DOCUMENT)

    first = generate_payload(resume_json, "ai-resume", "artifacts")
    second = generate_payload(resume_json, "ai-resume", "artifacts")

    assert first["ok"] is True
    assert second["ok"] is True
    assert first["pdf_path"].endswith("ai-resume.pdf")
    assert second["pdf_path"].endswith("ai-resume-2.pdf")
    assert len(first["pdf_sha256"]) == 64
    assert len(second["pdf_sha256"]) == 64
    assert first["page_count"] == 1


def test_validate_does_not_write_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESUME_MCP_WORKSPACE_ROOT", str(tmp_path))
    result = validate_payload(json.dumps(EXAMPLE_DOCUMENT))
    assert result["ok"] is True
    assert list(tmp_path.iterdir()) == []
