"""Read only PointCloud2 from finalized, uncompressed ROS 2 SQLite bags."""
from collections import OrderedDict
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3
import threading
import time

import yaml
from cloud import encode
from cloud_reference import ODOM_TOPIC, ODOM_TYPE, align, match_pose, odom_pose, stamp_ns

TOPIC = '/cloud_registered_body'
TYPE = 'sensor_msgs/msg/PointCloud2'
RUN_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,58}\Z')
DB_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.db3\Z')
MAX_FRAMES = 100_000


@dataclass(frozen=True)
class Bag:
    id: str
    path: Path
    parts: tuple
    count: int
    fingerprint: tuple

    @property
    def title(self):
        stamp = self.id[4:]
        m = re.fullmatch(r'(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})-(\d+)', stamp)
        if m:
            y, mo, d, h, mi, s, number = m.groups()
            return f'{y}-{mo}-{d} {h}:{mi}:{s} · {number}'
        return stamp


def connect(path):
    return closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True))


def identity(path):
    stat = path.stat()
    return stat.st_ino, stat.st_size, stat.st_mtime_ns


def load_part(bag, name):
    if not isinstance(name, str) or not DB_NAME.fullmatch(name):
        raise ValueError('Invalid SQLite part name')
    path = bag / name
    if path.is_symlink() or not path.is_file() or path.resolve(strict=True) != path or not path.stat().st_size:
        raise ValueError('Missing or invalid SQLite part')
    for suffix in ('-wal', '-journal'):
        sidecar = path.with_name(name + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError('SQLite part not finalized')
    return path


class BagCatalog:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.entries, self.skipped = {}, []
        self.scanned = -10
        self.scan_lock = threading.Lock()
        self.index_lock = threading.Lock()
        self.indexes = OrderedDict()

    def scan(self):
        with self.scan_lock:
            if time.monotonic() - self.scanned < 3:
                return self.entries, self.skipped
            entries, skipped = {}, []
            if self.root.is_dir():
                for run in sorted(self.root.iterdir(), reverse=True)[:500]:
                    if not RUN_ID.fullmatch(run.name) or not run.is_dir() or run.is_symlink():
                        continue
                    # The recorder uses <run>/control_bag; another configured root may hold flat bag directories.
                    bag = run / 'control_bag' if (run / 'control_bag').is_dir() else run
                    meta = bag / 'metadata.yaml'
                    if bag == run and not (meta.exists() or next(run.glob('*.db3'), None)):
                        continue
                    try:
                        if bag.resolve() != bag or meta.is_symlink():
                            raise ValueError('invalid bag path')
                        if not meta.is_file() or not 0 < meta.stat().st_size <= 1024*1024:
                            raise ValueError('missing or incomplete metadata')
                        info = yaml.safe_load(meta.read_text(encoding='utf-8'))['rosbag2_bagfile_information']
                        if info['storage_identifier'] != 'sqlite3' or info.get('compression_mode', '') not in ('', 'none', None):
                            raise ValueError('unsupported format or compression')
                        topics = [i for i in info['topics_with_message_count'] if i['topic_metadata']['name'] == TOPIC]
                        if not topics:
                            continue
                        tm = topics[0]['topic_metadata']
                        count = int(topics[0]['message_count'])
                        names = info['relative_file_paths']
                        if (tm['type'], tm['serialization_format']) != (TYPE, 'cdr') or not 0 < count <= MAX_FRAMES:
                            raise ValueError('invalid PointCloud2 topic or message count')
                        if not isinstance(names, list) or not 0 < len(names) <= 256 or len(set(names)) != len(names):
                            raise ValueError('invalid part list')
                        parts = tuple(load_part(bag, n) for n in names)
                        sig = (identity(meta), *(identity(p) for p in parts))
                        bid = 'bag-' + run.name
                        entries[bid] = Bag(bid, bag, parts, count, sig)
                    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
                        skipped.append(run.name)
            self.entries, self.skipped, self.scanned = entries, skipped, time.monotonic()
            return entries, skipped

    def index(self, bag):
        with self.index_lock:
            key = (bag.id, bag.fingerprint)
            if key not in self.indexes:
                self.indexes[key] = BagIndex(bag)
                if len(self.indexes) > 8:
                    self.indexes.popitem(last=False)
            self.indexes.move_to_end(key)
            return self.indexes[key]


class BagIndex:
    def __init__(self, bag):
        self.bag = bag
        refs = []
        for part_index, part in enumerate(bag.parts):
            with self.open(part_index) as con:
                topics = con.execute('SELECT id FROM topics WHERE name=? AND type=? AND serialization_format=?', (TOPIC, TYPE, 'cdr')).fetchall()
                if not topics:
                    continue
                for (topic,) in topics:
                    for row, ns in con.execute('SELECT id,timestamp FROM messages WHERE topic_id=?', (topic,)):
                        refs.append((ns, part_index, row))
                        if len(refs) > MAX_FRAMES:
                            raise ValueError('point cloud frame limit exceeded')
        if not refs or len(refs) != bag.count:
            raise ValueError('PointCloud2 count does not match completed bag metadata')
        refs.sort()
        self.refs = []
        for ref in refs:
            if self.refs and ref[0] == self.refs[-1][0]:
                self.refs[-1] = ref
            else:
                self.refs.append(ref)
        start = self.refs[0][0]
        times = [(ns - start) / 1e9 for ns, _, _ in self.refs]
        frame = self.message(0).header.frame_id
        if not frame:
            raise ValueError('missing source coordinate frame')
        self.frame_id = frame
        self.reference_lock=threading.Lock()
        self.reference_cache=None
        self.session = dict(id=bag.id, title=bag.title, demo=False, kind='rosbag2', videos=[], sync=False,
                            clouds=[dict(id='body', label=TOPIC, topic=TOPIC, offset=0, times=times,
                                         frame=frame[:64], segmented=False)])

    def open(self, part_index):
        part = self.bag.parts[part_index]
        if load_part(self.bag.path, part.name) != part or identity(part) != self.bag.fingerprint[part_index + 1]:
            raise ValueError('source bag changed; refresh playback')
        return connect(part)

    def message(self, index):
        from rclpy.serialization import deserialize_message
        from sensor_msgs.msg import PointCloud2
        _, part_index, row = self.refs[index]
        with self.open(part_index) as con:
            result = con.execute('SELECT data FROM messages WHERE id=? AND length(data)<=?', (row, 16*1024*1024)).fetchone()
        if not result:
            raise ValueError('invalid cloud message length or id')
        return deserialize_message(result[0], PointCloud2)

    def frame(self, index):
        message = self.message(index)
        if message.header.frame_id != self.frame_id:
            raise ValueError('cloud coordinate frame changed')
        return encode(message)[0]

    def reference(self):
        with self.reference_lock:
            if self.reference_cache is not None:
                return self.reference_cache
            from nav_msgs.msg import Odometry
            from rclpy.serialization import deserialize_message
            poses={}
            if self.frame_id=='body':
                for i in range(len(self.bag.parts)):
                    with self.open(i) as con:
                        ids=con.execute('SELECT id FROM topics WHERE name=? AND type=? AND serialization_format=?', (ODOM_TOPIC,ODOM_TYPE,'cdr')).fetchall()
                        for (topic,) in ids:
                            for (data,) in con.execute('SELECT data FROM messages WHERE topic_id=? AND length(data)<4096', (topic,)):
                                try:
                                    msg=deserialize_message(data,Odometry)
                                    pose=odom_pose(msg);stamp=stamp_ns(msg.header)
                                    if pose is not None and stamp>0:poses[stamp]=pose
                                except (ValueError,RuntimeError):continue
                                if len(poses)>2*MAX_FRAMES: raise ValueError('odometry frame limit exceeded')
            times=sorted(poses);result={'available':False};anchor=None
            if times:
                for i in range(len(self.refs)):
                    try:msg=self.message(i)
                    except (ValueError,RuntimeError):continue
                    if msg.header.frame_id!='body':continue
                    stamp=stamp_ns(msg.header);anchor=match_pose(times,poses,stamp)
                    if anchor is not None:
                        result={'available':True,'anchorIndex':i,'anchorTime':self.session['clouds'][0]['times'][i],'source':'recording'}
                        break
            self.reference_cache=(result, anchor, times, poses)
            return self.reference_cache

    def aligned_frame(self,index,data,msg=None):
        _,anchor,times,poses=self.reference()
        if anchor is None:return None
        if msg is None:msg=self.message(index)
        if msg.header.frame_id!='body':return None
        pose=match_pose(times,poses,stamp_ns(msg.header))
        if pose is None:return None
        return align(data,anchor,pose)
