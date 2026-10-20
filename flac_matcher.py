"""Playlist metadata -> verifiable local FLAC -> Lidarr album requests.

No media is fetched here. Spotify's 'save' exports track metadata only;
Lidarr may use separately configured, authorized sources/download clients.
A codec/bit-depth check cannot prove the recording was never transcoded.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import json
import re
import subprocess
import unicodedata


class MatchError(Exception):
    pass


def norm(value: object) -> str:
    s = unicodedata.normalize('NFKD', str(value or '')).casefold()
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r'[^\w]+', ' ', s, flags=re.UNICODE)
    return ' '.join(s.split())


def first(value: Any) -> str:
    if isinstance(value, list):
        return first(value[0]) if value else ''
    return str(value or '')


def artists_from(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(x.get('name') or x.get('artist') or '').strip() if isinstance(x, dict) else str(x).strip() for x in value if x]
    return []


@dataclass
class Song:
    title: str
    artist: str
    album: str = ''
    album_artist: str = ''
    duration: int = 0
    isrc: str = ''
    status: str = 'Pendente'
    path: str = ''
    bits: int = 0
    sample_rate: int = 0
    album_id: int = 0
    artist_id: int = 0
    jellyfin_id: str = ''
    catalogue_record: dict = field(default_factory=dict)
    note: str = ''

    def display_quality(self):
        return f'{self.bits} bit / {self.sample_rate / 1000:g} kHz' if self.bits else '—'

    def as_record(self):
        return asdict(self)


def read_spotdl_metadata(file: Path) -> list[Song]:
    """Read spotDL save format (list or {songs:[...]}), never the audio."""
    try:
        content = json.loads(file.read_text(encoding='utf-8-sig'))
    except (ValueError, UnicodeError, OSError) as exc:
        raise MatchError('O arquivo .spotdl não contém JSON válido.') from exc
    rows = content if isinstance(content, list) else content.get('songs', []) if isinstance(content, dict) else []
    if not isinstance(rows, list) or not rows:
        raise MatchError('Nenhuma faixa foi retornada pelo spotDL. Verifique autenticação/visibilidade da playlist.')
    result = []
    for entry in rows:
        if not isinstance(entry, dict):
            continue
        title = first(entry.get('name') or entry.get('title'))
        artists = artists_from(entry.get('artists') or entry.get('artist'))
        artist = first(entry.get('artist_name')) or (artists[0] if artists else '')
        album = first(entry.get('album_name') or entry.get('album'))
        album_artist = first(entry.get('album_artist')) or artist
        duration = entry.get('duration') or 0
        try:
            duration = int(float(duration))
            if duration > 100000:  # Spotify API milliseconds
                duration //= 1000
        except (ValueError, TypeError):
            duration = 0
        if title and artist:
            result.append(Song(title=title, artist=artist, album=album, album_artist=album_artist, duration=duration,
                               isrc=first(entry.get('isrc')).upper()))
    if not result:
        raise MatchError('Metadados sem título/artista. Nenhum arquivo foi baixado.')
    return result


def export_spotify_playlist(url: str, spotdl_program: str, file: Path, timeout=240) -> list[Song]:
    """Use the documented `spotdl save`: it downloads metadata, not audio."""
    from playlist_api import is_playlist_link, identify_media_url
    if identify_media_url(url)[0] != 'Spotify' or not is_playlist_link(url):
        raise MatchError('Cole um link válido de playlist Spotify.')
    file.parent.mkdir(parents=True, exist_ok=True)
    if file.exists():
        file.unlink()  # Never reuse stale playlist data
    args = [spotdl_program, 'save', url, '--save-file', str(file)]
    try:
        proc = subprocess.run(args, cwd=str(file.parent), capture_output=True, text=True,
                              timeout=timeout, check=False, errors='replace')
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MatchError('Não foi possível consultar os metadados Spotify pelo spotDL: ' + str(exc)) from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or '').strip()[-550:]
        raise MatchError('spotDL falhou ao ler a playlist (não houve download de áudio): ' + detail)
    return read_spotdl_metadata(file)


def flac_information(path: Path):
    """True FLAC bitstream parser; cannot certify provenance of source audio."""
    from mutagen.flac import FLAC
    try:
        if not path.is_file() or path.stat().st_size < 42:
            return None
        with path.open('rb') as stream:
            if stream.read(4) != b'fLaC':
                return None
        tags = FLAC(path)
        if tags.info.bits_per_sample < 16 or tags.info.sample_rate < 8000:
            return None
        artist = first(tags.get('artist') or tags.get('albumartist'))
        return dict(path=str(path), title=first(tags.get('title')), artist=artist,
                    album=first(tags.get('album')), duration=int(tags.info.length),
                    isrc=first(tags.get('isrc')).upper(), bits=tags.info.bits_per_sample,
                    sample_rate=tags.info.sample_rate, has_cover=bool(tags.pictures))
    except Exception:
        return None


def scan_flacs(root: Path, max_files=20000) -> tuple[list[dict], bool]:
    """Scan local library (and accessible mounted Lidarr folders)."""
    if not root.is_dir():
        return [], False
    found = []
    truncated = False
    for i, file in enumerate(root.rglob('*')):
        if file.suffix.lower() != '.flac':
            continue
        if i > max_files * 8 or len(found) >= max_files:
            truncated = True
            break
        info = flac_information(file)
        if info:
            found.append(info)
    return found, truncated


def match_score(song: Song, candidate: dict) -> float:
    """Conservative matching. Versions/remixes are intentionally not fuzzy-matched."""
    st, ct = norm(song.title), norm(candidate.get('title'))
    sa, ca = norm(song.artist), norm(candidate.get('artist'))
    if not st or not sa or not ct or not ca:
        return 0.0
    if st != ct:  # avoid wrong remasters, live editions and edits
        return 0.0
    if not (sa == ca or sa in ca.split(' feat ') or ca in sa.split(' feat ')):
        return 0.0
    song_isrc = norm(song.isrc)
    candidate_isrc = norm(candidate.get('isrc'))
    if song_isrc and candidate_isrc and song_isrc != candidate_isrc:
        return 0.0
    target_album = norm(song.album)
    actual_album = norm(candidate.get('album'))
    if target_album and actual_album and target_album != actual_album:
        # An identical ISRC is a reliable recording match despite album differences.
        if not (song_isrc and candidate_isrc and song_isrc == candidate_isrc):
            return 0.0
    d1, d2 = song.duration, candidate.get('duration') or 0
    if d1 and d2 and abs(d1 - d2) > 4:
        return 0.0
    score = 80.0
    if target_album and actual_album and target_album == actual_album:
        score += 10
    if d1 and d2:
        score += 5
    if song_isrc and candidate_isrc and song_isrc == candidate_isrc:
        score += 10
    return score


def apply_local_matches(songs: list[Song], files: list[dict]) -> int:
    """Mutates statuses; matches only stream-validated .flac files."""
    index: dict[str,list[dict]] = {}
    for file in files:
        index.setdefault(norm(file.get('title')), []).append(file)
    count = 0
    for song in songs:
        matches = [(match_score(song, item), item) for item in index.get(norm(song.title), [])]
        matches = [(score, item) for score, item in matches if score >= 80]
        if matches:
            score, match = max(matches, key=lambda row: (row[0], row[1].get('bits', 0), row[1].get('sample_rate', 0)))
            song.status = 'FLAC confirmado'
            song.path = str(match['path'])
            song.bits = match['bits']
            song.sample_rate = match['sample_rate']
            song.note = ('FLAC técnico válido; origem lossless anterior à codificação não é comprovável. '
                         + ('Capa incorporada.' if match.get('has_cover') else 'Sem capa incorporada.'))
            count += 1
        elif song.status == 'FLAC confirmado':
            song.status, song.path, song.bits, song.sample_rate = 'Pendente', '', 0, 0
    return count


def lidarr_album_matches(songs: list[Song], albums: list[dict], artists: list[dict]):
    """Match Spotify album+artist against monitored Lidarr albums; no title-only requests."""
    artist_names = {a.get('id'): str(a.get('artistName') or '') for a in artists}
    catalog = {}
    for album in albums:
        artist = (album.get('artist') or {}).get('artistName') or artist_names.get(album.get('artistId')) or ''
        key = (norm(artist), norm(album.get('title')))
        if all(key):
            catalog.setdefault(key, []).append(album)
    possible = {}
    for song in songs:
        if song.status == 'FLAC confirmado':
            continue
        key = (norm(song.album_artist or song.artist), norm(song.album))
        records = catalog.get(key) or []
        records = [a for a in records if int(a.get('id') or 0) > 0]
        if len(records) == 1:
            album = records[0]
            song.album_id = int(album['id'])
            song.status = 'Álbum no Lidarr'
            song.note = 'A busca do Lidarr é por álbum; cada faixa será revalidada depois da importação.'
            possible[song.album_id] = album
        else:
            song.status = 'Não catalogada' if not records else 'Álbum ambíguo'
            song.note = ('Artista/álbum não cadastrados no Lidarr.' if not records else
                         'Há vários lançamentos com mesmo artista e álbum; escolha manualmente no Lidarr.')
    return possible


def find_remote_album_candidates(client, songs: list[Song], artists: list[dict], max_lookups=40):
    """Non-mutating remote catalogue lookup; only exact, unique album+artist.

    This does not invent an audio source and does not change Lidarr. Avoids
    matching an album belonging to a different artist or a similarly named
    deluxe/reissue edition.
    """
    known_artists = {norm(a.get('artistName')): a.get('id') for a in artists if a.get('id')}
    grouped = {}
    for song in songs:
        if song.status != 'Não catalogada':
            continue
        key = (norm(song.album_artist or song.artist), norm(song.album))
        if key[0] and key[1] and known_artists.get(key[0]):
            grouped.setdefault(key, []).append(song)
    searched, errors = 0, []
    for (artist, album), tracks in list(grouped.items())[:max_lookups]:
        try:
            hits = client.lookup_albums(f'{tracks[0].album_artist or tracks[0].artist} {tracks[0].album}')
            exact = []
            for hit in hits:
                title = norm(hit.get('title'))
                name = norm((hit.get('artist') or {}).get('artistName') or hit.get('artistName'))
                if title == album and (not name or name == artist) and hit.get('foreignAlbumId'):
                    exact.append(hit)
            if len(exact) != 1:
                continue
            for song in tracks:
                song.artist_id = int(known_artists[artist])
                song.catalogue_record = exact[0]
                song.status = 'Catálogo encontrado'
                song.note = 'Lançamento exato localizado no catálogo, ainda NÃO baixado. Requer confirmação para cadastrar e buscar.'
            searched += 1
        except Exception as exc:
            errors.append(str(exc))
    return {'found': searched, 'errors': errors, 'skipped': max(0, len(grouped)-max_lookups)}


def request_missing_albums(client, songs: list[Song]) -> dict:
    """Submit de-duplicated album searches, only with user approval from UI."""
    registered, failures = [], []
    pending = {}
    for song in songs:
        if song.status == 'Catálogo encontrado' and song.catalogue_record and song.artist_id:
            key = (song.artist_id, str(song.catalogue_record.get('foreignAlbumId') or ''))
            pending[key] = song.catalogue_record
    for (artist_id, fid), record in sorted(pending.items()):
        try:
            album = client.register_album(record, artist_id)
            new_id = int(album['id'])
            registered.append(new_id)
            for song in songs:
                if song.artist_id == artist_id and str(song.catalogue_record.get('foreignAlbumId')) == fid:
                    song.album_id = new_id
        except Exception as exc:
            failures.append((fid, str(exc)))
    ids = sorted({s.album_id for s in songs if s.album_id > 0 and s.status not in ('FLAC confirmado', 'FLAC no Jellyfin')})
    successes = []
    for album_id in ids:
        try:
            client.search_album(album_id)
            successes.append(album_id)
        except Exception as exc:
            failures.append((album_id, str(exc)))
    for song in songs:
        if song.album_id in successes and song.status not in ('FLAC confirmado', 'FLAC no Jellyfin'):
            song.status = 'Busca solicitada'
            song.note = 'Lidarr consultará os indexadores configurados; FLAC não garantido.'
    return {'submitted': successes, 'registered': registered, 'errors': failures}


def generate_m3u(songs: list[Song], destination: Path):
    """Export only matched, accessible FLAC tracks, in playlist order."""
    content = ['#EXTM3U']
    for song in songs:
        if song.status == 'FLAC confirmado' and song.path and Path(song.path).is_file():
            content.append(f'#EXTINF:{song.duration or -1},{song.artist} - {song.title}')
            content.append(song.path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text('\n'.join(content)+'\n', encoding='utf-8-sig')
    return (len(content)-1)//2


def export_mobile_package(songs: list[Song], destination: Path, name='Playlist FLAC') -> dict:
    """Copy unique already-present FLAC files, export ordered *relative* M3U8.

    Nothing is fetched from the network. Repeated playlist entries point to the
    same copied file. Audio codec is checked before copying, and original tags
    including embedded covers are preserved with copy2.
    """
    from collections import defaultdict
    import hashlib
    import shutil

    def clean(text):
        # Windows reserved filename protection plus cross-device safety.
        s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(text or 'Sem nome'))
        s = s.strip(' .')[:90] or 'Sem nome'
        if s.upper() in {'CON','PRN','AUX','NUL', *(f'COM{i}' for i in range(1,10)),
                          *(f'LPT{i}' for i in range(1,10))}:
            s = '_' + s
        return s

    destination = Path(destination).expanduser()
    selected = [s for s in songs if s.status == 'FLAC confirmado' and s.path]
    if not selected:
        raise MatchError('Nenhum FLAC local confirmado para exportar ao celular.')
    # Preflight: don't leave an incomplete playlist due to invalid FLAC sources.
    validated = {}
    for song in selected:
        src = Path(song.path)
        identifier = str(src.absolute()).casefold()
        if identifier not in validated:
            item = flac_information(src)
            if not item:
                raise MatchError('Um arquivo selecionado não é FLAC válido: ' + str(src))
            validated[identifier] = (src,item)
    destination.mkdir(parents=True,exist_ok=True)
    folder = destination / 'Music'
    folder.mkdir(exist_ok=True)
    copied = {}
    playlist = ['#EXTM3U']
    covers_written = set()
    for song in selected:
        src, info = validated[str(Path(song.path).absolute()).casefold()]
        origin = str(src.absolute()).casefold()
        if origin not in copied:
            suffix = hashlib.sha256(origin.encode('utf-8')).hexdigest()[:8]
            album_dir = folder / clean(song.album_artist or song.artist) / clean(song.album or 'Singles')
            album_dir.mkdir(parents=True,exist_ok=True)
            target = album_dir / f'{clean(song.title)}-{suffix}.flac'
            if (not target.is_file() or target.stat().st_size != src.stat().st_size
                    or target.stat().st_mtime_ns != src.stat().st_mtime_ns):
                shutil.copy2(src,target)
            copied[origin] = target
            if str(album_dir) not in covers_written:
                covers_written.add(str(album_dir))
                cover = album_dir / 'cover.jpg'
                if not cover.exists():
                    try:
                        from mutagen.flac import FLAC
                        pictures = FLAC(src).pictures
                        pictures = [pic for pic in pictures if str(pic.mime).lower() == 'image/jpeg']
                        if pictures:
                            cover.write_bytes(pictures[0].data)
                    except (ValueError,OSError):
                        pass
        target = copied[origin]
        playlist.append(f'#EXTINF:{song.duration or info.get("duration") or -1},{song.artist} - {song.title}')
        playlist.append(target.relative_to(destination).as_posix())
    final_file = destination / (clean(name) + '.m3u8')
    final_file.write_text('\n'.join(playlist)+'\n',encoding='utf-8-sig')
    return {'tracks':len(selected),'unique_files':len(copied),'playlist':str(final_file),
            'excluded':len(songs)-len(selected),
            'bytes':sum(src.stat().st_size for src,_ in validated.values())}
