from setuptools import setup
from glob import glob
setup(name='pipe_pointcloud_recognition',version='0.1.0',packages=['pipe_pointcloud_recognition'],data_files=[
('share/ament_index/resource_index/packages',['resource/'+'pipe_pointcloud_recognition']),
('share/'+'pipe_pointcloud_recognition',['package.xml']),('share/'+'pipe_pointcloud_recognition'+'/launch',glob('launch/*.launch.py'))],
install_requires=['setuptools'],zip_safe=True,maintainer='Pipe Inspection Maintainers',maintainer_email='maintainers@example.invalid',
description='Pipe inspection inference and output ROS2 nodes',license='Apache-2.0',
entry_points={'console_scripts':['inference_node = '+'pipe_pointcloud_recognition'+'.inference_node:main','output_node = '+'pipe_pointcloud_recognition'+'.output_node:main']})
