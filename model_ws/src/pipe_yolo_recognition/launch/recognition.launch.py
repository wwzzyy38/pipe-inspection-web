from pathlib import Path
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():
 return LaunchDescription([
  DeclareLaunchArgument('input_topic',default_value='/siyi/camera/image_raw'),
  DeclareLaunchArgument('python_executable',default_value=str(Path.home()/'yolo-venv/bin/python')),
  DeclareLaunchArgument('model',default_value=str(Path.home()/'pipe_inspection_web/assets/weights/yolo26m.pt')),
  Node(package='pipe_yolo_recognition',executable='inference_node',namespace='pipe_yolo',name='inference',prefix=[LaunchConfiguration('python_executable')],parameters=[{'input_topic':LaunchConfiguration('input_topic'),'model':LaunchConfiguration('model')}],output='screen'),
  Node(package='pipe_yolo_recognition',executable='output_node',namespace='pipe_yolo',name='output',prefix=[LaunchConfiguration('python_executable')],output='screen')])
