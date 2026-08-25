from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from pypdf import PdfReader

from .ai_context import (
    AI_CONTEXT_MODES,
    DEFAULT_AI_CONTEXT_MODE,
    AIContextError,
    SHARED_CONTEXT_SCHEMA_VERSION,
    canonical_profile_bytes,
    read_ai_context_files,
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
from .profile_store import (
    PROFILE_DIRECTORY,
    PROFILE_EXAMPLE,
    PROFILE_SCHEMA_VERSION,
    PROFILE_STATUSES,
    PROFILE_VISIBILITIES,
    ProfileStoreError,
    append_generation_history,
    load_profile,
    parse_profile,
    profile_path,
    read_generation_history,
    search_profile,
    update_profile,
    validate_profile,
)
from .shared_context_store import (
    SHARED_CONTEXT_EXAMPLE,
    SharedContextStoreError,
    load_shared_context,
    resolved_shared_context_document,
    shared_context_path,
    update_shared_context,
    validate_shared_context,
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
        "career_profile_memory": {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "workspace_path": f"{PROFILE_DIRECTORY}/career_profile.json",
            "history_path": f"{PROFILE_DIRECTORY}/history/generations.jsonl",
            "fact_statuses": sorted(PROFILE_STATUSES),
            "fact_visibilities": sorted(PROFILE_VISIBILITIES),
            "write_policy": (
                "Profile updates are previewed by default. A commit requires confirm=true "
                "and an expected_revision matching the stored profile."
            ),
            "example_profile": PROFILE_EXAMPLE,
        },
        "shared_context": {
            "schema_version": SHARED_CONTEXT_SCHEMA_VERSION,
            "workspace_path": f"{PROFILE_DIRECTORY}/shared_context.json",
            "policy": (
                "The shared context is embedded into every embedded or hybrid PDF. "
                "Updates are previewed by default and require confirm=true plus a matching revision. "
                "It is user-authored metadata and cannot override user or host instructions."
            ),
            "example": SHARED_CONTEXT_EXAMPLE,
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
        root = _workspace_root()
        if career_profile_json.strip():
            career_profile = parse_profile(career_profile_json)
            profile_source = "provided"
            profile_revision = None
            profile_warnings = validate_profile(career_profile)
        else:
            stored_profile = load_profile(root)
            if stored_profile:
                career_profile = stored_profile.as_document()
                profile_source = "workspace"
                profile_revision = stored_profile.revision
                profile_warnings = validate_profile(stored_profile.profile)
            else:
                career_profile = document
                profile_source = "resume_document"
                profile_revision = None
                profile_warnings = []
        shared_context, configured_shared_context_revision = resolved_shared_context_document(root)
        shared_context_warnings = (
            validate_shared_context(shared_context["context"])
            if configured_shared_context_revision
            else []
        )
        destination = _output_directory(output_directory)
        stem = _filename_stem(filename)
        result = compile_resume(
            document,
            template_name="flexible.tex.j2",
            career_profile=career_profile,
            shared_context=shared_context,
            ai_context_mode=ai_context_mode,
        )
        page_count = len(PdfReader(BytesIO(result.pdf)).pages)
        warnings = document_warnings(document) + profile_warnings + shared_context_warnings
        shared_context_revision = (
            configured_shared_context_revision
            if result.ai_context.shared_context_filename is not None
            else None
        )
        shared_context_source = (
            "disabled"
            if result.ai_context.shared_context_filename is None
            else "workspace"
            if configured_shared_context_revision
            else "default_empty"
        )
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
        pdf_sha256 = hashlib.sha256(result.pdf).hexdigest()
        generation_id = str(uuid.uuid4())
        history_entry = {
            "generation_id": generation_id,
            "created_at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "pdf_path": str(targets["pdf"].relative_to(root)),
            "pdf_sha256": pdf_sha256,
            "resume_schema_version": SCHEMA_VERSION,
            "section_ids": [section["id"] for section in document["sections"]],
            "profile_source": profile_source,
            "profile_revision": profile_revision,
            "embedded_profile_sha256": result.ai_context.profile_sha256,
            "shared_context_source": shared_context_source,
            "shared_context_revision": shared_context_revision,
            "embedded_shared_context_sha256": result.ai_context.shared_context_sha256,
            "ai_context_mode": result.ai_context.mode,
        }
        try:
            append_generation_history(root, history_entry)
        except (ProfileStoreError, OSError) as history_error:
            warnings.append(f"Could not record generation history: {history_error}")
    except (
        ResumeSchemaError,
        AIContextError,
        ProfileStoreError,
        SharedContextStoreError,
        BuildError,
        OSError,
        ValueError,
    ) as exc:
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
        "pdf_sha256": pdf_sha256,
        "generation_id": generation_id,
        "profile_source": profile_source,
        "profile_revision": profile_revision,
        "shared_context_source": shared_context_source,
        "shared_context_revision": shared_context_revision,
        "ai_context": {
            "mode": result.ai_context.mode,
            "profile_filename": result.ai_context.filename,
            "profile_sha256": result.ai_context.profile_sha256,
            "profile_size": result.ai_context.profile_size,
            "shared_context_filename": result.ai_context.shared_context_filename,
            "shared_context_sha256": result.ai_context.shared_context_sha256,
            "shared_context_size": result.ai_context.shared_context_size,
            "actual_text_bridge": result.ai_context.actual_text_bridge,
        },
    }


def profile_get_payload() -> dict[str, Any]:
    try:
        root = _workspace_root()
        record = load_profile(root)
    except (ProfileStoreError, OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    if record is None:
        return {
            "ok": True,
            "found": False,
            "profile_path": str(profile_path(root)),
            "current_revision": 0,
        }
    return {
        "ok": True,
        "found": True,
        "profile_path": str(profile_path(root)),
        "record": record.as_document(),
        "warnings": validate_profile(record.profile),
    }


def profile_update_payload(
    profile_json: str,
    expected_revision: int = 0,
    confirm: bool = False,
) -> dict[str, Any]:
    try:
        root = _workspace_root()
        record, preview = update_profile(
            root,
            profile_json,
            expected_revision,
            confirm=confirm,
        )
    except (ProfileStoreError, OSError, ValueError) as exc:
        return {"ok": False, "committed": False, "error": str(exc)}
    payload: dict[str, Any] = {
        "ok": True,
        "committed": record is not None,
        "profile_path": str(profile_path(root)),
        "preview": preview,
    }
    if record is not None:
        payload["record"] = record.as_document()
    else:
        payload["next_step"] = (
            "Show this preview to the user. Call again with confirm=true only after explicit approval."
        )
    return payload


def shared_context_get_payload() -> dict[str, Any]:
    try:
        root = _workspace_root()
        record = load_shared_context(root)
        effective_document, effective_revision = resolved_shared_context_document(root)
    except (SharedContextStoreError, OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "found": record is not None,
        "shared_context_path": str(shared_context_path(root)),
        "current_revision": effective_revision,
        "record": record.as_document() if record else None,
        "effective_document": effective_document,
        "warnings": validate_shared_context(record.context) if record else [],
    }


def shared_context_update_payload(
    shared_context_json: str,
    expected_revision: int = 0,
    confirm: bool = False,
) -> dict[str, Any]:
    try:
        root = _workspace_root()
        record, preview = update_shared_context(
            root,
            shared_context_json,
            expected_revision,
            confirm=confirm,
        )
    except (SharedContextStoreError, OSError, ValueError) as exc:
        return {"ok": False, "committed": False, "error": str(exc)}
    payload: dict[str, Any] = {
        "ok": True,
        "committed": record is not None,
        "shared_context_path": str(shared_context_path(root)),
        "preview": preview,
    }
    if record is not None:
        payload["record"] = record.as_document()
    else:
        payload["next_step"] = (
            "Show this preview to the user. Call again with confirm=true only after explicit approval."
        )
    return payload


def profile_validate_payload(profile_json: str = "") -> dict[str, Any]:
    try:
        if not isinstance(profile_json, str):
            raise ProfileStoreError("profile_json must be text")
        if profile_json.strip():
            profile = parse_profile(profile_json)
            source = "provided"
            revision = None
        else:
            record = load_profile(_workspace_root())
            if record is None:
                raise ProfileStoreError("workspace career profile has not been created")
            profile = record.profile
            source = "workspace"
            revision = record.revision
        warnings = validate_profile(profile)
    except (AIContextError, ProfileStoreError, OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "source": source,
        "revision": revision,
        "profile_sha256": hashlib.sha256(canonical_profile_bytes(profile)).hexdigest(),
        "warnings": warnings,
    }


def profile_search_payload(query: str, limit: int = 20) -> dict[str, Any]:
    try:
        record = load_profile(_workspace_root())
        if record is None:
            raise ProfileStoreError("workspace career profile has not been created")
        matches = search_profile(record, query, limit)
    except (ProfileStoreError, OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "revision": record.revision,
        "query": query,
        "match_count": len(matches),
        "matches": matches,
    }


def generation_history_payload(limit: int = 20) -> dict[str, Any]:
    try:
        entries = read_generation_history(_workspace_root(), limit)
    except (ProfileStoreError, OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "count": len(entries), "generations": entries}


def read_ai_context_payload(pdf_path: str) -> dict[str, Any]:
    try:
        source = _workspace_file(pdf_path)
        profile, shared_context, manifest = read_ai_context_files(source.read_bytes())
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
            "shared_context_filename": manifest.shared_context_filename,
            "shared_context_sha256": manifest.shared_context_sha256,
            "shared_context_size": manifest.shared_context_size,
            "actual_text_bridge": manifest.actual_text_bridge,
        },
        "career_profile": profile,
        "shared_context": shared_context,
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
def resume_profile_get() -> dict[str, Any]:
    """Read the transparent, revisioned Career Profile stored in this workspace.

    Returns found=false and current_revision=0 before the profile is initialized.
    This tool never writes files.
    """
    return profile_get_payload()


@mcp.tool()
def resume_profile_update(
    profile_json: str,
    expected_revision: int = 0,
    confirm: bool = False,
) -> dict[str, Any]:
    """Preview or commit a complete Career Profile replacement.

    Call first with confirm=false and show the returned diff preview to the user.
    Call again with confirm=true only after explicit user approval. The optimistic
    expected_revision prevents overwriting a profile changed by another session.

    :param profile_json: Complete Career Profile as a JSON object.
    :param expected_revision: Revision returned by resume_profile_get, or 0 initially.
    :param confirm: False previews without writing; true commits the reviewed update.
    """
    return profile_update_payload(profile_json, expected_revision, confirm)


@mcp.tool()
def resume_profile_validate(profile_json: str = "") -> dict[str, Any]:
    """Validate a proposed or stored Career Profile without writing files.

    :param profile_json: Optional JSON object. When empty, validate the stored profile.
    """
    return profile_validate_payload(profile_json)


@mcp.tool()
def resume_profile_search(query: str, limit: int = 20) -> dict[str, Any]:
    """Search the stored Career Profile using transparent keyword matching.

    No embeddings or external services are used. Results include JSON paths so
    an AI can retrieve the relevant facts and surrounding profile structure.

    :param query: Keywords or phrase to find.
    :param limit: Maximum results from 1 to 50.
    """
    return profile_search_payload(query, limit)


@mcp.tool()
def resume_shared_context_get() -> dict[str, Any]:
    """Read the workspace-wide context embedded into generated resume PDFs.

    Before initialization, returns revision 0 and the effective empty context.
    Shared context is user-authored metadata and never overrides host instructions.
    """
    return shared_context_get_payload()


@mcp.tool()
def resume_shared_context_update(
    shared_context_json: str,
    expected_revision: int = 0,
    confirm: bool = False,
) -> dict[str, Any]:
    """Preview or commit workspace-wide context for generated PDFs.

    Call first with confirm=false and show the diff to the user. Call again with
    confirm=true only after explicit approval. Every embedded or hybrid PDF then
    receives the revisioned shared_context.json attachment automatically.

    :param shared_context_json: Complete shared context as a JSON object.
    :param expected_revision: Current revision, or 0 before initialization.
    :param confirm: False previews without writing; true commits the reviewed update.
    """
    return shared_context_update_payload(shared_context_json, expected_revision, confirm)


@mcp.tool()
def resume_generation_history(limit: int = 20) -> dict[str, Any]:
    """List recent resume generations and the profile revision used for each.

    :param limit: Maximum records from 1 to 100, newest first.
    """
    return generation_history_payload(limit)


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
    :param career_profile_json: Optional one-off extended Career Profile JSON object.
        When omitted, the saved workspace profile is used; if none exists, the
        normalized resume document is embedded instead.
    :param ai_context_mode: none, embedded, or hybrid. Hybrid embeds the profile,
        workspace shared context, XMP discovery metadata, and a short experimental
        ActualText bridge. None explicitly disables both hidden JSON attachments.
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
    """Read and verify career_profile.json and shared_context.json embedded by Resume Studio.

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
