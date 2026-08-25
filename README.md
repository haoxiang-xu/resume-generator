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

The profile has a revision, updated timestamp, SHA-256, a flexible `profile`
object, and its own flexible `invisible_context` object. A profile update is
preview-only by default. The AI must create both objects, show the diff
to the user, then call `resume_profile_update` again with `confirm=true` and the
same `expected_revision` after explicit approval. Stale revisions are rejected.

Example Profile bundle:

```json
{
  "basics": {"name": "Candidate Name"},
  "facts": [],
  "invisible_context": {
    "resume_preferences": {"target_page_count": 1},
    "additional_context": ["Context associated only with this Profile."]
  }
}
```

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

## Profile-owned invisible context

Every `embedded` or `hybrid` PDF contains a separate `shared_context.json`. This
file is generated from the selected Profile's `invisible_context`; there is no
tracked global default shared by unrelated Profiles. Creating or replacing a
stored Profile requires `invisible_context`, and the two are versioned together.

Use `resume_profile_get` to inspect the pair and `resume_profile_update` to update
both in one reviewed operation. `resume_shared_context_get` exposes the effective
PDF context; `resume_shared_context_update` remains an optional workspace override
for the current Profile. Context is marked `user-authored-metadata` and cannot
override user, host, developer, or system instructions. Keys that masquerade as
host prompts, along with credentials and identity secrets, are rejected.

`ai_context_mode=none` remains the explicit escape hatch and produces a PDF with
no `career_profile.json`, no `shared_context.json`, and no `/ActualText` bridge.

### Add Profile context from code

Put `invisible_context` directly in the Career Profile passed to Python, the CLI,
or MCP. The renderer removes that reserved field from `career_profile.json` and
writes it as the separately checksummed `shared_context.json` attachment.

Python callers can add or override code-level values directly:

```python
from resume_builder import compile_resume

result = compile_resume(
    resume_data,
    career_profile={
        "basics": {"name": "Candidate Name"},
        "invisible_context": {
            "target_role": "AI Engineer",
            "resume_preferences": {"target_page_count": 1},
        },
    },
)
```

The command line automatically reads the context from the Profile:

```bash
uv run resume-build resume.json output/resume.pdf \
  --career-profile profiles/my_profile.json
```

An optional code-managed or CLI override can still be layered over the Profile:

```text
RESUME_MCP_CODE_SHARED_CONTEXT_PATH=/absolute/path/to/shared_context.json
```

Values are recursively merged with this precedence:

```text
Profile invisible_context < code file / Python shared_context < workspace override
```

Objects merge recursively; arrays and scalar values are replaced by the higher
precedence layer. The embedded document records the Profile binding, context
SHA-256, and optional workspace override revision.
