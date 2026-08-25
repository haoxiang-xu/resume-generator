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
    assert [message.value for message in app.success] == ["PDF 与 LaTeX 已生成"]
    assert "Resume 对照工作台" in [heading.value for heading in app.subheader]
    markdown = [block.value for block in app.markdown]
    assert "#### Rendered Resume" in markdown
    assert "#### Raw LaTeX" in markdown
    assert {button.label for button in app.get("download_button")} >= {
        "下载 PDF",
        "下载 .tex",
    }
    assert len(app.code) == 1
    assert r"\documentclass" in app.code[0].value
