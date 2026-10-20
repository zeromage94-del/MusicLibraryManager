import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from flac_matcher import Song,export_mobile_package,MatchError

class MobileTests(unittest.TestCase):
    def test_valid_flac_no_duplicate_copy(self):
        import numpy as np
        import soundfile as sf
        from mutagen.flac import FLAC
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp)
            source=tmp/'source'/'song.flac'; source.parent.mkdir()
            sf.write(str(source),numpy_array:=np.zeros(44100),44100,format='FLAC')
            tags=FLAC(source);tags['artist']=['Solo'];tags['album']=['Album'];tags['title']=['Same song'];tags.save()
            song=Song(title='Same song',artist='Solo',album='Album',duration=1,
                      status='FLAC confirmado',path=str(source),bits=16,sample_rate=44100)
            skipped=Song(title='Missing',artist='Band')
            report=export_mobile_package([song,song,skipped],tmp/'phone','My Playlist')
            self.assertEqual((report['tracks'],report['unique_files'],report['excluded']),(2,1,1))
            playlist=Path(report['playlist']).read_text(encoding='utf-8-sig')
            self.assertEqual(playlist.count('#EXTINF'),2)
            self.assertEqual(len(list((tmp/'phone'/'Music').rglob('*.flac'))),1)
            self.assertNotIn(str(source),playlist)
            rels=[line for line in playlist.splitlines() if line and not line.startswith('#')]
            self.assertEqual(rels[0],rels[1])
            self.assertTrue((tmp/'phone'/rels[0]).exists())
            self.assertEqual(FLAC(tmp/'phone'/rels[0])['title'][0],'Same song')

    def test_nonexistent_files_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            song=Song(title='A',artist='B',path=str(Path(tmp)/'gone.flac'),status='FLAC confirmado')
            with self.assertRaises(MatchError):
                export_mobile_package([song],Path(tmp)/'out')

    def test_blank_selection_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(MatchError):
                export_mobile_package([],Path(tmp)/'out')

if __name__=='__main__':unittest.main()
