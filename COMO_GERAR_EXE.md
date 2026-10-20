# Music Library Manager v0.7 — gerar instalador Windows automaticamente

## Método recomendado: GitHub Actions (sem instalar Python no seu computador)

1. Crie um repositório **privado** no GitHub (por exemplo, `music-library-manager`).
2. Envie **o conteúdo da pasta `MusicLibraryManager`** para a raiz do repositório, incluindo
   `.github/workflows/build-windows.yml` (pasta oculta!). Pode usar **Add file → Upload files**
   para código, mas confira que o workflow permaneceu em `.github/workflows/`.
   Forma confiável via Git:
   ```powershell
   cd MusicLibraryManager
   git init
   git add .
   git commit -m "Windows setup e portatil v0.7"
   git branch -M main
   git remote add origin https://github.com/SEU_USUARIO/music-library-manager.git
   git push -u origin main
   ```
3. Abra GitHub → repositório → **Actions** → `Compilar Windows (EXE + Setup + Portatil)`.
   Selecione **Run workflow** → **Run workflow**.
4. Na execução concluída, abra **Artifacts** e baixe `MusicLibraryManager-v0.7-Windows-x64`.
   Dentro estarão:
   - `MusicLibraryManager_Setup_v0.7_Windows_x64.exe` (instalador)
   - `MusicLibraryManager_Portatil_v0.7_Windows_x64.zip` (versão sem instalação)

Observação: a interface do GitHub e quotas do Actions podem mudar; conta e repositório
precisam ter Actions habilitado. O workflow não cria releases nem publica músicas.

## Compilar no próprio Windows (opcional)

Instale Python **3.13**, e Inno Setup 6 (`choco install innosetup -y` com terminal de administrador).
Execute `gerar_exe.bat`. O build roda os testes e compila executáveis independentes
para interface, tiddl e spotDL. Os resultados ficam em `release/`.

## Como funciona internamente

- `scripts/build_windows.ps1` — testes, PyInstaller GUI, tiddl.exe e spotdl.exe,
  auto-verificação com `--help`, pacote ZIP, compilação Inno Setup.
- `packaging/MusicLibraryManager.iss` — instalação por usuário, atalhos, desinstalador.
- `.github/workflows/build-windows.yml` — ambiente Windows x64 / Python 3.13.
- `tools/*_entry.py` — pontos de entrada dos CLI compactados.
- Os dados das playlists são armazenados em `%APPDATA%/MusicLibraryManager` e não serão apagados
  ao atualizar o aplicativo ou desinstalar.

## Dependências e limitações

- Não precisa de Python instalado para usar Setup ou portátil.
- FFmpeg continua externo; instale com `winget install --id Gyan.FFmpeg -e`.
- Se o spotDL precisar de Deno para certos sites, instale-o separadamente.
- Lidarr, Prowlarr, qBittorrent e Jellyfin são serviços externos opcionais.
- O executável GUI **não tem um único arquivo solitário**: a versão portátil mantém
  a pasta `_internal` e dois CLIs junto ao executável; isso evita problemas comuns
  com PySide6 e com a execução de subprogramas.
- A pipeline executa testes e valida o carregamento dos binários. Mesmo quando
  ela passa, é recomendado verificar visualmente o app e autenticações em uma
  instalação real no Windows 11 antes de distribuir amplamente.

**Situação atual:** este ZIP inclui os fontes e o pipeline de compilação; NÃO inclui
executáveis Windows compilados. Os arquivos .exe sairão da execução GitHub Actions.
