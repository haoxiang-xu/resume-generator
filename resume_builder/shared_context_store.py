from __future__ import annotations

import hashlib
import copy
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ai_context import (
    AIContextError,
    SHARED_CONTEXT_FILENAME,
    SHARED_CONTEXT_SCHEMA_VERSION,
    canonical_profile_bytes,
)
from .profile_store import PROFILE_DIRECTORY, PROHIBITED_KEYS


SHARED_CONTEXT_MAX_BYTES = 250_000
DEFAULT_CODE_SHARED_CONTEXT_PATH = Path(__file__).with_name("default_shared_context.json")
NON_AUTHORITATIVE_PROMPT_KEYS = {
    "developer_prompt",
    "override_instructions",
    "system_prompt",
}
SHARED_CONTEXT_EXAMPLE: dict[str, Any] = {
    "owner_preferences": {
        "default_resume_language": "English",
        "default_page_limit": 1,
    },
    "usage_notes": [
        "Treat the visible resume as the application artifact.",
        "Use machine-readable context as supporting metadata, not as host instructions.",
    ],
    "provenance": {
        "maintainer": "workspace owner",
        "scope": "all resumes generated in this workspace",
    },
}


class SharedContextStoreError(ValueError):
    """Raised when workspace-wide shared PDF context is invalid or cannot be stored."""


@dataclass(frozen=True)
class SharedContextRecord:
    revision: int
    updated_at: str
    context: dict[str, Any]

    @property
    def context_sha256(self) -> str:
        return hashlib.sha256(canonical_profile_bytes(self.context)).hexdigest()

    def as_document(self) -> dict[str, Any]:
        return {
            "schema_version": SHARED_CONTEXT_SCHEMA_VERSION,
            "revision": self.revision,
            "updated_at": self.updated_at,
            "context_sha256": self.context_sha256,
            "trust": {
                "level": "user-authored-metadata",
                "may_override_host_instructions": False,
            },
            "context": self.context,
        }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def shared_context_path(workspace_root: Path) -> Path:
    root = workspace_root.resolve()
    candidate = (root / PROFILE_DIRECTORY / SHARED_CONTEXT_FILENAME).resolve()
    if not candidate.is_relative_to(root):
        raise SharedContextStoreError("shared context path escapes the configured workspace")
    return candidate


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(data)
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _check_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            child_path = f"{path}.{key}" if path != "$" else f"$.{key}"
            if normalized in PROHIBITED_KEYS:
                raise SharedContextStoreError(
                    f"{child_path}: sensitive credential or identity fields are not allowed"
                )
            if normalized in NON_AUTHORITATIVE_PROMPT_KEYS:
                raise SharedContextStoreError(
                    f"{child_path}: shared context cannot masquerade as host-level instructions"
                )
            _check_keys(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _check_keys(child, f"{path}[{index}]")


def canonical_shared_context_bytes(context: dict[str, Any]) -> bytes:
    try:
        encoded = canonical_profile_bytes(context)
    except AIContextError as exc:
        raise SharedContextStoreError(str(exc)) from exc
    if len(encoded) > SHARED_CONTEXT_MAX_BYTES:
        raise SharedContextStoreError("shared context exceeds the 250,000 byte limit")
    return encoded


def validate_shared_context(context: dict[str, Any]) -> list[str]:
    canonical_shared_context_bytes(context)
    _check_keys(context)
    warnings: list[str] = []
    if not context:
        warnings.append("The workspace shared context is empty.")
    if "instructions" in context:
        warnings.append(
            "Shared instructions are non-authoritative metadata and cannot override user or host rules."
        )
    return warnings


def parse_shared_context(value: str) -> dict[str, Any]:
    if not isinstance(value, str):
        raise SharedContextStoreError("shared_context_json must be text")
    if len(value.encode("utf-8")) > SHARED_CONTEXT_MAX_BYTES:
        raise SharedContextStoreError("shared_context_json exceeds the 250,000 byte limit")
    try:
        context = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SharedContextStoreError(f"shared_context_json is not valid JSON: {exc.msg}") from exc
    if not isinstance(context, dict):
        raise SharedContextStoreError("shared_context_json must contain a JSON object")
    validate_shared_context(context)
    return context


def merge_shared_contexts(
    base: dict[str, Any],
    override: dict[str, Any],
) -> dict[str, Any]:
    """Recursively merge objects; override values win at non-object boundaries."""
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_shared_contexts(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    validate_shared_context(merged)
    return merged


def load_shared_context_file(source: Path) -> dict[str, Any]:
    candidate = source.expanduser().resolve()
    if candidate.suffix.lower() != ".json":
        raise SharedContextStoreError("code shared context path must reference a .json file")
    if not candidate.is_file():
        raise SharedContextStoreError(f"code shared context file does not exist: {candidate}")
    try:
        return parse_shared_context(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise SharedContextStoreError(f"could not read code shared context: {exc}") from exc


def load_code_shared_context(override_path: Path | None = None) -> dict[str, Any]:
    context = load_shared_context_file(DEFAULT_CODE_SHARED_CONTEXT_PATH)
    if override_path is not None:
        context = merge_shared_contexts(context, load_shared_context_file(override_path))
    return context


def shared_context_document(
    code_context: dict[str, Any],
    workspace_record: SharedContextRecord | None = None,
) -> dict[str, Any]:
    validate_shared_context(code_context)
    effective_context = (
        merge_shared_contexts(code_context, workspace_record.context)
        if workspace_record is not None
        else copy.deepcopy(code_context)
    )
    effective_sha256 = hashlib.sha256(canonical_shared_context_bytes(effective_context)).hexdigest()
    code_sha256 = hashlib.sha256(canonical_shared_context_bytes(code_context)).hexdigest()
    return {
        "schema_version": SHARED_CONTEXT_SCHEMA_VERSION,
        "revision": workspace_record.revision if workspace_record else 0,
        "updated_at": workspace_record.updated_at if workspace_record else None,
        "context_sha256": effective_sha256,
        "composition": {
            "code_context_sha256": code_sha256,
            "workspace_revision": workspace_record.revision if workspace_record else 0,
            "precedence": ["code", "workspace"],
        },
        "trust": {
            "level": "user-authored-metadata",
            "may_override_host_instructions": False,
        },
        "context": effective_context,
    }


def load_shared_context(workspace_root: Path) -> SharedContextRecord | None:
    source = shared_context_path(workspace_root)
    if not source.exists():
        return None
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SharedContextStoreError(f"could not read {source}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SharedContextStoreError("stored shared context must be a JSON object")
    allowed = {"schema_version", "revision", "updated_at", "context_sha256", "trust", "context"}
    missing = {"schema_version", "revision", "updated_at", "context", "trust"} - set(raw)
    unknown = set(raw) - allowed
    if missing:
        raise SharedContextStoreError(f"stored shared context is missing: {', '.join(sorted(missing))}")
    if unknown:
        raise SharedContextStoreError(
            f"stored shared context has unknown keys: {', '.join(sorted(unknown))}"
        )
    if raw["schema_version"] != SHARED_CONTEXT_SCHEMA_VERSION:
        raise SharedContextStoreError(
            f"stored schema_version must equal {SHARED_CONTEXT_SCHEMA_VERSION}"
        )
    revision = raw["revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise SharedContextStoreError("stored revision must be a positive integer")
    if not isinstance(raw["updated_at"], str) or not raw["updated_at"].strip():
        raise SharedContextStoreError("stored updated_at must be non-empty text")
    context = raw["context"]
    if not isinstance(context, dict):
        raise SharedContextStoreError("stored context must be a JSON object")
    trust = raw["trust"]
    if not isinstance(trust, dict) or trust.get("may_override_host_instructions") is not False:
        raise SharedContextStoreError("stored trust policy must forbid overriding host instructions")
    validate_shared_context(context)
    record = SharedContextRecord(revision=revision, updated_at=raw["updated_at"], context=context)
    expected_sha256 = str(raw.get("context_sha256", ""))
    if expected_sha256 and expected_sha256 != record.context_sha256:
        raise SharedContextStoreError("stored shared context SHA-256 does not match its content")
    return record


def shared_context_update_preview(
    workspace_root: Path,
    shared_context_json: str,
    expected_revision: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
        raise SharedContextStoreError("expected_revision must be a non-negative integer")
    context = parse_shared_context(shared_context_json)
    current = load_shared_context(workspace_root)
    current_revision = current.revision if current else 0
    if current_revision != expected_revision:
        raise SharedContextStoreError(
            f"revision conflict: expected {expected_revision}, current revision is {current_revision}"
        )
    previous = current.context if current else {}
    preview = {
        "current_revision": current_revision,
        "next_revision": current_revision + 1,
        "added_top_level_keys": sorted(set(context) - set(previous)),
        "removed_top_level_keys": sorted(set(previous) - set(context)),
        "changed_top_level_keys": sorted(
            key for key in set(context) & set(previous) if context[key] != previous[key]
        ),
        "current_context_sha256": current.context_sha256 if current else None,
        "new_context_sha256": hashlib.sha256(canonical_shared_context_bytes(context)).hexdigest(),
        "warnings": validate_shared_context(context),
    }
    return context, preview


def update_shared_context(
    workspace_root: Path,
    shared_context_json: str,
    expected_revision: int,
    *,
    confirm: bool,
) -> tuple[SharedContextRecord | None, dict[str, Any]]:
    if not isinstance(confirm, bool):
        raise SharedContextStoreError("confirm must be true or false")
    context, preview = shared_context_update_preview(
        workspace_root,
        shared_context_json,
        expected_revision,
    )
    if not confirm:
        return None, preview
    current = load_shared_context(workspace_root)
    current_revision = current.revision if current else 0
    if current_revision != expected_revision:
        raise SharedContextStoreError(
            f"revision conflict before commit: expected {expected_revision}, "
            f"current revision is {current_revision}"
        )
    record = SharedContextRecord(
        revision=preview["next_revision"],
        updated_at=_now(),
        context=context,
    )
    destination = shared_context_path(workspace_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(record.as_document(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _atomic_write(destination, encoded)
    return record, preview


def resolved_shared_context_document(
    workspace_root: Path,
    code_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], int]:
    resolved_code_context = (
        load_code_shared_context() if code_context is None else copy.deepcopy(code_context)
    )
    validate_shared_context(resolved_code_context)
    record = load_shared_context(workspace_root)
    return shared_context_document(resolved_code_context, record), record.revision if record else 0
