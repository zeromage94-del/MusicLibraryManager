"""Offline integration tests for the live dashboard aggregation and stats."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard_api import collect_dashboard, collect_one, missing_albums, summarize, qbit_metrics


class FakeLidarr:
    def health(self):
        return {'version':'2.0'}
    def artists(self):
        return [{'id':5,'artistName':'Artist 1'}]
    def albums(self):
        return [
            {'title':'Missing','artistId':5,'monitored':True,'statistics':{'trackFileCount':0}},
            {'title':'Imported','monitored':True,'statistics':{'trackFileCount':11}},
            {'title':'Unknown','monitored':True,'statistics':{}},
            {'title':'Not monitored','monitored':False,'statistics':{'trackFileCount':0}},
        ]
    def queue(self):
        return [{'title':'Album download'}]

class FakeProwlarr:
    def status(self):
        return {'version':'3.0'}
    def indexers(self):
        return [{'enable':True}, {'enable':False}]
    def health(self):
        return []

class FakeQbit:
    def version(self):
        return 'v5.0.5'
    def torrents(self):
        return [{'name':'Example FLAC','state':'downloading','progress':0.5,'dlspeed':10240},
                {'name':'Done','state':'uploading','progress':1,'dlspeed':0}]

class OfflineFailure:
    def status(self):
        raise ConnectionError('Offline de propósito')

class DashboardTests(unittest.TestCase):
    def test_three_connected(self):
        data = collect_dashboard({'lidarr':FakeLidarr(),'prowlarr':FakeProwlarr(),'qbit':FakeQbit()})
        self.assertEqual([data[x]['online'] for x in ('lidarr','prowlarr','qbit')],[True]*3)
        metrics=summarize(data)
        self.assertEqual(metrics['artists'],1)
        self.assertEqual(metrics['albums'],4)
        self.assertEqual(metrics['missing'],1)
        self.assertEqual(metrics['lidarr_queue'],1)
        self.assertEqual(metrics['indexers'],1)
        self.assertEqual(metrics['qbit_active'],1)
        self.assertEqual(metrics['download_speed'],10240)

    def test_missing_does_not_count_unknown(self):
        albums=FakeLidarr().albums()
        self.assertEqual([x['title'] for x in missing_albums(albums)], ['Missing'])

    def test_one_service_offline_does_not_block_others(self):
        data = collect_dashboard({'lidarr':FakeLidarr(),'prowlarr':OfflineFailure(),'qbit':FakeQbit()})
        self.assertTrue(data['lidarr']['online'])
        self.assertFalse(data['prowlarr']['online'])
        self.assertIn('Offline',data['prowlarr']['error'])
        self.assertTrue(data['qbit']['online'])

    def test_not_configured_is_unknown_not_zero(self):
        data=collect_dashboard({'lidarr':None,'prowlarr':None,'qbit':None})
        stats=summarize(data)
        self.assertIsNone(stats['missing'])
        self.assertIsNone(stats['qbit_active'])
        self.assertIsNone(stats['artists'])

    def test_transferring_not_synonymous_with_all_pending(self):
        metrics = qbit_metrics([
            {'state':'queuedDL','progress':0.2,'dlspeed':0},
            {'state':'stalledDL','progress':0.5,'dlspeed':0},
            {'state':'downloading','progress':0.5,'dlspeed':1234}])
        self.assertEqual(metrics['pending'],3)
        self.assertEqual(metrics['transferring'],1)
        self.assertEqual(metrics['down_speed'],1234)

    def test_partial_endpoint_error(self):
        class Partial(FakeLidarr):
            def queue(self):
                raise TimeoutError('Fila lenta')
        row=collect_one('lidarr',Partial())
        self.assertTrue(row['online'])
        self.assertIsNone(row['queue'])
        self.assertIn('queue:',row['warnings'][0])

if __name__=='__main__': unittest.main()
