from pathlib import Path

import pytest


def pytest_addoption(parser):
    """Add custom command-line options for zip compilation tests."""
    parser.addoption(
        "--zip-path",
        type=Path,
        help="Path to LaTeX ZIP file to test compilation"
    )
    parser.addoption(
        "--engine",
        type=str,
        default="auto",
        help="LaTeX engine to use (auto, latexmk, pdflatex, xelatex, lualatex, tectonic)"
    )


def pytest_generate_tests(metafunc):
    """Generate test parameters from command-line options."""
    if "zip_path" in metafunc.fixturenames:
        zip_path = metafunc.config.getoption("--zip-path")
        if zip_path:
            metafunc.parametrize("zip_path", [zip_path], scope="module")
        else:
            # Skip the test if no zip-path is provided
            metafunc.parametrize("zip_path", [None], scope="module")
    
    if "engine" in metafunc.fixturenames:
        engine = metafunc.config.getoption("--engine")
        metafunc.parametrize("engine", [engine], scope="module")
