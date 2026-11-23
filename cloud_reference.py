"""Read-only display transform from the current body pose into the first body pose."""
from bisect import bisect_left
import numpy as np

ODOM_TOPIC='/Odometry'
ODOM_TYPE='nav_msgs/msg/Odometry'
MAX_GAP=220_000_000


def stamp_ns(header):
    return header.stamp.sec*10**9+header.stamp.nanosec


def odom_pose(msg):
    if (msg.header.frame_id.lstrip('/'),msg.child_frame_id.lstrip('/'))!=('camera_init','body'):
        return None
    p,q=msg.pose.pose.position,msg.pose.pose.orientation
    pos=np.array([p.x,p.y,p.z],dtype=np.float64)
    quat=np.array([q.x,q.y,q.z,q.w],dtype=np.float64)
    n=np.linalg.norm(quat)
    if not np.all(np.isfinite(pos)) or not .98<=n<=1.02:
        return None
    return pos,quat/n


def matrix(q):
    x,y,z,w=q
    return np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
                     [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
                     [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])


def match_pose(times,poses,stamp):
    if stamp<=0:return None
    i=bisect_left(times,stamp)
    if i<len(times) and times[i]==stamp:return poses[times[i]]
    if i==0 or i==len(times):return None
    a,b=times[i-1],times[i]
    if b-a>MAX_GAP:return None
    p,q=poses[a];v,w=poses[b];f=(stamp-a)/(b-a)
    if np.dot(q,w)<0:w=-w
    q=(1-f)*q+f*w
    return ((1-f)*p+f*v,q/np.linalg.norm(q))


def align(data,anchor,pose):
    if pose is anchor:return data
    p0,q0=anchor;p,q=pose
    r0=matrix(q0);rotation=r0.T@matrix(q);offset=r0.T@(p-p0)
    out=bytearray(data)
    xyz=np.ndarray(((len(out)-8)//16,3),dtype='<f4',buffer=out,offset=8,strides=(16,4))
    xyz[:]=xyz@rotation.T+offset
    return out
