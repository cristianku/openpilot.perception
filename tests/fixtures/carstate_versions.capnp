@0xc4b2f1e9a7d35e81;

# Three versions of a carState, for the tests: before the radar fields, with the four fields of the
# agreed contract, and as in cristianku/opendbc psa-torque-sunny-testing (plus the speed validity).

struct CarStateOld {
  vEgo @0 :Float32;
}

struct CarStateContract {
  vEgo @0 :Float32;
  stockRadarLeadDistance @1 :Float32;
  stockRadarLeadRelativeSpeed @2 :Float32;
  stockRadarLeadValid @3 :Bool;
  stockRadarMeasurementTimeNanos @4 :UInt64;
}

struct CarStateNew {
  vEgo @0 :Float32;
  stockRadarLeadDistance @1 :Float32;
  stockRadarLeadRelativeSpeed @2 :Float32;
  stockRadarLeadValid @3 :Bool;
  stockRadarMeasurementTimeNanos @4 :UInt64;
  stockRadarRelativeSpeedValid @5 :Bool;
}
