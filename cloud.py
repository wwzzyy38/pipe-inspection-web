"""PWC1: 8 byte header, then little-endian XYZ float32, RGB and label."""
import struct
import numpy as np

MAX_POINTS = 30000


def pack(xyz, colors=None, labels=None):
    a = np.empty(len(xyz), dtype=[('xyz','<f4',(3,)),('rgb','u1',(3,)),('label','u1')])
    a['xyz']=xyz
    a['rgb']=(117,190,210) if colors is None else np.column_stack(((colors>>16)&255,(colors>>8)&255,colors&255))
    if labels is None:
        a['label']=255
    else:
        a['label']=np.where(np.isfinite(labels)&(labels>=0)&(labels<8),labels,255).astype('u1')
    return struct.pack('<4sI',b'PWC1',len(a))+a.tobytes()


def encode(msg, limit=MAX_POINTS):
    fields={f.name:f for f in msg.fields}
    w,h,step,row=msg.width,msg.height,msg.point_step,msg.row_step
    if not w or not h:
        return pack(np.empty((0,3))),0
    if not step or row<w*step or len(msg.data)<h*row:
        raise ValueError('Invalid PointCloud2 dimensions')
    idx=np.arange(0,w*h,max(1,(w*h+limit-1)//limit),dtype=np.int64)
    endian='>' if msg.is_bigendian else '<'
    def field(name,types):
        f=fields[name]
        dtype=np.dtype(endian+types[f.datatype])
        if f.count!=1 or f.offset+dtype.itemsize>step:
            raise ValueError(f'Invalid field: {name}')
        a=np.ndarray((h,w),dtype=dtype,buffer=msg.data,offset=f.offset,strides=(row,step))
        return a[idx//w,idx%w]
    xyz=np.column_stack([field(k,{7:'f4',8:'f8'}) for k in 'xyz'])
    valid=np.isfinite(xyz).all(axis=1)&(np.abs(xyz)<1e7).all(axis=1)
    rgb=next((n for n in ('rgb','rgba') if n in fields),None)
    colors=field(rgb,{5:'u4',6:'u4',7:'u4'})[valid] if rgb else None
    labels=field('label',{1:'i1',2:'u1',3:'i2',4:'u2',5:'i4',6:'u4',7:'f4'})[valid] if 'label' in fields else None
    return pack(xyz[valid],colors,labels),int(valid.sum())
