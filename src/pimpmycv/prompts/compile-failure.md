The LaTeX candidate did not compile with ${compiler}. Reflect on these
diagnostics and return a corrected complete document. Use save_and_compile_cv
when function calling is available; otherwise return only the LaTeX from
\begin{document} through \end{document}, without Markdown fences.

Compiler diagnostics:
${diagnostics}
