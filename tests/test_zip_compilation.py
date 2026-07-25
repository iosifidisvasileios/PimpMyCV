from pathlib import Path

import pytest

from pimpmycv.archive import ArchiveError, extract_cv_archive
from pimpmycv.compiler import compile_latex, find_engine


@pytest.mark.integration
def test_compile_latex_from_zip(
    tmp_path: Path,
    zip_path: Path,
    engine: str = "auto",
) -> None:
    """
    Integration test that extracts a LaTeX ZIP file and compiles it with a specified engine.
    
    Usage:
        pytest tests/test_zip_compilation.py::test_compile_latex_from_zip --zip-path examples/cv.zip --engine latexmk
    """
    try:
        selected_engine = find_engine(engine)
    except RuntimeError as exc:
        pytest.skip(f"LaTeX engine not available: {exc}")

    if not zip_path.exists():
        pytest.skip(f"ZIP file not found: {zip_path}")

    try:
        with extract_cv_archive(zip_path, main_tex=None) as project:
            result = compile_latex(
                project.main_tex,
                source_dir=project.main_tex.parent,
                engine=selected_engine,
            )

            assert result.success, f"Compilation failed:\n{result.log[-4000:]}"
            assert result.pdf_path.is_file(), "PDF file was not created"
            assert result.pdf_path.stat().st_size > 0, "PDF file is empty"
            
            # Copy PDF to a permanent location for inspection
            output_dir = Path("test_output")
            output_dir.mkdir(exist_ok=True)
            output_pdf = output_dir / f"compiled_{zip_path.stem}.pdf"
            import shutil
            shutil.copy2(result.pdf_path, output_pdf)
            
            print(f"\n✓ Successfully compiled with {selected_engine}")
            print(f"  PDF: {output_pdf}")
            print(f"  Main .tex: {project.main_relative_path}")
    except ArchiveError as exc:
        pytest.skip(f"Invalid ZIP archive: {exc}")


def test_compile_latex_from_zip_with_custom_engine(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """
    Test with mocked engine to verify the workflow without requiring actual LaTeX installation.
    """
    zip_path = tmp_path / "test_cv.zip"
    
    # Create a minimal valid LaTeX ZIP
    import zipfile
    tex_content = r"\documentclass{article}\begin{document}Test CV\end{document}"
    
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("cv.tex", tex_content)
    
    # Mock the compiler to simulate successful compilation
    def mock_compile(tex_path, *, source_dir, engine):
        from pimpmycv.compiler import CompileResult
        pdf_path = tex_path.with_suffix(".pdf")
        pdf_path.write_bytes(b"%PDF-1.4\n%mocked pdf")
        return CompileResult(
            success=True,
            engine=engine,
            pdf_path=pdf_path,
            log="Mocked compilation successful"
        )
    
    monkeypatch.setattr("pimpmycv.compiler.compile_latex", mock_compile)
    
    with extract_cv_archive(zip_path, main_tex=None) as project:
        result = compile_latex(
            project.main_tex,
            source_dir=project.main_tex.parent,
            engine="latexmk",
        )
        
        assert result.success
        assert result.pdf_path.exists()


