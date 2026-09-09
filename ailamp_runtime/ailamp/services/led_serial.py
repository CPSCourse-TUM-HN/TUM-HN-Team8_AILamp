from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


RGB = tuple[int, int, int]


def _validate_channel(value: int) -> int:
    if not isinstance(value, int) or value < 0 or value > 255:
        raise ValueError(f"RGB channels must be integers between 0 and 255, got {value!r}")
    return value


@dataclass(frozen=True)
class LEDSerialProtocol:
    led_count: int

    def ping(self) -> bytes:
        return b"PING\n"

    def clear(self) -> bytes:
        return b"CLEAR\n"

    def solid(self, red: int, green: int, blue: int) -> bytes:
        red, green, blue = (_validate_channel(red), _validate_channel(green), _validate_channel(blue))
        return f"SOLID {red} {green} {blue}\n".encode()

    def brightness(self, value: int) -> bytes:
        _validate_channel(value)
        return f"BRIGHTNESS {value}\n".encode()

    def pixels(self, colors: Iterable[RGB]) -> bytes:
        color_list = list(colors)
        if len(color_list) > self.led_count:
            raise ValueError(f"Expected at most {self.led_count} pixels, got {len(color_list)}")
        encoded = []
        for red, green, blue in color_list:
            encoded.append(f"{_validate_channel(red)},{_validate_channel(green)},{_validate_channel(blue)}")
        return f"PIXELS {';'.join(encoded)}\n".encode()


class LEDSerialService:
    def __init__(self, port: str, led_count: int, baudrate: int = 115200, timeout: float = 1.0):
        self.port = port
        self.protocol = LEDSerialProtocol(led_count=led_count)
        self.baudrate = baudrate
        self.timeout = timeout
        self._serial: Optional[object] = None

    def connect(self) -> None:
        import serial  # type: ignore

        self._serial = serial.Serial(self.port, self.baudrate, timeout=self.timeout)

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def send(self, command: bytes) -> str:
        if self._serial is None:
            raise RuntimeError("LEDSerialService is not connected")
        self._serial.write(command)
        response = self._serial.readline().decode(errors="ignore").strip()
        if response.startswith("ERR") or response == "":
            raise RuntimeError(f"LED controller rejected command: {response or '<empty response>'}")
        return response

    def ping(self) -> str:
        return self.send(self.protocol.ping())

    def clear(self) -> str:
        return self.send(self.protocol.clear())

    def solid(self, red: int, green: int, blue: int) -> str:
        return self.send(self.protocol.solid(red, green, blue))

    def brightness(self, value: int) -> str:
        return self.send(self.protocol.brightness(value))
