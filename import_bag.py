"""Build a preview session from selected PointCloud2 tracks (read-only source)."""
import argparse, json, os, re, shutil, tempfile
from pathlib import Path
from cloud import encode


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('bag',type=Path)
    p.add_argument('session',type=Path)
    p.add_argument('--storage',choices=['sqlite3','mcap'],default='sqlite3')
    p.add_argument('--raw',default='/cloud_registered_body')
    p.add_argument('--segmented',default='/pipe_pointcloud/segmented')
    p.add_argument('--hz',type=float,default=5)
    p.add_argument('--video',type=Path,help='H.264 MP4 copied to the new session')
    p.add_argument('--video-offset',type=float,help='Signed seconds from first selected bag message to video time zero; omit if unknown')
    args=p.parse_args()
    if not (0.2<=args.hz<=20): p.error('--hz must be 0.2..20')
    if args.session.exists() or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',args.session.name): p.error('session must be a new ASCII identifier directory')
    if args.raw==args.segmented: p.error('track topics must differ')
    if args.video and args.video.suffix.lower()!='.mp4': p.error('use a browser-compatible .mp4')
    if args.video_offset is not None and (not args.video or not -10**7<=args.video_offset<=10**7): p.error('invalid --video-offset')
    import rosbag2_py as bag
    from rclpy.serialization import deserialize_message
    from sensor_msgs.msg import PointCloud2
    reader=bag.SequentialReader()
    reader.open(bag.StorageOptions(uri=str(args.bag.resolve(strict=True)),storage_id=args.storage),bag.ConverterOptions('',''))
    wanted={args.raw:'raw',args.segmented:'segmented'}
    topics=[x.name for x in reader.get_all_topics_and_types() if x.name in wanted and x.type=='sensor_msgs/msg/PointCloud2' and x.serialization_format=='cdr']
    if not topics: p.error('selected topics are absent or not PointCloud2/CDR')
    reader.set_filter(bag.StorageFilter(topics=topics))
    temp=Path(tempfile.mkdtemp(prefix='.import-',dir=args.session.parent))
    try:
        m=dict(version=1,title=args.session.name,clouds={})
        t0=None; previous={}
        while reader.has_next():
            topic,raw,ns=reader.read_next()
            if t0 is None: t0=ns
            if topic in previous and ns-previous[topic]<1e9/args.hz: continue
            key=wanted[topic]
            msg=deserialize_message(raw,PointCloud2)
            data,count=encode(msg)
            if not count: continue
            track=m['clouds'].get(key)
            if track is None:
                track=dict(label='分割结果' if key=='segmented' else '原始点云',frame=msg.header.frame_id,segmented=key=='segmented',frames=[])
                m['clouds'][key]=track
                (temp/key).mkdir()
            if track['frame'] != msg.header.frame_id: raise ValueError(f'Multiple coordinate frames on {topic}; choose a single-frame topic')
            if len(track['frames'])>=100000: raise ValueError('too many frames; use a lower frequency or a shorter bag')
            path=f'{key}/{len(track["frames"]):06}.pwc'
            (temp/path).write_bytes(data)
            track['frames'].append([(ns-t0)/1e9,path])
            previous[topic]=ns
        if not m['clouds']: raise ValueError('no valid cloud frames')
        if args.video:
            shutil.copyfile(args.video,temp/'camera.mp4')
            m['videos']={'camera':dict(label='相机录像',file='camera.mp4',offset=args.video_offset or 0)}
            if args.video_offset is not None: m['clock']='shared'
        text=json.dumps(m,ensure_ascii=False,separators=(',',':'))
        if len(text.encode())>4*1024*1024: raise ValueError('index too large; lower --hz or split the recording')
        (temp/'session.json').write_text(text,encoding='utf-8')
        # Rename only into a new identifier; an already populated session is not overwritten.
        if args.session.exists(): raise FileExistsError(args.session)
        os.rename(temp,args.session)
        print({k:len(v['frames']) for k,v in m['clouds'].items()})
    finally:
        if temp.exists(): shutil.rmtree(temp)

if __name__=='__main__': main()
