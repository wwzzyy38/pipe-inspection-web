"""Compute native bag segmentation offline; never initializes ROS publishers."""
import json
import os
import sqlite3
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree
from bag_replay import BagCatalog
from cloud import encode, pack
from pipe_pointcloud_recognition.inference_node import Segmenter, decode


def main():
    root, bag_id, signature, directory = sys.argv[1:]
    working = Path(directory)
    bags = BagCatalog(root)
    bag = bags.scan()[0][bag_id]
    if repr(bag.fingerprint) != signature:
        raise ValueError('source bag changed before prediction')
    index = bags.index(bag)
    net = Segmenter(os.environ['PIPE_POINT_UPSTREAM'], os.environ['PIPE_POINT_MODEL'])
    db = sqlite3.connect(working/'frames.db3')
    try:
        db.execute('CREATE TABLE predictions (id INTEGER PRIMARY KEY, data BLOB NOT NULL)')
        count = len(index.refs)
        for i in range(count):
            msg = index.message(i)
            if msg.header.frame_id != 'body' or index.frame_id != msg.header.frame_id:
                raise ValueError(f'Unexpected frame at index {i}')
            xyz = decode(msg)
            xyz = xyz[(np.abs(xyz)<1e7).all(axis=1)]
            if len(xyz)<3:
                raise ValueError(f'Not enough finite points at frame {i}')
            labels, _ = net.predict(xyz)
            raw, n = encode(msg)
            preview = np.ndarray((n,3),dtype='<f4',buffer=raw,offset=8,strides=(16,4))
            if not np.array_equal(preview, xyz):
                dist, keep = cKDTree(xyz).query(preview,k=1)
                if np.any(dist>1e-5):
                    raise ValueError(f'Prediction does not match raw geometry at frame {i}')
                labels = labels[keep]
            if np.any(labels>7) or len(labels)!=n:
                raise ValueError(f'Invalid labels at frame {i}')
            db.execute('INSERT INTO predictions VALUES (?,?)',(i,pack(preview,labels=labels)))
            if i==0 or (i+1)%5==0 or i+1==count:
                db.commit()
                tmp=working/'progress.new'
                tmp.write_text(str(i+1),encoding='ascii')
                tmp.replace(working/'progress')
        assert db.execute('SELECT COUNT(*) FROM predictions').fetchone()==(count,)
        db.commit()
    finally:
        db.close()
    print(json.dumps({'bag':bag_id,'frames':count}),flush=True)

if __name__=='__main__':
    main()
