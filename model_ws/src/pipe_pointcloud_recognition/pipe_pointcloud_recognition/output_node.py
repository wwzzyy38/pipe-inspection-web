import json
import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import PointCloud2
from std_msgs.msg import String


class Output(Node):
    def __init__(self):
        super().__init__('output')
        self.cloud=self.create_publisher(PointCloud2,'segmented',1)
        self.summary=self.create_publisher(String,'summary',1)
        self.create_subscription(PointCloud2,'internal/segmented',self.process,1)

    def process(self,msg):
        self.cloud.publish(msg)
        labels=np.ndarray((msg.width,),dtype='<u4',buffer=msg.data,offset=16,strides=(24,))
        self.summary.publish(String(data=json.dumps(dict(
            stamp=dict(sec=msg.header.stamp.sec,nanosec=msg.header.stamp.nanosec),
            frame_id=msg.header.frame_id,points=msg.width,
            class_counts=np.bincount(labels.astype(int),minlength=8).tolist()))))


def main():
    rclpy.init();node=Output()
    try:rclpy.spin(node)
    except KeyboardInterrupt:pass
    finally:
        node.destroy_node()
        if rclpy.ok():rclpy.shutdown()
