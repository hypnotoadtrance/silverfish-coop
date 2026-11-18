# Local cooked death probe

This opt-in lab tool builds a cooked package pair from the user's extracted copy of the qualified Project Silverfish build. It rewrites the single Player_FP_C:Die Blueprint wrapper to return immediately. Place the resulting package in an isolated co-op lab stage only after checking retoc conversion and mount precedence. Finishing the native death handoff also requires a co-op-scoped runtime listener.

Run with .NET 8. The project pins UAssetAPI 1.1.0, checks the original split files, and verifies that reversing the edit recovers both stock SHA-256 hashes. The output directory must be new and separate from the input package directory.

```powershell
dotnet run --project tools/cooked_patch -c Release -- `
  'C:\Path\To\Converted\SilverFish\Content\Classes\Player\Player_FP.uasset' `
  'C:\Path\To\SilverFish.usmap' `
  'C:\Path\To\Scratch\death-probe'
```

Keep the generated pair local. Retoc conversion, stage setup, and runtime validation are separate operations. The stock Steam installation and the input package pair are source data.

Build the lab overlay with retoc 0.1.5. Put the generated pair under `SilverFish/Content/Classes/Player` in a scratch input directory and place the matching extracted `scriptobjects.bin` at that directory's root. Convert the directory with `retoc to-zen --version UE5_4` and name the output `zzzz_SilverfishCoop_Death_P.utoc`. Verify the container before copying its three files into `stage/SilverFish/Content/Paks` in a dedicated lab workspace.

The launcher checks all three fingerprints and uses a different connection compatibility fingerprint for this lab. Launch both processes from qualified lab stages. Run `tests/live_smoke.py --work LAB_WORKSPACE --native-death` to exercise the reflected function, check that the native Dead flag stays clear, and verify that co-op downing and reviving use the same pawn. The --native-damage flag invokes the stock direct-damage function against a temporary independent guest save and records health for both players before and after.
