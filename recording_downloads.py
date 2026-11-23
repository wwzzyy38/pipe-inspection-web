"""Export one selected recording track without changing or replaying its source."""
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
from threading import BoundedSemaphore
from zipfile import ZIP_STORED, ZipFile

from bag_replay import TOPIC, TYPE, connect
from catalog import ID, file_in

_ARCHIVE_SLOT = BoundedSemaphore(1)
_METADATA_ALLOWANCE = 64 * 1024 * 1024


class ArchiveBusy(Exception):
    pass


class ArchiveSpace(Exception):
    pass


@contextmanager
def workspace(root, required):
    if not _ARCHIVE_SLOT.acquire(blocking=False):
        raise ArchiveBusy
    try:
        if shutil.disk_usage(root).free < required + _METADATA_ALLOWANCE:
            raise ArchiveSpace
        with tempfile.TemporaryDirectory(prefix='.download-', dir=root) as tmp:
            yield Path(tmp)
    finally:
        _ARCHIVE_SLOT.release()


def archive_name(session, kind, track, extension):
    if not ID.fullmatch(session) or not ID.fullmatch(track):
        raise KeyError('unknown download')
    return f'{session}-{kind}-{track}.{extension}'


def write_rosbag(bag, destination):
    # Import lazily: downloading existing MP4 and PWC1 does not need rosbag2_py.
    import rosbag2_py as rosbag

    writer = rosbag.SequentialWriter()
    count = 0
    try:
        writer.open(rosbag.StorageOptions(uri=str(destination), storage_id='sqlite3'), rosbag.ConverterOptions('', ''))
        writer.create_topic(rosbag.TopicMetadata(name=TOPIC, type=TYPE, serialization_format='cdr'))
        for i in range(len(bag.parts)):
            with bag.open(i) as db:
                rows = db.execute('''SELECT m.timestamp, m.data FROM messages m
                    JOIN topics t ON t.id=m.topic_id
                    WHERE t.name=? AND t.type=? AND t.serialization_format='cdr'
                    ORDER BY m.timestamp, m.id''', (TOPIC, TYPE))
                for timestamp, data in rows:
                    if not data or len(data) > 16 * 1024 * 1024:
                        raise ValueError('unsupported pointcloud message size')
                    writer.write(TOPIC, data, timestamp)
                    count += 1
        if count != bag.count:
            raise ValueError('pointcloud message count changed')
    finally:
        close = getattr(writer, 'close', None)
        if close is not None:
            close()
        del writer  # Humble may finalize metadata through its destructor.


def zip_rosbag(bag, directory):
    source = directory / 'pointcloud'
    write_rosbag(bag, source)
    files = sorted(source.glob('*.db3'))
    meta = source / 'metadata.yaml'
    if not files or not meta.is_file():
        raise ValueError('pointcloud export did not finalize')
    result = directory / 'recording.zip'
    with ZipFile(result, 'w', ZIP_STORED) as archive:
        for path in [meta, *files]:
            archive.write(path, f'pointcloud/{path.name}')
    return result


def imported_frames(base, samples):
    paths = []
    for _, name in samples:
        path = file_in(base, name, '.pwc')
        if not 8 <= path.stat().st_size <= 480008:
            raise ValueError('unsupported frame size')
        paths.append(path)
    return paths


def zip_track(directory, session, track, frames):
    result = directory / 'recording.zip'
    times = track['times']
    names = []
    with ZipFile(result, 'w', ZIP_STORED) as archive:
        for i, frame in enumerate(frames):
            if i >= len(times):
                raise ValueError('unexpected frame count')
            name = f'pointcloud/{i:06d}.pwc'
            if isinstance(frame, Path):
                archive.write(frame, name)
            else:
                if not 8 <= len(frame) <= 480008 or frame[:4] != b'PWC1':
                    raise ValueError('invalid cached pointcloud frame')
                archive.writestr(name, frame)
            names.append([times[i], name])
        if len(names) != len(times) or not names:
            raise ValueError('incomplete pointcloud track')
        manifest = dict(version=1, title=session['title'], demo=session['demo'], clouds={track['id']:dict(
            label=track['label'], frame=track['frame'], offset=track['offset'],
            segmented=track['segmented'], frames=names)})
        archive.writestr('session.json', json.dumps(manifest, ensure_ascii=False, separators=(',',':')))
    return result


def prediction_frames(path):
    with connect(path) as db:
        rows = db.execute('SELECT id, data FROM predictions WHERE length(data)<=480008 ORDER BY id')
        for i, (row, data) in enumerate(rows):
            if i != row:
                raise ValueError('incomplete prediction track')
            yield data
