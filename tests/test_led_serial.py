import pytest

from ailamp.services.led_serial import LEDSerialProtocol
from ailamp.services.led_serial import LEDSerialService


def test_led_protocol_encodes_required_commands():
    protocol = LEDSerialProtocol(led_count=64)

    assert protocol.ping() == b"PING\n"
    assert protocol.clear() == b"CLEAR\n"
    assert protocol.solid(255, 180, 80) == b"SOLID 255 180 80\n"
    assert protocol.brightness(128) == b"BRIGHTNESS 128\n"
    assert protocol.pixels([(255, 0, 0), (0, 255, 0)]) == b"PIXELS 255,0,0;0,255,0\n"


def test_led_protocol_rejects_invalid_values():
    protocol = LEDSerialProtocol(led_count=64)

    with pytest.raises(ValueError):
        protocol.solid(256, 0, 0)
    with pytest.raises(ValueError):
        protocol.brightness(-1)
    with pytest.raises(ValueError):
        protocol.pixels([(0, 0, 0)] * 65)


def test_led_service_rejects_error_ack():
    class FakeSerial:
        def __init__(self):
            self.commands = []

        def write(self, command):
            self.commands.append(command)

        def readline(self):
            return b"ERR bad command\n"

    service = LEDSerialService("/dev/null", led_count=64)
    service._serial = FakeSerial()

    with pytest.raises(RuntimeError, match="LED controller rejected command"):
        service.solid(1, 2, 3)
