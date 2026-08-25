from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any


WATERMARK_RENDER_SCHEMA_VERSION = "resume.watermark-render.v1"
PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*profile\.([A-Za-z0-9_.-]+)\s*\}\}")

COLLECTION_KEYS: dict[str, tuple[str, ...]] = {
    "experience": (
        "experience",
        "experiences",
        "work_experience",
        "work_experiences",
        "employment",
        "work",
    ),
    "education": ("education", "educations", "academic_history"),
    "project": ("project", "projects"),
    "skill": ("skill", "skills", "competencies"),
    "certification": ("certification", "certifications", "certificate", "certificates"),
    "award": ("award", "awards", "honor", "honors"),
    "publication": ("publication", "publications", "research"),
    "language": ("language", "languages"),
    "volunteer": ("volunteer", "volunteering", "community_service"),
    "reference": ("reference", "references"),
    "fact": ("fact", "facts"),
}
COLLECTION_ALIASES = {
    alias: canonical
    for canonical, aliases in COLLECTION_KEYS.items()
    for alias in aliases
}
IDENTITY_ALIASES: dict[str, tuple[str, ...]] = {
    "email": ("email", "email_address"),
    "phone": ("phone", "phone_number", "mobile"),
    "location": ("location", "address"),
    "linkedin": ("linkedin", "linkedin_url"),
    "github": ("github", "github_url"),
    "website": ("website", "portfolio", "url"),
}


class WatermarkTemplateError(ValueError):
    """Raised when a watermark template cannot be rendered safely."""


@dataclass(frozen=True)
class RenderedWatermark:
    content: dict[str, Any]
    bindings: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class _Resolved:
    value: Any
    source_path: str | None
    found: bool
    requested_index: int | None = None
    resolved_index: int | None = None
    collection_size: int | None = None
    cycled: bool = False


def _canonical_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise WatermarkTemplateError(f"watermark value is not valid JSON: {exc}") from exc


def _normalized_key(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _mapping_value(mapping: dict[str, Any], key: str) -> tuple[str, Any] | None:
    if key in mapping:
        return key, mapping[key]
    normalized = _normalized_key(key)
    for actual_key, value in mapping.items():
        if _normalized_key(str(actual_key)) == normalized:
            return str(actual_key), value
    return None


def _identity_containers(profile: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    containers: list[tuple[str, dict[str, Any]]] = []
    for key in ("basics", "candidate", "identity", "personal"):
        value = profile.get(key)
        if isinstance(value, dict):
            containers.append((f"$.{key}", value))
    containers.append(("$", profile))
    return containers


def _identity_field(
    profile: dict[str, Any], aliases: tuple[str, ...]
) -> tuple[Any, str] | None:
    for base_path, container in _identity_containers(profile):
        for alias in aliases:
            located = _mapping_value(container, alias)
            if located is not None and located[1] not in (None, ""):
                key, value = located
                return value, f"{base_path}.{key}" if base_path != "$" else f"$.{key}"
    return None


def _name_parts(profile: dict[str, Any]) -> dict[str, tuple[Any, str] | None]:
    explicit_first = _identity_field(profile, ("first_name", "given_name"))
    explicit_middle = _identity_field(profile, ("middle_name", "middle_names"))
    explicit_last = _identity_field(profile, ("last_name", "family_name", "surname"))
    full = _identity_field(profile, ("name", "full_name"))
    if full is None:
        pieces = [
            str(value[0]).strip()
            for value in (explicit_first, explicit_middle, explicit_last)
            if value is not None and str(value[0]).strip()
        ]
        full = (" ".join(pieces), "$.<composed_name>") if pieces else None
    tokens = str(full[0]).split() if full is not None else []
    first = explicit_first or ((tokens[0], full[1]) if tokens else None)
    last = explicit_last or ((tokens[-1], full[1]) if len(tokens) > 1 else None)
    middle = explicit_middle or (
        (" ".join(tokens[1:-1]), full[1]) if len(tokens) > 2 else None
    )
    initials = None
    if tokens:
        initials = ("".join(token[0].upper() for token in tokens if token), full[1])
    return {
        "full_name": full,
        "name": full,
        "first_name": first,
        "middle_name": middle,
        "middle_names": middle,
        "last_name": last,
        "initials": initials,
    }


def _collection_from_value(
    value: Any,
    source_path: str,
) -> list[tuple[Any, str]]:
    if isinstance(value, list):
        return [(item, f"{source_path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, dict):
        items: list[tuple[Any, str]] = []
        for key, child in value.items():
            child_path = f"{source_path}.{key}"
            if isinstance(child, list):
                items.extend(
                    (
                        {"category": str(key), "value": item},
                        f"{child_path}[{index}]",
                    )
                    for index, item in enumerate(child)
                )
            else:
                items.append(({"category": str(key), "value": child}, child_path))
        return items
    if value in (None, ""):
        return []
    return [(value, source_path)]


def _collection(profile: dict[str, Any], canonical: str) -> list[tuple[Any, str]]:
    for key in COLLECTION_KEYS[canonical]:
        located = _mapping_value(profile, key)
        if located is not None:
            actual_key, value = located
            items = _collection_from_value(value, f"$.{actual_key}")
            if items:
                return items

    skills = profile.get("skills")
    if canonical == "language" and isinstance(skills, dict):
        located = _mapping_value(skills, "languages")
        if located is not None:
            actual_key, value = located
            items = _collection_from_value(value, f"$.skills.{actual_key}")
            if items:
                return items

    facts = profile.get("facts")
    if canonical != "fact" and isinstance(facts, list):
        accepted_categories = {
            _normalized_key(alias) for alias in COLLECTION_KEYS[canonical]
        }
        matching = []
        for index, fact in enumerate(facts):
            if not isinstance(fact, dict):
                continue
            category = _normalized_key(str(fact.get("category", "")))
            if category in accepted_categories:
                matching.append((fact, f"$.facts[{index}]"))
        if matching:
            return matching
    return []


def _missing(expression: str, profile_sha256: str) -> _Resolved:
    marker = f"missing:{expression}:{profile_sha256[:16]}"
    return _Resolved(value=marker, source_path=None, found=False)


def _walk_value(
    value: Any,
    segments: list[str],
    source_path: str,
    expression: str,
    profile_sha256: str,
) -> _Resolved:
    requested_index = None
    resolved_index = None
    collection_size = None
    cycled = False
    current = value
    current_path = source_path
    for segment in segments:
        if isinstance(current, dict):
            located = _mapping_value(current, segment)
            if located is None:
                return _missing(expression, profile_sha256)
            actual_key, current = located
            current_path = f"{current_path}.{actual_key}"
            continue
        if isinstance(current, list) and segment.isdigit() and int(segment) >= 1:
            requested_index = int(segment)
            collection_size = len(current)
            if not current:
                return _missing(expression, profile_sha256)
            resolved_index = ((requested_index - 1) % collection_size) + 1
            cycled = requested_index > collection_size
            current = current[resolved_index - 1]
            current_path = f"{current_path}[{resolved_index - 1}]"
            continue
        return _missing(expression, profile_sha256)
    return _Resolved(
        value=current,
        source_path=current_path,
        found=True,
        requested_index=requested_index,
        resolved_index=resolved_index,
        collection_size=collection_size,
        cycled=cycled,
    )


def _resolve(
    expression: str,
    profile: dict[str, Any],
    profile_sha256: str,
    revision: int | None,
) -> _Resolved:
    if expression in {"profile_sha256", "raw_sha256"}:
        return _Resolved(profile_sha256, "$", True)
    if expression == "revision":
        return _Resolved(revision, "$.<profile_revision>", True)
    if expression == "raw":
        return _Resolved(copy.deepcopy(profile), "$", True)
    if expression == "top_level_keys":
        return _Resolved(sorted(str(key) for key in profile), "$", True)

    names = _name_parts(profile)
    if expression in names:
        located = names[expression]
        return (
            _Resolved(located[0], located[1], True)
            if located is not None
            else _missing(expression, profile_sha256)
        )
    if expression in IDENTITY_ALIASES:
        located = _identity_field(profile, IDENTITY_ALIASES[expression])
        return (
            _Resolved(located[0], located[1], True)
            if located is not None
            else _missing(expression, profile_sha256)
        )

    if expression.endswith("_count"):
        alias = expression[: -len("_count")]
        canonical = COLLECTION_ALIASES.get(alias)
        if canonical is not None:
            items = _collection(profile, canonical)
            return _Resolved(len(items), f"$.<{canonical}_count>", True)

    segments = expression.split(".")
    canonical = COLLECTION_ALIASES.get(segments[0])
    if canonical is not None and len(segments) >= 2 and segments[1].isdigit():
        requested_index = int(segments[1])
        if requested_index < 1:
            return _missing(expression, profile_sha256)
        items = _collection(profile, canonical)
        collection_size = len(items)
        if not items:
            missing = _missing(expression, profile_sha256)
            return _Resolved(
                value=missing.value,
                source_path=None,
                found=False,
                requested_index=requested_index,
                collection_size=0,
            )
        resolved_index = ((requested_index - 1) % collection_size) + 1
        value, source_path = items[resolved_index - 1]
        nested = _walk_value(
            value,
            segments[2:],
            source_path,
            expression,
            profile_sha256,
        )
        return _Resolved(
            value=nested.value,
            source_path=nested.source_path,
            found=nested.found,
            requested_index=requested_index,
            resolved_index=resolved_index,
            collection_size=collection_size,
            cycled=requested_index > collection_size,
        )

    return _walk_value(profile, segments, "$", expression, profile_sha256)


def _display_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def render_watermark_template(
    template: dict[str, Any],
    profile: dict[str, Any],
    *,
    profile_sha256: str,
    revision: int | None,
) -> RenderedWatermark:
    """Render all {{profile.*}} values and emit a per-field binding manifest."""
    if not isinstance(template, dict):
        raise WatermarkTemplateError("watermark template must be a JSON object")
    bindings: dict[str, dict[str, Any]] = {}

    def resolve_and_record(expression: str) -> Any:
        resolved = _resolve(expression, profile, profile_sha256, revision)
        value_bytes = _canonical_bytes(resolved.value)
        value_sha256 = hashlib.sha256(value_bytes).hexdigest()
        binding_id = hashlib.sha256(
            f"{profile_sha256}:{expression}:{value_sha256}".encode("utf-8")
        ).hexdigest()
        bindings[f"profile.{expression}"] = {
            "binding_id": f"wm-{binding_id[:32]}",
            "source_path": resolved.source_path,
            "found": resolved.found,
            "requested_index": resolved.requested_index,
            "resolved_index": resolved.resolved_index,
            "collection_size": resolved.collection_size,
            "cycled": resolved.cycled,
            "value_sha256": value_sha256,
        }
        return copy.deepcopy(resolved.value)

    def render(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: render(child) for key, child in value.items()}
        if isinstance(value, list):
            return [render(child) for child in value]
        if not isinstance(value, str):
            return value
        exact = PLACEHOLDER_PATTERN.fullmatch(value)
        if exact is not None:
            return resolve_and_record(exact.group(1))

        def replace(match: re.Match[str]) -> str:
            return _display_value(resolve_and_record(match.group(1)))

        return PLACEHOLDER_PATTERN.sub(replace, value)

    content = render(template)
    if not isinstance(content, dict):
        raise WatermarkTemplateError("rendered watermark must be a JSON object")
    return RenderedWatermark(content=content, bindings=bindings)
