import hashlib
import json

import pytest

import resume_builder.profile_store as profile_store
from resume_builder.ai_context import canonical_profile_bytes
from resume_builder.flexible_schema import EXAMPLE_DOCUMENT
from resume_builder.mcp_server import (
    generate_payload,
    generation_history_payload,
    profile_get_payload,
    profile_search_payload,
    profile_update_payload,
    read_ai_context_payload,
)
from resume_builder.profile_store import (
    PROFILE_SCHEMA_VERSION,
    ProfileStoreError,
    load_watermark_file,
    load_profile,
    profile_path,
    search_profile,
    update_profile,
    verify_profile_watermark,
)
from resume_builder.shared_context_store import shared_context_document


PROFILE = {
    "basics": {"name": "Haoxiang Xu", "location": "Vancouver, Canada"},
    "facts": [
        {
            "id": "sap-rag-rearchitecture",
            "category": "experience",
            "content": "Re-architected an LLM retrieval system across 30+ related tables.",
            "status": "verified",
            "visibility": "public",
            "source": "user-confirmed",
        },
        {
            "id": "private-career-goal",
            "category": "preference",
            "content": "Interested in applied AI platform roles.",
            "status": "draft",
            "visibility": "private",
        },
    ],
}


def test_profile_update_previews_before_commit(tmp_path) -> None:
    profile_json = json.dumps(PROFILE)

    record, preview = update_profile(tmp_path, profile_json, 0, confirm=False)

    assert record is None
    assert preview["next_revision"] == 1
    assert preview["added_top_level_keys"] == ["basics", "facts"]
    assert preview["invisible_context_added_top_level_keys"] == [
        "profile_context_zones",
        "watermark",
        "watermark_file",
    ]
    assert preview["watermark"]["ai_editable"] is False
    assert not profile_path(tmp_path).exists()

    record, _ = update_profile(tmp_path, profile_json, 0, confirm=True)

    assert record is not None
    assert record.revision == 1
    assert record.invisible_context["watermark"]["profile_owner"] == "Haoxiang Xu"
    assert record.invisible_context["watermark"]["profile_revision"] == 1
    zones = record.invisible_context["profile_context_zones"]
    assert zones["policy"]["profile_collection_mode"] == "exhaustive"
    assert zones["policy"]["visible_resume_mode"] == "selective"
    assert load_profile(tmp_path) == record


def test_profile_update_rejects_stale_revision(tmp_path) -> None:
    profile_json = json.dumps(PROFILE)
    update_profile(tmp_path, profile_json, 0, confirm=True)

    with pytest.raises(ProfileStoreError, match="revision conflict"):
        update_profile(tmp_path, profile_json, 0, confirm=True)


def test_profile_rejects_ai_supplied_invisible_context(tmp_path) -> None:
    profile_with_context = dict(PROFILE)
    profile_with_context["invisible_context"] = {"notes": "AI-authored"}

    with pytest.raises(ProfileStoreError, match="application-generated"):
        update_profile(
            tmp_path,
            json.dumps(profile_with_context),
            0,
            confirm=False,
        )


def test_profile_watermark_is_deterministic(tmp_path) -> None:
    first, _ = update_profile(tmp_path, json.dumps(PROFILE), 0, confirm=True)
    assert first is not None

    second_workspace = tmp_path / "second"
    second, _ = update_profile(second_workspace, json.dumps(PROFILE), 0, confirm=True)
    assert second is not None

    assert first.invisible_context == second.invisible_context


def test_stored_profile_rejects_watermark_tampering(tmp_path) -> None:
    record, _ = update_profile(tmp_path, json.dumps(PROFILE), 0, confirm=True)
    assert record is not None
    destination = profile_path(tmp_path)
    stored = json.loads(destination.read_text(encoding="utf-8"))
    stored["invisible_context"]["watermark"]["ai_editable"] = True
    stored["invisible_context_sha256"] = hashlib.sha256(
        canonical_profile_bytes(stored["invisible_context"])
    ).hexdigest()
    destination.write_text(json.dumps(stored), encoding="utf-8")

    with pytest.raises(ProfileStoreError, match="application-generated watermark"):
        load_profile(tmp_path)


def test_watermark_file_is_included_with_its_sha256(tmp_path) -> None:
    source = tmp_path / "watermark.json"
    payload = {"verification": {"manual": ["Check supporting evidence."]}}
    source.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_watermark_file(source)

    assert loaded == payload


def test_existing_profile_picks_up_current_watermark_file(tmp_path, monkeypatch) -> None:
    source = tmp_path / "watermark.json"
    workspace = tmp_path / "workspace"
    source.write_text(json.dumps({"version": 1}), encoding="utf-8")
    monkeypatch.setattr(profile_store, "WATERMARK_FILE_PATH", source)
    created, _ = update_profile(workspace, json.dumps(PROFILE), 0, confirm=True)
    assert created is not None
    assert created.invisible_context["watermark_file"]["content"] == {"version": 1}

    source.write_text(json.dumps({"version": 2, "notes": ["current"]}), encoding="utf-8")
    loaded = load_profile(workspace)

    assert loaded is not None
    assert loaded.invisible_context["watermark_file"]["content"] == {
        "version": 2,
        "notes": ["current"],
    }
    expected = hashlib.sha256(
        canonical_profile_bytes({"version": 2, "notes": ["current"]})
    ).hexdigest()
    assert loaded.invisible_context["watermark_file"]["sha256"] == expected


@pytest.mark.parametrize("content", ["[]", "not-json"])
def test_watermark_file_must_contain_a_json_object(tmp_path, content) -> None:
    source = tmp_path / "watermark.json"
    source.write_text(content, encoding="utf-8")

    with pytest.raises(ProfileStoreError, match="watermark"):
        load_watermark_file(source)


def test_profile_watermark_verifier_detects_rendered_content_tampering(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "watermark.json"
    source.write_text(
        json.dumps({"name": "{{profile.full_name}}", "job": "{{profile.experience.2}}"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(profile_store, "WATERMARK_FILE_PATH", source)
    profile = {
        "candidate": {"name": "Verified Candidate"},
        "experience": [{"organization": "Only Employer"}],
    }
    context = profile_store.build_profile_invisible_context(profile, 1)
    shared = shared_context_document(context, profile_revision=1)
    embedded_profile = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "revision": 1,
        "updated_at": "2026-08-25T00:00:00Z",
        "profile": profile,
    }

    verified = verify_profile_watermark(embedded_profile, shared)
    assert verified["status"] == "verified"
    assert verified["binding_count"] == 2
    assert verified["profile_context_zones_status"] == "verified"

    shared["context"]["watermark_file"]["content"]["name"] = "Forged Candidate"
    with pytest.raises(ProfileStoreError, match="rendered_content"):
        verify_profile_watermark(embedded_profile, shared)


def test_profile_watermark_verifier_detects_context_zone_tampering(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "watermark.json"
    source.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(profile_store, "WATERMARK_FILE_PATH", source)
    profile = {"experience": [{"organization": "Verified Employer"}]}
    context = profile_store.build_profile_invisible_context(profile, 1)
    shared = shared_context_document(context, profile_revision=1)
    embedded_profile = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "revision": 1,
        "updated_at": "2026-08-25T00:00:00Z",
        "profile": profile,
    }

    shared["context"]["profile_context_zones"]["zones"][0]["profile_path"] = "$.forged"

    with pytest.raises(ProfileStoreError, match="context-zone verification"):
        verify_profile_watermark(embedded_profile, shared)


def test_profile_path_rejects_symlink_escape(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (workspace / ".resume").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ProfileStoreError, match="escapes"):
        profile_path(workspace)


def test_legacy_profile_loads_with_regenerated_watermark(tmp_path) -> None:
    destination = profile_path(tmp_path)
    destination.parent.mkdir(parents=True)
    destination.write_text(
        json.dumps(
            {
                "schema_version": "resume.career-profile.v1",
                "revision": 1,
                "updated_at": "2026-08-24T00:00:00Z",
                "profile": {"basics": {"name": "Legacy Candidate"}},
            }
        ),
        encoding="utf-8",
    )

    record = load_profile(tmp_path)

    assert record is not None
    assert record.invisible_context["watermark"]["profile_owner"] == "Legacy Candidate"
    assert record.invisible_context["watermark"]["profile_revision"] == 1


def test_profile_rejects_sensitive_credentials(tmp_path) -> None:
    profile_json = json.dumps({"api_key": "do-not-store-this"})

    with pytest.raises(ProfileStoreError, match="sensitive credential"):
        update_profile(tmp_path, profile_json, 0, confirm=False)


def test_profile_search_returns_json_paths(tmp_path) -> None:
    record, _ = update_profile(tmp_path, json.dumps(PROFILE), 0, confirm=True)
    assert record is not None

    matches = search_profile(record, "LLM retrieval", 10)

    assert matches
    assert matches[0]["path"] == "$.facts[0].content"
    assert "30+ related tables" in matches[0]["value"]


def test_mcp_profile_round_trip_and_generation_history(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESUME_MCP_WORKSPACE_ROOT", str(tmp_path))
    profile_json = json.dumps(PROFILE)

    preview = profile_update_payload(profile_json, expected_revision=0, confirm=False)
    assert preview["ok"] is True
    assert preview["committed"] is False
    assert profile_get_payload()["found"] is False

    committed = profile_update_payload(profile_json, expected_revision=0, confirm=True)
    assert committed["ok"] is True
    assert committed["record"]["schema_version"] == PROFILE_SCHEMA_VERSION
    assert committed["record"]["revision"] == 1

    generated = generate_payload(
        json.dumps(EXAMPLE_DOCUMENT),
        "memory-resume",
        "artifacts",
    )
    assert generated["ok"] is True
    assert generated["profile_source"] == "workspace"
    assert generated["profile_revision"] == 1
    assert any("draft fact" in warning for warning in generated["warnings"])
    assert any("private fact" in warning for warning in generated["warnings"])

    extracted = read_ai_context_payload(generated["pdf_path"])
    assert extracted["ok"] is True
    assert extracted["watermark_verification"]["status"] == "verified"
    assert extracted["career_profile"]["schema_version"] == PROFILE_SCHEMA_VERSION
    assert extracted["career_profile"]["revision"] == 1
    assert extracted["career_profile"]["profile"] == PROFILE
    assert extracted["career_profile"]["invisible_context_attachment"] == "shared_context.json"
    assert extracted["career_profile"]["invisible_context_sha256"]
    assert extracted["shared_context"]["context"]["watermark"]["ai_editable"] is False
    assert extracted["shared_context"]["context"]["watermark"]["profile_revision"] == 1
    assert generated["shared_context_source"] == "application_watermark"

    search_result = profile_search_payload("applied AI", 5)
    assert search_result["ok"] is True
    assert search_result["match_count"] == 1

    history = generation_history_payload(10)
    assert history["ok"] is True
    assert history["count"] == 1
    assert history["generations"][0]["generation_id"] == generated["generation_id"]
    assert history["generations"][0]["profile_revision"] == 1
