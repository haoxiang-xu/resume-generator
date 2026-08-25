from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any

from .watermark_template import COLLECTION_ALIASES


PROFILE_CONTEXT_ZONES_SCHEMA_VERSION = "resume.profile-context-zones.v1"
LONG_TEXT_MIN_CHARS = 160
MAX_CONTEXT_ZONES = 1_000


def _canonical_bytes(value: Any) -> bytes:
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


def _normalized_key(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _path_key(path: str, key: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key):
        return f"{path}.{key}"
    return f"{path}[{json.dumps(key, ensure_ascii=False)}]"


def _text_char_count(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(_text_char_count(child) for child in value)
    if isinstance(value, dict):
        return sum(_text_char_count(child) for child in value.values())
    return 0


def build_profile_context_zones(
    profile: dict[str, Any],
    *,
    profile_sha256: str,
    revision: int | None,
) -> dict[str, Any]:
    """Index exhaustive Profile content that remains AI-readable outside the page."""
    zones: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    category_counts: Counter[str] = Counter()
    truncated = False

    def add_zone(path: str, category: str, kind: str, value: Any) -> None:
        nonlocal truncated
        identity = (path, kind)
        if identity in seen:
            return
        if len(zones) >= MAX_CONTEXT_ZONES:
            truncated = True
            return
        seen.add(identity)
        encoded = _canonical_bytes(value)
        content_sha256 = hashlib.sha256(encoded).hexdigest()
        zone_seed = f"{profile_sha256}:{path}:{kind}:{content_sha256}".encode("utf-8")
        normalized_category = category or "profile"
        zone = {
            "zone_id": f"ctx-{hashlib.sha256(zone_seed).hexdigest()[:32]}",
            "category": normalized_category,
            "kind": kind,
            "profile_path": path,
            "value_type": (
                "object"
                if isinstance(value, dict)
                else "array"
                if isinstance(value, list)
                else "text"
                if isinstance(value, str)
                else type(value).__name__
            ),
            "text_char_count": _text_char_count(value),
            "content_sha256": content_sha256,
            "source_attachment": "career_profile.json",
            "default_visibility": "ai_only",
            "eligible_for_visible_resume": True,
        }
        if isinstance(value, (list, dict)):
            zone["item_count"] = len(value)
        zones.append(zone)
        category_counts[normalized_category] += 1

    def walk(value: Any, path: str, category: str, *, indexed_collection: bool) -> None:
        if isinstance(value, dict):
            for raw_key in sorted(value, key=lambda item: _normalized_key(str(item))):
                child = value[raw_key]
                key = str(raw_key)
                walk(
                    child,
                    _path_key(path, key),
                    category,
                    indexed_collection=indexed_collection,
                )
            return
        if isinstance(value, list):
            if _text_char_count(value) >= LONG_TEXT_MIN_CHARS:
                add_zone(path, category, "content_collection", value)
            for index, child in enumerate(value):
                child_path = f"{path}[{index}]"
                if indexed_collection or _text_char_count(child) >= LONG_TEXT_MIN_CHARS:
                    add_zone(
                        child_path,
                        category,
                        "record" if isinstance(child, (dict, list)) else "content_item",
                        child,
                    )
                walk(
                    child,
                    child_path,
                    category,
                    indexed_collection=indexed_collection,
                )
            return
        if isinstance(value, str) and len(value) >= LONG_TEXT_MIN_CHARS:
            add_zone(path, category, "long_text", value)

    for raw_key in sorted(profile, key=lambda item: _normalized_key(str(item))):
        value = profile[raw_key]
        key = str(raw_key)
        normalized_key = _normalized_key(key)
        category = COLLECTION_ALIASES.get(normalized_key, normalized_key)
        path = _path_key("$", key)
        known_collection = normalized_key in COLLECTION_ALIASES
        if known_collection:
            add_zone(path, category, "profile_section", value)
        elif isinstance(value, (list, dict)) and _text_char_count(value) >= LONG_TEXT_MIN_CHARS:
            add_zone(path, category, "profile_section", value)
        walk(value, path, category, indexed_collection=known_collection)

    zones_sha256 = hashlib.sha256(_canonical_bytes(zones)).hexdigest()
    return {
        "schema_version": PROFILE_CONTEXT_ZONES_SCHEMA_VERSION,
        "profile_sha256": profile_sha256,
        "profile_revision": revision,
        "source_attachment": "career_profile.json",
        "zones_sha256": zones_sha256,
        "policy": {
            "profile_collection_mode": "exhaustive",
            "visible_resume_mode": "selective",
            "default_profile_visibility": "ai_only",
            "selection_decider": "resume_generation_ai",
            "page_budget_applies_to_profile": False,
            "unselected_profile_content_remains_ai_readable": True,
        },
        "coverage": {
            "zone_count": len(zones),
            "category_counts": dict(sorted(category_counts.items())),
            "long_text_min_chars": LONG_TEXT_MIN_CHARS,
            "max_zones": MAX_CONTEXT_ZONES,
            "truncated": truncated,
        },
        "zones": zones,
    }
