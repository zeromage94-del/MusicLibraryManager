"""One fixed CC BY test track; no arbitrary source or playlist inputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

SPOTIFY = 'https://open.spotify.com/track/6PyUsu22LkahLb2bOOfdUZ'
SOURCE = 'https://www.scottbuckley.com.au/library/wp-content/uploads/2018/12/sb_snowfall.mp3'
LICENSE_PAGE = 'https://www.scottbuckley.com.au/library/snowfall/'
# Public thumbnail supplied by Spotify's oEmbed endpoint for this exact test track.
# It is inspected in the temporary runner only and excluded from uploaded artifacts.
COVER = 'https://image-cdn-ak.spotifycdn.com/image/ab67616d00001e02d799783c0945c519747e42a4'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spotdl', required=True, type=Path)
    parser.add_argument('--report-dir', required=True, type=Path)
    args = parser.parse_args()
    report_dir = args.report_dir.resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    work = report_dir.parent / 'licensed-track-work'
    work.mkdir(parents=True, exist_ok=False)
    audio_dir = work / 'audio'
    audio_dir.mkdir()
    report = {
        'started_utc': datetime.now(timezone.utc).isoformat(),
        'binary_source_run': 39476119104,
        'track': 'Snowfall', 'artist': 'Scott Buckley',
        'source_audio': SOURCE, 'spotify_metadata_url': SPOTIFY,
        'license': 'CC BY 4.0', 'license_page': LICENSE_PAGE,
        'maximum_tracks': 1, 'expected_output': 'M4A/AAC',
        'source_format': 'MP3 (not a lossless source)',
        'gui_tested': False, 'flac_tested': False,
        'audio_redistributed': False,
        'checks': {}, 'stages': {}, 'test_passed': False,
        'spotify_end_to_end_passed': False,
    }
    (report_dir / 'ATTRIBUTION.txt').write_text(
        "'Snowfall' by Scott Buckley - released under CC-BY 4.0.\n"
        'www.scottbuckley.com.au\n'
        f'Work/license: {LICENSE_PAGE}\n'
        'License: https://creativecommons.org/licenses/by/4.0/\n'
        f'Authorized source: {SOURCE}\n'
        'Test transformation: MP3 to M4A/AAC.\n'
        'Only text evidence is uploaded; audio and album artwork are excluded.\n',
        encoding='utf-8',
    )

    def run(name, command, timeout, *, separate_stderr=False):
        started = time.monotonic()
        process = subprocess.Popen(
            [str(value) for value in command], cwd=work,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE if separate_stderr else subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace',
        )
        try:
            output, error_output = process.communicate(timeout=timeout)
            code = process.returncode
        except subprocess.TimeoutExpired as exc:
            # A onefile PyInstaller bootloader can own a child process. Kill only
            # this stage's process tree so a timed-out request cannot remain alive.
            if os.name == 'nt':
                subprocess.run(
                    ['taskkill', '/PID', str(process.pid), '/T', '/F'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=15, check=False,
                )
            else:
                process.kill()
            output = exc.stdout or ''
            if isinstance(output, bytes):
                output = output.decode('utf-8', errors='replace')
            try:
                output, error_output = process.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                error_output = None
            output += f'\nStage timed out after {timeout} seconds.\n'
            code = None
        log = output
        if separate_stderr and error_output:
            log += '\n[stderr]\n' + error_output
        (report_dir / f'{name}.log').write_text(log, encoding='utf-8')
        report['stages'][name] = {
            'exit_code': code, 'seconds': round(time.monotonic() - started, 3),
            'timed_out': code is None,
        }
        return code, output

    try:
        if not args.spotdl.is_file():
            raise RuntimeError('The compiled spotdl.exe was not found.')
        ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
        if not ffmpeg or not ffprobe:
            raise RuntimeError('FFmpeg and ffprobe must be installed.')
        run('binary-version', [args.spotdl, '--version'], 30)

        metadata_file = work / 'spotify-metadata.spotdl'
        code, _ = run('spotify-metadata', [
            args.spotdl, 'save', SPOTIFY, '--save-file', metadata_file,
        ], 90)
        record = None
        if code == 0 and metadata_file.exists():
            try:
                rows = json.loads(metadata_file.read_text(encoding='utf-8-sig'))
                if (isinstance(rows, list) and len(rows) == 1
                        and rows[0].get('name', '').casefold() == 'snowfall'
                        and 'scott buckley' in rows[0].get('artists', [])[0].casefold()):
                    record = rows[0]
            except (ValueError, KeyError, IndexError, AttributeError):
                pass
        report['spotify_metadata_passed'] = record is not None

        if record is None:
            # Explicit fixed fixture keeps an unavailable Spotify API separate from
            # the real frozen downloader test. It is never reported as Spotify success.
            report['metadata_mode'] = 'curated artist metadata; Spotify stage failed'
            record = {
                'name': 'Snowfall', 'artists': ['Scott Buckley'],
                'artist': 'Scott Buckley', 'genres': [],
                'disc_number': 1, 'disc_count': 1,
                'album_name': 'Snowfall', 'album_artist': 'Scott Buckley',
                'duration': 248, 'year': 2018, 'date': '2018-12-20',
                'track_number': 1, 'tracks_count': 1,
                'song_id': '6PyUsu22LkahLb2bOOfdUZ', 'explicit': False,
                'publisher': 'Scott Buckley', 'url': SPOTIFY,
                'isrc': None, 'cover_url': COVER,
                'copyright_text': 'Scott Buckley; CC BY 4.0',
            }
        else:
            report['metadata_mode'] = 'exported by the bundled spotDL from Spotify'

        # This fixed, artist-authorized URL prevents any search for alternate audio.
        record['download_url'] = SOURCE
        record['lyrics'] = None  # Instrumental; lyric downloading is out of scope.
        selected = work / 'one-authorized-track.spotdl'
        selected.write_text(json.dumps([record], ensure_ascii=False), encoding='utf-8')
        download_code, _ = run('download', [
            args.spotdl, 'download', selected,
            '--format', 'm4a', '--bitrate', 'disable', '--threads', '1',
            '--audio', 'youtube', '--lyrics',
            '--output', audio_dir / '{artist}' / '{album}' / '{title}.{output-ext}',
        ], 240)
        media = [p for p in audio_dir.rglob('*') if p.is_file() and p.suffix.lower()
                 in {'.m4a', '.mp3', '.flac', '.wav', '.ogg', '.opus'}]
        report['audio_files_found'] = len(media)
        report['checks']['exactly_one_audio_file'] = len(media) == 1
        if len(media) != 1:
            raise RuntimeError('Expected exactly one downloaded audio file; inspect download.log.')
        file = media[0]
        code, probe_text = run('ffprobe', [
            ffprobe, '-v', 'error', '-show_format', '-show_streams', '-of', 'json', file,
        ], 45, separate_stderr=True)
        if code != 0:
            raise RuntimeError('ffprobe could not inspect the output file.')
        probe = json.loads(probe_text)
        streams = [s for s in probe['streams'] if s.get('codec_type') == 'audio']
        duration = float(probe['format']['duration'])
        from mutagen.mp4 import MP4
        audio = MP4(file)
        tags = audio.tags or {}
        title = tags.get('\xa9nam', [''])[0]
        artists = tags.get('\xa9ART', [])
        covers = tags.get('covr', [])
        cover_bytes = bytes(covers[0]) if covers else b''
        cover_format = ('JPEG' if cover_bytes.startswith(b'\xff\xd8\xff') else
                        'PNG' if cover_bytes.startswith(b'\x89PNG\r\n\x1a\n') else
                        'WEBP' if cover_bytes.startswith(b'RIFF') and cover_bytes[8:12] == b'WEBP'
                        else 'unknown')
        cover_streams = [s for s in probe['streams']
                         if s.get('disposition', {}).get('attached_pic')]
        cover_valid = bool(cover_bytes and cover_format in {'JPEG', 'PNG'}
                           and any(s.get('width', 0) > 0 and s.get('height', 0) > 0
                                   for s in cover_streams))
        report['checks'].update({
            'process_succeeded': download_code == 0,
            'm4a_extension': file.suffix.lower() == '.m4a',
            'nonempty_audio': file.stat().st_size > 100_000,
            'one_aac_stream': len(streams) == 1 and streams[0].get('codec_name') == 'aac',
            'full_track_duration': 240 <= duration <= 256,
        })
        report['checks'].update({
            'title_tag': title.casefold() == 'snowfall',
            'artist_tag': any('scott buckley' in artist.casefold() for artist in artists),
            'cover_embedded': bool(tags.get('covr')),
            'cover_valid': cover_valid,
        })
        code, _ = run('full-decode', [
            ffmpeg, '-hide_banner', '-v', 'error', '-xerror', '-i', file,
            '-map', '0:a:0', '-f', 'null', '-',
        ], 90)
        report['checks']['complete_audio_decodes'] = code == 0
        digest = hashlib.sha256()
        with file.open('rb') as reader:
            for block in iter(lambda: reader.read(1024 * 1024), b''):
                digest.update(block)
        report['file'] = {
            'relative_path': file.relative_to(work).as_posix(),
            'bytes': file.stat().st_size, 'sha256': digest.hexdigest(),
            'duration_seconds': duration,
            'codec': streams[0].get('codec_name') if streams else None,
            'sample_rate': streams[0].get('sample_rate') if streams else None,
            'title': title, 'artists': artists,
            'album': tags.get('\xa9alb', []), 'cover_embedded': bool(tags.get('covr')),
            'cover_bytes': len(cover_bytes), 'cover_format': cover_format,
            'cover_header_hex': cover_bytes[:16].hex(),
            'lyrics_checked': False,
        }
        report['test_passed'] = all(report['checks'].values())
        report['audio_download_passed'] = all(
            value for name, value in report['checks'].items()
            if name not in {'cover_embedded', 'cover_valid'}
        )
        report['spotify_end_to_end_passed'] = (
            report['test_passed'] and report['spotify_metadata_passed']
        )
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        (report_dir / 'report.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8',
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['test_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
