# Auditoria das integrações e do fluxo FLAC

Data: 20/10/2026. Escopo: código e configuração do MusicLibraryManager v0.7
presentes neste repositório. Esta revisão identifica o que o aplicativo executa;
não atribui ao aplicativo todos os recursos disponíveis nos projetos de referência.

## Resultado

O aplicativo já integra downloads TIDAL por tiddl, metadados Spotify por spotDL,
busca de FLACs locais e APIs de Lidarr, Prowlarr, qBittorrent e Jellyfin.
**O fluxo completo “playlist Spotify ou TIDAL → FLAC correspondente → metadados,
capa e letras” ainda está incompleto.** Faltam a correspondência Spotify → catálogo
TIDAL e uma etapa de letras habilitada e validada. A correção do empacotamento
Windows resolve o carregamento dos executáveis; não implementa essas funções.

## Os oito projetos citados

| Projeto | Papel efetivo no aplicativo | Evidência local e limites |
| --- | --- | --- |
| [TIDAL Media Downloader](https://github.com/yaronzz/tidal-media-downloader) | Referência externa, sem integração implementada | Não consta em `requirements.txt`, nos imports ou nos comandos de build. `MainWindow._start_next()` em `app.py` chama **tiddl**, não um CLI do TIDAL Media Downloader. Não há código desse projeto incorporado ao pacote atual. |
| [spotDL](https://github.com/spotDL/spotify-downloader) | Pacote Python e CLI empacotado | `requirements.txt` declara `spotdl>=4,<5`; `tools/spotdl_entry.py` chama `spotdl.console_entry_point`; `scripts/build_windows.ps1` gera `spotdl.exe`. `flac_matcher.export_spotify_playlist()` usa `spotdl save` para metadados. O download direto em `MainWindow._start_next()` solicita M4A, sem garantia lossless. |
| [tiddl](https://github.com/oskvr37/tiddl) | Pacote Python e CLI empacotado | `requirements.txt` declara `tiddl>=3.4,<4`; `tools/tiddl_entry.py` chama `tiddl.cli.app.app`; o build gera `tiddl.exe`. `MainWindow.tidal_login()` abre `auth login`; `_start_next()` usa `download url` em HIGH/MAX. A dependência vem do pacote `tiddl`, sem fixar esse repositório Git ou uma revisão específica. |
| [Lidarr](https://github.com/Lidarr/Lidarr) | Integração com servidor externo por API | `lidarr_api.LidarrClient` consulta artistas, álbuns, fila e listas; `search_album()`, `lookup_albums()` e `register_album()` dão suporte à busca de álbuns. `register_qbittorrent()` cadastra o cliente externo. O servidor e seu catálogo não são incluídos no instalador. A busca é por álbum e não garante FLAC. |
| [Prowlarr](https://github.com/Prowlarr/Prowlarr) | Integração com servidor externo por API | `prowlarr_api.ProwlarrClient` consulta indexadores, aplicações e saúde; `sync_indexers()` e `register_lidarr()` conectam a configuração dos serviços. Não há mecanismo próprio de busca/download de áudio pelo Prowlarr nem indexadores fornecidos pelo pacote. |
| [qBittorrent](https://github.com/qbittorrent/qBittorrent) | Integração com Web API de cliente externo | `qbit_api.QBittorrentClient` implementa login, listagem, categorias, `add_torrent()` e `control()`. `MainWindow.connect_qbit_lidarr()` cadastra esse cliente no Lidarr. O aplicativo não inclui o cliente qBittorrent nem suas fontes; o fluxo de importação depende dos serviços configurados. |
| [Jellyfin](https://github.com/jellyfin/jellyfin) | Integração com servidor de biblioteca e playlists | `jellyfin_api.JellyfinClient` implementa autenticação, `music()`, `refresh_library()`, `create_playlist()` e `sync_playlist_items()`. `link_jellyfin_songs()` cruza músicas já indexadas. Não baixa um catálogo novo: um item FLAC remoto não vira automaticamente um arquivo local exportável. |
| [Octave](https://github.com/johagan94/octave) | Referência conceitual de sincronização | `playlist_sync.py` implementa localmente `SyncStore`, `sync_playlist()`, `cache_reuse()` e `request_new_albums()`. Não existe dependência, execução de CLI ou código Octave vendorizado na árvore auditada. Modos de sincronização, cache e histórico são implementação do MusicLibraryManager. |

## Fluxos existentes e funções ausentes

**Playlist TIDAL → download:** `playlist_api.identify_media_url()` aceita links TIDAL.
`MainWindow.add_jobs()` os coloca na fila; `_start_next()` os envia ao tiddl.
`write_tiddl_config()` em `app.py` configura organização de pastas, metadados,
capa incorporada e arquivo de capa conforme a opção do usuário, além de playlists
M3U. A qualidade HIGH/MAX depende do formato disponibilizado pelo serviço.

**Playlist Spotify → FLACs existentes:** `flac_matcher.export_spotify_playlist()`
extrai metadados com `spotdl save`. `scan_flacs()`, `flac_information()` e
`apply_local_matches()` verificam arquivos locais; `link_jellyfin_songs()` verifica
itens da biblioteca remota. `lidarr_album_matches()`,
`find_remote_album_candidates()` e `request_missing_albums()` atendem álbuns
ausentes por meio do Lidarr. A solicitação de busca depende de confirmação na
interface. `playlist_sync.sync_playlist()` preserva o estado e gera as referências
de playlist; não transforma metadados Spotify em download TIDAL.

**Download direto Spotify:** `MainWindow._start_next()` usa spotDL com
`--format m4a`. Esse caminho é mantido como alternativa informada ao usuário;
codificar sua saída em FLAC não recuperaria qualidade perdida.

**Correspondência Spotify → TIDAL: ausente.** Os critérios de
`flac_matcher.match_score()` comparam título, artista, álbum, duração e ISRC com
candidatos locais/Jellyfin. Não consultam o catálogo TIDAL, não resolvem IDs TIDAL
e não encaminham faixas Spotify correspondentes para o tiddl. O campo de URL
aceitar ambas as plataformas não implementa essa ponte.

**Importação TIDAL para sincronização por faixa: ausente.**
`playlist_sync.playlist_key()` aceita somente playlists Spotify;
`MainWindow.analyze_flac_playlist()` também orienta links TIDAL para Downloads.
Uma pasta já baixada pelo tiddl pode ser escaneada como biblioteca local.

**Letras: ausentes no fluxo unificado.** `write_tiddl_config()` fixa
`[metadata] lyrics = false`. Não há opção de letras na interface, enriquecimento
comum após download, exportação própria de `.lrc` ou teste de letras incorporadas.
Recursos que os CLIs externos possam oferecer não constituem uma integração
habilitada e verificada pelo MusicLibraryManager.

**Organização de arquivos: parcial, implementada por caminho.** O tiddl recebe
templates de pastas, tags e capas; o spotDL recebe seu template de saída.
`flac_matcher.export_mobile_package()` copia FLACs locais preservando tags e capas,
extrai capa JPEG disponível e gera M3U8 relativo. Não há uma etapa comum que
complete ou corrija tags, capas e letras em arquivos de todas as fontes.

**Auditoria de qualidade: implementada com limite conhecido.**
`flac_information()` lê o cabeçalho e os dados do stream com Mutagen;
`flac_audit()` em `app.py` apresenta codec, bits, frequência e capa.
Esses dados verificam o arquivo FLAC, mas não comprovam que sua origem nunca
passou por compressão com perdas. `playlist_api.can_submit_url()` preserva a
restrição de fontes no modo “somente FLAC”.

## Build Windows e validação

Os fontes agora são compilados diretamente da branch selecionada:

- `scripts/build_windows.ps1` executa a suíte em `tests/`, gera a GUI e os dois
  CLIs, coleta licenças, verifica os executáveis e prepara os pacotes.
- O spotDL recebe `--collect-data pykakasi`, incluindo
  `pykakasi/data/kanwadict4.db`. `tools/spotdl_entry.py --build-check` exerce os
  dicionários por transliteração; a verificação do executável congelado deve
  passar antes da distribuição.
- `packaging/MusicLibraryManager.iss` define instalação por usuário. Não há regra
  para apagar dados do usuário na desinstalação.
- `.github/workflows/build-windows.yml` prepara Python 3.13 x64 no runner
  `windows-2022`, usa `requirements-build.txt` e chama o script versionado.
  Os produtos esperados são `MusicLibraryManager_Setup_v0.7_Windows_x64.exe`
  e `MusicLibraryManager_Portatil_v0.7_Windows_x64.zip`, para Windows 11 x64.

Nesta sessão, **53 dos 68 testes passaram**, incluindo geração de FLAC real,
leitura de tags, exportação móvel, sincronização e empacotamento. Foram usados
Mutagen 1.47.0 e SoundFile 0.13.1 obtidos dos repositórios oficiais, com
libsndfile disponível no executor. Os 15 testes HTTP restantes não puderam
iniciar porque o sandbox bloqueia os servidores locais simulados de Lidarr,
Prowlarr e qBittorrent. A compilação dos fontes Python e a verificação do diff
também passaram.

Não foram gerados nem executados binários Windows nesta sessão. A tentativa de
publicar a branch encontrou HTTP 403 no acesso de escrita ao GitHub, impedindo
a abertura do PR e a validação desta revisão pelo Actions. O build está preparado
no código; a geração e os testes dos artefatos ainda precisam concluir no Windows.

Os testes existentes usam APIs simuladas e arquivos de teste. Mesmo após o
workflow passar, login TIDAL/Spotify, downloads, capas, interface e conexões com
servidores reais exigem validação integrada. FFmpeg e, quando exigido pelo spotDL,
Deno continuam externos. Esta auditoria não altera credenciais ou autenticações.

## Próximas entregas para alcançar o fluxo desejado

1. Implementar consulta e correspondência Spotify → catálogo TIDAL, com resolução
   de ID, tratamento de versões/edições, ambiguidades e indisponibilidade.
2. Integrar a importação de playlists TIDAL ao modelo persistente de sincronização.
3. Habilitar letras como opção explícita e verificar a gravação de tags/arquivos
   suportados, sem sobrescrever dados existentes do usuário sem necessidade.
4. Definir uma etapa comum de verificação de tags, capa e letras para cada arquivo,
   mantendo a origem e a qualidade reais identificáveis.
5. Executar o workflow desta revisão e validar Setup, ZIP, autenticações e os
   fluxos de download em uma instalação Windows 11 com os serviços configurados.

Esses itens descrevem trabalho pendente; não são recursos entregues pela correção
de empacotamento. Para compilar os fontes, consulte
[COMO_GERAR_EXE.md](../COMO_GERAR_EXE.md).
