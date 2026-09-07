# Project Working Agreements

## Teaching-first communication

This repository is a learning project. When implementing or explaining an Agent Runtime mechanism:

1. Assume the reader is new to the mechanism. Define each important term in plain language before using it.
2. Explain the concrete problem first: what breaks or becomes impossible without this mechanism?
3. Use a small example to show how data moves through the mechanism.
4. Explain why this design was chosen, how mainstream coding agents use a similar concept, and where our implementation differs.
5. State limitations and tradeoffs. Distinguish implemented behavior from planned behavior.
6. Only after the explanation, summarize files changed and tests run.

Do not report a list of added classes or files without explaining what each one does and why it exists.

## Interview-oriented learning

The user is building this project to learn AI Agent engineering and prepare for interviews. For every completed mechanism:

1. Add or update a concise interview note: definition, problem, data flow, design choice, tradeoffs, and evidence from tests.
2. Distinguish protocol, provider, SDK, runtime policy, and product behavior instead of treating them as interchangeable.
3. Include a short answer the user can say in an interview and at least one likely follow-up question.
4. Never claim a mechanism is production-ready merely because its happy path works; name the untested boundaries.
5. Prefer small executable experiments that prove the explanation over terminology-only documentation.
