[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Wheel,
    [string]$BuildPython = '.venv/Scripts/python.exe',
    [string]$OutputDirectory = 'dist/portable-win-x64',
    [string]$CacheDirectory = 'dist/phase5-cache',
    [switch]$Offline
)

$ErrorActionPreference = 'Stop'
$packageWheel = Get-Item -LiteralPath $Wheel
if ($packageWheel.Name -notmatch '^netcraft_networking-([0-9]+\.[0-9]+\.[0-9]+)-py3-none-any\.whl$') {
    throw 'Provide a NetCraft release-candidate wheel built with python -m build.'
}
$packageVersion = $Matches[1]
$buildPythonPath = (Resolve-Path -LiteralPath $BuildPython).Path
$pipVersion = & $buildPythonPath -I -m pip --version
if ($LASTEXITCODE -ne 0 -or $pipVersion -notmatch '^pip 26\.3 ') {
    throw 'Use a dedicated build Python environment with pip 26.3 installed.'
}
if (Test-Path -LiteralPath $OutputDirectory) {
    throw 'Choose a new output directory to keep existing builds intact.'
}
$outputRoot = (New-Item -ItemType Directory -Path $OutputDirectory).FullName
$cacheRoot = (New-Item -ItemType Directory -Path $CacheDirectory -Force).FullName
$packageName = "NetCraft-$packageVersion-win-x64"
$bundle = (New-Item -ItemType Directory -Path (Join-Path $outputRoot $packageName)).FullName
$runtime = Join-Path $bundle 'runtime'
$app = (New-Item -ItemType Directory -Path (Join-Path $bundle 'app')).FullName
$wheelStage = (New-Item -ItemType Directory -Path (Join-Path $outputRoot 'wheel-stage')).FullName
$projectStage = (New-Item -ItemType Directory -Path (Join-Path $outputRoot 'project-stage')).FullName
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$content = Join-Path $PSScriptRoot 'bundle_contents.py'

function Get-VendorArchive([string]$Name, [string]$Url) {
    $path = Join-Path $cacheRoot $Name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        if ($Offline) { throw "Missing offline input $Name" }
        Invoke-WebRequest -Uri $Url -OutFile $path -TimeoutSec 120
    }
    return $path
}

function Assert-Command([string]$Operation) {
    if ($LASTEXITCODE -ne 0) { throw "$Operation failed with exit code $LASTEXITCODE" }
}

function Get-VerifiedInput([string]$Name, [string]$Url, [string]$Sha256) {
    $path = Get-VendorArchive $Name $Url
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ine $Sha256) {
        throw "The SHA-256 checksum differs for $Name."
    }
    return $path
}

function Copy-NormalizedText([string]$Path, [string]$Destination) {
    $data = [IO.File]::ReadAllText($Path).Replace("`r`n", "`n")
    [IO.File]::WriteAllText($Destination, $data)
}

$pythonArchive = Get-VendorArchive 'python-3.14.10-embed-amd64.zip' 'https://www.python.org/ftp/python/3.14.10/python-3.14.10-embed-amd64.zip'
$pythonDigest = '90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159'
if ((Get-FileHash -LiteralPath $pythonArchive -Algorithm SHA256).Hash -ine $pythonDigest) {
    throw 'The CPython archive hash differs from the Python release manifest.'
}
$sqliteArchive = Get-VendorArchive 'sqlite-dll-win-x64-3540100.zip' 'https://www.sqlite.org/2026/sqlite-dll-win-x64-3540100.zip'
$sqliteDigest = 'c2d09c8babb9399277055c057c97a53f1ffd7c5f11d0c1f2118ffdfa39f2daa4'
$actualSqlite = & $buildPythonPath -I -c 'import hashlib,sys; print(hashlib.file_digest(open(sys.argv[1],"rb"),"sha3_256").hexdigest())' $sqliteArchive
Assert-Command 'SQLite archive hashing'
if ($actualSqlite -ine $sqliteDigest) {
    throw 'The SQLite archive hash differs from the vendor manifest.'
}

Expand-Archive -LiteralPath $pythonArchive -DestinationPath $runtime
# The embedded interpreter has no Tcl/Tk extension and no retained binary imports libtommath.
foreach ($name in @('_remote_debugging.pyd','libtommath.dll')) {
    Remove-Item -LiteralPath (Join-Path $runtime $name)
}
$sqliteStage = Join-Path $outputRoot 'sqlite-stage'
Expand-Archive -LiteralPath $sqliteArchive -DestinationPath $sqliteStage
Copy-Item -LiteralPath (Join-Path $sqliteStage 'sqlite3.dll') -Destination (Join-Path $runtime 'sqlite3.dll')
Set-Content -LiteralPath (Join-Path $runtime 'python314._pth') -Encoding ascii -Value @('python314.zip', '.', '..\app')

$lock = Join-Path $PSScriptRoot 'requirements-win-x64.txt'
$target = @('--only-binary=:all:', '--platform', 'win_amd64', '--python-version', '3.14', '--implementation', 'cp', '--abi', 'cp314')
if (-not $Offline) {
    & $buildPythonPath -I -m pip --isolated download @target --no-deps --require-hashes --index-url https://pypi.org/simple --dest $cacheRoot -r $lock
    Assert-Command 'Locked Windows wheel download'
}
& $buildPythonPath -I -m pip --isolated install @target --no-index --find-links $cacheRoot --no-deps --require-hashes --no-compile --target $wheelStage -r $lock
Assert-Command 'Locked Windows wheel installation'
$projectInput = New-Item -ItemType Directory -Path (Join-Path $outputRoot 'project-input')
Copy-Item -LiteralPath $packageWheel.FullName -Destination $projectInput.FullName
& $buildPythonPath -I -m pip --isolated install --no-index --find-links $projectInput.FullName --only-binary=:all: --no-deps --no-compile --target $projectStage "netcraft-networking==$packageVersion"
Assert-Command 'NetCraft wheel installation'
& $buildPythonPath -I -B $content stage $app $wheelStage $projectStage
Assert-Command 'Desktop runtime staging'

foreach ($name in @('portable_entry.py','requirements-win-x64.txt')) {
    Copy-NormalizedText (Join-Path $PSScriptRoot $name) (Join-Path $bundle $name)
}
foreach ($name in @('LICENSE','THIRD_PARTY_NOTICES.md')) {
    Copy-NormalizedText (Join-Path $projectRoot $name) (Join-Path $bundle $name)
}
$qtLicenses = [ordered]@{
    'LGPL-3.0-only.txt' = 'da7eabb7bafdf7d3ae5e9f223aa5bdc1eece45ac569dc21b3b037520b4464768'
    'GPL-2.0-only.txt' = '8177f97513213526df2cf6184d8ff986c675afb514d4e68a404010521b880643'
    'GPL-3.0-only.txt' = '8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903'
    'Qt-GPL-exception-1.0.txt' = '40678d338ce53cd93f8b22b281a2ecbcaa3ee65ce60b25ffb0c462b0530846b2'
}
$qtNotices = (New-Item -ItemType Directory -Path (Join-Path $bundle 'licenses/qt') -Force).FullName
foreach ($license in $qtLicenses.GetEnumerator()) {
    $licensePath = Get-VerifiedInput $license.Key "https://raw.githubusercontent.com/qt/qtbase/v6.12.0/LICENSES/$($license.Key)" $license.Value
    Copy-Item -LiteralPath $licensePath -Destination $qtNotices
}
$opensslNotice = Get-VerifiedInput 'OpenSSL-3.5.9-LICENSE.txt' 'https://raw.githubusercontent.com/openssl/openssl/openssl-3.5.9/LICENSE.txt' '7d5450cb2d142651b8afa315b5f238efc805dad827d91ba367d8516bc9d49e7a'
Copy-Item -LiteralPath $opensslNotice -Destination (Join-Path $bundle 'licenses')
$qtSources = Get-VerifiedInput 'qtbase-v6.12.0.tar.gz' 'https://codeload.github.com/qt/qtbase/tar.gz/refs/tags/v6.12.0' 'dff6570582c272cbe744d2e460700318262af505460f4215276459970ae31b46'
$pysideSources = Get-VerifiedInput 'pyside-setup-v6.12.0.tar.gz' 'https://codeload.github.com/pyside/pyside-setup/tar.gz/refs/tags/v6.12.0' 'dce2a944b34320e51a0ec8100cb983956399b97dec386f0fbe39e1b52571a336'
$pythonSources = Get-VerifiedInput 'cpython-v3.14.10.tar.gz' 'https://codeload.github.com/python/cpython/tar.gz/refs/tags/v3.14.10' '4fae8dc3af30ad4accf6dd20cbd6c80c7882900bb91684072a9ea3475768068c'
& $buildPythonPath -I -B (Join-Path $PSScriptRoot 'qt_notices.py') $qtSources $qtNotices --pyside $pysideSources --python $pythonSources
Assert-Command 'Qt source notices'

$pythonDependencies = @(
    @('mpdecimal-4.0.0','COPYRIGHT.txt','2713324211652ce4a60e6e21e54f9dc3004299e591b0933a352f1a89c5fb53c2'),
    @('xz-5.2.5','COPYING','bcb02973ef6e87ea73d331b3a80df7748407f17efdb784b61b47e0e610d3bb5c'),
    @('xz-5.2.5','COPYING.GPLv2','8177f97513213526df2cf6184d8ff986c675afb514d4e68a404010521b880643'),
    @('xz-5.2.5','COPYING.GPLv3','8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903'),
    @('xz-5.2.5','COPYING.LGPLv2.1','dc626520dcd53a22f727af3ee42c770e56c97a64fe3adb063799d8ab032fe551'),
    @('zlib-ng-2.2.4','LICENSE.md','6c9f0d975b41afaa34d22f55bb8986ce69e5cb7ad327cb2b28820cd425edf5ee'),
    @('zstd-1.5.7','LICENSE','7055266497633c9025b777c78eb7235af13922117480ed5c674677adc381c9d8')
)
foreach ($entry in $pythonDependencies) {
    $tag, $name, $digest = $entry
    $notice = Get-VerifiedInput "$tag-$name" "https://raw.githubusercontent.com/python/cpython-source-deps/$tag/$name" $digest
    $directory = New-Item -ItemType Directory -Force -Path (Join-Path $bundle "licenses/cpython/$tag")
    Copy-Item -LiteralPath $notice -Destination (Join-Path $directory.FullName $name)
}

$compilerId = 'microsoft.net.compilers.toolset/4.14.0/microsoft.net.compilers.toolset.4.14.0.nupkg'
$referenceId = 'microsoft.netframework.referenceassemblies.net48/1.0.3/microsoft.netframework.referenceassemblies.net48.1.0.3.nupkg'
$nuget = 'https://api.nuget.org/v3-flatcontainer'
$compiler = Get-VerifiedInput ($compilerId.Split('/')[-1]) "$nuget/$compilerId" '941a9cf3ea618d88d01a3dd6b1a45a06bcf07716a9f81ce4031caa3edd24a845'
$references = Get-VerifiedInput ($referenceId.Split('/')[-1]) "$nuget/$referenceId" '8a7e348538e7eb91351696911689f49e3d4f63f8bab517432bbe159b8b1104a2'
$compilerStage = Join-Path $outputRoot 'compiler'
$referenceStage = Join-Path $outputRoot 'references'
& $buildPythonPath -I -m zipfile -e $compiler $compilerStage
Assert-Command 'Roslyn extraction'
& $buildPythonPath -I -m zipfile -e $references $referenceStage
Assert-Command '.NET Framework references extraction'
$referenceRoot = Join-Path $referenceStage 'build/.NETFramework/v4.8'
$targetReferences = @('mscorlib.dll','System.dll','System.Windows.Forms.dll') |
    ForEach-Object { "/reference:$(Join-Path $referenceRoot $_)" }
$launcher = Join-Path $PSScriptRoot 'Launcher.cs'
& (Join-Path $compilerStage 'tasks/net472/csc.exe') /nologo /noconfig /nostdlib+ /deterministic+ /debug- /target:winexe /platform:x64 /optimize+ @targetReferences "/pathmap:$PSScriptRoot=/src" "/out:$(Join-Path $bundle 'NetCraft.exe')" $launcher
Assert-Command 'Windows launcher compile'

if (-not (Test-Path -LiteralPath (Join-Path $app 'PySide6/plugins/platforms/qwindows.dll') -PathType Leaf)) {
    throw 'The package is missing the Qt Windows platform plugin.'
}
$embedded = Join-Path $runtime 'python.exe'
$runtimeInfo = & $embedded -I -B -c 'import json, platform, ssl, sqlite3, struct; from importlib.metadata import version; from PySide6.QtCore import qVersion; import dns,numpy,pyqtgraph,shiboken6; assert struct.calcsize("P")==8 and sqlite3.sqlite_version=="3.54.1"; print(json.dumps(dict(app=version("netcraft-networking"),python=platform.python_version(),sqlite=sqlite3.sqlite_version,openssl=ssl.OPENSSL_VERSION,qt=qVersion(),dependencies={n:version(n) for n in ("dnspython","PySide6-Essentials","shiboken6","pyqtgraph","numpy","colorama")})))'
Assert-Command 'Bundled runtime validation'
$manifest = $runtimeInfo | ConvertFrom-Json -AsHashtable
$manifest['netcraft_wheel_sha256'] = (Get-FileHash -LiteralPath $packageWheel.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
$manifest['build_tools'] = [ordered]@{ pip = '26.3'; roslyn = '4.14.0'; framework_target = '4.8' }
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $bundle 'runtime-manifest.json') -Encoding utf8
Write-Output $runtimeInfo
$nativeFiles = [ordered]@{}
Get-ChildItem -LiteralPath $bundle -Recurse -File | Where-Object { $_.Extension -in '.exe','.dll','.pyd' } |
    Sort-Object FullName | ForEach-Object {
        $relative = [IO.Path]::GetRelativePath($bundle,$_.FullName).Replace('\','/')
        $nativeFiles[$relative] = [ordered]@{
            file_version = $_.VersionInfo.FileVersion
            product = $_.VersionInfo.ProductName
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
$nativeFiles | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $bundle 'native-files.json') -Encoding utf8
@'
NetCraft portable for Windows x64

Extract the entire ZIP. Open NetCraft.exe from the extracted folder.
Keep NetCraft.exe, portable_entry.py, app and runtime together.

NetCraft stores history and preferences outside this folder. A new workspace
starts empty and no probes run until you request one.
NetCraft uses the Qt and Qt for Python community libraries as separate DLLs.
See licenses/qt/ for the GNU LGPL 3.0 and GPL texts and THIRD_PARTY_NOTICES.md
for component licenses, source locations and shared-library replacement.
This candidate is unsigned and may display a Windows trust warning.
'@ | Set-Content -LiteralPath (Join-Path $bundle 'START-HERE.txt') -Encoding utf8

$archive = Join-Path $outputRoot "$packageName.zip"
& $embedded -I -B $content pack $bundle $archive
Assert-Command 'Reproducible ZIP creation'
& $embedded -I -B $content verify $archive
Assert-Command 'ZIP inventory verification'
$digest = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
"$digest  $packageName.zip" | Set-Content -LiteralPath "$archive.sha256" -Encoding ascii
Write-Output "$digest  $archive"
