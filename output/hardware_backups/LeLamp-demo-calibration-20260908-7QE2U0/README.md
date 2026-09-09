# LeLamp 五轴演示校准（2026-09-08）

本目录保存当前实体灯的新校准配置。机器人身份仍为 `lelamp`，ID 1–5 不变。

- Nano 主机目录：`/home/<nano-user>/Downloads/LeLamp-demo-calibration-20260908-7QE2U0`
- 容器内目录：`/home/LeLamp-demo-calibration-20260908-7QE2U0`
- 原运行容器：`2082fbab718a`
- 文件：`lelamp.json`
- SHA-256：`8285b8c0d24239046dec3db33e305fecbec5a4be2f9c3a4c2e718ed068a943c1`

## 变更与验证

只修改 ID 1 / base_yaw 的校准：Homing_Offset `-223 → 811`，范围 `1210..2841 → 1588..2506`。ID 2–5 校准逐项读回确认未变。新配置由实际寄存器读回后使用原运行库导出，并在独立的新进程中重新加载验证。

用户选定的原坐标 `3081` 映射为新坐标 `2047`，归一化值为 `0`。人工确认的原坐标两侧为 `2480` 和 `3572`；实际采用范围相当于原坐标 `2622..3540`，围绕所选零点对称，保留余量。完整五轴 CSV 可保留，但 ID 1 的物理动作幅度会按新校准范围重新映射。

验证结果：新配置与五轴寄存器匹配；五轴当前位置均在各自范围内；Torque_Enable 均为 `0`；所有 Lock 均为 `1`；检查结束后串口释放。此次没有发送 Goal_Position、没有提高出力、没有进行全臂动作或独立承重验证。

## 使用边界

后续程序必须显式使用上述新校准目录。原容器默认路径下的 Leader/Follower 文件是旧配置，特意保留且未覆盖；不要通过默认校准流程把旧文件重新写回电机。

原始备份保留在相邻的 `LeLamp-calibration-backup-20260908-g0jqYZ`，本目录不替代原始备份。所有恢复或再次改校准前先核对当前硬件状态。

关节临时 SRAM 出力/速度参数不在本 JSON 内：ID 1 仍为 Goal_Velocity `0`、Acceleration `0`、Torque_Limit `1000`；ID 2–5 仍为 `50/5/200`。不能仅凭加载本配置就认为整臂已经能保持重量。

## 原动作快照

`nano_recordings/` 是从 Nano 主机 `/home/<nano-user>/Downloads/lelamp_runtime/lelamp/recordings/` 通过 SCP 取得的 13 个原始 CSV，每个文件与本轮 Nano 源文件 SHA-256 相同。它包括旧 Mac 动作库没有的 `test01`、`test02`。

现有 `RecordingStore` 对全部 5488 帧的五轴列、有限数值和 `[-100,100]` 范围检查通过；这只是数据预检，不证明全身动作或承重已通过。具体方案与待确认的实现任务见 `docs/superpowers/specs/2026-09-08-five-axis-physical-demo-design.md`。
