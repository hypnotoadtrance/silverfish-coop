# Contribute with a coding agent

This project supports agent-assisted contributions from Codex and other coding tools. The repository gives agents test guidance in [AGENTS.md](AGENTS.md), the co-op rules in [DESIGN.md](docs/DESIGN.md), and the current gaps in the [integration notes](docs/IN-GAME-INTEGRATION.md). The mod’s [machine-readable controls](docs/PLAYING-COOP.md#machine-readable-controls) are a separate interface for operating an isolated playtest.

## Get access

The repository is private. Ask the owner to invite your GitHub account, then accept the invitation. Collaborator access on a private repository owned by a personal account allows pushing code, so the owner should grant it only to contributors they trust. [GitHub collaborator permissions](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/repository-access-and-collaboration/permission-levels-for-a-personal-account-repository)

## Work from a local clone

Clone the repository using an authenticated GitHub account and create a branch from `main`.

```powershell
git clone https://github.com/hypnotoadtrance/silverfish-coop.git
Set-Location silverfish-coop
git switch -c fix/short-description origin/main
```

Open the cloned folder in your desktop or editor agent. With the Codex CLI, run `codex` from that folder and sign in when prompted. Other local coding agents can use the same checkout and your existing Git authentication for pushes. [Codex CLI setup](https://learn.chatgpt.com/docs/codex/cli)

For a hosted agent such as Codex Cloud, connect your GitHub account, grant the agent access to this repository, and select it in the task’s cloud environment. If the repository is missing, check your GitHub invitation and the repository access granted to the integration. Cloud tasks are suitable for documentation, source review, and portable tests. Live Silverfish checks and the native Windows release build require an authorized Windows workspace with the game and pinned dependencies. [Codex Cloud setup](https://learn.chatgpt.com/docs/cloud) · [Native build guide](native/README.md)

## Open a pull request

Give the agent a specific issue, the map or feature involved, and the expected result. For example:

> Read `AGENTS.md`, `docs/DESIGN.md`, and the relevant integration notes. Fix the reported guest inventory regression on this branch. Run focused tests for the affected path. Use muted replicas and copied saves for live checks. Summarize what ran and what still needs a human check. Push the branch and open a draft pull request targeting `main` for review.

Review the diff and the test evidence. If your agent has GitHub write access and PR tools, it can push the branch and open the draft PR. You can also push the branch and use **Compare & pull request** on GitHub. Use `main` as the base and your change branch as the compare branch. [Create a pull request](https://docs.github.com/en/pull-requests/how-tos/create-pull-requests/creating-a-pull-request)

In the PR, describe the player-visible change, affected roles and maps, tests actually run, remaining limitations, and any human playtest results. Identify material AI assistance. Have a maintainer review before merging or publishing a build. Keep extracted game files, private saves, passwords, invitation files, credentials, and build intermediates out of the branch and PR.
