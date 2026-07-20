# Short feedback-loop example

Run the bundled CV project and job description:

```powershell
pimpmycv --provider openai --cv examples/cv.zip --job examples/job.txt
```

An illustrative interaction looks like this (the agent's wording will vary):

```text
Draft 1 is ready:
  PDF:     build\draft_cv.pdf
  Project: build\draft_cv.zip

Agent's rewrite summary:
Moved Python automation experience to the top and aligned the skills order
with the role.

Enter feedback for another revision, or press Enter to accept:
Shorten the profile and emphasize deployment checks.

Draft 2 is ready:
  PDF:     build\draft_cv.pdf
  Project: build\draft_cv.zip

Agent's rewrite summary:
Shortened the profile and strengthened the deployment-check bullet without
adding new claims.

Enter feedback for another revision, or press Enter to accept: [press Enter]

Project: build\tailored_cv.zip
PDF:     build\tailored_cv.pdf
```

Open each draft PDF before responding. Pressing Enter accepts the current draft;
entering text sends another revision request to the agent.
