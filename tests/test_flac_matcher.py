import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flac_matcher import (Song, read_spotdl_metadata, apply_local_matches, match_score,
                          lidarr_album_matches, request_missing_albums, generate_m3u,
                          export_spotify_playlist, MatchError, scan_flacs)


class MatcherTests(unittest.TestCase):
    def test_spotdl_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'p.spotdl'
            p.write_text(json.dumps([{'name':'Song A','artist':'A','artists':['A','B'],
                'album_name':'Album','album_artist':'A','isrc':'USABC123','duration':210},
                {'name':'Song B','artists':['C'],'album_name':'Album 2','duration':203000}]))
            songs = read_spotdl_metadata(p)
            self.assertEqual(len(songs), 2)
            self.assertEqual(songs[0].isrc,'USABC123')
            self.assertEqual(songs[1].duration,203)
            self.assertEqual(songs[1].album_artist,'C')

    def test_reject_blank_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'b.spotdl'
            p.write_text('{}')
            with self.assertRaises(MatchError):
                read_spotdl_metadata(p)

    def test_fuzzy_does_not_accept_remaster(self):
        song = Song(title='Sound', artist='Band', album='Studio', duration=204)
        candidate = dict(title='Sound (Live)',artist='Band',album='Studio',duration=204)
        self.assertEqual(match_score(song,candidate), 0)

    def test_reject_wrong_album_without_isrc(self):
        song = Song(title='Sound',artist='Band',album='Album X')
        candidate = dict(title='Sound',artist='Band',album='Album Y')
        self.assertEqual(match_score(song,candidate), 0)

    def test_accept_isrc_identical_different_album(self):
        song = Song(title='Sound',artist='Band',album='Best of',isrc='CODE')
        candidate = dict(title='Sound',artist='Band',album='Album Y',isrc='CODE')
        self.assertGreater(match_score(song,candidate),80)

    def test_matching_and_resets(self):
        songs = [Song(title='Title',artist='Singer',album='Original',duration=210)]
        candidate = dict(title='Title',artist='Singer',album='Original',duration=209,
                         path='file.flac',bits=24,sample_rate=96000,has_cover=True)
        self.assertEqual(apply_local_matches(songs,[candidate]),1)
        self.assertEqual(songs[0].display_quality(),'24 bit / 96 kHz')
        self.assertEqual(apply_local_matches(songs,[]),0)
        self.assertEqual(songs[0].status,'Pendente')

    def test_lidarr_album_lookup_and_dedupe(self):
        songs = [Song(title='Track 1', artist='Guest',album_artist='Singer',album='Studio'),
                 Song(title='Track 2',artist='Singer',album_artist='Singer',album='Studio'),
                 Song(title='Track 3',artist='Unknown',album='Other')]
        albums=[{'id':14,'title':'Studio','artistId':4}]
        artist=[{'id':4,'artistName':'Singer'}]
        lidarr_album_matches(songs,albums,artist)
        self.assertEqual([s.album_id for s in songs],[14,14,0])
        self.assertEqual(songs[-1].status,'Não catalogada')
        class Client:
            def __init__(self): self.requested=[]
            def search_album(self,id): self.requested.append(id)
        client = Client()
        report = request_missing_albums(client,songs)
        self.assertEqual(report['submitted'],[14])
        self.assertEqual(client.requested,[14])
        self.assertEqual(songs[0].status,'Busca solicitada')

    def test_m3u_only_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp)/'song.flac'; f.write_bytes(b'fLaC')
            songs=[Song(title='Good',artist='A',status='FLAC confirmado',path=str(f)),
                   Song(title='Bad',artist='A',status='Não catalogada')]
            target=Path(tmp)/'list.m3u8'
            self.assertEqual(generate_m3u(songs,target),1)
            text=target.read_text(encoding='utf-8-sig')
            self.assertIn('Good',text)
            self.assertNotIn('Bad',text)

    def test_export_spotdl_is_metadata_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/'saved.spotdl'
            class Result:
                returncode=0
                stderr=''
                stdout=''
            def fake_run(args,**kwargs):
                self.assertEqual(args[1],'save')
                out.write_text('[{"name":"A","artists":["Singer"]}]')
                return Result()
            with patch('subprocess.run',side_effect=fake_run):
                tracks=export_spotify_playlist('https://open.spotify.com/playlist/test','spotdl',out)
            self.assertEqual(tracks[0].title,'A')

    def test_genuine_flac_header_scan(self):
        import numpy as np
        import soundfile as sf
        from mutagen.flac import FLAC
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp)/'good.flac'
            sf.write(str(real),np.zeros((44100,2)),44100,format='FLAC')
            tags=FLAC(real)
            tags['title']=['Song'];tags['artist']=['Band'];tags['album']=['LP']
            tags.save()
            (Path(tmp)/'fake.flac').write_bytes(b'fake')
            result, clipped=scan_flacs(Path(tmp))
            self.assertFalse(clipped)
            self.assertEqual(len(result),1)
            self.assertEqual(result[0]['title'],'Song')
            self.assertEqual(result[0]['bits'],16)

if __name__=='__main__': unittest.main()
