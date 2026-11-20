[CmdletBinding()]
param([string]$Game)

$ErrorActionPreference = 'Stop'
$pair = '__REPAIR_PAIR_JSON__' | ConvertFrom-Json
$names = @('SilverfishCoopCoordinator.exe', 'main.dll')
$shipping = 'SilverFish\Binaries\Win64\SilverFish-Win64-Shipping.exe'

function Confirm-GameClosed {
    if (Get-Process -Name 'SilverFish','SilverFish-Win64-Shipping','SilverfishCoopCoordinator' -ErrorAction SilentlyContinue) {
        throw 'Close Project Silverfish and then run Update.cmd again.'
    }
}

function Find-Game {
    $steam = @(
        (Get-ItemPropertyValue -LiteralPath 'HKCU:\Software\Valve\Steam' -Name SteamPath -ErrorAction SilentlyContinue),
        (Get-ItemPropertyValue -LiteralPath 'HKLM:\SOFTWARE\WOW6432Node\Valve\Steam' -Name InstallPath -ErrorAction SilentlyContinue),
        "$(${env:ProgramFiles(x86)})\Steam",
        "$env:ProgramFiles\Steam"
    ) | Where-Object { $_ }
    foreach ($folder in $steam) {
        $libraries = @($folder)
        $index = Join-Path $folder 'steamapps\libraryfolders.vdf'
        if (Test-Path -LiteralPath $index -PathType Leaf) {
            $libraries += Select-String -LiteralPath $index -Pattern '^\s*"path"\s+"([^"]+)"' |
                ForEach-Object { $_.Matches[0].Groups[1].Value.Replace('\\', '\') }
        }
        foreach ($library in $libraries) {
            $candidate = Join-Path $library 'steamapps\common\Project Silverfish'
            if (Test-Path -LiteralPath (Join-Path $candidate $shipping) -PathType Leaf) { return $candidate }
        }
    }
    Write-Host 'In Steam, open Project Silverfish > Manage > Browse local files.'
    return (Read-Host 'Paste that folder path and press Enter').Trim().Trim('"')
}

function Require-Ordinary([string]$Path, [bool]$Directory) {
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -ne $Directory -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Expected a regular filesystem path at $Path"
    }
}

function Read-Sha([string]$Path) {
    Require-Ordinary $Path $false
    $stream = [IO.File]::OpenRead($Path)
    try {
        $sha = [Security.Cryptography.SHA256]::Create()
        try {
            return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
        } finally {
            $sha.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
}

function Confirm-Pair($Paths, [string]$Version) {
    foreach ($name in $names) {
        if ((Read-Sha $Paths[$name]) -ne $pair.$name.$Version) {
            throw "The $Version checksum did not match for $name"
        }
    }
}

try {
    Confirm-GameClosed
    if (!$Game) { $Game = Find-Game }
    if (!$Game -or ![IO.Path]::IsPathRooted($Game)) { throw 'Choose the full path to Project Silverfish.' }
    $gamePath = [IO.Path]::GetFullPath($Game)
    $root = [IO.Path]::GetPathRoot($gamePath)
    $cursor = $root
    foreach ($segment in ($gamePath.Substring($root.Length) -split '[\\/]' | Where-Object { $_ })) {
        $cursor = Join-Path $cursor $segment
        Require-Ordinary $cursor $true
    }
    Require-Ordinary (Join-Path $gamePath $shipping) $false
    $dllDirectory = $gamePath
    foreach ($segment in @('SilverFish','Binaries','Win64','ue4ss','Mods','SilverfishCoopBootstrap','dlls')) {
        $dllDirectory = Join-Path $dllDirectory $segment
        Require-Ordinary $dllDirectory $true
    }

    $original = @{}
    $payload = @{}
    $backup = @{}
    $candidate = @{}
    $before = @{}
    $repair = Join-Path $gamePath 'SilverfishCoopTitleRepair-4917824'
    Require-Ordinary (Join-Path $PSScriptRoot 'payload') $true
    foreach ($name in $names) {
        $original[$name] = Join-Path $dllDirectory $name
        $payload[$name] = Join-Path $PSScriptRoot "payload\$name"
        $backup[$name] = Join-Path $repair "$name.bak"
    }
    Confirm-Pair $payload 'new'
    foreach ($name in $names) {
        $before[$name] = Read-Sha $original[$name]
        if ($before[$name] -notin @($pair.$name.old, $pair.$name.new)) {
            throw "This repair requires the exact trial-4917824 installation. Unexpected checksum for $name"
        }
    }
    if ($before['main.dll'] -eq $pair.'main.dll'.new -and
        $before['SilverfishCoopCoordinator.exe'] -eq $pair.'SilverfishCoopCoordinator.exe'.new) {
        Write-Host 'The title repair is already installed.'
        exit 0
    }

    [void][IO.Directory]::CreateDirectory($repair)
    Require-Ordinary $repair $true
    $lockPath = Join-Path $repair 'update.lock'
    if (Test-Path -LiteralPath $lockPath) { Require-Ordinary $lockPath $false }
    $lock = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    try {
        $nonce = [guid]::NewGuid().ToString('N')
        foreach ($name in $names) {
            if ((Read-Sha $original[$name]) -ne $before[$name]) { throw 'The installed title files changed during setup.' }
            if (!(Test-Path -LiteralPath $backup[$name])) {
                if ($before[$name] -ne $pair.$name.old) { throw "The original backup for $name is missing." }
                [IO.File]::Copy($original[$name], $backup[$name])
            }
            $candidate[$name] = Join-Path $repair "$name.$nonce.tmp"
            [IO.File]::Copy($payload[$name], $candidate[$name])
        }
        Confirm-Pair $backup 'old'
        Confirm-Pair $candidate 'new'
        Confirm-GameClosed

        try {
            foreach ($name in $names) {
                if ((Read-Sha $original[$name]) -ne $before[$name]) { throw 'The installed title files changed during setup.' }
                if ($before[$name] -ne $pair.$name.new) {
                    [IO.File]::Replace($candidate[$name], $original[$name], [NullString]::Value)
                }
            }
            Confirm-Pair $original 'new'
        } catch {
            $failure = $_
            try {
                foreach ($name in $names) {
                    $current = Read-Sha $original[$name]
                    if ($current -eq $pair.$name.old) { continue }
                    if ($current -ne $pair.$name.new) { throw "Unexpected checksum while restoring $name" }
                    $restore = Join-Path $repair "$name.$nonce.restore"
                    [IO.File]::Copy($backup[$name], $restore)
                    try {
                        if ((Read-Sha $restore) -ne $pair.$name.old) { throw "Backup checksum changed for $name" }
                        [IO.File]::Replace($restore, $original[$name], [NullString]::Value)
                    } finally {
                        [IO.File]::Delete($restore)
                    }
                }
                Confirm-Pair $original 'old'
            } catch {
                throw "Could not restore the original pair. The verified backups are in $repair. $($_.Exception.Message)"
            }
            throw "The repair failed and the original pair was restored. $($failure.Exception.Message)"
        }
    } finally {
        try {
            foreach ($path in $candidate.Values) { [IO.File]::Delete($path) }
        } finally {
            $lock.Dispose()
        }
    }
    Write-Host 'Updated. Launch Project Silverfish through Steam and press F7 on the title screen.'
} catch {
    Write-Host "Co-op title repair failed. $($_.Exception.Message)"
    exit 1
}
