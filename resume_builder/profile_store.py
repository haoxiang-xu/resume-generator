from __future__ import annotations

import hashlib
import copy
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .ai_context import AIContextError, canonical_profile_bytes, parse_profile_json
from .watermark_template import (
    WATERMARK_RENDER_SCHEMA_VERSION,
    WatermarkTemplateError,
    render_watermark_template,
)


PROFILE_SCHEMA_VERSION = "resume.career-profile.v3"
LEGACY_PROFILE_SCHEMA_VERSIONS = {
    "resume.career-profile.v1",
    "resume.career-profile.v2",
}
WATERMARK_SCHEMA_VERSION = "resume.profile-watermark.v1"
WATERMARK_FILE_PATH = Path(__file__).with_name("watermark.json")
PROFILE_DIRECTORY = ".resume"
PROFILE_FILENAME = "career_profile.json"
INVISIBLE_CONTEXT_KEY = "invisible_context"
INVISIBLE_CONTEXT_MAX_BYTES = 250_000
HISTORY_FILENAME = "generations.jsonl"
PROFILE_STATUSES = {"verified", "draft", "archived"}
PROFILE_VISIBILITIES = {"public", "ai_only", "private"}
PROFILE_EXAMPLE: dict[str, Any] = {
    "basics": {
        "name": "Example Candidate",
        "location": "Vancouver, Canada",
    },
    "facts": [
        {
            "id": "example-impact",
            "category": "experience",
            "content": "Improved a production workflow with a measurable result.",
            "status": "verified",
            "visibility": "public",
            "source": "user-confirmed",
            "last_updated": "2026-08-24",
        },
        {
            "id": "example-preference",
            "category": "preference",
            "content": "Interested in platform engineering roles.",
            "status": "draft",
            "visibility": "ai_only",
        },
    ],
}
PROHIBITED_KEYS = {
    "access_token",
    "api_key",
    "password",
    "passwd",
    "private_key",
    "refresh_token",
    "secret",
    "social_security_number",
    "ssn",
}
class ProfileStoreError(ValueError):
    """Raised when a workspace career profile cannot be validated or persisted."""


@dataclass(frozen=True)
class ProfileRecord:
    revision: int
    updated_at: str
    profile: dict[str, Any]
    invisible_context: dict[str, Any]

    @property
    def profile_sha256(self) -> str:
        return hashlib.sha256(canonical_profile_bytes(self.profile)).hexdigest()

    @property
    def invisible_context_sha256(self) -> str:
        return hashlib.sha256(canonical_profile_bytes(self.invisible_context)).hexdigest()

    def as_document(self) -> dict[str, Any]:
        return {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "revision": self.revision,
            "updated_at": self.updated_at,
            "profile_sha256": self.profile_sha256,
            "invisible_context_sha256": self.invisible_context_sha256,
            "profile": self.profile,
            INVISIBLE_CONTEXT_KEY: self.invisible_context,
        }

    def as_embedded_profile_document(self) -> dict[str, Any]:
        """Return profile history without duplicating its invisible PDF sidecar."""
        document = self.as_document()
        document.pop(INVISIBLE_CONTEXT_KEY)
        document["invisible_context_attachment"] = "shared_context.json"
        return document

    def as_profile_bundle(self) -> dict[str, Any]:
        return copy.deepcopy(self.profile)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def profile_path(workspace_root: Path) -> Path:
    root = workspace_root.resolve()
    candidate = (root / PROFILE_DIRECTORY / PROFILE_FILENAME).resolve()
    if not candidate.is_relative_to(root):
        raise ProfileStoreError("career profile path escapes the configured workspace")
    return candidate


def history_path(workspace_root: Path) -> Path:
    root = workspace_root.resolve()
    candidate = (root / PROFILE_DIRECTORY / "history" / HISTORY_FILENAME).resolve()
    if not candidate.is_relative_to(root):
        raise ProfileStoreError("generation history path escapes the configured workspace")
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


def _walk(value: Any, path: str = "$") -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path != "$" else f"$.{key}"
            yield from _walk(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")
    else:
        yield path, value


def _check_sensitive_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            child_path = f"{path}.{key}" if path != "$" else f"$.{key}"
            if normalized in PROHIBITED_KEYS:
                raise ProfileStoreError(
                    f"{child_path}: sensitive credential or identity fields are not allowed"
                )
            _check_sensitive_keys(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _check_sensitive_keys(child, f"{path}[{index}]")


def _profile_owner(profile: dict[str, Any]) -> str | None:
    for container_key in ("basics", "candidate"):
        container = profile.get(container_key)
        if isinstance(container, dict):
            name = str(container.get("name", "")).strip()
            if name:
                return name
    name = str(profile.get("name", "")).strip()
    return name or None


def load_watermark_file(path: Path | None = None) -> dict[str, Any]:
    """Load the code-managed JSON payload included in every invisible context."""
    source = path or WATERMARK_FILE_PATH
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProfileStoreError(f"could not read watermark file {source}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProfileStoreError("watermark.json must contain a JSON object")
    try:
        encoded = canonical_profile_bytes(payload)
    except AIContextError as exc:
        raise ProfileStoreError(f"invalid watermark.json: {exc}") from exc
    if len(encoded) > INVISIBLE_CONTEXT_MAX_BYTES:
        raise ProfileStoreError("watermark.json exceeds the 250,000 byte limit")
    return payload


def build_profile_invisible_context(
    profile: dict[str, Any],
    revision: int | None = None,
) -> dict[str, Any]:
    """Build the application-owned watermark. Profile inputs cannot supply it."""
    if not isinstance(profile, dict):
        raise ProfileStoreError("career profile must be a JSON object")
    profile_sha256 = hashlib.sha256(canonical_profile_bytes(profile)).hexdigest()
    revision_value = revision if revision is not None else 0
    watermark_file = load_watermark_file()
    watermark_file_bytes = canonical_profile_bytes(watermark_file)
    try:
        rendered_watermark = render_watermark_template(
            watermark_file,
            profile,
            profile_sha256=profile_sha256,
            revision=revision,
        )
    except WatermarkTemplateError as exc:
        raise ProfileStoreError(f"could not render watermark.json: {exc}") from exc
    rendered_bytes = canonical_profile_bytes(rendered_watermark.content)
    bindings_bytes = canonical_profile_bytes(rendered_watermark.bindings)
    context = {
        "watermark": {
            "schema_version": WATERMARK_SCHEMA_VERSION,
            "watermark_id": f"resume-{profile_sha256[:24]}-r{revision_value}",
            "profile_sha256": profile_sha256,
            "profile_revision": revision,
            "profile_owner": _profile_owner(profile),
            "generated_by": "Resume Studio",
            "ai_editable": False,
            "purpose": "Bind this PDF's invisible context to its Career Profile.",
        },
        "watermark_file": {
            "schema_version": WATERMARK_RENDER_SCHEMA_VERSION,
            "template_sha256": hashlib.sha256(watermark_file_bytes).hexdigest(),
            "sha256": hashlib.sha256(rendered_bytes).hexdigest(),
            "bindings_sha256": hashlib.sha256(bindings_bytes).hexdigest(),
            "template": watermark_file,
            "content": rendered_watermark.content,
            "bindings": rendered_watermark.bindings,
        },
    }
    validate_invisible_context(context)
    return context


def validate_invisible_context(context: dict[str, Any]) -> list[str]:
    if not isinstance(context, dict):
        raise ProfileStoreError("stored invisible_context must be a JSON object")
    encoded = canonical_profile_bytes(context)
    if len(encoded) > INVISIBLE_CONTEXT_MAX_BYTES:
        raise ProfileStoreError("stored invisible_context exceeds the 250,000 byte limit")
    return []


def split_profile_bundle(
    profile: dict[str, Any],
    *,
    profile_revision: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(profile, dict):
        raise ProfileStoreError("career profile must be a JSON object")
    bundle = copy.deepcopy(profile)
    if INVISIBLE_CONTEXT_KEY in bundle:
        raise ProfileStoreError(
            f"$.{INVISIBLE_CONTEXT_KEY} is application-generated and cannot be supplied or edited"
        )
    return bundle, build_profile_invisible_context(bundle, profile_revision)


def validate_profile(profile: dict[str, Any]) -> list[str]:
    profile_body, _ = split_profile_bundle(profile)
    try:
        canonical_profile_bytes(profile_body)
    except AIContextError as exc:
        raise ProfileStoreError(str(exc)) from exc
    _check_sensitive_keys(profile_body)

    warnings: list[str] = []
    if not profile_body:
        warnings.append("The career profile is empty.")

    facts = profile_body.get("facts")
    if facts is not None:
        if not isinstance(facts, list):
            raise ProfileStoreError("$.facts must be an array when present")
        seen_ids: set[str] = set()
        draft_count = 0
        private_count = 0
        for index, fact in enumerate(facts):
            path = f"$.facts[{index}]"
            if not isinstance(fact, dict):
                raise ProfileStoreError(f"{path} must be an object")
            fact_id = str(fact.get("id", "")).strip()
            if not fact_id:
                warnings.append(f"{path} has no stable id.")
            elif fact_id in seen_ids:
                raise ProfileStoreError(f"{path}.id duplicates '{fact_id}'")
            else:
                seen_ids.add(fact_id)

            status = str(fact.get("status", "draft")).strip()
            if status not in PROFILE_STATUSES:
                raise ProfileStoreError(
                    f"{path}.status must be one of {', '.join(sorted(PROFILE_STATUSES))}"
                )
            visibility = str(fact.get("visibility", "ai_only")).strip()
            if visibility not in PROFILE_VISIBILITIES:
                raise ProfileStoreError(
                    f"{path}.visibility must be one of "
                    f"{', '.join(sorted(PROFILE_VISIBILITIES))}"
                )
            if status == "draft":
                draft_count += 1
            if visibility == "private":
                private_count += 1

        if draft_count:
            warnings.append(
                f"The profile contains {draft_count} draft fact(s); do not present them as verified claims."
            )
        if private_count:
            warnings.append(
                f"The profile contains {private_count} private fact(s); exclude them from generated resumes."
            )
    return warnings


def parse_profile(value: str) -> dict[str, Any]:
    try:
        profile = parse_profile_json(value)
    except AIContextError as exc:
        raise ProfileStoreError(str(exc)) from exc
    validate_profile(profile)
    return profile


def load_profile(workspace_root: Path) -> ProfileRecord | None:
    source = profile_path(workspace_root)
    if not source.exists():
        return None
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProfileStoreError(f"could not read {source}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProfileStoreError("stored career profile must be a JSON object")
    allowed = {
        "schema_version",
        "revision",
        "updated_at",
        "profile_sha256",
        "invisible_context_sha256",
        "profile",
        INVISIBLE_CONTEXT_KEY,
    }
    missing = {"schema_version", "revision", "updated_at", "profile"} - set(raw)
    unknown = set(raw) - allowed
    if missing:
        raise ProfileStoreError(f"stored career profile is missing: {', '.join(sorted(missing))}")
    if unknown:
        raise ProfileStoreError(f"stored career profile has unknown keys: {', '.join(sorted(unknown))}")
    stored_schema_version = raw["schema_version"]
    if stored_schema_version not in {PROFILE_SCHEMA_VERSION, *LEGACY_PROFILE_SCHEMA_VERSIONS}:
        raise ProfileStoreError(
            f"stored schema_version must equal {PROFILE_SCHEMA_VERSION} "
            f"or a supported legacy version"
        )
    revision = raw["revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ProfileStoreError("stored revision must be a positive integer")
    if not isinstance(raw["updated_at"], str) or not raw["updated_at"].strip():
        raise ProfileStoreError("stored updated_at must be non-empty text")
    profile = raw["profile"]
    if not isinstance(profile, dict):
        raise ProfileStoreError("stored profile must be a JSON object")
    validate_profile(profile)
    generated_context = build_profile_invisible_context(profile, revision)
    if stored_schema_version == PROFILE_SCHEMA_VERSION:
        stored_context = raw.get(INVISIBLE_CONTEXT_KEY)
        if not isinstance(stored_context, dict):
            raise ProfileStoreError("stored invisible_context must be a JSON object")
        validate_invisible_context(stored_context)
        expected_context_sha256 = str(raw.get("invisible_context_sha256", ""))
        stored_context_sha256 = hashlib.sha256(
            canonical_profile_bytes(stored_context)
        ).hexdigest()
        if expected_context_sha256 and expected_context_sha256 != stored_context_sha256:
            raise ProfileStoreError("stored invisible context SHA-256 does not match its content")
        if stored_context.get("watermark") != generated_context["watermark"]:
            raise ProfileStoreError(
                "stored invisible context does not match the application-generated watermark"
            )
        invisible_context = generated_context
    else:
        invisible_context = generated_context
    record = ProfileRecord(
        revision=revision,
        updated_at=raw["updated_at"],
        profile=profile,
        invisible_context=invisible_context,
    )
    expected_sha256 = str(raw.get("profile_sha256", ""))
    if expected_sha256 and expected_sha256 != record.profile_sha256:
        raise ProfileStoreError("stored career profile SHA-256 does not match its content")
    return record


def verify_profile_watermark(
    embedded_profile: dict[str, Any],
    shared_context: dict[str, Any],
) -> dict[str, Any]:
    """Re-render an embedded template and verify its complete Profile binding."""
    if not isinstance(embedded_profile, dict) or not isinstance(shared_context, dict):
        raise ProfileStoreError("embedded Profile and shared context must be JSON objects")
    stored_schema = embedded_profile.get("schema_version")
    if stored_schema in {PROFILE_SCHEMA_VERSION, *LEGACY_PROFILE_SCHEMA_VERSIONS}:
        profile = embedded_profile.get("profile")
        revision = embedded_profile.get("revision")
        if not isinstance(profile, dict):
            raise ProfileStoreError("embedded career Profile body must be a JSON object")
    else:
        profile = embedded_profile
        revision = None

    context = shared_context.get("context")
    if not isinstance(context, dict):
        raise ProfileStoreError("shared context must contain a context object")
    watermark = context.get("watermark")
    watermark_file = context.get("watermark_file")
    if not isinstance(watermark, dict) or not isinstance(watermark_file, dict):
        trust = shared_context.get("trust")
        if isinstance(trust, dict) and trust.get("level") == "application-managed-watermark":
            raise ProfileStoreError("shared context is missing its Profile watermark")
        return {
            "status": "unavailable",
            "reason": "PDF does not contain an application-managed Profile watermark",
        }
    if watermark_file.get("schema_version") != WATERMARK_RENDER_SCHEMA_VERSION:
        return {
            "status": "unavailable",
            "reason": "PDF predates the verifiable placeholder watermark format",
        }

    template = watermark_file.get("template")
    content = watermark_file.get("content")
    bindings = watermark_file.get("bindings")
    if not isinstance(template, dict):
        raise ProfileStoreError("watermark verification template must be a JSON object")
    if not isinstance(content, dict) or not isinstance(bindings, dict):
        raise ProfileStoreError("watermark content and bindings must be JSON objects")

    profile_sha256 = hashlib.sha256(canonical_profile_bytes(profile)).hexdigest()
    if watermark.get("profile_sha256") != profile_sha256:
        raise ProfileStoreError("watermark Profile SHA-256 does not match the embedded Profile")
    if watermark.get("profile_revision") != revision:
        raise ProfileStoreError("watermark Profile revision does not match the embedded Profile")

    try:
        expected = render_watermark_template(
            template,
            profile,
            profile_sha256=profile_sha256,
            revision=revision,
        )
    except WatermarkTemplateError as exc:
        raise ProfileStoreError(f"could not verify embedded watermark template: {exc}") from exc
    template_sha256 = hashlib.sha256(canonical_profile_bytes(template)).hexdigest()
    rendered_sha256 = hashlib.sha256(canonical_profile_bytes(expected.content)).hexdigest()
    bindings_sha256 = hashlib.sha256(canonical_profile_bytes(expected.bindings)).hexdigest()
    checks = {
        "template_sha256": (watermark_file.get("template_sha256"), template_sha256),
        "rendered_sha256": (watermark_file.get("sha256"), rendered_sha256),
        "bindings_sha256": (watermark_file.get("bindings_sha256"), bindings_sha256),
        "rendered_content": (content, expected.content),
        "bindings": (bindings, expected.bindings),
    }
    failed = [name for name, (actual, expected_value) in checks.items() if actual != expected_value]
    if failed:
        raise ProfileStoreError(
            "watermark Profile binding verification failed: " + ", ".join(failed)
        )
    return {
        "status": "verified",
        "schema_version": WATERMARK_RENDER_SCHEMA_VERSION,
        "profile_sha256": profile_sha256,
        "profile_revision": revision,
        "binding_count": len(bindings),
        "template_sha256": template_sha256,
        "rendered_sha256": rendered_sha256,
        "bindings_sha256": bindings_sha256,
    }


def profile_update_preview(
    workspace_root: Path,
    profile_json: str,
    expected_revision: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
        raise ProfileStoreError("expected_revision must be a non-negative integer")
    profile = parse_profile(profile_json)
    current = load_profile(workspace_root)
    current_revision = current.revision if current else 0
    if current_revision != expected_revision:
        raise ProfileStoreError(
            f"revision conflict: expected {expected_revision}, current revision is {current_revision}"
        )
    invisible_context = build_profile_invisible_context(profile, current_revision + 1)
    previous = current.profile if current else {}
    previous_context = current.invisible_context if current else {}
    added = sorted(set(profile) - set(previous))
    removed = sorted(set(previous) - set(profile))
    changed = sorted(
        key for key in set(profile) & set(previous) if profile[key] != previous[key]
    )
    preview = {
        "current_revision": current_revision,
        "next_revision": current_revision + 1,
        "added_top_level_keys": added,
        "removed_top_level_keys": removed,
        "changed_top_level_keys": changed,
        "current_profile_sha256": current.profile_sha256 if current else None,
        "new_profile_sha256": hashlib.sha256(canonical_profile_bytes(profile)).hexdigest(),
        "invisible_context_added_top_level_keys": sorted(
            set(invisible_context) - set(previous_context)
        ),
        "invisible_context_removed_top_level_keys": sorted(
            set(previous_context) - set(invisible_context)
        ),
        "invisible_context_changed_top_level_keys": sorted(
            key
            for key in set(invisible_context) & set(previous_context)
            if invisible_context[key] != previous_context[key]
        ),
        "current_invisible_context_sha256": (
            current.invisible_context_sha256 if current else None
        ),
        "new_invisible_context_sha256": hashlib.sha256(
            canonical_profile_bytes(invisible_context)
        ).hexdigest(),
        "watermark": invisible_context["watermark"],
        "warnings": validate_profile(profile),
    }
    return profile, invisible_context, preview


def update_profile(
    workspace_root: Path,
    profile_json: str,
    expected_revision: int,
    *,
    confirm: bool,
) -> tuple[ProfileRecord | None, dict[str, Any]]:
    if not isinstance(confirm, bool):
        raise ProfileStoreError("confirm must be true or false")
    profile, invisible_context, preview = profile_update_preview(
        workspace_root,
        profile_json,
        expected_revision,
    )
    if not confirm:
        return None, preview

    current = load_profile(workspace_root)
    current_revision = current.revision if current else 0
    if current_revision != expected_revision:
        raise ProfileStoreError(
            f"revision conflict before commit: expected {expected_revision}, "
            f"current revision is {current_revision}"
        )
    record = ProfileRecord(
        revision=preview["next_revision"],
        updated_at=_now(),
        profile=profile,
        invisible_context=invisible_context,
    )
    destination = profile_path(workspace_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(record.as_document(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _atomic_write(destination, encoded)
    return record, preview


def search_profile(record: ProfileRecord, query: str, limit: int = 20) -> list[dict[str, Any]]:
    if not isinstance(query, str) or not query.strip():
        raise ProfileStoreError("query must be non-empty text")
    if len(query) > 200:
        raise ProfileStoreError("query must contain at most 200 characters")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ProfileStoreError("limit must be an integer from 1 to 50")
    phrase = query.strip().lower()
    tokens = [token for token in re.findall(r"[\w+#.-]+", phrase) if token]
    matches: list[dict[str, Any]] = []
    for path, value in _walk(record.profile):
        if value is None or isinstance(value, bool):
            continue
        rendered = str(value)
        searchable = rendered.lower()
        score = (10 if phrase in searchable else 0) + sum(searchable.count(token) for token in tokens)
        if not score:
            continue
        matches.append(
            {
                "path": path,
                "value": rendered[:500],
                "score": score,
            }
        )
    matches.sort(key=lambda item: (-item["score"], item["path"]))
    return matches[:limit]


def append_generation_history(workspace_root: Path, entry: dict[str, Any]) -> None:
    destination = history_path(workspace_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(entry, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    if len(line) > 100_000:
        raise ProfileStoreError("generation history entry exceeds 100,000 bytes")
    descriptor = os.open(destination, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, line)
    finally:
        os.close(descriptor)


def read_generation_history(workspace_root: Path, limit: int = 20) -> list[dict[str, Any]]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ProfileStoreError("limit must be an integer from 1 to 100")
    source = history_path(workspace_root)
    if not source.exists():
        return []
    try:
        with source.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            start = max(0, size - 10_000_000)
            stream.seek(start)
            data = stream.read()
        if start:
            first_newline = data.find(b"\n")
            data = data[first_newline + 1 :] if first_newline >= 0 else b""
        lines = data.decode("utf-8").splitlines()
        entries = [json.loads(line) for line in lines[-limit:] if line.strip()]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProfileStoreError(f"could not read generation history: {exc}") from exc
    entries.reverse()
    return entries
