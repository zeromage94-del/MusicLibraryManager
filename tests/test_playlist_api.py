import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playlist_api import identify_media_url, is_playlist_link, can_submit_url

class PlaylistTests(unittest.TestCase):
    def test_spotify_playlist(self):
        url='https://open.spotify.com/playlist/37i9dQZF1E8UXBoz02kGID?si=abc'
        self.assertEqual(identify_media_url(url)[0],'Spotify')
        self.assertTrue(is_playlist_link(url))

    def test_tidal_playlist(self):
        for url in ('https://tidal.com/playlist/abc','https://listen.tidal.com/browse/playlist/abc', 'https://tidal.com/mix/abc'):
            self.assertEqual(identify_media_url(url)[0],'TIDAL')
            self.assertTrue(is_playlist_link(url))

    def test_spotify_international_link(self):
        self.assertTrue(is_playlist_link('https://open.spotify.com/intl-pt/playlist/abc'))

    def test_avoid_fake_or_hijacked_host(self):
        for url in ('https://open.spotify.com.evil.com/playlist/abc', 'file:///tmp/playlist',
                    'https://user:pass@open.spotify.com/playlist/abc', 'https://evil.org/playlist/abc'):
            self.assertFalse(is_playlist_link(url))

    def test_album_is_not_playlist(self):
        self.assertFalse(is_playlist_link('https://open.spotify.com/album/abc'))
        self.assertEqual(identify_media_url('https://open.spotify.com/album/abc')[0],'Spotify')

    def test_flac_only_does_not_upscale_spotify(self):
        url='https://open.spotify.com/playlist/abc'
        self.assertEqual(can_submit_url(url,lossless_only=True)[0],'')
        self.assertEqual(can_submit_url(url,lossless_only=False)[0],'Spotify')
        self.assertEqual(can_submit_url('https://tidal.com/playlist/abc',lossless_only=True)[0],'TIDAL')

if __name__=='__main__': unittest.main()
