# Music Library Manager v0.7 — gerar instalador Windows automaticamente

## Método recomendado: GitHub Actions (sem instalar Python no seu computador)

1. Abra [zeromage94-del/MusicLibraryManager](https://github.com/zeromage94-del/MusicLibraryManager)
   → **Actions** → **Gerar instalador Windows**.
2. Selecione **Run workflow**, escolha a branch desejada e execute. O workflow também
   roda automaticamente nos pull requests e em atualizações da branch `main`.
3. Na execução concluída com sucesso, abra **Artifacts** e baixe:
   - `MusicLibraryManager-Setup-Windows-v0.7`, contendo
     `MusicLibraryManager_Setup_v0.7_Windows_x64.exe` (instalador).
   - `MusicLibraryManager-Portatil-Windows-v0.7`, contendo
     `MusicLibraryManager_Portatil_v0.7_Windows_x64.zip` (versão sem instalação).

O Actions compila diretamente os arquivos versionados da branch selecionada.
O ZIP de fontes v0.7 mantido no repositório é histórico e não é usado no build;
não é necessário extraí-lo nem corrigir scripts manualmente no YAML.

Observação: a interface do GitHub e quotas do Actions podem mudar; conta e repositório
precisam ter Actions habilitado. O workflow não cria releases nem publica músicas.

## Compilar no próprio Windows (opcional)

Instale Python **3.13 x64** com o launcher `py` e Inno Setup 6
(`choco install innosetup -y` com terminal de administrador). No checkout do repositório,
execute `gerar_exe.bat`. Ele prepara `.venv`, instala `requirements-build.txt` e
executa o mesmo script usado no Actions. O build roda os testes e compila executáveis
independentes para interface, tiddl e spotDL. Os resultados ficam em `release/`.

Para gerar apenas os executáveis e o ZIP portátil, sem precisar de Inno Setup,
abra o PowerShell na raiz do repositório e execute:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-build.txt
.\scripts\build_windows.ps1 -SkipInstaller
```

O script aceita Windows PowerShell 5.1 ou PowerShell 7. O runner `windows-2022`
do Actions gera executáveis x64 destinados ao Windows 11.

## Como funciona internamente

- `scripts/build_windows.ps1` — testes em `tests/`, PyInstaller GUI, tiddl.exe e spotdl.exe,
  verificação dos executáveis fora do diretório fonte, pacote ZIP e compilação Inno Setup.
- `requirements-build.txt` — dependências do aplicativo, PyInstaller e bibliotecas dos testes.
- `packaging/MusicLibraryManager.iss` — instalação por usuário, atalhos, desinstalador.
- `.github/workflows/build-windows.yml` — ambiente Windows x64 / Python 3.13.
- `tools/*_entry.py` — pontos de entrada dos CLI compactados.
- O spotDL inclui os dados do `pykakasi`, incluindo `data/kanwadict4.db`.
  A verificação `spotdl.exe --build-check` testa a transliteração com os dicionários
  empacotados; `--help` também é executado nos dois CLIs.
- `scripts/collect_licenses.py` — coleta licenças e avisos dos pacotes distribuídos.
- Os dados das playlists são armazenados em `%APPDATA%/MusicLibraryManager` e não serão apagados
  ao atualizar o aplicativo ou desinstalar.

## Dependências e limitações

- Não precisa de Python instalado para usar Setup ou portátil.
- FFmpeg continua externo; instale com `winget install --id Gyan.FFmpeg -e`.
- Se o spotDL precisar de Deno para certos sites, instale-o separadamente.
- Lidarr, Prowlarr, qBittorrent e Jellyfin são serviços externos opcionais.
- No download direto Spotify, o spotDL normalmente obtém áudio com perdas de outra
  plataforma; salvar esse áudio como FLAC não criaria qualidade lossless. O fluxo
  de busca FLAC usa metadados Spotify e arquivos locais, Jellyfin ou álbuns no Lidarr.
- TIDAL via tiddl permite HIGH/MAX conforme acesso e disponibilidade. Metadados e
  capas seguem as configurações existentes; a configuração atual do TIDAL mantém
  letras desabilitadas. O empacotamento não muda essas opções.
- O executável GUI **não tem um único arquivo solitário**: a versão portátil mantém
  a pasta `_internal` e dois CLIs junto ao executável; isso evita problemas comuns
  com PySide6 e com a execução de subprogramas.
- A pipeline executa testes e valida o carregamento dos binários. Mesmo quando
  ela passa, é recomendado verificar visualmente o app e autenticações em uma
  instalação real no Windows 11 antes de distribuir amplamente.

O checkout contém fontes e scripts. Os executáveis Windows compilados são gerados
em `release/` e disponibilizados nos dois artifacts após o build concluir.
