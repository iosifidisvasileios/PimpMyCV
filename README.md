# PimpMyCV

PimpMyCV is a small Python CLI that rewrites a LaTeX CV for a job description,
compiles it, and lets you review and revise the result. It supports OpenAI,
Azure OpenAI, and Ollama. The original CV is never modified, and the agent is
instructed not to invent experience or credentials.

## Requirements

- Python 3.10+
- `latexmk` with TeX Live or MiKTeX on `PATH` (recommended), or Tectonic
- OpenAI, Azure OpenAI, or Ollama 0.13.3+

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

On Linux/macOS, activate with `source .venv/bin/activate`.

Install a LaTeX distribution on Linux:

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

- `--cv`: a ZIP containing the main `.tex` file and any styles, images, fonts,
  or `\input` files it needs.
- `--job`: a UTF-8 `.txt` job description.
- `--instructions`: an optional UTF-8 `.txt` or `.md` file with your preferred
  tone, length, ordering, emphasis, or formatting.

The main LaTeX file is detected automatically. If the ZIP contains more than
one document, pass its path explicitly: `--main-tex cv/main.tex`.

When available, PimpMyCV prefers `latexmk` and runs:

```text
latexmk -pdf -interaction=nonstopmode -file-line-error -f main.tex
```

This also runs bibliography tools such as Biber when the CV requires them. The
agent can rewrite CV content, but the original LaTeX preamble is preserved.

## Configure a provider

| Provider | Configuration | Default model |
| --- | --- | --- |
| OpenAI | `OPENAI_API_KEY` | `gpt-5.6-sol` |
| AzureOpenAI | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_VERSION` | deployment name |
| Ollama | local service at `http://localhost:11434` | `qwen3:8b` |

Ollama exposes an OpenAI-compatible API, so PimpMyCV uses the same `openai`
Python library. The separate `ollama` Python package is not required. If an
Ollama model returns LaTeX as ordinary text instead of a function call,
PimpMyCV detects and compiles that response as a fallback.

Azure OpenAI:

```powershell
$env:AZURE_OPENAI_API_KEY = "your-azure-key"
$env:AZURE_OPENAI_ENDPOINT = "https://YOUR-RESOURCE.openai.azure.com"
$env:AZURE_OPENAI_DEPLOYMENT = "YOUR-DEPLOYMENT-NAME"
$env:AZURE_OPENAI_API_VERSION = "YOUR-SUPPORTED-API-VERSION"

pimpmycv --provider azure `
  --cv examples/cv.zip `
  --job examples/job.txt
```

Ollama:

```powershell
ollama pull qwen3:8b
```

Example using an Ollama model named `XYZ`:

```powershell
# Create the local XYZ name from a tool-capable model.
ollama cp qwen3:8b XYZ

pimpmycv --provider ollama --model XYZ `
  --cv examples/cv.zip `
  --job examples/job.txt `
  --instructions examples/instructions.txt
```

If `XYZ` already exists in `ollama list`, skip the `ollama cp` command.

Use `--model` and `--endpoint` to override provider defaults.

## Run

```powershell
pimpmycv --provider ollama --model XYZ --cv examples/cv.zip --job examples/job.txt
```

Add your own rewrite preferences with a file:

```powershell
pimpmycv --provider ollama --model XYZ --cv examples/cv.zip --job examples/job.txt `
  --instructions examples/instructions.txt
```

Available providers are `openai`, `azure`, and `ollama`.

After compiling a draft, the CLI shows the agent's change summary and writes:

- `build/draft_cv.pdf`
- `build/draft_cv.zip`

Open the PDF, then enter feedback for another revision or press Enter to accept.
The accepted result is written to:

- `build/tailored_cv.pdf`
- `build/tailored_cv.zip`

See the [short feedback-loop example](examples/feedback-loop.md) for a sample
terminal interaction.

Useful options:

```text
--output PATH              Output directory
--main-tex PATH            Main .tex path inside the ZIP
--instructions PATH        Additional .txt or .md rewrite instructions
--max-attempts N           Compile/fix attempts
--max-feedback-rounds N    User-requested revisions
--no-feedback              Accept the first compilable draft
--engine xelatex           Select a LaTeX engine
```

## Prompts

Agent prompts are editable Markdown files in `src/pimpmycv/prompts/`:

- `system.md`
- `task.md`
- `feedback.md`
- `tool-required.md`
- `compile-failure.md`

## Tests

```powershell
pytest
```

Tests use a fake model client, so they do not spend API credits or require a
LaTeX installation.

To verify an installed LaTeX engine by compiling a minimal PDF:

```bash
pytest tests/test_latex_integration.py -q -rs
```

This test uses `latexmk`, `pdflatex`, `xelatex`, `lualatex`, or `tectonic` from
`PATH`. It is skipped when none is installed.

## Privacy

The CV and job description are sent to the selected endpoint. A local Ollama
endpoint keeps them on that machine; cloud endpoints receive their contents.
Always review the generated CV before using it.
