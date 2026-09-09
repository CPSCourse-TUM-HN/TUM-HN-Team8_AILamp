# 五轴实体动作实验与后续操作页面

状态：第一阶段的离线软件实现已完成；最终全仓库 327 项测试通过，13 段 CSV 预检和默认完整剧本的内存模拟运行通过。尚未部署或驱动实物，实体动作实验及第二阶段网页待继续。详见 `output/motor_demo/离线验证报告_20260908.md`。

## 目标与顺序

第一阶段：使用原 LeLamp 的完整五轴 CSV，完成整臂保持、单段动作、连续剧本和归位的实体实验。触发情景由剧本模拟，不依赖 LED、AI、摄像头或音频。

第二阶段：在第一阶段实测完成后，为同一执行器增加操作页面，支持全部动作独立触发、一键剧本、归位、停止及实时状态。第一阶段不先做网页，不创建第二套硬件控制进程。

“完整原动作”指保留每一条原始五轴目标，不掩蔽 ID 1、不删帧凑时长、不静默裁剪幅度。允许明确显示的降速以及首帧/段间插值。各轴实际角度取决于这台实物的校准；尤其 ID 1 的幅度会按新实测范围映射。

## 已核实的输入

- 原运行环境：Nano 主机 `/home/hany/Downloads/lelamp_runtime`，容器 `2082fbab718a` 中为 `/home/lelamp_runtime`，Python 3.12。
- 机器人身份 `lelamp`，串口 `/dev/ttyACM0`；稳定别名 `/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B14033387-if00`。连接时须重新解析、独占，不把历史接口状态当作当前证据。
- 新校准目录（容器内）：`/home/LeLamp-demo-calibration-20260908-7QE2U0`，文件 `lelamp.json`，SHA-256 `8285b8c0d24239046dec3db33e305fecbec5a4be2f9c3a4c2e718ed068a943c1`。
- ID 1 的 offset/min/max 为 `811/1588/2506`，原中立朝向对应新原始坐标 `2047`，归一化 `0`。ID 2–5 校准未改变。
- Mac 新校准和 Nano 动作快照：`output/hardware_backups/LeLamp-demo-calibration-20260908-7QE2U0/`，其中 `nano_recordings/` 有 13 个 CSV，与本轮 Nano 源文件 SHA-256 逐个相同。
- 现有 `RecordingStore` 全量读取快照通过：13 段、5488 帧，每帧均为完整五轴、有限数值且在 `[-100,100]`。这不代表实体运动/碰撞/负载验证通过。
- 13 段为 `curious, excited, happy_wiggle, headshake, idle, nod, sad, scanning, shock, shy, test01, test02, wake_up`。动态枚举，不能只列 Mac 旧库的 11 段。
- 当前只有 ID 2–5 人工支撑下往返通过，整臂保持和连续回放尚未验收。最新硬件状态以 `docs/zh/Nano连接记录.md` 为准；本方案不会自动启用电机。

## 方案选择

采用“薄硬件适配器 + 共用动作执行器”：复用现有 CSV 校验和 `MotorService` 单 worker，新增明确的硬件接入边界及实验 CLI，网页随后复用同一执行器。

不采用直接运行上游 `replay.py`：它在读取 CSV 之前调用 follower.connect，触发 configure/使能，且缺少本次新校准目录入口及可靠的异常收尾。

不采用先扩展旧综合 Web/AI 控制链：旧 `web-control --with-outputs` 同时接入 LED，连接/导入路径还带有当前实验不需要的组件。

## 第一阶段组件

1. `ailamp_runtime/ailamp/services/motor_backend.py`：新增仅电机适配器，负责只读连接、原始位置/校准校验、明确的保持使能、发包和反馈。使用 `LeLampFollowerConfig` 显式传新校准目录、`id=lelamp`、空摄像头、非角度模式，但只调用 `bus.connect()`，绝不调用 follower.configure/calibrate/setup_motors 或写 EEPROM 校准。
2. `ailamp_runtime/ailamp/services/motor_runtime.py`：新增实验执行器，复用 `MotorService(robot=adapter)` 的唯一发送 worker，统一处理 play/home/script/stop/state。普通动作忙时拒绝新任务，剧本内部逐段等待完成；stop 可抢占并取消后续步骤。不另建第二个发包者。
3. `ailamp_runtime/ailamp/motor_cli.py`：新增轻量 CLI/交互实验入口，不走旧综合 CLI 的 AI/视觉/音频导入链。支持 list、preflight、arm、capture-home、play、home、script、stop、status、release、quit。默认模拟；真实连接与真实使能分离。

允许对 `services/motor.py` 做必要的最小兼容扩展，但保留既有 API 和回归行为，不重构无关控制器。

## 执行规则

- help/list/离线预检不打开串口；真实 connect 只读，先校验明确指定的文件、哈希、ID 映射、硬件校准和原始位置。不匹配即拒绝，不回退默认旧 Leader/Follower 文件。
- connect 同时读取五轴实际 Torque_Enable 和模式。读取失败即为未知；若任一轴已使能或模式不符，拒绝自动 arm/修改出力/覆盖目标，不因“本进程尚未 arm”就推断电机未使能，也不静默卸力。只提供诊断状态，等待操作者明确处理。
- 先完整解析和校验整段/整份剧本，再允许第一次运动写入；归一化值不是角度，原始位置先检查再转换，避免底层归一化的裁剪掩盖越界。
- arm 是独立动作：现场支撑后读取新鲜实测位置、确认模式/扭矩状态，先设置明确提供且有界的 SRAM 速度、加速度和出力参数，再用当前实测位置预置五轴目标并核对使能状态。Goal_Position 写入本身可能使能，必须按运动操作处理。
- 不因 connect 或打开页面自动提高出力。arm 的出力参数须显式提供、校验范围并记录；实验结束反馈只能说明实际测得结果，不能把寄存器上限当作额定承重。
- 仅一个实例拥有串口；端口别名必须归一到相同资源。现有 flock 是合作锁，不能冒充对上游不遵守锁的程序也有保护；实验前仍核对端口占用。
- 保存 home 时读取五轴实测值，而不是已发送目标缓存；文件包含原始/归一化位置、单位、时间、机器人 ID、关节映射和校准 SHA-256。必须由操作者确认捕获姿态；缺失或哈希不符时 home 拒绝执行。
- 从当前姿态到首帧、段间衔接和归位均使用有界插值，保留所有原始帧。原版以 FPS 调度而不依赖 CSV 时间戳；实际插值/降速后的时长应据计算结果报告，不承诺恰好 80 秒。
- 初版剧本顺序：`wake_up → nod → curious → shy → headshake → scanning → happy_wiggle → home`。每个场景明确标为模拟触发；单段入口仍支持其余通过预检的动作。`idle` 不是静态保持命令。
- “已发送”“反馈到位”“用户确认可见”分开记录。使用反馈检测到位和超时；状态缺失或通信失败不得显示完成。允许在 CLI 参数中明确指定有界反馈容差/超时，离线测试注入时钟，避免测试真实等待。
- 正常 stop 先取消后续帧和剧本，再由唯一发送 worker 读取新鲜实测位置并设置保持目标；不能仅停止排队就宣称“已停在当前位置”，因为电机可能还在追赶上一目标。正常保持成功需反馈确认，不自动卸力。
- 反馈/发送/通信故障时锁存错误、取消后续动作，禁止自动重试或恢复动作；仅报告“保持状态不确定”，不尝试把未知/越界读数写作保持目标。release 必须单独确认物理支撑。
- 正常 quit 在已使能时拒绝直接退出，要求先 release，或显式选择 leave-holding 并提示退出后不再监测。EOF/信号退出应取消发送并结束 worker；通信可用时执行正常 stop，故障时只报告最后已知状态，关闭串口但不静默卸力。SIGKILL、断电或网络故障不能保证软件收尾执行，不称为硬件急停。
- 默认只读或模拟不产生任何电机写入。代码实现阶段不连接 Nano、不部署、不触发真实动作；部署与现场实验由主操作者在代码验证后单独进行。

## 第二阶段页面边界

第一阶段动作实测完成后再实现 `motor_web.py` 和相应页面，按钮调用同一执行器，不能逐个 HTTP 请求重新连接电机。网页拥有执行器时，CLI 仅作为客户端，不启动独立硬件实例。

页面显示所有动作及其验证状态；未通过预检/实测的项不能显示“已验证”。包含独立动作、一键剧本、归位、停止并保持、支撑确认后释放、当前任务与实测/目标区分。采用现有 Web 的 Host/Origin/CSRF 与 token 安全模式；非回环访问不能无认证。页面不调用 AI、LED、摄像头或麦克风。

## 验收

- Fake bus/robot 测试覆盖：只读连接无写入；校准/哈希/原始范围不匹配拒绝；五轴/NaN/空 CSV/路径穿越拒绝；别名串口互斥；使能前参数和当前目标准备顺序；忙状态和单发送者；中断剧本且不卸力；归位取实测且匹配校准；发送故障锁存；到位超时不报完成；每条原始五轴目标保留；无 LED/AI/音视频依赖启动。
- 用本轮 Nano 13 段 CSV 快照与新校准做离线全量预检，保留各文件哈希、帧数、估算时长和结果。
- 运行现有 `test_motor_service.py`、`test_motor_runtime.py` 及新增测试，必要时扩展相关回归；离线通过不称为实体完成。
- 实体验收顺序：只读预检 → 当前姿态五轴保持 → 捕获确认的 home → 单段与归位 → 完整剧本 → 再开发页面。记录参数、目标、实测、错误和现场确认。

## 已确认的 Codex 编程任务（仅第一阶段）

上下文：工作区 `/Users/yugu/Documents/New project 4/AILamp`，遵守 AGENTS.md。先阅读本方案、`docs/zh/Nano连接记录.md`、`ailamp_runtime/ailamp/services/motor.py` 和原 `../lelamp_runtime/lelamp/follower/`。新校准和 Nano 13 段快照位于 `output/hardware_backups/LeLamp-demo-calibration-20260908-7QE2U0/`。

任务：实现本方案第一阶段的仅电机适配器、共用执行器、轻量 CLI 与测试，使它能先离线验证、再由主操作者部署进行完整五轴动作实验。第二阶段网页本次不写。先用测试复现现有接入边界问题，再按批准方案做最小实现。

约束：不访问 Nano、不运行 SSH/sudo/电机接口、不联网安装依赖、不覆盖校准/CSV、不修改原 LeLamp 库、不提交 Git。以 fake bus/robot 做所有测试；保持原五轴每帧、不使用 LED/AI/摄像头/音频。通过 apply_patch 编辑。作为代码实现端不再次启动 Codex 委托，遇到缺失信息先报告，不自行扩大范围。

输出：新增上述三个模块，新增 `tests/test_motor_backend.py`、`tests/test_motor_demo_runtime.py`、`tests/test_motor_cli.py`，按需要最小修改 `services/motor.py`；补充使用说明和离线测试结果，列出改动文件。主操作者在用户确认本方案后先整理实现计划，再启动此编程任务；实现端不得自行部署或上电验证。
