# 管道巡检工作台 · Pipe Inspection Web

部署在 NVIDIA Jetson 上的中文巡检网页。它可回放机上原始视频与点云、预览两种 ROS 2 机载推理，并查看从实录离线生成的 PointNeXt 分割。此仓库只包含应用、配套 ROS 2 节点和部署脚本，不包含真实录制、模型权重、浏览器凭据、证书或飞行控制代码。

## 回放与实时识别

- 浏览器支持 H.264 MP4 进度条和 SQLite ROS 2 bag 的点云回放。只读 `/cloud_registered_body`，可按时间跳转；不执行 `ros2 bag play`，不改录制，也不向运行中的 ROS 图重放消息。
- 可在原始 `body` 坐标和首个同步位姿定义的起始坐标之间切换。参考网格是起始 XY（Z=0），不是实测地面。原始和预测轨道可以共用时间轴。
- 实时读取相机、机体系点云以及 YOLO26m / PointNeXt-B 的结果。网页只管理自己启动的固定识别进程，不启动传感器或飞控，也不发送飞行指令。
- 离线预测单段运行，原始 bag 保持只读。通常要有新鲜的 MAVROS 落地、未解锁状态；仅 Jetson 供电、飞控和电机物理断电时可按提示确认一段台架任务。不要在任务结束前给飞控或电机上电。预测输出不是人工确认的缺陷。

## 机载部署

1. 准备 Ubuntu / JetPack 6 的 ARM64 Jetson、ROS 2 Humble、Python 3.10+、NumPy、PyYAML，以及所需的 ROS 消息包（含 `sensor_msgs`、`nav_msgs`、`mavros_msgs`）、rosbag2 SQLite 插件和 `colcon`。`runtime/` 的机载推理脚本针对 JetPack 6 / CUDA 12.6，不是通用桌面安装器。
2. 复制 `config.env.example` 到本机 `config.env` 后配置路径。`run.sh` 以仓库目录为工作目录，可复用现有 `~/pipe_ws` 和仓库内 `model_ws` 的 ROS 2 overlay。构建自研节点时，先加载 `/opt/ros/humble/setup.bash`，再在 `model_ws` 执行 `colcon build --packages-select pipe_yolo_recognition pipe_pointcloud_recognition`。
3. 只需查看数据时可以先不配置模型。要运行推理，另行准备有使用授权的 YOLO26m 权重、PointNeXt-B 方案 2 的第 62 轮权重、匹配平台的 CUDA PyTorch 及该模型所需的 OpenPoints 源码和本机编译算子。`runtime/bootstrap_python.sh` 提供该平台 Python wheel 的参考；`runtime/model_env.sh` 和 `runtime/build_pointops.sh` 假定另行准备了 JetPack 对应的 CUDA 头文件和动态库。不要把别的架构的 Python 环境或 TensorRT 引擎直接复制过来。
4. 运行 `bash run.sh`。默认访问本机 `http://127.0.0.1:18120/`。需要局域网或 Tailscale 直连时，在 `config.env` 显式设置监听、允许子网、TLS 证书/私钥和密码文件；默认不开放公网。用 `mkdir -p web-security && chmod 700 web-security` 准备私有目录，执行 `python3 web_auth.py web-security/password.json` 无回显设置至少 12 个字符的密码。密码文件、私钥不得放进 Git。`run.sh` 默认附加 `--allow-http`，在已配置 TLS 证书时会在同一端口同时支持 HTTPS 与需要密码登录的 HTTP（可用 `--no-allow-http` 仅保留 HTTPS）；若未配置证书且仅需局域网免证书访问，可显式运行 `bash run.sh --http`（可配合 `--auto-wifi` 自动读取当前 Wi-Fi 网卡地址与子网；多网卡可传 `--wifi-interface`）。HTTP 模式下会话和密码明文传输，仅限受信任网络使用。

### Jetson03 一键启动预览

`start_preview_stack.sh` 在同一个 tmux 窗口分四格启动网页、SIYI 可见光与云台俯仰控制（`launch/siyi_pitch.launch.py`）、红外、Livox + FAST‑LIO。它沿用设备已经配置的三个传感器网口和 `config.env`，不会重配网络。使用前应停掉已有预览或实飞任务；脚本检测到冲突时会退出，不会替你关闭它们。

```bash
bash start_preview_stack.sh --check
bash start_preview_stack.sh
```

`--check` 只检查依赖，不启动节点。加 `--detach` 可让进程留在后台；用 `tmux attach-session -t pipe_preview` 看四格日志。再次运行进入已有会话，不会重复启动；需要停止这四格时执行 `tmux kill-session -t '=pipe_preview'`。

此脚本不启动 MAVROS、PX4 位姿桥、飞行任务或录制。YOLO 和 PointNeXt 仍从网页按需启动；没有飞控遥测时，离线预测仍受现有落地确认或台架条件限制。

### 云台俯仰控制（可选）

在安装了 `siyi_a2_mini` 的设备上，加载 ROS 和该驱动的工作区，再运行 `ros2 launch ./launch/siyi_pitch.launch.py`。这只启动俯仰控制和反馈，不重复启动可见光取流或驱动自带网页。若已有控制和反馈节点，直接复用，不要再启动一份。

登录巡检网页，在「实时识别」的可见光窗口拖动滑条选择目标，再点「应用角度」。网页限位为 −25° 到 +50°（大角度接近实机干涉/振荡区，已按稳定区间限位），当前角度由驱动反馈；没有新反馈时不能发送。首次接入时应在地面检查安装标定、实际转动方向与机械间隙。首次显示、滑条拖动和切换画面都不会自动发送转动命令。

### 下载录制

在「记录回放」中选择一段，再分别点击可见光、红外或点云下方的下载按钮。可见光和红外按选择的轨道下载原始 MP4；红外需要预先在 `session.json` 的 `infrared` 字段登记录像，格式与 `videos` 相同，例如 `"infrared":{"thermal":{"file":"thermal.mp4","label":"红外录像"}}`。下载不代表红外设备已接通，也不会开始录制。

原生 ROS 2 点云的 ZIP 解压后得到可读取的 SQLite rosbag2 目录，只包含 `/cloud_registered_body`，保留原时间戳与序列化点云。它不包含飞控状态、里程计、可见光或红外数据。导入的点云和已完成的 PointNeXt 预测各按选中的 PWC1 轨道导出 ZIP，保留录制坐标和帧时间。实时识别页和尚未保存的结果不提供下载。点云归档需先打包，大录制可能要等一会儿；同一服务同一时间处理一个归档。

### 录制入口与验证

- 双轨媒体目录在 `PIPE_WEB_RECORDINGS`；`python3 make_demo.py` 可生成明确标注的合成演示，需要 GStreamer 的 `x264enc`、`mp4mux`，不使用客户数据。
- 原生 bag 默认扫描 `~/.ros/log/pipe_hover`，可使用 `PIPE_WEB_BAG_ROOT` 改目录。仅支持有有效 `metadata.yaml` 的未压缩 SQLite3/CDR bag，兼容多个分卷；缺少同步 `/Odometry` 时不会绘制虚假的起始参考面。PWC1 导入另见 `python3 import_bag.py --help`。
- 安装并配置模型之后，可在板子空闲时执行 `bash runtime/verify_models.sh yolo segment`。该脚本在仅本机可见的独立 ROS 域使用合成输入验证推理和预览，不是飞行控制测试或识别精度报告。

有关第三方软件和数据使用，请见 [THIRD_PARTY.md](THIRD_PARTY.md)。
