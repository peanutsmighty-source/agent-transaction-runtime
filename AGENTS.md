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
6. At the end of each completed development task, summarize likely interviewer follow-up questions when they add learning value; skip this section when there is no meaningful follow-up.

## Critical design evaluation

User-proposed designs are hypotheses, not requirements or preferred solutions unless the user explicitly makes a product decision.

1. Start by challenging the proposal: identify its failure modes, hidden assumptions, and the evidence still missing.
2. Compare it with credible alternatives and the option of making no architectural change.
3. Recommend a design only after stating the decision criteria and, where practical, running a focused experiment or failure-injection test.
4. Do not add policies or abstractions merely because an example can be encoded as another rule. Prefer mechanisms that generalize, such as authoritative state, bounded retrieval, verification, and measurable quality gates.
5. Clearly distinguish a plausible idea, a planned design, an implemented mechanism, and a mechanism whose behavior is supported by tests.
