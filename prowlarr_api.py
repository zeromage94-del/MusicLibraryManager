"""Prowlarr API v1 connector: indexers, app sync, health and configuration status.

This library does not add trackers/indexers or retrieve protected content.
"""
from __future__ import annotations
import json
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


class ProwlarrError(Exception):
    pass


def validate_server_url(base_url: str, service: str) -> str:
    value = base_url.strip().rstrip('/')
    try:
        p = urlsplit(value)
        if (p.scheme not in ('http', 'https') or not p.hostname or
                p.username is not None or p.password is not None or
                p.query or p.fragment or p.path not in ('', '/')):
            raise ValueError()
        _ = p.port  # rejects an invalid port
    except ValueError as exc:
        raise ProwlarrError(f'URL do {service} inválida. Informe apenas http(s)://host:porta.') from exc
    return value


class ProwlarrClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 15):
        self.base_url = validate_server_url(base_url, 'Prowlarr')
        self.api_key = api_key.strip()
        self.timeout = timeout
        if not self.api_key:
            raise ProwlarrError('Informe a API Key do Prowlarr.')

    def request(self, method: str, endpoint: str, data=None):
        if not endpoint.startswith('/') or endpoint.startswith('//') or '?' in endpoint or '..' in endpoint:
            raise ProwlarrError('Endpoint não permitido.')
        payload = json.dumps(data, ensure_ascii=False).encode('utf-8') if data is not None else None
        req = Request(self.base_url + '/api/v1' + endpoint, method=method, data=payload,
                      headers={'X-Api-Key': self.api_key, 'Accept': 'application/json',
                               'Content-Type': 'application/json', 'User-Agent': 'MusicLibraryManager/0.3'})
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise ProwlarrError('API Key rejeitada pelo Prowlarr (HTTP %s).' % exc.code) from exc
            raise ProwlarrError('Erro HTTP %s na API Prowlarr.' % exc.code) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ProwlarrError('Prowlarr inacessível: confira se está aberto, a URL e a porta.') from exc
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProwlarrError('Resposta inesperada da API Prowlarr.') from exc

    def status(self):
        return self.request('GET', '/system/status')

    def indexers(self):
        return self.request('GET', '/indexer')

    def indexer_status(self):
        return self.request('GET', '/indexerstatus')

    def applications(self):
        return self.request('GET', '/applications')

    def download_clients(self):
        return self.request('GET', '/downloadclient')

    def health(self):
        return self.request('GET', '/health')

    def sync_indexers(self):
        # Official Servarr command used by Prowlarr's 'Sync App Indexers' action.
        return self.request('POST', '/command', {'name': 'ApplicationIndexerSync'})

    def application_schemas(self):
        return self.request('GET', '/applications/schema')

    def register_lidarr(self, lidarr_url: str, lidarr_api_key: str, sync_level: str = 'fullSync'):
        """Use server-provided schema, never guess category defaults.

        Does not overwrite or duplicate an existing Lidarr instance.
        """
        target = validate_server_url(lidarr_url, 'Lidarr')
        if not lidarr_api_key.strip():
            raise ProwlarrError('A API Key do Lidarr é necessária para vinculá-lo.')
        if sync_level not in ('fullSync', 'addOnly'):
            raise ProwlarrError('Nível de sincronização inválido.')
        applications = self.applications()
        for app in applications:
            if str(app.get('implementation', '')).lower() != 'lidarr':
                continue
            fields = {f.get('name'): str(f.get('value', '')).rstrip('/') for f in app.get('fields', [])}
            if fields.get('baseUrl', '').casefold() == target.casefold():
                raise ProwlarrError('Este Lidarr já está cadastrado no Prowlarr. Não foi criado duplicado.')
        import copy
        schema = next((row for row in self.application_schemas() if
                       str(row.get('implementation', '')).casefold() == 'lidarr'), None)
        if not schema:
            raise ProwlarrError('Sua versão do Prowlarr não apresentou o schema de conexão Lidarr.')
        config = copy.deepcopy(schema)
        config.pop('id', None)
        config['name'] = 'Lidarr - Music Library Manager'
        config['syncLevel'] = sync_level
        mapping = {'baseUrl': target, 'prowlarrUrl': self.base_url, 'apiKey': lidarr_api_key.strip()}
        for field in config.get('fields', []):
            if field.get('name') in mapping:
                field['value'] = mapping[field['name']]
        field_names = {f.get('name') for f in config.get('fields', [])}
        if not {'baseUrl', 'prowlarrUrl', 'apiKey'}.issubset(field_names):
            raise ProwlarrError('O schema retornado pelo Prowlarr não tem os campos necessários.')
        return self.request('POST', '/applications', config)

    def snapshot(self):
        return {'status': self.status(), 'indexers': self.indexers(),
                'indexer_status': self.indexer_status(), 'applications': self.applications(),
                'download_clients': self.download_clients(), 'health': self.health()}
