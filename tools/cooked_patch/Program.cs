using System.Security.Cryptography;
using System.Text.Json;
using UAssetAPI;
using UAssetAPI.ExportTypes;
using UAssetAPI.Kismet.Bytecode;
using UAssetAPI.Kismet.Bytecode.Expressions;
using UAssetAPI.UnrealTypes;
using UAssetAPI.Unversioned;

const string assetFileName = "Player_FP.uasset";
const string exportsFileName = "Player_FP.uexp";
const string sourceAssetHash = "DAC83F0A6C22F66AAA5B3FC31B850FFFA4A8913136C00ED4C087A91BD845FCA7";
const string sourceExportsHash = "5CB2680D11AFA31415BB7CD3E8A311D00894B085A5E461DA88924DACB4BE7719";
const int originalDeathEntry = 26893;
const int expectedFunctions = 223;

if (args.Length != 3 || args.Contains("--help") || args.Contains("-h"))
{
    Console.Error.WriteLine("Usage: CookedPatch <stock Player_FP.uasset> <Silverfish UE5.4 .usmap> <new output directory>");
    return args.Contains("--help") || args.Contains("-h") ? 0 : 2;
}

string? temporary = null;
try
{
    var sourcePath = Path.GetFullPath(args[0]);
    var mappingsPath = Path.GetFullPath(args[1]);
    var destination = Path.TrimEndingDirectorySeparator(Path.GetFullPath(args[2]));
    var sourceDirectory = Path.GetDirectoryName(sourcePath)
        ?? throw new InvalidDataException("The source must be a stock Player_FP.uasset file.");
    var parent = Path.GetDirectoryName(destination)
        ?? throw new InvalidDataException("The output must be a new directory under an existing parent.");

    Require(Path.GetFileName(sourcePath) == assetFileName, "Expected the original Player_FP.uasset filename.");
    Require(Path.GetExtension(mappingsPath).Equals(".usmap", StringComparison.OrdinalIgnoreCase) &&
        File.Exists(mappingsPath), "Supply the matching Silverfish .usmap.");
    Require(Directory.Exists(parent), "The output parent directory must exist.");
    Require(!Path.Exists(destination), "Choose a new output directory.");
    Require(!IsInside(sourceDirectory, destination), "Choose an output directory outside the input directory.");

    var pairedPath = Path.Combine(sourceDirectory, exportsFileName);
    Require(Sha256(sourcePath) == sourceAssetHash && Sha256(pairedPath) == sourceExportsHash,
        "The cooked files do not match the qualified stock player package.");

    var mapping = new Usmap(mappingsPath);
    var asset = new UAsset(sourcePath, EngineVersion.VER_UE5_4, mapping);
    Require(asset.UseSeparateBulkDataFiles && asset.VerifyBinaryEquality(),
        "The untouched package does not round-trip as its original split bytes.");

    var die = FindDie(asset);
    var originalScript = die.ScriptBytecode;
    AssertOriginalScript(asset, originalScript);

    die.ScriptBytecode =
    [
        new EX_Return { ReturnExpression = new EX_Nothing() },
        new EX_EndOfScript(),
    ];

    temporary = Path.Combine(parent, $".silverfish-death-probe-{Guid.NewGuid():N}");
    Directory.CreateDirectory(temporary);
    var outputAsset = Path.Combine(temporary, assetFileName);
    var outputExports = Path.Combine(temporary, exportsFileName);
    asset.Write(outputAsset);

    var readback = new UAsset(outputAsset, EngineVersion.VER_UE5_4, mapping);
    var patchedDie = FindDie(readback);
    Require(readback.VerifyBinaryEquality() &&
        patchedDie.ScriptBytecode is [EX_Return { ReturnExpression: EX_Nothing }, EX_EndOfScript],
        "The generated package does not read back as the expected no-op wrapper.");

    // Recover both exact input hashes by reversing only the targeted function.
    patchedDie.ScriptBytecode = originalScript;
    readback.Write(out var recoveredAsset, out var recoveredExports);
    using (recoveredAsset)
    using (recoveredExports)
    {
        Require(recoveredExports is not null &&
            Sha256Bytes(recoveredAsset.ToArray()) == sourceAssetHash &&
            Sha256Bytes(recoveredExports.ToArray()) == sourceExportsHash,
            "Reversing the Die edit did not recover the exact stock package.");
    }

    var result = new
    {
        output = destination,
        changed = "Player_FP_C:Die",
        function_count = expectedFunctions,
        stock_recovered = true,
        uasset_sha256 = Sha256(outputAsset),
        uexp_sha256 = Sha256(outputExports),
    };
    Directory.Move(temporary, destination);
    temporary = null;
    Console.WriteLine(JsonSerializer.Serialize(result));
    return 0;
}
catch (Exception failure)
{
    Console.Error.WriteLine(failure.Message);
    return 1;
}
finally
{
    if (temporary is not null && Directory.Exists(temporary))
    {
        File.Delete(Path.Combine(temporary, assetFileName));
        File.Delete(Path.Combine(temporary, exportsFileName));
        Directory.Delete(temporary);
    }
}

static bool IsInside(string directory, string path)
{
    var root = Path.TrimEndingDirectorySeparator(directory);
    return path.Equals(root, StringComparison.OrdinalIgnoreCase) ||
        path.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
}

static void Require(bool condition, string message)
{
    if (!condition) throw new InvalidDataException(message);
}

static string Sha256(string file)
{
    using var stream = File.OpenRead(file);
    return Convert.ToHexString(SHA256.HashData(stream));
}

static string Sha256Bytes(byte[] data) => Convert.ToHexString(SHA256.HashData(data));

static FunctionExport FindDie(UAsset asset)
{
    var functions = asset.Exports.OfType<FunctionExport>().ToArray();
    Require(functions.Length == expectedFunctions, "Unexpected player function count.");
    var die = functions.Single(function => function.ObjectName.ToString() == "Die");
    Require(die.FunctionFlags == (EFunctionFlags.FUNC_BlueprintCallable | EFunctionFlags.FUNC_BlueprintEvent),
        "Unexpected Die function flags.");
    return die;
}

static void AssertOriginalScript(UAsset asset, KismetExpression[] script)
{
    Require(script is
    [
        EX_LetValueOnPersistentFrame,
        EX_LetValueOnPersistentFrame,
        EX_LetValueOnPersistentFrame,
        EX_FinalFunction { Parameters: [EX_IntConst { Value: originalDeathEntry }] } call,
        EX_Return { ReturnExpression: EX_Nothing },
        EX_EndOfScript,
    ] && call.StackNode.IsExport() &&
        call.StackNode.ToExport(asset).ObjectName.ToString() == "ExecuteUbergraph_Player_FP",
        "The stock Die wrapper has an unexpected bytecode shape.");
}
