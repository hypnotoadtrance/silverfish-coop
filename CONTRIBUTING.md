# Contribute with a coding agent

This project supports agent-assisted contributions from Codex and other coding tools. The repository gives agents test guidance in [AGENTS.md](AGENTS.md), the co-op rules in [DESIGN.md](docs/DESIGN.md), and the current gaps in the [integration notes](docs/IN-GAME-INTEGRATION.md). The mod’s [machine-readable controls](docs/PLAYING-COOP.md#machine-readable-controls) are a separate interface for operating an isolated playtest.

## Work from a local clone

The repository is public. Fork it on GitHub if you plan to submit changes. Clone your fork and create a branch from the project’s `main`. [GitHub fork guide](https://docs.github.com/en/pull-requests/how-tos/work-with-forks/fork-a-repo)

```powershell
git clone https://github.com/YOUR-USERNAME/silverfish-coop.git
Set-Location silverfish-coop
git remote add upstream https://github.com/hypnotoadtrance/silverfish-coop.git
git fetch upstream
git switch -c fix/short-description upstream/main
```

Open the cloned folder in your desktop or editor agent. With the Codex CLI, run `codex` from that folder and sign in when prompted. Other local coding agents can use the same checkout and your existing Git authentication for pushes. [Codex CLI setup](https://learn.chatgpt.com/docs/codex/cli)

For a hosted agent such as Codex Cloud, connect your GitHub account, grant the integration access to your fork, and select the fork in the task environment. If the fork is missing, check the repositories available to the integration. Cloud tasks are suitable for documentation, source review, and portable tests. Live Silverfish checks and the native Windows release build require an authorized Windows workspace with the game and pinned dependencies. [Codex Cloud setup](https://learn.chatgpt.com/docs/cloud) · [Native build guide](native/README.md)

## Open a pull request

Start with an [open issue](https://github.com/hypnotoadtrance/silverfish-coop/issues) or [file a new one](https://github.com/hypnotoadtrance/silverfish-coop/issues/new) with the build and reproduction steps. Maintainers can use that issue as the coding agent’s task and link the resulting draft PR back to it.

Give the agent a specific issue, the map or feature involved, and the expected result. For example:

> Read `AGENTS.md`, `docs/DESIGN.md`, and the relevant integration notes. Fix the reported guest inventory regression on this branch. Run focused tests for the affected path. Use muted replicas and copied saves for live checks. Summarize what ran and what still needs a human check. Push the branch and open a draft pull request targeting `main` for review.

Review the diff and the test evidence. If your agent has write access to your fork and PR tools, it can push the branch and open the draft PR. You can also push the branch to your fork and use **Compare & pull request** on GitHub. Select `hypnotoadtrance/silverfish-coop` and `main` as the base, then select your fork and change branch as the compare branch. [Create a pull request from a fork](https://docs.github.com/en/pull-requests/how-tos/create-pull-requests/creating-a-pull-request-from-a-fork)

In the PR, describe the player-visible change, affected roles and maps, tests actually run, remaining limitations, and any human playtest results. Identify material AI assistance. Have a maintainer review before merging or publishing a build. Keep extracted game files, private saves, passwords, invitation files, credentials, and build intermediates out of the branch and PR.
