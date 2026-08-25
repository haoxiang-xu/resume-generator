# Resume Studio MCP v1

This MCP lets an AI choose resume section names, order, count, and content while a constrained renderer owns layout and PDF safety.

## Tools

- `resume_get_schema`: returns the versioned JSON Schema, supported layouts, and an example.
- `resume_validate`: validates and normalizes `resume.document.v1` without writing files.
- `resume_generate`: writes a collision-safe PDF, LaTeX source, and canonical JSON document inside the configured workspace.

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

## Sequence contract

### SEQ-001 - repeated generation

1. First valid call writes `<stem>.pdf/.tex/.json`.
2. A repeated call with the same arguments writes `<stem>-2.*`.
3. Validation is stateless and performs no writes.
4. Restart preserves collision behavior because the workspace files are the source of truth.
5. No retry or resume operation overwrites an existing artifact.

## Acceptance criteria

- AC-001: valid documents with AI-selected titles and mixed layouts generate searchable PDFs.
- AC-002: unknown fields and wrong schema versions fail at BC-001.
- AC-003: absolute paths and `..` escapes fail at BC-002.
- AC-004: repeated generation creates a numeric suffix and preserves the first artifact.
- AC-005: `resume_validate` performs no filesystem writes.
- AC-006: a real MCP stdio client can list and call all three tools.
- AC-007: the rendered PDF is visually inspected after every meaningful template change.

## Current renderer constraint

The v1 MCP uses `pdflatex` and supports English/Latin resume content. The development machine already has the engine. A distributable curated PuPu release must bundle a renderer instead of assuming a user-installed TeX distribution.
