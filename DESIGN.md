# Resume Studio MCP v1

This MCP lets an AI choose resume section names, order, count, and content while a constrained renderer owns layout and PDF safety.

## Tools

- `resume_get_schema`: returns the versioned JSON Schema, supported layouts, and an example.
- `resume_validate`: validates and normalizes `resume.document.v1` without writing files.
- `resume_generate`: writes a collision-safe PDF, LaTeX source, and canonical JSON document inside the configured workspace.
- `resume_read_ai_context`: extracts and verifies the associated `career_profile.json` from a generated PDF.
- `resume_profile_get`: reads the revisioned workspace Career Profile.
- `resume_profile_update`: previews or explicitly commits a Profile; the app generates its watermark.
- `resume_profile_validate`: validates proposed or stored profile data without writing.
- `resume_profile_search`: performs local transparent keyword search over profile values.
- `resume_shared_context_get`: reads the current Profile's application-generated watermark.
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
- Canonical representation: `resume.career-profile.v3` JSON containing revision, UTC update timestamp, Profile SHA-256, a flexible profile object, and the application-generated watermark plus its SHA-256. v1/v2 records remain readable and receive a regenerated watermark in memory.
- Admission: Profile input must not contain the reserved `invisible_context` field. Profile JSON must be under 1,000,000 bytes. Reserved fact status and visibility values are closed enums. Credential and high-risk identity-secret keys fail closed.
- Mutation: `confirm=false` returns a diff without writing. `confirm=true` writes atomically only when `expected_revision` matches the current revision.
- Privacy: `.resume/` is ignored in this public source repository. Users may version it only in an appropriately protected workspace.
- Failure semantics: corrupt storage, checksum mismatch, stale revision, path escape through symlinks, invalid fact fields, and sensitive keys fail closed.

### BC-005 - generation provenance

- Producer: each successful `resume_generate` call.
- Boundary: append-only `.resume/history/generations.jsonl` inside the workspace.
- Consumer: `resume_generation_history`.
- Canonical entry: generation UUID, UTC timestamp, relative PDF path, PDF SHA-256, resume schema, section IDs, AI-context mode, embedded profile/shared-context SHA-256 values, their sources, and optional revisions.
- Failure semantics: history failure does not invalidate an already generated PDF; it is returned as a warning.

### BC-006 - application-managed Profile watermark

- Producer: `build_profile_invisible_context`, using the canonical selected Profile, its SHA-256, optional stored revision, and the current `resume_builder/watermark.json` object.
- Boundary: revisioned Profile record, then the PDF associated file `shared_context.json`.
- Consumer: every embedded/hybrid `resume_generate` call and `resume_read_ai_context`, which re-renders the embedded template against the embedded Profile before returning `verified`.
- Canonical representation: `resume.shared-context.v1` containing `resume.profile-watermark.v1`, watermark ID, bound Profile SHA-256/revision, detected owner, `generated_by`, purpose, `ai_editable=false`, and `resume.watermark-render.v1`. The render contains Profile-resolved JSON, per-placeholder bindings, and separate template/content/binding SHA-256 values.
- Composition: `[application_watermark, code_managed_watermark_file]`. There is no CLI flag, environment override, Python raw override, or MCP update tool; `resume_builder/watermark.json` is the only supported payload edit point.
- Default: generation without a saved Profile derives a revision-0 watermark from the visible normalized resume document. `none` mode is the explicit opt-out from all hidden attachments.
- Resolution: placeholders use `{{profile.<path>}}`. Common identity aliases and collection aliases are built in; arbitrary Profile paths are supported. Collection indexes are one-based and cycle with `((requested - 1) % count) + 1`; missing values produce deterministic Profile-bound markers.
- Mutation: Profile input containing `invisible_context` and Python callers supplying raw `shared_context` both fail closed. Stored v3 Profile-binding watermarks must exactly match regeneration from the stored Profile and revision. The file-controlled template is refreshed and re-rendered on load, so existing Profiles adopt later code-managed file changes.
- Safety: the watermark carries no host instructions and cannot override host or user instructions. It is tamper evidence, not a private-key digital signature, and its PDF attachment is extractable.

## Sequence contract

### SEQ-001 - repeated generation

1. First valid call writes `<stem>.pdf/.tex/.json`.
2. A repeated call with the same arguments writes `<stem>-2.*`.
3. Validation is stateless and performs no writes.
4. Restart preserves collision behavior because the workspace files are the source of truth.
5. No retry or resume operation overwrites an existing artifact.

### SEQ-002 - Profile update and watermark generation

1. `resume_profile_get` returns revision 0 when no profile exists.
2. `resume_profile_update(confirm=false)` rejects caller-supplied `invisible_context`, generates the revision-1 watermark, and previews both Profile and watermark hashes without writing.
3. After user approval, the same call with `confirm=true` atomically commits revision 1.
4. A later update must supply revision 1; stale revision 0 is rejected.
5. `resume_generate` embeds the saved Profile record as `career_profile.json`, its bound context as `shared_context.json`, and logs revision 1.

### SEQ-003 - shared context propagation

1. Profile creation generates and stores the watermark in the same revisioned Profile document.
2. `resume_shared_context_get` exposes the watermark read-only; no mutation tool is registered.
3. Every subsequent embedded/hybrid PDF carries that generated watermark as a separate associated file.
4. XMP, PDF metadata, `/ActualText`, generation history, and the MCP result expose its SHA-256 and revision.
5. `none` mode carries no hidden files and logs the shared context as disabled.

## Acceptance criteria

- AC-001: valid documents with AI-selected titles and mixed layouts generate searchable PDFs.
- AC-002: unknown fields and wrong schema versions fail at BC-001.
- AC-003: absolute paths and `..` escapes fail at BC-002.
- AC-004: repeated generation creates a numeric suffix and preserves the first artifact.
- AC-005: `resume_validate` performs no filesystem writes.
- AC-006: a real MCP stdio client can list and call all ten tools; no shared-context update tool is registered.
- AC-007: the rendered PDF is visually inspected after every meaningful template change.
- AC-008: embedded and hybrid PDFs round-trip `career_profile.json` with a verified SHA-256.
- AC-009: hybrid is discoverable through `/ActualText`; embedded mode contains no `/ActualText` bridge.
- AC-010: the AI context layer does not change the rendered appearance of the resume.
- AC-011: profile preview performs no writes and commit requires explicit confirmation.
- AC-012: stale profile revisions and sensitive credential keys fail closed.
- AC-013: profile search returns matching JSON paths without external services.
- AC-014: generation automatically uses the saved profile and records its revision.
- AC-015: every embedded/hybrid PDF contains a generated `shared_context.json` watermark; no saved Profile means a watermark derived from the visible document.
- AC-016: the same canonical Profile and revision produce the same watermark deterministically.
- AC-017: `none` mode omits both JSON attachments and the `/ActualText` bridge.
- AC-018: two different Profiles produce different generated watermark IDs and Profile hashes.
- AC-019: AI-authored `invisible_context`, Python raw overrides, and stored watermark tampering fail closed.
- AC-020: placeholder rendering is deterministic; every binding records its source, value digest, binding ID, and collection-cycle metadata.
- AC-021: an out-of-range one-based collection slot cycles by modulo, while an empty collection emits a Profile-bound missing marker.
- AC-022: new-format PDF verification re-renders the embedded template and rejects mismatched Profile hashes, revisions, rendered content, bindings, or checksums.

## Current renderer constraint

The v1 MCP uses `pdflatex` and supports English/Latin resume content. The development machine already has the engine. A distributable curated PuPu release must bundle a renderer instead of assuming a user-installed TeX distribution.
