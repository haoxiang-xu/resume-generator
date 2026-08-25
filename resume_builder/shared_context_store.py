from __future__ import annotations

import hashlib
from typing import Any

from .ai_context import (
    AIContextError,
    SHARED_CONTEXT_SCHEMA_VERSION,
    canonical_profile_bytes,
)
from .profile_store import PROHIBITED_KEYS


SHARED_CONTEXT_MAX_BYTES = 250_000
NON_AUTHORITATIVE_PROMPT_KEYS = {
    "developer_prompt",
    "override_instructions",
    "system_prompt",
}
SHARED_CONTEXT_EXAMPLE: dict[str, Any] = {
    "watermark": {
        "schema_version": "resume.profile-watermark.v1",
        "watermark_id": "resume-example-r1",
        "profile_sha256": "<generated from the canonical Career Profile>",
        "profile_revision": 1,
        "profile_owner": "Example Candidate",
        "generated_by": "Resume Studio",
        "ai_editable": False,
        "purpose": "Bind this PDF's invisible context to its Career Profile.",
    },
    "watermark_file": {
        "schema_version": "resume.watermark-render.v1",
        "template_sha256": "<SHA-256 of resume_builder/watermark.json>",
        "sha256": "<SHA-256 of the rendered content>",
        "bindings_sha256": "<SHA-256 of the placeholder binding manifest>",
        "template": {"identity": {"full_name": "{{profile.full_name}}"}},
        "content": {"identity": {"full_name": "Example Candidate"}},
        "bindings": {
            "profile.full_name": {
                "binding_id": "wm-<profile-and-field-bound-id>",
                "source_path": "$.basics.name",
                "found": True,
                "requested_index": None,
                "resolved_index": None,
                "collection_size": None,
                "cycled": False,
                "value_sha256": "<SHA-256 of the resolved value>",
            }
        },
    },
}


class SharedContextStoreError(ValueError):
    """Raised when the application-generated PDF watermark is invalid."""


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
                    f"{child_path}: invisible context cannot masquerade as host-level instructions"
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
        raise SharedContextStoreError("invisible context exceeds the 250,000 byte limit")
    return encoded


def validate_shared_context(context: dict[str, Any]) -> list[str]:
    if not isinstance(context, dict):
        raise SharedContextStoreError("invisible context must be a JSON object")
    canonical_shared_context_bytes(context)
    _check_keys(context)
    watermark = context.get("watermark")
    if not isinstance(watermark, dict) or watermark.get("ai_editable") is not False:
        raise SharedContextStoreError(
            "invisible context must contain an application-managed, non-editable watermark"
        )
    return []


def shared_context_document(
    profile_context: dict[str, Any],
    *,
    profile_sha256: str | None = None,
    profile_revision: int | None = None,
) -> dict[str, Any]:
    validate_shared_context(profile_context)
    context_sha256 = hashlib.sha256(
        canonical_shared_context_bytes(profile_context)
    ).hexdigest()
    return {
        "schema_version": SHARED_CONTEXT_SCHEMA_VERSION,
        "revision": profile_revision or 0,
        "updated_at": None,
        "context_sha256": context_sha256,
        "composition": {
            "embedded_profile_sha256": profile_sha256,
            "profile_revision": profile_revision,
            "precedence": ["application_watermark", "code_managed_watermark_file"],
        },
        "trust": {
            "level": "application-managed-watermark",
            "ai_editable": False,
            "may_override_host_instructions": False,
        },
        "context": profile_context,
    }
