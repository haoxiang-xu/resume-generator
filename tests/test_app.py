import json
from pathlib import Path

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parent.parent


def test_web_app_generates_side_by_side_pdf_and_latex_workspace() -> None:
    app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=20)
    assert not app.exception
    assert [message.value for message in app.info if "Rendered Resume" in message.value]

    generate = next(button for button in app.button if button.label == "生成并查看对照")
    generate.click()
    app.run(timeout=60)

    assert not app.exception
    success_messages = [message.value for message in app.success]
    assert "PDF 与 LaTeX 已生成" in success_messages
    assert any(message.startswith("Watermark verified · ") for message in success_messages)
    assert "Resume 对照工作台" in [heading.value for heading in app.subheader]
    markdown = [block.value for block in app.markdown]
    assert "#### Rendered Resume" in markdown
    assert "#### Source & Watermark" in markdown
    assert {tab.label for tab in app.tabs} >= {"Raw LaTeX", "Watermark JSON"}
    assert {button.label for button in app.get("download_button")} >= {
        "下载 PDF",
        "下载 .tex",
        "下载 watermark JSON",
    }
    assert len(app.code) == 2
    latex = next(block.value for block in app.code if block.language == "latex")
    watermark = next(block.value for block in app.code if block.language == "json")
    assert r"\documentclass" in latex
    watermark_document = json.loads(watermark)
    assert watermark_document["context"]["watermark"]["ai_editable"] is False
    assert watermark_document["context"]["watermark_file"]["bindings"]
