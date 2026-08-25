from __future__ import annotations

import copy
import json
import re
from typing import Any


SCHEMA_VERSION = "resume.document.v1"
LAYOUTS = ("entries", "compact_rows", "paragraphs", "bullet_list")
MAX_DOCUMENT_CHARS = 1_000_000


class ResumeSchemaError(ValueError):
    """Raised when an AI-authored resume document violates the v1 contract."""


DOCUMENT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": SCHEMA_VERSION,
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "basics", "sections"],
    "properties": {
        "schema_version": {"const": SCHEMA_VERSION},
        "basics": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "contacts"],
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 120},
                "headline": {"type": "string", "maxLength": 180},
                "contacts": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["label", "value"],
                        "properties": {
                            "label": {"type": "string", "minLength": 1, "maxLength": 40},
                            "value": {"type": "string", "minLength": 1, "maxLength": 240},
                            "url": {"type": "string", "maxLength": 500},
                        },
                    },
                },
            },
        },
        "sections": {
            "type": "array",
            "minItems": 1,
            "maxItems": 12,
            "items": {"$ref": "#/$defs/section"},
        },
        "render": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "template": {"const": "classic"},
                "paper_size": {"enum": ["letter", "a4"]},
                "max_pages": {"type": "integer", "minimum": 1, "maximum": 3},
                "density": {"enum": ["compact", "standard", "relaxed"]},
            },
        },
    },
    "$defs": {
        "section": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "title", "layout"],
            "properties": {
                "id": {"type": "string", "pattern": "^[a-z][a-z0-9_]{0,63}$"},
                "title": {"type": "string", "minLength": 1, "maxLength": 80},
                "layout": {"enum": list(LAYOUTS)},
                "entries": {"type": "array", "maxItems": 30},
                "rows": {"type": "array", "maxItems": 30},
                "paragraphs": {"type": "array", "maxItems": 12},
                "bullets": {"type": "array", "maxItems": 30},
            },
        }
    },
}


EXAMPLE_DOCUMENT: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "basics": {
        "name": "Haoxiang Xu",
        "headline": "Cloud Platform Developer and Applied AI Engineer",
        "contacts": [
            {"label": "Email", "value": "name@example.com", "url": "mailto:name@example.com"},
            {"label": "GitHub", "value": "github.com/example", "url": "https://github.com/example"},
        ],
    },
    "sections": [
        {
            "id": "selected_impact",
            "title": "Selected Impact",
            "layout": "entries",
            "entries": [
                {
                    "primary": "Example Company",
                    "secondary": "Cloud Platform Developer",
                    "aside_top": "Vancouver, Canada",
                    "aside_bottom": "2025 - Present",
                    "link": "",
                    "bullets": [
                        "Re-architected an **LLM retrieval system** across 30+ related tables.",
                        "Added support for long and complex filter conditions.",
                    ],
                }
            ],
        },
        {
            "id": "technical_foundation",
            "title": "Technical Foundation",
            "layout": "compact_rows",
            "rows": [
                {"label": "Languages", "content": "Python, Java, Go, Rust", "link": ""},
                {"label": "Platforms", "content": "AWS, Docker, Kubernetes", "link": ""},
            ],
        },
    ],
    "render": {
        "template": "classic",
        "paper_size": "letter",
        "max_pages": 1,
        "density": "compact",
    },
}


def _fail(path: str, message: str) -> None:
    raise ResumeSchemaError(f"{path}: {message}")


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    return value


def _list(value: Any, path: str, *, minimum: int = 0, maximum: int) -> list[Any]:
    if not isinstance(value, list):
        _fail(path, "must be an array")
    if len(value) < minimum:
        _fail(path, f"must contain at least {minimum} item(s)")
    if len(value) > maximum:
        _fail(path, f"must contain at most {maximum} item(s)")
    return value


def _keys(value: dict[str, Any], path: str, *, allowed: set[str], required: set[str]) -> None:
    missing = required - set(value)
    unknown = set(value) - allowed
    if missing:
        _fail(path, f"missing keys: {', '.join(sorted(missing))}")
    if unknown:
        _fail(path, f"unknown keys: {', '.join(sorted(unknown))}")


def _text(
    value: Any,
    path: str,
    *,
    minimum: int = 0,
    maximum: int,
) -> str:
    if not isinstance(value, str):
        _fail(path, "must be text")
    cleaned = value.strip()
    if len(cleaned) < minimum:
        _fail(path, f"must contain at least {minimum} character(s)")
    if len(cleaned) > maximum:
        _fail(path, f"must contain at most {maximum} characters")
    return cleaned


def _optional_text(value: dict[str, Any], key: str, path: str, maximum: int) -> str:
    if key not in value:
        return ""
    return _text(value[key], f"{path}.{key}", maximum=maximum)


def _text_array(value: Any, path: str, maximum: int) -> list[str]:
    raw = _list(value, path, maximum=maximum)
    return [_text(item, f"{path}[{index}]", minimum=1, maximum=2_000) for index, item in enumerate(raw)]


def parse_document(resume_json: str) -> dict[str, Any]:
    if not isinstance(resume_json, str):
        raise ResumeSchemaError("resume_json must be text")
    if len(resume_json) > MAX_DOCUMENT_CHARS:
        raise ResumeSchemaError("resume_json exceeds the 1,000,000 character limit")
    try:
        raw = json.loads(resume_json)
    except json.JSONDecodeError as exc:
        raise ResumeSchemaError(f"resume_json is not valid JSON: {exc.msg}") from exc
    return validate_document(raw)


def validate_document(raw: Any) -> dict[str, Any]:
    document = copy.deepcopy(_object(raw, "$"))
    _keys(
        document,
        "$",
        allowed={"schema_version", "basics", "sections", "render"},
        required={"schema_version", "basics", "sections"},
    )
    if document["schema_version"] != SCHEMA_VERSION:
        _fail("$.schema_version", f"must equal {SCHEMA_VERSION}")

    basics = _object(document["basics"], "$.basics")
    _keys(
        basics,
        "$.basics",
        allowed={"name", "headline", "contacts"},
        required={"name", "contacts"},
    )
    basics["name"] = _text(basics["name"], "$.basics.name", minimum=1, maximum=120)
    basics["headline"] = _optional_text(basics, "headline", "$.basics", 180)
    contacts = _list(basics["contacts"], "$.basics.contacts", maximum=8)
    normalized_contacts: list[dict[str, str]] = []
    for index, raw_contact in enumerate(contacts):
        path = f"$.basics.contacts[{index}]"
        contact = _object(raw_contact, path)
        _keys(contact, path, allowed={"label", "value", "url"}, required={"label", "value"})
        normalized_contacts.append(
            {
                "label": _text(contact["label"], f"{path}.label", minimum=1, maximum=40),
                "value": _text(contact["value"], f"{path}.value", minimum=1, maximum=240),
                "url": _optional_text(contact, "url", path, 500),
            }
        )
    basics["contacts"] = normalized_contacts

    raw_sections = _list(document["sections"], "$.sections", minimum=1, maximum=12)
    normalized_sections: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw_section in enumerate(raw_sections):
        path = f"$.sections[{index}]"
        section = _object(raw_section, path)
        _keys(
            section,
            path,
            allowed={"id", "title", "layout", "entries", "rows", "paragraphs", "bullets"},
            required={"id", "title", "layout"},
        )
        section_id = _text(section["id"], f"{path}.id", minimum=1, maximum=64)
        if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", section_id) is None:
            _fail(f"{path}.id", "must match ^[a-z][a-z0-9_]{0,63}$")
        if section_id in seen_ids:
            _fail(f"{path}.id", "must be unique")
        seen_ids.add(section_id)
        title = _text(section["title"], f"{path}.title", minimum=1, maximum=80)
        layout = _text(section["layout"], f"{path}.layout", minimum=1, maximum=32)
        if layout not in LAYOUTS:
            _fail(f"{path}.layout", f"must be one of {', '.join(LAYOUTS)}")

        normalized: dict[str, Any] = {"id": section_id, "title": title, "layout": layout}
        expected_field = {
            "entries": "entries",
            "compact_rows": "rows",
            "paragraphs": "paragraphs",
            "bullet_list": "bullets",
        }[layout]
        supplied_content = {key for key in ("entries", "rows", "paragraphs", "bullets") if key in section}
        if supplied_content != {expected_field}:
            _fail(path, f"layout '{layout}' requires only the '{expected_field}' content field")

        if layout == "entries":
            entries = _list(section["entries"], f"{path}.entries", minimum=1, maximum=30)
            normalized_entries: list[dict[str, Any]] = []
            for entry_index, raw_entry in enumerate(entries):
                entry_path = f"{path}.entries[{entry_index}]"
                entry = _object(raw_entry, entry_path)
                _keys(
                    entry,
                    entry_path,
                    allowed={"primary", "secondary", "aside_top", "aside_bottom", "link", "bullets"},
                    required={"primary"},
                )
                normalized_entries.append(
                    {
                        "primary": _text(entry["primary"], f"{entry_path}.primary", minimum=1, maximum=240),
                        "secondary": _optional_text(entry, "secondary", entry_path, 240),
                        "aside_top": _optional_text(entry, "aside_top", entry_path, 120),
                        "aside_bottom": _optional_text(entry, "aside_bottom", entry_path, 120),
                        "link": _optional_text(entry, "link", entry_path, 500),
                        "bullets": _text_array(entry.get("bullets", []), f"{entry_path}.bullets", 12),
                    }
                )
            normalized["entries"] = normalized_entries
        elif layout == "compact_rows":
            rows = _list(section["rows"], f"{path}.rows", minimum=1, maximum=30)
            normalized_rows: list[dict[str, str]] = []
            for row_index, raw_row in enumerate(rows):
                row_path = f"{path}.rows[{row_index}]"
                row = _object(raw_row, row_path)
                _keys(row, row_path, allowed={"label", "content", "link"}, required={"label", "content"})
                normalized_rows.append(
                    {
                        "label": _text(row["label"], f"{row_path}.label", minimum=1, maximum=80),
                        "content": _text(row["content"], f"{row_path}.content", minimum=1, maximum=2_000),
                        "link": _optional_text(row, "link", row_path, 500),
                    }
                )
            normalized["rows"] = normalized_rows
        elif layout == "paragraphs":
            normalized["paragraphs"] = _text_array(section["paragraphs"], f"{path}.paragraphs", 12)
            if not normalized["paragraphs"]:
                _fail(f"{path}.paragraphs", "must contain at least one item")
        else:
            normalized["bullets"] = _text_array(section["bullets"], f"{path}.bullets", 30)
            if not normalized["bullets"]:
                _fail(f"{path}.bullets", "must contain at least one item")
        normalized_sections.append(normalized)
    document["sections"] = normalized_sections

    render = _object(document.get("render", {}), "$.render")
    _keys(
        render,
        "$.render",
        allowed={"template", "paper_size", "max_pages", "density"},
        required=set(),
    )
    template = str(render.get("template") or "classic").strip()
    paper_size = str(render.get("paper_size") or "letter").strip()
    density = str(render.get("density") or "compact").strip()
    max_pages = render.get("max_pages", 1)
    if template != "classic":
        _fail("$.render.template", "must equal classic")
    if paper_size not in {"letter", "a4"}:
        _fail("$.render.paper_size", "must be letter or a4")
    if density not in {"compact", "standard", "relaxed"}:
        _fail("$.render.density", "must be compact, standard, or relaxed")
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= 3:
        _fail("$.render.max_pages", "must be an integer from 1 to 3")
    document["render"] = {
        "template": template,
        "paper_size": paper_size,
        "max_pages": max_pages,
        "density": density,
    }
    return document


def document_warnings(document: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if len(document["sections"]) > 8:
        warnings.append("The resume has more than eight sections; consider consolidating it.")
    text = json.dumps(document, ensure_ascii=False)
    if text.count("**") % 2:
        warnings.append("The document contains an unmatched ** bold marker.")
    if any("\u4e00" <= character <= "\u9fff" for character in text):
        warnings.append("The classic v1 renderer supports English/Latin content only.")
    return warnings
