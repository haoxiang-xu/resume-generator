from __future__ import annotations

import hashlib
import json
import os
import re
from io import BytesIO
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from pypdf import PdfReader

from .ai_context import (
    AI_CONTEXT_MODES,
    DEFAULT_AI_CONTEXT_MODE,
    AIContextError,
    parse_profile_json,
    read_embedded_profile,
)
from .compiler import BuildError, compile_resume, render_latex
from .flexible_schema import (
    DOCUMENT_SCHEMA,
    EXAMPLE_DOCUMENT,
    LAYOUTS,
    SCHEMA_VERSION,
    ResumeSchemaError,
    document_warnings,
    parse_document,
)


SERVER_NAME = "Resume Studio"
WORKSPACE_ENV = "RESUME_MCP_WORKSPACE_ROOT"
mcp = FastMCP(SERVER_NAME)


def _workspace_root() -> Path:
    configured = os.environ.get(WORKSPACE_ENV, "").strip()
    return Path(configured or os.getcwd()).expanduser().resolve()


def _output_directory(relative_directory: str) -> Path:
    if not isinstance(relative_directory, str) or not relative_directory.strip():
        raise ResumeSchemaError("output_directory must be a non-empty relative path")
    raw = Path(relative_directory.strip())
    if raw.is_absolute():
        raise ResumeSchemaError("output_directory must be relative to the configured workspace")
    root = _workspace_root()
    destination = (root / raw).resolve()
    if not destination.is_relative_to(root):
        raise ResumeSchemaError("output_directory escapes the configured workspace")
    return destination


def _workspace_file(file_path: str) -> Path:
    if not isinstance(file_path, str) or not file_path.strip():
        raise ResumeSchemaError("pdf_path must be a non-empty path")
    root = _workspace_root()
    raw = Path(file_path.strip()).expanduser()
    candidate = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not candidate.is_relative_to(root):
        raise ResumeSchemaError("pdf_path escapes the configured workspace")
    if candidate.suffix.lower() != ".pdf":
        raise ResumeSchemaError("pdf_path must reference a .pdf file")
    if not candidate.is_file():
        raise ResumeSchemaError("pdf_path does not exist or is not a file")
    return candidate


def _filename_stem(filename: str) -> str:
    if not isinstance(filename, str) or not filename.strip():
        raise ResumeSchemaError("filename must be non-empty text")
    raw = filename.strip()
    if "/" in raw or "\\" in raw or raw in {".", ".."}:
        raise ResumeSchemaError("filename must not contain a path")
    for suffix in (".pdf", ".tex", ".json"):
        if raw.lower().endswith(suffix):
            raw = raw[: -len(suffix)]
            break
    stem = re.sub(r"[^0-9A-Za-z._-]+", "-", raw).strip("-._")[:80]
    if not stem:
        raise ResumeSchemaError("filename does not contain a usable character")
    return stem


def _unique_targets(directory: Path, stem: str) -> dict[str, Path]:
    for index in range(1, 1_001):
        suffix = "" if index == 1 else f"-{index}"
        candidate = f"{stem}{suffix}"
        targets = {
            "pdf": directory / f"{candidate}.pdf",
            "tex": directory / f"{candidate}.tex",
            "json": directory / f"{candidate}.json",
        }
        if not any(path.exists() for path in targets.values()):
            return targets
    raise ResumeSchemaError("could not allocate a collision-free output filename")


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def schema_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "section_policy": (
            "The AI chooses section titles, order, count, and content. "
            "It must choose one supported layout per section and must not emit raw LaTeX."
        ),
        "layouts": list(LAYOUTS),
        "ai_context": {
            "default_mode": DEFAULT_AI_CONTEXT_MODE,
            "modes": list(AI_CONTEXT_MODES),
            "profile_filename": "career_profile.json",
            "policy": (
                "career_profile_json is optional extended machine-readable JSON. "
                "It is embedded as an associated PDF file; hybrid also adds a short "
                "experimental ActualText discovery bridge."
            ),
        },
        "json_schema": DOCUMENT_SCHEMA,
        "example": EXAMPLE_DOCUMENT,
    }


def validate_payload(resume_json: str) -> dict[str, Any]:
    try:
        document = parse_document(resume_json)
        render_latex(document, template_name="flexible.tex.j2")
    except (ResumeSchemaError, BuildError) as exc:
        return {
            "ok": False,
            "schema_version": SCHEMA_VERSION,
            "error": str(exc),
        }
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "section_count": len(document["sections"]),
        "sections": [
            {
                "id": section["id"],
                "title": section["title"],
                "layout": section["layout"],
            }
            for section in document["sections"]
        ],
        "warnings": document_warnings(document),
        "normalized_document": document,
    }


def generate_payload(
    resume_json: str,
    filename: str = "resume",
    output_directory: str = "output/resumes",
    career_profile_json: str = "",
    ai_context_mode: str = DEFAULT_AI_CONTEXT_MODE,
) -> dict[str, Any]:
    try:
        document = parse_document(resume_json)
        if not isinstance(career_profile_json, str):
            raise AIContextError("career_profile_json must be text")
        career_profile = (
            parse_profile_json(career_profile_json) if career_profile_json.strip() else document
        )
        destination = _output_directory(output_directory)
        stem = _filename_stem(filename)
        result = compile_resume(
            document,
            template_name="flexible.tex.j2",
            career_profile=career_profile,
            ai_context_mode=ai_context_mode,
        )
        page_count = len(PdfReader(BytesIO(result.pdf)).pages)
        warnings = document_warnings(document)
        max_pages = document["render"]["max_pages"]
        if page_count > max_pages:
            warnings.append(
                f"Rendered PDF has {page_count} pages, exceeding max_pages={max_pages}. "
                "The AI should consolidate sections or shorten content and generate again."
            )

        destination.mkdir(parents=True, exist_ok=True)
        targets = _unique_targets(destination, stem)
        canonical_json = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        _atomic_write(targets["pdf"], result.pdf)
        _atomic_write(targets["tex"], result.tex.encode("utf-8"))
        _atomic_write(targets["json"], canonical_json.encode("utf-8"))
    except (ResumeSchemaError, AIContextError, BuildError, OSError, ValueError) as exc:
        return {
            "ok": False,
            "schema_version": SCHEMA_VERSION,
            "error": str(exc),
        }

    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "pdf_path": str(targets["pdf"]),
        "tex_path": str(targets["tex"]),
        "json_path": str(targets["json"]),
        "page_count": page_count,
        "section_count": len(document["sections"]),
        "warnings": warnings,
        "pdf_sha256": hashlib.sha256(result.pdf).hexdigest(),
        "ai_context": {
            "mode": result.ai_context.mode,
            "profile_filename": result.ai_context.filename,
            "profile_sha256": result.ai_context.profile_sha256,
            "profile_size": result.ai_context.profile_size,
            "actual_text_bridge": result.ai_context.actual_text_bridge,
        },
    }


def read_ai_context_payload(pdf_path: str) -> dict[str, Any]:
    try:
        source = _workspace_file(pdf_path)
        profile, manifest = read_embedded_profile(source.read_bytes())
    except (ResumeSchemaError, AIContextError, OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "pdf_path": str(source),
        "ai_context": {
            "mode": manifest.mode,
            "profile_filename": manifest.filename,
            "profile_sha256": manifest.profile_sha256,
            "profile_size": manifest.profile_size,
            "actual_text_bridge": manifest.actual_text_bridge,
        },
        "career_profile": profile,
    }


@mcp.tool()
def resume_get_schema() -> dict[str, Any]:
    """Get the versioned resume JSON schema, supported layouts, and an example.

    Call this before composing a resume. Section titles, order, and count are
    chosen by the AI; layout values remain constrained for deterministic output.
    """
    return schema_payload()


@mcp.tool()
def resume_validate(resume_json: str) -> dict[str, Any]:
    """Validate and normalize an AI-authored resume document without writing files.

    :param resume_json: JSON string that conforms to resume.document.v1.
    """
    return validate_payload(resume_json)


@mcp.tool()
def resume_generate(
    resume_json: str,
    filename: str = "resume",
    output_directory: str = "output/resumes",
    career_profile_json: str = "",
    ai_context_mode: str = DEFAULT_AI_CONTEXT_MODE,
) -> dict[str, Any]:
    """Generate PDF, LaTeX, and canonical JSON files with optional AI-only context.

    Existing files are never overwritten; a numeric suffix is added on collision.

    :param resume_json: JSON string that conforms to resume.document.v1.
    :param filename: Filename stem without a directory.
    :param output_directory: Directory relative to RESUME_MCP_WORKSPACE_ROOT.
    :param career_profile_json: Optional extended career profile JSON object. When
        omitted, the normalized resume document is embedded instead.
    :param ai_context_mode: none, embedded, or hybrid. Hybrid embeds the profile,
        writes XMP discovery metadata, and adds a short experimental ActualText bridge.
    """
    return generate_payload(
        resume_json,
        filename,
        output_directory,
        career_profile_json,
        ai_context_mode,
    )


@mcp.tool()
def resume_read_ai_context(pdf_path: str) -> dict[str, Any]:
    """Read and verify career_profile.json embedded by Resume Studio.

    Use this instead of relying on a model's generic PDF reader to discover the
    attachment or ActualText bridge.

    :param pdf_path: PDF path inside RESUME_MCP_WORKSPACE_ROOT. Absolute paths are
        accepted only when they still resolve inside that workspace.
    """
    return read_ai_context_payload(pdf_path)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
