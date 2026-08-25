# LaTeX Resume Studio

A local Python app for editing structured resume content and compiling it into a real, searchable, ATS-friendly LaTeX PDF.

## Run the app

```bash
git clone https://github.com/haoxiang-xu/resume-generator.git
cd resume-generator
uv sync
uv run streamlit run app.py
```

The app opens in your browser. Edit each section, click **Generate PDF**, preview the result, and download the PDF or its JSON source.

## Build from the command line

```bash
uv run resume-build example_resume.json output/resume.pdf
```

By default, the PDF uses `hybrid` AI context mode: it embeds machine-readable
`career_profile.json` and workspace-wide `shared_context.json`, writes discovery
metadata, and adds a short experimental `/ActualText` bridge. To embed a richer
profile than the visible resume:

```bash
uv run resume-build example_resume.json output/resume.pdf \
  --career-profile full_career_profile.json \
  --ai-context-mode hybrid
```

Available modes:

- `none`: generate an ordinary PDF without either hidden JSON attachment.
- `embedded`: embed `career_profile.json`, `shared_context.json`, and XMP metadata.
- `hybrid`: use `embedded` behavior plus a short `/ActualText` discovery bridge.

`/ActualText` is an experimental compatibility layer, not private storage. It
may be exposed by screen readers, copy/paste, text extractors, or ATS software.
Do not put secrets, false claims, or keyword stuffing in the career profile.

The classic template currently targets English resumes and requires `pdflatex` (TinyTeX, MacTeX, or TeX Live).

## Run as a PuPu custom MCP

The MCP exposes resume generation, PDF AI-context reading, and a transparent
workspace Career Profile:

- `resume_get_schema` and `resume_validate`
- `resume_profile_get`, `resume_profile_validate`, `resume_profile_search`
- `resume_profile_update`
- `resume_shared_context_get` and `resume_shared_context_update`
- `resume_generate` and `resume_read_ai_context`
- `resume_generation_history`

```bash
cd resume-generator
uv sync
RESUME_MCP_WORKSPACE_ROOT=/absolute/path/to/workspace uv run resume-mcp
```

In PuPu, add a custom stdio MCP with:

- Name: `Resume Studio`
- Command: `/opt/homebrew/bin/uv` (or the absolute path returned by `which uv`)
- Arguments: `--directory /absolute/path/to/resume_app run resume-mcp`
- Environment: `RESUME_MCP_WORKSPACE_ROOT=/absolute/path/to/workspace`

The AI is free to choose section names, order, and count. It chooses one constrained layout per section so the renderer remains deterministic and safe. See `DESIGN.md` for the versioned boundary contract.

`resume_generate` accepts optional `career_profile_json` and `ai_context_mode`
arguments. `resume_read_ai_context` reliably extracts and verifies the embedded
profile and shared context from a generated PDF; use it instead of assuming a
generic PDF reader will discover attachments or accessibility replacement text.

## Workspace Career Profile

The MCP stores durable memory as a user-visible JSON document, not opaque chat
memory:

```text
.resume/
├── career_profile.json
├── shared_context.json
└── history/
    └── generations.jsonl
```

The profile has a revision, updated timestamp, SHA-256, and a flexible `profile`
object. A profile update is preview-only by default. The AI must show the diff
to the user, then call `resume_profile_update` again with `confirm=true` and the
same `expected_revision` after explicit approval. Stale revisions are rejected.

When `career_profile_json` is omitted from `resume_generate`, the saved workspace
profile is embedded automatically. If no profile exists yet, generation falls
back to the visible resume document. Each successful generation records the PDF
hash, section IDs, AI-context mode, profile source, and profile revision.

Optional `facts` entries can use:

- `status`: `verified`, `draft`, or `archived`
- `visibility`: `public`, `ai_only`, or `private`

Draft and private facts produce warnings and should not appear as claims in the
visible resume. Credential and identity-secret keys such as passwords, API keys,
access tokens, private keys, and SSNs are rejected.

The `.resume/` directory is ignored by this repository to prevent accidental
publication of personal data. If you version it, use a private repository with
appropriate access controls.

## Workspace-wide hidden context

Every `embedded` or `hybrid` PDF contains a separate `shared_context.json`. This
is the common machine-readable area shared by every resume generated from the
workspace. Before workspace initialization it contains the package code defaults
as revision 0, so the attachment contract is stable from the first PDF onward.

Use `resume_shared_context_get` to inspect it and `resume_shared_context_update`
to preview or explicitly commit changes. Typical contents include default resume
language, page-limit preferences, provenance, and non-authoritative usage notes.
The context is marked `user-authored-metadata` and cannot override user, host,
developer, or system instructions. Keys that masquerade as host prompts, along
with credentials and identity secrets, are rejected.

`ai_context_mode=none` remains the explicit escape hatch and produces a PDF with
no `career_profile.json`, no `shared_context.json`, and no `/ActualText` bridge.

### Add shared context from code

The tracked package default lives at:

```text
resume_builder/default_shared_context.json
```

This repository uses that file as Haoxiang Xu's profile-specific baseline. Every
generated PDF receives the same career focus, resume preferences, and known fact
notes unless a higher-precedence layer overrides them. Keep secrets out of it:
the file is part of the source repository and Python package.

Python callers can add or override code-level values directly:

```python
from resume_builder import compile_resume

result = compile_resume(
    resume_data,
    shared_context={
        "profile": {"target_role": "AI Engineer"},
        "resume_preferences": {"target_page_count": 1},
    },
)
```

The command line accepts a JSON override:

```bash
uv run resume-build resume.json output/resume.pdf \
  --shared-context config/my_shared_context.json
```

For MCP hosts, point to an additional code-managed JSON file:

```text
RESUME_MCP_CODE_SHARED_CONTEXT_PATH=/absolute/path/to/shared_context.json
```

Values are recursively merged with this precedence:

```text
package default < code file / Python argument < workspace .resume/shared_context.json
```

Objects merge recursively; arrays and scalar values are replaced by the higher
precedence layer. The embedded document records the code-context SHA-256 and
workspace revision used to compose it.
