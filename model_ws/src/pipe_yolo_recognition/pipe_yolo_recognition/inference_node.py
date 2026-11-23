from pathlib import Path
import json, time
import numpy as np
import cv2
import torch
from ultralytics import YOLO
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image, CompressedImage
from std_msgs.msg import String


class Inference(Node):
    def __init__(self):
        super().__init__('inference')
        model = self.declare_parameter('model', str(Path.home()/'pipe_inspection_web/assets/weights/yolo26m.pt')).value
        topic = self.declare_parameter('input_topic', '/siyi/camera/image_raw').value
        self.size = int(self.declare_parameter('imgsz', 640).value)
        self.conf = float(self.declare_parameter('confidence', .25).value)
        torch.set_num_threads(2)
        cv2.setNumThreads(1)
        self.model = YOLO(model, task='detect')
        self.detections = self.create_publisher(String, 'internal/detections', 1)
        self.preview = self.create_publisher(CompressedImage, 'internal/image/compressed', 1)
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.create_subscription(Image, topic, self.process, qos)
        self.get_logger().info('Reading '+topic+'; model='+model)

    def process(self, msg):
        try:
            if msg.encoding not in ('bgr8', 'rgb8'):
                raise ValueError('Expected bgr8 or rgb8, received '+msg.encoding)
            image = np.ndarray((msg.height,msg.width,3), np.uint8,
                               buffer=msg.data, strides=(msg.step,3,1)).copy()
            if msg.encoding == 'rgb8': image = cv2.cvtColor(image,cv2.COLOR_RGB2BGR)
            start = time.perf_counter()
            prediction = self.model.predict(image, imgsz=self.size, device=0,
                                            conf=self.conf, rect=False, verbose=False)[0]
            boxes = prediction.boxes
            payload = dict(stamp=dict(sec=msg.header.stamp.sec,nanosec=msg.header.stamp.nanosec),
                           frame_id=msg.header.frame_id, width=msg.width, height=msg.height,
                           names=prediction.names, xyxy=boxes.xyxy.cpu().tolist(),
                           classes=boxes.cls.int().cpu().tolist(),
                           confidence=boxes.conf.cpu().tolist(),
                           processing_ms=(time.perf_counter()-start)*1000)
            self.detections.publish(String(data=json.dumps(payload,ensure_ascii=False)))
            if self.preview.get_subscription_count():
                annotated = image.copy()
                for xy, cls, conf in zip(payload['xyxy'],payload['classes'],payload['confidence']):
                    x1,y1,x2,y2 = [int(v) for v in xy]
                    cv2.rectangle(annotated,(x1,y1),(x2,y2),(0,220,255),2)
                    cv2.putText(annotated,'class %d %.2f'%(cls,conf),(x1,max(18,y1-5)),
                                cv2.FONT_HERSHEY_SIMPLEX,.55,(0,220,255),1)
                ok, data = cv2.imencode('.jpg',annotated,[cv2.IMWRITE_JPEG_QUALITY,85])
                if ok:
                    preview = CompressedImage(header=msg.header, format='jpeg', data=data.tobytes())
                    self.preview.publish(preview)
        except Exception as exc:
            self.get_logger().error(str(exc), throttle_duration_sec=5.)


def main():
    rclpy.init()
    node = Inference()
    try: rclpy.spin(node)
    except KeyboardInterrupt: pass
    finally:
        node.destroy_node()
        if rclpy.ok(): rclpy.shutdown()
