# PimpMyCV

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
![Version 0.1.0](https://img.shields.io/badge/version-0.1.0-blue)
![Providers: OpenAI, Azure OpenAI, Ollama](https://img.shields.io/badge/providers-OpenAI%20%7C%20Azure%20OpenAI%20%7C%20Ollama-412991)
![Output: LaTeX and PDF](https://img.shields.io/badge/output-LaTeX%20%2B%20PDF-008080)

PimpMyCV is a small Python CLI that tailors a LaTeX CV to a job description,
compiles it, and lets you request revisions before accepting the final PDF.
It supports OpenAI, Azure OpenAI, and local Ollama models.

The input ZIP is never modified. The agent preserves the main document's LaTeX
preamble and is instructed not to invent experience, skills, or credentials.

## Install

Requirements:

- Python 3.10+
- A LaTeX compiler on `PATH`
- OpenAI, Azure OpenAI, or a running Ollama server

```bash
python -m venv .venv
source .venv/bin/activate                 # Linux or macOS
# .\.venv\Scripts\Activate.ps1            # PowerShell
python -m pip install -e .
```

For tests and `.env` file support:

```bash
python -m pip install -e ".[dev,dotenv]"
```

Install `latexmk` and common LaTeX packages on Linux:

```bash
# Ubuntu or Debian
sudo apt update
sudo apt install -y latexmk biber texlive-latex-base texlive-latex-extra

# Fedora
sudo dnf install -y latexmk biber texlive-scheme-medium

# Arch Linux
sudo pacman -S biber texlive-basic texlive-binextra texlive-latexextra
```

## Inputs

- `--cv`: a ZIP containing the complete LaTeX project
- `--job`: a UTF-8 `.txt` job description
- `--instructions`: an optional UTF-8 `.txt` or `.md` file with your rewrite
  preferences

The ZIP must include the main `.tex` document and every referenced style,
image, font, bibliography, and `\input` file. Create it from inside the CV
project directory:

```bash
zip -r ../cv.zip .
```

The main document is detected automatically. If the ZIP contains multiple
documents, select one using its path inside the archive:

```bash
pimpmycv --cv cv.zip --main-tex main.tex --job examples/job.txt
```

Archives are limited to 1,000 files and 100 MB uncompressed. Unsafe paths,
duplicate paths, and symbolic links are rejected.

## Providers

| Provider | Required configuration | Default model |
| --- | --- | --- |
| OpenAI | `OPENAI_API_KEY` | `gpt-5.6-sol` |
| Azure OpenAI | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`, plus `AZURE_OPENAI_API_VERSION` or `OPENAI_API_VERSION` | deployment name |
| Ollama | Server at `http://localhost:11434` | `qwen3:8b` |

Use `--model` to override the model or Azure deployment and `--endpoint` to
override the endpoint. Other optional variables are listed in `.env.example`.
To load that configuration from `.env`, install the `dotenv` extra first.

Ollama uses its OpenAI-compatible API through the `openai` Python package, so
no separate Ollama Python package is required:

```bash
ollama serve
ollama pull qwen3:8b
```

Azure OpenAI example configuration:

```bash
export AZURE_OPENAI_API_KEY="your-key"
export AZURE_OPENAI_ENDPOINT="https://YOUR-RESOURCE.openai.azure.com"
export AZURE_OPENAI_DEPLOYMENT="your-deployment"
export AZURE_OPENAI_API_VERSION="your-supported-api-version"
```

## Run

Complete Ollama example:

```bash
pimpmycv \
  --provider ollama \
  --model qwen3:8b \
  --cv /path/to/cv.zip \
  --job examples/job.txt \
  --instructions examples/instructions.txt \
  --output build \
  --engine latexmk
```

With `--engine auto`, the first available engine is selected in this order:
`latexmk`, `pdflatex`, `xelatex`, `lualatex`, then `tectonic`. The recommended
`latexmk` workflow is:

```text
latexmk -pdf -interaction=nonstopmode -file-line-error -f main.tex
```

After each successful compilation, review `build/draft_cv.pdf`. Enter feedback
to request another revision, or press Enter to accept it. Use `--no-feedback`
to accept the first compilable draft automatically.

The final output is:

```text
build/tailored_cv.pdf
build/tailored_cv.zip
```

The final ZIP contains the rewritten `.tex` document, the original support
files, and the generated PDF. Run `pimpmycv --help` for all CLI options.

## Diagnostics

- `--verbose` shows model, candidate, compiler, and feedback-loop progress.
- `--debug` adds detailed logs and saves responses, LaTeX candidates,
  reasoning when available, and compiler logs under `OUTPUT/debug/`.

Debug artifacts can contain personal information. Review them before sharing.

## Prompts

Agent prompts are editable Markdown files in `src/pimpmycv/prompts/`:
`system.md`, `task.md`, `feedback.md`, `compile-failure.md`, and
`tool-required.md`.

## Tests

```bash
pytest
```

Check an installed LaTeX compiler:

```bash
pytest tests/test_latex_integration.py -q -rs
```

Compile your own ZIP:

```bash
pytest tests/test_zip_compilation.py::test_compile_latex_from_zip \
  --zip-path /path/to/cv.zip \
  --engine latexmk \
  -q -rs
```

Integration tests are skipped when their input or compiler is unavailable.

## Privacy

The CV, job description, and instructions are sent to the selected endpoint.
A local Ollama endpoint keeps them on that machine; OpenAI and Azure OpenAI
receive their contents. Always review the generated CV before using it.
