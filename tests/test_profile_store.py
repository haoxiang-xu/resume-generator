import json

import pytest

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
    load_profile,
    profile_path,
    search_profile,
    update_profile,
)


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
    assert not profile_path(tmp_path).exists()

    record, _ = update_profile(tmp_path, profile_json, 0, confirm=True)

    assert record is not None
    assert record.revision == 1
    assert load_profile(tmp_path) == record


def test_profile_update_rejects_stale_revision(tmp_path) -> None:
    profile_json = json.dumps(PROFILE)
    update_profile(tmp_path, profile_json, 0, confirm=True)

    with pytest.raises(ProfileStoreError, match="revision conflict"):
        update_profile(tmp_path, profile_json, 0, confirm=True)


def test_profile_path_rejects_symlink_escape(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (workspace / ".resume").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ProfileStoreError, match="escapes"):
        profile_path(workspace)


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
    assert extracted["career_profile"]["schema_version"] == PROFILE_SCHEMA_VERSION
    assert extracted["career_profile"]["revision"] == 1
    assert extracted["career_profile"]["profile"] == PROFILE

    search_result = profile_search_payload("applied AI", 5)
    assert search_result["ok"] is True
    assert search_result["match_count"] == 1

    history = generation_history_payload(10)
    assert history["ok"] is True
    assert history["count"] == 1
    assert history["generations"][0]["generation_id"] == generated["generation_id"]
    assert history["generations"][0]["profile_revision"] == 1
