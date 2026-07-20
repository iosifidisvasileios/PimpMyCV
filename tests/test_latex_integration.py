from pathlib import Path

import pytest

from pimpmycv.compiler import compile_latex, find_engine


@pytest.mark.integration
def test_installed_latex_engine_creates_pdf(tmp_path: Path):
    try:
        engine = find_engine()
    except RuntimeError as exc:
        pytest.skip(str(exc))

    tex = tmp_path / "smoke_test.tex"
    tex.write_text(
        "\\documentclass{article}\\begin{document}Compiler works.\\end{document}",
        encoding="utf-8",
    )
    result = compile_latex(tex, source_dir=tmp_path, engine=engine)

    assert result.success, result.log[-4000:]
    assert result.pdf_path.is_file()
    assert result.pdf_path.stat().st_size > 0
