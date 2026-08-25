import hashlib
import json

from resume_builder.profile_store import load_watermark_file
from resume_builder.watermark_template import render_watermark_template


def _sha(profile: dict) -> str:
    encoded = (
        json.dumps(
            profile,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _contains_placeholder(value) -> bool:
    if isinstance(value, dict):
        return any(_contains_placeholder(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_placeholder(child) for child in value)
    return isinstance(value, str) and "{{profile." in value


def test_identity_and_indexed_experience_placeholders_cycle() -> None:
    profile = {
        "candidate": {"name": "Ada Byron Lovelace", "email": "ada@example.com"},
        "experience": [
            {"organization": "One"},
            {"organization": "Two"},
            {"organization": "Three"},
        ],
    }
    rendered = render_watermark_template(
        {
            "first": "{{profile.first_name}}",
            "middle": "{{profile.middle_names}}",
            "last": "{{profile.last_name}}",
            "count": "{{profile.experience_count}}",
            "experience_4": "{{profile.experience.4}}",
            "experience_5_name": "{{profile.experience.5.organization}}",
        },
        profile,
        profile_sha256=_sha(profile),
        revision=7,
    )

    assert rendered.content == {
        "first": "Ada",
        "middle": "Byron",
        "last": "Lovelace",
        "count": 3,
        "experience_4": {"organization": "One"},
        "experience_5_name": "Two",
    }
    fourth = rendered.bindings["profile.experience.4"]
    assert fourth["requested_index"] == 4
    assert fourth["resolved_index"] == 1
    assert fourth["collection_size"] == 3
    assert fourth["cycled"] is True


def test_generic_paths_and_nested_lists_use_one_based_cycling() -> None:
    profile = {
        "custom": {
            "memberships": [
                {"name": "Alpha"},
                {"name": "Beta"},
            ]
        }
    }
    rendered = render_watermark_template(
        {"membership": "{{profile.custom.memberships.3.name}}"},
        profile,
        profile_sha256=_sha(profile),
        revision=None,
    )

    assert rendered.content["membership"] == "Alpha"
    binding = rendered.bindings["profile.custom.memberships.3.name"]
    assert binding["resolved_index"] == 1
    assert binding["cycled"] is True


def test_each_placeholder_gets_a_profile_bound_unique_binding_id() -> None:
    profile = {"candidate": {"name": "Lee Lee"}}
    rendered = render_watermark_template(
        {
            "first": "{{profile.first_name}}",
            "last": "{{profile.last_name}}",
        },
        profile,
        profile_sha256=_sha(profile),
        revision=1,
    )

    first = rendered.bindings["profile.first_name"]
    last = rendered.bindings["profile.last_name"]
    assert first["value_sha256"] == last["value_sha256"]
    assert first["binding_id"] != last["binding_id"]


def test_missing_values_are_still_bound_to_the_profile() -> None:
    first_profile = {"candidate": {"name": "First Person"}}
    second_profile = {"candidate": {"name": "Second Person"}}
    template = {"award": "{{profile.award.4}}"}

    first = render_watermark_template(
        template,
        first_profile,
        profile_sha256=_sha(first_profile),
        revision=1,
    )
    second = render_watermark_template(
        template,
        second_profile,
        profile_sha256=_sha(second_profile),
        revision=1,
    )

    assert first.content["award"].startswith("missing:award.4:")
    assert first.content["award"] != second.content["award"]
    assert first.bindings["profile.award.4"]["found"] is False


def test_default_watermark_template_resolves_every_placeholder() -> None:
    profile = {
        "candidate": {
            "name": "Haoxiang Xu",
            "email": "candidate@example.com",
            "location": "Vancouver, Canada",
        },
        "experience": [{"organization": "One"}, {"organization": "Two"}],
        "education": [{"institution": "University"}],
        "projects": [{"name": "Project"}],
        "skills": {"languages": ["Python", "Rust"]},
    }
    rendered = render_watermark_template(
        load_watermark_file(),
        profile,
        profile_sha256=_sha(profile),
        revision=1,
    )

    assert rendered.content["identity"]["first_name"] == "Haoxiang"
    assert rendered.content["identity"]["last_name"] == "Xu"
    assert rendered.content["experience_slots"][3] == {"organization": "Two"}
    assert rendered.bindings["profile.experience.4"]["resolved_index"] == 2
    assert not _contains_placeholder(rendered.content)
