from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import streamlit as st

from resume_builder import AI_CONTEXT_MODES, BuildError, compile_resume


ROOT = Path(__file__).resolve().parent
EXAMPLE = json.loads((ROOT / "example_resume.json").read_text(encoding="utf-8"))


st.set_page_config(page_title="LaTeX Resume Studio", page_icon="📄", layout="wide")


def clear_editor_widgets(prefix: str = "edit_") -> None:
    for key in list(st.session_state):
        if str(key).startswith(prefix):
            del st.session_state[key]


def reset_resume(data: dict[str, Any]) -> None:
    st.session_state.resume = copy.deepcopy(data)
    st.session_state.pop("pdf_result", None)
    clear_editor_widgets()


if "resume" not in st.session_state:
    reset_resume(EXAMPLE)


def text_field(
    obj: dict[str, Any],
    field: str,
    label: str,
    key: str,
    placeholder: str = "",
) -> None:
    obj[field] = st.text_input(
        label,
        value=str(obj.get(field, "")),
        key=f"edit_{key}_{field}",
        placeholder=placeholder,
    )


def bullet_field(obj: dict[str, Any], field: str, key: str) -> None:
    current = "\n\n".join(obj.get(field, []))
    value = st.text_area(
        "要点（每段一个 bullet；用 **文字** 加粗）",
        value=current,
        height=180,
        key=f"edit_{key}_{field}",
    )
    obj[field] = [part.strip() for part in value.split("\n\n") if part.strip()]


def remove_item(section: str, index: int) -> None:
    st.session_state.resume[section].pop(index)
    clear_editor_widgets(f"edit_{section}_")
    st.session_state.pop("pdf_result", None)
    st.rerun()


st.title("LaTeX Resume Studio")
st.caption("结构化编辑内容，生成真正可搜索、可点击链接、ATS 友好的 LaTeX PDF。")

with st.sidebar:
    st.subheader("文件")
    uploaded = st.file_uploader("导入简历 JSON", type=["json"])
    if st.button("载入 JSON", use_container_width=True, disabled=uploaded is None):
        try:
            imported = json.loads(uploaded.getvalue().decode("utf-8"))
            if not isinstance(imported, dict) or "basics" not in imported:
                raise ValueError("JSON 缺少 basics 字段")
            reset_resume(imported)
            st.rerun()
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            st.error(f"无法载入：{exc}")

    st.download_button(
        "导出当前 JSON",
        data=json.dumps(st.session_state.resume, ensure_ascii=False, indent=2),
        file_name="resume.json",
        mime="application/json",
        use_container_width=True,
    )
    if st.button("恢复示例内容", use_container_width=True):
        reset_resume(EXAMPLE)
        st.rerun()

    st.divider()
    st.subheader("AI 上下文")
    ai_context_mode = st.selectbox(
        "PDF 上下文模式",
        AI_CONTEXT_MODES,
        index=AI_CONTEXT_MODES.index("hybrid"),
        help="hybrid 会嵌入 JSON、写入 XMP，并添加一段实验性的 ActualText 发现提示。",
    )
    profile_upload = st.file_uploader(
        "扩展职业档案（可选）",
        type=["json"],
        key="career_profile_upload",
        help="未提供时，将当前简历 JSON 作为 career_profile.json 嵌入。",
    )
    st.caption("ActualText 可能被屏幕阅读器或复制操作读出，不应放入虚假或敏感信息。")

    st.divider()
    st.info("当前经典模板适合英文技术简历。PDF 在本机生成，内容不会上传到外部服务。")


resume = st.session_state.resume
tabs = st.tabs(["基本信息", "教育", "经历", "项目", "技能"])

with tabs[0]:
    st.subheader("基本信息")
    basics = resume.setdefault("basics", {})
    left, right = st.columns(2)
    with left:
        text_field(basics, "name", "姓名", "basics", "Jane Doe")
        text_field(basics, "email", "邮箱", "basics", "jane@example.com")
        text_field(basics, "phone", "电话", "basics", "+1-604-555-0100")
    with right:
        text_field(basics, "linkedin", "LinkedIn", "basics", "linkedin.com/in/jane")
        text_field(basics, "github", "GitHub", "basics", "github.com/jane")
        text_field(basics, "website", "个人网站（可选）", "basics", "janedoe.dev")

with tabs[1]:
    st.subheader("教育经历")
    education = resume.setdefault("education", [])
    for index, item in enumerate(education):
        with st.expander(item.get("institution") or f"教育经历 {index + 1}", expanded=index == 0):
            left, right = st.columns(2)
            with left:
                text_field(item, "institution", "学校", f"education_{index}")
                text_field(item, "degree", "学位 / 专业 / GPA", f"education_{index}")
            with right:
                text_field(item, "location", "地点", f"education_{index}")
                text_field(item, "date", "日期", f"education_{index}")
            bullet_field(item, "details", f"education_{index}")
            if st.button("删除这条教育经历", key=f"remove_education_{index}"):
                remove_item("education", index)
    if st.button("＋ 添加教育经历"):
        education.append({"institution": "", "location": "", "degree": "", "date": "", "details": []})
        st.rerun()

with tabs[2]:
    st.subheader("工作与研究经历")
    experience = resume.setdefault("experience", [])
    for index, item in enumerate(experience):
        with st.expander(item.get("company") or f"经历 {index + 1}", expanded=index == 0):
            left, right = st.columns(2)
            with left:
                text_field(item, "company", "公司 / 机构", f"experience_{index}")
                text_field(item, "role", "职位", f"experience_{index}")
            with right:
                text_field(item, "location", "地点", f"experience_{index}")
                text_field(item, "date", "日期", f"experience_{index}")
            bullet_field(item, "bullets", f"experience_{index}")
            if st.button("删除这条经历", key=f"remove_experience_{index}"):
                remove_item("experience", index)
    if st.button("＋ 添加经历"):
        experience.append({"company": "", "location": "", "role": "", "date": "", "bullets": []})
        st.rerun()

with tabs[3]:
    st.subheader("项目")
    projects = resume.setdefault("projects", [])
    for index, item in enumerate(projects):
        with st.expander(item.get("name") or f"项目 {index + 1}", expanded=index == 0):
            left, right = st.columns(2)
            with left:
                text_field(item, "name", "项目名", f"projects_{index}")
            with right:
                text_field(item, "url", "项目链接（可选）", f"projects_{index}")
            item["description"] = st.text_area(
                "描述（用 **文字** 加粗）",
                value=str(item.get("description", "")),
                height=130,
                key=f"edit_projects_{index}_description",
            )
            if st.button("删除这个项目", key=f"remove_projects_{index}"):
                remove_item("projects", index)
    if st.button("＋ 添加项目"):
        projects.append({"name": "", "url": "", "description": ""})
        st.rerun()

with tabs[4]:
    st.subheader("技能")
    skills = resume.setdefault("skills", [])
    for index, item in enumerate(skills):
        left, middle, right = st.columns([1, 3, 0.5])
        with left:
            text_field(item, "name", "分类", f"skills_{index}")
        with middle:
            text_field(item, "items", "技能（逗号分隔）", f"skills_{index}")
        with right:
            st.write("")
            st.write("")
            if st.button("删除", key=f"remove_skills_{index}"):
                remove_item("skills", index)
    if st.button("＋ 添加技能分类"):
        skills.append({"name": "", "items": ""})
        st.rerun()


st.divider()
generate_col, note_col = st.columns([1, 3])
with generate_col:
    generate = st.button("生成 PDF", type="primary", use_container_width=True)
with note_col:
    st.caption("内容较长时会自然分页。建议最终控制在 1-2 页，并检查日期与拼写。")

if generate:
    try:
        with st.spinner("正在编译 LaTeX..."):
            career_profile = (
                json.loads(profile_upload.getvalue().decode("utf-8"))
                if profile_upload is not None
                else resume
            )
            if not isinstance(career_profile, dict):
                raise ValueError("扩展职业档案必须是 JSON object")
            st.session_state.pdf_result = compile_resume(
                resume,
                career_profile=career_profile,
                ai_context_mode=ai_context_mode,
            )
    except (BuildError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        st.error(str(exc))

result = st.session_state.get("pdf_result")
if result:
    st.success("PDF 已生成")
    if result.ai_context.filename:
        st.caption(
            f"AI 上下文：{result.ai_context.mode} · {result.ai_context.filename} · "
            f"{result.ai_context.profile_size} bytes"
        )
    download_col, source_col = st.columns(2)
    with download_col:
        st.download_button(
            "下载 PDF",
            data=result.pdf,
            file_name="resume.pdf",
            mime="application/pdf",
            type="primary",
            use_container_width=True,
        )
    with source_col:
        st.download_button(
            "下载 LaTeX 源文件",
            data=result.tex,
            file_name="resume.tex",
            mime="text/plain",
            use_container_width=True,
        )

    st.pdf(result.pdf, height=920)

    with st.expander("查看 LaTeX 源码"):
        st.code(result.tex, language="latex")
