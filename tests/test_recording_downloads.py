"""Synthetic download integration test. Requires the Linux/ROS 2 Humble runtime."""
import http.client
import io
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
from threading import Thread
import time
import unittest
from zipfile import ZipFile

from bag_replay import BagCatalog, TOPIC, TYPE
from catalog import Catalog
from cloud import pack
from replay_predictions import Predictions
from server import InspectionHTTP
from web_auth import PasswordGate, save_password


class DownloadsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.recordings = self.root / 'recordings'
        self.recordings.mkdir()
        self.session = self.recordings / 'session-a'
        self.session.mkdir()
        self.session.joinpath('rgb.mp4').write_bytes(b'visible data')
        self.session.joinpath('ir.mp4').write_bytes(b'infrared data')
        self.session.joinpath('frame.pwc').write_bytes(struct.pack('<4sIfffBBBB', b'PWC1', 1, 1., 2., 3., 10, 20, 30, 255))
        self.session.joinpath('session.json').write_text(json.dumps(dict(version=1,
            videos={'camera': {'file': 'rgb.mp4'}}, infrared={'camera': {'file': 'ir.mp4'}},
            clouds={'raw': {'frame': 'body', 'frames': [[0, 'frame.pwc']]},
                    'missing': {'frames': [[0, 'missing.pwc']]}})), encoding='utf-8')
        self.bags = BagCatalog(self.root / 'bags')
        self.predictions = Predictions(self.root, self.bags, None)
        password = self.root / 'password.json'
        save_password(password, b'synthetic-fixture-password')
        self.gate = PasswordGate(password)
        self.server = InspectionHTTP(('127.0.0.1', 0), None, self.gate, None)
        self.addCleanup(self.server.server_close)
        self.server.catalog = Catalog(self.recordings)
        self.server.bags = self.bags
        self.server.predictions = self.predictions
        thread = Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.shutdown)
        token = self.gate.login('127.0.0.1', b'synthetic-fixture-password')[1]
        self.headers = {'Cookie': self.gate.cookie(token).split(';', 1)[0]}

    def request(self, path, method='GET', headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            conn.request(method, path, headers=headers if headers is not None else self.headers)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def assert_archive_cleaned(self):
        # A client can read the last byte just before the handler exits its workspace.
        deadline = time.monotonic() + 2
        while any(self.recordings.glob('.download-*')) and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(any(self.recordings.glob('.download-*')))

    def test_independent_tracks_and_restrictions(self):
        root = '/api/sessions/session-a'
        base = root + '/downloads'
        self.assertEqual(self.request(base + '/visible/camera', headers={})[0], 401)
        self.assertEqual(self.request(base + '/pointcloud/raw', headers={**self.headers, 'Sec-Fetch-Site': 'cross-site'})[0], 403)
        for kind, expected in [('visible', b'visible data'), ('infrared', b'infrared data')]:
            status, headers, data = self.request(base + f'/{kind}/camera')
            self.assertEqual((status, data), (200, expected))
            self.assertEqual(headers['Content-Disposition'], f'attachment; filename="session-a-{kind}-camera.mp4"')
        status, headers, data = self.request(base + '/infrared/camera', headers={**self.headers, 'Range':'bytes=1-3'})
        self.assertEqual((status, data), (206, b'nfr'))
        self.assertEqual(headers['Content-Range'], 'bytes 1-3/13')
        self.assertNotIn('Content-Disposition', self.request(root + '/video/camera')[1])
        self.assertEqual(self.request(base + '/pointcloud/raw', method='HEAD')[0], 200)
        self.assertEqual(self.request(base + '/pointcloud/missing')[0], 404)
        self.assertEqual(self.request(base + '/visible/..%2fpassword.json')[0], 404)
        for unknown in ('/infrared/missing', '/visible/missing', '/predictions/raw'):
            self.assertEqual(self.request(base + unknown)[0], 404)
        status, headers, data = self.request(base + '/pointcloud/raw')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'application/zip')
        with ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(set(archive.namelist()), {'pointcloud/000000.pwc', 'session.json'})
            meta = json.loads(archive.read('session.json'))
            self.assertEqual(meta['clouds']['raw']['frame'], 'body')
            self.assertEqual(meta['clouds']['raw']['frames'], [[0, 'pointcloud/000000.pwc']])
            self.assertEqual(archive.read('pointcloud/000000.pwc'), self.session.joinpath('frame.pwc').read_bytes())
        self.assert_archive_cleaned()

    def test_rosbag_only_exports_pointcloud_and_cached_prediction_is_separate(self):
        import rosbag2_py as rosbag
        from rclpy.serialization import serialize_message
        from sensor_msgs.msg import PointCloud2, PointField
        from std_msgs.msg import String

        path = self.root / 'bags' / 'test-run' / 'control_bag'
        path.parent.mkdir(parents=True)
        writer = rosbag.SequentialWriter()
        writer.open(rosbag.StorageOptions(uri=str(path), storage_id='sqlite3'), rosbag.ConverterOptions('', ''))
        writer.create_topic(rosbag.TopicMetadata(name=TOPIC, type=TYPE, serialization_format='cdr'))
        writer.create_topic(rosbag.TopicMetadata(name='/test_status', type='std_msgs/msg/String', serialization_format='cdr'))
        cloud = PointCloud2(height=1, width=1, point_step=12, row_step=12, is_dense=True,
                             data=struct.pack('<fff', 1, 2, 3),
                             fields=[PointField(name=k, offset=i*4, datatype=7, count=1) for i, k in enumerate('xyz')])
        cloud.header.frame_id = 'body'
        cloud.header.stamp.sec = 10
        first = serialize_message(cloud)
        writer.write(TOPIC, first, 10_000_000_000)
        writer.write('/test_status', serialize_message(String(data='must not be downloaded')), 10_000_000_001)
        cloud.header.stamp.sec = 11
        second = serialize_message(cloud)
        writer.write(TOPIC, second, 11_000_000_000)
        del writer
        snapshot = {p.name: p.read_bytes() for p in path.iterdir() if p.is_file()}
        base = '/api/sessions/bag-test-run/downloads/pointcloud/'
        self.assertEqual(self.request(base + 'pointnext')[0], 404)
        status, _, data = self.request(base + 'body')
        self.assertEqual(status, 200)
        unpacked = self.root / 'unpacked'
        with ZipFile(io.BytesIO(data)) as archive:
            self.assertIn('pointcloud/metadata.yaml', archive.namelist())
            self.assertEqual(len(archive.namelist()), 2)
            # Extract only the two expected types from this locally produced archive.
            for name in archive.namelist():
                n = Path(name)
                self.assertEqual(n.parent, Path('pointcloud'))
                self.assertIn(n.suffix, ('.db3', '.yaml'))
                dst = unpacked / n
                dst.parent.mkdir(exist_ok=True, parents=True)
                dst.write_bytes(archive.read(name))
        reader = rosbag.SequentialReader()
        reader.open(rosbag.StorageOptions(uri=str(unpacked / 'pointcloud'), storage_id='sqlite3'), rosbag.ConverterOptions('', ''))
        self.assertEqual([t.name for t in reader.get_all_topics_and_types()], [TOPIC])
        self.assertEqual(reader.read_next(), (TOPIC, first, 10_000_000_000))
        self.assertEqual(reader.read_next(), (TOPIC, second, 11_000_000_000))
        self.assertFalse(reader.has_next())
        self.assertEqual({p.name: p.read_bytes() for p in path.iterdir() if p.is_file()}, snapshot)
        bag = self.bags.scan()[0]['bag-test-run']
        from contextlib import closing
        import numpy as np
        frame = pack(np.array([[1., 2., 3.]]), labels=np.array([1]))
        with closing(sqlite3.connect(self.predictions.destination(bag))) as db:
            db.execute('CREATE TABLE predictions (id INTEGER, data BLOB)')
            db.executemany('INSERT INTO predictions VALUES (?, ?)', ((0, frame), (1, frame)))
            db.commit()
        status, _, data = self.request(base + 'pointnext')
        self.assertEqual(status, 200)
        with ZipFile(io.BytesIO(data)) as archive:
            self.assertEqual(set(archive.namelist()), {'session.json', 'pointcloud/000000.pwc', 'pointcloud/000001.pwc'})
            self.assertEqual(archive.read('pointcloud/000001.pwc')[23], 1)
        self.assert_archive_cleaned()


if __name__ == '__main__':
    unittest.main()
