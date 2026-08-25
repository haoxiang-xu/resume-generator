from __future__ import annotations

import argparse
import json
from pathlib import Path

from .compiler import BuildError, compile_resume


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an ATS-friendly LaTeX resume PDF.")
    parser.add_argument("input", type=Path, help="Resume JSON file")
    parser.add_argument("output", type=Path, help="Destination PDF file")
    args = parser.parse_args()

    data = json.loads(args.input.read_text(encoding="utf-8"))
    try:
        result = compile_resume(data)
    except BuildError as exc:
        parser.error(str(exc))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(result.pdf)
    print(f"Created {args.output}")


if __name__ == "__main__":
    main()
