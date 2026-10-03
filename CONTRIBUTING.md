# Contributing

Thanks for taking a look. This is a personal learning project, but issues and pull requests are welcome.

## Before you start

- Open an issue for anything bigger than a small fix, so we can agree on the approach.
- Read [docs/architecture.md](docs/architecture.md) and [docs/development.md](docs/development.md); the second one gets you a local setup in a few minutes.

## Workflow

1. Branch from `main`: `feat/<topic>`, `fix/<topic>` or `docs/<topic>`.
2. Make the change, with its documentation. If behaviour, configuration or an operational step changes, update `README.md` or `docs/` in the same pull request.
3. Check it locally (see [Checking your changes](docs/development.md#checking-your-changes)).
4. Open a pull request. CI will run once it is set up; until then, describe how you tested.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`, optionally with a scope (`feat(laya): ...`). Keep the subject short and explain the why in the body when it is not obvious.

## Ground rules

- **No secrets, tokens, real addresses or personal data** in commits. Configuration comes from environment variables and cluster Secrets.
- **No third-party text** (news headlines, summaries) in the repository. Evaluation and training data are generated locally and git-ignored.
- Pin dependency versions, and use explicit image tags (never `latest`).
- Anything that changes a cluster is described in the pull request, not run silently.
- If a metric or result is added, say how it was measured and by whom the labels were written. Be honest about the limits.

## Reporting a security problem

Please do not open a public issue for a vulnerability. Contact the maintainer through the email on their GitHub profile.
