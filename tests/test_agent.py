from pathlib import Path
from types import SimpleNamespace

from pimpmycv import agent
from pimpmycv.compiler import CompileResult


class FakeResponses:
    def __init__(self, latex_documents: list[str]):
        self.latex_documents = iter(latex_documents)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        latex = next(self.latex_documents)
        call = SimpleNamespace(
            type="function_call",
            name="save_and_compile_cv",
            arguments=(
                '{"latex": '
                + __import__("json").dumps(latex)
                + ', "summary": "Focused the CV on relevant experience."}'
            ),
            call_id=f"call-{len(self.requests)}",
        )
        return SimpleNamespace(id=f"response-{len(self.requests)}", output=[call])


class FakeTextResponses:
    def __init__(self, outputs: list[str]):
        self.outputs = iter(outputs)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(
            id=f"response-{len(self.requests)}",
            output=[],
            output_text=next(self.outputs),
        )


def test_agent_writes_candidate_and_returns_pdf(monkeypatch, tmp_path: Path):
    latex = "\\documentclass{article}\\begin{document}Tailored\\end{document}"
    responses = FakeResponses([latex])
    client = SimpleNamespace(responses=responses)
    expected_pdf = tmp_path / "tailored_cv.pdf"

    def fake_compile(tex_path, **kwargs):
        assert tex_path.read_text(encoding="utf-8") == latex
        return CompileResult(True, "pdflatex", expected_pdf, "ok")

    monkeypatch.setattr(agent, "compile_latex", fake_compile)
    result = agent.tailor_cv(
        client,
        cv_tex="original",
        job_description="job",
        output_tex=tmp_path / "tailored_cv.tex",
        source_dir=tmp_path,
    )

    assert result.success
    assert result.pdf_path == expected_pdf
    assert responses.requests[0]["tool_choice"] == "required"
    assert responses.requests[0]["reasoning"] == {"effort": "medium"}
    assert responses.requests[0]["parallel_tool_calls"] is False


def test_agent_returns_compiler_feedback_and_retries(monkeypatch, tmp_path: Path):
    broken = "broken latex"
    fixed = "fixed latex"
    responses = FakeResponses([broken, fixed])
    client = SimpleNamespace(responses=responses)
    results = iter([
        CompileResult(False, "pdflatex", tmp_path / "tailored_cv.pdf", "line 7: error"),
        CompileResult(True, "pdflatex", tmp_path / "tailored_cv.pdf", "ok"),
    ])

    monkeypatch.setattr(agent, "compile_latex", lambda *args, **kwargs: next(results))
    result = agent.tailor_cv(
        client,
        cv_tex="original",
        job_description="job",
        output_tex=tmp_path / "tailored_cv.tex",
        source_dir=tmp_path,
    )

    assert result.success
    assert len(responses.requests) == 2
    retry = responses.requests[1]
    assert retry["previous_response_id"] == "response-1"
    assert retry["instructions"] == agent.load_prompt("system.md")
    assert "line 7: error" in retry["input"][0]["output"]
    assert (tmp_path / "tailored_cv.tex").read_text(encoding="utf-8") == fixed


def test_agent_replays_history_for_stateless_provider(monkeypatch, tmp_path: Path):
    responses = FakeResponses(["broken", "fixed"])
    client = SimpleNamespace(responses=responses)
    results = iter([
        CompileResult(False, "pdflatex", tmp_path / "tailored_cv.pdf", "bad latex"),
        CompileResult(True, "pdflatex", tmp_path / "tailored_cv.pdf", "ok"),
    ])
    monkeypatch.setattr(agent, "compile_latex", lambda *args, **kwargs: next(results))

    result = agent.tailor_cv(
        client,
        cv_tex="original",
        job_description="job",
        output_tex=tmp_path / "tailored_cv.tex",
        source_dir=tmp_path,
        supports_stateful_responses=False,
        response_options={},
    )

    assert result.success
    retry = responses.requests[1]
    assert "previous_response_id" not in retry
    assert "reasoning" not in retry
    assert retry["input"][0]["role"] == "user"
    assert retry["input"][-1]["type"] == "function_call_output"


def test_agent_applies_user_feedback_in_same_response_chain(monkeypatch, tmp_path: Path):
    responses = FakeResponses(["first draft", "revised draft"])
    client = SimpleNamespace(responses=responses)
    expected_pdf = tmp_path / "tailored_cv.pdf"
    monkeypatch.setattr(
        agent,
        "compile_latex",
        lambda *args, **kwargs: CompileResult(True, "pdflatex", expected_pdf, "ok"),
    )
    reviews = []

    def review(result, summary, draft_number):
        reviews.append((summary, draft_number))
        return "Emphasize the Python automation work." if draft_number == 1 else None

    result = agent.tailor_cv(
        client,
        cv_tex="original",
        job_description="job",
        output_tex=tmp_path / "tailored_cv.tex",
        source_dir=tmp_path,
        feedback_callback=review,
    )

    assert result.success
    assert [number for _, number in reviews] == [1, 2]
    assert len(responses.requests) == 2
    revision = responses.requests[1]
    assert revision["previous_response_id"] == "response-1"
    assert revision["input"][0]["type"] == "function_call_output"
    assert '"success": true' in revision["input"][0]["output"]
    assert "Emphasize the Python automation work" in revision["input"][1]["content"]
    assert (tmp_path / "tailored_cv.tex").read_text(encoding="utf-8") == "revised draft"


def test_stateless_provider_replays_user_feedback(monkeypatch, tmp_path: Path):
    responses = FakeResponses(["first draft", "revised draft"])
    client = SimpleNamespace(responses=responses)
    monkeypatch.setattr(
        agent,
        "compile_latex",
        lambda *args, **kwargs: CompileResult(
            True, "pdflatex", tmp_path / "tailored_cv.pdf", "ok"
        ),
    )
    feedback = iter(["Shorten the profile.", None])

    result = agent.tailor_cv(
        client,
        cv_tex="original",
        job_description="job",
        output_tex=tmp_path / "tailored_cv.tex",
        source_dir=tmp_path,
        feedback_callback=lambda *args: next(feedback),
        supports_stateful_responses=False,
        response_options={},
    )

    assert result.success
    revision = responses.requests[1]
    assert "previous_response_id" not in revision
    assert revision["input"][-2]["type"] == "function_call_output"
    assert "Shorten the profile" in revision["input"][-1]["content"]


def test_prompt_templates_are_loaded_and_rendered_from_package_files():
    system = agent.load_prompt("system.md")
    task = agent.render_prompt(
        "task.md",
        cv_tex="\\textbf{Python} costs $5",
        job_description="Backend role",
        user_instructions="Keep it to one page.",
    )

    assert "Never invent" in system
    assert "\\textbf{Python} costs $5" in task
    assert "Backend role" in task
    assert "Keep it to one page." in task


def test_agent_preserves_original_preamble(monkeypatch, tmp_path: Path):
    original = (
        "\\documentclass{altacv}\n"
        "\\geometry{left=1cm}\n"
        "\\begin{document}\n"
        "Original body\n"
        "\\end{document}"
    )
    candidate = (
        "\\documentclass{article}\n"
        "\\titleformat{\\section}{}{_}\n"
        "\\begin{document}\n"
        "Tailored body\n"
        "\\end{document}"
    )
    responses = FakeResponses([candidate])
    client = SimpleNamespace(responses=responses)
    expected_pdf = tmp_path / "tailored_cv.pdf"

    def fake_compile(tex_path, **kwargs):
        saved = tex_path.read_text(encoding="utf-8")
        assert saved.startswith(
            "\\documentclass{altacv}\n\\geometry{left=1cm}\n"
        )
        assert "\\titleformat" not in saved
        assert "Tailored body" in saved
        return CompileResult(True, "latexmk", expected_pdf, "ok")

    monkeypatch.setattr(agent, "compile_latex", fake_compile)

    result = agent.tailor_cv(
        client,
        cv_tex=original,
        job_description="job",
        output_tex=tmp_path / "tailored_cv.tex",
        source_dir=tmp_path,
    )

    assert result.success


def test_agent_accepts_plain_text_latex_when_tool_call_is_missing(
    monkeypatch,
    tmp_path: Path,
):
    candidate = "\\begin{document}\nTailored body\n\\end{document}"
    responses = FakeTextResponses([candidate])
    client = SimpleNamespace(responses=responses)
    expected_pdf = tmp_path / "tailored_cv.pdf"
    debug_dir = tmp_path / "debug"

    def fake_compile(tex_path, **kwargs):
        saved = tex_path.read_text(encoding="utf-8")
        assert "\\documentclass{article}" in saved
        assert "Tailored body" in saved
        return CompileResult(True, "latexmk", expected_pdf, "ok")

    monkeypatch.setattr(agent, "compile_latex", fake_compile)

    result = agent.tailor_cv(
        client,
        cv_tex=(
            "\\documentclass{article}\n"
            "\\begin{document}\nOriginal\n\\end{document}"
        ),
        job_description="job",
        output_tex=tmp_path / "tailored_cv.tex",
        source_dir=tmp_path,
        supports_stateful_responses=False,
        response_options={},
        debug_dir=debug_dir,
    )

    assert result.success
    assert "Tailored body" in (debug_dir / "candidate-01.tex").read_text(
        encoding="utf-8"
    )
    assert (debug_dir / "candidate-01.log").read_text(encoding="utf-8") == "ok"
    assert (debug_dir / "response-01.txt").read_text(encoding="utf-8") == candidate


def test_plain_text_candidate_receives_compiler_feedback(monkeypatch, tmp_path: Path):
    responses = FakeTextResponses([
        "\\begin{document}\nbroken\n\\end{document}",
        "\\begin{document}\nfixed\n\\end{document}",
    ])
    client = SimpleNamespace(responses=responses)
    results = iter([
        CompileResult(False, "latexmk", tmp_path / "cv.pdf", "line 7: error"),
        CompileResult(True, "latexmk", tmp_path / "cv.pdf", "ok"),
    ])
    monkeypatch.setattr(agent, "compile_latex", lambda *args, **kwargs: next(results))

    result = agent.tailor_cv(
        client,
        cv_tex=(
            "\\documentclass{article}\n"
            "\\begin{document}\nOriginal\n\\end{document}"
        ),
        job_description="job",
        output_tex=tmp_path / "cv.tex",
        source_dir=tmp_path,
        supports_stateful_responses=False,
        response_options={},
    )

    assert result.success
    retry = responses.requests[1]
    assert "previous_response_id" not in retry
    assert "line 7: error" in retry["input"][-1]["content"]


def test_no_candidate_prompt_allows_direct_latex_fallback(monkeypatch, tmp_path: Path):
    responses = FakeTextResponses([
        "I cannot call the tool.",
        "\\begin{document}\nTailored\n\\end{document}",
    ])
    client = SimpleNamespace(responses=responses)
    monkeypatch.setattr(
        agent,
        "compile_latex",
        lambda *args, **kwargs: CompileResult(
            True,
            "latexmk",
            tmp_path / "cv.pdf",
            "ok",
        ),
    )

    result = agent.tailor_cv(
        client,
        cv_tex=(
            "\\documentclass{article}\n"
            "\\begin{document}\nOriginal\n\\end{document}"
        ),
        job_description="job",
        output_tex=tmp_path / "cv.tex",
        source_dir=tmp_path,
        supports_stateful_responses=False,
        response_options={},
    )

    assert result.success
    retry_prompt = responses.requests[1]["input"][-1]["content"]
    assert "return the complete document directly" in retry_prompt
