#!/usr/bin/env bash
set -euo pipefail

echo "AILamp Jetson Nano environment diagnostic"
echo "This script is read-only: it does not create venvs, install packages, or edit calibration."
echo

if [ -f /etc/nv_tegra_release ]; then
  echo "Jetson BSP:"
  cat /etc/nv_tegra_release
else
  echo "WARN: /etc/nv_tegra_release not found. Run this on the Jetson Nano for a real BSP check."
fi

echo
echo "Ubuntu release:"
if command -v lsb_release >/dev/null 2>&1; then
  lsb_release -a || true
else
  cat /etc/os-release || true
fi

echo
echo "Machine architecture:"
uname -m

echo
echo "Active Python:"
python3 - <<'PY'
import importlib.util
import os
import sys

print(sys.executable)
print(sys.version.split()[0])
print(f"VIRTUAL_ENV={os.environ.get('VIRTUAL_ENV', '<unset>')}")
def has_module(name):
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False

for module in ("ailamp.cli", "openai", "serial"):
    print(f"{module}: {'found' if has_module(module) else 'missing'}")

lelamp_modules = (
    "lelamp.robots.lelamp_follower",
    "lelamp.robot.lelamp_follower",
    "lelamp.service.motors.lelamp_follower",
)
for module in lelamp_modules:
    if has_module(module):
        print(f"LeLamp follower: found {module}")
        break
else:
    print("LeLamp follower: missing")
PY

echo
echo "Expected current route:"
echo "  1. Activate the already working AILamp/LeLamp Python environment."
echo "  2. Confirm the calibrated lamp_id in config/hardware.toml matches the existing LeLamp calibration."
echo "  3. Install only missing project extras in that environment, for example: pip install -e '.[nano]'"
echo "  4. export OPENAI_API_KEY=... only when using ailamp web-control --brain"
echo "  5. ailamp runtime-check --offline"
echo "  6. ailamp runtime-check --include-motor-runtime"
echo "  7. ailamp hardware-check --include-devices"
echo "  8. ailamp web-control --brain"
echo
echo "Do not blindly rebuild Python or overwrite servo calibration. The AILamp package supports Python >=3.11; the upstream LeLamp follower may require Python >=3.12, so reuse the environment where motion playback already works."
