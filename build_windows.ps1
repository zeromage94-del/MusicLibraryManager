#Requires -Version 5.1
param([switch]$SkipInstaller)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root
New-Item -ItemType Directory -Force -Path (Join-Path $root 'release') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $root 'build') | Out-Null
$env:PYTHONUTF8 = '1'

function RunPython {
    & python @args
    if ($LASTEXITCODE -ne 0) { throw "Falha Python exit=$LASTEXITCODE: $($args -join ' ')" }
}
# Windows e Python 3.13: requisito atual do tiddl 3.4.x.
$ver = (& python -c 'import sys; print(sys.version_info.major, sys.version_info.minor)')
if ($LASTEXITCODE -ne 0 -or $ver.Trim() -ne '3 13') { throw 'Execute com Python 3.13 no Windows.' }

Write-Host '1/6 - Testes unitarios'
RunPython -m unittest discover -s tests -q
Write-Host '2/6 - EXE da interface (PyInstaller onedir)'
RunPython -m PyInstaller --noconfirm --clean --onedir --windowed --name MusicLibraryManager `
  --collect-all keyring --collect-submodules keyring.backends `
  --distpath dist --workpath build/gui --specpath build/specs app.py

Write-Host '3/6 - spotDL independente (PyInstaller onefile)'
RunPython -m PyInstaller --noconfirm --clean --onefile --console --name spotdl `
  --collect-all spotdl --collect-all yt_dlp --collect-data yt_dlp `
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

Write-Host '5/6 - Verificacao de empacotamento'
& (Join-Path $bin 'MusicLibraryManager.exe') --build-check
if ($LASTEXITCODE -ne 0) { throw 'GUI --build-check falhou' }
& (Join-Path $bin 'tiddl.exe') --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'tiddl.exe --help falhou' }
& (Join-Path $bin 'spotdl.exe') --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'spotdl.exe --help falhou' }

Write-Host '6/6 - ZIP portatil + Instalador'
$portable = Join-Path $root 'release/MusicLibraryManager_Portatil_v0.7_Windows_x64.zip'
if (Test-Path $portable) { Remove-Item $portable -Force }
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
   $setup = Join-Path $root 'release/MusicLibraryManager_Setup_v0.7_Windows_x64.exe'
   if (-not (Test-Path $setup)) { throw 'Setup.exe nao gerado' }
}
Get-ChildItem release/* | ForEach-Object { Write-Host ($_.Name + ': ' + $_.Length + ' bytes') }
