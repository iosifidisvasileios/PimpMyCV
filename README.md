# PimpMyCV

PimpMyCV is a small Python CLI that rewrites a LaTeX CV for a job description,
compiles it, and lets you review and revise the result. It supports OpenAI,
Azure OpenAI, and Ollama. The original CV is never modified, and the agent is
instructed not to invent experience or credentials.

## Requirements

- Python 3.10+
- MiKTeX, TeX Live, or Tectonic on `PATH`
- OpenAI, Azure OpenAI, or Ollama 0.13.3+

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

On Linux/macOS, activate with `source .venv/bin/activate`.

## Inputs

- `--cv`: a ZIP containing the main `.tex` file and any styles, images, fonts,
  or `\input` files it needs.
- `--job`: a UTF-8 `.txt` job description.
- `--instructions`: an optional UTF-8 `.txt` or `.md` file with your preferred
  tone, length, ordering, emphasis, or formatting.

The main LaTeX file is detected automatically. If the ZIP contains more than
one document, pass its path explicitly: `--main-tex cv/main.tex`.

## Configure a provider

| Provider | Configuration | Default model |
| --- | --- | --- |
| OpenAI | `OPENAI_API_KEY` | `gpt-5.6-sol` |
| Azure | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT` | deployment name |
| Ollama | local service at `http://localhost:11434` | `qwen3:8b` |

Ollama exposes an OpenAI-compatible API, so PimpMyCV uses the same `openai`
Python library for all three providers. The separate `ollama` Python package is
not required.

OpenAI:

```powershell
$env:OPENAI_API_KEY = "your-api-key"
```

Azure OpenAI:

```powershell
$env:AZURE_OPENAI_API_KEY = "your-azure-key"
$env:AZURE_OPENAI_ENDPOINT = "https://YOUR-RESOURCE.openai.azure.com"
$env:AZURE_OPENAI_DEPLOYMENT = "YOUR-DEPLOYMENT-NAME"
```

Ollama:

```powershell
ollama pull qwen3:8b
```

Use `--model` and `--endpoint` to override provider defaults.

## Run

```powershell
pimpmycv --provider openai --cv examples/cv.zip --job examples/job.txt
```

Add your own rewrite preferences with a file:

```powershell
pimpmycv --provider openai --cv examples/cv.zip --job examples/job.txt `
  --instructions examples/instructions.txt
```

For Azure or Ollama, change `--provider` to `azure` or `ollama`.

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

## Tests

```powershell
pytest
```

Tests use a fake model client, so they do not spend API credits or require a
LaTeX installation.

## Privacy

The CV and job description are sent to the selected endpoint. A local Ollama
endpoint keeps them on that machine; cloud endpoints receive their contents.
Always review the generated CV before using it.
