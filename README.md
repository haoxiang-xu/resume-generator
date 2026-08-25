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
`career_profile.json` and its profile-specific `shared_context.json`, writes discovery
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
- `resume_shared_context_get` (read-only watermark inspection)
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
└── history/
    └── generations.jsonl
```

The profile has a revision, updated timestamp, SHA-256, a flexible `profile`
object, and an application-generated invisible watermark. A profile update is
preview-only by default. The AI supplies only the Career Profile, shows the diff
to the user, then calls `resume_profile_update` again with `confirm=true` and the
same `expected_revision` after explicit approval. Stale revisions are rejected.

Example Profile bundle:

```json
{
  "basics": {"name": "Candidate Name"},
  "facts": []
}
```

`invisible_context` is a reserved field. Profile input containing it is rejected;
Resume Studio derives the watermark from the canonical Profile SHA-256 and revision.

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

## Application-managed invisible watermark

Every `embedded` or `hybrid` PDF contains a separate `shared_context.json`. This
file contains a deterministic watermark generated from the selected Career
Profile. There is no tracked global default and no AI-authored context field.
Different Profiles therefore receive different watermark IDs and Profile hashes.

Use `resume_profile_get` or `resume_shared_context_get` to inspect the generated
watermark. There is deliberately no MCP mutation tool, CLI override, environment
override, or raw Python override. Stored v3 Profile records are rejected if their
watermark does not exactly match the value regenerated by the application.

`ai_context_mode=none` remains the explicit escape hatch and produces a PDF with
no `career_profile.json`, no `shared_context.json`, and no `/ActualText` bridge.

### Watermark generated from code

Python, CLI, and MCP callers pass only the Career Profile. Resume Studio generates
the separately checksummed `shared_context.json` watermark automatically:

Python callers provide the Career Profile directly:

```python
from resume_builder import compile_resume

result = compile_resume(
    resume_data,
    career_profile={
        "basics": {"name": "Candidate Name"},
    },
)
```

The command line does the same:

```bash
uv run resume-build resume.json output/resume.pdf \
  --career-profile profiles/my_profile.json
```

The watermark records its contract version, ID, bound Profile SHA-256, Profile
revision, detected owner, generator, purpose, and `ai_editable=false`. Supplying
`invisible_context` or a raw `shared_context` override fails closed.
