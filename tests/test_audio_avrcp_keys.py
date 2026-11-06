"""Which AVRCP pass-through key lands on which bit of the audio media-key report.

The bits follow the Consumer Control descriptor host.py injects for an audio
device: bit 0 Scan Next Track, bit 1 Scan Previous Track, bit 2 Play/Pause,
bit 3 Volume Increment, bit 4 Volume Decrement. hid-input turns those into
KEY_NEXTSONG (163), KEY_PREVIOUSSONG (165), KEY_PLAYPAUSE (164), KEY_VOLUMEUP
(115) and KEY_VOLUMEDOWN (114).
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip('bumble')
sys.path.insert(0, str(Path(__file__).parent.parent / 'kindle_hid_passthrough'))
# Imports the real bumble, so it has to be collected before
# test_classic_reconnect.py, which swaps bumble modules for stubs for the rest
# of a pytest session. The file name is what keeps that order.

from bumble import avc
from config import Protocol
from host import KindleAvrcpDelegate

Op = avc.PassThroughFrame.OperationId

NEXT, PREV, PLAY_PAUSE, VOL_UP, VOL_DOWN = 0x01, 0x02, 0x04, 0x08, 0x10


class FakeNode:
    def __init__(self):
        self.reports = []

    def send_input(self, data):
        self.reports.append(bytes(data))


def press_and_release(operation_id, protocol=Protocol.CLASSIC_AUDIO):
    node = FakeNode()
    host = SimpleNamespace(sessions={
        'AA:BB:CC:DD:EE:FF': SimpleNamespace(protocol=protocol, uhid_device=node)})
    delegate = KindleAvrcpDelegate(host)

    async def run():
        await delegate.on_key_event(operation_id, True, b'')
        await delegate.on_key_event(operation_id, False, b'')

    asyncio.run(run())
    return node.reports


def keyed(bit):
    """Report ID 1 with the bit pressed, then every key released."""
    return [bytes([0x01, bit]), bytes([0x01, 0x00])]


@pytest.mark.parametrize('operation_id, bit', [
    (Op.FORWARD, NEXT),
    (Op.BACKWARD, PREV),
    (Op.PLAY, PLAY_PAUSE),
    (Op.PAUSE, PLAY_PAUSE),
    (Op.VOLUME_UP, VOL_UP),
    (Op.VOLUME_DOWN, VOL_DOWN),
    (Op.UP, NEXT),
    (Op.RIGHT, NEXT),
    (Op.LEFT, PREV),
])
def test_key_reaches_its_bit(operation_id, bit):
    assert press_and_release(operation_id) == keyed(bit)


def test_down_is_previous_track():
    # Op(0x02) rather than Op.DOWN: bumble 0.0.226 gives DOWN the value of UP.
    assert press_and_release(Op(0x02)) == keyed(PREV)


def test_key_the_descriptor_does_not_declare_sends_nothing():
    assert press_and_release(Op.STOP) == []


def test_only_an_audio_session_receives_keys():
    assert press_and_release(Op.FORWARD, protocol=Protocol.CLASSIC) == []
