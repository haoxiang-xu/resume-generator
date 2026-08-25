from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .ai_context import (
    DEFAULT_AI_CONTEXT_MODE,
    AIContextError,
    AIContextManifest,
    add_ai_context,
)


TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"


class BuildError(RuntimeError):
    """Raised when the resume data cannot be rendered or compiled."""


@dataclass(frozen=True)
class BuildResult:
    pdf: bytes
    tex: str
    log: str
    ai_context: AIContextManifest


LATEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def latex_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\u2013", "--").replace("\u2014", "---").replace("\u2011", "-")
    return "".join(LATEX_ESCAPES.get(char, char) for char in text)


def latex_inline(value: Any) -> str:
    """Convert a tiny, safe subset of Markdown: **text** becomes bold."""
    text = "" if value is None else str(value)
    parts = text.split("**")
    rendered: list[str] = []
    for index, part in enumerate(parts):
        escaped = _link_urls(part)
        rendered.append(rf"\textbf{{{escaped}}}" if index % 2 else escaped)
    return "".join(rendered)


def _link_urls(text: str) -> str:
    url_pattern = re.compile(r"https?://[^\s]+")
    rendered: list[str] = []
    cursor = 0
    for match in url_pattern.finditer(text):
        rendered.append(latex_escape(text[cursor : match.start()]))
        display = match.group(0).rstrip(".,;)")
        trailing = match.group(0)[len(display) :]
        rendered.append(rf"\href{{{latex_url(display)}}}{{{latex_escape(display)}}}")
        rendered.append(latex_escape(trailing))
        cursor = match.end()
    rendered.append(latex_escape(text[cursor:]))
    return "".join(rendered)


def latex_url(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    # URL arguments still need TeX's comment and parameter characters escaped.
    return (
        text.replace("%", r"\%")
        .replace("#", r"\#")
        .replace("_", r"\_")
        .replace("&", r"\&")
    )


def link_target(value: Any, kind: str = "") -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    if kind == "email" and not text.startswith("mailto:"):
        return f"mailto:{text}"
    if kind == "phone" and not text.startswith("tel:"):
        normalized = re.sub(r"[^+0-9]", "", text)
        return f"tel:{normalized}"
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
        return f"https://{text}"
    return text


def _environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        undefined=StrictUndefined,
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        block_start_string="((*",
        block_end_string="*))",
        variable_start_string="((",
        variable_end_string="))",
        comment_start_string="((#",
        comment_end_string="#))",
    )
    env.filters["tex"] = latex_escape
    env.filters["inline"] = latex_inline
    env.filters["url"] = latex_url
    env.globals["link_target"] = link_target
    return env


def validate_resume(data: dict[str, Any]) -> None:
    if not str(data.get("basics", {}).get("name", "")).strip():
        raise BuildError("请先填写姓名。")

    serialized = str(data)
    if any("\u4e00" <= char <= "\u9fff" for char in serialized):
        raise BuildError("当前经典模板面向英文简历，正文暂不支持中文字符。界面可以使用中文。")


def render_latex(data: dict[str, Any], template_name: str = "classic.tex.j2") -> str:
    validate_resume(data)
    template = _environment().get_template(template_name)
    return template.render(resume=data)


def compile_resume(
    data: dict[str, Any],
    template_name: str = "classic.tex.j2",
    *,
    career_profile: dict[str, Any] | None = None,
    ai_context_mode: str = DEFAULT_AI_CONTEXT_MODE,
) -> BuildResult:
    tex = render_latex(data, template_name=template_name)
    engine = shutil.which("pdflatex")
    if not engine:
        raise BuildError("没有找到 pdflatex。请先安装 TinyTeX、MacTeX 或 TeX Live。")

    with tempfile.TemporaryDirectory(prefix="resume-studio-") as temp_name:
        temp_dir = Path(temp_name)
        tex_path = temp_dir / "resume.tex"
        tex_path.write_text(tex, encoding="utf-8")

        command = [
            engine,
            "-no-shell-escape",
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-file-line-error",
            f"-output-directory={temp_dir}",
            str(tex_path),
        ]
        logs: list[str] = []
        for _ in range(2):
            try:
                completed = subprocess.run(
                    command,
                    cwd=temp_dir,
                    capture_output=True,
                    text=True,
                    timeout=45,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise BuildError("LaTeX 编译超时。") from exc
            logs.append(completed.stdout + completed.stderr)
            if completed.returncode != 0:
                tail = "\n".join(logs[-1].splitlines()[-35:])
                raise BuildError(f"LaTeX 编译失败：\n{tail}")

        pdf_path = temp_dir / "resume.pdf"
        if not pdf_path.exists():
            raise BuildError("LaTeX 已运行，但没有生成 PDF。")
        try:
            pdf, ai_context = add_ai_context(
                pdf_path.read_bytes(),
                career_profile if career_profile is not None else data,
                ai_context_mode,
            )
        except AIContextError as exc:
            raise BuildError(f"无法写入 PDF AI 上下文：{exc}") from exc
        return BuildResult(pdf=pdf, tex=tex, log="\n".join(logs), ai_context=ai_context)
