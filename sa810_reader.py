"""Minimal TCP client for the Yanzeo SA810 IR reader-control protocol."""

from __future__ import annotations

import queue
import socket
import threading
import time
from dataclasses import dataclass

SOI_COMMAND = 0x7C
SOI_RESPONSE = 0xCC
PUBLIC_ADDRESS = 0xFFFF
CID_READ_C_UII = 0x20
CID_TX_POWER_SET = 0x51
RTN_OK = 0x00
RTN_FAIL = 0x01
RTN_MESSAGE = 0x02
RTN_AUTO = 0x05


def checksum(data: bytes) -> int:
    return ((~sum(data)) + 1) & 0xFF


def build_packet(cid1: int, info: bytes = b"") -> bytes:
    body = bytes((SOI_COMMAND, 0xFF, 0xFF, cid1, 0x00, len(info))) + info
    return body + bytes((checksum(body),))


def inventory_packet(antenna: int | None = None) -> bytes:
    info = b"" if antenna is None else bytes((0x01, antenna & 0xFF))
    return build_packet(CID_READ_C_UII, info)


def set_power_packet(dbm: int) -> bytes:
    return build_packet(CID_TX_POWER_SET, bytes((max(0, min(33, dbm)),)))


def rssi_to_dbm(raw: int) -> float:
    """Convert this SA810's magnitude byte to conventional negative dBm."""
    return float(129 - (raw & 0xFF))


@dataclass(frozen=True)
class ReaderTag:
    epc: str
    pc: str
    antenna: int
    rssi: float
    received_at: float


class PacketFramer:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def feed(self, data: bytes) -> list[tuple[int, int, bytes]]:
        self.buffer.extend(data)
        frames: list[tuple[int, int, bytes]] = []
        while True:
            while self.buffer and self.buffer[0] not in (SOI_RESPONSE, SOI_COMMAND):
                del self.buffer[0]
            if len(self.buffer) < 7:
                break
            length = self.buffer[5]
            total = 7 + length
            if len(self.buffer) < total:
                break
            raw = bytes(self.buffer[:total])
            del self.buffer[:total]
            if checksum(raw[:-1]) != raw[-1]:
                continue
            frames.append((raw[3], raw[4], raw[6:-1]))
        return frames


def parse_tag(info: bytes) -> ReaderTag | None:
    # Inventory INFO is ANT | PC+EPC | RSSI.
    if len(info) < 5:
        return None
    antenna = info[0]
    body = info[1:-1]
    if len(body) < 2:
        return None
    pc_epc_length = (((body[0] >> 3) + 1) * 2)
    pc_epc = body[:pc_epc_length] if len(body) >= pc_epc_length else body
    if len(pc_epc) <= 2:
        return None
    return ReaderTag(
        epc=pc_epc[2:].hex().upper(),
        pc=pc_epc[:2].hex().upper(),
        antenna=antenna,
        rssi=rssi_to_dbm(info[-1]),
        received_at=time.time(),
    )


class Sa810TcpClient:
    """Background TCP connection with a thread-safe event queue."""

    def __init__(self) -> None:
        self.tags: queue.Queue[ReaderTag] = queue.Queue()
        self.messages: queue.Queue[str] = queue.Queue()
        self.connected = False
        self.host = ""
        self.port = 0
        self._socket: socket.socket | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._inventory = threading.Event()
        self._cycle_done = threading.Event()
        self._receive_thread: threading.Thread | None = None
        self._send_thread: threading.Thread | None = None
        self._framer = PacketFramer()
        # The IR v1.1 protocol defines Read Type C UII with no INFO payload.
        # Some demo variants accept an optional antenna payload, but SA810's
        # integrated antenna uses the protocol default (antenna 0).
        self.antenna: int | None = None

    def connect(self, host: str, port: int, timeout: float = 3.0) -> None:
        self.disconnect()
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.settimeout(0.2)
        with self._lock:
            self._socket = sock
        self.host, self.port = host, port
        self.connected = True
        self._stop.clear()
        self._inventory.clear()
        self._cycle_done.set()
        self._framer = PacketFramer()
        self._receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
        self._send_thread = threading.Thread(target=self._send_loop, daemon=True)
        self._receive_thread.start()
        self._send_thread.start()
        self.messages.put(f"Connected to {host}:{port}")

    def disconnect(self) -> None:
        self._inventory.clear()
        self._stop.set()
        with self._lock:
            sock, self._socket = self._socket, None
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        self.connected = False

    def set_power(self, dbm: int) -> None:
        self._send(set_power_packet(dbm))

    def start_inventory(self) -> None:
        if not self.connected:
            raise ConnectionError("Reader is not connected")
        while not self.tags.empty():
            try:
                self.tags.get_nowait()
            except queue.Empty:
                break
        self._inventory.set()

    def stop_inventory(self) -> None:
        self._inventory.clear()

    def _send(self, packet: bytes) -> None:
        with self._lock:
            sock = self._socket
        if not sock:
            raise ConnectionError("Reader is not connected")
        sock.sendall(packet)

    def _send_loop(self) -> None:
        while not self._stop.is_set():
            if self._inventory.is_set() and self.connected:
                try:
                    self._cycle_done.clear()
                    self._send(inventory_packet(self.antenna))
                except OSError as error:
                    self.messages.put(f"Send error: {error}")
                    self.connected = False
                    return
                # SDK state machine waits for RTN OK/ERR before issuing the
                # next inventory. The timeout also recovers from a lost frame.
                self._cycle_done.wait(timeout=0.5)
                time.sleep(0.01)
            else:
                time.sleep(0.05)

    def _receive_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                sock = self._socket
            if not sock:
                return
            try:
                data = sock.recv(4096)
                if not data:
                    self.messages.put("Reader closed the TCP connection")
                    self.connected = False
                    return
                for cid, rtn, info in self._framer.feed(data):
                    if cid == CID_READ_C_UII and rtn in (RTN_MESSAGE, RTN_AUTO):
                        tag = parse_tag(info)
                        if tag:
                            self.tags.put(tag)
                    elif cid == CID_READ_C_UII and rtn in (RTN_OK, RTN_FAIL):
                        # RTN_FAIL commonly means an inventory cycle in which
                        # no tag answered; both statuses complete the cycle.
                        self._cycle_done.set()
            except socket.timeout:
                continue
            except OSError as error:
                if not self._stop.is_set():
                    self.messages.put(f"Receive error: {error}")
                self.connected = False
                return
