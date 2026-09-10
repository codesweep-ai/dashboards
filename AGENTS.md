# Working in this repo

This file routes; it holds no knowledge of its own. The docs it points at are
authoritative. When one covers what you need, open it rather than inferring
the answer from the code. When nothing covers it, say so instead of guessing.

- [README.md](README.md) · what this is, how the pages get their data, and how
  to preview them.
- [SPEC.md](SPEC.md) · the contract between a project and this repository: what
  a status file must contain, and what a page may assume about one.
- [CONTRIBUTING.md](CONTRIBUTING.md) · conventions, and the rituals a diff does
  not show. Read it before your first change.
- [ledger/AGENTS.md](ledger/AGENTS.md) · this repo keeps a ledger of open
  issues. Read it before you start work.

Two rules that catch people out, both spelled out where they belong:

- **No colour or spacing literals.** Every value resolves through a token from
  `tokens.css`, which `npm run tokens` copies out of the `@codesweep-ai/ui`
  dependency. It is generated, so never edit it. CONTRIBUTING.md says how to
  take an update.
- **A page may not call the GitHub API.** It reads static JSON that projects
  publish. SPEC.md says why, and what breaks if you do.
