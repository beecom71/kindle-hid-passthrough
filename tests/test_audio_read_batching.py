"""When HIDHost batches HCI reads, and that it never does while audio is up."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip('bumble')
sys.path.insert(0, str(Path(__file__).parent.parent / 'kindle_hid_passthrough'))
# Imports the real bumble, so it has to be collected before
# test_classic_reconnect.py, which swaps bumble modules for stubs for the rest
# of a pytest session. The file name is what keeps that order.

from config import Protocol
from host import HIDHost


class FakeReads:
    def __init__(self):
        self.batching = False

    def start(self):
        self.batching = True

    def stop(self):
        self.batching = False


def batching_with(*sessions):
    host = HIDHost.__new__(HIDHost)
    host.transport = SimpleNamespace(reads=FakeReads())
    host.sessions = {f"s{i}": s for i, s in enumerate(sessions)}
    host._update_read_batching()
    return host.transport.reads.batching


def session(protocol, node):
    return SimpleNamespace(protocol=protocol, uhid_device=object() if node else None)


def test_no_session_reads_per_packet():
    assert not batching_with()


def test_hid_node_batches():
    assert batching_with(session(Protocol.CLASSIC, node=True))


def test_hid_without_node_reads_per_packet():
    assert not batching_with(session(Protocol.BLE, node=False))


def test_audio_with_media_key_node_reads_per_packet():
    assert not batching_with(session(Protocol.CLASSIC_AUDIO, node=True))


def test_audio_still_connecting_reads_per_packet():
    assert not batching_with(session(Protocol.CLASSIC_AUDIO, node=False))


def test_audio_wins_over_a_streaming_gamepad():
    assert not batching_with(session(Protocol.CLASSIC, node=True),
                             session(Protocol.CLASSIC_AUDIO, node=True))


def test_transport_without_reads_is_left_alone():
    host = HIDHost.__new__(HIDHost)
    host.transport = SimpleNamespace()
    host.sessions = {"s": session(Protocol.CLASSIC, node=True)}
    host._update_read_batching()
