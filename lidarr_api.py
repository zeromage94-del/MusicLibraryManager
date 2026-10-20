"""Small, dependency-free Lidarr v1 API integration.

Lidarr is a *manager*, not a source of audio. This API client never adds
indexers, supplies trackers, bypasses authentication, or downloads content.
"""
from __future__ import annotations

import json
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


class LidarrError(Exception):
    pass


class LidarrClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 12):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key.strip()
        self.timeout = timeout
        parts = urlsplit(self.base_url)
        if (parts.scheme not in ('http', 'https') or not parts.hostname
                or parts.username or parts.password or parts.query or parts.fragment):
            raise LidarrError('Informe a URL HTTP(S) do Lidarr, sem usuário, senha ou parâmetros.')
        if not self.api_key:
            raise LidarrError('Informe a API Key do Lidarr.')

    def request(self, method: str, endpoint: str, data=None, params=None):
        if not endpoint.startswith('/') or endpoint.startswith('//'):
            raise LidarrError('Endpoint inválido.')
        url = self.base_url + '/api/v1' + endpoint
        if params:
            url += '?' + urlencode(params)
        payload = json.dumps(data, ensure_ascii=False).encode('utf-8') if data is not None else None
        req = Request(url, data=payload, method=method,
                      headers={'X-Api-Key': self.api_key, 'Accept': 'application/json',
                               'Content-Type': 'application/json', 'User-Agent': 'MusicLibraryManager/0.2'})
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
                return json.loads(body) if body else {}
        except HTTPError as exc:
            if exc.code == 401:
                raise LidarrError('API Key rejeitada (HTTP 401). Verifique Settings > General no Lidarr.') from exc
            if exc.code == 404:
                raise LidarrError('Endpoint indisponível (HTTP 404). Confirme a URL e a versão do Lidarr.') from exc
            raise LidarrError(f'Erro HTTP {exc.code} na comunicação com o Lidarr.') from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise LidarrError('Não foi possível conectar ao Lidarr. Verifique se está em execução e acessível.') from exc
        except ValueError as exc:
            raise LidarrError('Resposta inválida do servidor Lidarr.') from exc

    def health(self):
        return self.request('GET', '/system/status')

    def artists(self):
        return self.request('GET', '/artist')

    def albums(self):
        return self.request('GET', '/album')

    def queue(self):
        data = self.request('GET', '/queue', params={'page': 1, 'pageSize': 100, 'includeArtist': 'true', 'includeAlbum': 'true'})
        return data.get('records', []) if isinstance(data, dict) else data

    def quality_profiles(self):
        return self.request('GET', '/qualityprofile')

    def search_album(self, album_id: int):
        if not isinstance(album_id, int) or album_id <= 0:
            raise LidarrError('Escolha um álbum válido.')
        return self.request('POST', '/command', {'name': 'AlbumSearch', 'albumIds': [album_id]})

    def refresh_library(self):
        return self.request('POST', '/command', {'name': 'RefreshArtist'})

    def import_lists(self):
        return self.request('GET', '/importlist')

    def sync_import_list(self, list_id: int):
        if not isinstance(list_id, int) or list_id <= 0:
            raise LidarrError('Selecione uma lista válida.')
        return self.request('POST', '/command', {'name': 'ImportListSync', 'definitionId': list_id})

    def download_clients(self):
        return self.request('GET', '/downloadclient')

    def download_client_schemas(self):
        return self.request('GET', '/downloadclient/schema')

    def register_qbittorrent(self, qbit_url: str, username: str, password: str, category: str = 'lidarr'):
        """Configure an existing qBittorrent WebUI as a Lidarr download client.

        Uses a version-specific schema returned by Lidarr. Does not modify an
        existing entry. Lidarr will handle future downloads and imports.
        """
        from urllib.parse import urlsplit
        from copy import deepcopy
        url = urlsplit(qbit_url)
        if not url.hostname or url.scheme not in ('http', 'https') or url.query or url.fragment:
            raise LidarrError('URL inválida para o cliente qBittorrent.')
        if not username.strip() or not password:
            raise LidarrError('Usuário e senha da WebUI são obrigatórios.')
        host, port = url.hostname, url.port or (443 if url.scheme == 'https' else 80)
        for client in self.download_clients():
            if str(client.get('implementation', '')).lower() != 'qbittorrent':
                continue
            fields = {f.get('name'): f.get('value') for f in client.get('fields', [])}
            if str(fields.get('host', '')).casefold() == host.casefold() and str(fields.get('port', '')) == str(port):
                raise LidarrError('qBittorrent já configurado no Lidarr para este endereço e porta.')
        schema = next((obj for obj in self.download_client_schemas() if
                       str(obj.get('implementation', '')).casefold() == 'qbittorrent'), None)
        if schema is None:
            raise LidarrError('Schema qBittorrent não está disponível na sua versão do Lidarr.')
        config = deepcopy(schema)
        config.pop('id', None)
        config['name'] = 'qBittorrent - Music Library Manager'
        config['enable'] = True
        mapping = {'host': host, 'port': port, 'useSsl': url.scheme == 'https',
                   'username': username.strip(), 'password': password, 'category': category}
        found = set()
        for field in config.get('fields', []):
            if field.get('name') in mapping:
                field['value'] = mapping[field['name']]
                found.add(field['name'])
        if not {'host', 'port', 'username', 'password', 'category'}.issubset(found):
            raise LidarrError('Schema incompleto para configurar o qBittorrent.')
        return self.request('POST', '/downloadclient', config)

    def snapshot(self):
        # Individually returned data sets are deliberately not synthesized
        # into a fictitious "available to download" count.
        return {
            'status': self.health(),
            'artists': self.artists(),
            'albums': self.albums(),
            'queue': self.queue(),
            'profiles': self.quality_profiles(),
            'lists': self.import_lists(),
        }

    def lookup_albums(self, term: str):
        """Search Lidarr/MusicBrainz album catalogue; returns *metadata*, not files."""
        if not term.strip() or len(term) > 250:
            raise LidarrError('Busca de álbum inválida.')
        return self.request('GET', '/album/lookup', params={'term': term.strip()})

    def register_album(self, candidate: dict, artist_id: int):
        """Register a precise album selection for an already-tracked artist.

        Never inserts arbitrary releases or triggers download without a caller's
        explicit confirmation. Lidarr is responsible for release selection.
        """
        from copy import deepcopy
        if not isinstance(artist_id, int) or artist_id <= 0:
            raise LidarrError('O artista deve estar cadastrado no Lidarr.')
        if not (isinstance(candidate, dict) and candidate.get('foreignAlbumId') and candidate.get('title')):
            raise LidarrError('Álbum sem identificação MusicBrainz verificável.')
        payload = deepcopy(candidate)
        payload.pop('id', None)
        payload.pop('statistics', None)
        payload['artistId'] = artist_id
        payload['monitored'] = True
        result = self.request('POST', '/album', payload)
        if not isinstance(result, dict) or int(result.get('id') or 0) <= 0:
            raise LidarrError('Lidarr não confirmou a inclusão do álbum.')
        return result
