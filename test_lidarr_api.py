"""Tests run with stdlib unittest and do not require an actual Lidarr instance."""
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lidarr_api import LidarrClient, LidarrError


class LidarrMock(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *args):
        pass

    def _handle(self):
        if self.headers.get('X-Api-Key') != 'test-only-secret':
            self.send_response(401)
            self.end_headers()
            return
        data = {'/api/v1/system/status': {'version': '2.test'},
                '/api/v1/artist': [{'id': 11, 'artistName': 'Demo'}],
                '/api/v1/album': [{'id': 77, 'title': 'Demo Album', 'artistId': 11}],
                '/api/v1/queue': {'records': [{'title': 'Demo Album', 'size': 100, 'sizeleft': 30}]},
                '/api/v1/qualityprofile': [{'name': 'FLAC'}],
                '/api/v1/importlist': [],
                '/api/v1/downloadclient': [],
                '/api/v1/downloadclient/schema': [{'implementation': 'QBittorrent', 'configContract': 'QBittorrentSettings',
                    'fields': [{'name': name, 'value': None} for name in ('host', 'port', 'useSsl', 'username', 'password', 'category')]}],
                '/api/v1/command': {'name': 'AlbumSearch', 'status': 'queued'}}
        path = self.path.split('?', 1)[0]
        if path not in data:
            self.send_response(404)
            self.end_headers()
            return
        request_body = self.rfile.read(int(self.headers.get('Content-Length', 0))) if self.command == 'POST' else b''
        self.calls.append((self.command, path, json.loads(request_body) if request_body else None))
        body = json.dumps(data[path]).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _handle
    do_POST = _handle


class TestLidarr(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), LidarrMock)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_snapshot(self):
        client = LidarrClient(self.url, 'test-only-secret')
        data = client.snapshot()
        self.assertEqual(data['status']['version'], '2.test')
        self.assertEqual(len(data['artists']), 1)
        self.assertEqual(data['queue'][0]['title'], 'Demo Album')
        self.assertEqual(data['profiles'][0]['name'], 'FLAC')
        self.assertEqual(data['lists'], [])

    def test_album_search_payload(self):
        client = LidarrClient(self.url, 'test-only-secret')
        self.assertEqual(client.search_album(77)['status'], 'queued')
        self.assertIn(('POST', '/api/v1/command', {'name': 'AlbumSearch', 'albumIds': [77]}), LidarrMock.calls)
        with self.assertRaises(LidarrError):
            client.search_album(-1)

    def test_sync_import_list(self):
        client = LidarrClient(self.url, 'test-only-secret')
        client.sync_import_list(22)
        self.assertIn(('POST', '/api/v1/command', {'name': 'ImportListSync', 'definitionId': 22}), LidarrMock.calls)
        with self.assertRaises(LidarrError):
            client.sync_import_list(0)

    def test_rejects_invalid_credentials(self):
        with self.assertRaises(LidarrError) as ctx:
            LidarrClient(self.url, 'invalid').health()
        self.assertIn('401', str(ctx.exception))

    def test_register_qbit_schema_based(self):
        client = LidarrClient(self.url, 'test-only-secret')
        client.register_qbittorrent('http://127.0.0.1:8080', 'admin', 'qpass', 'lidarr')
        method, path, data = LidarrMock.calls[-1]
        self.assertEqual((method, path), ('POST', '/api/v1/downloadclient'))
        self.assertEqual(data['implementation'], 'QBittorrent')
        fields = {f['name']: f['value'] for f in data['fields']}
        self.assertEqual(fields['port'], 8080)
        self.assertEqual(fields['category'], 'lidarr')
        self.assertEqual(fields['password'], 'qpass')

    def test_url_validation(self):
        for addr in ['file:///etc/passwd', 'http://user:secret@127.0.0.1:8686', 'ftp://example.com', 'https://a.com/?apikey=xx']:
            with self.subTest(addr=addr), self.assertRaises(LidarrError):
                LidarrClient(addr, 'key')
        with self.assertRaises(LidarrError):
            LidarrClient('http://localhost:8686', '')


if __name__ == '__main__':
    unittest.main()
