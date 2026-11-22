[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Bundle,
    [Parameter(Mandatory)][string]$Workspace
)

$ErrorActionPreference = 'Stop'
$folder = (Resolve-Path -LiteralPath $Bundle).Path
$workspacePath = [IO.Path]::GetFullPath($Workspace)
if ($workspacePath.StartsWith($folder.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or
    $workspacePath -eq $folder -or (Test-Path -LiteralPath $workspacePath)) {
    throw 'Choose a new disposable workspace outside the bundle.'
}
$state = (New-Item -ItemType Directory -Path $workspacePath).FullName
$history = Join-Path $state 'history.sqlite3'
$settings = Join-Path $state 'preferences.ini'
$entry = Join-Path $folder 'NetCraft.exe'
$expectedRuntime = Join-Path $folder 'runtime\pythonw.exe'
$launchArgs = '--history "{0}" --settings "{1}"' -f $history, $settings
$launcher = Start-Process -FilePath $entry -ArgumentList $launchArgs -PassThru -WorkingDirectory $state -WindowStyle Hidden
$child = $null
try {
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    while ([DateTime]::UtcNow -lt $deadline -and -not $launcher.HasExited) {
        $owned = Get-CimInstance Win32_Process -Filter "ParentProcessId=$($launcher.Id) AND Name='pythonw.exe'"
        if ($owned -and $owned.ExecutablePath -ieq $expectedRuntime) {
            $child = Get-Process -Id $owned.ProcessId -ErrorAction SilentlyContinue
            if ($child -and $child.MainWindowHandle -ne 0 -and $child.MainWindowTitle.StartsWith('NetCraft') -and
                $child.Responding -and (Test-Path -LiteralPath $history -PathType Leaf)) { break }
        }
        Start-Sleep -Milliseconds 150
    }
    if (-not $child -or $child.MainWindowHandle -eq 0 -or
        -not $child.MainWindowTitle.StartsWith('NetCraft') -or -not $child.Responding -or
        -not (Test-Path -LiteralPath $history -PathType Leaf)) {
        throw 'The extracted launcher did not open a responsive Qt window with the isolated history.'
    }
    Write-Output 'NetCraft.exe opened the native Qt window using bundled pythonw.exe.'
    if (-not $child.CloseMainWindow() -or -not $launcher.WaitForExit(10000) -or $launcher.ExitCode -ne 0) {
        throw 'The packaged GUI did not close normally.'
    }
    & (Join-Path $folder 'runtime\python.exe') -I -B -c 'import sqlite3,sys; from pathlib import Path; db=sqlite3.connect(Path(sys.argv[1]).resolve().as_uri()+"?mode=ro",uri=True); assert db.execute("SELECT count(*) FROM runs").fetchone()==(0,); assert db.execute("SELECT count(*) FROM samples").fetchone()==(0,); assert db.execute("PRAGMA integrity_check").fetchone()==("ok",); db.close()' $history
    if ($LASTEXITCODE -ne 0) { throw 'The fresh launcher workspace was not an empty, valid history.' }
    Write-Output 'The launcher closed with exit 0. The isolated history was valid and empty.'
}
finally {
    if ($child -and -not $child.HasExited -and $child.MainWindowHandle -ne 0) {
        $null = $child.CloseMainWindow()
        $null = $launcher.WaitForExit(5000)
    }
}
