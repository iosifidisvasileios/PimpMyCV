# PimpMyCV

A deliberately small Python CLI that tailors a zipped LaTeX CV project to a job
description, then compiles the result to PDF. The ZIP can contain style files,
images, fonts, `\input` sections, and other support files. The model can act
through one local tool: it writes a candidate, runs the LaTeX compiler, reads
any compiler errors, and revises until compilation succeeds.

The original CV is never modified. The prompt explicitly forbids inventing
experience or credentials.

## Requirements

- Python 3.10+
- One model endpoint: OpenAI, Azure OpenAI, or Ollama 0.13.3+
- A LaTeX engine on `PATH`: `pdflatex`, `xelatex`, `lualatex`, or `tectonic`

On Windows, MiKTeX is a convenient LaTeX distribution. On Linux/macOS, install
TeX Live or Tectonic using your package manager.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Linux/macOS activation is `source .venv/bin/activate`.

## Run

### OpenAI

```powershell
$env:OPENAI_API_KEY = "your-api-key"
pimpmycv --provider openai --cv examples/cv.zip --job examples/job.txt
```

The OpenAI default is `gpt-5.6-sol` with medium reasoning. Override it with
`--model MODEL_ID`, `PIMPMYCV_MODEL`, or use `--endpoint`/`OPENAI_BASE_URL` for
a custom OpenAI-compatible base URL.

### Azure OpenAI

Use your Azure resource endpoint and the name of a deployed model:

```powershell
$env:AZURE_OPENAI_API_KEY = "your-azure-key"
$env:AZURE_OPENAI_ENDPOINT = "https://YOUR-RESOURCE.openai.azure.com"
$env:AZURE_OPENAI_DEPLOYMENT = "YOUR-DEPLOYMENT-NAME"
pimpmycv --provider azure --cv examples/cv.zip --job examples/job.txt
```

You can pass the deployment with `--model` and the endpoint with `--endpoint`
instead of setting the last two environment variables. The client uses Azure's
current `/openai/v1/` Responses endpoint.

### Ollama

Pull a tool-capable model and make sure the Ollama service is running:

```powershell
ollama pull qwen3:8b
pimpmycv --provider ollama --model qwen3:8b `
  --cv examples/cv.zip --job examples/job.txt
```

The default Ollama URL is `http://localhost:11434`. Override it with
`--endpoint` or `OLLAMA_BASE_URL`; use `OLLAMA_MODEL` to set the default model.
`OLLAMA_API_KEY` is optional for authenticated remote Ollama-compatible servers.
Ollama's Responses API is stateless, so PimpMyCV automatically replays the
agent history after compiler failures.

### Common options

```powershell
pimpmycv --cv examples/cv.zip --job examples/job.txt --output build
```

The command creates:

- `build/tailored_cv.pdf`
- `build/tailored_cv.zip`, containing the rewritten `.tex`, generated PDF, and
  every support file from the input archive

Set `PIMPMYCV_PROVIDER` and `PIMPMYCV_MODEL` to avoid repeating provider and
model flags. To choose a compiler explicitly, pass for example
`--engine xelatex`.

The main `.tex` file is detected by looking for the archive's single
`\documentclass`. If the ZIP contains multiple standalone documents, select the
CV explicitly, for example `--main-tex cv/main.tex`. Relative `\input`, image,
font, and style paths are resolved from the main document's directory.

## How the agent loop works

1. The ZIP is safely extracted to an isolated temporary directory; traversal
   paths, symbolic links, oversized archives, and ambiguous main files are
   rejected.
2. The selected provider's Responses API receives the main CV document, job
   description, factuality constraints, and the `save_and_compile_cv` tool.
3. The model reflects on relevance and submits a complete LaTeX candidate.
4. The host saves and compiles it with shell escape disabled while support files
   remain available.
5. On a compiler error, diagnostics are returned to the same response chain and
   the model tries again, up to `--max-attempts`.
6. The command exits successfully only when a non-empty PDF exists, then creates
   a tailored ZIP containing the updated project and PDF.

This is intentionally not built on a larger agent framework: the Responses API
tool loop is enough for this single-agent workflow.

## Tests

```powershell
pytest
```

The unit tests use a fake model client and do not call the API or require a
LaTeX installation.

## Privacy and safety

Your CV and job description are sent to the endpoint you select. With a local
Ollama endpoint they remain on that machine; cloud or remote endpoints receive
the contents. Review the generated CV before applying. LaTeX compilation runs
locally with shell escape disabled, but you should still treat generated LaTeX
as untrusted input and inspect it when using sensitive local assets.
