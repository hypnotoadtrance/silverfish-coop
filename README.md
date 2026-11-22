# Project Silverfish Co-op

An unofficial Windows mod that adds Direct IP co-op to the Steam version of Project Silverfish. Open **Multiplayer** from the game’s title screen to host a campaign or join a friend. The mod runs in the existing Steam installation and keeps multiplayer campaigns separate from singleplayer saves.

[Download the release candidate](https://github.com/hypnotoadtrance/silverfish-coop/releases/tag/rc-d9978a3) · [Player guide](docs/PLAYING-COOP.md) · [18-rule design](docs/DESIGN.md) · [Contribute with a coding agent](CONTRIBUTING.md) · [Build and publish a release](docs/RELEASING.md)

![Project Silverfish title screen showing the Singleplayer and Multiplayer choices](docs/screenshots/title-menu.png)

## Install or update

1. Close Project Silverfish on both PCs.
2. On the [Releases page](https://github.com/hypnotoadtrance/silverfish-coop/releases), download the **Silverfish-Coop-RC-*.zip** file under **Assets**. Both players need the same ZIP and a compatible Steam game build.
3. In Steam, right-click **Project Silverfish** and choose **Manage → Browse local files**. The game folder contains `SilverFish.exe`.
4. Extract the ZIP and merge its `SilverFish` folder into the game folder. Replace existing bundled files when prompted. This also updates the included UE4SS loader and its settings.
5. Launch from Steam and select **Multiplayer**. Use these same steps for future updates.

**Host.** Choose **Host**, then **New Game** to use the stock character creator or **Load Game** to resume a co-op campaign. Select a UDP port, which defaults to **7777**. Enable **Require password** if you want to set a password.

**Join.** Choose **Join by Direct IP** and enter the host’s IP address, port, and password if enabled. Review the trust prompt on the first connection or after a host identity change. Choose a saved character for that campaign or create a new one. Character lists belong to each host campaign.

For LAN play, use the host’s LAN address. For internet play, the guest needs a route to the host’s UDP port. This may require a host firewall exception and router port forwarding. Co-op saves default to `%LOCALAPPDATA%\SilverfishCoop\Resident`. Keep a copy of important campaigns before changing builds.

## Screenshots

| Join by Direct IP | A teammate as seen by the other player |
| --- | --- |
| ![In-game Direct IP form with host address, UDP port, and optional password](docs/screenshots/direct-ip.png) | ![A remote player avatar wearing armor and holding a pistol during a co-op playtest](docs/screenshots/teammate.png) |

Captured in development builds. The Direct IP form shows a test port.

## What the mod covers

| Area | Current scope |
| --- | --- |
| Sessions | In-game Direct IP host and join, optional passwords, encrypted transport, hot join, and recovery of the same character after a short interruption. One host and up to four guests have connected in loopback tests. Most gameplay checks use two players. |
| Saves and characters | Co-op campaigns store the host’s world and each player’s character, inventory, and stash. New players can use the stock character creator and rejoin the same campaign with an existing character. Singleplayer has its own save scope. |
| Shared world | The host resolves supported loot pickups and drops, door state, party money, campaign flags, and travel. Covered zone exits enforce the host-only and party-distance rules. Verified traps and hazards use host-side damage. |
| Campaign gameplay | Supported paths cover downing, revives, party wipes, stock backpack and stash actions, merchant transactions, and selected NPC conversations and quest objectives. Exact coverage varies by map and interaction. |
| Test and automation controls | Local commands expose session and gameplay status, events, and supported actions as JSON. They support human playtest operators and AI-assisted testing. |

[Verified native scenarios](docs/NATIVE-INTEGRATION.md) · [Current integration and known gaps](docs/IN-GAME-INTEGRATION.md) · [Machine-readable controls](docs/PLAYING-COOP.md#machine-readable-controls)

## Limitations and known issues

- **Campaign coverage.** Co-op is still in development. Some quests and dialogue triggers, faction updates, inventory actions, NPC behaviors, map exits, and scripted events lack complete multiplayer coverage.
- **Visual and physical state.** Avatar appearance, equipment and combat animations, thrown items, harvested objects, and some props and anomalies may differ between players. Release notes distinguish fixes included in the ZIP from work on `main`.
- **Responsiveness.** Some interactions wait for the host to commit a change. Players have reported delays and Crossroads stutter. Muted loopback timings cover local iteration. Foreground and internet play can behave differently.
- **Networking.** Steam Invites, matchmaking, and an internet relay are outside the current build. Direct IP depends on the guest being able to reach the host.
- **Compatibility.** Each ZIP targets a qualified Windows Steam build and includes a pinned UE4SS version. A game update or another UE4SS mod can introduce conflicts. Very long installation paths can prevent a bundled Windows DLL from loading.

When reporting a problem, include the release tag, map, what each player did and saw, and whether the connection was LAN or internet. Keep passwords, private invitation files, and save data out of issue reports.

## AI disclosure and human playtesting

OpenAI Codex assisted with implementation, debugging, automated tests, and documentation. Human players have tested prerelease builds together and their feedback guides fixes. Release candidates are intended for human playtesting, and release notes state whether the exact build has completed a human two-PC test alongside its automated checks. A green loopback test covers the specific scenario it exercised.

## Development

Use the [development setup in the player guide](docs/PLAYING-COOP.md#stage-the-development-build), the [native build guide](native/README.md), and the [release workflow](docs/RELEASING.md). Run focused tests for incremental changes and save the full suite for major releases. Keep game files, extracted assets, saves, credentials, and build outputs out of Git. Compiled ZIPs are attached to tagged GitHub releases.
