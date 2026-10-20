"""Tests use simulated Jellyfin responses; no user credentials or network."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from jellyfin_api import (JellyfinClient,JellyfinError,jellyfin_flac_candidate,
                          link_jellyfin_songs,valid_base_url)
from flac_matcher import Song,generate_m3u,request_missing_albums


def item(title='Dream', artist='The Band', album='First Album', identifier='xx01', path='Z:/music/dream.flac',container='flac', length=201):
    return {'Type':'Audio','Id':identifier,'Name':title,'Artists':[artist], 'Album':album,
            'Path':path,'RunTimeTicks':length*10_000_000,
            'MediaSources':[{'Container':container,'MediaStreams':[{'Type':'Audio','Codec':container}]}]}


class JellyfinTests(unittest.TestCase):
    def test_url_rejects_credentials(self):
        for addr in ['https://user:pass@localhost:8096','ftp://localhost:8096','http://localhost:8096/?token=123']:
            with self.assertRaises(JellyfinError):valid_base_url(addr)

    def test_flac_codec_only(self):
        self.assertIsNone(jellyfin_flac_candidate(item(container='mp3')))
        self.assertEqual(jellyfin_flac_candidate(item())['title'],'Dream')

    def test_metadata_reuses_jellyfin_without_file(self):
        song=Song(title='Dream',artist='The Band',album='First Album',duration=201)
        self.assertEqual(link_jellyfin_songs([song],[item()]),1)
        self.assertEqual(song.status,'FLAC no Jellyfin')
        self.assertEqual(song.jellyfin_id,'xx01')
        self.assertFalse(song.path)

    def test_different_version_and_wrong_isrc_rejected(self):
        song=Song(title='Dream',artist='The Band',album='First Album',duration=201)
        self.assertEqual(link_jellyfin_songs([song],[item(title='Dream (Remix)')]),0)
        self.assertFalse(song.jellyfin_id)

    def test_existing_local_track_no_duplicate_and_order(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'local.flac';p.write_bytes(b'fLaC')
            song=Song(title='Dream',artist='The Band',album='First Album',
                      status='FLAC confirmado',path=str(p),bits=16,sample_rate=44100,duration=201)
            second=Song(title='Missing',artist='Another',album='Not here')
            matched=link_jellyfin_songs([song,second],[item()])
            self.assertEqual(matched,1)
            self.assertEqual(song.path,str(p))
            self.assertEqual(song.status,'FLAC confirmado')
            destination=Path(folder)/'test.m3u8'
            self.assertEqual(generate_m3u([song,second,song],destination),2)
            self.assertEqual(destination.read_text(encoding='utf-8-sig').count(str(p)),2)

    def test_ambiguity_no_wrong_song(self):
        song=Song(title='Dream',artist='The Band',album='First Album',duration=201)
        items=[item(identifier='1'),item(identifier='2')]
        self.assertEqual(link_jellyfin_songs([song],items),0)

    def test_dedupe_lidarr_wont_search_existing_jellyfin(self):
        songs=[Song(title='Dream',artist='The Band',album='First Album',status='FLAC no Jellyfin',album_id=9)]
        class Spy:
            def search_album(self,id):raise AssertionError('Search attempted')
        report=request_missing_albums(Spy(),songs)
        self.assertEqual(report['submitted'],[])

    def test_auth_and_playlist_order(self):
        client=JellyfinClient('http://localhost:8096')
        calls=[]
        def fake_request(method,endpoint,data=None,params=None,authenticated=True):
            calls.append((method,endpoint,data))
            if endpoint.endswith('AuthenticateByName'):
                return {'User':{'Id':'userid','Name':'My User'},'AccessToken':'token123'}
            if endpoint=='/Playlists':return {'Id':'playlist-id'}
            return {}
        with patch.object(client,'request',side_effect=fake_request):
            self.assertEqual(client.authenticate('me','password')['user_id'],'userid')
            created=client.create_playlist('My songs',['track2','track1','track2'])
        self.assertEqual(created['count'],3)
        self.assertEqual(calls[-1][2]['Ids'],['track2','track1','track2'])
        self.assertEqual(calls[-1][2]['IsPublic'],False)

    def test_music_pagination(self):
        client=JellyfinClient('http://localhost:8096','token','userid')
        batches=[]
        def mock_req(method,url,data=None,params=None,authenticated=True):
            batches.append(params['StartIndex'])
            count=500 if params['StartIndex']==0 else 100
            return {'Items':[{'Id':str(n)} for n in range(count)],'TotalRecordCount':600}
        with patch.object(client,'request',side_effect=mock_req):
            rows=client.music(limit=600)
        self.assertEqual(len(rows),600)
        self.assertEqual(batches,[0,500])

if __name__=='__main__': unittest.main()
