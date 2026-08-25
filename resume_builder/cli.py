from __future__ import annotations

import argparse
import json
from pathlib import Path

from .ai_context import AI_CONTEXT_MODES, DEFAULT_AI_CONTEXT_MODE, AIContextError, parse_profile_json
from .compiler import BuildError, compile_resume
from .shared_context_store import SharedContextStoreError, parse_shared_context


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an ATS-friendly LaTeX resume PDF.")
    parser.add_argument("input", type=Path, help="Resume JSON file")
    parser.add_argument("output", type=Path, help="Destination PDF file")
    parser.add_argument(
        "--career-profile",
        type=Path,
        help="Optional extended career profile JSON to embed inside the PDF",
    )
    parser.add_argument(
        "--ai-context-mode",
        choices=AI_CONTEXT_MODES,
        default=DEFAULT_AI_CONTEXT_MODE,
        help="PDF machine-readable context mode (default: hybrid)",
    )
    parser.add_argument(
        "--shared-context",
        type=Path,
        help="Optional JSON override merged over the Career Profile's invisible_context",
    )
    args = parser.parse_args()

    data = json.loads(args.input.read_text(encoding="utf-8"))
    try:
        career_profile = (
            parse_profile_json(args.career_profile.read_text(encoding="utf-8"))
            if args.career_profile
            else data
        )
        shared_context = (
            parse_shared_context(args.shared_context.read_text(encoding="utf-8"))
            if args.shared_context
            else None
        )
        result = compile_resume(
            data,
            career_profile=career_profile,
            shared_context=shared_context,
            ai_context_mode=args.ai_context_mode,
        )
    except (AIContextError, SharedContextStoreError, BuildError, OSError) as exc:
        parser.error(str(exc))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(result.pdf)
    print(f"Created {args.output}")
    if result.ai_context.filename:
        print(
            f"Embedded {result.ai_context.filename} "
            f"({result.ai_context.profile_size} bytes, mode={result.ai_context.mode})"
        )
    if result.ai_context.shared_context_filename:
        print(
            f"Embedded {result.ai_context.shared_context_filename} "
            f"({result.ai_context.shared_context_size} bytes)"
        )


if __name__ == "__main__":
    main()
