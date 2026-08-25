from resume_builder.profile_context_zones import build_profile_context_zones


def _zones(profile: dict) -> dict:
    return build_profile_context_zones(
        profile,
        profile_sha256="a" * 64,
        revision=3,
    )


def test_every_experience_and_skill_receives_an_ai_only_context_zone() -> None:
    profile = {
        "experience": [
            {
                "organization": "First Employer",
                "details": ["Short fact", "A" * 180],
            },
            {"organization": "Second Employer", "details": ["Another fact"]},
        ],
        "skills": {
            "languages": ["Python", "Rust"],
            "platforms": ["AWS", "Kubernetes"],
        },
    }

    context = _zones(profile)
    indexed = {(zone["profile_path"], zone["kind"]): zone for zone in context["zones"]}

    assert ("$.experience[0]", "record") in indexed
    assert ("$.experience[1]", "record") in indexed
    assert ("$.experience[0].details[1]", "long_text") in indexed
    assert ("$.skills", "profile_section") in indexed
    assert ("$.skills.languages[0]", "content_item") in indexed
    assert ("$.skills.platforms[1]", "content_item") in indexed
    assert all(zone["default_visibility"] == "ai_only" for zone in context["zones"])
    assert all(zone["eligible_for_visible_resume"] is True for zone in context["zones"])


def test_arbitrary_long_text_outside_known_sections_receives_a_zone() -> None:
    profile = {
        "custom_material": {
            "case_study": "Detailed supporting material " * 20,
        }
    }

    context = _zones(profile)
    paths = {zone["profile_path"] for zone in context["zones"]}

    assert "$.custom_material" in paths
    assert "$.custom_material.case_study" in paths


def test_context_zone_index_is_deterministic_and_profile_bound() -> None:
    profile = {"experience": [{"organization": "Employer"}], "skills": ["Python"]}
    reordered_profile = {
        "skills": ["Python"],
        "experience": [{"organization": "Employer"}],
    }

    first = _zones(profile)
    second = _zones(profile)
    reordered = _zones(reordered_profile)
    changed = build_profile_context_zones(
        profile,
        profile_sha256="b" * 64,
        revision=3,
    )

    assert first == second
    assert first == reordered
    assert first["zones_sha256"] == second["zones_sha256"]
    assert first["zones"][0]["zone_id"] != changed["zones"][0]["zone_id"]
    assert first["policy"]["profile_collection_mode"] == "exhaustive"
    assert first["policy"]["visible_resume_mode"] == "selective"
    assert first["policy"]["page_budget_applies_to_profile"] is False
