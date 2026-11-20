# Stock title bootstrap

This opt-in UE4SS Lua mod presents **Open Co-op** on the stock Project Silverfish title. It needs the exact standalone title with its normal menu mounted and in front. On a packaged coordinator offer, a fresh F7 press submits a one-use request. The title shows **Coordinator unavailable** until a compatible coordinator supplies a valid offer.

The bootstrap only reads the title and adds its own passive text widget. The coordinator will own isolated game startup and the handoff.

## Package

From a checkout, run `python bootstrap/package.py <new-archive.zip>` to assemble the standalone mod. The archive includes the bootstrap entry and the same stock title qualification and display code used by the staged game. Its three Lua files load through UE4SS's absolute per-mod script search path.

The archive ships disabled. An isolated lab can unpack `SilverfishCoopBootstrap` into the held replica's `SilverFish/Binaries/Win64/ue4ss/Mods` and add `SilverfishCoopBootstrap : 1` to that replica's `mods.txt`.

Build the test coordinator with a matching Windows x64 Python environment, the repository requirements, and PyInstaller 6.22.3. Run `python bootstrap/build_coordinator.py <fresh-build-directory>`. The output in `dist` embeds the coordinator, Python dependencies, and the exact source used for invitation compatibility. Its local setup reads `%LOCALAPPDATA%\SilverfishCoop\coordinator.json` with schema `1`, the absolute `work` stage path, and `host` and `join` selections. The current Host selection includes `campaign`, numeric `bind`, and `port`. Join includes `profile`, an absolute `invite` imported under that co-op workspace, and a `character` UUID or `null`. Set either unused selection to `null`. The stock title adapter supplies one verified launch receipt over its inherited input pipe.

Build the [native title bridge](native/README.md) with the coordinator's SHA-256 pin. Run `python bootstrap/package.py <new-archive.zip> --native-dll <main.dll> --coordinator <SilverfishCoopCoordinator.exe>` to package the matching pair beside the three Lua scripts. The same opt-in UE4SS mod entry loads the complete bootstrap.

## Install the stock title entry

Prepare the matched private embedded stage first. Close both the stock game and the private stage before setup. From the matched checkout, use the pinned UE4SS release, the Project Silverfish signature folder, and the full bootstrap archive.

```powershell
python -m bootstrap.install_stock --game $steamGame --work $workspace --ue4ss $ue4ssRelease --signatures $silverfishSignatures --bootstrap $archive --host-campaign $campaign --bind $listenAddress --port $udpPort
```

To enable the in-game Join form, supply `--join-profile $profile`. The guest can paste the host invitation inside the game and choose a character. For a preselected automation action, import the invitation file into the private workspace and add `--invite $invitation` and, optionally, `--character $characterId`. You can configure Host, Join, or both. The installer checks that the stock game and private stage match, preserves compatible existing loader settings and other mods, and enables the entry after it writes the matched bootstrap and the local coordinator configuration. Repeating the same setup verifies the installed files and keeps the existing activation.

Open the game with **Play** in Steam, then use **Open Co-op** on the title to reach the isolated Host and Join controls. The interactive Steam startup and handoff still need foreground verification. [Private stage setup](../native/embedded_runtime/README.md)

## Coordinator calls

- `SilverfishReadCoopBootstrapOffer()` returns integer protocol version `1` and the current generation. A generation is a nonzero string of 32 lowercase hexadecimal characters. The coordinator issues a new ID for each new offer.
- `SilverfishRequestCoopBootstrapOpen(generation, request_id)` accepts the exact generation plus a fresh 32-character request ID in the same format. It returns `true` once the request has been queued. The coordinator consumes that generation once and verifies the isolated title's panel and effective save path before acknowledging readiness.

Both callbacks run on the game thread. The selection callback queues the launch and readiness checks on a worker thread. **Opening Co-op** stays visible while the isolated game starts. After the matching readiness receipt, the original unchanged stock title uses the stock Quit action. A refused or unverified reply leaves that title available. A new attempt requires a fresh coordinator offer.

## Isolated stock-layout check

1. Use an independently staged stock copy with the matching UE4SS runtime and Project Silverfish signature. Enable only the bootstrap Lua mod in that copy. Give the replica a fresh standalone `-userdir` outside the Steam install and original save directory. Start with no inherited Silverfish co-op environment or special Steam launch options. Run one case with the executable's directory as the working directory and another from a different directory.
2. Verify the mounted stock title shows **Open Co-op** and **Coordinator unavailable**. The loader log should show `[SilverfishCoopBootstrap] state=unavailable` after mount and text verification. A title or display failure reports `state=error`. Press F7 and check that the title stays usable and the process tree stays the same. Open a stock title dialog and verify the prompt disappears until the title is back on top. Check the archive remains inactive with its `mods.txt` entry absent.
3. Record the original save hashes before and after the isolated run. Record the replica's effective profile and distinguish stock title configuration writes from the bootstrap. Exercise the future coordinator separately by providing one bounded offer and verifying one real F7 edge delivers the typed request. The Lua mock covers that callback contract today.
