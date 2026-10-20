# v0.7 — Instalador Windows e versão portátil

Veja [COMO_GERAR_EXE.md](COMO_GERAR_EXE.md) para usar o GitHub Actions e obter `Setup.exe` e ZIP portátil. O projeto v0.6 e todas as 10 abas estão preservados.

# Music Library Manager v0.6 — Windows 11

Interface em português (Python + PySide6) para **playlists Spotify/TIDAL, detecção de FLACs existentes, Lidarr, Prowlarr, qBittorrent e Jellyfin**.

> **O ZIP inclui código-fonte e scripts `.bat`, não um executável `.exe` pronto.** Os 64 testes foram realizados **offline**, com APIs simuladas e arquivos de teste. Nem a interface gráfica nem downloads reais foram validados num Windows com todos os serviços instalados. Não fornece arquivos musicais, indexadores, torrents, assinaturas, credenciais ou mecanismos para contornar DRM.

## A função central: não baixar músicas repetidas

Na tela inicial, use **Preparar playlist FLAC** e cole um link Spotify `https://open.spotify.com/playlist/...`.

1. **spotDL `save`** recupera *apenas metadados* da playlist (nomes, artistas, álbuns, tempo e possivelmente ISRC); não baixa áudio nesta etapa.
2. O aplicativo lê a pasta que você escolher e procura FLACs já existentes, verificando o cabeçalho/codec e tags com `mutagen`.
3. Faz correspondência conservadora por artista, título exato, álbum, duração e ISRC (quando disponível). Live, remixes ou versões ambíguas **não são aceitos automaticamente**.
4. Se o Jellyfin estiver conectado, consulta também os FLACs indexados e vincula os IDs dos áudios correspondentes. Os arquivos que o Jellyfin possui mas o Windows não consegue ler recebem o estado **FLAC no Jellyfin**, separado de **FLAC confirmado** localmente.
5. Opcionalmente consulta o catálogo de álbuns do Lidarr para faixas não encontradas. Botão **Buscar álbuns faltantes no Lidarr** exige sua confirmação e solicita buscas **por álbum** em fontes autorizadas previamente configuradas, não é um downloader universal por música.
6. Com **Exportar .m3u8**, cria uma playlist apontando para os FLACs locais existentes, sem copiar os arquivos.
7. Com **Criar no Jellyfin**, cria uma playlist no servidor utilizando as músicas já indexadas, **sem duplicar áudios**, mantendo ordem e repetições da playlist original; faixas não encontradas são omitidas e contadas.
8. Com **Exportar para celular**, copia **cada FLAC local uma única vez** para uma pasta `Music/Artista/Álbum`, preserva os metadados e capa incorporada (e salva `cover.jpg` se for JPEG) e gera `Playlist FLAC.m3u8` com caminhos **relativos**. Copie a pasta inteira para seu celular e abra a playlist num reprodutor compatível.

**IMPORTANTE:** um FLAC tecnicamente válido NÃO prova que a gravação nunca foi convertida de MP3. O app audita codec, bits/kHz e tags, mas não pode garantir a proveniência lossless original só por isso.

## Jellyfin: funções da v0.5, mantidas na v0.6

- **Nova aba Jellyfin:** listar biblioteca musical indexada, distinguir as faixas cujo servidor reporta FLAC, atualizar índice, abrir o Jellyfin e criar playlists a partir da análise FLAC.
- **Configurações → Jellyfin:** endereço (padrão `http://localhost:8096`), usuário e senha para login. A senha é usada apenas no login; o token retornado é armazenado pelo `keyring` no cofre de credenciais do sistema, não no arquivo de configurações.
- **Reutilização:** se uma música já existir no Jellyfin, a criação de playlists utiliza seu identificador de mídia; **não aciona downloads**.
- **Condição:** Jellyfin deve estar instalado e ter uma biblioteca de música configurada e escaneada. O programa **não instala Jellyfin automaticamente**.
- **Limite:** músicas no Jellyfin em um servidor remoto cuja pasta não esteja acessível ao PC podem entrar em playlists Jellyfin, mas **não podem ser exportadas para uso offline no celular** pelo botão de cópia. Monte/compartilhe a pasta com permissão de leitura para isso.
- **Uso no celular:** navegue e reproduza remotamente com clientes Jellyfin. Para uso offline, exporte o pacote FLAC (ou use um cliente que ofereça download/sincronização e verifique suas capacidades).
- **Privacidade:** mantenha a API Jellyfin restrita a uma rede confiável e use HTTPS/VPN fora da rede local.

Documentação do projeto: https://github.com/jellyfin/jellyfin — playlists pela API Jellyfin (`POST /Playlists`) e biblioteca (`GET /Users/{id}/Items`).

## Fluxo dos serviços

```text
Link Spotify → spotDL save (metadados da playlist)
                        │
                        ▼
             Localizador FLAC, por faixa
                        │
              ┌─────────┴─────────────┐
              │                       │
     Biblioteca Windows          Jellyfin existente
     (FLAC verificado)         (codec reportado FLAC)
              │                       │
              ▼                       ▼
   .m3u8 ou pacote celular     Playlist no Jellyfin
              │
    Se álbum ainda ausente:
              ▼
Lidarr + Prowlarr + qBittorrent
(buscas/transferências somente após confirmação; requer fontes configuradas)

Link TIDAL → tiddl (quando acesso autorizado e formato disponível) →
     Pasta musical local → verificar novamente → reutilizar na playlist
```

### Limitação atual de playlists TIDAL

A correspondência detalhada automática **por faixa** na aba Busca FLAC usa o arquivo de metadados Spotify via `spotDL save`. Links TIDAL continuam aceitos em **Downloads** via `tiddl`, mas o analisador ainda não importa os metadados de playlist TIDAL para essa correspondência antes do download. Uma pasta TIDAL já existente pode ser escaneada normalmente.

### Qualidade e licenças

- `spotDL` geralmente obtém áudio de plataformas alternativas como YouTube. Não existe garantia de FLAC real, mesmo com Spotify Premium. O botão **Download direto** oferece essa alternativa de qualidade inferior e a informa claramente.
- `tiddl` pode acessar áudio FLAC conforme o serviço, a conta e as limitações técnicas/legais. HIGH e MAX não garantem disponibilidade uniforme.
- **Lidarr** não hospeda música; gerencia coleções e busca álbuns em indexadores configurados. **Prowlarr** gerencia indexadores, e **qBittorrent** é cliente de transferência. Não há trackers ou fontes embutidos no projeto.
- Respeite direitos autorais, termos de serviço e acesse somente conteúdos que esteja autorizado a baixar.

## Instalação

1. Windows 11, Python 3.11+ e FFmpeg disponível no PATH. Para FFmpeg: `winget install --id Gyan.FFmpeg -e`.
2. Extraia o ZIP e execute `instalar.bat`. Cria `.venv` e instala `PySide6`, `keyring`, `mutagen`, `tiddl`, `spotdl`.
3. Execute `iniciar.bat`. Se houver falha, `INICIAR_COM_LOG.bat` gera `inicializacao.log`.
4. Instale separadamente, conforme suas necessidades, [Lidarr](https://github.com/Lidarr/Lidarr), [Prowlarr](https://github.com/Prowlarr/Prowlarr), [qBittorrent](https://github.com/qbittorrent/qBittorrent) e [Jellyfin](https://github.com/jellyfin/jellyfin).
5. Abra **Configurações** no programa. Informe URL/API Key de Lidarr/Prowlarr, URL/senha WebUI qBittorrent e usuário/senha Jellyfin. Verifique cada conexão. No Jellyfin, selecione uma biblioteca de música no painel do servidor apontando para a pasta que guarda os FLACs.
6. Na tela inicial, cole a playlist Spotify e clique **Preparar playlist FLAC** para detectar as músicas que já possui antes de pedir qualquer busca de álbum.

Para gerar um EXE **no seu Windows**, execute `gerar_exe.bat`. É necessário ter Python e dependências instalados antes. O EXE **não inclui** os quatro servidores de terceiros nem os arquivos de música.

## Testes offline

Execute `TESTAR.bat` ou:

```cmd
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Foram realizados **64 testes automatizados** para lógica das APIs, correspondências, TIDAL/Spotify URLs, qBittorrent, Lidarr, Prowlarr, Jellyfin e exportação FLAC portátil. Testes **não comprovam** que o botão de login, reprodução/download real e interfaces gráficas funcionem com as instalações reais; faça verificação antes de confiar em processos extensos.

## Limitações conhecidas

- A criação de playlists Jellyfin depende das permissões da conta, da API instalada e dos arquivos já indexados.
- Nenhum conteúdo externo é garantido ou procurado fora das fontes autorizadas configuradas.
- A análise remota do Jellyfin depende das propriedades de codec reportadas pelo servidor. Ela não comprova o bitstream nem a qualidade de origem quando o arquivo físico é inacessível.
- O modo offline do celular exige arquivos locais; playlist Jellyfin pela rede não é automaticamente offline.
- O `export_mobile_package` copia FLACs únicos que já estão no computador e nunca faz download de serviços de streaming.

---

## Novidade v0.6 — sincronização automática inspirada no Octave

**10 abas.** A nova aba **Sincronização** cadastra links públicos ou acessíveis do Spotify e mantém listas FLAC locais. O projeto [Octave](https://github.com/johagan94/octave) serviu de referência para o conceito de correspondência, espera por arquivos ausentes, repetição de tarefas e sincronização. **Não incluímos o Octave nem copiamos seu código; implementamos uma integração independente em Python para o Windows.**

### Passo a passo

1. Configure a pasta **Pasta FLAC** na aba Busca FLAC, o local onde estão seus arquivos originais (ou um volume acessível na rede).
2. Configure, opcionalmente, Lidarr e Jellyfin em Configurações. Prowlarr e qBittorrent continuam gerenciáveis nas abas próprias.
3. Na aba **Sincronização**, cole `https://open.spotify.com/playlist/...` e clique **Adicionar**.
4. Clique **Sincronizar selecionada**. O programa executará **spotDL save somente para metadados**, escaneará FLACs locais e cruzará os metadados com o Jellyfin e álbuns do Lidarr, quando conectados.
5. A playlist `.m3u8` é gerada em sua pasta de saída, dentro de **Playlists FLAC**. Sem duplicar áudio e sem inventar faixas: só músicas FLAC acessíveis que passaram pela verificação de codec e tags.
6. Clique **Solicitar álbuns no Lidarr** para confirmar buscas faltantes. Elas seguem a configuração de fontes autorizadas do seu Lidarr. A busca é **por álbum**, e pode não encontrar um FLAC.
7. Quando novos arquivos chegarem, clique **Sincronizar selecionada** novamente ou escolha **30 minutos / 1 hora / 3 horas / 12 horas** para sincronizar periodicamente todas as playlists cadastradas. Não serão solicitados novos downloads sem seu consentimento.
8. **Publicar Jellyfin** cria a playlist Jellyfin e conecta esse ID à playlist cadastrada; futuras sincronizações passam a atualizá-la automaticamente quando o Jellyfin estiver disponível.
9. **Exportar ausentes (.csv)** gera uma lista apenas com faixas pendentes. **Exportar para celular** copia FLACs já existentes (uma vez cada), capas incorporadas e `.m3u8` com caminhos relativos.

**Modos:**

- **Adicionar novas:** mantém entradas antigas da lista e adiciona novas sem repetir posições já incluídas; não altera o áudio original.
- **Espelhar:** mantém a mesma seleção e ordem da playlist Spotify original; remove da `.m3u8` referências a músicas que saíram do Spotify, sem apagar arquivos.
- **Reconstruir:** gera uma lista nova a partir do estado atual. No Jellyfin, Espelhar/Reconstruir alteram *referências de itens da playlist*, sem apagar arquivos do servidor.

A base persistente **`playlist_sync.sqlite3`** fica no diretório de dados do aplicativo no Windows (`%APPDATA%/MusicLibraryManager`). Ela guarda playlists, posições, metadados, solicitações feitas ao Lidarr, histórico e IDs das playlists criadas no Jellyfin. Não armazena senhas das plataformas; tokens de servidor usam `keyring`/cofre de credenciais.

### Proteções

- Nunca baixa MP3 para converter e rotular artificialmente como lossless; a sincronização usa apenas FLACs já disponíveis em fontes autorizadas e pastas acessíveis.
- O cache só reutiliza um arquivo FLAC depois de validar novamente bitstream e tags no disco, inclusive em reinicializações; um arquivo movido, substituído ou ausente não é considerado disponível.
- Músicas com título de remix, gravação ao vivo ou álbum diferente não são associadas indiscriminadamente. ISRC compatível quando existe pode ajudar a identificar a gravação.
- Para faixas anunciadas como FLAC pelo Jellyfin sem arquivo fisicamente acessível, o programa poderá usá-las na playlist Jellyfin, **mas não na `.m3u8` para celular**.
- O banco mantém registros de álbuns já solicitados para não enviar buscas repetidas ao Lidarr; quando um FLAC válido aparece, ele passa a ser reutilizado.
- Em falhas de leitura da playlist Spotify, a última lista salva é preservada e a falha é registrada no histórico.
- **FLAC confirmado** significa contêiner/codec FLAC válido com tags coerentes; não comprova sozinho que a origem nunca passou por compressão com perdas. Autenticidade Hi-Res requer proveniência adicional.

### Limites atuais da v0.6

- A sincronização programada e a consulta da API Jellyfin não foram testadas com servidores reais neste ambiente. A operação inicial exige spotDL funcional e que o link Spotify seja acessível ao spotDL.
- Links **TIDAL** continuam na aba Downloads/tiddl; a nova sincronização recorrente foi desenvolvida para playlists **Spotify**.
- Metadados retornados pelo spotDL variam conforme a versão e autenticação do Spotify, e a API do Jellyfin pode apresentar diferenças entre versões.
- O programa não faz download direto de músicas pelo Octave ou Jellyfin, e Lidarr não possui um catálogo próprio de músicas para download.
- Como ocorre em qualquer atualização de playlist por API remota, interrupções de rede durante modificações no Jellyfin podem deixar duplicações ou mudanças parciais, exigindo uma nova sincronização.
- **Nenhum arquivo musical é fornecido com o projeto.**

**Testes:** 64 verificações offline aprovadas em 20/10/2026 (49 anteriores + 15 cenários novos de sincronização e Jellyfin). A interface PySide6 e o funcionamento de downloads reais precisam ser testados no Windows 11. Para repetir: `TESTAR.bat`.
