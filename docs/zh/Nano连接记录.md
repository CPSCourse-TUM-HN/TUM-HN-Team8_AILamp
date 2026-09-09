# Jetson Nano 连接记录

最近进展：2026-09-09 00:35（Asia/Shanghai）。第二轮剧本在 wake_up 末尾等待 30 秒仍未达到 2 单位容差，未启动后续动作；2 号关节稳定偏离原始目标 33 counts。随后只读采样中，2 号 Present_Temperature 出现 `33 → 33 → 99` 的异常读数，已立即要求用户托稳机械臂后切断舵机电源，Nano 可保持供电，不直接触摸电机外壳。尚未收到断电确认，最后已知五轴 Torque_Enable=1；串口和 Python 已关闭。必须先核实温度异常和供电状态，禁止继续剧本、自动归位、提高出力或放宽到位容差。

## 2026-09-09 00:30–00:35 第二轮剧本与温度异常暂停

- 用户回复“已就位”，确认重新托稳后，只读复查校准、位置、模式和状态；然后明确将五轴 Torque_Enable 写为 0，并逐轴读回全部为 0。没有带旧目标直接使能。
- 启动同一版本 CLI，唯一参数变化为 `--feedback-timeout 30`；仍为 `--fps 15 --max-step-units 2`、反馈容差 2。只读 connect 后用 `arm 100 10 500 CONFIRM` 重新保持当前实测姿态，fresh stop-and-hold 通过；没有覆盖已捕获的 home。
- 已提示用户移开手并等待 5 秒。约 00:32:02 启动完整默认 script：wake_up → nod → curious → shy → headshake → scanning → happy_wiggle → home。
- 约 00:33:08 检查到 `fresh motor feedback did not reach target within 30s`。当前目标仍是 wake_up 最后一帧，证明剧本停在第一段，后续 nod 等没有启动；本轮不能记录为完整剧本通过。用户现场回复“正常”，仅记录为可见动作反馈，不能替代传感器到位判定。
- 超时目标（ID 1–5，归一化）：`-2.235180/-46.232179/72.017167/5.625606/37.389202`；末次反馈 `-3.267974/-51.183432/71.715902/5.219638/37.087168`。主要差值在 2 号，约 4.9513 单位，超过当前容差 2。
- 退出 CLI 时保留扭矩，之后只通过独立原 SDK 总线连接做读取。原始 Goal_Position 为 `2037/1712/3008/2037/2368`；连续三次 Present_Position 为 `2032/1679/3006/2033/2365`，保持不变。2 号恒差 33 counts，不是单纯继续延长超时时间即可保证解决；负载下稳态误差、机械受力等原因尚未最终确定。
- 三次采样五轴 Present_Velocity、Moving、Status 都是 0。最后核实 Torque_Enable 全部为 1；Goal_Velocity/Acceleration/Torque_Limit 均仍为 100/10/500；P/I/D 为 16/0/32，CW/CCW 死区为 1/1，最小启动力为 16。以上仅为读回，未修改 PID、扭矩、死区或任何 EEPROM。
- 2 号 Present_Load 原始值为 1168，Present_Current 为 7/8/8；这些原始编码值不是直接的力矩百分比或安培。各轴电压原始值约 120..121。
- **关键异常：三次温度采样（ID 1–5）分别为 `31/33/32/30/32`、`31/33/32/30/33`、`31/99/32/30/33`。** 2 号出现突跳，尚不能区分真实过热、传感器/通信读数异常；Status=0 不能据此排除风险。不再提出扩大容差继续表演。
- 随后已执行 `bus.disconnect(disable_torque=False)` 并确认 serial_closed=True，退出 Python，SSH 位于 Nano shell；没有自动卸力造成坠落，也没有新运动写入。已要求用户立即托稳再切断舵机电源，不直接触摸电机外壳；截至本条记录尚未确认已断电。
- 后续顺序：确认舵机供电已断开、结构被支撑 → 冷却并核实 2 号温度异常与机械受力 → 在重新获准并确认现场条件后检查，不自动恢复回放。当前不能声称全套动作、归位可靠性或热稳定性验收通过。

## 2026-09-09 00:25–00:29 wake_up 与归位实验

- 用户回复“可以维持”，确认本次五轴同时保持时整臂能维持姿态。CLI 中已记录 `confirm arm`，随后 `capture-home CONFIRM` 从实测值保存 `/home/AILamp-motor-demo-20260909-7CeZo8/home.hardware.json`。
- 归位目标（ID 1–5 原始值）：`2098/2135/1812/2065/2183`；绑定的新校准哈希不变。普通 <nano-user> 账号 SCP 读取该新文件被权限拒绝，未修改权限或绕过；目前原文件仅在 Nano，未声称已备份到 Mac。
- 执行 `play wake_up`，保留原 258 帧完整五轴目标，使用 fps=15、max-step-units=2 及有界过渡；速度/加速度/出力参数为 100/10/500。未提高出力或更改校准。
- wake_up 最终目标（归一化，ID 1–5）：`-2.235180/-46.232179/72.017167/5.625606/37.389202`；反馈为 `-3.050109/-45.414201/70.081710/5.426357/36.978885`，各轴误差均不超过本次 2 单位容差。`busy=false, fault=null`。用户随后反馈“没问题”，已在会话记录 wake_up 的现场确认。
- 之后发出 `home`。目标完整发送，但最终反馈在 10 秒内未到位，锁存 `fresh motor feedback did not reach target within 10s`。超时末次反馈为 `11.111111/12.426036/-20.427404/8.010336/17.920953`，其中肘关节与 home 差约 57.95 归一化单位。没有忽略错误继续播放剧本，也没有提高力矩或盲目重试。
- 注意：当前实现锁存超时后取消后续任务，但舵机可能继续执行最后写入的归位目标；软件状态中的 holding 不等于故障发生时已经机械停止。因此向现场明确提示可能仍在追赶目标，退出 CLI 保留扭矩，再使用同一串口的独立只读诊断核对，未向电机发送新的位置/参数。
- 后续直接读回：Goal_Position 仍为 home；Present_Position 为 `2097/2129/1825/2060/2188`，均已回到 2 归一化单位容差内（最大约 1.54）。三次约一秒间隔采样的位置完全相同，Present_Velocity、Moving 和 Status 全部为 0；Torque_Enable 全部为 1；校准匹配为 True。
- 温度寄存器 `31/31/31/30/32`；电压原始值约 `120..121`。Present_Load 原始值 `0/1060/64/1056/32`，含编码信息，不能直接当作力矩百分比。未根据状态 0 断言所有负载/碰撞风险都已排除。
- 本轮结果是“wake_up 反馈通过 + home 曾超时后实际到位”，不是“home 在既定时限内通过”。下一轮单变量调整建议：只将 `--feedback-timeout` 从 10 改为 30；保持 fps=15、max-step-units=2 和 100/10/500 不变，观察能否在新窗口内完成，不据此假定所有动作均已验收。
- 已 `bus.disconnect(disable_torque=False)` 并确认 serial_closed=True，退出 Python 至 Nano shell。五轴仍使能并保持；未自动卸力。下一轮需要用户重新托稳后进行明确的释放/重新启用，不能通过篡改内存状态绕过已使能或故障拦截。

## 2026-09-09 首次五轴同时保持

- 用户已明确确认机械臂有支撑、周围无障碍且可随时断电。连接前再次检查串口无其他占用；新版 CLI 只读诊断仍为 can_arm=true，五轴模式均为 0、扭矩均为 0、位置均在范围内。
- 执行 `arm 100 10 500 CONFIRM`：先以新鲜实测位置预置完整五轴目标，再核对全部已使能。未修改 EEPROM 校准，未写入旧目标 0。
- 之后执行 `stop` 获取新鲜实测位置并保持，等待反馈到位后查询 `status`：`armed=true, busy=false, fault=null, torque_state=holding, position_source=fresh_measured_hold`。
- 实测保持值与使能前相同：ID 1–5 原始位置 `2098/2135/1812/2065/2183`。归一化位置为 `11.111111/16.272189/-78.378378/8.527132/17.379534`。该反馈只证明此次采样时的位置一致，不证明整臂已能脱离人工支撑承重。
- `status.connection_diagnostics.torque_enable` 是 connect 时的历史快照（当时为 0），不是使能后的实时扭矩值；当前使能状态来自 arm 的寄存器读回，正常状态为 holding。
- 尚未执行 `capture-home`、`play` 或 `script`。用户现场保持反馈待确认，不能报告完整五轴动作实验已通过。
- SSH 会话仍在同一个真实 `ailamp-motor>` 中，持有唯一串口连接并保持五轴；不得另启动第二个硬件控制器。需要卸力时必须先确认支撑，再 `release CONFIRM`；不要直接断开程序并误以为电机已卸力。
- 本次 CLI 使用 `--fps 15 --max-step-units 2 --feedback-timeout 10`，作为后续降速实验参数，目前没有启动 CSV 回放。

## 2026-09-09 00:15–00:21 新版部署与实机只读预检

- USB `<nano-user>@192.168.55.1:22` 已重新认证成功，严格复用已保存的主机密钥。旧 Wi-Fi 地址 `172.20.10.10:22` 本轮超时；Nano 当前 IPv4 列表未显示 wlan0 地址。本轮使用 USB，不依赖 Wi-Fi、OpenAI、LED 或音视频。
- 原容器 `2082fbab718a` / `lelamp:V1.0` 已运行约 4 小时；未重启、重建或安装依赖。Downloads → `/home`、`/dev` → `/dev` 映射仍在，容器 Python 为 3.12.0。
- 最新六个必要 Python 源文件上传到独立目录 `/home/<nano-user>/Downloads/AILamp-motor-demo-20260909-7CeZo8`（容器内 `/home/AILamp-motor-demo-20260909-7CeZo8`）。没有覆盖原 `/home/<nano-user>/Downloads/lelamp_runtime`、校准文件或 CSV。上传压缩包双端 SHA-256 均为 `63f934abab398466b49424a64cdc342ea55185261b4d5e89125a69fd16f33476`。
- Mac 全仓库测试：`327 passed in 5.04s`。Nano 使用原容器解释器和实际原动作目录执行新版 `preflight`：13 段、5488 帧通过，各 CSV 哈希与本地快照一致。
- 新校准文件 SHA-256 仍为 `8285b8c0d24239046dec3db33e305fecbec5a4be2f9c3a4c2e718ed068a943c1`。实际 SDK 的只读校准比较通过；没有写 EEPROM、没有调用 follower.configure/calibrate/setup_motors。
- 串口 `/dev/ttyACM0` 与稳定别名 `/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B14033387-if00` 存在。连接前 `sudo fuser` 未发现占用。
- 新版真实 CLI 执行 `connect`、`status` 后正常 `quit`；未执行 arm/play/home/release。诊断为 `can_arm=true, issues=[], armed=false, fault=null, torque_state=verified_all_off`。这表示软件前置条件满足，不代表承重或实体动作通过。
- 五轴实时原始位置（ID 1–5）：`2098/2135/1812/2065/2183`；Operating_Mode 全部 0；Torque_Enable 全部 0。所有位置均在当前校准范围内。
- 实机 home 路径预留为 `/home/AILamp-motor-demo-20260909-7CeZo8/home.hardware.json`，尚未捕获，不可用模拟 home 代替。
- 只读检查后已退出 CLI、释放串口；SSH 留在 Nano shell。等待现场确认支撑、清空运动空间且可随时断电，然后先做五轴当前姿态保持，再记录 home、单动作及完整剧本。
- 昨晚生成的交接 ZIP 是打包时快照，不自动包含本次新增部署和记录。

本次真实 CLI 启动命令（在 Nano SSH shell 中；启动本身不会使能）：

```bash
sudo docker exec -it \
  -e PYTHONPATH=/home/AILamp-motor-demo-20260909-7CeZo8:/home/lelamp_runtime \
  -w /home/lelamp_runtime 2082fbab718a \
  /home/lelamp_runtime/.venv/bin/python -m ailamp.motor_cli \
  --hardware \
  --calibration-dir /home/LeLamp-demo-calibration-20260908-7QE2U0 \
  --recordings-dir /home/lelamp_runtime/lelamp/recordings \
  --home-path /home/AILamp-motor-demo-20260909-7CeZo8/home.hardware.json shell
```

## 21:30 动作快照与软件接入准备

- 13 段原动作从 Nano 主机 `/home/<nano-user>/Downloads/lelamp_runtime/lelamp/recordings/` 复制到 Mac `output/hardware_backups/LeLamp-demo-calibration-20260908-7QE2U0/nano_recordings/`，逐文件 SHA-256 与 Nano 源相同。
- 现有 `RecordingStore` 全量读取通过：`curious=177, excited=144, happy_wiggle=149, headshake=144, idle=1798, nod=187, sad=149, scanning=189, shock=144, shy=220, test01=1123, test02=806, wake_up=258` 帧，共 5488 帧。检查五轴列、有限数值与归一化范围；没有据此宣称实体动作通过。
- 最新只读位置 `2098/2135/1812/2065/2183`，Torque_Enable 和 Status 全部 `0`，新配置匹配为 True，读取后串口关闭。
- 仍保留旧 Goal_Position：`0/2421/1803/2058/2160`。尤其 ID 1 的目标 `0` 与新范围不符，ID 2 目标与当前位置也相差较大；后续必须先按新鲜实测姿态准备目标，不能直接全轴 enable。
- 方案：`docs/superpowers/specs/2026-09-08-five-axis-physical-demo-design.md`，包含仅电机适配器、共用执行器、实验 CLI、测试与后续页面边界。已自审并补清正常停止保持、通信故障及未知扭矩状态的处理。当前没有实现或部署这些新增模块，未触发任何动作。

## 21:15–21:20 1 号校准完成

- 用户归中后原坐标读数为 `3132/2135/1812/2065/2183`。最初选定的中立朝向仍保留为原坐标 `3081`，没有把近似归中后的 `3132` 偷换为零点，也没有要求用户继续精细摆动。
- 根据 Nano 实际 SDK 的 `Present_Position = Actual_Position - Homing_Offset`，只为 ID 1 写入 Homing_Offset `811`、Min_Position_Limit `1588`、Max_Position_Limit `2506`；Lock 暂时设 `0` 以写校准，逐项读回后恢复 `1`。没有调用全轴校准/重置函数。
- 换算为 `p_new = p_old - 1034`；旧零向 `3081` 对应新坐标 `2047`，SDK 实测其归一化值为 `0.0`。工作区间对应旧坐标 `2622..3540`，对称半宽 `459 counts`（约 40.3°），位于人工确认的 `2480..3572` 之内。
- 其他四轴校准逐项与写入前快照比较相同。完整五轴原 CSV 可以保留，但 ID 1 的实际转角会随新校准跨度重新映射，不能声称与原作者硬件的物理幅度完全一致。
- 新文件 Nano 主机路径：`/home/<nano-user>/Downloads/LeLamp-demo-calibration-20260908-7QE2U0/lelamp.json`；容器内路径：`/home/LeLamp-demo-calibration-20260908-7QE2U0/lelamp.json`。后续配置参数 `calibration_dir` 应使用这个**目录**，`id` 仍为 `lelamp`。
- Mac 已通过 SCP 保存原始字节：`AILamp/output/hardware_backups/LeLamp-demo-calibration-20260908-7QE2U0/lelamp.json`；同目录有说明文件。双端 SHA-256 相同：`8285b8c0d24239046dec3db33e305fecbec5a4be2f9c3a4c2e718ed068a943c1`。
- 原容器 Leader/Follower 文件均未覆盖，哈希仍分别为 `7fa29fb124cd86a702a02a73a91f87fb160aee6b59380f0069235111c5bb863c` 与 `718ab2c24c51a643f19fc3f4a0ab59de1c1d82b849ac7e258839e6aaf3a3590d`。它们现在是保留的旧配置，**不可通过默认校准流程重新写回**。
- 使用新目录启动独立 Python 进程，只调用 `bus.connect()` 而不调用 `follower.connect()`：`is_calibrated=True`；原始位置 `2098/2135/1812/2065/2183` 全部在范围内；五轴 Torque_Enable 均为 `0`；五轴 Lock 均为 `1`；串口关闭成功，21:20 `fuser` 无占用。
- SRAM 参数未变：ID 1 的 Goal_Velocity/Acceleration/Torque_Limit 为 `0/0/1000`，ID 2–5 为 `50/5/200`。尚不能把新校准完成称作整臂独立承重或完整剧本运行通过。

## 后续操作页面（用户明确要求在动作实验之后）

- 顺序：先做整臂保持和完整五轴原动作实验，再实现操作页面；不把页面按钮存在当作硬件动作已经通过。
- 页面至少支持动作库单独触发、一键剧本演示、归位和执行状态；停止当前动作与释放扭矩必须区分，不让普通“停止”无提示地卸力导致机械臂下落。
- “归位”应返回记录并确认的五轴起始姿态，不是把五个舵机原始编码器值全部写成 `0`。
- 网页与实验工具应复用同一动作执行队列/硬件连接，避免多个进程争用 `/dev/ttyACM0`；LED、AI 云调用、摄像头/音频自动触发不是当前剧本演示的依赖。
- 网页尚未实现、未部署；Nano 上的网页端口仍未验证，不能把 Mac 旧调试台 `127.0.0.1:8766` 称为已接入本次真实硬件的页面。

## 21:12 记录 1 号另一侧位置

- 用户再次回复“到了”后只读采样：`base_yaw=2480`、`base_pitch=2135`、`elbow_pitch=1812`、`wrist_roll=2065`、`wrist_pitch=2183`。
- 相对用户选定中立参考 `3081`，这一侧为 `-601 counts`，约 `-52.8°`；另一侧已记录为 `3572`，约 `+43.2°`。两侧总跨度 `1092 counts`，约 `96.0°`。
- 两次手动调整只有 ID 1 读数发生变化。这里只记录人工到达的位置，不宣称机械硬极限、带载能力或其他关节姿态下的全空间无碰撞已验证。
- 五轴 Torque_Enable 和 Status 均为 `0`；1 号原校准仍为 `Homing_Offset=-223, Min_Position_Limit=1210, Max_Position_Limit=2841`。读取后 `serial_closed=True`，没有任何电机寄存器写入。
- 后续中立映射须保留用户选定朝向；不能直接把两端平均值 `3026` 当作用户指定的 `3081`。拟定工作范围应位于实测两侧参考之内，并预留余量；尚未确定或写入新限位。

## 21:09 记录 1 号第一侧位置

- 用户回复“到了”后只读采样：`base_yaw=3572`、`base_pitch=2135`、`elbow_pitch=1812`、`wrist_roll=2065`、`wrist_pitch=2183`。
- 与用户选定的中立朝向 `3081` 相比，只有 1 号变化 `491 counts`，按 4096 counts/圈约为 `43.2°`。这确认此次手动调整确实作用于 ID 1；不是电机通电驱动测试，也不是机械极限测量。
- 已将 `3572` 记录为本次人工确认的一侧演示范围参考，尚未作为舵机硬限位写入；另一侧参考仍待测。
- 五轴 Torque_Enable 和 Status 均为 `0`，1 号 Homing_Offset/Min_Position_Limit/Max_Position_Limit 仍为 `-223/1210/2841`；没有写入任何电机寄存器。读取后 `serial_closed=True`。

## 21:01–21:05 五轴剧本路线与 1 号零点核对

- 现场照片：`/Users/yugu/Downloads/IMG_1220.HEIC`。照片中露出的舵机可见“2”标签，安装在长臂根部的俯仰连接处；按照原 LeLamp 装配资料，1 号对应它下方使整块支架相对底座水平转动的关节。1 号本体/标签未在照片中完整露出，不能仅据照片进一步判断内部紧固或摩擦情况。
- 用户选定“必须完整五轴原动作，先解决1号启动位置与校准范围问题”，不采用四轴掩码或以纯视频代替实体动作；演示触发为预设情景，不应宣称是真实 AI/摄像头/音频自动触发。
- 最新只读值：位置 `3081/2135/1812/2065/2183`，五轴 Torque_Enable 均为 `0`，Status 均为 `0`；Torque_Limit 为 `1000/200/200/200/200`。因此 1 号并非保留了与 2–5 号相同的低出力测试上限，但当前仍未使能，未验证带载驱动力。
- 1 号寄存器仍为 Homing_Offset `-223`、Min_Position_Limit `1210`、Max_Position_Limit `2841`、Operating_Mode `0`；当前 `3081` 比旧上限高 `240`。这只说明当前姿态与所记录范围不对应，不等于电机损坏。
- Nano 实际 vendored Feetech 源码说明 `Present_Position = Actual_Position - Homing_Offset`。若只更换坐标原点，同时等量变换原限位，当前点仍在原物理范围之外；若保留数值范围而改偏置，则会改变物理运动包络，不能称为无影响的“归零”。
- 实际 `set_half_turn_homings()` 先调用 `reset_calibration()`，后者会把所选轴限位设为 `0..4095`；本轮**没有调用**这些方法，也没有自动运行全五轴 `calibrate()`。原 Leader/Follower 备份和舵机校准均未改动。
- 后续需要以用户选定的当前朝向为中立参考，实测 1 号左右可动区间，再建立只影响 1 号的新校准映射。仅记录当前点或调大扭矩不能替代左右范围核对。2–5 号不重复单轴验收、不重写既有校准。
- 只读检查结束后 `serial_closed=True`；21:05 `sudo -n fuser -v /dev/ttyACM0` 无占用输出。SSH 保留在 Nano shell，容器未重建，没有启动后台运动程序。

## 用户现场确认与传感器进展

- 用户明确确认所有电机本体无问题。这是用户的现场判断，与下方远程实测证据分别保留；不再仅凭 1 号读数越界推断电机故障。
- 本轮只读复查位置为 `3081/2135/1812/2065/2183`（按 ID 1–5）；Torque_Enable 和 Status 均为 0；与当前 Leader 校准参数匹配检查为 True。随后关闭串口，没有发送运动或校准写入。
- 1 号当前位置仍高于记录上限，这一运行姿态/校准对应问题没有因用户确认电机本体正常而自动消失；没有放宽限位、覆写校准或启用全臂保持。
- reSpeaker 通过现有主机 PulseAudio 输入源 `alsa_input.usb-Seeed_Studio_reSpeaker_XVF3800_4-Mic_Array_114993701262400965-00.analog-stereo`，执行 `arecord -D pulse` 两秒采集，S16_LE、16 kHz、双声道，退出码为 0。
- 音频全部丢弃到 `/dev/null`，没有保留录音，没有安装 PortAudio、没有停止 PulseAudio 或修改默认音频设备。该结果证明采集通路可用，尚未验证音频幅度、拍手检测或语音内容。
- 容器内 `sounddevice` 缺 PortAudio 的限制仍存在；本次成功的是**主机经 PulseAudio 的 ALSA 采集通路**，不能写成容器内音频模块已经修复。
- 后续重点为正确的启动/保持逻辑与传感器联动，不重复单电机验收；整臂独立承重和云 AI 调用仍未验证。

## 20:40–20:43 逐轴运动结果（最新）

用户要求直接进行全部电机测试，并确认托举。在没有放宽限位、没有写入校准、没有运行原始整段 CSV 的前提下，先完成范围内的 2、4、5 号测试。用户随后手动收回肘部，3 号进入范围后补测通过。

| ID / 关节 | 起点 | 偏移目标 | 偏移后实测 | 返回目标 | 返回实测 | 验证状态 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 / base_yaw | 3049 | 未发送 | — | — | — | 超过记录上限 2841，仅通信/状态读取通过 |
| 2 / base_pitch | 2421 | 2330 | 2331 | 2421 | 2416 | 编码器跟随；用户确认可见往返 |
| 3 / elbow_pitch | 1803 | 1894 | 1892 | 1803 | 1812 | 手动回到范围后测试；编码器跟随，用户确认可见往返 |
| 4 / wrist_roll | 2058 | 2149 | 2144 | 2058 | 2061 | 编码器跟随；用户确认可见往返 |
| 5 / wrist_pitch | 2160 | 2069 | 2075 | 2160 | 2158 | 编码器跟随；用户确认可见往返 |

以上是舵机原始编码器计数，目标偏移 91 counts，按 4096 counts/圈约为 8°。每个受测轴先核对当前位置与目标，再独立设置 Goal_Velocity=50、Acceleration=5、Torque_Limit=200；每轴往返后单独关闭扭矩。没有同时启用多轴。

- 测试过程中受测轴 Status 均为 0，末次五轴 Torque_Enable 均为 0；最新温度寄存器为 `32/32/32/31/33`（按 ID 排列）。
- 校准匹配检查仍为 True，使用的是与当前寄存器一致的 Leader 校准文件读取路径；未将其覆盖到 Follower 文件。
- 用户在 20:40 明确答复：“这三个都看到了”（2/4/5 号）；20:43 对 3 号答复：“看到了，3号正常运动”。因此 2–5 号可以记录为**人工支撑下单轴运动验证通过**，不能称为整臂独立承重或全五轴验证通过。
- 当前保留的临时 SRAM 参数：ID 2/3/4/5 为 `Goal_Velocity=50, Acceleration=5, Torque_Limit=200`；ID 1 仍为 `0, 0, 1000`。未自动恢复更高扭矩限制。
- 已执行 `bus.disconnect(disable_torque=False)` 并确认串口关闭，退出 Python REPL；原容器仍运行，SSH 留在 Nano shell。
- 最新五轴位置为 `3049/2135/1812/2066/2183`。3 号测试前后，未使能的其他轴存在被动/人工姿态变化；不能把本测试解释为已实现全臂位置保持。
- 最后对 1 号单独读取：Present_Position `3049`，Min_Position_Limit `1210`，Max_Position_Limit `2841`，Homing_Offset `-223`，Operating_Mode `0`，Torque_Enable `0`，Status `0`。它在几次用户手动调整过程中始终约为 3050，原因尚未判定，不能据此断言电机损坏。
- 下一步核对底座与第一段臂连接部位的实物照片，确认 1 号的机械连接、实际被调整的部件及校准与装配对应关系，再决定测试方式；不自动更改/放宽限位。用户继续托稳，不依赖当前全关闭的电机承重。

## 20:36 暂停状态历史

- 用户已明确确认托举，并请求测试全部电机；随后确认已手动调整底座。
- 全轴读取成功；Status 均为 `0`，Operating_Mode 均为 `0`；校准仍与 Leader 备份一致。尚不能据此声称承重或运动测试通过。
- 调整后原始位置：`base_yaw=3047`、`base_pitch=2425`、`elbow_pitch=3088`、`wrist_roll=2056`、`wrist_pitch=2158`。底座读数与调整前基本相同，仍高于校准上限 `2841`；其余四轴在对应记录范围内。
- 温度寄存器读数为 `31/33/32/31/34`，电压寄存器原始读数为 `121/120/121/120/121`（按 ID 1–5 排列）。未根据状态 0 宣称没有任何机械故障。
- 在用户已托稳后，仅将 4 号 Torque_Enable 写为 `0`。最后读取确认 **ID 1–5 Torque_Enable 全部为 0**，没有发出任何新 Goal_Position。
- `r.bus.disconnect(disable_torque=False)` 后确认 `serial_closed=True`，已退出容器 Python REPL，SSH 返回 Nano shell。容器仍运行；电机串口不再由本诊断持有。
- 4 号临时 SRAM 参数仍为 Goal_Velocity `50`、Acceleration `5`、Torque_Limit `200`；未恢复原 `0/0/1000`。其他轴这些参数仍为 `0/0/1000`。后续测试必须明确处理，不要把有限出力当作额定承重表现。
- 等待一张能看清底座、整条机械臂与灯头的照片，以确认正确的手动调整轴。不要盲目继续扭动、自动放宽限位或将越界位置强行裁剪为有效目标。
- 用户必须继续托稳或使用可靠支撑物；五轴关闭扭矩时不能依赖电机承重。全电机动作测试和 8° 腕部测试仍未执行。

## 上一暂停点历史：先支撑机构，再继续

- 原容器 `2082fbab718a` 已启动，Python 3.12.0、`lelamp.follower` 导入通过；没有重装运行环境。
- 五个 ID 均通过总线握手。现场读取的校准与备份的 **Leader** 文件逐项一致，与 Follower 文件不同。没有把 Leader 文件覆盖到 Follower 路径，也没有写入舵机校准。
- 实测当前校准范围：base_yaw `[1210, 2841]`，base_pitch `[1349, 2701]`，elbow_pitch `[1640, 3231]`，wrist_roll `[1015, 2950]`，wrist_pitch `[1099, 2946]`。
- 最后位置原始读数：base_yaw `3049`，base_pitch `2421`，elbow_pitch `3251`，wrist_roll `2057`，wrist_pitch `2160`。底座水平轴和肘部超过各自记录范围；归一化函数会裁剪越界原始值，因此不能只检查归一化位置。
- 第一轮经用户许可测试 4 号腕部：起点 `2053`，目标 `2076`，实测最远 `2070`；返回目标 `2053`，实测 `2058`。编码器有变化，但用户反馈“没动”，因此不能称整机动作已验证。第一轮结束时五轴扭矩均恢复为 0。
- 第二轮用户批准约 8° 测试，但**尚未发出偏移目标**。仅完成当前位姿保持准备，用户随即报告“电机在自然状态下无法支撑整体重量”，故暂停。
- 最后核实的 Torque_Enable：ID 1/2/3/5 为 `0`；ID 4 为 `1`，目标与实测均为 `2057`，Status 为 `0`。4 号临时 SRAM 设置：Goal_Velocity `50`、Acceleration `5`、Torque_Limit `200`；原设置分别为 `0`、`0`、`1000`。不要误认为所有轴均已关闭或整臂已能保持。
- 本轮观察到写入当前 Goal_Position 后，4 号 Torque_Enable 变为 1；虽然没有调用 follower.configure，也不能把目标位置写入视为“不使能”。其他四轴未收到位置、扭矩或配置写入。
- 目前必须由用户先托稳灯头和机械臂。不要直接释放唯一已使能关节，也不要带着其他轴的旧目标 0 一键使能全部关节。全臂保持前需要核对姿态、范围和目标预置；不得自动改校准或放宽限位。
- SSH 中的容器 Python 交互会话仍打开，持有电机串口；后续不要另开电机进程争用接口。以上是暂停时快照，下次操作必须重新读取状态。
- 摄像头 `/dev/video0` 已实际读取一帧 `640×480×3`，驱动报告 30 FPS，随后释放设备，没有保存图像。
- reSpeaker 为 ALSA card 2，支持双声道、16 kHz、S16_LE 录放。容器 `sounddevice` 导入报 `PortAudio library not found`；主机 `arecord` 直接访问 `hw:2,0` 报 `Device or resource busy`，尚未采集成功。PulseAudio 有对应 reSpeaker 输入源；没有停止音频服务或安装依赖。
- 传感器触发、电机全臂回放、云 AI 联动均未完成；LED 仍排除。

## 当前可用连接

| 项目 | 本次验证值 | 状态 |
| --- | --- | --- |
| 连接方式 | USB 设备模式直连 Mac，同时通过 Wi-Fi 热点联网 | USB SSH 保留，Wi-Fi 已连接 |
| Nano USB 网络 IP | `192.168.55.1` | Ping 成功，2/2 响应 |
| SSH 登录端口 | TCP `22` | 已完成 `<nano-user>` 账号身份认证并进入终端 |
| Mac USB 网络 IP | `192.168.55.100` | 已由 Nano 的 DHCP 服务分配 |
| Mac USB 网络接口 | `en11` | active；到 Nano 的路由经过此接口 |
| USB 网络掩码 | `255.255.255.0`（/24） | 已验证 |
| Mac USB 串口 | `/dev/cu.usbmodem14228250656683` | 设备存在；尚未打开串口会话 |
| USB 设备名称 | `Linux for Tegra`，厂商 `NVIDIA` | 已验证 |
| USB VID:PID | `0955:7020` | 来自本机 USB 枚举 |
| Nano Wi-Fi IP | `172.20.10.10/28` | DHCP 分配；`wlan0` 已连接 |
| 热点名称 | `cc的iPhone5s` | 用户指定并授权连接 |
| 无线网卡 | USB `0bda:b812`，Realtek；驱动 `rtl88x2bu` | 已识别为 `wlan0`，未另装驱动 |
| 无线网关 / DNS | `172.20.10.1` | 来自热点 DHCP |
| 无线自动连接 | `yes` | 已保存该热点的 NetworkManager 配置 |
| Nano 登录用户名 | `<nano-user>` | 用户提供，`whoami` 实测确认 |
| Nano 主机名 | `nvidia` | `hostname` 实测确认 |
| 板卡标识 | `NVIDIA Jetson Nano Developer Kit` | 来自 `/proc/device-tree/model` |
| 操作系统 | Ubuntu 18.04.6 LTS，aarch64 | SSH 登录横幅确认 |
| 内核 | `4.9.253-tegra` | `uname -r` 实测确认 |
| NVIDIA L4T | R32.7.1 | 来自 `/etc/nv_tegra_release` |

SSH 服务响应：`SSH-2.0-OpenSSH_7.6p1 Ubuntu-4ubuntu0.5`。

## 登录方式

在 Mac 终端运行：

```bash
ssh -p 22 <nano-user>@192.168.55.1
```

密码只在 SSH 的密码提示处输入，不另存到本文件、脚本或记忆。用户明确同意保存主机密钥后，已将以下 ED25519 主机密钥加入 Mac 的 SSH known_hosts，并成功登录：

`<nano-host-key-fingerprint>`

后续若指纹变化，应先核实原因，不禁用主机密钥检查。

### 无线连接说明

当前 Nano 的无线地址为 `172.20.10.10`，SSH 服务端口仍为 `22`。此地址来自 DHCP，热点重连后可能变化。

Mac 必须能通过局域网到达该无线地址，才可以使用它登录。本次检查时 Mac 的 Wi-Fi IPv4 仍为 `192.168.74.248`，到 `172.20.10.10` 的路由经过 `utun4`，而不是已验证的热点直连路径。因此尚未验证 Mac 到 Nano 的无线 SSH，当前仍使用 USB SSH；不要为了改用无线而提前拔掉 USB 线。

仅在 Mac 已连接该热点且确认设备互通后，可用下面的命令复用已经核对的 Nano 主机密钥，检查无线登录：

```bash
ssh -o HostKeyAlias=192.168.55.1 -o StrictHostKeyChecking=yes -p 22 <nano-user>@172.20.10.10
```

`HostKeyAlias` 使用此前 USB 登录保存的同一台 Nano 的主机密钥，仍严格检查指纹。该无线登录命令尚未执行。

### 本次 Wi-Fi 配置与外网验证

- 用户授权 `<nano-user>` 执行本次 NetworkManager 管理员认证。只连接并保存了指定热点，没有修改系统权限策略，没有安装驱动，没有启动硬件控制。
- 连接配置 UUID：`d29a2e26-8bb7-41c9-8853-96d61c144ba0`；`connection.autoconnect: yes`。
- Wi-Fi 默认路由：`default via 172.20.10.1 dev wlan0 metric 600`；原 USB 默认路由仍保留，metric 为 `32766`。
- Nano 通过 HTTPS 访问 `https://www.baidu.com/` 返回 `200 OK`，确认至少这条通用互联网访问路径可用。
- 无凭据探测 `https://api.openai.com/v1/models` 时，域名获得了解析结果，但 IPv6 和 IPv4 的 TCP 443 连接均超时。没有调用模型、没有提交 API 密钥，不能宣称 OpenAI 已可用；具体原因未确定。
- 系统时间读取为 `2026-09-08T20:04:52+08:00`，与 Mac 当前时间相符；未手动调整时钟。
- Wi-Fi 密码通过交互式提示输入，仅由 Nano 的正常网络连接配置机制处理，不另存到项目文件、脚本或记忆。

## 验证边界

- 已验证 USB 设备、串口存在、USB 网络可达和 SSH 服务响应。
- 已登录 Nano，完成 Docker、控制程序、校准和后续有限硬件检查；4 号小幅测试只有编码器证据，用户尚未确认可见动作。摄像头单帧采集通过，模型调用未验证。
- 没有重新校准或运行整段动作，LED 不纳入当前功能方案。当前扭矩状态以本文顶部暂停记录为准。
- Mac 上的 `127.0.0.1:8766` 是此前的本地调试台地址，不是本次发现的 Nano 服务地址。Nano 的 Web 控制服务端口尚未验证。
- IP、Mac 接口名称和串口路径是本次连接快照，重插或配置变化后应复查，不视为永久固定值。

## 2026-09-08 电机运行环境只读核查

用户明确授权使用 sudo 只读查看容器状态、挂载和校准文件。没有启动、停止或修改容器，没有执行任何电机脚本，也没有读取环境变量、密码文件或 API 密钥。以下是静态检查结果，不等同于硬件运行测试。

| 项目 | 已核实内容 |
| --- | --- |
| Nano 下载目录 | `/home/<nano-user>/Downloads`；其中有 `lelamp_runtime` 和 `lerobot` |
| 实际 LeLamp 程序 | `/home/<nano-user>/Downloads/lelamp_runtime` |
| 程序指定的 LeRobot 来源 | 该运行目录中的 `vendor/lerobot`，不是相邻的 `Downloads/lerobot` |
| 电机串口 | `/dev/ttyACM0`，权限为 root:dialout `0660`；`<nano-user>` 当前不在 dialout 组 |
| 稳定串口别名 | `/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B14033387-if00`，本次指向 `/dev/ttyACM0` |
| 原 LeLamp 容器 | `2082fbab718a`，名称 `condescending_gould`，镜像 `lelamp:V1.0` |
| 容器状态 | `exited`；最后退出码 255，原因未调查；核查结束时仍未启动 |
| 目录映射 | Nano `/home/<nano-user>/Downloads` → 容器 `/home`；Nano `/dev` → 容器 `/dev` |
| 容器默认命令 | Entrypoint 为 null，Cmd 为 `/bin/bash` |
| Python 环境线索 | `.venv/bin/python` 指向 `/usr/local/bin/python3.12`；未执行解释器或验证全部依赖 |
| 校准身份 | follower 类型 `lelamp_follower`，ID 为 `lelamp` |

### 校准文件保护

已从停止的容器中，以 `docker cp … -` 管道方式只读查看：

- Follower：`/root/.cache/huggingface/lerobot/calibration/robots/lelamp_follower/lelamp.json`
- Leader：`/root/.cache/huggingface/lerobot/calibration/teleoperators/lelamp_leader/lelamp.json`（只确认路径，未展开内容）

Follower 文件的关节与 ID 对应关系：`base_yaw=1`、`base_pitch=2`、`elbow_pitch=3`、`wrist_roll=4`、`wrist_pitch=5`。代码中的电机型号为 `sts3215`，这不是本次重新读取舵机型号寄存器的结果。

Follower 原文件字节的 SHA-256：

`718ab2c24c51a643f19fc3f4a0ab59de1c1d82b849ac7e258839e6aaf3a3590d`

`docker diff` 确认以上校准文件是该容器可写层中的新增内容，不在 Downloads 绑定挂载内。最初只读检查只进行了流式读取和哈希计算，未排查所有可能的其他备份位置。后续经用户明确授权，已完成以下持久备份。

### 已完成的双端校准备份

2026-09-08 20:19 验证：

- Nano 备份目录：`/home/<nano-user>/Downloads/LeLamp-calibration-backup-20260908-g0jqYZ`
- Mac 备份目录：`/Users/yugu/Documents/New project 4/AILamp/output/hardware_backups/LeLamp-calibration-backup-20260908-g0jqYZ`
- 保留完整 `calibration/robots/lelamp_follower/lelamp.json` 与 `calibration/teleoperators/lelamp_leader/lelamp.json` 目录结构和原始 JSON 字节。
- Follower：769 字节，SHA-256 为上文的 `718ab2c24c51a643f19fc3f4a0ab59de1c1d82b849ac7e258839e6aaf3a3590d`。
- Leader：768 字节，SHA-256 为 `7fa29fb124cd86a702a02a73a91f87fb160aee6b59380f0069235111c5bb863c`。
- 两个文件的原容器、Nano 备份、Mac 备份哈希逐一一致；本地 JSON 解析、五关节 ID 和范围次序检查均通过。
- 附带 `README.md` 恢复注意事项及 `SHA256SUMS` 校验清单；Follower 和 Leader 不可互换，身份仍为 `lelamp`。
- 只备份校准数据，没有备份整个程序、动作库、Python 环境或 Docker 镜像，没有执行恢复。
- 备份后原容器仍为 `exited`，最后启动时间保持 `2026-08-25T19:31:00.703157375Z`；没有连接电机、写入舵机参数或运行动作。

### 动作与接入限制

- 找到 13 个 CSV 动作：`nod`、`test01`、`excited`、`curious`、`happy_wiggle`、`idle`、`headshake`、`test02`、`shy`、`shock`、`sad`、`wake_up`、`scanning`。存在文件不代表当前机构姿态适合直接播放。
- CSV 使用五个 `<joint>.pos` 列，当前 follower 配置采用 `[-100, 100]` 归一化位置，不是角度；原回放程序按固定 FPS 播放，不采用 CSV 的时间戳调度。
- `connect(calibrate=False)` 仍会执行 `configure()`，写入舵机运行模式和控制参数。因此不能通过随便运行回放脚本来做无副作用检查。
- 当前 Mac AILamp 控制器的真实输出路径同时连接 LED 和电机；用户已明确 LED 不可用，需要先增加明确的仅电机模式，不能直接启用现有 `--with-outputs`。
- AILamp 当前 `config/hardware.toml` 的 `lamp_id="ailamp"` 与真实校准身份 `lelamp` 不同；接入时必须显式匹配真实身份，保留现有校准。
- 当前电机后端直接导入 `lelamp.follower`，没有现成的 SSH 电机转发层；不能把 Mac 上的 `/dev/ttyACM0` 配置当成 Nano 的远程串口。
- 软件停止不等于物理断电急停。第一次物理测试前需确认串口独占、机构周围无遮挡，以及人工断电条件。

建议下一阶段顺序：获得写入/启动授权 → 备份校准并核对哈希 → 复用原容器检查 Python 依赖（不连接舵机）→ 修改并离线测试仅电机接入与校准身份 → 获得现场确认后进行小幅运动测试 → 最后接入 AI 决策。

## 本次只读检查

```bash
ioreg -p IOUSB -l -w 0
ls /dev | rg '^(cu|tty)\.'
ifconfig en11
ipconfig getpacket en11
route -n get 192.168.55.1
ping -c 2 -W 1500 192.168.55.1
```

初次 TCP 22 探测只读取了 SSH 协议响应，该诊断连接已关闭。随后经用户授权，通过独立 SSH 会话完成登录；没有将密码写入远端 shell 命令。

19:52 登录后只读执行了 `whoami`、`hostname`、`uname -r`、板卡标识与 L4T 版本读取、`ip -4 -br addr` 和 `uptime`。当时网络输出仅包含 `l4tbr0: 192.168.55.1/24`、`docker0: 172.17.0.1/16 (DOWN)` 及回环接口。该历史输出不代表当前 Wi-Fi 状态，也不能据此断言 Docker 中没有容器。
