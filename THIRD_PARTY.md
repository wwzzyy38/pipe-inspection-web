# 第三方软件和数据

本仓库不打包第三方上游源码、模型权重、CUDA 运行库或客户录制。`model_ws/src/` 中的 ROS 2 包保留各自 `package.xml` 的许可证声明。没有声明的部分不因此获得公开再分发授权。

- [ROS 2 Humble](https://docs.ros.org/en/humble/)：ROS 2 运行时及消息包分别遵循上游许可证。
- [Ultralytics](https://github.com/ultralytics/ultralytics)：AGPL-3.0 或商业许可，客户部署及分发前请确认适用条款。
- [PointNeXt](https://github.com/guochengqian/PointNeXt) / [OpenPoints](https://github.com/guochengqian/openpoints)：请遵循上游 MIT 许可和自定义 CUDA 算子的许可证要求。
- PyTorch、torchvision、NVIDIA 驱动和 CUDA/cuDNN、Jetson Python wheel 在目标设备上分别安装，遵循各自许可。

训练数据和项目生成的权重需要单独确认使用与分发权限。本仓库不授予这些资产的任何权利。
