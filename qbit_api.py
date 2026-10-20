"""qBittorrent WebUI API v2 client (supports v4 and v5).

Cookie-session authentication is performed using a local cookie jar; credentials
are not embedded in URLs. Operations are explicit, and never delete files.
"""
from __future__ import annotations
import http.cookiejar
import re
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener
from urllib.error import HTTPError, URLError

from prowlarr_api import validate_server_url, ProwlarrError

HASH_RE = re.compile(r'^[0-9a-fA-F]{40}(?:\|[0-9a-fA-F]{40})*$')


class QBittorrentError(Exception):
    pass


class QBittorrentClient:
    def __init__(self, base_url: str, username: str, password: str, timeout: int = 15):
        try:
            self.base_url = validate_server_url(base_url, 'qBittorrent')
        except ProwlarrError as exc:
            raise QBittorrentError(str(exc)) from exc
        self.username = username.strip()
        self.password = password
        self.timeout = timeout
        if not self.username or not self.password:
            raise QBittorrentError('Preencha usuário e senha da WebUI do qBittorrent.')
        self.opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._logged_in = False
        self._version = None

    def _request(self, endpoint: str, data: dict | None = None, *, multipart=None, retry=True):
        if not endpoint.startswith('/') or endpoint.startswith('//'):
            raise QBittorrentError('Caminho inválido na API do qBittorrent.')
        url = self.base_url + '/api/v2' + endpoint
        headers = {'Referer': self.base_url + '/', 'Origin': self.base_url,
                   'User-Agent': 'MusicLibraryManager/0.3'}
        payload = None
        if data is not None:
            payload = urlencode(data).encode('utf-8')
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        if multipart is not None:
            # Fields + optional .torrent file, supplied as bytes. Never alter contents.
            import secrets
            boundary = '----MLM' + secrets.token_hex(14)
            parts = []
            for key, value in multipart.get('fields', {}).items():
                parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
                              str(value).encode(), b'\r\n'])
            if multipart.get('file') is not None:
                name, blob = multipart['file']
                safe_name = str(name).replace('"', '').replace('\r', '').replace('\n', '')
                parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="torrents"; filename="{safe_name}"\r\nContent-Type: application/x-bittorrent\r\n\r\n'.encode(), blob, b'\r\n'])
            parts.append(f'--{boundary}--\r\n'.encode())
            payload = b''.join(parts)
            headers['Content-Type'] = 'multipart/form-data; boundary=' + boundary
        req = Request(url, data=payload, headers=headers, method='POST' if payload is not None else 'GET')
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                return resp.read().decode('utf-8', 'replace')
        except HTTPError as exc:
            if exc.code in (401, 403) and retry and self._logged_in:
                self._logged_in = False
                self.login()
                return self._request(endpoint, data, multipart=multipart, retry=False)
            if exc.code in (401, 403):
                raise QBittorrentError('A WebUI rejeitou a autenticação ou a origem da solicitação (HTTP %s).' % exc.code) from exc
            raise QBittorrentError('Erro HTTP %s no qBittorrent (%s).' % (exc.code, endpoint)) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise QBittorrentError('Não foi possível acessar o qBittorrent. Ative a WebUI e confira a porta.') from exc

    def login(self):
        # No authentication retry during login, and no logging of credentials.
        response = self._request('/auth/login', {'username': self.username, 'password': self.password}, retry=False)
        if response.strip() != 'Ok.':
            raise QBittorrentError('Login rejeitado. Confira usuário, senha e limitações de IP na WebUI.')
        self._logged_in = True
        return True

    def _auth(self):
        if not self._logged_in:
            self.login()

    def version(self):
        self._auth()
        return self._request('/app/version').strip()

    def torrents(self, category=None):
        import json
        self._auth()
        raw = self._request('/torrents/info' + ('?'+urlencode({'category': category}) if category else ''))
        try:
            results = json.loads(raw)
            if not isinstance(results, list):
                raise ValueError()
            return results
        except ValueError as exc:
            raise QBittorrentError('Resposta inválida na lista de torrents.') from exc

    def categories(self):
        import json
        self._auth()
        try:
            result = json.loads(self._request('/torrents/categories'))
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except ValueError as exc:
            raise QBittorrentError('Resposta inválida de categorias.') from exc

    def create_category(self, name: str, save_path: str = ''):
        name = name.strip()
        if not name or len(name) > 64 or any(c in name for c in '/\\\n\r'):
            raise QBittorrentError('Nome de categoria inválido.')
        self._auth()
        self._request('/torrents/createCategory', {'category': name, 'savePath': save_path})
        return True

    def add_torrent(self, magnet_or_url: str = '', category: str = '', torrent_file: Path | None = None):
        if torrent_file is None:
            link = magnet_or_url.strip()
            p = urlsplit(link)
            if not (link.startswith('magnet:?xt=urn:btih:') or p.scheme in ('https', 'http') and p.hostname):
                raise QBittorrentError('Informe um link magnet ou um endereço HTTP(S) para arquivo .torrent.')
            info = {'fields': {'urls': link}}
        else:
            file_path = Path(torrent_file)
            if file_path.suffix.lower() != '.torrent' or not file_path.is_file():
                raise QBittorrentError('Selecione um arquivo .torrent existente.')
            if file_path.stat().st_size > 10 * 1024 * 1024:
                raise QBittorrentError('Arquivo .torrent maior que o limite de 10 MB.')
            info = {'fields': {}, 'file': (file_path.name, file_path.read_bytes())}
        if category:
            info['fields']['category'] = category
        self._auth()
        response = self._request('/torrents/add', multipart=info).strip()
        if response != 'Ok.':
            raise QBittorrentError('qBittorrent não confirmou a inclusão: ' + response[:80])
        return True

    @staticmethod
    def _hash(value: str):
        if not HASH_RE.fullmatch(value):
            raise QBittorrentError('Selecione um torrent válido para esta ação.')
        return value

    def control(self, action: str, torrent_hash: str):
        if action not in ('pause', 'resume', 'recheck'):
            raise QBittorrentError('Ação não permitida.')
        value = self._hash(torrent_hash)
        self._auth()
        if action == 'recheck':
            self._request('/torrents/recheck', {'hashes': value})
            return True
        ver = self.version()
        major = int((re.search(r'\d+', ver) or ['5'])[0])
        endpoint = {'pause': 'stop' if major >= 5 else 'pause',
                    'resume': 'start' if major >= 5 else 'resume'}[action]
        self._request('/torrents/' + endpoint, {'hashes': value})
        return True

    def set_category(self, torrent_hash: str, category: str):
        self._auth()
        self._request('/torrents/setCategory', {'hashes': self._hash(torrent_hash), 'category': category})
        return True

    def snapshot(self):
        return {'version': self.version(), 'torrents': self.torrents(), 'categories': self.categories()}
