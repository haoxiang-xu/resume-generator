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

The classic template currently targets English resumes and requires `pdflatex` (TinyTeX, MacTeX, or TeX Live).

## Run as a PuPu custom MCP

The MCP exposes `resume_get_schema`, `resume_validate`, and `resume_generate`.

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
