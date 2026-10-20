MUSIC LIBRARY MANAGER 0.7 - WINDOWS 11

OBTER OS ARQUIVOS
1. No repositorio zeromage94-del/MusicLibraryManager, abra Actions > Gerar instalador Windows.
2. Execute Run workflow na branch desejada e aguarde a conclusao com sucesso.
3. Baixe os artifacts MusicLibraryManager-Setup-Windows-v0.7 e/ou
   MusicLibraryManager-Portatil-Windows-v0.7. Extraia o arquivo desejado do artifact.
4. O build usa diretamente os fontes versionados, sem extrair o ZIP historico do projeto.

INSTALACAO
1. Se voce tem o arquivo MusicLibraryManager_Setup_v0.7_Windows_x64.exe, clique duas vezes.
2. O instalador cria atalho no menu Iniciar e oferece atalho na area de trabalho.
3. O programa e instalado no seu perfil Windows, sem exigir permissao de administrador.
4. Para remover, use Configuracoes > Aplicativos > Music Library Manager.
5. Configuracoes e banco SQLite ficam em %APPDATA%\MusicLibraryManager e nao
   sao apagados automaticamente na desinstalacao.

VERSAO PORTATIL
1. Extraia todo o ZIP MusicLibraryManager_Portatil_v0.7_Windows_x64.zip.
2. Abra a pasta MusicLibraryManager e clique MusicLibraryManager.exe.
3. Nao mova somente o EXE: mantenha a pasta _internal e os EXEs auxiliares juntos.
4. "Portatil" dispensa instalacao, mas as configuracoes persistem no perfil do Windows.

INTEGRACOES
- spotdl.exe e tiddl.exe seguem junto com o aplicativo, sem precisar instalar Python.
- FFmpeg continua necessario para processamento de audio e deve estar no PATH:
  winget install --id Gyan.FFmpeg -e
  Depois feche e reabra o programa.
- Alguns downloads do spotDL requerem Deno para yt-dlp; consulte a documentacao do spotDL.
- Lidarr, Prowlarr, qBittorrent, Jellyfin sao aplicativos servidores separados.
  Instale-os e configure endereco e credenciais na aba Configuracoes.
- TIDAL depende de acesso e autorizacao apropriados, disponibilidade do formato e
  restricoes de cada plataforma. FLAC tecnicamente valido nao comprova origem lossless.
- Spotify direto usa spotDL e normalmente gera audio com perdas de outra plataforma.
  A busca FLAC usa metadados Spotify e arquivos locais, Jellyfin ou albuns do Lidarr.
- Metadados e capas seguem as configuracoes existentes; no TIDAL, letras permanecem
  desabilitadas na configuracao atual. O empacotamento nao altera essas opcoes.

SEM PYTHON INSTALADO: OK. Esta versao empacota o runtime dentro dos executaveis.
NAO INCLUI: musica, indexadores, credenciais, FFmpeg e servidores externos.

COMPILAR OS FONTES
- Use Python 3.13 x64 e Inno Setup 6; execute gerar_exe.bat na raiz do repositorio.
- requirements-build.txt centraliza as dependencias do build e dos testes.
- scripts/build_windows.ps1 gera os arquivos em release/. Com -SkipInstaller,
  gera apenas executaveis e ZIP portatil, sem exigir Inno Setup.
- O build inclui os dicionarios pykakasi do spotDL e verifica os executaveis
  empacotados antes de gerar o instalador e o ZIP. Veja COMO_GERAR_EXE.md.

ORIGEM: Codigo Python e scripts versionados no repositorio MusicLibraryManager.
O build testa as APIs offline e o carregamento dos executaveis. Autenticacoes,
interface grafica e downloads reais exigem verificacao com os servicos no Windows 11.
