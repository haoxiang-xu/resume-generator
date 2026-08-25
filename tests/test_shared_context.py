import json
import inspect
from pathlib import Path

from pypdf import PdfReader

import resume_builder.mcp_server as mcp_server
import resume_builder.profile_store as profile_store
from resume_builder.ai_context import SHARED_CONTEXT_FILENAME, read_ai_context_files
from resume_builder.compiler import compile_resume
from resume_builder.flexible_schema import EXAMPLE_DOCUMENT
from resume_builder.mcp_server import (
    generate_payload,
    profile_update_payload,
    read_ai_context_payload,
    shared_context_get_payload,
)


def _profile(name: str) -> dict:
    return {
        "basics": {"name": name, "location": "Vancouver, Canada"},
        "facts": [],
    }


def test_python_api_generates_watermark_from_profile() -> None:
    profile = _profile("Python Candidate")
    result = compile_resume(
        EXAMPLE_DOCUMENT,
        template_name="flexible.tex.j2",
        career_profile=profile,
    )
    extracted_profile, shared_context, _ = read_ai_context_files(result.pdf)

    assert extracted_profile == profile
    assert shared_context is not None
    watermark = shared_context["context"]["watermark"]
    assert watermark["profile_owner"] == "Python Candidate"
    assert watermark["ai_editable"] is False
    assert watermark["schema_version"] == "resume.profile-watermark.v1"
    watermark_file = shared_context["context"]["watermark_file"]
    assert watermark_file["content"]["identity"]["full_name"] == "Python Candidate"
    assert watermark_file["bindings"]["profile.full_name"]["found"] is True
    assert len(watermark_file["bindings_sha256"]) == 64
    zones = shared_context["context"]["profile_context_zones"]
    assert zones["source_attachment"] == "career_profile.json"
    assert zones["policy"]["unselected_profile_content_remains_ai_readable"] is True


def test_pdf_contains_code_managed_watermark_file(tmp_path, monkeypatch) -> None:
    source = tmp_path / "watermark.json"
    source.write_text(
        json.dumps({"verification_manual": {"steps": ["Confirm dates."]}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(profile_store, "WATERMARK_FILE_PATH", source)

    result = compile_resume(
        EXAMPLE_DOCUMENT,
        template_name="flexible.tex.j2",
        career_profile=_profile("Watermark Candidate"),
    )
    _, shared_context, _ = read_ai_context_files(result.pdf)

    assert shared_context is not None
    watermark_file = shared_context["context"]["watermark_file"]
    assert watermark_file["content"] == {
        "verification_manual": {"steps": ["Confirm dates."]}
    }
    assert len(watermark_file["sha256"]) == 64


def test_python_api_has_no_shared_context_override() -> None:
    assert "shared_context" not in inspect.signature(compile_resume).parameters


def test_generation_without_saved_profile_still_gets_application_watermark(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("RESUME_MCP_WORKSPACE_ROOT", str(tmp_path))

    generated = generate_payload(json.dumps(EXAMPLE_DOCUMENT), "watermarked", "artifacts")
    extracted = read_ai_context_payload(generated["pdf_path"])

    assert generated["ok"] is True
    assert generated["shared_context_source"] == "application_watermark"
    assert generated["shared_context_revision"] == 0
    assert generated["ai_context"]["shared_context_filename"] == SHARED_CONTEXT_FILENAME
    watermark = extracted["shared_context"]["context"]["watermark"]
    assert watermark["ai_editable"] is False
    assert watermark["profile_revision"] is None


def test_each_profile_gets_a_distinct_generated_watermark(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESUME_MCP_WORKSPACE_ROOT", str(tmp_path))
    first_profile = _profile("First Candidate")
    second_profile = _profile("Second Candidate")

    first = generate_payload(
        json.dumps(EXAMPLE_DOCUMENT),
        "first-profile",
        "artifacts",
        json.dumps(first_profile),
    )
    second = generate_payload(
        json.dumps(EXAMPLE_DOCUMENT),
        "second-profile",
        "artifacts",
        json.dumps(second_profile),
    )
    first_context = read_ai_context_payload(first["pdf_path"])["shared_context"]["context"]
    second_context = read_ai_context_payload(second["pdf_path"])["shared_context"]["context"]

    assert first_context["watermark"]["profile_owner"] == "First Candidate"
    assert second_context["watermark"]["profile_owner"] == "Second Candidate"
    assert first_context["watermark"]["watermark_id"] != second_context["watermark"][
        "watermark_id"
    ]


def test_mcp_exposes_watermark_read_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESUME_MCP_WORKSPACE_ROOT", str(tmp_path))
    committed = profile_update_payload(json.dumps(_profile("Stored Candidate")), 0, True)

    result = shared_context_get_payload()

    assert committed["ok"] is True
    assert result["found"] is True
    assert result["profile_invisible_context"]["watermark"]["ai_editable"] is False
    assert not hasattr(mcp_server, "resume_shared_context_update")
    assert not hasattr(mcp_server, "shared_context_update_payload")


def test_none_mode_omits_shared_context_and_profile(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESUME_MCP_WORKSPACE_ROOT", str(tmp_path))
    generated = generate_payload(
        json.dumps(EXAMPLE_DOCUMENT),
        "no-hidden-context",
        "artifacts",
        ai_context_mode="none",
    )
    reader = PdfReader(Path(generated["pdf_path"]))

    assert generated["ok"] is True
    assert generated["shared_context_source"] == "disabled"
    assert generated["shared_context_revision"] is None
    assert generated["ai_context"]["profile_filename"] is None
    assert generated["ai_context"]["shared_context_filename"] is None
    assert reader.attachments == {}
