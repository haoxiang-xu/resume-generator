import copy
import hashlib
import shutil
import subprocess
from io import BytesIO

import pytest
from pypdf import PdfReader

from resume_builder.ai_context import (
    AIContextError,
    PROFILE_FILENAME,
    SHARED_CONTEXT_FILENAME,
    add_ai_context,
    canonical_profile_bytes,
    parse_profile_json,
    read_embedded_profile,
)
from resume_builder.compiler import compile_resume
from resume_builder.flexible_schema import EXAMPLE_DOCUMENT
from resume_builder.mcp_server import read_ai_context_payload


@pytest.fixture(scope="module")
def base_pdf() -> bytes:
    result = compile_resume(
        EXAMPLE_DOCUMENT,
        template_name="flexible.tex.j2",
        ai_context_mode="none",
    )
    return result.pdf


def test_hybrid_profile_round_trip(base_pdf: bytes) -> None:
    profile = {
        "schema_version": "resume.ai-profile.v1",
        "additional_projects": [
            {"name": "Internal Platform", "detail": "Extended context not shown on one-page resume."}
        ],
    }
    pdf, manifest = add_ai_context(base_pdf, profile, "hybrid")
    extracted, extracted_manifest = read_embedded_profile(pdf)
    reader = PdfReader(BytesIO(pdf))
    metadata_packet = reader.trailer["/Root"]["/Metadata"].get_object().get_data()

    assert pdf.startswith(b"%PDF-1.7")
    assert extracted == profile
    assert manifest == extracted_manifest
    assert manifest.filename == PROFILE_FILENAME
    assert manifest.actual_text_bridge is True
    assert manifest.profile_sha256 == hashlib.sha256(canonical_profile_bytes(profile)).hexdigest()
    assert b"profileFilename" in metadata_packet
    assert b"sharedContextFilename" in metadata_packet
    assert b"/ActualText" in reader.pages[0].get_contents().get_data()
    assert SHARED_CONTEXT_FILENAME in reader.attachments
    assert manifest.shared_context_filename == SHARED_CONTEXT_FILENAME


def test_embedded_mode_has_no_actual_text(base_pdf: bytes) -> None:
    pdf, manifest = add_ai_context(base_pdf, {"extra": "context"}, "embedded")
    reader = PdfReader(BytesIO(pdf))

    assert manifest.actual_text_bridge is False
    assert b"/ActualText" not in reader.pages[0].get_contents().get_data()
    assert PROFILE_FILENAME in reader.attachments
    assert SHARED_CONTEXT_FILENAME in reader.attachments


def test_none_mode_preserves_pdf_bytes(base_pdf: bytes) -> None:
    pdf, manifest = add_ai_context(base_pdf, {"extra": "context"}, "none")
    assert pdf == base_pdf
    assert manifest.mode == "none"
    assert manifest.filename is None


def test_profile_parser_rejects_non_object() -> None:
    with pytest.raises(AIContextError, match="JSON object"):
        parse_profile_json('["not", "an", "object"]')


def test_reader_wraps_malformed_pdf_errors() -> None:
    with pytest.raises(AIContextError, match="could not read embedded career profile"):
        read_embedded_profile(b"not a PDF")


def test_mcp_reader_obeys_workspace_boundary(base_pdf: bytes, tmp_path, monkeypatch) -> None:
    profile = copy.deepcopy(EXAMPLE_DOCUMENT)
    pdf, _ = add_ai_context(base_pdf, profile, "hybrid")
    source = tmp_path / "resume.pdf"
    source.write_bytes(pdf)
    monkeypatch.setenv("RESUME_MCP_WORKSPACE_ROOT", str(tmp_path))

    result = read_ai_context_payload("resume.pdf")

    assert result["ok"] is True
    assert result["career_profile"] == profile
    assert result["ai_context"]["actual_text_bridge"] is True


def test_poppler_extracts_actual_text_bridge(base_pdf: bytes, tmp_path) -> None:
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        pytest.skip("pdftotext is not installed")
    pdf, _ = add_ai_context(base_pdf, {"extra": "context"}, "hybrid")
    source = tmp_path / "resume.pdf"
    source.write_bytes(pdf)

    completed = subprocess.run(
        [pdftotext, str(source), "-"],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )

    assert "AI-CONTEXT: This PDF contains the associated file career_profile.json" in completed.stdout
