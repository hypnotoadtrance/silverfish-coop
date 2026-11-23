# Build and publish a Windows release candidate

The mod is a proof of concept in heavy development. Publish Windows release candidates on GitHub Releases for people testing and [reporting bugs](https://github.com/hypnotoadtrance/silverfish-coop/issues/new). Ask players to wait for a stable release for regular play. Each release tag identifies the source revision used to build it. Keep the ZIP as a release asset and keep game files, saves, credentials, and local build directories outside Git. [GitHub Releases](https://github.com/hypnotoadtrance/silverfish-coop/releases)

## Build the ZIP on Windows

Use a local Windows x64 machine with the qualified Steam installation, the pinned UE4SS v4.0.0 release and game signature, and the matching C++ build dependencies. Build from one committed revision throughout. [Native add-on build](../native/README.md) · [Embedded runtime build and manifest pin](../native/embedded_runtime/README.md#build-and-layout)

1. Build the gameplay native DLL with the pinned UE4SS toolchain.
2. Build the embedded Python runtime from the same Git revision and link the embedded startup DLL with that runtime manifest’s SHA-256.
3. Run `bootstrap.resident_drop` from the repository root. Set the paths in the example to your matching local artifacts. Use a new output filename. The older installer-based and F7 packagers are still available for their test layouts. [Legacy tooling](../bootstrap/README.md#maintainer-setup-for-test-layouts)

```powershell
$sfRevision = (git rev-parse HEAD).Trim()
$sfTag = "rc-$($sfRevision.Substring(0,7))"
& $sfPython -m bootstrap.resident_drop `
  --output "$sfReleaseFolder\Silverfish-Coop-RC-$($sfRevision.Substring(0,7)).zip" `
  --version $sfTag --revision $sfRevision `
  --shipping "$sfGame\SilverFish\Binaries\Win64\SilverFish-Win64-Shipping.exe" `
  --ue4ss $sfUe4ssRelease --signatures $sfSignatures --include-loader `
  --cpp-addon $sfGameplayDll --embedded-addon $sfEmbeddedDll --embedded-runtime $sfRuntime `
  --steam-api $sfSteamApi
```

`$sfPython` is the Python interpreter with the repository requirements installed. `$sfSteamApi` points to the matching redistributable `steam_api64.dll` from Steamworks. `$sfReleaseFolder` should already exist. The packager reports the ZIP’s SHA-256. Use the complete ZIP for both first installs and updates. The package contains the mod and bundled runtime and loader files. Players use their own Steam installation.

## Verify and publish

1. Apply the exact ZIP to a clean isolated install and over an older supported resident install. Check that the game reaches the in-game title menu, both seats can host and join, and singleplayer saves remain isolated. Verify loading an existing co-op campaign when the release supports it. If a fundamental change makes earlier campaigns incompatible, verify the documented reset and fresh campaign instead. Keep the upgrade replica's old runtime directory and local descriptor so the test exercises the overwrite players perform. Run focused checks for the changed mechanics. [Portable title test](../tests/live_portable_title_smoke.py)
2. On GitHub, open **Releases → Draft a new release**. Use a tag such as `rc-d9978a3` and target the exact source commit used for the ZIP.
3. State that the candidate is for testing and bug reports. Summarize changes, supported game build, installation, known issues, and the ZIP’s SHA-256. Record automated checks and human playtesting separately. If the exact ZIP is awaiting human verification, say so. Give testers a short list of actions, expected results, and details to include if they report a problem.
4. Attach `Silverfish-Coop-RC-*.zip` under the release assets and select **This is a pre-release**. Publish once the upload is complete, then open the release and verify the asset name and checksum. Keep the automatically generated **Source code** downloads available for readers who need the source snapshot.

The current workflow builds locally and uploads through GitHub Releases. A future automated build would need a Windows runner with access to the pinned SDK dependencies and the qualified Steam executable. [GitHub’s release publishing guide](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository)

## Releases that require a fresh campaign

Backward compatibility is preferred during development. A fundamental change may require a fresh co-op campaign. In that release's notes, name the affected versions, say whether migration is available, and explain that starting fresh resets the host world and its guest characters. Include the reset steps in the release itself. Tell hosts to close the game, pause Steam Cloud for Project Silverfish if enabled, and move `%LOCALAPPDATA%\SilverfishCoop\Resident\campaigns` and the optional `%LOCALAPPDATA%\SilverFish\Saved\SaveGames\SilverfishCoopCloud` folder to a backup outside AppData. Explain that hosts should leave the parent `SaveGames` folder in place, and then start a new co-op campaign. Include a link to the full instructions, including custom workspace locations and how to prevent automatic re-import of incompatible cloud backups. [Player reset instructions](PLAYING-COOP.md#reset-co-op-campaigns)
