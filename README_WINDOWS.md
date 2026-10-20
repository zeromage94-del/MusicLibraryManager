MUSIC LIBRARY MANAGER 0.7 - WINDOWS 11

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

SEM PYTHON INSTALADO: OK. Esta versao empacota o runtime dentro dos executaveis.
NAO INCLUI: musica, indexadores, credenciais, FFmpeg e servidores externos.

ORIGEM: Codigo Python e scripts de build em pacote fonte v0.7.
Status: procedimentos de build e testes offline; integracao completa em Windows
e downloads reais so poderao ser confirmados apos executar a pipeline e instalar.
