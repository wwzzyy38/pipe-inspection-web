import json
import math
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from cloud import encode
from cloud_reference import ODOM_TOPIC, align, match_pose, odom_pose, stamp_ns
from flight_state import FlightState

TOPICS={'video':'/siyi/camera/image_raw/compressed','yolo':'/pipe_yolo/image/compressed',
        'thermal':'/thermal/image_raw/compressed',
        'cloud':'/cloud_registered_body','segment':'/pipe_pointcloud/segmented',
        'detections':'/pipe_yolo/detections','summary':'/pipe_pointcloud/summary'}
JPEG_FEEDS={'video','yolo','thermal'}
GIMBAL_MIN, GIMBAL_MAX = -25, 89.9
GIMBAL_FEEDBACK_TTL = 2.0
GIMBAL_COMMAND_INTERVAL = .4
MODELS={'yolo':('pipe_yolo_recognition','video'), 'segment':('pipe_pointcloud_recognition','cloud')}
CONFIG={
    'yolo': [('python_executable','PIPE_YOLO_PYTHON','yolo-venv/bin/python'),('model','PIPE_YOLO_MODEL','pipe_inspection_web/assets/weights/yolo26m.pt')],
    'segment':[('python_executable','PIPE_POINT_PYTHON','pipe_pointcloud_venv/bin/python'),('upstream','PIPE_POINT_UPSTREAM','pipe_models/pointnext_scheme2/upstream'),('checkpoint','PIPE_POINT_MODEL','pipe_models/pointnext_scheme2/best.pt')]
}

class Live:
    def __init__(self,root):
        self.root=root
        self.ready=False
        self.problem=None
        self.graph_at=0
        self.pubs,self.nodes=set(),set()
        self.last,self.proc,self.exits={},{},{}
        self.lock=threading.RLock()
        self.flight=FlightState(self.lock)
        self.gimbal_command=None
        self.gimbal_pitch=None
        self.gimbal_connected=None
        self.gimbal_last_sent=-math.inf
        self.poses={};self.anchor=None;self.ref_version=0
        self.cloud_stamp=0;self.cloud_at=0.0;self.odom_at=0.0
        self.done=threading.Event()
        self.thread=threading.Thread(target=self.run,daemon=True)
        self.thread.start()

    def run(self):
        ctx=node=None
        try:
            import rclpy
            from rclpy.context import Context
            from rclpy.executors import SingleThreadedExecutor
            from rclpy.qos import QoSProfile, ReliabilityPolicy
            from sensor_msgs.msg import CompressedImage,PointCloud2
            from std_msgs.msg import Bool, Float64, String
            ctx=Context()
            rclpy.init(context=ctx)
            node=rclpy.create_node('pipe_inspection_web',context=ctx,enable_rosout=False)
            qos=QoSProfile(depth=1,reliability=ReliabilityPolicy.BEST_EFFORT)
            for k,topic in TOPICS.items():
                msg=CompressedImage if k in JPEG_FEEDS else PointCloud2 if k in ('cloud','segment') else String
                node.create_subscription(msg,topic,lambda m,k=k:self.collect(k,m),qos)
            from nav_msgs.msg import Odometry
            node.create_subscription(Odometry,ODOM_TOPIC,self.collect_pose,qos)
            self.gimbal_command=node.create_publisher(Float64,'/siyi/gimbal/set_pitch_deg',1)
            node.create_subscription(Float64,'/siyi/gimbal/pitch_deg',self.collect_gimbal_pitch,qos)
            node.create_subscription(Bool,'/siyi/gimbal/connected',self.collect_gimbal_connection,qos)
            self.flight.subscribe(node,qos)
            executor=SingleThreadedExecutor(context=ctx)
            executor.add_node(node)
            self.ready=True
            while not self.done.is_set():
                executor.spin_once(timeout_sec=.12)
                self.flight.refresh()
                now=time.monotonic()
                if now-self.graph_at>=1:
                    nodes=set(node.get_node_names_and_namespaces())
                    pubs={k for k,topic in TOPICS.items() if node.count_publishers(topic)}
                    with self.lock:
                        self.nodes,self.pubs,self.graph_at=nodes,pubs,now
            executor.shutdown()
        except Exception as exc:
            self.problem=f'{type(exc).__name__}: {exc}'
        finally:
            with self.lock:
                self.ready=False
                self.gimbal_command=None
            if node is not None:
                node.destroy_node()
            if ctx is not None and ctx.ok():
                ctx.shutdown()

    def collect(self,k,msg):
        now=time.monotonic()
        prev=self.last.get(k)
        if prev and now-prev[0]<(.12 if k in JPEG_FEEDS else .25):
            return
        try:
            extra={}
            if k in JPEG_FEEDS:
                data=bytes(msg.data)
                if len(data)>4000000 or not data.startswith(b'\xff\xd8'):
                    return
            elif k in ('cloud','segment'):
                data,count=encode(msg)
                extra=dict(count=count,frame=msg.header.frame_id[:64])
            else:
                if len(msg.data)>250000:
                    return
                data=json.dumps(json.loads(msg.data),allow_nan=False).encode()
            if hasattr(msg,'header'):
                s=msg.header.stamp
                extra['stamp']=f'{s.sec}.{s.nanosec:09d}'
            with self.lock:
                if k=='cloud' and extra['frame']=='body' and count:
                    stamp=stamp_ns(msg.header)
                    if stamp>0:
                        if now-self.cloud_at>3 or (self.cloud_stamp and stamp<self.cloud_stamp):
                            self.anchor=None;self.ref_version+=1
                            if self.cloud_stamp and stamp<self.cloud_stamp:self.poses.clear()
                        self.cloud_at=now;self.cloud_stamp=stamp
                        self.set_anchor()
                self.last[k]=(now,time.time_ns(),data,extra)
        except (ValueError,KeyError,TypeError,OverflowError,BufferError):
            return

    def collect_pose(self,msg):
        pose=odom_pose(msg);stamp=stamp_ns(msg.header)
        if pose is None or stamp<=0:return
        with self.lock:
            now=time.monotonic()
            if now-self.odom_at>3 or (self.poses and stamp<max(self.poses)-1_000_000_000):
                self.poses.clear();self.anchor=None;self.ref_version+=1
            self.odom_at=now
            self.poses[stamp]=pose
            if len(self.poses)>128:del self.poses[min(self.poses)]
            self.set_anchor()

    def set_anchor(self):
        if self.anchor is None and time.monotonic()-self.cloud_at<=3:
            self.anchor=match_pose(sorted(self.poses),self.poses,self.cloud_stamp)

    def reference_available(self):
        now=time.monotonic()
        return bool(self.ready and self.anchor is not None and now-self.cloud_at<=3 and now-self.odom_at<=3)

    def aligned_frame(self,k,record):
        with self.lock:
            if k not in ('cloud','segment') or not self.reference_available() or record[3].get('frame')!='body':return None
            sec,ns=record[3]['stamp'].split('.')
            pose=match_pose(sorted(self.poses),self.poses,int(sec)*10**9+int(ns))
            anchor=self.anchor
        return align(record[2],anchor,pose) if pose is not None else None

    def frame(self,k):
        with self.lock:
            r=self.last.get(k)
            return r if self.ready and r and time.monotonic()-r[0]<=3 else None

    def configured(self,k):
        from ament_index_python.packages import get_package_prefix,PackageNotFoundError
        params,missing=[],[]
        try:
            get_package_prefix(MODELS[k][0])
        except PackageNotFoundError:
            missing.append(MODELS[k][0])
        for name,var,default in CONFIG[k]:
            p=Path(os.environ.get(var,str(Path.home()/default))).expanduser()
            valid=p.is_dir() if name=='upstream' else p.is_file() and (name!='python_executable' or os.access(p,os.X_OK))
            if not valid:
                missing.append(var)
            params.append(f'{name}:={p}')
        return params,missing

    def owned(self,k):
        rec=self.proc.get(k)
        if rec and rec[0].poll() is not None:
            self.exits[k]=rec[0].returncode
            self.proc.pop(k)
            return None
        return rec

    def check(self,k):
        rec=self.owned(k)
        ns='/pipe_yolo' if k=='yolo' else '/pipe_pointcloud'
        output_now=self.frame(k)
        external=bool(output_now or k in self.pubs or any((n,ns) in self.nodes for n in ('inference','output')))
        source=self.frame(MODELS[k][1])
        valid=bool(source and (k=='yolo' or source[3].get('frame')=='body'))
        try:
            _,missing=self.configured(k)
        except ImportError:
            missing=['ROS 2 environment']
        if not self.ready:
            state='ros_unavailable'
        elif output_now:
            state='online'
        elif rec and time.monotonic()-rec[1]<40:
            state='starting'
        elif rec or external:
            state='stale' if k in self.last else 'waiting'
        elif k in self.exits:
            state='failed'
        elif missing:
            state='not_configured'
        else:
            state='idle' if valid else 'no_source'
        return dict(state=state,owned=bool(rec),external=bool(external and not rec),source=valid,missing=missing,
                    exit=self.exits.get(k),canStart=bool(self.ready and valid and not missing and not rec and not external and time.monotonic()-self.graph_at < 3))

    def collect_gimbal_pitch(self, msg):
        with self.lock:
            self.gimbal_pitch=(time.monotonic(),msg.data) if math.isfinite(msg.data) else None

    def collect_gimbal_connection(self, msg):
        with self.lock:
            self.gimbal_connected=(time.monotonic(),msg.data)
            if not msg.data:self.gimbal_pitch=None

    def gimbal_status(self):
        # The caller holds self.lock to keep the feedback and publisher state consistent.
        now=time.monotonic()
        pitch=self.gimbal_pitch
        connected=self.gimbal_connected
        online=bool(connected and connected[1] and now-connected[0]<GIMBAL_FEEDBACK_TTL)
        fresh=bool(pitch and now-pitch[0]<GIMBAL_FEEDBACK_TTL and online)
        controller=self.ready and self.gimbal_command is not None and self.gimbal_command.get_subscription_count()>0
        reason='ready' if controller and fresh else 'no_controller' if not controller else 'no_feedback'
        return dict(ready=reason=='ready',reason=reason,pitch=pitch[1] if fresh else None,
                    minimum=GIMBAL_MIN,maximum=GIMBAL_MAX)

    def set_gimbal_pitch(self, angle):
        if type(angle) not in (int,float) or not GIMBAL_MIN <= angle <= GIMBAL_MAX:
            return False,'invalid_angle'
        with self.lock:
            state=self.gimbal_status()
            if not state['ready']:return False,state['reason']
            now=time.monotonic()
            if now-self.gimbal_last_sent<GIMBAL_COMMAND_INTERVAL:return False,'rate_limited'
            from std_msgs.msg import Float64
            try:self.gimbal_command.publish(Float64(data=float(angle)))
            except (OSError,RuntimeError):return False,'publish_failed'
            self.gimbal_last_sent=now
            return True,'sent'

    def status(self):
        with self.lock:
            feeds={}
            for k in TOPICS:
                r=self.last.get(k)
                feeds[k]=dict(available=bool(self.frame(k)),publisher=k in self.pubs,version=str(r[1]) if r else None,**(r[3] if r else {}))
            return dict(ros=self.ready,problem=self.problem,feeds=feeds,gimbal=self.gimbal_status(),reference=dict(available=self.reference_available(),version=self.ref_version,source='live'),models={k:self.check(k) for k in MODELS})

    def control(self,k,action):
        with self.lock:
            rec=self.owned(k)
            if action=='stop':
                if not rec:
                    return False,'not_owned'
                self.stop_model(k,rec[0])
                return True,'stopped'
            state=self.check(k)
            if not state['canStart']:
                return False,state['state']
            params,_=self.configured(k)
            with (self.root/'logs'/f'{k}.log').open('ab') as log:
                p=subprocess.Popen(['/opt/ros/humble/bin/ros2','launch',MODELS[k][0],'recognition.launch.py',*params],
                                   stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,cwd=self.root,start_new_session=True)
            self.proc[k]=(p,time.monotonic())
            self.exits.pop(k,None)
            return True,'started'

    def stop_model(self,k,p):
        for sig,seconds in ((signal.SIGINT,5),(signal.SIGTERM,2),(signal.SIGKILL,1)):
            try:
                os.killpg(p.pid,sig)
                p.wait(timeout=seconds)
                break
            except ProcessLookupError:
                break
            except subprocess.TimeoutExpired:
                continue
        self.proc.pop(k,None)
        self.exits.pop(k,None)

    def close(self):
        self.done.set()
        with self.lock:
            for k,(p,_) in list(self.proc.items()):
                self.stop_model(k,p)
        self.thread.join(timeout=2)
