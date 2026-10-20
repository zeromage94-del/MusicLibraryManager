"""Fast, isolated, read-only dashboard aggregation for configured music services.

Collect each service independently, preserving errors instead of claiming an
unreachable service is online.  This module has no Qt dependency and can be
exercised using fakes in its unit tests.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

ACTIVE_QBIT_STATES = frozenset({'downloading', 'forcedDL', 'metaDL', 'forcedMetaDL', 'stalledDL', 'queuedDL', 'checkingDL', 'allocating'})
TRANSFER_QBIT_STATES = frozenset({'downloading', 'forcedDL', 'metaDL', 'forcedMetaDL'})


def missing_albums(albums):
    """Only count albums *confirmed* monitored and with zero imported tracks.

    Without Lidarr statistics we cannot know whether an album has missing
    tracks; unknown entries are explicitly excluded rather than counted.
    """
    missing = []
    for album in albums or []:
        if not isinstance(album, dict) or not album.get('monitored'):
            continue
        stats = album.get('statistics') or {}
        count = stats.get('trackFileCount')
        if count is None:
            continue
        try:
            is_missing = int(count) == 0
        except (TypeError, ValueError):
            continue
        if is_missing:
            missing.append(album)
    return missing


def qbit_metrics(torrents):
    rows = list(torrents or [])
    transferring = [t for t in rows if t.get('state') in TRANSFER_QBIT_STATES]
    pending = [t for t in rows if t.get('state') in ACTIVE_QBIT_STATES]
    complete = [t for t in rows if float(t.get('progress') or 0) >= 1.0]
    return {'total': len(rows), 'transferring': len(transferring), 'pending': len(pending),
            'complete': len(complete), 'down_speed': sum(max(0, int(t.get('dlspeed') or 0)) for t in rows),
            'torrents': sorted(rows, key=lambda t: (float(t.get('progress') or 0) >= 1.0, -(int(t.get('dlspeed') or 0)), str(t.get('name') or '').casefold()))}


def collect_one(service, client):
    """Fetch the minimum calls needed for the dashboard, per-endpoint errors retained."""
    if client is None:
        return {'online': False, 'configured': False, 'error': 'Configure as credenciais na aba Configurações.'}
    try:
        if service == 'lidarr':
            status = client.health()
            endpoints = [('artists', client.artists), ('albums', client.albums), ('queue', client.queue)]
        elif service == 'prowlarr':
            status = client.status()
            endpoints = [('indexers', client.indexers), ('health', client.health)]
        elif service == 'qbit':
            status = {'version': client.version()}
            endpoints = [('torrents', client.torrents)]
        else:
            raise ValueError('Serviço desconhecido.')
    except Exception as exc:
        return {'online': False, 'configured': True, 'error': str(exc)}
    result = {'online': True, 'configured': True, 'version': str((status or {}).get('version') or '?'), 'warnings': []}
    for name, get in endpoints:
        try:
            data = get()
            result[name] = data if isinstance(data, list) else []
        except Exception as exc:
            result[name] = None
            result['warnings'].append(f'{name}: {exc}')
    return result


def collect_dashboard(clients):
    """Collect the 3 services in parallel and keep failures independent."""
    names = ('lidarr', 'prowlarr', 'qbit')
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix='music-dashboard') as pool:
        futures = {name: pool.submit(collect_one, name, clients.get(name)) for name in names}
        result = {name: futures[name].result() for name in names}
    result['updated_at'] = datetime.now().astimezone().strftime('%d/%m/%Y %H:%M:%S')
    return result


def summarize(data):
    lidarr, prowlarr, qbit = (data.get(key) or {} for key in ('lidarr', 'prowlarr', 'qbit'))
    albums = lidarr.get('albums')
    torrents = qbit.get('torrents')
    indexers = prowlarr.get('indexers')
    return {
        'artists': len(lidarr['artists']) if lidarr.get('artists') is not None else None,
        'albums': len(albums) if albums is not None else None,
        'missing': len(missing_albums(albums)) if albums is not None else None,
        'lidarr_queue': len(lidarr['queue']) if lidarr.get('queue') is not None else None,
        'indexers': sum(bool(i.get('enable')) for i in indexers) if indexers is not None else None,
        'indexers_total': len(indexers) if indexers is not None else None,
        'qbit_active': qbit_metrics(torrents)['transferring'] if torrents is not None else None,
        'qbit_total': len(torrents) if torrents is not None else None,
        'download_speed': qbit_metrics(torrents)['down_speed'] if torrents is not None else None,
    }
