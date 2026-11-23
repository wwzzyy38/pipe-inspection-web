import os, sys, json, time, struct, hashlib
from pathlib import Path
from dataclasses import replace
import numpy as np
import torch
import yaml
from scipy.spatial import cKDTree
from easydict import EasyDict
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import PointCloud2, PointField

COLORS = np.array([[70,110,230],[255,100,160],[230,50,40],[30,180,240],
                   [140,100,220],[50,190,80],[10,150,140],[230,160,30]],np.uint8)
SHA = '78d06ebca47500c08b304f3ca1acc5efa8dc505a2e9a8f0c8062c671d027ad51'


def decode(msg):
    types={1:'i1',2:'u1',3:'i2',4:'u2',5:'i4',6:'u4',7:'f4',8:'f8'}
    endian='>' if msg.is_bigendian else '<'
    dtype=np.dtype(dict(names=[f.name for f in msg.fields],
                        formats=[endian+types[f.datatype] if f.count==1 else
                                 (endian+types[f.datatype],(f.count,)) for f in msg.fields],
                        offsets=[f.offset for f in msg.fields],itemsize=msg.point_step))
    a=np.ndarray((msg.height,msg.width),dtype=dtype,buffer=msg.data,
                 strides=(msg.row_step,msg.point_step)).reshape(-1)
    xyz=np.c_[a['x'],a['y'],a['z']].astype(np.float32)
    return xyz[np.isfinite(xyz).all(1)]


def partitions(x,limit=24000):
    pending=[np.arange(len(x))]
    while pending:
        ids=pending.pop()
        if len(ids)<=limit:
            yield ids
        else:
            sub=x[ids];order=np.argsort(sub[:,np.argmax(np.ptp(sub,axis=0))]);mid=len(ids)//2
            pending.extend([ids[order[:mid]],ids[order[mid:]]])


class Segmenter:
    def __init__(self, upstream, checkpoint):
        assert hashlib.sha256(Path(checkpoint).read_bytes()).hexdigest()==SHA,'Unexpected model checkpoint'
        sys.path.insert(0,upstream)
        from openpoints.models import build_model_from_cfg
        cfg=yaml.safe_load((Path(upstream)/'cfgs/s3dis/pointnext-b.yaml').read_text())['model']
        cfg['encoder_args']['in_channels']=4;cfg['cls_args']['num_classes']=8
        self.model=build_model_from_cfg(EasyDict(cfg)).cuda()
        state=torch.load(checkpoint,map_location='cpu',weights_only=False)
        assert state['epoch']==62
        self.model.load_state_dict(state['model'],strict=True)
        self.model.eval().float()
        torch.set_num_threads(2)

    @torch.inference_mode()
    def predict(self,xyz):
        if len(xyz)<3: raise ValueError('Insufficient finite points')
        _,idx=np.unique(np.floor((xyz-xyz.min(0))/.04).astype(np.int64),axis=0,return_index=True)
        idx=np.sort(idx);v=xyz[idx]
        normal=[];tree=cKDTree(xyz);k=min(20,len(xyz))
        for start in range(0,len(v),8192):
            q=v[start:start+8192];_,near=tree.query(q,k=k)
            local=xyz[near];local-=local.mean(1,keepdims=True)
            _,vectors=np.linalg.eigh(np.einsum('nki,nkj->nij',local,local)/k)
            n=vectors[:,:,0].astype(np.float32)
            n[np.sum(n*(-q),axis=1)<0]*=-1
            n/=np.maximum(np.linalg.norm(n,axis=1,keepdims=True),1e-8)
            normal.append(n)
        pos=v-v.min(0);features=np.c_[np.concatenate(normal),pos[:,2]].astype(np.float32)
        label=np.empty(len(v),np.uint32);confidence=np.empty(len(v),np.float32)
        for ids in partitions(pos):
            original=ids
            if len(ids)<24000:ids=np.r_[ids,np.resize(ids,24000-len(ids))]
            p=torch.from_numpy(pos[ids]).unsqueeze(0).cuda()
            f=torch.from_numpy(features[ids].T.copy()).unsqueeze(0).cuda()
            prob=self.model(dict(pos=p,x=f)).softmax(1)[0].T.cpu().numpy()[:len(original)]
            label[original]=prob.argmax(1);confidence[original]=prob.max(1)
        _,near=cKDTree(v).query(xyz,k=1)
        return label[near],confidence[near]


class Inference(Node):
    def __init__(self):
        super().__init__('inference')
        root=str(Path.home()/'pipe_models/pointnext_scheme2')
        upstream=self.declare_parameter('upstream',root+'/upstream').value
        checkpoint=self.declare_parameter('checkpoint',root+'/best.pt').value
        topic=self.declare_parameter('input_topic','/cloud_registered_body').value
        self.segmenter=Segmenter(upstream,checkpoint)
        self.publisher=self.create_publisher(PointCloud2,'internal/segmented',1)
        qos=QoSProfile(depth=1,reliability=ReliabilityPolicy.BEST_EFFORT)
        self.create_subscription(PointCloud2,topic,self.process,qos)
        self.get_logger().info('Reading '+topic+'; scheme2 best62 loaded')

    def process(self,msg):
        try:
            if msg.header.frame_id!='body': raise ValueError('Expected body-frame cloud')
            xyz=decode(msg);label,confidence=self.segmenter.predict(xyz)
            arr=np.empty(len(xyz),dtype=[('xyz','<f4',(3,)),('rgb','<u4'),('label','<u4'),('confidence','<f4')])
            arr['xyz']=xyz;c=COLORS[label].astype(np.uint32)
            arr['rgb']=(c[:,0]<<16)|(c[:,1]<<8)|c[:,2];arr['label']=label;arr['confidence']=confidence
            fields=[PointField(name=name,offset=i*4,datatype=6 if i==4 else 7,count=1)
                    for i,name in enumerate(['x','y','z','rgb','label','confidence'])]
            out=PointCloud2(header=msg.header,height=1,width=len(xyz),fields=fields,
                           is_bigendian=False,point_step=24,row_step=len(xyz)*24,
                           data=arr.tobytes(),is_dense=True)
            self.publisher.publish(out)
        except Exception as exc:
            self.get_logger().error(str(exc),throttle_duration_sec=5.)


def main():
    rclpy.init();node=Inference()
    try:rclpy.spin(node)
    except KeyboardInterrupt:pass
    finally:
        node.destroy_node()
        if rclpy.ok():rclpy.shutdown()
