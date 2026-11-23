import rclpy
from rclpy.node import Node
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import String


class Output(Node):
    def __init__(self):
        super().__init__('output')
        self.detections = self.create_publisher(String,'detections',1)
        self.preview = self.create_publisher(CompressedImage,'image/compressed',1)
        self.create_subscription(String,'internal/detections',self.detections.publish,1)
        self.create_subscription(CompressedImage,'internal/image/compressed',self.preview.publish,1)


def main():
    rclpy.init()
    node = Output()
    try: rclpy.spin(node)
    except KeyboardInterrupt: pass
    finally:
        node.destroy_node()
        if rclpy.ok(): rclpy.shutdown()
