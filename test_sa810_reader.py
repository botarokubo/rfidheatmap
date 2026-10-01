import unittest

from sa810_reader import Sa810Client, checksum, inventory_packet, set_encryption_packet


class FakeHidDevice:
    def __init__(self) -> None:
        self.reports: list[bytes] = []

    def write(self, report: bytes) -> int:
        self.reports.append(bytes(report))
        return len(report)


class EncryptionPacketTests(unittest.TestCase):
    def assert_valid_packet(self, packet: bytes) -> None:
        self.assertEqual(checksum(packet[:-1]), packet[-1])
        self.assertEqual(packet[0], 0x7C)
        self.assertEqual(packet[3:6], bytes((0x84, 0x31, 0x03)))

    def test_none_mode(self) -> None:
        packet = set_encryption_packet("None")
        self.assert_valid_packet(packet)
        self.assertEqual(packet[6:9], bytes((0x00, 0x00, 0x00)))

    def test_pairing_mode_uses_one_password_byte(self) -> None:
        packet = set_encryption_packet("Pairing", "165")
        self.assert_valid_packet(packet)
        self.assertEqual(packet[6:9], bytes((0x01, 0xA5, 0x00)))

    def test_crc_mode_uses_two_password_bytes(self) -> None:
        packet = set_encryption_packet("CRC", "4847")
        self.assert_valid_packet(packet)
        self.assertEqual(packet[6:9], bytes((0x02, 0x12, 0xEF)))

    def test_invalid_passwords_are_rejected(self) -> None:
        for mode, password in (("Pairing", "256"), ("Pairing", "GG"),
                               ("CRC", "65536"), ("CRC", "12EF")):
            with self.subTest(mode=mode, password=password):
                with self.assertRaises(ValueError):
                    set_encryption_packet(mode, password)


class UsbTransportTests(unittest.TestCase):
    def test_usb_command_is_wrapped_in_a_64_byte_hid_report(self) -> None:
        client = Sa810Client()
        device = FakeHidDevice()
        client._transport = "usb"
        client._hid_device = device
        packet = inventory_packet()

        client._send(packet)

        self.assertEqual(len(device.reports), 1)
        self.assertEqual(len(device.reports[0]), 65)
        self.assertEqual(device.reports[0][0], 0x00)
        self.assertEqual(device.reports[0][1:1 + len(packet)], packet)
        self.assertEqual(
            device.reports[0][1 + len(packet):],
            bytes(Sa810Client.USB_REPORT_SIZE - len(packet)),
        )


if __name__ == "__main__":
    unittest.main()
