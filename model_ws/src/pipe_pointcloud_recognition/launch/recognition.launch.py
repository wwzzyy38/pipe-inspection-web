from pathlib import Path
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():
 return LaunchDescription([
  DeclareLaunchArgument('input_topic',default_value='/cloud_registered_body'),
  DeclareLaunchArgument('python_executable',default_value=str(Path.home()/'pipe_pointcloud_venv/bin/python')),
  DeclareLaunchArgument('upstream',default_value=str(Path.home()/'pipe_models/pointnext_scheme2/upstream')),DeclareLaunchArgument('checkpoint',default_value=str(Path.home()/'pipe_models/pointnext_scheme2/best.pt')),
  Node(package='pipe_pointcloud_recognition',executable='inference_node',namespace='pipe_pointcloud',name='inference',prefix=[LaunchConfiguration('python_executable')],parameters=[{'input_topic':LaunchConfiguration('input_topic'),'upstream':LaunchConfiguration('upstream'),'checkpoint':LaunchConfiguration('checkpoint')}],output='screen'),
  Node(package='pipe_pointcloud_recognition',executable='output_node',namespace='pipe_pointcloud',name='output',prefix=[LaunchConfiguration('python_executable')],output='screen')])
