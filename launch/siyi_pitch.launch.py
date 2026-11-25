"""Run SIYI pitch command and feedback only; do not start video or a web server."""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    camera_ip = LaunchConfiguration('camera_ip')
    parameters = {'camera_ip': camera_ip, 'pitch_zero_deg': -0.1, 'pitch_sign': -1.0,
                  'pitch_limit_deg': 90.0}
    return LaunchDescription([
        DeclareLaunchArgument('camera_ip', default_value='192.168.144.25'),
        Node(package='siyi_a2_mini', executable='control_node', namespace='siyi',
             parameters=[parameters], output='screen'),
        Node(package='siyi_a2_mini', executable='feedback_node', namespace='siyi',
             parameters=[{**parameters, 'pitch_offset_deg': 180.0}], output='screen'),
    ])
