# NOTICE

AILamp is an integration project derived from the open source LeLamp project by Human Computer Lab.

Copied source assets:

- 3D print files from `https://github.com/humancomputerlab/LeLamp`, local source commit `9650849`.
- MuJoCo / URDF simulation files from `https://github.com/humancomputerlab/LeLamp`, local source commit `9650849`.
- Motion recording CSV files from `https://github.com/humancomputerlab/lelamp_runtime`, local source commit `ee23699`.

The copied upstream project is licensed under GNU GPL v3. See `LICENSE`.

AILamp modifies the upstream simulation by adding `simulation/ailamp_robot.xml` and `simulation/ailamp_scene.xml`. The derived robot MJCF removes the upstream `lamp_base` and `lamp_base_cover` visuals so the scene can show AILamp replacement-base STL mesh visuals for the selected Jetson electronics layout while retaining the original LeLamp MJCF assets for reference. The AILamp replacement shell preserves the original LampBase motor chamber and arm-root relationship, then expands the base for the selected electronics envelope, airflow, service ports, and cable routing rather than stacking a second base below the original.

AILamp-specific additions include:

- Jetson Nano 4GB API-hybrid hardware, software, and OS configuration
- Jetson Orin Nano Super reference hardware configuration
- Generated AILamp adapter kit in `3D/AILamp_Adapters/`
- Raspberry Pi Pico WH LED serial firmware
- Arducam UB0234 camera integration
- Seeed ReSpeaker XVF3800 audio integration
- Virtual vision simulation events
- AILamp runtime services and CLI
- Bilingual build documentation
