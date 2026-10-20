"""Real HTTP protocol tests against in-memory fake servers; no live services."""
import json
import sys
import threading
import tempfile
import unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prowlarr_api import ProwlarrClient, ProwlarrError
from qbit_api import QBittorrentClient, QBittorrentError


class Handler(BaseHTTPRequestHandler):
    calls = []
    qversion = 'v5.0.5'
    payloads = {
        '/api/v1/system/status': {'version': '2.3.0'},
        '/api/v1/indexer': [{'id': 10, 'name': 'Public-domain', 'protocol': 'torrent', 'enable': True}],
        '/api/v1/indexerstatus': [],
        '/api/v1/applications': [{'name': 'Test Sonarr', 'implementation': 'Sonarr', 'syncLevel': 'fullSync'}],
        '/api/v1/applications/schema': [{'name': '', 'implementation': 'Lidarr', 'configContract': 'LidarrSettings', 'fields': [
            {'name': 'baseUrl', 'value': ''}, {'name': 'apiKey', 'value': ''}, {'name': 'prowlarrUrl', 'value': ''},
            {'name': 'syncCategories', 'value': [3000, 3010, 3030]}]}],
        '/api/v1/downloadclient': [{'name': 'qBittorrent'}],
        '/api/v1/health': [],
        '/api/v1/command': {'name': 'ApplicationIndexerSync', 'status': 'queued'},
        '/api/v2/torrents/info': [{'hash': 'a'*40, 'name': 'Legal audio', 'progress': 0.5, 'category': 'music'}],
        '/api/v2/torrents/categories': {'music': {'name': 'music', 'savePath': 'C:/Music'}},
    }

    def log_message(self, *args):
        return

    def send(self, status=200, content=b'', ct='application/json', headers=None):
        self.send_response(status)
        self.send_header('Content-Type', ct)
        for k,v in (headers or {}).items():
            self.send_header(k,v)
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def process(self):
        path = self.path.split('?', 1)[0]
        n = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(n)
        self.calls.append((self.command, path, body, dict(self.headers)))
        if path.startswith('/api/v1/'):
            if self.headers.get('X-Api-Key') != 'prowlarr-secret':
                return self.send(401)
            result = self.payloads.get(path)
            if result is None:
                return self.send(404)
            return self.send(content=json.dumps(result).encode())
        if path == '/api/v2/auth/login':
            form = parse_qs(body.decode())
            if form.get('username') == ['admin'] and form.get('password') == ['secret'] and self.headers.get('Origin', '').startswith('http://'):
                return self.send(content=b'Ok.', ct='text/plain', headers={'Set-Cookie': 'SID=sessionvalue; Path=/'})
            return self.send(200, b'Fails.', 'text/plain')
        if self.headers.get('Cookie') != 'SID=sessionvalue':
            return self.send(403, b'Forbidden', 'text/plain')
        if path == '/api/v2/app/version':
            return self.send(content=self.qversion.encode(), ct='text/plain')
        if path in self.payloads:
            return self.send(content=json.dumps(self.payloads[path]).encode())
        if path.startswith('/api/v2/torrents/') and self.command == 'POST':
            if path.endswith('/add'):
                if b'name="urls"' not in body and b'name="torrents"' not in body:
                    return self.send(400)
            return self.send(content=b'Ok.', ct='text/plain')
        return self.send(404)

    do_GET = process
    do_POST = process


class TestConnections(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.srv.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.thread.join(timeout=2)

    def test_prowlarr_snapshot(self):
        p = ProwlarrClient(self.base, 'prowlarr-secret')
        info = p.snapshot()
        self.assertEqual(info['indexers'][0]['name'], 'Public-domain')
        self.assertEqual(info['applications'][0]['name'], 'Test Sonarr')
        self.assertEqual(info['health'], [])

    def test_prowlarr_sync(self):
        p = ProwlarrClient(self.base, 'prowlarr-secret')
        p.sync_indexers()
        method, path, body, _ = Handler.calls[-1]
        self.assertEqual(method, 'POST')
        self.assertEqual(path, '/api/v1/command')
        self.assertEqual(json.loads(body)['name'], 'ApplicationIndexerSync')

    def test_prowlarr_register_lidarr_schema_based(self):
        p = ProwlarrClient(self.base, 'prowlarr-secret')
        p.register_lidarr('http://localhost:8686', 'test-lidarr-key')
        m, endpoint, body, _ = Handler.calls[-1]
        payload = json.loads(body)
        self.assertEqual((m, endpoint), ('POST', '/api/v1/applications'))
        self.assertEqual(payload['implementation'], 'Lidarr')
        self.assertEqual(payload['syncLevel'], 'fullSync')
        fields = {f['name']: f['value'] for f in payload['fields']}
        self.assertEqual(fields['apiKey'], 'test-lidarr-key')
        self.assertEqual(fields['syncCategories'], [3000, 3010, 3030])

    def test_prowlarr_security(self):
        for url in ('file:///tmp/abc', 'http://user:pw@localhost:9696', 'http://localhost:9696/?key=secret'):
            with self.assertRaises(ProwlarrError):
                ProwlarrClient(url, 'abc')
        with self.assertRaises(ProwlarrError):
            ProwlarrClient(self.base, 'wrong').status()

    def test_qbit_auth_list(self):
        client = QBittorrentClient(self.base, 'admin', 'secret')
        snap = client.snapshot()
        self.assertEqual(snap['version'], Handler.qversion)
        self.assertEqual(snap['torrents'][0]['category'], 'music')
        self.assertIn('music', snap['categories'])
        self.assertIn(('POST', '/api/v2/auth/login'), [(m,p) for m,p,*_ in Handler.calls])

    def test_qbit_control_v5(self):
        Handler.qversion = 'v5.0.5'
        client = QBittorrentClient(self.base, 'admin', 'secret')
        client.control('pause', 'a'*40)
        self.assertEqual(Handler.calls[-1][1], '/api/v2/torrents/stop')
        client.control('resume', 'a'*40)
        self.assertEqual(Handler.calls[-1][1], '/api/v2/torrents/start')
        client.control('recheck', 'a'*40)
        self.assertEqual(Handler.calls[-1][1], '/api/v2/torrents/recheck')

    def test_qbit_control_v4(self):
        Handler.qversion = 'v4.6.7'
        client = QBittorrentClient(self.base, 'admin', 'secret')
        client.control('pause', 'a'*40)
        self.assertEqual(Handler.calls[-1][1], '/api/v2/torrents/pause')
        client.control('resume', 'a'*40)
        self.assertEqual(Handler.calls[-1][1], '/api/v2/torrents/resume')
        Handler.qversion = 'v5.0.5'

    def test_qbit_add_magnet_file_and_category(self):
        client = QBittorrentClient(self.base, 'admin', 'secret')
        client.create_category('music')
        self.assertEqual(Handler.calls[-1][1], '/api/v2/torrents/createCategory')
        client.add_torrent('magnet:?xt=urn:btih:'+'a'*40, 'music')
        self.assertIn(b'name="category"', Handler.calls[-1][2])
        self.assertIn(b'name="urls"', Handler.calls[-1][2])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, 'legal.torrent')
            path.write_bytes(b'd3:foo3:bare')
            client.add_torrent(category='music', torrent_file=path)
            self.assertIn(b'legal.torrent', Handler.calls[-1][2])
        with self.assertRaises(QBittorrentError):
            client.add_torrent('javascript:alert(1)', 'music')

    def test_qbit_security(self):
        with self.assertRaises(QBittorrentError):
            QBittorrentClient('http://user:pw@localhost:8080', 'admin', 'x')
        with self.assertRaises(QBittorrentError):
            QBittorrentClient(self.base, 'admin', 'bad').version()
        c = QBittorrentClient(self.base, 'admin', 'secret')
        with self.assertRaises(QBittorrentError):
            c.control('pause', 'all')  # must target one torrent; prevent accidental all-pause
        with self.assertRaises(QBittorrentError):
            c.control('delete', 'a'*40)  # no deletion implementation


if __name__ == '__main__':
    unittest.main()
