"""Offline tests. No Spotify, Jellyfin or Lidarr credentials required."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import numpy as np
import soundfile as sf
from mutagen.flac import FLAC
from flac_matcher import Song
from playlist_sync import SyncStore, merge_playlist, cache_reuse, sync_playlist, request_new_albums, playlist_key, export_missing_csv
from jellyfin_api import JellyfinClient,JellyfinError

URL='https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M'

def track(name='Song',artist='Band',album='Record',isrc=''):
    return Song(title=name,artist=artist,album=album,album_artist=artist,duration=1,isrc=isrc)

def flac(path, name='Song',artist='Band',album='Record'):
    sf.write(str(path),np.zeros(44100),44100,format='FLAC')
    f=FLAC(path)
    f['title']=[name]; f['artist']=[artist]; f['album']=[album]
    f.save()

class PlaylistSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.music=self.root/'music';self.music.mkdir()
        self.output=self.root/'playlists';self.output.mkdir()
        self.store=SyncStore(self.root/'state.sqlite')
        self.id=self.store.add(URL,'Minha Lista')

    def test_valid_and_invalid_urls(self):
        self.assertEqual(playlist_key(URL),self.id)
        self.assertEqual(playlist_key(URL+'?si=tracking'),self.id)
        with self.assertRaises(ValueError):playlist_key('https://evilspotify.com/playlist/x')
        with self.assertRaises(ValueError):playlist_key('https://open.spotify.com/album/abc')

    def test_first_scan_and_reuse_no_duplicate_audio(self):
        f=self.music/'source.flac';flac(f)
        first=sync_playlist(self.store,self.id,lambda url:[track(),track(),track('Missing')],self.music,self.output)
        self.assertEqual((first['found'],first['pending'],first['total']),(2,1,3))
        playlist=Path(first['output']).read_text(encoding='utf-8-sig')
        self.assertEqual(playlist.count(str(f)),2)
        self.assertEqual(len(list(self.music.rglob('*.flac'))),1)
        second=sync_playlist(self.store,self.id,lambda url:[track(),track(),track('Missing')],self.music,self.output)
        self.assertEqual(second['found'],2)
        self.assertEqual(second['cached'],2)
        self.assertEqual(len(self.store.tracks(self.id)),3)

    def test_add_only_preserves_originals(self):
        self.store.save_snapshot(self.id,[track('A'),track('B')],self.output)
        self.store.set_mode(self.id,'add_only')
        report=sync_playlist(self.store,self.id,lambda url:[track('B'),track('C')],self.music,self.output)
        self.assertEqual([s.title for s in report['songs']],['A','B','C'])
        followup=sync_playlist(self.store,self.id,lambda url:[track('B'),track('C')],self.music,self.output)
        self.assertEqual([s.title for s in followup['songs']],['A','B','C'])

    def test_add_only_does_not_duplicate_existing_entry_count(self):
        self.store.save_snapshot(self.id,[track('A')],self.output)
        self.store.set_mode(self.id,'add_only')
        result=sync_playlist(self.store,self.id,lambda url:[track('A'),track('A')],self.music,self.output)
        self.assertEqual(len(result['songs']),2)

    def test_mirror_changes_order_and_keeps_audio(self):
        f=self.music/'original.flac';flac(f,name='A')
        sync_playlist(self.store,self.id,lambda url:[track('A'),track('B')],self.music,self.output)
        result=sync_playlist(self.store,self.id,lambda url:[track('B')],self.music,self.output)
        self.assertEqual([s.title for s in result['songs']],['B'])
        self.assertTrue(f.exists())
        self.assertNotIn(str(f),Path(result['output']).read_text(encoding='utf-8-sig'))

    def test_failed_read_preserves_previous_snapshot(self):
        sync_playlist(self.store,self.id,lambda url:[track('A')],self.music,self.output)
        before=self.store.tracks(self.id)
        with self.assertRaises(ValueError):sync_playlist(self.store,self.id,lambda _:[],self.music,self.output)
        self.assertEqual([s.title for s in self.store.tracks(self.id)],[s.title for s in before])
        self.assertIn('Nenhuma música',self.store.get(self.id)['last_error'])

    def test_cache_rejects_missing_and_wrong_source(self):
        old=track('A');old.path=str(self.music/'unavailable.flac')
        incoming=[track('A')]
        self.assertEqual(cache_reuse(incoming,[old]),0)
        wrong=self.music/'wrong.flac';flac(wrong,name='B')
        old.path=str(wrong)
        self.assertEqual(cache_reuse(incoming,[old]),0)

    def test_missing_export(self):
        path=export_missing_csv([track('Missing'),Song(title='A',artist='B',status='FLAC confirmado')],self.root/'missing.csv')
        data=path.read_text(encoding='utf-8-sig')
        self.assertIn('Missing',data)
        self.assertNotIn('A;B;',data)

    def test_persistent_dedupe_of_lidarr_requests(self):
        s1=track('A');s1.album_id=42;s1.status='Álbum no Lidarr'
        s2=track('B');s2.album_id=42;s2.status='Álbum no Lidarr'
        self.store.save_snapshot(self.id,[s1,s2],self.output)
        class Lidarr:
            def __init__(self):self.calls=[]
            def search_album(self,id):self.calls.append(id)
        cli=Lidarr()
        first=request_new_albums(self.store,self.id,cli,None)
        second=request_new_albums(self.store,self.id,cli,None)
        self.assertEqual(cli.calls,[42])
        self.assertEqual(first['submitted'],[42])
        self.assertEqual(second['submitted'],[])
        self.assertEqual([s.album_id for s in self.store.tracks(self.id)],[42,42])

    def test_mode_validation(self):
        with self.assertRaises(ValueError):self.store.set_mode(self.id,'delete_everything')

    def test_events_and_persistence(self):
        self.store.event(self.id,'Ação','Observação')
        reopened=SyncStore(self.root/'state.sqlite')
        self.assertEqual(reopened.history(self.id)[0]['detail'],'Observação')
        reopened.remove(self.id)
        self.assertFalse(reopened.list())

class JellyfinSyncTests(unittest.TestCase):
    def setUp(self):
        self.client=JellyfinClient('http://localhost:8096','secret','user')
        self.calls=[]

    def stub(self,mode,existing,wanted):
        def request(method,endpoint,data=None,params=None,authenticated=True):
            self.calls.append((method,endpoint,params))
            if method=='GET':return {'Items':existing,'TotalRecordCount':len(existing)}
            return {}
        with patch.object(self.client,'request',side_effect=request):
            return self.client.sync_playlist_items('pl',wanted,mode)

    def test_add_only_idempotent_and_duplicates(self):
        data=[{'Id':'a','PlaylistItemId':'entry1'},{'Id':'b','PlaylistItemId':'entry2'}]
        result=self.stub('add_only',data,['a','b','b'])
        self.assertEqual(result['added'],1)
        self.assertEqual(self.calls[-1][2]['Ids'],'b')
        self.calls=[]
        self.stub('add_only',data,['a','b'])
        self.assertEqual(len(self.calls),1)

    def test_mirror_removes_references_not_audio(self):
        r=self.stub('full_sync',[{'Id':'a','PlaylistItemId':'entry1'}],['b','c','b'])
        self.assertEqual(r['removed'],1)
        self.assertEqual([a[0] for a in self.calls],['GET','POST','DELETE'])
        self.assertEqual(self.calls[1][2]['Ids'],'b,c,b')

    def test_missing_entry_id_abort_and_preserve(self):
        with self.assertRaises(JellyfinError):self.stub('rebuild',[{'Id':'a'}],['b'])
        self.assertEqual(len(self.calls),1)

    def test_blank_list_never_erases(self):
        with self.assertRaises(JellyfinError):self.stub('full_sync',[{'Id':'a','PlaylistItemId':'1'}],[])
        self.assertEqual(self.calls,[])

if __name__=='__main__':unittest.main()
