"""Octave-inspired FLAC playlist synchronization, with persistent local state.

Never fetches audio, bypasses DRM, or represents transcoded audio as verified
lossless. No code from Octave is copied into this module.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import csv
from contextlib import contextmanager
import json
import os
import re
import sqlite3
from urllib.parse import urlsplit

from flac_matcher import (Song, norm, flac_information, match_score,
                          apply_local_matches, scan_flacs, lidarr_album_matches,
                          find_remote_album_candidates, generate_m3u)
from jellyfin_api import link_jellyfin_songs
from playlist_api import identify_media_url, is_playlist_link


MODES = {'add_only': 'Adicionar novas', 'full_sync': 'Espelhar', 'rebuild': 'Reconstruir'}


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def playlist_key(url):
    """Stable playlist identity, without auth tokens / tracking query args."""
    platform, result = identify_media_url(url)
    if platform != 'Spotify' or not is_playlist_link(url):
        raise ValueError('Sincronização por metadados aceita links de playlist Spotify.')
    parts = [x for x in urlsplit(url).path.split('/') if x]
    if parts and parts[0].startswith('intl-'):
        parts = parts[1:]
    return 'spotify:' + parts[1]


def safe_filename(s):
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(s or 'Playlist')).strip(' .')[:92]
    return ('_' + clean if clean.upper() in {'CON','PRN','AUX','NUL'} else clean) or 'Playlist'


class SyncStore:
    """Connection per operation; only call mutations from the foreground worker."""
    def __init__(self, dbfile):
        self.dbfile = Path(dbfile)
        self.dbfile.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS playlists (
                  id TEXT PRIMARY KEY, url TEXT NOT NULL, name TEXT NOT NULL,
                  mode TEXT NOT NULL DEFAULT 'full_sync',
                  output_path TEXT NOT NULL DEFAULT '',
                  last_sync TEXT NOT NULL DEFAULT '',
                  last_error TEXT NOT NULL DEFAULT '',
                  last_count INTEGER NOT NULL DEFAULT 0,
                  last_found INTEGER NOT NULL DEFAULT 0,
                  last_pending INTEGER NOT NULL DEFAULT 0,
                  jellyfin_id TEXT NOT NULL DEFAULT '',
                  enabled INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS tracks (
                  playlist_id TEXT NOT NULL, position INTEGER NOT NULL,
                  payload TEXT NOT NULL,
                  PRIMARY KEY (playlist_id, position),
                  FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS requests (
                  album_key TEXT PRIMARY KEY, lidarr_id INTEGER NOT NULL,
                  requested_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'waiting_for_lidarr'
                );
                CREATE TABLE IF NOT EXISTS events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  playlist_id TEXT NOT NULL, at TEXT NOT NULL,
                  event TEXT NOT NULL, detail TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS events_playlist_idx ON events(playlist_id, id);
            ''')

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.dbfile, timeout=15)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('PRAGMA busy_timeout=15000')
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        finally:
            conn.close()

    def add(self, url, name=None, mode='full_sync'):
        ident = playlist_key(url)
        if mode not in MODES:
            raise ValueError('Modo inválido')
        with self.connect() as c:
            c.execute('''INSERT INTO playlists(id,url,name,mode) VALUES(?,?,?,?)
                        ON CONFLICT(id) DO UPDATE SET url=excluded.url,mode=excluded.mode''',
                      (ident,url,(name or f'Playlist {ident.split(":")[1][:12]}'),mode))
        return ident

    def list(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute('SELECT * FROM playlists ORDER BY name COLLATE NOCASE')]

    def get(self, ident):
        with self.connect() as c:
            row = c.execute('SELECT * FROM playlists WHERE id=?',(ident,)).fetchone()
        if not row:
            raise KeyError('Playlist não encontrada no cadastro.')
        return dict(row)

    def tracks(self, ident):
        with self.connect() as c:
            rows = c.execute('SELECT payload FROM tracks WHERE playlist_id=? ORDER BY position', (ident,)).fetchall()
        return [Song(**json.loads(r['payload'])) for r in rows]

    def set_mode(self, ident, mode):
        if mode not in MODES:
            raise ValueError('Modo inválido')
        with self.connect() as c:
            c.execute('UPDATE playlists SET mode=? WHERE id=?', (mode,ident))

    def set_jellyfin_id(self, ident, jid):
        with self.connect() as c:
            c.execute('UPDATE playlists SET jellyfin_id=? WHERE id=?',(jid,ident))

    def remove(self, ident):
        with self.connect() as c:
            c.execute('DELETE FROM playlists WHERE id=?',(ident,))
            c.execute('DELETE FROM events WHERE playlist_id=?',(ident,))

    def event(self, ident, event, detail=''):
        with self.connect() as c:
            c.execute('INSERT INTO events(playlist_id,at,event,detail) VALUES (?,?,?,?)',
                      (ident,utcnow(),event,str(detail)[:700]))

    def history(self, ident=None, limit=80):
        with self.connect() as c:
            if ident:
                rows = c.execute('SELECT * FROM events WHERE playlist_id=? ORDER BY id DESC LIMIT ?', (ident,limit))
            else:
                rows = c.execute('SELECT * FROM events ORDER BY id DESC LIMIT ?', (limit,))
            return [dict(r) for r in rows]

    def requested(self):
        with self.connect() as c:
            return {r['album_key']:dict(r) for r in c.execute('SELECT * FROM requests')}

    def mark_requested(self, album_key, lidarr_id):
        with self.connect() as c:
            c.execute('INSERT OR IGNORE INTO requests(album_key,lidarr_id,requested_at) VALUES (?,?,?)',
                      (album_key,int(lidarr_id),utcnow()))

    def save_snapshot(self, ident, fresh_songs, output_root):
        """Atomic DB commit and atomic UTF-8 M3U writing. Never delete audio.

        add_only uses stored playlist entries as base, and append newly introduced
        entries. Other modes follow current external ordering. Matches are always
        revalidated; old cached paths only survive if current file is valid.
        """
        item = self.get(ident)
        previous = self.tracks(ident)
        mode = item['mode']
        songs = merge_playlist(previous, fresh_songs, mode)
        # For add_only, old FLACs are revalidated before writing below.
        root = Path(output_root).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        output = Path(item['output_path']) if item['output_path'] else (root / (safe_filename(item['name']) + '_' + ident.split(':')[1][:8] + '.m3u8'))
        if not output.is_absolute():
            output = root / output
        output.parent.mkdir(parents=True, exist_ok=True)
        # Build first so failures do not corrupt prior M3U or persisted track state.
        content = ['#EXTM3U']
        found = 0
        for song in songs:
            if song.status == 'FLAC confirmado' and song.path:
                check = flac_information(Path(song.path))
                if check and match_score(song,check) >= 80:
                    content.extend((f'#EXTINF:{song.duration or -1},{song.artist} - {song.title}',song.path))
                    found += 1
                else:
                    song.status, song.path = 'Pendente', ''
                    song.bits, song.sample_rate = 0, 0
                    song.note = 'FLAC antigo indisponível; aguarda nova correspondência.'
        import tempfile
        fd, tmp = tempfile.mkstemp(prefix='.mlm_', suffix='.m3u8', dir=str(output.parent))
        try:
            with os.fdopen(fd,'w', encoding='utf-8-sig',newline='\n') as f:
                f.write('\n'.join(content)+'\n')
            # commit DB before replacing M3U. A failed replace won't modify existing playlist.
            with self.connect() as c:
                c.execute('DELETE FROM tracks WHERE playlist_id=?',(ident,))
                c.executemany('INSERT INTO tracks(playlist_id,position,payload) VALUES(?,?,?)',
                              [(ident,i,json.dumps(s.as_record(),ensure_ascii=False)) for i,s in enumerate(songs)])
                c.execute('''UPDATE playlists SET output_path=?,last_sync=?,last_error='',
                           last_count=?,last_found=?,last_pending=? WHERE id=?''',
                          (str(output),utcnow(),len(songs),found,len(songs)-found,ident))
                c.execute('INSERT INTO events(playlist_id,at,event,detail) VALUES(?,?,?,?)',
                          (ident,utcnow(),'Sincronizada',f'{found}/{len(songs)} FLAC locais'))
            os.replace(tmp,output)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return {'id':ident,'name':item['name'],'output':str(output),'songs':songs,
                'total':len(songs),'found':found,'pending':len(songs)-found,'mode':mode}

    def error(self,ident,message):
        with self.connect() as c:
            c.execute('UPDATE playlists SET last_error=? WHERE id=?',(str(message)[:1000],ident))
        self.event(ident,'Falha',str(message))


def track_signature(song):
    # Keep duplicated positions from the original playlist (1:1) while uniquely
    # identifying the *recording*. Key ISRC alone is insufficient for distinct editions.
    return (norm(song.isrc),norm(song.artist),norm(song.title),norm(song.album),int(song.duration or 0))


def merge_playlist(previous, fresh, mode):
    if mode not in MODES:
        raise ValueError('Modo inválido')
    if mode != 'add_only':
        return list(fresh)
    from collections import Counter
    existed = Counter(track_signature(s) for s in previous)
    received = Counter()
    updated = list(previous)
    for song in fresh:
        key = track_signature(song)
        received[key] += 1
        if received[key] > existed[key]:
            updated.append(song)
    return updated


def cache_reuse(songs, stored, skip_matched=False):
    """Check cached FLAC path against on-disk stream metadata before trusting it."""
    candidates = {}
    for s in stored:
        if s.path:
            candidates.setdefault(track_signature(s),[]).append(s.path)
    count=0
    for song in songs:
        if skip_matched and song.status == 'FLAC confirmado' and song.path:
            continue
        for filename in candidates.get(track_signature(song),[]):
            path = Path(filename)
            info = flac_information(path)
            if info and match_score(song,info) >= 80:
                song.path=info['path']; song.bits=info['bits']; song.sample_rate=info['sample_rate']
                song.status='FLAC confirmado'; song.note='Reutilizado do cache; arquivo revalidado no disco.'
                count += 1
                break
    return count


def sync_playlist(store, ident, fetch_metadata, music_root, playlist_root,
                  lidarr_client=None, jellyfin_client=None, lookup_remote=True):
    """Read Spotify metadata, reuse proven local FLAC, locate remaining items.

    This never requests Lidarr downloads. User must separately confirm searches.
    If a provider is unavailable, local FLAC playlists are still generated.
    """
    p = store.get(ident)
    try:
        incoming = fetch_metadata(p['url'])
        if not incoming:
            raise ValueError('Nenhuma música encontrada. Não sobrescrevemos a playlist existente.')
        previous = store.tracks(ident)
        cached = cache_reuse(incoming,previous)
        local, truncated = scan_flacs(Path(music_root))
        # Prefer strongest currently available local FLAC (bit depth, rate).
        apply_local_matches(incoming,local)
        # Cache may include a second legitimate music volume not in scan root;
        # reuse only when the in-root library did not provide a validated file.
        cache_reuse(incoming,previous,skip_matched=True)
        warnings=[]
        if truncated:
            warnings.append('Biblioteca local limitada a 20 mil FLACs; aumente o limite se necessário.')
        if lidarr_client:
            try:
                lidarr_album_matches(incoming,lidarr_client.albums(),lidarr_client.artists())
                if lookup_remote:
                    remote=find_remote_album_candidates(lidarr_client,incoming,lidarr_client.artists())
                    warnings.extend(remote.get('errors') or [])
            except Exception as exc:
                warnings.append('Lidarr: ' + str(exc))
        if jellyfin_client:
            try:
                link_jellyfin_songs(incoming,jellyfin_client.music())
            except Exception as exc:
                warnings.append('Jellyfin: ' + str(exc))
        # Preserve the waiting state across sync runs even if Lidarr returns
        # the same album in its library rather than downloading it yet.
        history = store.requested()
        for song in incoming:
            if song.status in ('FLAC confirmado','FLAC no Jellyfin'):
                continue
            foreign = str((song.catalogue_record or {}).get('foreignAlbumId') or '')
            keys = ([f'lidarr:{song.album_id}'] if song.album_id > 0 else [])
            if song.artist_id and foreign:
                keys.append(f'catalogue:{song.artist_id}:{foreign}')
            if any(key in history for key in keys):
                song.status = 'Aguardando Lidarr'
                song.note = 'Busca já solicitada; esperando importação e reverificação do arquivo FLAC.'
        result=store.save_snapshot(ident,incoming,playlist_root)
        result.update({'cached':cached,'scanned':len(local),'warnings':warnings})
        return result
    except Exception as exc:
        store.error(ident,str(exc))
        raise


def request_new_albums(store, ident, lidarr_client, requester=None):
    """Submit missing album searches *only after UI consent*, with persistent dedupe.

    Supports Lidarr library albums and unique remote-catalog album matches.
    """
    songs = store.tracks(ident)
    previous = store.requested()
    from collections import OrderedDict
    from flac_matcher import request_missing_albums
    keys = OrderedDict()
    actionable=[]
    for song in songs:
        if song.path or song.status in ('FLAC confirmado','FLAC no Jellyfin'):
            continue
        foreign = str((song.catalogue_record or {}).get('foreignAlbumId') or '')
        if song.album_id > 0:
            key = f'lidarr:{song.album_id}'
        elif song.status == 'Catálogo encontrado' and song.artist_id and foreign:
            key = f'catalogue:{song.artist_id}:{foreign}'
        else:
            continue
        if key not in previous:
            keys.setdefault(key,[]).append(song)
            actionable.append(song)
    if not actionable:
        return {'submitted':[],'registered':[],'errors':[],
                'skipped':'Todos os álbuns já solicitados ou ainda não cadastrados.'}
    report=request_missing_albums(lidarr_client,actionable)
    for key, members in keys.items():
        ids = {s.album_id for s in members if s.album_id > 0}
        confirmed = ids.intersection(report.get('submitted',[]))
        if confirmed:
            registered_id=next(iter(confirmed))
            store.mark_requested(key,registered_id)
            store.mark_requested(f'lidarr:{registered_id}',registered_id)
            for original in songs:
                if original.album_id == registered_id or (
                    original.album_id==0 and original.artist_id==members[0].artist_id and
                    original.catalogue_record and
                    original.catalogue_record.get('foreignAlbumId')==members[0].catalogue_record.get('foreignAlbumId')):
                    original.album_id=registered_id
                    original.status='Busca solicitada'
                    original.note='Aguardando importação pelo Lidarr; arquivo FLAC ainda não confirmado.'
    with store.connect() as c:
        c.executemany('UPDATE tracks SET payload=? WHERE playlist_id=? AND position=?',
                      [(json.dumps(song.as_record(),ensure_ascii=False),ident,i) for i,song in enumerate(songs)])
    store.event(ident,'Buscas Lidarr',f'{len(report.get("submitted",[]))} álbuns solicitados')
    return report


def export_missing_csv(songs, filename):
    file = Path(filename)
    file.parent.mkdir(parents=True,exist_ok=True)
    with file.open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.writer(f,delimiter=';')
        writer.writerow(['Faixa','Artista','Álbum','Duração (s)','ISRC','Estado','Observação'])
        for s in songs:
            if s.status not in ('FLAC confirmado','FLAC no Jellyfin'):
                writer.writerow([s.title,s.artist,s.album,s.duration,s.isrc,s.status,s.note])
    return file
