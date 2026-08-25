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

By default, the PDF uses `hybrid` AI context mode: it embeds a machine-readable
`career_profile.json`, writes discovery metadata, and adds a short experimental
`/ActualText` bridge. To embed a richer profile than the visible resume:

```bash
uv run resume-build example_resume.json output/resume.pdf \
  --career-profile full_career_profile.json \
  --ai-context-mode hybrid
```

Available modes:

- `none`: generate an ordinary PDF without machine-readable profile data.
- `embedded`: embed `career_profile.json` and XMP discovery metadata.
- `hybrid`: use `embedded` behavior plus a short `/ActualText` discovery bridge.

`/ActualText` is an experimental compatibility layer, not private storage. It
may be exposed by screen readers, copy/paste, text extractors, or ATS software.
Do not put secrets, false claims, or keyword stuffing in the career profile.

The classic template currently targets English resumes and requires `pdflatex` (TinyTeX, MacTeX, or TeX Live).

## Run as a PuPu custom MCP

The MCP exposes `resume_get_schema`, `resume_validate`, `resume_generate`, and
`resume_read_ai_context`.

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
profile from a generated PDF; use it instead of assuming a generic PDF reader
will discover attachments or accessibility replacement text.
