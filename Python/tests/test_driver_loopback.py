"""Drives real drivers against software endpoints: sockets, a pseudo-terminal,
a virtual CAN bus, a Modbus server and an OPC UA server. Passing here is what
loopback_verified means in the compatibility matrix. It proves nothing about a
physical instrument."""
import os
import socket
import struct
import sys
import threading
import time

import pytest

from edaq import codefile
from edaq.hw import discovery
from edaq.hw.matrix import CompatibilityMatrix
from edaq.hw.registry import DriverRegistry

REGISTRY = DriverRegistry()


def make(driver_id, settings):
    driver = REGISTRY.create(driver_id)
    driver.configure(settings)
    return driver


def stream_settings(transport, port):
    return {"transport": transport, "host": "127.0.0.1", "tcpPort": port, "format": "delimited", "delimiter": ",",
            "channels": [{"hw": "a", "field": "1"}, {"hw": "b", "field": "2"}]}


def read_until_data(driver):
    t = 1000.0
    deadline = time.time() + 5
    while time.time() < deadline:
        time.sleep(0.05)
        t += 0.1
        block = driver.read(t)
        if any(b.t.size for b in block):
            return block
    pytest.fail("No data arrived from the endpoint.")


def test_tcp_stream_delivers_samples():
    server = socket.create_server(("127.0.0.1", 0))
    driver = make("stream", stream_settings("tcp", server.getsockname()[1]))
    driver.connect()
    peer, _ = server.accept()
    peer.sendall(b"1.5,2.5\n3.5,4.5\nbad,frame\n")
    block = read_until_data(driver)
    assert list(block[0].raw) == [1.5, 3.5] and list(block[1].raw) == [2.5, 4.5]
    status = driver.status()
    assert status["packetsOk"] == 2 and status["packetErrors"] == 1 and status["instrument"] == "not_assessed"
    driver.disconnect()
    peer.close()
    server.close()


def test_tcp_frame_split_across_reads_is_reassembled():
    server = socket.create_server(("127.0.0.1", 0))
    driver = make("stream", stream_settings("tcp", server.getsockname()[1]))
    driver.connect()
    peer, _ = server.accept()
    peer.sendall(b"7.0,8")
    time.sleep(0.1)
    assert not any(b.t.size for b in driver.read(1000.0))
    peer.sendall(b".0\n")
    block = read_until_data(driver)
    assert (block[0].raw[0], block[1].raw[0]) == (7, 8)
    driver.disconnect()
    peer.close()
    server.close()


def test_tcp_peer_closing_is_reported_as_link_loss():
    server = socket.create_server(("127.0.0.1", 0))
    driver = make("stream", stream_settings("tcp", server.getsockname()[1]))
    driver.connect()
    peer, _ = server.accept()
    peer.close()
    time.sleep(0.1)
    with pytest.raises(ConnectionError):
        driver.read(1000.0)
    driver.disconnect()
    server.close()


def test_udp_stream_delivers_json_lines():
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("", 0))
    port = probe.getsockname()[1]
    probe.close()
    settings = {**stream_settings("udp", port), "format": "json", "channels": [{"hw": "a", "field": "a"}, {"hw": "b", "field": "b"}]}
    driver = make("stream", settings)
    driver.connect()
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender.sendto(b'{"a":10,"b":20}\n', ("127.0.0.1", port))
    block = read_until_data(driver)
    assert (block[0].raw[0], block[1].raw[0]) == (10, 20)
    driver.disconnect()
    sender.close()


@pytest.mark.skipif(sys.platform == "win32", reason="Pseudo-terminals need a Unix system.")
def test_serial_stream_reads_a_real_port_device():
    master, slave = os.openpty()
    settings = {**stream_settings("serial", 0), "port": os.ttyname(slave), "baudRate": 9600}
    driver = make("stream", settings)
    driver.connect()
    os.write(master, b"1.25,2.75\n")
    block = read_until_data(driver)
    assert (block[0].raw[0], block[1].raw[0]) == (1.25, 2.75)
    driver.disconnect()
    os.close(master)
    os.close(slave)


@pytest.mark.parametrize("fd", [False, True])
def test_can_decodes_from_virtual_bus(fd):
    import can
    channel = f"edaq_test_{fd}"
    signal = {"hw": "speed", "messageId": 256, "extended": False, "startBit": 0, "length": 16, "byteOrder": "intel", "signed": False, "scale": 0.5, "offset": 0}
    driver = make("can", {"interface": "virtual", "channel": channel, "fd": fd, "listenOnly": True, "channels": [signal]})
    driver.connect()
    with can.Bus(interface="virtual", channel=channel) as sender:
        sender.send(can.Message(arbitration_id=256, is_extended_id=False, data=[100, 0, 0, 0, 0, 0, 0, 0], is_fd=fd))
        block = read_until_data(driver)
    assert block[0].raw[0] == 50 and driver.status()["connected"]
    driver.disconnect()


class ModbusFixture:
    """Minimal Modbus TCP server: registers from 0 hold 1234, then float 12.5."""

    def __init__(self):
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        high, low = struct.unpack(">2H", struct.pack(">f", 12.5))
        words = [1234, high, low, 0, 0, 0]
        try:
            peer, _ = self.server.accept()
            while True:
                query = peer.recv(12)
                if len(query) < 12:
                    return
                address, count = struct.unpack(">2H", query[8:12])
                body = struct.pack(f">{count}H", *words[address:address + count])
                peer.sendall(query[:4] + struct.pack(">H", 3 + len(body)) + query[6:8] + bytes([len(body)]) + body)
        except OSError:
            return


def test_modbus_tcp_reads_from_software_server():
    fixture = ModbusFixture()
    driver = make("modbus", {"transport": "tcp", "host": "127.0.0.1", "tcpPort": fixture.port, "timeoutS": 2, "pollHz": 100, "wordOrder": "big",
                             "channels": [{"hw": "u16", "table": "holdingregs", "address": 1, "type": "uint16", "serverId": 1}, {"hw": "f32", "table": "holdingregs", "address": 2, "type": "single", "serverId": 1}]})
    driver.connect()
    block = driver.read(1000.0)
    assert block[0].raw[0] == 1234 and block[1].raw[0] == 12.5
    driver.disconnect()
    fixture.server.close()


def test_opcua_reads_from_software_server():
    from asyncua import ua
    from asyncua.sync import Server
    probe = socket.create_server(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    server = Server()
    server.set_endpoint(f"opc.tcp://127.0.0.1:{port}")
    namespace = server.register_namespace("edaq")
    node = server.nodes.objects.add_variable(ua.NodeId("temp", namespace), "temp", 42.5)
    node.set_writable()
    server.start()
    try:
        driver = make("opcua", {"host": "127.0.0.1", "tcpPort": port, "pollHz": 100, "channels": [{"hw": "temp", "namespace": namespace, "identifier": "temp"}]})
        driver.connect()
        block = driver.read(1000.0)
        assert block[0].raw[0] == 42.5 and driver.status()["packetsOk"] == 1
        driver.disconnect()
    finally:
        server.stop()


def test_connection_test_reports_failure_step():
    report = discovery.connection_test(REGISTRY, {"driver": "stream", "settings": stream_settings("tcp", 1)}, 0.5)
    assert not report["protocolVerified"] and not report["steps"][-1]["ok"] and report["steps"][-1]["step"] == "Connect"


def test_discovery_runs_on_this_host():
    found, skipped = discovery.scan(REGISTRY, CompatibilityMatrix())
    assert any(c["driver"] == "simulator" for c in found) and all(c["verdict"]["message"] for c in found) and isinstance(skipped, list)
