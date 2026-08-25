# Resume Studio MCP v1

This MCP lets an AI choose resume section names, order, count, and content while a constrained renderer owns layout and PDF safety.

## Tools

- `resume_get_schema`: returns the versioned JSON Schema, supported layouts, and an example.
- `resume_validate`: validates and normalizes `resume.document.v1` without writing files.
- `resume_generate`: writes a collision-safe PDF, LaTeX source, and canonical JSON document inside the configured workspace.
- `resume_read_ai_context`: extracts and verifies the associated `career_profile.json` from a generated PDF.
- `resume_profile_get`: reads the revisioned workspace Career Profile.
- `resume_profile_update`: previews or explicitly commits a complete profile replacement.
- `resume_profile_validate`: validates proposed or stored profile data without writing.
- `resume_profile_search`: performs local transparent keyword search over profile values.
- `resume_shared_context_get`: reads workspace-wide metadata embedded in generated PDFs.
- `resume_shared_context_update`: previews or explicitly commits the shared context.
- `resume_generation_history`: returns recent generation provenance records.

## Boundary contracts

### BC-001 - AI/MCP resume document

- Producer: model composing the `resume_json` tool argument.
- Boundary: MCP stdio JSON-RPC tool call.
- Consumer: `resume_builder.flexible_schema.parse_document`.
- Canonical representation: UTF-8 JSON using `schema_version=resume.document.v1`.
- Admission: `VERSIONED`, with a closed key set at every v1 object boundary. Section titles and IDs are data, not schema extensions. Section layout is a closed enum.
- Unknown fields, unknown versions, duplicate IDs, mismatched layout fields, invalid paths, and oversize documents fail closed with a structured `{ok:false,error}` result.
- Runtime identity: the `resume-mcp` package and `resume.document.v1` schema version. PuPu transports the MCP result without becoming the schema validator.

### BC-002 - MCP/workspace file output

- Producer: `resume_generate`.
- Boundary: local filesystem beneath `RESUME_MCP_WORKSPACE_ROOT`.
- Consumer: user, PuPu, or another workspace tool reading the returned absolute paths.
- Canonical output: one PDF, one `.tex`, and one canonical `.json` sharing a collision-free filename stem.
- Admission: `CLOSED`. `output_directory` must be relative and resolve under the configured root; `filename` cannot contain a path.
- Existing files are never overwritten. Writes use a temporary sibling and atomic replace.
- Failure semantics: no successful result is returned unless all three output writes complete. A future transaction/cleanup refinement may remove earlier siblings if a later write fails.

### BC-003 - machine-readable career profile in PDF

- Producer: optional `career_profile_json` passed to `resume_generate`; when omitted, the normalized visible resume document is used.
- Boundary: PDF embedded-file name tree plus document-level `/AF`, XMP metadata, and optional marked content.
- Consumer: `resume_read_ai_context`, PDF attachment tools, or generic text extractors.
- Canonical representation: UTF-8 JSON object serialized with sorted keys and a trailing newline as `career_profile.json`, limited to 1,000,000 bytes.
- Integrity: the canonical profile SHA-256 is recorded in PDF metadata and XMP; the embedded-file checksum uses the PDF-defined MD5 field. Reads fail if the SHA-256 does not match.
- Modes: `none`, `embedded`, and `hybrid`. Embedded and hybrid carry both `career_profile.json` and `shared_context.json`. `hybrid` adds a short invisible `/ActualText` discovery bridge containing only attachment names and SHA-256 values, never the complete payloads.
- Accessibility warning: the bridge is intentionally experimental and is not claimed to conform to PDF/UA. Screen readers, copy/paste, ATS tools, and text extractors may expose it.
- Failure semantics: malformed JSON, non-object JSON, oversize profiles, unsupported modes, missing attachments, and integrity mismatches fail closed.

### BC-004 - workspace Career Profile memory

- Producer: user-approved `resume_profile_update` calls.
- Boundary: `.resume/career_profile.json` beneath `RESUME_MCP_WORKSPACE_ROOT`.
- Consumer: profile tools and `resume_generate` when no one-off profile is supplied.
- Canonical representation: `resume.career-profile.v1` JSON containing revision, UTC update timestamp, profile SHA-256, and a flexible profile object.
- Admission: profile JSON must be an object under 1,000,000 bytes. Reserved fact status and visibility values are closed enums. Credential and high-risk identity-secret keys fail closed.
- Mutation: `confirm=false` returns a diff without writing. `confirm=true` writes atomically only when `expected_revision` matches the current revision.
- Privacy: `.resume/` is ignored in this public source repository. Users may version it only in an appropriately protected workspace.
- Failure semantics: corrupt storage, checksum mismatch, stale revision, path escape through symlinks, invalid fact fields, and sensitive keys fail closed.

### BC-005 - generation provenance

- Producer: each successful `resume_generate` call.
- Boundary: append-only `.resume/history/generations.jsonl` inside the workspace.
- Consumer: `resume_generation_history`.
- Canonical entry: generation UUID, UTC timestamp, relative PDF path, PDF SHA-256, resume schema, section IDs, AI-context mode, embedded profile/shared-context SHA-256 values, their sources, and optional revisions.
- Failure semantics: history failure does not invalidate an already generated PDF; it is returned as a warning.

### BC-006 - workspace shared context

- Producer: the tracked profile-specific `resume_builder/default_shared_context.json`, an optional `RESUME_MCP_CODE_SHARED_CONTEXT_PATH` file or Python `shared_context` argument, and user-approved `resume_shared_context_update` calls.
- Boundary: package/code context plus `.resume/shared_context.json` beneath `RESUME_MCP_WORKSPACE_ROOT`, then the PDF associated file of the same name.
- Consumer: every embedded/hybrid `resume_generate` call and `resume_read_ai_context`.
- Canonical representation: `resume.shared-context.v1` JSON with revision, UTC timestamp, context SHA-256, explicit non-authoritative trust policy, and a flexible context object limited to 250,000 bytes.
- Composition: objects merge recursively with precedence `package default < code override < workspace`; arrays and scalar values replace lower layers. The embedded document records the code SHA-256 and workspace revision.
- Default: before workspace initialization, generation embeds Haoxiang Xu's package profile context as revision 0. `none` mode is the only explicit opt-out from hidden attachments.
- Mutation: preview by default; `confirm=true` plus matching `expected_revision` commits atomically.
- Safety: credential keys, identity-secret keys, and fields masquerading as system/developer prompts fail closed. Shared metadata cannot override host or user instructions.

## Sequence contract

### SEQ-001 - repeated generation

1. First valid call writes `<stem>.pdf/.tex/.json`.
2. A repeated call with the same arguments writes `<stem>-2.*`.
3. Validation is stateless and performs no writes.
4. Restart preserves collision behavior because the workspace files are the source of truth.
5. No retry or resume operation overwrites an existing artifact.

### SEQ-002 - profile update and generation

1. `resume_profile_get` returns revision 0 when no profile exists.
2. `resume_profile_update(confirm=false)` validates and previews revision 1 without writing.
3. After user approval, the same call with `confirm=true` atomically commits revision 1.
4. A later update must supply revision 1; stale revision 0 is rejected.
5. `resume_generate` embeds the saved profile record and logs revision 1 in generation history.

### SEQ-003 - shared context propagation

1. Before workspace initialization, `resume_shared_context_get` returns the effective code-composed revision 0 document.
2. Preview and confirmed update create revision 1 under `.resume/shared_context.json`.
3. Every subsequent embedded/hybrid PDF carries that exact revision as a separate associated file.
4. XMP, PDF metadata, `/ActualText`, generation history, and the MCP result expose its SHA-256 and revision.
5. `none` mode carries no hidden files and logs the shared context as disabled.

## Acceptance criteria

- AC-001: valid documents with AI-selected titles and mixed layouts generate searchable PDFs.
- AC-002: unknown fields and wrong schema versions fail at BC-001.
- AC-003: absolute paths and `..` escapes fail at BC-002.
- AC-004: repeated generation creates a numeric suffix and preserves the first artifact.
- AC-005: `resume_validate` performs no filesystem writes.
- AC-006: a real MCP stdio client can list and call all eleven tools.
- AC-007: the rendered PDF is visually inspected after every meaningful template change.
- AC-008: embedded and hybrid PDFs round-trip `career_profile.json` with a verified SHA-256.
- AC-009: hybrid is discoverable through `/ActualText`; embedded mode contains no `/ActualText` bridge.
- AC-010: the AI context layer does not change the rendered appearance of the resume.
- AC-011: profile preview performs no writes and commit requires explicit confirmation.
- AC-012: stale profile revisions and sensitive credential keys fail closed.
- AC-013: profile search returns matching JSON paths without external services.
- AC-014: generation automatically uses the saved profile and records its revision.
- AC-015: every embedded/hybrid PDF contains `shared_context.json`, including code-composed revision 0 before workspace initialization.
- AC-016: a committed shared-context revision propagates identically to repeated generations.
- AC-017: `none` mode omits both JSON attachments and the `/ActualText` bridge.
- AC-018: Python, CLI, and MCP code-file overrides merge over the tracked package default.
- AC-019: workspace shared context recursively overrides code-level values in the embedded document.

## Current renderer constraint

The v1 MCP uses `pdflatex` and supports English/Latin resume content. The development machine already has the engine. A distributable curated PuPu release must bundle a renderer instead of assuming a user-installed TeX distribution.
