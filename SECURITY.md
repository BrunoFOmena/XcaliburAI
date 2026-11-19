# Security Policy

XcaliburAI is pre-alpha (v0.0.0). Even so, please report vulnerabilities privately.

## Reporting

Use GitHub's private vulnerability reporting: **Security → Report a vulnerability** on this repository. Do not open a public issue for a security problem.

We aim to acknowledge reports within a few days.

## Scope notes

- The execution layer launches workflow engines as subprocesses. Commands are built from manifest-validated parameters, never from raw model output.
- API keys are read from environment variables and must never be committed or written to configuration files. `xcalibur init` stores only the variable's NAME and rejects values that look like a key; provider errors never include the key. A workspace `xcalibur.yaml` (which may come from a cloned repository) cannot set `base_url`; only the user configuration or an explicit `--config` can.
- Genomic data may be sensitive (human data in particular). Data stays in the local workspace; the configured LLM provider receives only the messages and tool results the agent sends.
