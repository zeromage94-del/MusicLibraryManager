#Requires -Version 5.1
param([switch]$SkipInstaller)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$env:PYTHONUTF8 = '1'

function RunPython {
    & python @args
    if ($LASTEXITCODE -ne 0) { throw "Falha Python exit=${LASTEXITCODE}: $($args -join ' ')" }
}

function TestExecutable {
    param([string]$Executable, [string[]]$Arguments, [string]$WorkingDirectory)
    $stdout = Join-Path $WorkingDirectory 'stdout.log'
    $stderr = Join-Path $WorkingDirectory 'stderr.log'
    # Start-Process waits for the windowed GUI too; invocation alone can return early.
    $process = Start-Process -FilePath $Executable -ArgumentList $Arguments `
        -WorkingDirectory $WorkingDirectory -Wait -PassThru `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    if ($process.ExitCode -ne 0) {
        Get-Content $stdout, $stderr -ErrorAction SilentlyContinue | Write-Host
        throw "Falha $Executable $($Arguments -join ' ') (exit=$($process.ExitCode))"
    }
}

Push-Location $root
try {
    # tiddl 3.4.x requires Python 3.13. Build Windows x64 binaries on Windows x64.
    RunPython -c "import sys, struct; assert sys.platform == 'win32' and sys.version_info[:2] == (3, 13) and struct.calcsize('P') == 8, 'Execute com Python 3.13 x64 no Windows.'"
    New-Item -ItemType Directory -Force -Path release, build | Out-Null
    $portable = Join-Path $root 'release/MusicLibraryManager_Portatil_v0.7_Windows_x64.zip'
    $setup = Join-Path $root 'release/MusicLibraryManager_Setup_v0.7_Windows_x64.exe'
    foreach ($artifact in @($portable, $setup)) {
        if (Test-Path $artifact) { Remove-Item $artifact -Force }
    }

    Write-Host '1/6 - Testes unitarios'
    RunPython -m unittest discover -s tests -q

    Write-Host '2/6 - EXE da interface (PyInstaller onedir)'
    RunPython -m PyInstaller --noconfirm --clean --onedir --windowed --name MusicLibraryManager `
      --collect-all keyring --collect-submodules keyring.backends `
      --distpath dist --workpath build/gui --specpath build/specs app.py

    Write-Host '3/6 - spotDL independente (PyInstaller onefile)'
    # spotDL's formatter initializes pykakasi at import time. Its dictionaries are
    # package data, including pykakasi/data/kanwadict4.db; collecting spotdl alone
    # does not include data belonging to this transitive dependency.
    RunPython -m PyInstaller --noconfirm --clean --onefile --console --name spotdl `
      --collect-all spotdl --collect-all yt_dlp --collect-data yt_dlp `
      --collect-data pykakasi `
      --distpath dist/MusicLibraryManager --workpath build/spotdl --specpath build/specs tools/spotdl_entry.py

    Write-Host '4/6 - tiddl independente (PyInstaller onefile)'
    RunPython -m PyInstaller --noconfirm --clean --onefile --console --name tiddl `
      --collect-all tiddl --collect-all typer `
      --distpath dist/MusicLibraryManager --workpath build/tiddl --specpath build/specs tools/tiddl_entry.py

    $bin = Join-Path $root 'dist/MusicLibraryManager'
    foreach ($name in @('MusicLibraryManager.exe','tiddl.exe','spotdl.exe')) {
        if (-not (Test-Path (Join-Path $bin $name))) { throw "Ausente: $name" }
    }
    Copy-Item 'README_WINDOWS.md' (Join-Path $bin 'LEIA-ME.txt') -Force
    Copy-Item 'TERCEIROS.md' (Join-Path $bin 'TERCEIROS.md') -Force
    RunPython scripts/collect_licenses.py

    Write-Host '5/6 - Verificacao dos executaveis sem depender do diretorio fonte'
    $smokeDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
    New-Item -ItemType Directory -Path $smokeDirectory | Out-Null
    try {
        TestExecutable (Join-Path $bin 'MusicLibraryManager.exe') @('--build-check') $smokeDirectory
        TestExecutable (Join-Path $bin 'tiddl.exe') @('--help') $smokeDirectory
        TestExecutable (Join-Path $bin 'spotdl.exe') @('--build-check') $smokeDirectory
        TestExecutable (Join-Path $bin 'spotdl.exe') @('--help') $smokeDirectory
    } finally {
        Remove-Item $smokeDirectory -Recurse -Force
    }

    Write-Host '6/6 - ZIP portatil + Instalador'
    Compress-Archive -Path $bin -DestinationPath $portable -CompressionLevel Optimal
    if (-not $SkipInstaller) {
        $iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($null -eq $iscc) {
            $inno = @(
                (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6/ISCC.exe'),
                (Join-Path $env:ProgramFiles 'Inno Setup 6/ISCC.exe')
            ) | Where-Object { Test-Path $_ } | Select-Object -First 1
            if (-not $inno) { throw 'Inno Setup nao encontrado. Execute: choco install innosetup -y' }
            $isccPath = $inno
        } else { $isccPath = $iscc.Source }
        & $isccPath 'packaging/MusicLibraryManager.iss'
        if ($LASTEXITCODE -ne 0) { throw 'Falha Inno Setup' }
        if (-not (Test-Path $setup)) { throw 'Setup.exe nao gerado' }
    }
    Get-ChildItem release/* | ForEach-Object { Write-Host ($_.Name + ': ' + $_.Length + ' bytes') }
} finally {
    Pop-Location
}
