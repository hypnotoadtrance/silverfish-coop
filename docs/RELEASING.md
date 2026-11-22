# Build and publish a Windows release candidate

The repository is private. GitHub Releases is where playtesters with repository access download the compiled Windows ZIP. Each release tag identifies the source revision used to build it. Keep the ZIP as a release asset and keep game files, saves, credentials, and local build directories outside Git. [GitHub Releases](https://github.com/hypnotoadtrance/silverfish-coop/releases)

## Build the ZIP on Windows

Use a local Windows x64 machine with the qualified Steam installation, the pinned UE4SS v4.0.0 release and game signature, and the matching C++ build dependencies. Build from one committed revision throughout. [Native add-on build](../native/README.md) · [Embedded runtime build and manifest pin](../native/embedded_runtime/README.md#build-and-layout)

1. Build the gameplay native DLL with the pinned UE4SS toolchain.
2. Build the embedded Python runtime from the same Git revision and link the embedded startup DLL with that runtime manifest’s SHA-256.
3. Run the drag-and-drop packager from the repository root. Set the paths in the example to your matching local artifacts. Use a new output filename.

```powershell
$sfRevision = (git rev-parse HEAD).Trim()
$sfTag = "rc-$($sfRevision.Substring(0,7))"
& $sfPython -m bootstrap.resident_drop `
  --output "$sfReleaseFolder\Silverfish-Coop-RC-$($sfRevision.Substring(0,7)).zip" `
  --version $sfTag --revision $sfRevision `
  --shipping "$sfGame\SilverFish\Binaries\Win64\SilverFish-Win64-Shipping.exe" `
  --ue4ss $sfUe4ssRelease --signatures $sfSignatures --include-loader `
  --cpp-addon $sfGameplayDll --embedded-addon $sfEmbeddedDll --embedded-runtime $sfRuntime
```

`$sfPython` is the Python interpreter with the repository requirements installed. `$sfReleaseFolder` should already exist. The packager reports the ZIP’s SHA-256. Use the complete ZIP for both first installs and updates. The package contains the mod and bundled runtime and loader files. Players use their own Steam installation.

## Verify and publish

1. Apply the exact ZIP to isolated test installs. At minimum, check that the Steam-style entry reaches the title menu, both seats can host and join, and singleplayer saves remain isolated. Run focused checks for the changed mechanics. [Portable title test](../tests/live_portable_title_smoke.py)
2. On GitHub, open **Releases → Draft a new release**. Use a tag such as `rc-d9978a3` and target the exact source commit used for the ZIP.
3. Summarize changes, supported game build, installation, known issues, and the ZIP’s SHA-256. Record automated checks and human playtesting separately. If the exact ZIP is awaiting human verification, say so.
4. Attach `Silverfish-Coop-RC-*.zip` under the release assets and select **This is a pre-release**. Publish once the upload is complete, then open the release and verify the asset name and checksum. Keep the automatically generated **Source code** downloads available for readers who need the source snapshot.

The current workflow builds locally and uploads through GitHub Releases. A future automated build would need a Windows runner with access to the pinned SDK dependencies and the qualified Steam executable. [GitHub’s release publishing guide](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository)
