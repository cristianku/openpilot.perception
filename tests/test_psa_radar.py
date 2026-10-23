"""Tests of perception.radar.psa: python -m unittest discover -s tests (from the repo root)."""
import unittest

from perception.radar import psa

# Frames received from the radar of a Peugeot 3008 (bus 1), with a target at about 23 m.
REAL_2F6 = bytes.fromhex('01174c0050c04a00')
REAL_4F6 = bytes.fromhex('14174d7830')


def pack_2f6(target: int, distance_m: float, counter: int = 0) -> bytes:
    """0x2F6 built by hand from the DBC layout, with a valid checksum."""
    data = bytearray(8)
    raw = round(distance_m / 0.25)
    data[0] = target & 1
    data[1] = raw >> 2
    data[2] = (raw & 3) << 6
    data[6] = (counter & 0xF) << 4
    data[6] |= psa.checksum_2f6(bytes(data))
    return bytes(data)


class DecodeRealFrames(unittest.TestCase):
    def test_2f6(self):
        lead = psa.decode_2f6(1, REAL_2F6)
        self.assertTrue(lead.checksum_ok)
        self.assertTrue(lead.target)
        self.assertEqual(lead.distance_m, 23.25)
        self.assertAlmostEqual(lead.display_time_s, 2.0)
        self.assertEqual(lead.target_position_raw, 0)
        self.assertTrue(lead.valid)

    def test_4f6(self):
        lead = psa.decode_4f6(1, REAL_4F6)
        self.assertTrue(lead.target)
        self.assertEqual(lead.distance_m, 23.0)
        self.assertEqual(lead.relative_speed_mps, -1.04)
        self.assertEqual(lead.time_gap_s, 2.0)
        self.assertEqual(lead.sensor_state, psa.SENSOR_ACTIVE)
        self.assertTrue(lead.valid)

    def test_bad_checksum_is_not_valid(self):
        corrupted = bytearray(REAL_2F6)
        corrupted[1] ^= 0x01
        lead = psa.decode_2f6(1, bytes(corrupted))
        self.assertFalse(lead.checksum_ok)
        self.assertFalse(lead.valid)


class Sentinels(unittest.TestCase):
    def test_2f6_without_target(self):
        self.assertFalse(psa.decode_2f6(1, pack_2f6(0, 40.0)).valid)

    def test_2f6_no_target_distance(self):
        self.assertFalse(psa.decode_2f6(1, pack_2f6(1, 255.5)).valid)
        self.assertTrue(psa.decode_2f6(1, pack_2f6(1, 255.25)).valid)

    def test_4f6_values_written_without_target(self):
        # TIME_GAP 25.5 s, DISTANCE_GAP 254 m, RELATIVE_SPEED 93.8 m/s, TARGET_DETECTED 1
        raw_speed = round((93.8 + 70.0) / 0.02)
        data = bytes([255, 254, (psa.SENSOR_ACTIVE << 5) | (raw_speed >> 8), raw_speed & 0xFF, 0x10])
        lead = psa.decode_4f6(1, data)
        self.assertTrue(lead.target)
        self.assertFalse(lead.valid)

    def test_4f6_sensor_not_active(self):
        data = bytearray(REAL_4F6)
        data[2] = (data[2] & 0x1F) | (3 << 5)  # reduced visibility
        self.assertFalse(psa.decode_4f6(1, bytes(data)).valid)


class Sources(unittest.TestCase):
    def test_only_frames_from_the_bus_are_measurements(self):
        events = [
            (10, [(psa.LEAD_2F6, REAL_2F6, 1), (psa.LEAD_4F6, REAL_4F6, 1)]),
            (20, [(psa.LEAD_2F6, REAL_2F6, 129), (psa.LEAD_4F6, REAL_4F6, 129)]),  # sent by openpilot
            (30, [(psa.LEAD_2F6, REAL_2F6, 193)]),                                # blocked by the safety
            (40, [(psa.LEAD_2F6, REAL_2F6, 0), (psa.LEAD_2F6, REAL_2F6, 2)]),     # other buses
        ]
        log = psa.read_frames(events)
        self.assertEqual([x.t_ns for x in log.lead_2f6], [10])
        self.assertEqual([x.t_ns for x in log.lead_4f6], [10])
        self.assertEqual(log.injected_frames, 2)
        self.assertEqual(log.blocked_frames, 1)

    def test_artiv_programming_request(self):
        events = [
            (5, [(psa.ARTIV_DIAG_REQUEST, b'\x02\x3e\x00', 129)]),                  # TesterPresent
            (6, [(psa.ARTIV_DIAG_REQUEST, b'\x02\x10\x02', 129)]),
            (7, [(psa.ARTIV_DIAG_REQUEST, b'\x02\x10\x02\x00\x00\x00\x00\x00', 129)]),
            (8, [(psa.ARTIV_DIAG_REQUEST, b'\x02\x10\x02', 1)]),                    # not from this device
        ]
        self.assertEqual(psa.read_frames(events).artiv_requests_ns, [6, 7])

    def test_arrays(self):
        log = psa.read_frames([(10, [(psa.LEAD_2F6, REAL_2F6, 1)]), (20, [(psa.LEAD_2F6, pack_2f6(0, 0), 1)])])
        arrays = log.arrays()
        self.assertEqual(arrays['2f6_t_ns'].tolist(), [10, 20])
        self.assertEqual(arrays['2f6_valid'].tolist(), [True, False])
        self.assertEqual(len(arrays['4f6_t_ns']), 0)


class AgainstTheDbc(unittest.TestCase):
    """The hand-written bit layout must match opendbc's DBC, when opendbc is installed."""

    def setUp(self):
        try:
            from opendbc.can.packer import CANPacker
        except ImportError:
            self.skipTest('opendbc not installed')
        self.packer = CANPacker('psa_aee2010_r3')

    def test_2f6(self):
        _, data, _ = self.packer.make_can_msg('HS2_DYN_MDD_ETAT_2F6', 1, {
            'TARGET_DETECTED': 1, 'INTER_VEHICLE_DISTANCE': 123.75, 'DISPLAY_INTERVEHICLE_TIME': 1.7,
            'TARGET_POSITION': 5})
        lead = psa.decode_2f6(1, data)
        self.assertTrue(lead.target)
        self.assertEqual(lead.distance_m, 123.75)
        self.assertAlmostEqual(lead.display_time_s, 1.7)
        self.assertEqual(lead.target_position_raw, 5)

    def test_4f6(self):
        _, data, _ = self.packer.make_can_msg('HS2_DAT_ARTIV_V2_4F6', 1, {
            'TIME_GAP': 3.3, 'DISTANCE_GAP': 87, 'RELATIVE_SPEED': -12.34, 'ARTIV_SENSOR_STATE': 3,
            'TARGET_DETECTED': 1, 'TRAFFIC_DIRECTION': 2})
        lead = psa.decode_4f6(1, data)
        self.assertEqual(lead.time_gap_s, 3.3)
        self.assertEqual(lead.distance_m, 87.0)
        self.assertEqual(lead.relative_speed_mps, -12.34)
        self.assertEqual(lead.sensor_state, 3)
        self.assertTrue(lead.target)
        self.assertEqual(lead.traffic_direction, 2)


if __name__ == '__main__':
    unittest.main()
