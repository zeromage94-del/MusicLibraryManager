"""Optional Jellyfin integration: library inspection and ordered playlists.

Jellyfin is NOT an audio download source. Queries return items indexed from
media the user already has. Authentication uses a normal Jellyfin account;
passwords are not persisted by this module. All calls are time bounded.
"""
from __future__ import annotations

import json
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


class JellyfinError(Exception):
    pass


def valid_base_url(raw: str) -> str:
    raw = raw.strip().rstrip('/')
    u = urlsplit(raw)
    if (u.scheme not in ('http', 'https') or not u.hostname or u.username or
            u.password or u.query or u.fragment or '..' in u.path.split('/')):
        raise JellyfinError('URL Jellyfin inválida. Informe http(s)://servidor:8096 sem parâmetros ou credenciais.')
    return raw


class JellyfinClient:
    def __init__(self, base_url: str, token: str = '', user_id: str = '', timeout: int = 12):
        self.base_url = valid_base_url(base_url)
        self.token = token.strip()
        self.user_id = user_id.strip()
        self.timeout = timeout

    def request(self, method: str, endpoint: str, data=None, params=None, authenticated=True):
        if not endpoint.startswith('/') or endpoint.startswith('//'):
            raise JellyfinError('Endpoint Jellyfin inválido.')
        if authenticated and not self.token:
            raise JellyfinError('Conecte uma conta Jellyfin nas Configurações.')
        url = self.base_url + endpoint
        if params:
            url += '?' + urlencode(params)
        body = json.dumps(data, ensure_ascii=False).encode('utf-8') if data is not None else None
        headers = {'Accept':'application/json', 'Content-Type':'application/json',
                   'User-Agent':'MusicLibraryManager/0.5',
                   'X-Emby-Authorization': 'MediaBrowser Client="Music Library Manager", Device="Windows", DeviceId="music-library-manager", Version="0.5"'}
        if self.token:
            headers['X-Emby-Token'] = self.token
        try:
            with urlopen(Request(url, data=body, headers=headers, method=method), timeout=self.timeout) as resp:
                response = resp.read()
                return json.loads(response) if response else {}
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise JellyfinError('Jellyfin rejeitou acesso (401/403). Faça login e confira as permissões do usuário.') from exc
            if exc.code == 404:
                raise JellyfinError('Rota não encontrada no Jellyfin (404). Confira a URL e versão.') from exc
            raise JellyfinError(f'Jellyfin respondeu HTTP {exc.code}.') from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise JellyfinError('Jellyfin inacessível. Confira endereço, rede e servidor.') from exc
        except ValueError as exc:
            raise JellyfinError('Resposta JSON inválida do Jellyfin.') from exc

    def authenticate(self, username: str, password: str):
        if not username.strip() or not password:
            raise JellyfinError('Informe usuário e senha do Jellyfin.')
        answer = self.request('POST', '/Users/AuthenticateByName',
                              {'Username': username.strip(), 'Pw':password}, authenticated=False)
        token = str(answer.get('AccessToken') or '')
        user = answer.get('User') or {}
        user_id = str(user.get('Id') or '')
        if not token or not user_id:
            raise JellyfinError('Login Jellyfin não retornou token/usuário válidos.')
        self.token, self.user_id = token, user_id
        return {'user':str(user.get('Name') or username.strip()), 'user_id':user_id, 'token':token}

    def status(self):
        return self.request('GET', '/System/Info')

    def music(self, limit: int = 5000):
        if not self.user_id:
            raise JellyfinError('Usuário Jellyfin não identificado. Faça login nas Configurações.')
        results = []
        page_size = 500
        while len(results) < max(1, min(limit, 20000)):
            payload = self.request('GET', f'/Users/{self.user_id}/Items', params={
                'Recursive':'true','IncludeItemTypes':'Audio','Fields':'Path,MediaSources',
                'SortBy':'SortName', 'StartIndex':len(results),
                'Limit':min(page_size,limit - len(results))})
            items = payload.get('Items', []) if isinstance(payload, dict) else []
            if not isinstance(items, list):
                raise JellyfinError('Formato inesperado da biblioteca Jellyfin.')
            results.extend(items)
            if not items or len(results) >= int(payload.get('TotalRecordCount') or 0):
                break
        return results

    def playlists(self):
        payload = self.request('GET', f'/Users/{self.user_id}/Items', params={
            'Recursive':'true', 'IncludeItemTypes':'Playlist', 'Limit':1000})
        return payload.get('Items',[]) if isinstance(payload, dict) else []

    def refresh_library(self):
        return self.request('POST', '/Library/Refresh')

    def create_playlist(self, name: str, ids: list[str]):
        clean = name.strip()[:120]
        if not clean or not self.user_id:
            raise JellyfinError('Nome de playlist e usuário Jellyfin são obrigatórios.')
        # Preserve duplicates and track order. Jellyfin uses the supplied item-id order.
        valid = [x for x in ids if isinstance(x,str) and x.strip()]
        if not valid:
            raise JellyfinError('Nenhuma faixa da playlist foi localizada no Jellyfin.')
        payload = {'Name':clean, 'Ids':valid, 'UserId':self.user_id, 'MediaType':'Audio', 'IsPublic':False}
        response = self.request('POST', '/Playlists', payload)
        identifier = response.get('Id') or response.get('id') if isinstance(response,dict) else None
        if not identifier:
            raise JellyfinError('Jellyfin não retornou ID da playlist; verifique no servidor antes de tentar novamente.')
        return {'id':str(identifier),'count':len(valid),'name':clean}


    def playlist_items(self, playlist_id: str, limit: int = 10000):
        """Read playlist entries, including PlaylistItemId for safe removals."""
        if not playlist_id or not self.user_id:
            raise JellyfinError('Playlist e usuário são obrigatórios.')
        rows = []
        while len(rows) < limit:
            response = self.request('GET', f'/Playlists/{playlist_id}/Items',
                params={'UserId':self.user_id,'StartIndex':len(rows),'Limit':min(500,limit-len(rows))})
            items = response.get('Items',[]) if isinstance(response,dict) else []
            if not isinstance(items,list):
                raise JellyfinError('Jellyfin retornou uma lista de músicas inválida.')
            rows.extend(items)
            if not items or len(rows) >= int(response.get('TotalRecordCount') or 0):
                break
        return rows

    def sync_playlist_items(self, playlist_id: str, wanted_ids: list[str], mode: str = 'full_sync'):
        """Update an existing playlist created by this tool. Never remove media files.

        add_only preserves existing entries and appends missing occurrences.
        mirror/rebuild remove references by *PlaylistItemId*, then rebuild in order.
        """
        from collections import Counter
        if mode not in ('add_only','full_sync','rebuild'):
            raise JellyfinError('Modo de sincronização Jellyfin inválido.')
        if not playlist_id:
            raise JellyfinError('Playlist Jellyfin não cadastrada.')
        ids = [i for i in wanted_ids if isinstance(i,str) and i.strip()]
        if not ids:
            raise JellyfinError('Lista sem músicas no Jellyfin. Playlist existente preservada.')
        existing = self.playlist_items(playlist_id)
        if mode == 'add_only':
            have, seen = Counter(str(i.get('Id') or '') for i in existing),Counter()
            missing=[]
            for song_id in ids:
                seen[song_id]+=1
                if seen[song_id] > have[song_id]:
                    missing.append(song_id)
            if missing:
                self.request('POST', f'/Playlists/{playlist_id}/Items',
                             params={'Ids':','.join(missing),'UserId':self.user_id})
            return {'id':playlist_id,'count':len(existing)+len(missing),'added':len(missing),'removed':0}
        entries = [str(x['PlaylistItemId']) for x in existing if x.get('PlaylistItemId')]
        if existing and len(entries) != len(existing):
            raise JellyfinError('Alguns itens não têm PlaylistItemId; nada removido para evitar perda de referências.')
        # Add new references FIRST, then remove old ones. If the add fails,
        # the old playlist remains untouched. No audio file is ever deleted.
        self.request('POST',f'/Playlists/{playlist_id}/Items',
                     params={'Ids':','.join(ids),'UserId':self.user_id})
        if entries:
            self.request('DELETE',f'/Playlists/{playlist_id}/Items',params={'EntryIds':','.join(entries)})
        return {'id':playlist_id,'count':len(ids),'added':len(ids),'removed':len(entries)}


def jellyfin_flac_candidate(item: dict):
    """Jellyfin *reported* FLAC; not proof of lossless source or local readable file."""
    if str(item.get('Type') or '') != 'Audio':
        return None
    sources = item.get('MediaSources') or []
    if not isinstance(sources, list):
        sources = []
    supported = False
    for source in sources:
        container = str(source.get('Container') or '').lower().strip()
        streams = source.get('MediaStreams') or []
        audio = [s for s in streams if str(s.get('Type') or '').lower() == 'audio']
        if container == 'flac' and (not audio or any(str(s.get('Codec') or '').lower() == 'flac' for s in audio)):
            supported = True
            break
    if not supported:
        return None
    duration = int(item.get('RunTimeTicks') or 0) // 10_000_000
    artists = item.get('Artists') or []
    artist = str((artists[0] if artists else '') or item.get('AlbumArtist') or '')
    return {'id':str(item.get('Id') or ''), 'title':str(item.get('Name') or ''),
            'artist':artist,'album':str(item.get('Album') or ''),
            'duration':duration,'path':str(item.get('Path') or ''),
            'container':'flac'}


def link_jellyfin_songs(songs, items):
    """Associate Jellyfin playlist IDs conservatively, without downloading/copying."""
    from flac_matcher import norm, match_score, flac_information
    candidates = [x for item in items if (x := jellyfin_flac_candidate(item))]
    by_title = {}
    for candidate in candidates:
        if candidate['id']:
            by_title.setdefault(norm(candidate['title']),[]).append(candidate)
    matched = 0
    for song in songs:
        song.jellyfin_id = ''
        possibilities = []
        for candidate in by_title.get(norm(song.title), []):
            score = match_score(song,candidate)
            if score >= 80:
                # A *local* FLAC already matched takes precedence when the Jellyfin paths agree.
                if song.path and candidate['path'] and norm(song.path) == norm(candidate['path']):
                    score += 25
                possibilities.append((score,candidate))
        if not possibilities:
            continue
        possibilities.sort(key=lambda pair:pair[0], reverse=True)
        if len(possibilities) > 1 and possibilities[0][0] == possibilities[1][0]:
            # Distinct identical names / releases can be ambiguous.
            continue
        candidate = possibilities[0][1]
        song.jellyfin_id = candidate['id']
        if song.status == 'FLAC confirmado':
            song.note += ' Também indexado no Jellyfin.'
        else:
            accessible = flac_information(__import__('pathlib').Path(candidate['path'])) if candidate['path'] else None
            if accessible and match_score(song,accessible) >= 80:
                song.path = accessible['path']
                song.bits = accessible['bits']
                song.sample_rate = accessible['sample_rate']
                song.status = 'FLAC confirmado'
                song.note = 'Arquivo FLAC local acessível, já indexado no Jellyfin.'
            else:
                song.status = 'FLAC no Jellyfin'
                song.note = 'Jellyfin informa codec FLAC; arquivo não pôde ser validado localmente. Reutilizável na playlist Jellyfin.'
        matched += 1
    return matched

