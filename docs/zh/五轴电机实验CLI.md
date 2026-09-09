# 五轴电机实验 CLI（第一阶段）

本入口默认是离线模拟，不会打开串口。代码测试与离线预检不能证明实体动作、碰撞、负载或承重已经通过。

```bash
PYTHONPATH=ailamp_runtime python3 -m ailamp.motor_cli list
PYTHONPATH=ailamp_runtime python3 -m ailamp.motor_cli preflight
PYTHONPATH=ailamp_runtime python3 -m ailamp.motor_cli shell
```

模拟模式默认只使用 `output/five_axis_demo_home.simulation.json`，不会再与真实硬件共用默认 home 文件。

交互会话命令：

```text
connect
arm 300 20 250 CONFIRM
capture-home CONFIRM
play wake_up
wait
status
home
script
stop
confirm wake_up 现场可见
release CONFIRM
quit
```

- `connect` 只读连接并检查校准、原始位置、扭矩和模式；不会自动 arm。
- `arm` 的三个参数依次是 Goal_Velocity、Acceleration、Torque_Limit，必须由现场操作者支撑整臂后明确确认。
- 默认剧本为 `wake_up → nod → curious → shy → headshake → scanning → happy_wiggle → home`，其中触发均为模拟情景。
- 日志中的 `sent`、`feedback_reached`、`operator_confirmed` 含义不同，不能互相替代。
- `stop` 会取消后续帧，由唯一发送 worker 新读实测位置并保持，不会自动卸力。
- 已 arm 时普通 `quit` 会拒绝；先支撑并 `release CONFIRM`。`quit leave-holding` 仅用于明确选择带保持退出，退出后程序不再监测。
- EOF、SIGINT 或 SIGTERM 会先尝试正常 stop，再关闭连接；关闭使用 `disable_torque=False`，不会静默卸力。

真实硬件模式只能由现场主操作者显式选择：

```bash
PYTHONPATH=ailamp_runtime python3 -m ailamp.motor_cli --hardware --home-path output/five_axis_demo_home.hardware.json shell
```

真实硬件模式必须显式传入专用 `--home-path`，缺失时会在加载后端或连接总线之前拒绝启动。真实模式仍需先 `connect`，再按现场顺序单独 `arm`；本轮离线实现没有访问、部署或驱动 Nano。

本次实验 CLI 的数值边界为：`fps` 在 `(0, 30]`，`max-step-units` 在 `(0, 4]`，`feedback-tolerance` 在 `[0, 5]`，`feedback-timeout` 在 `(0, 30]` 秒，`feedback-poll-interval` 在 `(0, 1]` 秒。NaN、无穷大和越界值会在参数解析时拒绝。这些只是当前实验软件边界，不是电机额定承载、碰撞安全或物理安全证明，也不改变 `MotorService` 的通用 API 能力。
