"""Music Library Manager — Windows desktop front-end for tiddl and spotDL.

The two command-line applications must be installed separately. This front-end
never decrypts protected streams, invents lossless provenance, or stores logins.
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QProcess, QProcessEnvironment, QSettings, Qt, QUrl, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget, QHeaderView, QScrollArea, QAbstractItemView,
)

from lidarr_api import LidarrClient, LidarrError
from prowlarr_api import ProwlarrClient, ProwlarrError, validate_server_url
from qbit_api import QBittorrentClient, QBittorrentError
from dashboard_api import collect_dashboard, summarize, missing_albums, qbit_metrics
from playlist_api import identify_media_url, is_playlist_link, can_submit_url
from jellyfin_api import JellyfinClient, JellyfinError, link_jellyfin_songs
from playlist_sync import SyncStore, MODES, sync_playlist, request_new_albums, export_missing_csv, playlist_key
from flac_matcher import (MatchError, export_spotify_playlist, scan_flacs,
                          apply_local_matches, lidarr_album_matches,
                          find_remote_album_candidates, request_missing_albums, generate_m3u, export_mobile_package)

APP = 'Music Library Manager'
APP_ID = 'MusicLibraryManager'
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
VALID_TYPES = {'track', 'album', 'playlist', 'artist', 'mix'}


def identify_url(raw: str) -> tuple[str, str]:
    """Compatibility wrapper for existing download queue callers."""
    return identify_media_url(raw)


def toml_q(path: Path | str) -> str:
    # JSON quoted strings are compatible TOML basic strings.
    return json.dumps(str(path), ensure_ascii=False)


def app_data() -> Path:
    base = Path(os.environ.get('APPDATA', str(Path.home() / '.config')))
    path = base / APP_ID
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_tiddl_config(config_dir: Path, music_dir: Path, quality: str, covers: bool) -> None:
    """Write an isolated, app-specific tiddl config, not the user's existing config."""
    config_dir.mkdir(parents=True, exist_ok=True)
    music_dir.mkdir(parents=True, exist_ok=True)
    val = 'true' if covers else 'false'
    cfg = f'''# Configuração gerenciada pelo Music Library Manager.
# Login e tokens são gerenciados pelo próprio tiddl neste diretório.
[templates]
default = "{{album.artist}}/{{album.title}}/{{item.number:02d}} - {{item.title}}"
track = "{{album.artist}}/{{album.title}}/{{item.number:02d}} - {{item.title}}"
album = "{{album.artist}}/{{album.title}}/{{item.number:02d}} - {{item.title}}"
playlist = "{{playlist.title}}/{{playlist.index}} - {{item.artist}} - {{item.title}}"
mix = "Mixes/{{mix_id}}/{{item.artist}} - {{item.title}}"

[download]
track_quality = "{quality}"
video_quality = "fhd"
skip_existing = true
threads_count = 3
download_path = {toml_q(music_dir)}
scan_path = {toml_q(music_dir)}
singles_filter = "none"
videos_filter = "none"

[metadata]
enable = true
lyrics = false
cover = {val}

[cover]
save = {val}
size = 1280
allowed = ["album", "playlist"]

[cover.templates]
album = "{{album.artist}}/{{album.title}}/cover"
playlist = "{{playlist.title}}/cover"

[m3u]
save = true
allowed = ["album", "playlist", "mix"]

[m3u.templates]
album = "playlists/{{album.artist}} - {{album.title}}"
playlist = "playlists/{{playlist.title}}"
mix = "playlists/Mix-{{mix_id}}"
'''
    # Write atomically. No third-party config or credentials are overwritten.
    staging = config_dir / 'config.toml.tmp'
    staging.write_text(cfg, encoding='utf-8')
    staging.replace(config_dir / 'config.toml')


def executable(name: str) -> str | None:
    """Prefer the executable next to the active Python, including .venv/Scripts."""
    py_dir = Path(sys.executable).resolve().parent
    possible_dirs = [py_dir, py_dir / '.venv' / 'Scripts',
                     py_dir.parent / '.venv' / 'Scripts']
    for root in possible_dirs:
        for filename in [f'{name}.exe', name]:
            candidate = root / filename
            if candidate.exists():
                return str(candidate)
    return shutil.which(name)


def flac_audit(filepath: Path) -> tuple[str, str, str, str]:
    """Return display title, format, actual signal parameters and cover flag."""
    if filepath.suffix.lower() == '.flac':
        from mutagen.flac import FLAC
        data = FLAC(filepath)
        title = (data.get('title') or [filepath.stem])[0]
        return str(title), 'FLAC', f'{data.info.bits_per_sample} bits / {data.info.sample_rate / 1000:g} kHz', ('Sim' if data.pictures else 'Não')
    if filepath.suffix.lower() in {'.m4a', '.mp3', '.opus', '.ogg'}:
        from mutagen import File
        data = File(filepath)
        codec = filepath.suffix[1:].upper()
        rate = getattr(getattr(data, 'info', None), 'bitrate', 0)
        quality = f'~{round(rate / 1000)} kbps (não verificado lossless)' if rate else 'Qualidade não verificada'
        cover = 'Não verificada'
        if data and data.tags:
            title = data.tags.get('©nam') if codec == 'M4A' else None
            if isinstance(title, list) and title:
                title = str(title[0])
            else:
                title = filepath.stem
            cover = 'Sim' if ('covr' in data.tags or any(str(k).startswith('APIC') for k in data.tags.keys())) else 'Não'
        else:
            title = filepath.stem
        return str(title), codec, quality, cover
    raise ValueError('Extensão não compatível')


QSS = '''
QWidget { background: #0c1320; color: #e5eaf4; font-family: "Segoe UI"; font-size: 12px; }
QMainWindow { background: #0c1320; }
QFrame#sidebar { background: #111d2e; border-right: 1px solid #273449; }
QFrame#card { background: #131f31; border: 1px solid #25354d; border-radius: 13px; }
QLabel#headline { font-size: 24px; font-weight: 800; color: #f5f8fd; }
QLabel#sub { font-size: 12px; color: #9fb0c8; }
QLabel#pill { background: #123b3f; border: 1px solid #287975; border-radius: 9px; padding: 4px 8px; color: #7ce1ce; }
QPushButton { border-radius: 9px; border: 1px solid #32425c; background: #1a2a40; padding: 10px 15px; font-weight: 600; }
QPushButton:hover { background: #243955; }
QPushButton:pressed { background: #2d496b; }
QPushButton:disabled { color: #67758a; background: #182132; }
QPushButton#primary { background: #0caaa1; border: 1px solid #0caaa1; color: #051b24; }
QPushButton#primary:hover { background: #35c8bb; }
QPushButton#side { text-align: left; border: none; background: transparent; padding: 14px; }
QPushButton#side:checked { background: #1d4355; color: #80dfd5; }
QLabel#statvalue { color:#a6efe5; font-size: 24px; font-weight: 800; }
QLabel#statlabel { color:#8da2bf; font-size:11px; }
QScrollArea { border:none; background:transparent; }
QTableWidget::item:selected { background:#1a5867; }
QComboBox QAbstractItemView { background:#182b3e; }
QLineEdit, QPlainTextEdit, QComboBox { background: #101b2b; border: 1px solid #354661; border-radius: 8px; padding: 9px; selection-background-color: #2a918f; }
QPlainTextEdit { font-family: 'Consolas'; font-size: 11px; }
QTableWidget { background: #0f1b2b; alternate-background-color: #152439; border: 1px solid #2e3e55; gridline-color: #1c2b40; }
QHeaderView::section { background: #1a2b42; padding: 9px; border: none; border-bottom: 1px solid #36435b; font-weight: 700; }
QProgressBar { background: #26354b; border: 0; border-radius: 5px; height: 9px; }
QProgressBar::chunk { background: #23b9ab; border-radius: 5px; }
QCheckBox { spacing: 9px; }
'''


class LidarrWorker(QThread):
    """Run Lidarr HTTP requests off the main UI thread."""
    done = Signal(object, object)  # result, exception

    def __init__(self, func, parent=None):
        super().__init__(parent)
        self.func = func

    def run(self):
        try:
            self.done.emit(self.func(), None)
        except Exception as exc:
            self.done.emit(None, str(exc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Music Library Manager v0.6 — Sincronização inteligente • FLAC + Jellyfin + Lidarr + Prowlarr + qBittorrent')
        self.resize(1190, 800)
        self.setMinimumSize(900, 660)
        self.settings = QSettings('LocalDesktop', APP_ID)
        self.base = Path(self.settings.value('output', str(Path.home() / 'Music' / 'Music Library Manager')))
        self.queue: list[tuple[str, str, int]] = []
        self.lidarr_artists = []
        self.lidarr_albums = []
        self.lidarr_queue = []
        self.lidarr_import_lists = []
        self.lidarr_threads = set()  # Inclui também os workers Prowlarr/qBittorrent.
        self.qbit_rows = []
        self.prowlarr_rows = []
        self.active: QProcess | None = None
        self.shutting_down = False
        self.scan_rows: list[tuple[str, str, str, str, str]] = []
        self.dashboard_busy = False
        self.dashboard_refresh_job = None
        self.flac_songs = []
        self.sync_store = SyncStore(app_data() / 'playlist_sync.sqlite3')
        self.sync_busy = False
        self.sync_auto_queue = []
        self.flac_catalog_albums = {}
        self.flac_busy = False
        self.flac_metadata_url = ''
        self.jellyfin_items = []
        self.jellyfin_busy = False
        self._build()
        self._refresh_deps()
        self.dashboard_timer = QTimer(self)
        self.dashboard_timer.timeout.connect(self.refresh_dashboard)
        self.dashboard_interval.setCurrentIndex(max(0, self.dashboard_interval.findData(int(self.settings.value('dashboardInterval', 60)))))
        self._set_dashboard_interval()
        QTimer.singleShot(250, self.refresh_dashboard)
        self.sync_timer = QTimer(self)
        self.sync_timer.timeout.connect(self.sync_auto_tick)
        self.sync_interval.setCurrentIndex(max(0,self.sync_interval.findData(int(self.settings.value('syncMinutes',0)))))
        self.set_sync_interval()
        QTimer.singleShot(500,self.refresh_sync_list)

    def _build(self):
        body = QWidget()
        horizontal = QHBoxLayout(body)
        horizontal.setContentsMargins(0, 0, 0, 0)
        horizontal.setSpacing(0)
        self.setCentralWidget(body)

        aside = QFrame()
        aside.setObjectName('sidebar')
        aside.setFixedWidth(224)
        aside_layout = QVBoxLayout(aside)
        aside_layout.setContentsMargins(18, 28, 18, 20)
        aside_layout.setSpacing(12)
        brand = QLabel('♫  MUSIC LIBRARY')
        brand.setStyleSheet('font-size:17px; font-weight:900; color:#a4f5e7;')
        aside_layout.addWidget(brand)
        aside_layout.addWidget(QLabel('Lidarr • Prowlarr • qBittorrent'))
        aside_layout.addSpacing(16)
        self.nav = []
        for i, (title, symbol) in enumerate([('Visão geral', '⌂'), ('Downloads', '⇩'), ('Lidarr', '▦'), ('Prowlarr', '◎'), ('qBittorrent', '⇣'), ('Biblioteca local', '♫'), ('Configurações', '⚙'), ('Busca FLAC', '◇'), ('Jellyfin', '◉'), ('Sincronização', '↻')]):
            btn = QPushButton(f'{symbol}    {title}')
            btn.setObjectName('side')
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked=False, index=i: self._switch(index))
            self.nav.append(btn)
            aside_layout.addWidget(btn)
        aside_layout.addStretch()
        disclaimer = QLabel('Gerencie sua coleção em um só lugar.\n\nLidarr não hospeda músicas. Utilize fontes autorizadas.')
        disclaimer.setWordWrap(True)
        disclaimer.setObjectName('sub')
        aside_layout.addWidget(disclaimer)
        horizontal.addWidget(aside)

        right = QWidget()
        v = QVBoxLayout(right)
        v.setContentsMargins(25, 24, 25, 20)
        v.setSpacing(17)
        top = QHBoxLayout()
        title = QLabel('Music Library Manager')
        title.setObjectName('headline')
        top.addWidget(title)
        top.addStretch()
        self.dep = QLabel('Verificando ferramentas...')
        self.dep.setObjectName('pill')
        top.addWidget(self.dep)
        v.addLayout(top)
        self.stack = QStackedWidget()
        v.addWidget(self.stack, stretch=1)
        horizontal.addWidget(right, stretch=1)
        self._make_dashboard()
        self._make_downloads()
        self._make_lidarr()
        self._make_prowlarr()
        self._make_qbit()
        self._make_library()
        self._make_settings()
        self._make_flac_finder()
        self._make_jellyfin()
        self._make_sync()
        self._switch(0)

    @staticmethod
    def _card(layout: QVBoxLayout):
        card = QFrame()
        card.setObjectName('card')
        inside = QVBoxLayout(card)
        inside.setContentsMargins(16, 15, 16, 15)
        inside.setSpacing(10)
        layout.addWidget(card)
        return inside

    def _make_dashboard(self):
        """One link, live service health and useful operational actions."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 8)
        layout.setSpacing(14)
        top = QHBoxLayout()
        title = QLabel('Central de controle')
        title.setStyleSheet('font-size:19px;font-weight:800')
        top.addWidget(title)
        top.addStretch()
        self.dashboard_updated = QLabel('Ainda não atualizado')
        self.dashboard_updated.setObjectName('sub')
        top.addWidget(self.dashboard_updated)
        btn = QPushButton('↻ Atualizar agora')
        btn.setObjectName('primary')
        btn.clicked.connect(self.refresh_dashboard)
        top.addWidget(btn)
        layout.addLayout(top)

        card = self._card(layout)
        card.addWidget(QLabel('PLAYLIST PELO LINK • REUTILIZAR FLACs EXISTENTES'))
        row = QHBoxLayout()
        self.quick_playlist = QLineEdit()
        self.quick_playlist.setPlaceholderText('Cole https://open.spotify.com/playlist/... ou https://tidal.com/playlist/...')
        self.quick_playlist.returnPressed.connect(self.find_flac_from_quick)
        row.addWidget(self.quick_playlist, stretch=1)
        paste = QPushButton('Preparar playlist FLAC')
        paste.setObjectName('primary')
        paste.clicked.connect(self.find_flac_from_quick)
        row.addWidget(paste)
        flac_btn = QPushButton('Download direto / tiddl')
        flac_btn.clicked.connect(self.download_quick_playlist)
        row.addWidget(flac_btn)
        add_sync = QPushButton('↻ Cadastrar sincronização automática')
        add_sync.clicked.connect(self.sync_from_dashboard)
        card.addWidget(add_sync)
        card.addLayout(row)
        label = QLabel('Para playlists Spotify, use Procurar FLAC real: lê apenas os metadados e busca correspondências sem baixar áudio comprimido.  TIDAL → tiddl (FLAC conforme acesso).  O Lidarr realiza buscas por ÁLBUM, não por faixa.')
        label.setWordWrap(True)
        label.setObjectName('sub')
        card.addWidget(label)
        self.dashboard_direct_status = QLabel('Fila direta: nenhuma playlist ativa')
        self.dashboard_direct_status.setObjectName('sub')
        card.addWidget(self.dashboard_direct_status)

        services = QHBoxLayout()
        self.dashboard_services = {}
        for key, name in [('lidarr','LIDARR'), ('prowlarr','PROWLARR'), ('qbit','qBITTORRENT')]:
            frame = QFrame()
            frame.setObjectName('card')
            inner = QVBoxLayout(frame)
            inner.addWidget(QLabel(name))
            state = QLabel('Não verificado')
            state.setObjectName('statvalue')
            state.setStyleSheet('font-size:17px; font-weight:750; color:#98a5be')
            inner.addWidget(state)
            detail = QLabel('Configure na aba Configurações')
            detail.setObjectName('sub')
            detail.setWordWrap(True)
            inner.addWidget(detail)
            services.addWidget(frame, stretch=1)
            self.dashboard_services[key] = (state, detail)
        layout.addLayout(services)

        cards = QHBoxLayout()
        self.dashboard_stats = {}
        for key, title in [('artists','Artistas'),('albums','Álbuns'),('missing','Álbuns sem faixas'),
                           ('lidarr_queue','Fila Lidarr'),('indexers','Indexadores'),('qbit_active','Transferências')]:
            panel = QFrame()
            panel.setObjectName('card')
            inner = QVBoxLayout(panel)
            value = QLabel('—')
            value.setObjectName('statvalue')
            inner.addWidget(value)
            label = QLabel(title)
            label.setWordWrap(True)
            label.setObjectName('statlabel')
            inner.addWidget(label)
            cards.addWidget(panel, stretch=1)
            self.dashboard_stats[key] = value
        layout.addLayout(cards)

        controls = QHBoxLayout()
        controls.addWidget(QLabel('Atualização automática'))
        self.dashboard_interval = QComboBox()
        for label, secs in [('Desativada',0),('30 segundos',30),('60 segundos',60),('2 minutos',120)]:
            self.dashboard_interval.addItem(label, secs)
        self.dashboard_interval.currentIndexChanged.connect(self._set_dashboard_interval)
        controls.addWidget(self.dashboard_interval)
        controls.addStretch()
        for title, index in [('Downloads',1),('Lidarr',2),('Prowlarr',3),('qBittorrent',4),('Biblioteca',5),('Configurações',6),('Busca FLAC',7),('Jellyfin',8),('Sincronização',9)]:
            action = QPushButton(title)
            action.clicked.connect(lambda _=False, idx=index: self._switch(idx))
            controls.addWidget(action)
        layout.addLayout(controls)

        header = QHBoxLayout()
        header.addWidget(QLabel('TRANSFERÊNCIAS EM ANDAMENTO'))
        header.addStretch()
        self.dashboard_speed = QLabel('—')
        self.dashboard_speed.setObjectName('sub')
        header.addWidget(self.dashboard_speed)
        layout.addLayout(header)
        self.dashboard_transfers = QTableWidget(0,4)
        self.dashboard_transfers.setHorizontalHeaderLabels(['Nome','Progresso','Velocidade','Estado'])
        self.dashboard_transfers.setAlternatingRowColors(True)
        self.dashboard_transfers.setEditTriggers(QTableWidget.NoEditTriggers)
        self.dashboard_transfers.verticalHeader().setVisible(False)
        self.dashboard_transfers.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
        self.dashboard_transfers.setMinimumHeight(165)
        layout.addWidget(self.dashboard_transfers)

        layout.addWidget(QLabel('ÁLBUNS MONITORADOS SEM FAIXAS IMPORTADAS'))
        self.dashboard_missing = QTableWidget(0,3)
        self.dashboard_missing.setHorizontalHeaderLabels(['Álbum','Artista','Situação'])
        self.dashboard_missing.setAlternatingRowColors(True)
        self.dashboard_missing.setEditTriggers(QTableWidget.NoEditTriggers)
        self.dashboard_missing.verticalHeader().setVisible(False)
        self.dashboard_missing.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
        self.dashboard_missing.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch)
        self.dashboard_missing.setMinimumHeight(165)
        layout.addWidget(self.dashboard_missing)
        foot = QLabel('"Sem faixas" só é exibido quando o Lidarr fornece estatísticas suficientes. Não equivale a todas as músicas faltantes.')
        foot.setObjectName('sub')
        foot.setWordWrap(True)
        layout.addWidget(foot)
        scroll.setWidget(page)
        self.stack.addWidget(scroll)

    def _update_playlist_status(self):
        if not hasattr(self,'dashboard_direct_status'):
            return
        running = ('1 em execução' if self.active is not None else 'nenhum em execução')
        self.dashboard_direct_status.setText(f'Fila direta: {running} • {len(self.queue)} aguardando')

    def _set_dashboard_interval(self, *_):
        # During construction dashboard_timer is created after widgets; keep configuration safe.
        if not hasattr(self, 'dashboard_timer'):
            return
        seconds = self.dashboard_interval.currentData()
        seconds = int(seconds) if seconds is not None else 60
        self.settings.setValue('dashboardInterval', seconds)
        self.dashboard_timer.stop()
        if seconds > 0:
            self.dashboard_timer.start(seconds * 1000)

    def refresh_dashboard(self):
        """Never query HTTP from the GUI thread or overlap refreshes."""
        if self.dashboard_busy or self.shutting_down:
            return
        clients = {}
        for name, getter in [('lidarr',self._lidarr_client),('prowlarr',self._prowlarr_client),('qbit',self._qbit_client)]:
            try:
                clients[name] = getter()
            except Exception:
                clients[name] = None
        self.dashboard_busy = True
        self.dashboard_updated.setText('Consultando serviços...')
        job = LidarrWorker(lambda: collect_dashboard(clients), self)
        self.lidarr_threads.add(job)
        self.dashboard_refresh_job = job
        def completed(data, error):
            self.dashboard_busy = False
            if error:
                self.dashboard_updated.setText('Erro: ' + str(error))
            else:
                self._render_dashboard(data)
        def cleanup():
            self.lidarr_threads.discard(job)
            self.dashboard_refresh_job = None
            job.deleteLater()
        job.done.connect(completed)
        job.finished.connect(cleanup)
        job.start()

    def _render_dashboard(self, data):
        stats = summarize(data)
        self.dashboard_updated.setText('Atualizado em ' + str(data.get('updated_at','—')))
        for key, label in self.dashboard_stats.items():
            value = stats.get(key)
            label.setText('—' if value is None else str(value))
        for key, (state, detail) in self.dashboard_services.items():
            item = data.get(key) or {}
            if item.get('online'):
                state.setText('● Online')
                state.setStyleSheet('font-size:17px;font-weight:750;color:#79e2b4')
                warnings = item.get('warnings') or []
                detail.setText(('v' + str(item.get('version') or '?')) + (' • dados parciais' if warnings else ' • Conectado'))
                detail.setToolTip('\n'.join(warnings))
            else:
                state.setText('● Offline' if item.get('configured') else '● Configurar')
                state.setStyleSheet('font-size:17px;font-weight:750;color:#d9a583')
                detail.setText(str(item.get('error') or 'Sem conexão'))
                detail.setToolTip(str(item.get('error') or ''))
        torrents = ((data.get('qbit') or {}).get('torrents') or [])
        summary = qbit_metrics(torrents)
        self.dashboard_speed.setText('Recebendo: ' + self._human_rate(summary['down_speed']))
        ongoing = [t for t in summary['torrents'] if float(t.get('progress') or 0) < 1.0]
        self.dashboard_transfers.setRowCount(0)
        for item in ongoing[:100]:
            row = self.dashboard_transfers.rowCount()
            self.dashboard_transfers.insertRow(row)
            for j, val in enumerate((str(item.get('name') or '—'), f"{float(item.get('progress') or 0)*100:.1f}%",
                self._human_rate(item.get('dlspeed') or 0), str(item.get('state') or '—'))):
                self.dashboard_transfers.setItem(row,j,QTableWidgetItem(val))
        lidarr = data.get('lidarr') or {}
        artist_names = {a.get('id'): a.get('artistName','') for a in (lidarr.get('artists') or [])}
        self.dashboard_missing.setRowCount(0)
        for album in missing_albums(lidarr.get('albums') or [])[:100]:
            row = self.dashboard_missing.rowCount()
            self.dashboard_missing.insertRow(row)
            name = (album.get('artist') or {}).get('artistName') or artist_names.get(album.get('artistId'),'—')
            for j,val in enumerate((str(album.get('title') or '—'),str(name),'0 faixas importadas')):
                self.dashboard_missing.setItem(row,j,QTableWidgetItem(val))

    @staticmethod
    def _human_rate(value):
        try:
            n = max(0,float(value))
        except (ValueError,TypeError):
            n = 0
        for unit in ('B/s','KiB/s','MiB/s','GiB/s'):
            if n < 1024 or unit=='GiB/s':
                return f'{n:.1f} {unit}'
            n /= 1024

    def download_quick_playlist(self):
        url = self.quick_playlist.text().strip()
        service, reason = identify_url(url)
        if not service:
            QMessageBox.warning(self, 'Link inválido', reason)
            return
        if not is_playlist_link(url):
            QMessageBox.information(self, 'Playlist', 'Para esta ação, cole o link de uma playlist.\n\nLinks de faixas e álbuns também são aceitos na aba Downloads.')
            return
        self.links.setPlainText(url)
        self._switch(1)
        self.add_jobs()
        if not self.links.toPlainText().strip():
            self.quick_playlist.clear()

    def find_flac_from_quick(self):
        url = self.quick_playlist.text().strip()
        service, explanation = identify_url(url)
        if not service or not is_playlist_link(url):
            QMessageBox.warning(self, APP, explanation if not service else 'Informe um link de playlist válido.')
            return
        self.flac_url.setText(url)
        self._switch(7)
        if service == 'Spotify':
            self.analyze_flac_playlist()
        else:
            self.flac_state.setText('Para playlists TIDAL, a fonte FLAC integrada é o tiddl. Use Downloads com qualidade HIGH/MAX e, em seguida, Reverificar FLAC. O catálogo por faixa do TIDAL ainda não pode ser consultado por este analisador.')

    def _make_downloads(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(13)
        lead = QLabel('Cole um link do TIDAL ou Spotify por linha. O programa detecta o serviço.')
        lead.setObjectName('sub')
        layout.addWidget(lead)
        c = self._card(layout)
        c.addWidget(QLabel('URLs de faixas, álbuns ou playlists'))
        self.links = QPlainTextEdit()
        self.links.setPlaceholderText('https://tidal.com/playlist/...\nhttps://open.spotify.com/playlist/...')
        self.links.setFixedHeight(82)
        c.addWidget(self.links)
        controls = QHBoxLayout()
        controls.addWidget(QLabel('Qualidade TIDAL:'))
        self.quality = QComboBox()
        self.quality.addItem('Tentar FLAC Hi-Res: até 24-bit / 192 kHz (MAX; pode falhar)', 'max')
        self.quality.addItem('FLAC CD: 16-bit / 44,1 kHz (HIGH; mais compatível)', 'high')
        self.quality.setCurrentIndex(int(self.settings.value('qualityIndex', 1)))
        controls.addWidget(self.quality, stretch=1)
        self.download_btn = QPushButton('Adicionar à fila')
        self.download_btn.setObjectName('primary')
        self.download_btn.clicked.connect(self.add_jobs)
        controls.addWidget(self.download_btn)
        c.addLayout(controls)
        hint = QLabel('Spotify/spotDL: metadados e capas, mas áudio normalmente obtido do YouTube (M4A com perdas). Não é FLAC real.')
        hint.setObjectName('sub')
        hint.setWordWrap(True)
        c.addWidget(hint)
        format_row = QHBoxLayout()
        format_row.addWidget(QLabel('Critério:'))
        self.download_policy = QComboBox()
        self.download_policy.addItem('Automático: FLAC TIDAL / M4A Spotify', 'auto')
        self.download_policy.addItem('Somente fontes FLAC: aceitar apenas links TIDAL', 'flac')
        self.download_policy.setCurrentIndex(max(0,self.download_policy.findData(str(self.settings.value('downloadPolicy','auto')))))
        self.download_policy.currentIndexChanged.connect(lambda: self.settings.setValue('downloadPolicy',self.download_policy.currentData()))
        format_row.addWidget(self.download_policy,stretch=1)
        c.addLayout(format_row)

        q = self._card(layout)
        header = QHBoxLayout()
        header.addWidget(QLabel('Fila de downloads'))
        header.addStretch()
        self.cancel_btn = QPushButton('Cancelar tarefa')
        self.cancel_btn.clicked.connect(self.cancel)
        header.addWidget(self.cancel_btn)
        q.addLayout(header)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['Origem', 'Link', 'Status'])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        q.addWidget(self.table, stretch=1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        q.addWidget(self.progress)
        q.addWidget(QLabel('Registro das operações'))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(800)
        self.log.setFixedHeight(95)
        q.addWidget(self.log)
        self.stack.addWidget(page)

    def _make_lidarr(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(11)
        top = QHBoxLayout()
        title = QLabel('Lidarr  •  Central de biblioteca')
        title.setStyleSheet('font-size:17px; font-weight:700;')
        top.addWidget(title)
        top.addStretch()
        self.lidarr_state = QLabel('Desconectado')
        self.lidarr_state.setObjectName('pill')
        top.addWidget(self.lidarr_state)
        refresh = QPushButton('↻  Atualizar')
        refresh.setObjectName('primary')
        refresh.clicked.connect(self.load_lidarr)
        top.addWidget(refresh)
        layout.addLayout(top)

        metrics = QHBoxLayout()
        self.lidarr_metrics = {}
        for ident, label in [('artists', 'Artistas'), ('albums', 'Álbuns'), ('wanted', 'Álbuns sem arquivo'), ('queue', 'Fila Lidarr')]:
            tile = QFrame()
            tile.setObjectName('card')
            v = QVBoxLayout(tile)
            count = QLabel('—')
            count.setObjectName('statvalue')
            caption = QLabel(label)
            caption.setObjectName('statlabel')
            v.addWidget(count)
            v.addWidget(caption)
            metrics.addWidget(tile)
            self.lidarr_metrics[ident] = count
        layout.addLayout(metrics)

        actions = QHBoxLayout()
        self.filter_artists = QLineEdit()
        self.filter_artists.setPlaceholderText('Filtrar artistas ou álbuns...')
        self.filter_artists.textChanged.connect(self._lidarr_apply_filter)
        actions.addWidget(self.filter_artists, 1)
        open_lidarr = QPushButton('Abrir Lidarr')
        open_lidarr.clicked.connect(self.open_lidarr)
        actions.addWidget(open_lidarr)
        open_lists = QPushButton('Listas / Spotify ↗')
        open_lists.clicked.connect(self.open_lidarr_lists)
        actions.addWidget(open_lists)
        layout.addLayout(actions)

        notice = QLabel('O Lidarr gerencia álbuns e downloads de fontes configuradas no seu servidor. '
                        'Playlists Spotify são configuradas em Settings → Import Lists no Lidarr; '
                        'não são convertidas automaticamente em arquivos FLAC.')
        notice.setWordWrap(True)
        notice.setObjectName('sub')
        layout.addWidget(notice)

        from PySide6.QtWidgets import QTabWidget
        tabs = QTabWidget()
        tabs.setStyleSheet('''QTabWidget::pane{border:1px solid #32425c;border-radius:7px;}
            QTabBar::tab{background:#142339;padding:10px 17px;margin-right:4px;border-radius:5px;}
            QTabBar::tab:selected{background:#1a5665;color:#a5f5ee;}''')

        artist_page = QWidget()
        artist_layout = QVBoxLayout(artist_page)
        help_text = QLabel('Selecione um artista para ver seus álbuns. Selecione um álbum e clique em Buscar no Lidarr.')
        help_text.setWordWrap(True)
        help_text.setObjectName('sub')
        artist_layout.addWidget(help_text)
        self.artists_table = QTableWidget(0, 3)
        self._style_table(self.artists_table, ['Artista', 'Álbuns', 'Caminho'])
        self.artists_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.artists_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.artists_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.artists_table.itemSelectionChanged.connect(self._lidarr_apply_filter)
        artist_layout.addWidget(self.artists_table, 1)
        artist_layout.addWidget(QLabel('Álbuns do artista selecionado'))
        self.albums_table = QTableWidget(0, 4)
        self._style_table(self.albums_table, ['Álbum', 'Artista', 'Ano', 'Monitorado'])
        self.albums_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.albums_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        artist_layout.addWidget(self.albums_table, 1)
        album_actions = QHBoxLayout()
        search = QPushButton('⌕  Buscar álbum selecionado')
        search.setObjectName('primary')
        search.clicked.connect(self.search_lidarr_album)
        album_actions.addWidget(search)
        album_actions.addStretch()
        artist_layout.addLayout(album_actions)
        tabs.addTab(artist_page, 'Artistas e álbuns')

        queue_page = QWidget()
        queue_layout = QVBoxLayout(queue_page)
        queue_info = QLabel('Estado informado diretamente pela API do Lidarr. A fila só inclui itens em processamento no servidor.')
        queue_info.setWordWrap(True)
        queue_info.setObjectName('sub')
        queue_layout.addWidget(queue_info)
        self.lidarr_queue_table = QTableWidget(0, 4)
        self._style_table(self.lidarr_queue_table, ['Título', 'Qualidade', 'Progresso', 'Status'])
        self.lidarr_queue_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        queue_layout.addWidget(self.lidarr_queue_table, 1)
        tabs.addTab(queue_page, 'Fila de transferências')

        quality_page = QWidget()
        quality_layout = QVBoxLayout(quality_page)
        self.profile_label = QLabel('Conecte ao Lidarr para consultar os perfis de qualidade configurados.')
        self.profile_label.setWordWrap(True)
        self.profile_label.setObjectName('sub')
        quality_layout.addWidget(self.profile_label)
        self.profiles_table = QTableWidget(0, 2)
        self._style_table(self.profiles_table, ['Perfil', 'Ação de atualização'])
        self.profiles_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        quality_layout.addWidget(self.profiles_table)
        quality_layout.addWidget(QLabel('Para priorizar FLAC, configure um perfil de qualidade adequado diretamente no Lidarr.'))
        tabs.addTab(quality_page, 'Perfis de qualidade')

        lists_page = QWidget()
        lists_layout = QVBoxLayout(lists_page)
        lists_notice = QLabel('Veja as listas já cadastradas no Lidarr (inclusive Spotify, quando disponível). '
                              'Sincronizar uma lista solicita a importação de metadados/álbuns; '
                              'as músicas dependem das fontes e dos clientes configurados.')
        lists_notice.setWordWrap(True)
        lists_notice.setObjectName('sub')
        lists_layout.addWidget(lists_notice)
        self.import_lists_table = QTableWidget(0, 3)
        self._style_table(self.import_lists_table, ['Lista', 'Tipo', 'Ativa'])
        self.import_lists_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        lists_layout.addWidget(self.import_lists_table, 1)
        list_actions = QHBoxLayout()
        sync = QPushButton('↻  Sincronizar lista selecionada')
        sync.setObjectName('primary')
        sync.clicked.connect(self.sync_lidarr_list)
        list_actions.addWidget(sync)
        list_actions.addStretch()
        lists_layout.addLayout(list_actions)
        tabs.addTab(lists_page, 'Playlists / listas')
        layout.addWidget(tabs, 1)
        self.stack.addWidget(page)

    @staticmethod
    def _style_table(table: QTableWidget, headers):
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        for i in range(len(headers)):
            table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeToContents)

    def _lidarr_client(self) -> LidarrClient:
        from keyring import get_password
        url = self.lidarr_url.text().strip()
        key = self.lidarr_key.text().strip()
        if not key:
            try:
                key = get_password(APP_ID, 'lidarr-api-key') or ''
            except Exception:
                key = ''
        return LidarrClient(url, key)

    def _thread_lidarr(self, callback, on_success, service='Lidarr'):
        """Keep threads owned by the window until they finish."""
        job = LidarrWorker(callback, self)
        self.lidarr_threads.add(job)

        def finished(result, error):
            if error:
                if service == 'Lidarr':
                    self.lidarr_state.setText('Falha na conexão')
                elif service == 'Prowlarr':
                    self.prowlarr_state.setText('Falha na conexão')
                elif service == 'qBittorrent':
                    self.qbit_state.setText('Falha na conexão')
                QMessageBox.warning(self, service, str(error))
            else:
                on_success(result)
        def cleanup():
            self.lidarr_threads.discard(job)
            job.deleteLater()

        job.done.connect(finished)
        job.finished.connect(cleanup)
        job.start()

    def load_lidarr(self):
        try:
            client = self._lidarr_client()
        except Exception as exc:
            QMessageBox.warning(self, 'Lidarr', str(exc))
            self._switch(6)
            return
        self.lidarr_state.setText('Conectando...')
        self._thread_lidarr(client.snapshot, self._lidarr_render)

    @staticmethod
    def _safe_int(value):
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0

    def _lidarr_render(self, data):
        status = data.get('status') or {}
        self.lidarr_state.setText('Conectado  •  v' + str(status.get('version', '?')))
        self.lidarr_artists = data.get('artists') or []
        self.lidarr_albums = data.get('albums') or []
        self.lidarr_queue = data.get('queue') or []
        self.lidarr_import_lists = data.get('lists') or []
        self.lidarr_metrics['artists'].setText(str(len(self.lidarr_artists)))
        self.lidarr_metrics['albums'].setText(str(len(self.lidarr_albums)))
        missing = sum(1 for a in self.lidarr_albums
                      if a.get('monitored') and 'trackFileCount' in (a.get('statistics') or {})
                      and (a.get('statistics') or {})['trackFileCount'] == 0)
        self.lidarr_metrics['wanted'].setText(str(missing))
        self.lidarr_metrics['queue'].setText(str(len(self.lidarr_queue)))
        self.profile_label.setText('Perfis configurados na instância do Lidarr:')
        self.profiles_table.setRowCount(0)
        for prof in (data.get('profiles') or []):
            r = self.profiles_table.rowCount()
            self.profiles_table.insertRow(r)
            self.profiles_table.setItem(r, 0, QTableWidgetItem(str(prof.get('name', '—'))))
            self.profiles_table.setItem(r, 1, QTableWidgetItem('Ativo para novos itens' if prof.get('upgradeAllowed') else 'Sem atualização automática'))
        self.lidarr_queue_table.setRowCount(0)
        for task in self.lidarr_queue:
            album = task.get('album') or {}
            artist = task.get('artist') or {}
            left = task.get('sizeleft') or 0
            size = task.get('size') or 0
            progress = f'{100 * (1-left/size):.0f}%' if size else '—'
            data_row = [str(task.get('title') or album.get('title') or artist.get('artistName') or '—'),
                        str(((task.get('quality') or {}).get('quality') or {}).get('name', '—')),
                        progress, str(task.get('status') or '—')]
            r = self.lidarr_queue_table.rowCount()
            self.lidarr_queue_table.insertRow(r)
            for c, value in enumerate(data_row):
                self.lidarr_queue_table.setItem(r, c, QTableWidgetItem(value))
        self.import_lists_table.setRowCount(0)
        for record in self.lidarr_import_lists:
            r = self.import_lists_table.rowCount()
            self.import_lists_table.insertRow(r)
            cells = [str(record.get('name') or '—'), str(record.get('implementation') or '—'),
                     'Sim' if record.get('enable') else 'Não']
            for c, label in enumerate(cells):
                item = QTableWidgetItem(label)
                item.setData(Qt.UserRole, record.get('id'))
                self.import_lists_table.setItem(r, c, item)
        self._lidarr_apply_filter()

    def sync_lidarr_list(self):
        chosen = self.import_lists_table.selectedItems()
        if not chosen:
            QMessageBox.information(self, 'Lidarr', 'Selecione uma lista de importação.')
            return
        identifier = self._safe_int(chosen[0].data(Qt.UserRole))
        try:
            client = self._lidarr_client()
            self.lidarr_state.setText('Sincronizando lista...')
            self._thread_lidarr(lambda: client.sync_import_list(identifier),
                lambda _: (self.lidarr_state.setText('Sincronização solicitada'),
                           QMessageBox.information(self, 'Lidarr', 'Sincronização enviada ao Lidarr. A inclusão de álbuns pode não ser imediata.')))
        except Exception as exc:
            QMessageBox.warning(self, 'Lidarr', str(exc))

    def _lidarr_apply_filter(self, *_):
        needle = self.filter_artists.text().casefold().strip()
        selected = self.artists_table.selectedItems()
        selected_id = None
        if selected:
            selected_id = selected[0].data(Qt.UserRole)
        if self.sender() is not self.artists_table:
            # On text filter changes show all matches instead of restricting by prior selection.
            selected_id = None
        self.artists_table.blockSignals(True)
        if self.sender() is not self.artists_table:
            self.artists_table.clearSelection()
        self.artists_table.setRowCount(0)
        artist_names = {a.get('id'): str(a.get('artistName', '—')) for a in self.lidarr_artists}
        for artist in self.lidarr_artists:
            name = str(artist.get('artistName', '—'))
            if needle and needle not in name.casefold():
                continue
            r = self.artists_table.rowCount()
            self.artists_table.insertRow(r)
            cells = [name, str((artist.get('statistics') or {}).get('albumCount', '—')), str(artist.get('path') or '—')]
            for c, val in enumerate(cells):
                cell = QTableWidgetItem(val)
                cell.setData(Qt.UserRole, artist.get('id'))
                self.artists_table.setItem(r, c, cell)
        self.artists_table.blockSignals(False)
        self.albums_table.setRowCount(0)
        for album in self.lidarr_albums:
            artist = artist_names.get(album.get('artistId'), (album.get('artist') or {}).get('artistName', '—'))
            title = str(album.get('title', '—'))
            if selected_id is not None:
                if album.get('artistId') != selected_id:
                    continue
            elif needle and needle not in title.casefold() and needle not in artist.casefold():
                continue
            r = self.albums_table.rowCount()
            self.albums_table.insertRow(r)
            for c, val in enumerate([title, artist, str(album.get('releaseDate', '—'))[:4],
                                     'Sim' if album.get('monitored') else 'Não']):
                cell = QTableWidgetItem(val)
                cell.setData(Qt.UserRole, album.get('id'))
                self.albums_table.setItem(r, c, cell)

    def search_lidarr_album(self):
        selected = self.albums_table.selectedItems()
        if not selected:
            QMessageBox.information(self, 'Lidarr', 'Selecione um álbum na tabela.')
            return
        album_id = self._safe_int(selected[0].data(Qt.UserRole))
        title = selected[0].text()
        confirm = QMessageBox.question(self, 'Buscar álbum no Lidarr',
                      f'Solicitar ao Lidarr busca pelo álbum "{title}"?\n\n'
                      'O Lidarr usará somente as fontes e os clientes já configurados na sua instância.',
                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if confirm != QMessageBox.Yes:
            return
        try:
            client = self._lidarr_client()
            self.lidarr_state.setText('Solicitando busca...')
            self._thread_lidarr(lambda: client.search_album(album_id),
                               lambda _: (self.lidarr_state.setText('Busca solicitada'),
                                          QMessageBox.information(self, 'Lidarr', 'Busca enviada ao Lidarr. Clique em Atualizar para acompanhar.')))
        except Exception as exc:
            QMessageBox.warning(self, 'Lidarr', str(exc))

    def open_lidarr(self):
        url = self.lidarr_url.text().strip()
        try:
            # Validate URL without ever appending an API key to browser URLs.
            from urllib.parse import urlsplit
            p = urlsplit(url)
            if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
                raise ValueError('URL inválida do Lidarr.')
            QDesktopServices.openUrl(QUrl(url))
        except Exception as exc:
            QMessageBox.warning(self, 'Lidarr', str(exc))

    def open_lidarr_lists(self):
        # Do not assume stable settings route between Lidarr versions.
        self.open_lidarr()
        QMessageBox.information(self, 'Listas Spotify no Lidarr',
                'Na interface do Lidarr, acesse Settings → Import Lists para configurar a importação do Spotify. '
                'A disponibilidade depende da versão do Lidarr e da configuração da conta.')

    def save_lidarr(self):
        from keyring import set_password
        url = self.lidarr_url.text().strip()
        key = self.lidarr_key.text().strip()
        try:
            if key:
                LidarrClient(url, key)
                set_password(APP_ID, 'lidarr-api-key', key)
                self.lidarr_key.clear()
                self.lidarr_key.setPlaceholderText('API Key guardada no cofre de credenciais do sistema')
            else:
                # Allow saving just the base URL; checking connectivity validates the key.
                p = urlsplit(url)
                if p.scheme not in ('http', 'https') or not p.hostname:
                    raise ValueError('Informe a URL do Lidarr.')
            self.settings.setValue('lidarrUrl', url)
            self.lidarr_state.setText('Configuração salva')
            QMessageBox.information(self, 'Lidarr', 'Configuração salva. A API Key, quando fornecida, fica no gerenciador de credenciais do sistema.')
        except Exception as exc:
            QMessageBox.warning(self, 'Lidarr', 'Não foi possível salvar: ' + str(exc))

    def test_lidarr(self):
        try:
            client = self._lidarr_client()
            self.lidarr_state.setText('Testando conexão...')
            def show_status(data):
                self.lidarr_state.setText('Conectado')
                QMessageBox.information(self, 'Lidarr',
                    'Conexão realizada!\nVersão: ' + str(data.get('version', '?')))
            self._thread_lidarr(client.health, show_status)
        except Exception as exc:
            QMessageBox.warning(self, 'Lidarr', str(exc))

    # ---------------------- Prowlarr ----------------------
    def _prowlarr_client(self) -> ProwlarrClient:
        from keyring import get_password
        key = self.prowlarr_key.text().strip() or (get_password(APP_ID, 'prowlarr-api-key') or '')
        return ProwlarrClient(self.prowlarr_url.text().strip(), key)

    def _make_prowlarr(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        row = QHBoxLayout()
        title = QLabel('Prowlarr  •  Gerenciamento de indexadores')
        title.setStyleSheet('font-size:17px; font-weight:700;')
        row.addWidget(title)
        row.addStretch()
        self.prowlarr_state = QLabel('Desconectado')
        self.prowlarr_state.setObjectName('pill')
        row.addWidget(self.prowlarr_state)
        refresh = QPushButton('↻ Atualizar')
        refresh.setObjectName('primary')
        refresh.clicked.connect(self.load_prowlarr)
        row.addWidget(refresh)
        layout.addLayout(row)
        counts = QHBoxLayout()
        self.prowlarr_counts = {}
        for key, label in [('indexers', 'Indexadores'), ('apps', 'Aplicativos'), ('warnings', 'Avisos')]:
            tile = QFrame()
            tile.setObjectName('card')
            v = QVBoxLayout(tile)
            val = QLabel('—')
            val.setObjectName('statvalue')
            v.addWidget(val)
            caption = QLabel(label)
            caption.setObjectName('statlabel')
            v.addWidget(caption)
            counts.addWidget(tile)
            self.prowlarr_counts[key] = val
        layout.addLayout(counts)
        hint = QLabel('Configure fontes autorizadas no Prowlarr → Settings → Indexers. Em Settings → Apps, adicione o Lidarr e sincronize os indexadores. O Prowlarr não armazena músicas nem é um cliente BitTorrent.')
        hint.setWordWrap(True)
        hint.setObjectName('sub')
        layout.addWidget(hint)
        buttons = QHBoxLayout()
        sync = QPushButton('↻ Sincronizar indexadores com apps')
        sync.setObjectName('primary')
        sync.clicked.connect(self.sync_prowlarr)
        buttons.addWidget(sync)
        connect_lidarr = QPushButton('Vincular Lidarr')
        connect_lidarr.clicked.connect(self.connect_prowlarr_lidarr)
        buttons.addWidget(connect_lidarr)
        test = QPushButton('Abrir Prowlarr ↗')
        test.clicked.connect(lambda: self._open_service(self.prowlarr_url.text(), 'Prowlarr'))
        buttons.addWidget(test)
        layout.addLayout(buttons)
        from PySide6.QtWidgets import QTabWidget
        tabs = QTabWidget()
        self.prowlarr_indexers = QTableWidget(0, 4)
        self._style_table(self.prowlarr_indexers, ['Indexer', 'Protocolo', 'Habilitado', 'Saúde'])
        self.prowlarr_indexers.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        tabs.addTab(self.prowlarr_indexers, 'Indexadores')
        self.prowlarr_apps = QTableWidget(0, 3)
        self._style_table(self.prowlarr_apps, ['Aplicativo', 'Tipo', 'Sincronização'])
        self.prowlarr_apps.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        tabs.addTab(self.prowlarr_apps, 'Apps conectados')
        self.prowlarr_alerts = QTableWidget(0, 2)
        self._style_table(self.prowlarr_alerts, ['Origem', 'Aviso'])
        self.prowlarr_alerts.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        tabs.addTab(self.prowlarr_alerts, 'Alertas')
        layout.addWidget(tabs, 1)
        self.stack.addWidget(page)

    def load_prowlarr(self):
        try:
            client = self._prowlarr_client()
            self.prowlarr_state.setText('Conectando...')
            self._thread_lidarr(client.snapshot, self._render_prowlarr, 'Prowlarr')
        except Exception as exc:
            QMessageBox.warning(self, 'Prowlarr', str(exc))
            self._switch(6)

    def _render_prowlarr(self, data):
        status = data.get('status') or {}
        indexers = data.get('indexers') or []
        apps = data.get('applications') or []
        alerts = data.get('health') or []
        degraded = {x.get('indexerId'): x for x in (data.get('indexer_status') or [])}
        self.prowlarr_state.setText('Conectado  •  v' + str(status.get('version', '?')))
        self.prowlarr_counts['indexers'].setText(str(len(indexers)))
        self.prowlarr_counts['apps'].setText(str(len(apps)))
        self.prowlarr_counts['warnings'].setText(str(len(alerts)))
        self.prowlarr_indexers.setRowCount(0)
        for obj in indexers:
            record = degraded.get(obj.get('id')) or {}
            healthy = 'Com avisos' if record.get('disabledTill') or record.get('mostRecentFailure') else '—'
            vals = [str(obj.get('name') or '—'), str(obj.get('protocol') or '—'),
                    'Sim' if obj.get('enable') else 'Não', healthy]
            self._fill_row(self.prowlarr_indexers, vals)
        self.prowlarr_apps.setRowCount(0)
        for obj in apps:
            self._fill_row(self.prowlarr_apps,
                           [str(obj.get('name') or '—'), str(obj.get('implementation') or '—'),
                            str(obj.get('syncLevel') or '—')])
        self.prowlarr_alerts.setRowCount(0)
        for obj in alerts:
            self._fill_row(self.prowlarr_alerts,
                           [str(obj.get('source') or obj.get('type') or 'Prowlarr'),
                            str(obj.get('message') or '—')])

    def sync_prowlarr(self):
        try:
            client = self._prowlarr_client()
            answer = QMessageBox.question(self, 'Sincronizar indexadores',
                    'Enviar os indexadores do Prowlarr para os aplicativos cadastrados, inclusive Lidarr?\n\nSomente indexadores compatíveis são sincronizados.',
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
            self.prowlarr_state.setText('Sincronizando...')
            self._thread_lidarr(client.sync_indexers,
                    lambda _: (self.prowlarr_state.setText('Sincronização solicitada'),
                    QMessageBox.information(self, 'Prowlarr', 'Comando de sincronização aceito. Atualize o Lidarr para acompanhar.')), 'Prowlarr')
        except Exception as exc:
            QMessageBox.warning(self, 'Prowlarr', str(exc))

    def connect_prowlarr_lidarr(self):
        from keyring import get_password
        try:
            prowlarr = self._prowlarr_client()
            lidarr = self._lidarr_client()
            answer = QMessageBox.question(self, 'Conectar Prowlarr ao Lidarr',
                 'Cadastrar o Lidarr no Prowlarr com sincronização completa?\n\nIsso adiciona indexadores COMPATÍVEIS cadastrados no Prowlarr ao Lidarr. Verifique os endereços nos ajustes primeiro.',
                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
            self.prowlarr_state.setText('Vinculando Lidarr...')
            self._thread_lidarr(lambda: prowlarr.register_lidarr(lidarr.base_url, lidarr.api_key),
                  lambda _: (self.prowlarr_state.setText('Lidarr vinculado'), self.load_prowlarr(),
                      QMessageBox.information(self, 'Prowlarr', 'Lidarr cadastrado no Prowlarr. A sincronização agora é gerenciada pelo Prowlarr.')), 'Prowlarr')
        except Exception as exc:
            QMessageBox.warning(self, 'Prowlarr', str(exc))

    def save_prowlarr(self):
        from keyring import set_password
        try:
            url = validate_server_url(self.prowlarr_url.text(), 'Prowlarr')
            key = self.prowlarr_key.text().strip()
            if key:
                ProwlarrClient(url, key)
                set_password(APP_ID, 'prowlarr-api-key', key)
                self.prowlarr_key.clear()
            self.settings.setValue('prowlarrUrl', url)
            QMessageBox.information(self, 'Prowlarr', 'URL salva. A API Key foi guardada no cofre, caso informada.')
        except Exception as exc:
            QMessageBox.warning(self, 'Prowlarr', str(exc))

    def test_prowlarr(self):
        try:
            self.prowlarr_state.setText('Testando...')
            self._thread_lidarr(self._prowlarr_client().status,
                  lambda status: (self.prowlarr_state.setText('Conectado'),
                      QMessageBox.information(self, 'Prowlarr', 'Conectado à versão ' + str(status.get('version', '?')))), 'Prowlarr')
        except Exception as exc:
            QMessageBox.warning(self, 'Prowlarr', str(exc))

    # ---------------------- qBittorrent ----------------------
    def _qbit_client(self) -> QBittorrentClient:
        from keyring import get_password
        pwd = self.qbit_password.text() or (get_password(APP_ID, 'qbit-webui-password') or '')
        return QBittorrentClient(self.qbit_url.text(), self.qbit_user.text(), pwd)

    def _make_qbit(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(11)
        top = QHBoxLayout()
        label = QLabel('qBittorrent  •  Transferências')
        label.setStyleSheet('font-size:17px; font-weight:700;')
        top.addWidget(label)
        top.addStretch()
        self.qbit_state = QLabel('Desconectado')
        self.qbit_state.setObjectName('pill')
        top.addWidget(self.qbit_state)
        refresh = QPushButton('↻ Atualizar')
        refresh.clicked.connect(self.load_qbit)
        refresh.setObjectName('primary')
        top.addWidget(refresh)
        layout.addLayout(top)
        self.qbit_info = QLabel('Conecte-se à WebUI para acompanhar torrents do Lidarr, incluindo progresso, tamanho, estado e categoria.')
        self.qbit_info.setObjectName('sub')
        self.qbit_info.setWordWrap(True)
        layout.addWidget(self.qbit_info)
        searchrow = QHBoxLayout()
        self.qbit_filter = QLineEdit()
        self.qbit_filter.setPlaceholderText('Filtrar transferências por nome ou categoria...')
        self.qbit_filter.textChanged.connect(self._render_qbit_rows)
        searchrow.addWidget(self.qbit_filter)
        open_btn = QPushButton('Abrir WebUI ↗')
        open_btn.clicked.connect(lambda: self._open_service(self.qbit_url.text(), 'qBittorrent'))
        searchrow.addWidget(open_btn)
        layout.addLayout(searchrow)
        self.qbit_table = QTableWidget(0, 6)
        self._style_table(self.qbit_table, ['Nome', 'Progresso', 'Tamanho', 'Velocidade ↓', 'Categoria', 'Estado'])
        self.qbit_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.qbit_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        layout.addWidget(self.qbit_table, 1)
        actions = QHBoxLayout()
        for text, command in [('⏸ Pausar', 'pause'), ('▶ Retomar', 'resume'), ('↻ Verificar', 'recheck')]:
            btn = QPushButton(text)
            btn.clicked.connect(lambda checked=False, name=command: self.control_qbit(name))
            actions.addWidget(btn)
        layout.addLayout(actions)
        panel = self._card(layout)
        panel.addWidget(QLabel('Adicionar torrent manualmente (somente conteúdo autorizado)'))
        addrow = QHBoxLayout()
        self.qbit_link = QLineEdit()
        self.qbit_link.setPlaceholderText('magnet:?xt=urn:btih:...  ou  https://.../arquivo.torrent')
        addrow.addWidget(self.qbit_link, 1)
        self.qbit_cat = QComboBox()
        self.qbit_cat.setEditable(True)
        self.qbit_cat.addItem('music')
        addrow.addWidget(self.qbit_cat)
        panel.addLayout(addrow)
        buttons = QHBoxLayout()
        add = QPushButton('Adicionar link')
        add.setObjectName('primary')
        add.clicked.connect(self.add_qbit_link)
        buttons.addWidget(add)
        add_file = QPushButton('Abrir arquivo .torrent...')
        add_file.clicked.connect(self.add_qbit_file)
        buttons.addWidget(add_file)
        new_cat = QPushButton('Criar categoria')
        new_cat.clicked.connect(self.create_qbit_category)
        buttons.addWidget(new_cat)
        panel.addLayout(buttons)
        note = QLabel('Downloads iniciados pelo Lidarr devem ser enviados pelo próprio Lidarr para o qBittorrent, com categoria e importação configuradas no Lidarr. Adições manuais podem não ser importadas automaticamente.')
        note.setWordWrap(True)
        note.setObjectName('sub')
        panel.addWidget(note)
        connect = QPushButton('Vincular qBittorrent ao Lidarr (categoria lidarr)')
        connect.clicked.connect(self.connect_qbit_lidarr)
        panel.addWidget(connect)
        self.stack.addWidget(page)

    @staticmethod
    def _fill_row(table, vals, data=None):
        r = table.rowCount()
        table.insertRow(r)
        for col, value in enumerate(vals):
            cell = QTableWidgetItem(str(value))
            if data is not None:
                cell.setData(Qt.UserRole, data)
            table.setItem(r, col, cell)

    @staticmethod
    def _size_display(value):
        val = float(value or 0)
        for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
            if val < 1024 or unit == 'TB':
                return f'{val:.1f} {unit}'
            val /= 1024

    def load_qbit(self):
        try:
            client = self._qbit_client()
            self.qbit_state.setText('Conectando...')
            self._thread_lidarr(client.snapshot, self._render_qbit, 'qBittorrent')
        except Exception as exc:
            QMessageBox.warning(self, 'qBittorrent', str(exc))
            self._switch(6)

    def _render_qbit(self, data):
        self.qbit_rows = data.get('torrents') or []
        self.qbit_state.setText('Conectado  •  ' + str(data.get('version', '?')))
        active = sum(1 for t in self.qbit_rows if t.get('state') in ('downloading', 'forcedDL', 'metaDL'))
        done = sum(1 for t in self.qbit_rows if float(t.get('progress') or 0) >= 1)
        self.qbit_info.setText(f'{len(self.qbit_rows)} torrents  •  {active} baixando  •  {done} completos. Última consulta manual; clique em Atualizar.')
        present = self.qbit_cat.currentText()
        self.qbit_cat.clear()
        categories = list((data.get('categories') or {}).keys())
        self.qbit_cat.addItems(categories)
        if present:
            self.qbit_cat.setCurrentText(present)
        elif 'music' in categories:
            self.qbit_cat.setCurrentText('music')
        self._render_qbit_rows()

    def _render_qbit_rows(self, *_):
        text = self.qbit_filter.text().casefold().strip()
        self.qbit_table.setRowCount(0)
        for obj in self.qbit_rows:
            name = str(obj.get('name') or '—')
            category = str(obj.get('category') or '—')
            if text and text not in name.casefold() and text not in category.casefold():
                continue
            percent = max(0, min(100, round(float(obj.get('progress') or 0) * 100)))
            fields = [name, f'{percent}%', self._size_display(obj.get('size')),
                      self._size_display(obj.get('dlspeed')) + '/s', category, str(obj.get('state') or '—')]
            self._fill_row(self.qbit_table, fields, obj.get('hash'))

    def _qbit_selected(self):
        row = self.qbit_table.currentRow()
        if row < 0 or not self.qbit_table.item(row, 0):
            raise QBittorrentError('Selecione uma transferência na tabela.')
        return str(self.qbit_table.item(row, 0).data(Qt.UserRole) or '')

    def control_qbit(self, action):
        try:
            h = self._qbit_selected()
            client = self._qbit_client()
            self.qbit_state.setText('Enviando comando...')
            self._thread_lidarr(lambda: client.control(action, h),
                   lambda _: self.load_qbit(), 'qBittorrent')
        except Exception as exc:
            QMessageBox.warning(self, 'qBittorrent', str(exc))

    def _qbit_add(self, link='', file_path=None):
        try:
            client = self._qbit_client()
            category = self.qbit_cat.currentText().strip()
            confirm = QMessageBox.question(self, 'Adicionar download',
                        'Adicionar este torrent ao qBittorrent? Use somente material cujo compartilhamento ou download seja autorizado.\n\nCategoria: ' + (category or '(nenhuma)'),
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if confirm != QMessageBox.Yes:
                return
            self.qbit_state.setText('Adicionando...')
            self._thread_lidarr(lambda: client.add_torrent(link, category, file_path),
                       lambda _: (self.qbit_link.clear(), self.load_qbit()), 'qBittorrent')
        except Exception as exc:
            QMessageBox.warning(self, 'qBittorrent', str(exc))

    def add_qbit_link(self):
        if not self.qbit_link.text().strip():
            QMessageBox.information(self, 'qBittorrent', 'Informe o link magnet ou .torrent.')
            return
        self._qbit_add(self.qbit_link.text().strip())

    def add_qbit_file(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Adicionar torrent autorizado', str(Path.home()), 'Torrents (*.torrent)')
        if path:
            self._qbit_add(file_path=Path(path))

    def create_qbit_category(self):
        try:
            name = self.qbit_cat.currentText().strip()
            if not name:
                raise QBittorrentError('Informe o nome da categoria.')
            client = self._qbit_client()
            self._thread_lidarr(lambda: client.create_category(name),
                    lambda _: self.load_qbit(), 'qBittorrent')
        except Exception as exc:
            QMessageBox.warning(self, 'qBittorrent', str(exc))

    def _open_service(self, url, name):
        try:
            clean = validate_server_url(url, name)
            QDesktopServices.openUrl(QUrl(clean))
        except Exception as exc:
            QMessageBox.warning(self, name, str(exc))

    def connect_qbit_lidarr(self):
        try:
            qclient = self._qbit_client()
            lidarr = self._lidarr_client()
            answer = QMessageBox.question(self, 'Conectar qBittorrent ao Lidarr',
                  'Cadastrar o qBittorrent como cliente de download do Lidarr?\n\nSerá criada a categoria "lidarr" no qBittorrent se necessário. O Lidarr organizará apenas os downloads que ele próprio enviar.',
                  QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
            self.qbit_state.setText('Vinculando Lidarr...')
            def register():
                if 'lidarr' not in qclient.categories():
                    qclient.create_category('lidarr')
                return lidarr.register_qbittorrent(qclient.base_url, qclient.username, qclient.password, 'lidarr')
            self._thread_lidarr(register,
                 lambda _: (self.qbit_state.setText('Lidarr vinculado'), self.load_qbit(),
                      QMessageBox.information(self, 'qBittorrent', 'Cliente cadastrado no Lidarr. Revise Download Clients e Root Folders no Lidarr.')), 'qBittorrent')
        except Exception as exc:
            QMessageBox.warning(self, 'qBittorrent', str(exc))

    def save_qbit(self):
        from keyring import set_password
        try:
            url = validate_server_url(self.qbit_url.text(), 'qBittorrent')
            user = self.qbit_user.text().strip()
            pwd = self.qbit_password.text()
            if not user:
                raise QBittorrentError('Informe o usuário da WebUI.')
            if pwd:
                set_password(APP_ID, 'qbit-webui-password', pwd)
                self.qbit_password.clear()
            self.settings.setValue('qbitUrl', url)
            self.settings.setValue('qbitUser', user)
            QMessageBox.information(self, 'qBittorrent', 'Configurações salvas. Senha guardada no cofre, caso informada.')
        except Exception as exc:
            QMessageBox.warning(self, 'qBittorrent', str(exc))

    def test_qbit(self):
        try:
            self.qbit_state.setText('Testando...')
            self._thread_lidarr(self._qbit_client().version,
                    lambda ver: (self.qbit_state.setText('Conectado'),
                        QMessageBox.information(self, 'qBittorrent', 'Conexão realizada: ' + str(ver))), 'qBittorrent')
        except Exception as exc:
            QMessageBox.warning(self, 'qBittorrent', str(exc))

    def _make_library(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        hint = QLabel('Auditoria local: detecta FLAC e informa bits, frequência, tags e presença de capa incorporada.')
        hint.setObjectName('sub')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        bar = QHBoxLayout()
        scan = QPushButton('Analisar biblioteca')
        scan.setObjectName('primary')
        scan.clicked.connect(self.scan_library)
        bar.addWidget(scan)
        folder = QPushButton('Abrir pasta')
        folder.clicked.connect(self.open_folder)
        bar.addWidget(folder)
        export = QPushButton('Exportar CSV')
        export.clicked.connect(self.export_csv)
        bar.addWidget(export)
        bar.addStretch()
        layout.addLayout(bar)
        self.summary = QLabel('Nenhuma análise realizada.')
        layout.addWidget(self.summary)
        self.library_filter = QLineEdit()
        self.library_filter.setPlaceholderText('Filtrar por título, formato, pasta ou qualidade...')
        self.library_filter.textChanged.connect(self.filter_library)
        layout.addWidget(self.library_filter)
        results_area = QHBoxLayout()
        self.files_table = QTableWidget(0, 5)
        self.files_table.setHorizontalHeaderLabels(['Título', 'Formato', 'Qualidade detectada', 'Capa', 'Arquivo'])
        self.files_table.verticalHeader().setVisible(False)
        self.files_table.setAlternatingRowColors(True)
        self.files_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.files_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.files_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.files_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.files_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.files_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.files_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.files_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.files_table.itemSelectionChanged.connect(self.show_file_details)
        results_area.addWidget(self.files_table, 1)
        details_frame = QFrame()
        details_frame.setObjectName('card')
        details_frame.setFixedWidth(225)
        details_layout = QVBoxLayout(details_frame)
        self.album_cover = QLabel('♪')
        self.album_cover.setFixedSize(196, 196)
        self.album_cover.setAlignment(Qt.AlignCenter)
        self.album_cover.setStyleSheet('background:#1c3141;color:#61d3c5;font-size:60px;border-radius:9px;')
        details_layout.addWidget(self.album_cover)
        details_layout.addWidget(QLabel('Detalhes da faixa'))
        self.file_details = QLabel('Escolha uma música na biblioteca. Aqui você verá capa, tags e qualidade.')
        self.file_details.setObjectName('sub')
        self.file_details.setWordWrap(True)
        details_layout.addWidget(self.file_details)
        details_layout.addStretch()
        results_area.addWidget(details_frame)
        layout.addLayout(results_area, stretch=1)
        self.stack.addWidget(page)

    def filter_library(self, *_):
        needle = self.library_filter.text().strip().casefold()
        for r in range(self.files_table.rowCount()):
            values = [self.files_table.item(r, c).text().casefold()
                      for c in range(self.files_table.columnCount())
                      if self.files_table.item(r, c)]
            self.files_table.setRowHidden(r, bool(needle and not any(needle in v for v in values)))

    def show_file_details(self):
        items = self.files_table.selectedItems()
        if not items:
            return
        row = items[0].row()
        item = self.files_table.item(row, 4)
        if not item:
            return
        path = Path(item.text())
        title = self.files_table.item(row, 0).text()
        fmt = self.files_table.item(row, 1).text()
        quality = self.files_table.item(row, 2).text()
        artist = '—'
        album = '—'
        cover_bytes = None
        try:
            from mutagen import File as MusicFile
            tagged = MusicFile(path, easy=True)
            if tagged:
                artist = str((tagged.get('artist') or ['—'])[0])
                album = str((tagged.get('album') or ['—'])[0])
            if path.suffix.lower() == '.flac':
                from mutagen.flac import FLAC
                f = FLAC(path)
                if f.pictures:
                    cover_bytes = f.pictures[0].data
            elif path.suffix.lower() == '.mp3':
                from mutagen.id3 import ID3
                for tag in ID3(path).values():
                    if getattr(tag, 'FrameID', '') == 'APIC':
                        cover_bytes = tag.data
                        break
            elif path.suffix.lower() == '.m4a':
                from mutagen.mp4 import MP4
                covers = MP4(path).tags.get('covr', [])
                if covers:
                    cover_bytes = bytes(covers[0])
            if not cover_bytes:
                for cover_name in ('cover.jpg', 'cover.png', 'folder.jpg', 'folder.png'):
                    candidate = path.parent / cover_name
                    if candidate.is_file() and candidate.stat().st_size < 15_000_000:
                        cover_bytes = candidate.read_bytes()
                        break
        except Exception:
            pass
        self.album_cover.setPixmap(QPixmap())
        self.album_cover.setText('♪')
        if cover_bytes and len(cover_bytes) < 15_000_000:
            pix = QPixmap()
            if pix.loadFromData(cover_bytes):
                self.album_cover.setText('')
                self.album_cover.setPixmap(pix.scaled(196, 196, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.file_details.setText(f'{title}\n\nArtista: {artist}\nÁlbum: {album}\n\nFormato: {fmt}\nQualidade: {quality}\n\n{path.name}')

    def _make_settings(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(13)
        box = self._card(layout)
        box.addWidget(QLabel('Destino dos arquivos'))
        out = QHBoxLayout()
        self.path_box = QLineEdit(str(self.base))
        out.addWidget(self.path_box, stretch=1)
        browse = QPushButton('Escolher...')
        browse.clicked.connect(self.select_folder)
        out.addWidget(browse)
        box.addLayout(out)
        self.covers = QCheckBox('Incorporar capa no FLAC e salvar cover.jpg quando disponível (TIDAL)')
        self.covers.setChecked(self.settings.value('covers', 'true') == 'true')
        box.addWidget(self.covers)
        save = QPushButton('Salvar configurações')
        save.setObjectName('primary')
        save.clicked.connect(self.save_settings)
        box.addWidget(save)
        acc = self._card(layout)
        acc.addWidget(QLabel('Contas e ferramentas'))
        login = QPushButton('Conectar conta TIDAL (abrir terminal de login)')
        login.clicked.connect(self.tidal_login)
        acc.addWidget(login)
        test = QPushButton('Verificar dependências instaladas')
        test.clicked.connect(self._refresh_deps)
        acc.addWidget(test)
        text = QLabel('A autenticação é feita diretamente pelo tiddl, em uma pasta isolada do aplicativo. O Music Library Manager não solicita sua senha.\n\nAs assinaturas dos serviços não autorizam automaticamente exportar arquivos; respeite os termos e as licenças aplicáveis.')
        text.setObjectName('sub')
        text.setWordWrap(True)
        acc.addWidget(text)
        lid = self._card(layout)
        lid.addWidget(QLabel('Lidarr  •  Conexão com servidor'))
        lid.addWidget(QLabel('URL local ou na rede (Lidarr deve estar instalado e em execução)'))
        self.lidarr_url = QLineEdit(str(self.settings.value('lidarrUrl', 'http://localhost:8686')))
        self.lidarr_url.setPlaceholderText('http://localhost:8686')
        lid.addWidget(self.lidarr_url)
        lid.addWidget(QLabel('API Key  •  Lidarr → Settings → General → Security'))
        self.lidarr_key = QLineEdit()
        self.lidarr_key.setEchoMode(QLineEdit.Password)
        self.lidarr_key.setPlaceholderText('Cole sua API Key; ela não fica visível na tela')
        lid.addWidget(self.lidarr_key)
        row = QHBoxLayout()
        save_lidarr_btn = QPushButton('Salvar Lidarr')
        save_lidarr_btn.setObjectName('primary')
        save_lidarr_btn.clicked.connect(self.save_lidarr)
        row.addWidget(save_lidarr_btn)
        test_lidarr_btn = QPushButton('Testar conexão')
        test_lidarr_btn.clicked.connect(self.test_lidarr)
        row.addWidget(test_lidarr_btn)
        lid.addLayout(row)
        lid_hint = QLabel('A API Key é guardada no cofre de credenciais do sistema. Não configure o servidor na internet sem HTTPS e autenticação.')
        lid_hint.setWordWrap(True)
        lid_hint.setObjectName('sub')
        lid.addWidget(lid_hint)
        pr = self._card(layout)
        pr.addWidget(QLabel('Prowlarr  •  API de indexadores'))
        pr.addWidget(QLabel('Servidor (normalmente http://localhost:9696)'))
        self.prowlarr_url = QLineEdit(str(self.settings.value('prowlarrUrl', 'http://localhost:9696')))
        pr.addWidget(self.prowlarr_url)
        pr.addWidget(QLabel('API Key  •  Prowlarr → Settings → General'))
        self.prowlarr_key = QLineEdit()
        self.prowlarr_key.setEchoMode(QLineEdit.Password)
        self.prowlarr_key.setPlaceholderText('Cole para salvar; chave armazenada no cofre')
        pr.addWidget(self.prowlarr_key)
        prrow = QHBoxLayout()
        p_save = QPushButton('Salvar Prowlarr')
        p_save.clicked.connect(self.save_prowlarr)
        prrow.addWidget(p_save)
        p_test = QPushButton('Testar conexão')
        p_test.clicked.connect(self.test_prowlarr)
        prrow.addWidget(p_test)
        pr.addLayout(prrow)

        qb = self._card(layout)
        qb.addWidget(QLabel('qBittorrent  •  WebUI'))
        qb.addWidget(QLabel('Ative Ferramentas → Opções → Interface Web no qBittorrent'))
        self.qbit_url = QLineEdit(str(self.settings.value('qbitUrl', 'http://localhost:8080')))
        qb.addWidget(self.qbit_url)
        self.qbit_user = QLineEdit(str(self.settings.value('qbitUser', 'admin')))
        self.qbit_user.setPlaceholderText('Usuário da WebUI')
        qb.addWidget(self.qbit_user)
        self.qbit_password = QLineEdit()
        self.qbit_password.setEchoMode(QLineEdit.Password)
        self.qbit_password.setPlaceholderText('Senha da WebUI; armazenada no cofre')
        qb.addWidget(self.qbit_password)
        qbrow = QHBoxLayout()
        q_save = QPushButton('Salvar qBittorrent')
        q_save.clicked.connect(self.save_qbit)
        qbrow.addWidget(q_save)
        q_test = QPushButton('Testar conexão')
        q_test.clicked.connect(self.test_qbit)
        qbrow.addWidget(q_test)
        qb.addLayout(qbrow)
        warning = QLabel('IMPORTANTE: mantenha estas WebUIs restritas ao PC/rede confiável. Para acesso remoto, use HTTPS/VPN e autenticação. O programa não configura trackers automaticamente.')
        warning.setObjectName('sub')
        warning.setWordWrap(True)
        qb.addWidget(warning)
        jf = self._card(layout)
        jf.addWidget(QLabel('Jellyfin • Biblioteca e playlists pessoais'))
        jf.addWidget(QLabel('Servidor (normalmente http://localhost:8096)'))
        self.jellyfin_url = QLineEdit(str(self.settings.value('jellyfinUrl', 'http://localhost:8096')))
        jf.addWidget(self.jellyfin_url)
        jf.addWidget(QLabel('Conta com acesso à biblioteca de música e permissão para playlists'))
        self.jellyfin_username = QLineEdit(str(self.settings.value('jellyfinUser', '')))
        self.jellyfin_username.setPlaceholderText('Usuário Jellyfin')
        jf.addWidget(self.jellyfin_username)
        self.jellyfin_password = QLineEdit()
        self.jellyfin_password.setEchoMode(QLineEdit.Password)
        self.jellyfin_password.setPlaceholderText('Senha (não é salva; trocada por token no cofre)')
        jf.addWidget(self.jellyfin_password)
        row_jf = QHBoxLayout()
        jf_login = QPushButton('Conectar Jellyfin')
        jf_login.setObjectName('primary')
        jf_login.clicked.connect(self.login_jellyfin)
        row_jf.addWidget(jf_login)
        jf_test = QPushButton('Testar Jellyfin')
        jf_test.clicked.connect(self.test_jellyfin)
        row_jf.addWidget(jf_test)
        jf.addLayout(row_jf)
        jf.addWidget(QLabel('Jellyfin deve indexar a mesma pasta musical para permitir reutilizar músicas existentes. Não baixa áudio de outras fontes.'))
        layout.addStretch()
        scroll.setWidget(page)
        self.stack.addWidget(scroll)


    def _jellyfin_client(self) -> JellyfinClient:
        import keyring
        url = str(self.settings.value('jellyfinUrl', 'http://localhost:8096'))
        token = keyring.get_password(APP_ID, 'jellyfin-token') or ''
        user_id = str(self.settings.value('jellyfinUserId',''))
        if not token or not user_id:
            raise JellyfinError('Conecte sua conta Jellyfin em Configurações.')
        return JellyfinClient(url, token, user_id)

    def login_jellyfin(self):
        import keyring
        url = self.jellyfin_url.text().strip()
        username = self.jellyfin_username.text().strip()
        password = self.jellyfin_password.text()
        try:
            client = JellyfinClient(url)
            if not username or not password:
                raise JellyfinError('Informe seu usuário e senha Jellyfin.')
        except Exception as exc:
            QMessageBox.warning(self, 'Jellyfin', str(exc))
            return
        self.jellyfin_info.setText('Autenticando com Jellyfin...')
        def job():
            return client.authenticate(username,password)
        def ready(data):
            keyring.set_password(APP_ID, 'jellyfin-token', data['token'])
            self.settings.setValue('jellyfinUrl',url)
            self.settings.setValue('jellyfinUser',username)
            self.settings.setValue('jellyfinUserId',data['user_id'])
            self.jellyfin_password.clear()
            self.jellyfin_info.setText('Conectado como ' + data['user'] + '. Token salvo no cofre do Windows.')
        self._thread_lidarr(job,ready,'Jellyfin')

    def test_jellyfin(self):
        try:
            client = self._jellyfin_client()
        except Exception as exc:
            QMessageBox.warning(self,'Jellyfin',str(exc));return
        self._thread_lidarr(lambda:client.status(),
            lambda data: QMessageBox.information(self,'Jellyfin',
                'Conectado ao Jellyfin ' + str(data.get('Version') or '') + ' (' + str(data.get('ServerName') or '') + ')'), 'Jellyfin')

    def _make_jellyfin(self):
        page=QWidget()
        layout=QVBoxLayout(page)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(13)
        title=QLabel('◉ Jellyfin • Sua biblioteca de músicas')
        title.setObjectName('headline')
        layout.addWidget(title)
        desc=QLabel('Escute e organize FLACs já indexados no seu servidor Jellyfin. As playlists não duplicam nem baixam os arquivos.')
        desc.setWordWrap(True)
        desc.setObjectName('sub')
        layout.addWidget(desc)
        controls=QHBoxLayout()
        for caption, callback in [('Carregar biblioteca',self.load_jellyfin),
                                  ('Criar playlist da análise FLAC',self.create_jellyfin_playlist),
                                  ('Atualizar índice do servidor',self.refresh_jellyfin_library),
                                  ('Abrir Jellyfin',self.open_jellyfin)]:
            b=QPushButton(caption)
            b.clicked.connect(callback)
            controls.addWidget(b)
        layout.addLayout(controls)
        self.jellyfin_info=QLabel('Conecte Jellyfin em Configurações e carregue a biblioteca.\nAs músicas precisam existir no Jellyfin para entrar em suas playlists.')
        self.jellyfin_info.setWordWrap(True)
        self.jellyfin_info.setObjectName('sub')
        layout.addWidget(self.jellyfin_info)
        name_row=QHBoxLayout()
        name_row.addWidget(QLabel('Nome da playlist:'))
        self.jellyfin_playlist_name=QLineEdit('Playlist FLAC')
        name_row.addWidget(self.jellyfin_playlist_name,stretch=1)
        layout.addLayout(name_row)
        self.jellyfin_table=QTableWidget(0,4)
        self._style_table(self.jellyfin_table,['Música','Artista','Álbum','Jellyfin ID'])
        self.jellyfin_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
        self.jellyfin_table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch)
        self.jellyfin_table.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch)
        layout.addWidget(self.jellyfin_table,stretch=1)
        layout.addWidget(QLabel('Nota: FLAC indicado pelo Jellyfin é uma informação de mídia do servidor, não prova de origem lossless.'))
        self.stack.addWidget(page)

    def load_jellyfin(self):
        try:
            client=self._jellyfin_client()
        except Exception as exc:
            QMessageBox.warning(self,'Jellyfin',str(exc));return
        self.jellyfin_info.setText('Consultando os arquivos FLAC indexados no Jellyfin...')
        def job():
            return client.music()
        def ready(items):
            from jellyfin_api import jellyfin_flac_candidate
            self.jellyfin_items=items
            entries=[p for item in items if (p:=jellyfin_flac_candidate(item))]
            self.jellyfin_table.setRowCount(len(entries))
            for row,item in enumerate(entries):
                for col,key in enumerate(('title','artist','album','id')):
                    self.jellyfin_table.setItem(row,col,QTableWidgetItem(item[key]))
            self.jellyfin_info.setText(f'{len(items)} músicas indexadas • {len(entries)} anunciadas como FLAC. Nenhuma música foi baixada.')
        self._thread_lidarr(job,ready,'Jellyfin')

    def refresh_jellyfin_library(self):
        try:
            client=self._jellyfin_client()
        except Exception as exc:
            QMessageBox.warning(self,'Jellyfin',str(exc));return
        if QMessageBox.question(self,'Jellyfin','Solicitar uma nova varredura da biblioteca no servidor?',
                                QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:
            return
        self._thread_lidarr(lambda:client.refresh_library(),
            lambda data: self.jellyfin_info.setText('Atualização solicitada. Use Carregar biblioteca após a varredura.'),'Jellyfin')

    def open_jellyfin(self):
        try:
            client=self._jellyfin_client()
            QDesktopServices.openUrl(QUrl(client.base_url))
        except Exception as exc:
            QMessageBox.warning(self,'Jellyfin',str(exc))

    def create_jellyfin_playlist(self):
        if not self.flac_songs:
            QMessageBox.information(self,'Jellyfin','Analise uma playlist na aba Busca FLAC antes de criar.');return
        try:
            client=self._jellyfin_client()
        except Exception as exc:
            QMessageBox.warning(self,'Jellyfin',str(exc));return
        name=self.jellyfin_playlist_name.text().strip() or 'Playlist FLAC'
        import copy
        songs=copy.deepcopy(self.flac_songs)
        self.jellyfin_info.setText('Conferindo músicas disponíveis no Jellyfin...')
        def job():
            items=client.music()
            count=link_jellyfin_songs(songs,items)
            ids=[s.jellyfin_id for s in songs if s.jellyfin_id]
            if not ids:
                return songs,count,None,len(items)
            report=client.create_playlist(name,ids)
            return songs,count,report,len(items)
        def ready(data):
            songs,count,report,total=data
            self.flac_songs=songs
            self.draw_flac_tracks()
            if report:
                self.jellyfin_info.setText(f'Playlist "{name}" criada no Jellyfin com {report["count"]} faixa(s) na ordem original. {len(songs)-len([s for s in songs if s.jellyfin_id])} não foram encontradas.')
                QMessageBox.information(self,'Jellyfin',f'Playlist criada: {name} • {report["count"]} faixa(s). Músicas ausentes foram ignoradas.')
            else:
                self.jellyfin_info.setText(f'Nenhuma faixa da playlist corresponde aos {total} itens Jellyfin.')
                QMessageBox.information(self,'Jellyfin','Nenhuma música FLAC correspondente foi localizada no Jellyfin. Carregue sua biblioteca no servidor primeiro.')
        self._thread_lidarr(job,ready,'Jellyfin')

    def _make_flac_finder(self):
        """Lossless-only flow: Spotify metadata → local verified FLAC → Lidarr album search."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 10)
        layout.setSpacing(12)
        heading = QLabel('◇ Localizador FLAC • Faixa por faixa')
        heading.setObjectName('headline')
        layout.addWidget(heading)
        sub = QLabel('Cole a playlist do Spotify. O spotDL extrai SÓ metadados; o programa cruza artista, gravação e álbum com FLACs locais. O Lidarr pode buscar álbuns ainda ausentes em fontes que você configurou.')
        sub.setWordWrap(True)
        sub.setObjectName('sub')
        layout.addWidget(sub)

        card = self._card(layout)
        card.addWidget(QLabel('LINK DA PLAYLIST'))
        row = QHBoxLayout()
        self.flac_url = QLineEdit()
        self.flac_url.setPlaceholderText('https://open.spotify.com/playlist/... • TIDAL via tiddl')
        self.flac_url.returnPressed.connect(self.analyze_flac_playlist)
        row.addWidget(self.flac_url, stretch=1)
        self.flac_analyze_btn = QPushButton('1. Analisar playlist')
        self.flac_analyze_btn.setObjectName('primary')
        self.flac_analyze_btn.clicked.connect(self.analyze_flac_playlist)
        row.addWidget(self.flac_analyze_btn)
        card.addLayout(row)
        card.addWidget(QLabel('PASTA PARA VERIFICAR FLAC (LOCAL OU COMPARTILHADA COM LIDARR)'))
        location = QHBoxLayout()
        self.flac_folder = QLineEdit(str(self.settings.value('flacFolder', self.base)))
        location.addWidget(self.flac_folder, stretch=1)
        folder_btn = QPushButton('Selecionar pasta')
        folder_btn.clicked.connect(self.pick_flac_folder)
        location.addWidget(folder_btn)
        card.addLayout(location)
        self.flac_state = QLabel('Pronto para analisar. Você poderá solicitar buscas somente após conferir a lista.')
        self.flac_state.setWordWrap(True)
        self.flac_state.setObjectName('sub')
        card.addWidget(self.flac_state)

        self.flac_counter = QLabel('0 FLAC confirmado  •  0 aguardando  •  0 não catalogado')
        self.flac_counter.setObjectName('statvalue')
        layout.addWidget(self.flac_counter)
        self.flac_table = QTableWidget(0, 6)
        self._style_table(self.flac_table, ['#', 'Faixa', 'Artista', 'Álbum', 'Estado', 'Qualidade'])
        self.flac_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.flac_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.flac_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.flac_table.setMinimumHeight(290)
        self.flac_table.itemSelectionChanged.connect(self.show_flac_track)
        layout.addWidget(self.flac_table)
        self.flac_track_note = QLabel('Selecione uma faixa para ver o motivo do resultado e o caminho do arquivo.')
        self.flac_track_note.setWordWrap(True)
        self.flac_track_note.setObjectName('sub')
        layout.addWidget(self.flac_track_note)

        actions = QHBoxLayout()
        self.flac_search_btn = QPushButton('2. Buscar álbuns faltantes no Lidarr')
        self.flac_search_btn.setObjectName('primary')
        self.flac_search_btn.clicked.connect(self.search_missing_flacs)
        actions.addWidget(self.flac_search_btn)
        self.flac_recheck_btn = QPushButton('↻ Reverificar FLAC')
        self.flac_recheck_btn.clicked.connect(self.recheck_flacs)
        actions.addWidget(self.flac_recheck_btn)
        self.flac_m3u_btn = QPushButton('Exportar .m3u8')
        self.flac_m3u_btn.clicked.connect(self.export_flac_playlist)
        actions.addWidget(self.flac_m3u_btn)
        mobile_btn = QPushButton('Exportar para celular')
        mobile_btn.clicked.connect(self.export_mobile_playlist)
        actions.addWidget(mobile_btn)
        jelly_btn = QPushButton('Criar no Jellyfin')
        jelly_btn.clicked.connect(self.create_jellyfin_playlist)
        actions.addWidget(jelly_btn)
        layout.addLayout(actions)
        info = QLabel('IMPORTANTE: cada busca é por álbum completo, não por faixa. Sem artistas/álbuns cadastrados no Lidarr, será indicado “Não catalogada”. A busca não garante FLAC nem disponibilidade. Verificar codec/16-24 bits não comprova a origem lossless: conversões artificiais ainda exigem auditoria especializada.')
        info.setWordWrap(True)
        info.setObjectName('sub')
        layout.addWidget(info)
        layout.addStretch()
        scroll.setWidget(page)
        self.stack.addWidget(scroll)

    def pick_flac_folder(self):
        chosen = QFileDialog.getExistingDirectory(self, 'Pasta FLAC', self.flac_folder.text())
        if chosen:
            self.flac_folder.setText(chosen)
            self.settings.setValue('flacFolder', chosen)

    def _flac_task(self, fn, handle):
        if self.flac_busy:
            return
        self.flac_busy = True
        self.flac_analyze_btn.setEnabled(False)
        self.flac_search_btn.setEnabled(False)
        self.flac_recheck_btn.setEnabled(False)
        job = LidarrWorker(fn, self)
        self.lidarr_threads.add(job)
        def finished(data, error):
            self.flac_busy = False
            self.flac_analyze_btn.setEnabled(True)
            self.flac_search_btn.setEnabled(True)
            self.flac_recheck_btn.setEnabled(True)
            if error:
                self.flac_state.setText('Falha: ' + str(error))
                QMessageBox.warning(self, 'Busca FLAC', str(error))
            else:
                handle(data)
        def cleanup():
            self.lidarr_threads.discard(job)
            job.deleteLater()
        job.done.connect(finished)
        job.finished.connect(cleanup)
        job.start()

    def analyze_flac_playlist(self):
        from uuid import uuid4
        url = self.flac_url.text().strip()
        service, message = identify_media_url(url)
        if service != 'Spotify' or not is_playlist_link(url):
            QMessageBox.information(self, APP, 'O localizador lê metadados de playlists Spotify. Para TIDAL, use Downloads e tiddl em HIGH/MAX.')
            return
        spotdl_bin = executable('spotdl')
        if not spotdl_bin:
            QMessageBox.warning(self, APP, 'spotDL ausente. Execute instalar.bat.')
            return
        folder = Path(self.flac_folder.text()).expanduser()
        self.settings.setValue('flacFolder', str(folder))
        self.flac_state.setText('Consultando metadados sem baixar áudio e verificando biblioteca local...')
        metadata_path = app_data() / 'playlists' / (uuid4().hex + '.spotdl')
        try:
            jelly_client = self._jellyfin_client()
        except Exception:
            jelly_client = None
        try:
            client = self._lidarr_client()
        except Exception as exc:
            client = None
            client_err = str(exc)
        else:
            client_err = ''
        def job():
            songs = export_spotify_playlist(url, spotdl_bin, metadata_path)
            try:
                library, truncated = scan_flacs(folder)
                apply_local_matches(songs, library)
                lidarr_error = client_err
                if client:
                    try:
                        albums = client.albums()
                        artists = client.artists()
                        lidarr_album_matches(songs, albums, artists)
                        remote_info = find_remote_album_candidates(client, songs, artists)
                        if remote_info['errors']:
                            lidarr_error = ('Algumas consultas do catálogo falharam: ' + remote_info['errors'][0])
                    except Exception as exc:
                        lidarr_error = str(exc)
                jelly_count = 0
                if jelly_client:
                    try:
                        jelly_count = link_jellyfin_songs(songs, jelly_client.music())
                    except Exception as exc:
                        lidarr_error += ' Jellyfin: ' + str(exc)
                return (songs, len(library), truncated, lidarr_error, jelly_count)
            finally:
                metadata_path.unlink(missing_ok=True)
        def handle(data):
            self.flac_songs, n_files, truncated, lidarr_error, jelly_count = data
            self.flac_metadata_url = url
            self.draw_flac_tracks()
            note = f'{len(self.flac_songs)} faixas de playlist • {n_files} FLACs locais examinados • {jelly_count} músicas no Jellyfin.'
            if truncated:
                note += ' Varredura limitada a 20 mil FLACs.'
            if lidarr_error:
                note += ' Lidarr: ' + lidarr_error
            self.flac_state.setText(note)
        self._flac_task(job, handle)

    def draw_flac_tracks(self):
        self.flac_table.setRowCount(len(self.flac_songs))
        counts = {'FLAC confirmado': 0, 'FLAC no Jellyfin': 0, 'Álbum no Lidarr': 0, 'Busca solicitada': 0, 'Não catalogada': 0}
        for i, s in enumerate(self.flac_songs):
            counts[s.status] = counts.get(s.status, 0) + 1
            for col, value in enumerate((i+1, s.title, s.artist, s.album, s.status, s.display_quality())):
                self.flac_table.setItem(i, col, QTableWidgetItem(str(value)))
        self.flac_counter.setText(f'{counts["FLAC confirmado"]} FLAC local • {counts["FLAC no Jellyfin"]} FLAC Jellyfin • {counts["Álbum no Lidarr"] + counts["Busca solicitada"] + counts.get("Catálogo encontrado", 0)} pendentes no Lidarr  •  {counts["Não catalogada"]} não catalogadas')

    def show_flac_track(self):
        row = self.flac_table.currentRow()
        if 0 <= row < len(self.flac_songs):
            s = self.flac_songs[row]
            self.flac_track_note.setText(f'{s.artist} — {s.title} | {s.note or s.status}' + (f'\nArquivo: {s.path}' if s.path else ''))

    def search_missing_flacs(self):
        if not self.flac_songs:
            QMessageBox.information(self, APP, 'Primeiro analise uma playlist.')
            return
        ids = sorted({s.album_id for s in self.flac_songs if s.album_id > 0 and s.status not in ('FLAC confirmado','FLAC no Jellyfin')})
        additions = {(s.artist_id, s.catalogue_record.get('foreignAlbumId')) for s in self.flac_songs if s.status == 'Catálogo encontrado' and s.catalogue_record and s.artist_id}
        if not ids and not additions:
            QMessageBox.information(self, APP, 'Nenhum álbum faltante está cadastrado no Lidarr. Cadastre os artistas/álbuns na interface Lidarr e analise novamente.')
            return
        reply = QMessageBox.question(self, 'Solicitar buscas de álbuns',
            f'Solicitar busca de {len(ids)} álbum(ns) e cadastrar {len(additions)} lançamento(s) ausente(s) no Lidarr?\n\n'
            'Isso poderá adicionar álbuns à sua biblioteca. Os perfis de qualidade e indexadores do Lidarr definem o que será obtido. '
            'Não há garantia de arquivo FLAC nem de direitos sobre o conteúdo.',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            client = self._lidarr_client()
        except Exception as exc:
            QMessageBox.warning(self, 'Lidarr', str(exc))
            return
        import copy
        songs = copy.deepcopy(self.flac_songs)
        self.flac_state.setText('Solicitando busca dos álbuns cadastrados...')
        def job():
            return songs, request_missing_albums(client, songs)
        def handle(data):
            self.flac_songs, report = data
            self.draw_flac_tracks()
            self.flac_state.setText(f'{len(report["registered"])} álbuns cadastrados, {len(report["submitted"])} busca(s) enviada(s) ao Lidarr. {len(report["errors"])} erro(s). Reverifique após a importação.')
            if report['errors']:
                QMessageBox.warning(self, 'Lidarr', str(report['errors'][:5]))
        self._flac_task(job, handle)

    def recheck_flacs(self):
        if not self.flac_songs:
            QMessageBox.information(self, APP, 'Primeiro analise uma playlist.')
            return
        import copy
        songs = copy.deepcopy(self.flac_songs)
        root = Path(self.flac_folder.text()).expanduser()
        self.flac_state.setText('Conferindo os FLACs disponíveis no disco...')
        def job():
            files, truncated = scan_flacs(root)
            apply_local_matches(songs, files)
            return songs, len(files), truncated
        def handle(data):
            self.flac_songs, total, truncated = data
            self.draw_flac_tracks()
            self.flac_state.setText(f'Reverificação concluída: {total} arquivo(s) FLAC lidos.' + (' Varredura limitada.' if truncated else ''))
        self._flac_task(job, handle)

    def export_flac_playlist(self):
        if not self.flac_songs:
            QMessageBox.information(self, APP, 'Analise a playlist primeiro.')
            return
        filename, _ = QFileDialog.getSaveFileName(self, 'Salvar playlist FLAC',
                                      str(self.base / 'Minha Playlist FLAC.m3u8'), 'Playlist M3U8 (*.m3u8)')
        if filename:
            try:
                quantity = generate_m3u(self.flac_songs, Path(filename))
                QMessageBox.information(self, APP, f'{quantity} faixa(s) FLAC incluídas na playlist. As faltantes não foram inventadas nem convertidas.')
            except Exception as exc:
                QMessageBox.warning(self, APP, str(exc))

    def export_mobile_playlist(self):
        if not self.flac_songs:
            QMessageBox.information(self,APP,'Analise a playlist FLAC primeiro.');return
        folder=QFileDialog.getExistingDirectory(self,'Pasta para exportar ao celular',str(self.base))
        if not folder:
            return
        import copy
        songs=copy.deepcopy(self.flac_songs)
        name=self.jellyfin_playlist_name.text().strip() or 'Playlist FLAC'
        self.flac_state.setText('Copiando apenas FLACs existentes; preservando tags, capas e ordem da playlist...')
        def job():
            return export_mobile_package(songs,Path(folder),name)
        def ready(info):
            self.flac_state.setText(f'Pacote para celular pronto: {info["tracks"]} posições de playlist • {info["unique_files"]} FLACs únicos • {info["excluded"]} não disponíveis.')
            QMessageBox.information(self,'Playlist para celular',
                f'Playlist: {info["playlist"]}\nArquivos FLAC únicos: {info["unique_files"]}\n'
                'Copie a pasta inteira para o celular. A playlist usa caminhos relativos.')
        self._flac_task(job,ready)


    # --------------------- Sincronização inteligente ---------------------
    def _make_sync(self):
        page=QWidget()
        layout=QVBoxLayout(page)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(12)
        title=QLabel('↻ Sincronização FLAC • Octave-inspired')
        title.setObjectName('headline')
        layout.addWidget(title)
        desc=QLabel('Playlist Spotify → metadados → FLACs já existentes → Jellyfin → álbuns ausentes no Lidarr. '
                    'Nunca baixa MP3 e não converte arquivos com perdas para FLAC. '
                    'A atualização automática não solicita novos downloads sem sua confirmação.')
        desc.setWordWrap(True)
        desc.setObjectName('sub')
        layout.addWidget(desc)
        card=self._card(layout)
        card.addWidget(QLabel('CADASTRAR PLAYLIST • UMA VEZ'))
        row=QHBoxLayout()
        self.sync_url=QLineEdit()
        self.sync_url.setPlaceholderText('https://open.spotify.com/playlist/...')
        self.sync_url.returnPressed.connect(self.sync_add)
        row.addWidget(self.sync_url,stretch=1)
        self.sync_add_button=QPushButton('Adicionar')
        self.sync_add_button.setObjectName('primary')
        self.sync_add_button.clicked.connect(self.sync_add)
        row.addWidget(self.sync_add_button)
        card.addLayout(row)
        row2=QHBoxLayout()
        row2.addWidget(QLabel('Modo:'))
        self.sync_mode=QComboBox()
        for mode,label in MODES.items(): self.sync_mode.addItem(label,mode)
        row2.addWidget(self.sync_mode)
        row2.addWidget(QLabel('Atualizar:'))
        self.sync_interval=QComboBox()
        for txt,mins in [('Manual',0),('30 min',30),('1 hora',60),('3 horas',180),('12 horas',720)]:
            self.sync_interval.addItem(txt,mins)
        self.sync_interval.currentIndexChanged.connect(self.set_sync_interval)
        row2.addWidget(self.sync_interval)
        row2.addStretch()
        card.addLayout(row2)
        self.sync_table=QTableWidget(0,7)
        self._style_table(self.sync_table,['Nome','Modo','Faixas','FLAC local','Pendentes','Última atualização','Situação'])
        self.sync_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
        self.sync_table.horizontalHeader().setSectionResizeMode(6,QHeaderView.Stretch)
        self.sync_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.sync_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.sync_table.itemSelectionChanged.connect(self.sync_select)
        self.sync_table.setMinimumHeight(200)
        layout.addWidget(self.sync_table)
        row3=QHBoxLayout()
        for text,handler in [('↻ Sincronizar selecionada',self.sync_run),
                             ('Sincronizar todas',self.sync_run_all),
                             ('Solicitar álbuns no Lidarr',self.sync_request_lidarr),
                             ('Publicar Jellyfin',self.sync_publish_jellyfin)]:
            btn=QPushButton(text)
            btn.clicked.connect(handler)
            row3.addWidget(btn)
        layout.addLayout(row3)
        row4=QHBoxLayout()
        for text,handler in [('Exportar ausentes (.csv)',self.sync_export_missing),
                             ('Exportar para celular',self.sync_export_mobile),
                             ('Abrir playlist .m3u8',self.sync_open_file),
                             ('Salvar modo',self.sync_save_mode),
                             ('Remover do cadastro',self.sync_remove)]:
            btn=QPushButton(text)
            btn.clicked.connect(handler)
            row4.addWidget(btn)
        layout.addLayout(row4)
        self.sync_state=QLabel('Cadastre uma playlist para iniciar. Músicas existentes são reutilizadas; nenhuma faixa é apagada.')
        self.sync_state.setObjectName('sub')
        self.sync_state.setWordWrap(True)
        layout.addWidget(self.sync_state)
        self.sync_tracks=QTableWidget(0,5)
        self._style_table(self.sync_tracks,['#','Título','Artista','Resultado','Qualidade'])
        self.sync_tracks.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch)
        self.sync_tracks.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch)
        layout.addWidget(self.sync_tracks,stretch=1)
        history=QPushButton('Mostrar histórico de sincronizações')
        history.clicked.connect(self.sync_show_history)
        layout.addWidget(history)
        self.stack.addWidget(page)

    def sync_from_dashboard(self):
        self.sync_url.setText(self.quick_playlist.text().strip())
        self._switch(9)
        if self.sync_url.text(): self.sync_add()

    def sync_add(self):
        url=self.sync_url.text().strip()
        try:
            ident=self.sync_store.add(url,mode=self.sync_mode.currentData())
            self.refresh_sync_list(select_id=ident)
            self.sync_state.setText('Playlist cadastrada. Clique em Sincronizar selecionada para localizar FLACs.')
        except Exception as exc:
            QMessageBox.warning(self,APP,str(exc))

    def sync_selected(self):
        row=self.sync_table.currentRow()
        if row<0: return None
        item=self.sync_table.item(row,0)
        return item.data(Qt.UserRole) if item else None

    def refresh_sync_list(self, select_id=None):
        items=self.sync_store.list()
        if select_id is None: select_id=self.sync_selected()
        self.sync_table.blockSignals(True)
        self.sync_table.setRowCount(len(items))
        chosen=-1
        for row,p in enumerate(items):
            if p['id']==select_id: chosen=row
            for col,value in enumerate((p['name'],MODES.get(p['mode'],p['mode']),p['last_count'],
                                         p['last_found'],p['last_pending'],p['last_sync'][:19].replace('T',' '),
                                         ('Falha: '+p['last_error'][:64] if p['last_error'] else 'Pronta'))):
                it=QTableWidgetItem(str(value))
                if col==0: it.setData(Qt.UserRole,p['id'])
                self.sync_table.setItem(row,col,it)
        if chosen<0 and items: chosen=0
        if chosen>=0: self.sync_table.selectRow(chosen)
        self.sync_table.blockSignals(False)
        self.sync_select()

    def sync_select(self):
        ident=self.sync_selected()
        if not ident:
            self.sync_tracks.setRowCount(0)
            return
        p=self.sync_store.get(ident)
        self.sync_mode.blockSignals(True)
        self.sync_mode.setCurrentIndex(max(0,self.sync_mode.findData(p['mode'])))
        self.sync_mode.blockSignals(False)
        songs=self.sync_store.tracks(ident)
        self.sync_tracks.setRowCount(len(songs))
        for row,s in enumerate(songs):
            for col,value in enumerate((row+1,s.title,s.artist,s.status,s.display_quality())):
                self.sync_tracks.setItem(row,col,QTableWidgetItem(str(value)))

    def sync_save_mode(self):
        ident=self.sync_selected()
        if not ident:
            QMessageBox.information(self,APP,'Selecione uma playlist.');return
        new_mode=self.sync_mode.currentData()
        old_mode=self.sync_store.get(ident)['mode']
        if new_mode=='rebuild' and old_mode!=new_mode:
            if QMessageBox.question(self,APP,'Reconstruir removerá referências antigas da playlist local, mas nunca arquivos de áudio. Continuar?',
                                    QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes: return
        self.sync_store.set_mode(ident,new_mode)
        self.refresh_sync_list(select_id=ident)
        self.sync_state.setText('Modo salvo: '+MODES[new_mode])

    def sync_run(self):
        ident=self.sync_selected()
        if not ident:
            QMessageBox.information(self,APP,'Cadastre ou selecione uma playlist.');return
        self.sync_start(ident)

    def sync_run_all(self):
        ids=[p['id'] for p in self.sync_store.list() if p['enabled']]
        if not ids:
            QMessageBox.information(self,APP,'Nenhuma playlist cadastrada.');return
        if self.sync_busy:
            QMessageBox.information(self,APP,'Uma sincronização já está em andamento.');return
        self.sync_auto_queue=list(ids)
        self.sync_next()

    def sync_auto_tick(self):
        if self.sync_busy or self.shutting_down: return
        self.sync_auto_queue=[p['id'] for p in self.sync_store.list() if p['enabled']]
        self.sync_next()

    def sync_next(self):
        if not self.sync_busy and self.sync_auto_queue:
            self.sync_start(self.sync_auto_queue.pop(0))

    def set_sync_interval(self,*_):
        if not hasattr(self,'sync_timer'): return
        minutes=int(self.sync_interval.currentData() or 0)
        self.settings.setValue('syncMinutes',minutes)
        self.sync_timer.stop()
        if minutes: self.sync_timer.start(minutes*60*1000)

    def sync_start(self,ident):
        if self.sync_busy or self.shutting_down: return
        program=executable('spotdl')
        if not program:
            self.sync_state.setText('Instale o spotDL em instalar.bat antes de sincronizar.')
            return
        try:
            lidarr=self._lidarr_client()
        except Exception:
            lidarr=None
        try:
            jellyfin=self._jellyfin_client()
        except Exception:
            jellyfin=None
        root=Path(self.flac_folder.text()).expanduser()
        dest=self.base/'Playlists FLAC'
        from flac_matcher import export_spotify_playlist
        from uuid import uuid4
        self.sync_busy=True
        self.sync_state.setText('Lendo metadados, validando FLACs locais e montando playlist...')
        tmp=app_data()/'playlists'/(uuid4().hex+'.spotdl')
        def fetch(url):
            try: return export_spotify_playlist(url,program,tmp)
            finally: tmp.unlink(missing_ok=True)
        job=LidarrWorker(lambda:sync_playlist(self.sync_store,ident,fetch,root,dest,lidarr,jellyfin),self)
        self.lidarr_threads.add(job)
        def done(result,error):
            self.sync_busy=False
            if error:
                self.sync_state.setText('Falha na sincronização: '+str(error))
            else:
                self.sync_state.setText(f'{result["name"]}: {result["found"]}/{result["total"]} FLACs locais. '
                    f'{result["pending"]} pendentes • {result["cached"]} reutilizados do cache. '
                    + (' • '.join(result['warnings'][:2]) if result['warnings'] else ''))
                self.refresh_sync_list(select_id=ident)
                if self.sync_store.get(ident)['jellyfin_id']:
                    self.sync_update_jellyfin_background(ident)
            QTimer.singleShot(500,self.sync_next)
        def cleanup():
            self.lidarr_threads.discard(job)
            job.deleteLater()
        job.done.connect(done)
        job.finished.connect(cleanup)
        job.start()

    def sync_request_lidarr(self):
        ident=self.sync_selected()
        if not ident:
            QMessageBox.information(self,APP,'Selecione uma playlist.');return
        songs=self.sync_store.tracks(ident)
        count=len({(f'album:{s.album_id}' if s.album_id else f'catalogue:{s.artist_id}:{s.catalogue_record.get("foreignAlbumId")}') for s in songs if not s.path and s.status != 'FLAC no Jellyfin' and (s.album_id or (s.status=='Catálogo encontrado' and s.artist_id and s.catalogue_record.get('foreignAlbumId')))})
        if count==0:
            QMessageBox.information(self,APP,'Nenhum álbum pendente identificado no Lidarr. Sincronize ou cadastre o artista primeiro.');return
        if QMessageBox.question(self,'Lidarr',f'Solicitar busca de até {count} álbum(ns) no Lidarr? '
             'As fontes/indexadores configurados determinarão disponibilidade e legalidade. FLAC não é garantido.',
             QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes: return
        try: client=self._lidarr_client()
        except Exception as exc:
            QMessageBox.warning(self,APP,str(exc));return
        self._thread_lidarr(lambda:request_new_albums(self.sync_store,ident,client,None),
            lambda r:(self.refresh_sync_list(select_id=ident),
                self.sync_state.setText(f'{len(r.get("submitted",[]))} álbuns solicitados. '+str(r.get('skipped','')))), 'Sincronização')

    def sync_export_missing(self):
        ident=self.sync_selected()
        if not ident: return
        path,_=QFileDialog.getSaveFileName(self,'Exportar músicas ausentes',str(self.base/'ausentes.csv'),'CSV (*.csv)')
        if path:
            f=export_missing_csv(self.sync_store.tracks(ident),path)
            self.sync_state.setText('Relatório salvo em: '+str(f))

    def sync_export_mobile(self):
        ident=self.sync_selected()
        if not ident:
            QMessageBox.information(self,APP,'Selecione uma playlist.');return
        p=self.sync_store.get(ident)
        songs=self.sync_store.tracks(ident)
        if not any(s.path and s.status=='FLAC confirmado' for s in songs):
            QMessageBox.information(self,APP,'Não há FLACs locais confirmados para exportar.');return
        path=QFileDialog.getExistingDirectory(self,'Pasta de destino no celular/PC',str(self.base))
        if not path: return
        from flac_matcher import export_mobile_package
        self.sync_state.setText('Preparando pacote portátil com FLACs locais, capas e .m3u8...')
        def ready(r):
            self.sync_state.setText(f'Pacote pronto: {r["tracks"]} posições, {r["unique_files"]} arquivos únicos. '+r['playlist'])
        self._thread_lidarr(lambda:export_mobile_package(songs,Path(path),p['name']),ready,'Exportação para celular')

    def sync_open_file(self):
        ident=self.sync_selected()
        if not ident: return
        f=Path(self.sync_store.get(ident)['output_path'])
        if f.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(f)))
        else:
            QMessageBox.information(self,APP,'Execute Sincronizar primeiro para criar a playlist .m3u8.')

    def sync_remove(self):
        ident=self.sync_selected()
        if not ident:return
        if self.sync_busy:
            QMessageBox.information(self,APP,'Aguarde terminar a sincronização.');return
        if QMessageBox.question(self,APP,'Remover a playlist do cadastro e o histórico? Arquivos FLAC não serão excluídos.',
                                 QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes: return
        self.sync_store.remove(ident)
        self.refresh_sync_list()

    def sync_show_history(self):
        ident=self.sync_selected()
        if not ident:return
        entries=self.sync_store.history(ident)
        msg='\n'.join(f'{h["at"]}: {h["event"]} — {h["detail"]}' for h in entries) or 'Nenhuma execução.'
        QMessageBox.information(self,'Histórico de sincronização',msg[:3800])

    def sync_publish_jellyfin(self):
        ident=self.sync_selected()
        if not ident:
            QMessageBox.information(self,APP,'Selecione uma playlist.');return
        try: client=self._jellyfin_client()
        except Exception as exc:
            QMessageBox.warning(self,'Jellyfin',str(exc));return
        songs=self.sync_store.tracks(ident)
        if not songs:
            QMessageBox.information(self,'Jellyfin','Sincronize a playlist antes de publicá-la.');return
        if QMessageBox.question(self,'Jellyfin','Criar/atualizar playlist no Jellyfin usando as faixas encontradas? '
          'No modo Espelhar/Reconstruir, referências antigas sairão da playlist Jellyfin, mas os arquivos não serão apagados.',
          QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes: return
        self.sync_state.setText('Conferindo Jellyfin e sincronizando itens...')
        def task():
            return self._sync_jellyfin_now(client,ident)
        def ready(r):
            self.sync_state.setText(f'Jellyfin atualizado: {r["count"]} referências de música. '+r['mode'])
        self._thread_lidarr(task,ready,'Jellyfin')

    def _sync_jellyfin_now(self,client,ident):
        from jellyfin_api import link_jellyfin_songs
        item=self.sync_store.get(ident)
        songs=self.sync_store.tracks(ident)
        link_jellyfin_songs(songs,client.music())
        ids=[s.jellyfin_id for s in songs if s.jellyfin_id]
        if not ids: raise JellyfinError('Nenhuma música correspondente encontrada no Jellyfin.')
        if item['jellyfin_id']:
            result=client.sync_playlist_items(item['jellyfin_id'],ids,item['mode'])
        else:
            result=client.create_playlist(item['name'],ids)
            self.sync_store.set_jellyfin_id(ident,result['id'])
        self.sync_store.event(ident,'Jellyfin',f'{len(ids)} referências, modo {item["mode"]}')
        return {'count':len(ids),'mode':item['mode'],'id':result['id']}

    def sync_update_jellyfin_background(self,ident):
        try: client=self._jellyfin_client()
        except Exception: return
        def ready(info):
            self.sync_state.setText(self.sync_state.text()+f' • Jellyfin: {info["count"]} faixas.')
        # avoid blocking GUI; errors stay visible in status via existing helper
        self._thread_lidarr(lambda:self._sync_jellyfin_now(client,ident),ready,'Jellyfin')

    def _switch(self, page: int):
        self.stack.setCurrentIndex(page)
        for i, b in enumerate(self.nav):
            b.setChecked(i == page)

    def _refresh_deps(self):
        names = ('tiddl', 'spotdl', 'ffmpeg')
        missing = [n for n in names if not executable(n)]
        self.dep.setText('Ferramentas prontas' if not missing else 'Faltando: ' + ', '.join(missing))
        return missing

    def choose_base(self) -> Path:
        raw = self.path_box.text().strip()
        if not raw:
            raise ValueError('Escolha uma pasta de destino.')
        root = Path(raw).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def select_folder(self):
        chosen = QFileDialog.getExistingDirectory(self, 'Escolher pasta', self.path_box.text())
        if chosen:
            self.path_box.setText(chosen)

    def save_settings(self):
        try:
            root = self.choose_base()
            self.base = root
            self.settings.setValue('output', str(root))
            self.settings.setValue('covers', 'true' if self.covers.isChecked() else 'false')
            self.settings.setValue('qualityIndex', self.quality.currentIndex())
            QMessageBox.information(self, APP, 'Configurações salvas.')
        except Exception as exc:
            QMessageBox.warning(self, APP, str(exc))

    def make_tiddl_env(self) -> QProcessEnvironment:
        env = QProcessEnvironment.systemEnvironment()
        env.insert('TIDDL_PATH', str(app_data() / 'tiddl'))
        env.insert('PYTHONIOENCODING', 'utf-8')
        return env

    def prepare_tiddl(self, root: Path):
        write_tiddl_config(app_data() / 'tiddl', root / 'TIDAL', str(self.quality.currentData()), self.covers.isChecked())

    def tidal_login(self):
        path = executable('tiddl')
        if not path:
            QMessageBox.warning(self, APP, 'tiddl não instalado. Execute o arquivo instalar.bat primeiro.')
            return
        try:
            root = self.choose_base()
            self.prepare_tiddl(root)
            env = os.environ.copy()
            env['TIDDL_PATH'] = str(app_data() / 'tiddl')
            if os.name == 'nt':
                subprocess.Popen([path, 'auth', 'login'], env=env, creationflags=subprocess.CREATE_NEW_CONSOLE)
            else:
                QMessageBox.information(self, APP, f'Execute no terminal:\nTIDDL_PATH={env["TIDDL_PATH"]} tiddl auth login')
        except Exception as exc:
            QMessageBox.warning(self, APP, f'Falha ao abrir o login: {exc}')

    def add_jobs(self):
        try:
            root = self.choose_base()
            self.base = root
            self.settings.setValue('output', str(root))
            self.settings.setValue('covers', 'true' if self.covers.isChecked() else 'false')
            self.settings.setValue('qualityIndex', self.quality.currentIndex())
            raw_links = [s.strip() for s in self.links.toPlainText().splitlines() if s.strip()]
            if not raw_links:
                QMessageBox.information(self, APP, 'Cole ao menos um link.')
                return
            invalid = []
            count = 0
            for raw in dict.fromkeys(raw_links):
                svc, data = can_submit_url(raw, lossless_only=(self.download_policy.currentData() == 'flac'))
                if not svc:
                    invalid.append(raw + ': ' + data)
                    continue
                row = self.table.rowCount()
                self.table.insertRow(row)
                for col, value in enumerate([svc, data, 'Na fila']):
                    self.table.setItem(row, col, QTableWidgetItem(value))
                self.queue.append((svc, data, row))
                count += 1
                self._update_playlist_status()
            if invalid:
                QMessageBox.warning(self, APP, 'Links ignorados:\n' + '\n'.join(invalid[:8]))
            if count:
                self.links.clear()
                self._start_next()
        except Exception as exc:
            QMessageBox.warning(self, APP, str(exc))

    def _log(self, msg: str):
        self.log.appendPlainText(msg)

    def _status(self, row: int, label: str):
        item = self.table.item(row, 2)
        if item:
            item.setText(label)

    def _start_next(self):
        if self.shutting_down or self.active is not None or not self.queue:
            return
        svc, url, row = self.queue.pop(0)
        bin_name = 'tiddl' if svc == 'TIDAL' else 'spotdl'
        program = executable(bin_name)
        if not program:
            self._status(row, f'Faltando {bin_name}')
            self._log(f'ERRO: {bin_name} não encontrado. Execute instalar.bat.')
            self._start_next()
            return
        try:
            root = self.choose_base()
            folder = root / svc
            folder.mkdir(parents=True, exist_ok=True)
            if svc == 'TIDAL':
                self.prepare_tiddl(root)
                args = ['download', 'url', url, '--quality', str(self.quality.currentData())]
            else:
                # Audio originates from a separate online audio provider, NOT Spotify's lossless catalog.
                # Do not transcode to FLAC: encoding lossy sources as FLAC is misleading.
                args = ['download', url, '--format', 'm4a', '--bitrate', 'disable', '--output', '{artist}/{album}/{track-number} - {title}.{output-ext}']
            p = QProcess(self)
            p.setProgram(program)
            p.setArguments(args)
            p.setWorkingDirectory(str(folder))
            p.setProcessChannelMode(QProcess.MergedChannels)
            if svc == 'TIDAL':
                p.setProcessEnvironment(self.make_tiddl_env())
            self.active = p
            self.active_row = row
            p.readyReadStandardOutput.connect(self._on_output)
            p.errorOccurred.connect(self._process_error)
            p.finished.connect(self._finished)
            self._status(row, 'Executando')
            self._update_playlist_status()
            self.progress.setRange(0, 0)
            self._log(f'\n▶ {svc}: {url}\n  Destino: {folder}')
            if svc == 'Spotify':
                self._log('  Aviso: áudio do spotDL NÃO é lossless verdadeiro.')
            p.start()
        except Exception as exc:
            self._status(row, 'Falha')
            self._log(f'ERRO: {exc}')
            self.active = None
            self._start_next()

    def _on_output(self):
        if self.active:
            data = bytes(self.active.readAllStandardOutput()).decode('utf-8', 'replace')
            # Avoid printing terminal control escape sequences in the GUI.
            data = ANSI.sub('', data).replace('\r', '\n')
            if data.strip():
                self._log(data.strip())

    def _process_error(self, error):
        if self.active is not None:
            self._log('Erro na execução: ' + self.active.errorString())

    def _finished(self, exit_code: int, exit_status):
        self._on_output()
        row = self.active_row
        self._status(row, 'Concluído' if exit_code == 0 else f'Falha (código {exit_code})')
        self._log(f'Processo encerrado com código {exit_code}.')
        if self.active:
            self.active.deleteLater()
        self.active = None
        self._update_playlist_status()
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if exit_code == 0 else 0)
        self._start_next()

    def cancel(self):
        if self.active:
            self._log('Cancelamento solicitado.')
            self.active.kill()
        else:
            self._log('Não há processo em execução.')

    def open_folder(self):
        try:
            path = self.choose_base()
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except Exception as exc:
            QMessageBox.warning(self, APP, str(exc))

    def scan_library(self):
        try:
            root = self.choose_base()
            files = [p for p in root.rglob('*') if p.suffix.lower() in {'.flac', '.mp3', '.m4a', '.ogg', '.opus'}]
            if len(files) > 4000:
                files = files[:4000]
                self.summary.setText('Mostrando os 4.000 primeiros arquivos. Exporte ou separe a biblioteca para auditoria completa.')
            self.scan_rows.clear()
            self.files_table.setRowCount(0)
            self.album_cover.setPixmap(QPixmap())
            self.album_cover.setText('♪')
            good = 0
            for path in files:
                try:
                    title, fmt, quality, cover = flac_audit(path)
                    if fmt == 'FLAC':
                        good += 1
                except Exception:
                    title, fmt, quality, cover = path.stem, path.suffix[1:].upper(), 'Erro ao ler', '—'
                row = (title, fmt, quality, cover, str(path))
                self.scan_rows.append(row)
                r = self.files_table.rowCount()
                self.files_table.insertRow(r)
                for col, val in enumerate(row):
                    self.files_table.setItem(r, col, QTableWidgetItem(val))
            self.summary.setText(f'{len(files)} arquivo(s) analisado(s) | {good} FLAC com dados técnicos lidos. Conteúdo lossless não pode ser provado apenas por extensão.')
            self.filter_library()
        except Exception as exc:
            QMessageBox.warning(self, APP, 'Não foi possível analisar: ' + str(exc))

    def export_csv(self):
        if not self.scan_rows:
            QMessageBox.information(self, APP, 'Analise sua biblioteca antes de exportar.')
            return
        dest, _ = QFileDialog.getSaveFileName(self, 'Exportar biblioteca', str(self.base / 'auditoria-musicas.csv'), 'CSV (*.csv)')
        if dest:
            with open(dest, 'w', newline='', encoding='utf-8-sig') as f:
                w = csv.writer(f, delimiter=';')
                w.writerow(['Título', 'Formato', 'Qualidade', 'Capa', 'Arquivo'])
                w.writerows(self.scan_rows)
            QMessageBox.information(self, APP, 'Relatório exportado.')

    def closeEvent(self, event):
        if any(worker.isRunning() for worker in self.lidarr_threads):
            QMessageBox.information(self, APP, 'Existe uma consulta aos serviços em andamento. Aguarde a conclusão antes de fechar.')
            event.ignore()
            return
        if self.active and self.active.state() != QProcess.NotRunning:
            answer = QMessageBox.question(self, APP, 'Há uma tarefa em execução. Encerrar e cancelar o download?')
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.shutting_down = True
            self.queue.clear()
            self.active.kill()
            self.active.waitForFinished(1500)
        event.accept()


def main():
    # Teste de empacotamento: carrega dependências sem iniciar a janela.
    if len(sys.argv) == 2 and sys.argv[1] == '--build-check':
        from PySide6.QtCore import qVersion
        # PyInstaller --windowed pode deixar stdout=None.
        if sys.stdout is not None:
            print(f'Music Library Manager - Qt {qVersion()} - importacoes OK', flush=True)
        return
    # Prioriza ferramentas auxiliares colocadas ao lado do EXE pelo instalador.
    if getattr(sys, 'frozen', False):
        binary_folder = str(Path(sys.executable).resolve().parent)
        os.environ['PATH'] = binary_folder + os.pathsep + os.environ.get('PATH', '')
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setStyleSheet(QSS)
    app.setApplicationName(APP)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
