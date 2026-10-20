"""Non-network URL checks for supported playlist links.

These helpers do not scrape songs and cannot certify access to music or FLAC.
"""
from urllib.parse import urlsplit


def identify_media_url(raw: str):
    """Return service and full input URL, or ('', diagnostic)."""
    raw = raw.strip()
    try:
        url = urlsplit(raw)
        host = (url.hostname or '').lower()
    except ValueError:
        return '', 'URL inválida'
    if url.scheme != 'https' or not host or url.username or url.password:
        return '', 'Use links HTTPS das plataformas'
    pieces = [x for x in url.path.lower().split('/') if x]
    if host == 'open.spotify.com':
        if pieces and pieces[0].startswith('intl-'):
            pieces = pieces[1:]
        if len(pieces) >= 2 and pieces[0] in ('track','album','playlist','artist'):
            return 'Spotify', raw
    elif host in ('tidal.com','listen.tidal.com'):
        if pieces and pieces[0] in ('browse','view'):
            pieces = pieces[1:]
        if len(pieces) >= 2 and pieces[0] in ('track','album','playlist','artist','mix'):
            return 'TIDAL', raw
    return '', 'Link não reconhecido como música, álbum ou playlist do TIDAL/Spotify'


def is_playlist_link(raw: str) -> bool:
    """One-click home action accepts only playlist/mix, unlike Downloads tab."""
    service, _ = identify_media_url(raw)
    if not service:
        return False
    parts = [x for x in urlsplit(raw.strip()).path.lower().split('/') if x]
    if service == 'Spotify' and parts and parts[0].startswith('intl-'):
        parts = parts[1:]
    if service == 'TIDAL' and parts and parts[0] in ('browse','view'):
        parts = parts[1:]
    return bool(parts and parts[0] in ('playlist','mix'))


def can_submit_url(raw: str, lossless_only: bool):
    """Validate user-selected policy without claiming a lossy source is lossless."""
    service, result = identify_media_url(raw)
    if not service:
        return '', result
    if lossless_only and service == 'Spotify':
        return '', 'Spotify/spotDL não fornece FLAC lossless garantido. Use TIDAL com acesso permitido ou uma fonte FLAC legítima.'
    return service, raw.strip()
