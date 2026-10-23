"""perception.radar.oem_lead on synthetic carState messages."""
import math
import unittest

from fakes import MS, schemas
from perception.radar import oem_lead

S = 1_000_000_000


def states(struct, rows):
    """(logMonoTime, carState builder) from (log_ns, fields) rows."""
    return [(log_ns, struct.new_message(vEgo=10.0, **fields)) for log_ns, fields in rows]


def measured(t_ns, distance=30.0, speed=-1.5, speed_valid=True):
    return {'stockRadarLeadValid': True, 'stockRadarLeadDistance': distance,
            'stockRadarLeadRelativeSpeed': speed, 'stockRadarMeasurementTimeNanos': t_ns,
            'stockRadarRelativeSpeedValid': speed_valid}


class Schema(unittest.TestCase):
    def setUp(self):
        self.s = schemas()

    def test_schema_without_the_fields_is_vision_only(self):
        track = oem_lead.read_oem_leads(states(self.s.CarStateOld, [(S, {}), (S + 10 * MS, {})]))
        self.assertFalse(track.available)
        self.assertEqual(track.samples, [])
        self.assertIn('schema without the stockRadar fields', track.reason)
        self.assertIn('vision-only', track.reason)

    def test_old_rlog_read_with_the_new_schema(self):
        old = [self.s.CarStateOld.new_message(vEgo=v).to_bytes() for v in (1.0, 2.0, 3.0)]
        pairs = []
        for i, data in enumerate(old):
            with self.s.CarStateNew.from_bytes(data) as reader:
                self.assertFalse(reader.stockRadarLeadValid)
                pairs.append((S + i * 10 * MS, reader.as_builder()))
        track = oem_lead.read_oem_leads(pairs)
        self.assertTrue(track.available)
        self.assertEqual(track.samples, [])
        self.assertEqual(track.rejected['no valid target'], 3)
        self.assertIn('no valid radar measurement', track.reason)

    def test_contract_without_speed_validity_does_not_trust_the_speed(self):
        fields = measured(S)
        del fields['stockRadarRelativeSpeedValid']
        track = oem_lead.read_oem_leads(states(self.s.CarStateContract, [(S + 5 * MS, fields)]))
        self.assertEqual(len(track.samples), 1)
        self.assertIsNone(track.samples[0].relative_speed_mps)
        self.assertEqual(track.samples[0].distance_m, 30.0)

    def test_no_car_state(self):
        track = oem_lead.read_oem_leads([])
        self.assertFalse(track.available)
        self.assertIn('no carState', track.reason)


class Samples(unittest.TestCase):
    def setUp(self):
        self.new = schemas().CarStateNew

    def read(self, rows):
        return oem_lead.read_oem_leads(states(self.new, rows))

    def test_carstate_at_100hz_repeats_a_50hz_measurement(self):
        rows = [(S + i * 10 * MS, measured(S + (i // 2) * 20 * MS)) for i in range(10)]
        track = self.read(rows)
        self.assertEqual([s.t_ns for s in track.samples], [S + k * 20 * MS for k in range(5)])
        self.assertEqual(track.repeated, 5)
        self.assertEqual(track.samples[0].relative_speed_mps, -1.5)
        self.assertEqual(str(track.samples[0].provenance), 'oem_radar')

    def test_no_target(self):
        track = self.read([(S, {'stockRadarLeadValid': False, 'stockRadarLeadDistance': 255.5,
                                'stockRadarMeasurementTimeNanos': S})])
        self.assertEqual(track.samples, [])
        self.assertEqual(track.rejected['no valid target'], 1)

    def test_stale_future_and_missing_times(self):
        track = self.read([
            (S + 400 * MS, measured(S)),            # 400 ms old
            (S, measured(S + 1 * MS)),              # measured after its own message
            (S + 10 * MS, measured(0)),             # no time
        ])
        self.assertEqual(track.samples, [])
        self.assertEqual(track.rejected, {'stale': 1, 'measurement after its message': 1, 'no measurement time': 1})

    def test_out_of_range_and_not_finite(self):
        track = self.read([(S + i * MS, measured(S + i * MS - 1, distance=d))
                           for i, d in enumerate((0.2, 300.0, math.nan, math.inf, -5.0))])
        self.assertEqual(track.samples, [])
        self.assertEqual(track.rejected['distance out of range'], 5)

    def test_time_going_back(self):
        track = self.read([(S + 50 * MS, measured(S + 40 * MS)), (S + 60 * MS, measured(S + 20 * MS))])
        self.assertEqual(len(track.samples), 1)
        self.assertEqual(track.rejected['measurement time going back'], 1)

    def test_invalid_or_impossible_relative_speed(self):
        track = self.read([(S + 5 * MS, measured(S, speed_valid=False)),
                           (S + 25 * MS, measured(S + 20 * MS, speed=95.0)),
                           (S + 45 * MS, measured(S + 40 * MS, speed=math.nan))])
        self.assertEqual([s.relative_speed_mps for s in track.samples], [None, None, None])
        self.assertEqual([s.distance_m for s in track.samples], [30.0, 30.0, 30.0])


class Nearest(unittest.TestCase):
    def setUp(self):
        rows = [(S + k * 20 * MS + 3 * MS, measured(S + k * 20 * MS)) for k in range(3)]  # 0, 20, 40 ms
        self.track = oem_lead.read_oem_leads(states(schemas().CarStateNew, rows))

    def test_nearest_within_the_skew(self):
        sample, skew = self.track.nearest(S + 27 * MS)
        self.assertEqual(sample.t_ns, S + 20 * MS)
        self.assertEqual(skew, 7 * MS)
        sample, skew = self.track.nearest(S + 33 * MS)
        self.assertEqual(sample.t_ns, S + 40 * MS)
        self.assertEqual(skew, -7 * MS)

    def test_frames_too_far_from_any_measurement(self):
        self.assertEqual(self.track.nearest(S + 66 * MS), (None, None))   # 26 ms after the last one
        self.assertEqual(self.track.nearest(S - 30 * MS), (None, None))
        self.assertIsNotNone(self.track.nearest(S + 66 * MS, max_skew_ns=30 * MS)[0])


if __name__ == '__main__':
    unittest.main()
