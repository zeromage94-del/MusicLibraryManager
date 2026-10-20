#Requires -Version 7.0
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$reportDir = Join-Path $root 'diagnostic/report'
$packageDir = Join-Path $root 'diagnostic/package'
$unpackedDir = Join-Path $root 'diagnostic/unpacked'
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null
$env:PYTHONUTF8 = '1'

Push-Location $root
Start-Transcript -Path (Join-Path $reportDir 'preparation.log') -Append | Out-Null
try {
    @'
Snowfall by Scott Buckley - CC BY 4.0
Official work and license: https://www.scottbuckley.com.au/library/snowfall/
License: https://creativecommons.org/licenses/by/4.0/
Authorized audio source: https://www.scottbuckley.com.au/library/wp-content/uploads/2018/12/sb_snowfall.mp3
This diagnostic uses one openly licensed track. The source MP3 is converted to M4A/AAC.
Only text evidence is uploaded. Test audio and Spotify album artwork are not redistributed.
'@ | Set-Content -LiteralPath (Join-Path $reportDir 'ATTRIBUTION.txt') -Encoding utf8
    @'
Scope: built spotdl.exe command-line integration for exactly one licensed track.
Artifact source: run 39476119104, MusicLibraryManager-Portatil-Windows-v0.7.
This diagnostic does not launch the GUI, test playlist downloads, or establish FLAC/lossless quality.
No cookies, account login, or user credentials are used or modified.
'@ | Set-Content -LiteralPath (Join-Path $reportDir 'DIAGNOSTIC.txt') -Encoding utf8

    python -m pip install 'mutagen>=1.47,<2'
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar mutagen.' }

    if (-not (Get-Command ffmpeg.exe -ErrorAction SilentlyContinue) -or
        -not (Get-Command ffprobe.exe -ErrorAction SilentlyContinue)) {
        choco install ffmpeg -y --no-progress
        if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar FFmpeg.' }
    }
    if (-not (Get-Command ffmpeg.exe -ErrorAction SilentlyContinue) -or
        -not (Get-Command ffprobe.exe -ErrorAction SilentlyContinue)) {
        throw 'FFmpeg e ffprobe devem estar disponiveis no PATH.'
    }

    $archives = @(Get-ChildItem -LiteralPath $packageDir -Recurse -File |
        Where-Object { $_.Name -eq 'MusicLibraryManager_Portatil_v0.7_Windows_x64.zip' })
    if ($archives.Count -ne 1) { throw 'Esperado exatamente um ZIP portatil v0.7.' }
    Expand-Archive -LiteralPath $archives[0].FullName -DestinationPath $unpackedDir -Force
    $executables = @(Get-ChildItem -LiteralPath $unpackedDir -Recurse -File |
        Where-Object { $_.Name -eq 'spotdl.exe' })
    if ($executables.Count -ne 1) { throw 'Esperado exatamente um spotdl.exe compilado.' }

    # The root-owned Python script pins one licensed source and enforces subprocess
    # timeouts, one output track, decoding, duration, tags, and embedded-cover checks.
    # It writes only JSON and text/log evidence to reportDir; audio stays elsewhere.
    python tools/test_licensed_download.py --spotdl $executables[0].FullName --report-dir $reportDir
    if ($LASTEXITCODE -ne 0) { throw "Diagnostico falhou (exit=${LASTEXITCODE})." }
} finally {
    Stop-Transcript | Out-Null
    Pop-Location
}
