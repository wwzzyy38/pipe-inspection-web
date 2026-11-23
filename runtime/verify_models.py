"""Use isolated ROS inputs and actual HTTP model controls, never the production graph."""
import argparse
import json
import socket
import struct
import subprocess
import sys
import time
import urllib.request
import cv2
import numpy as np
import rclpy
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CompressedImage, Image, PointCloud2, PointField

parser=argparse.ArgumentParser()
parser.add_argument('models',nargs='+',choices=['yolo','segment'])
args=parser.parse_args()
with socket.socket() as listener:
    listener.bind(('127.0.0.1',0))
    port=listener.getsockname()[1]
base=f'http://127.0.0.1:{port}'

def api(path,post=False):
    req=urllib.request.Request(base+path,method='POST' if post else 'GET',headers={'Origin':base,'X-Inspection-Action':'1'} if post else {})
    with urllib.request.urlopen(req,timeout=14) as out:
        return out.status,out.read()

def live():
    return json.loads(api('/api/live/status')[1])

image=np.full((360,640,3),45,np.uint8)
cv2.circle(image,(320,180),145,(155,170,175),12)
cv2.line(image,(230,116),(303,190),(220,230,235),5)
msg=Image(height=360,width=640,encoding='bgr8',step=1920,data=image.tobytes())
ok,encoded=cv2.imencode('.jpg',image)
assert ok
jpeg=CompressedImage(format='jpeg',data=encoded.tobytes())
t=np.linspace(0,2*np.pi,64,dtype=np.float32)
x,t=np.meshgrid(np.linspace(.25,3.0,48,dtype=np.float32),t)
pts=np.stack((x,.57*np.cos(t),.57*np.sin(t)),axis=-1).reshape(-1,3).astype('<f4')
cloud=PointCloud2(height=1,width=len(pts),point_step=12,row_step=12*len(pts),is_dense=True,
                  fields=[PointField(name=k,offset=4*i,datatype=7,count=1) for i,k in enumerate('xyz')],data=pts.tobytes())
cloud.header.frame_id='body'
rclpy.init()
node=rclpy.create_node('inspection_model_smoke')
raw=node.create_publisher(Image,'/siyi/camera/image_raw',qos_profile_sensor_data)
compressed=node.create_publisher(CompressedImage,'/siyi/camera/image_raw/compressed',qos_profile_sensor_data)
pc=node.create_publisher(PointCloud2,'/cloud_registered_body',qos_profile_sensor_data)

def publish():
    for p,m in [(raw,msg),(compressed,jpeg),(pc,cloud)]:
        m.header.stamp=node.get_clock().now().to_msg()
        p.publish(m)
    rclpy.spin_once(node,timeout_sec=.14)

with open('logs/isolated-model-web.log','ab') as log:
    child=subprocess.Popen([sys.executable,'-u','server.py','--port',str(port)],stdout=log,stderr=subprocess.STDOUT)
    try:
        for _ in range(80):
            publish()
            try: state=live()
            except OSError: continue
            if all(state['models'][m]['canStart'] for m in args.models):break
        else: raise RuntimeError(f'Cannot start models: {state}')
        started=time.monotonic()
        for m in args.models:
            assert json.loads(api(f'/api/control/{m}/start',True)[1])['ok']
        tested=set()
        while time.monotonic()-started<90 and len(tested)<len(args.models):
            publish();state=live()
            for m in args.models:
                if m in tested:continue
                status=state['models'][m]
                if status['state']=='failed':raise RuntimeError(f'{m} exited {status["exit"]}; read logs/{m}.log')
                if status['state']!='online':continue
                _,body=api('/api/live/'+m)
                if not body:continue
                if m=='yolo':
                    assert body.startswith(b'\xff\xd8')
                    code,result=api('/api/live/detections')
                    if code==204:continue
                    data=json.loads(result)
                    assert data['width']==640 and data['height']==360 and len(data['names'])==9,data
                    assert data['processing_ms']>0
                    print(f'YOLO: first JPEG at {time.monotonic()-started:.1f}s; first inference {data["processing_ms"]:.0f} ms',flush=True)
                else:
                    assert struct.unpack('<4sI',body[:8])==(b'PWC1',len(pts)),len(body)
                    assert set(body[23::16])<=set(range(8))
                    code,result=api('/api/live/summary')
                    if code==204:continue
                    assert sum(json.loads(result)['class_counts'])==len(pts)
                    print(f'PointNeXt: first labelled cloud at {time.monotonic()-started:.1f}s; {len(pts)} points',flush=True)
                assert status['owned'],status
                tested.add(m)
        assert tested==set(args.models),state
        for m in args.models:
            assert json.loads(api(f'/api/control/{m}/stop',True)[1])['ok']
            assert not live()['models'][m]['owned']
        print('PASS: HTTP starts actual model nodes, ROS results reach gateway, HTTP stops only owned nodes',flush=True)
    finally:
        child.terminate()
        child.wait(timeout=20)
        node.destroy_node();rclpy.shutdown()
