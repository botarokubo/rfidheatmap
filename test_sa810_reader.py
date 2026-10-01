import unittest

from sa810_reader import checksum, set_encryption_packet


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


if __name__ == "__main__":
    unittest.main()
