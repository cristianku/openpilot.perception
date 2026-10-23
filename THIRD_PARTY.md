# Third-party code, data and models

The MIT license of this repository covers only its original code. Everything below keeps its own
license; check it again before adding or redistributing anything.

## Used at run time (not vendored)

| Component | Used for | License |
| --- | --- | --- |
| [numpy](https://numpy.org) | arrays of the radar labels | BSD-3-Clause |
| [openpilot](https://github.com/commaai/openpilot) (`tools.lib.logreader`) | reading comma `rlog.zst` | MIT |
| [opendbc](https://github.com/commaai/opendbc) | tests only: cross-check of the bit layout against `psa_aee2010_r3.dbc` | MIT |

## Derived knowledge

- The bit layout of 0x2F6 and 0x4F6 follows `opendbc/dbc/psa_aee2010_r3.dbc` (opendbc, MIT). No
  DBC file is copied here.
- The 0x2F6 checksum seed (`0x8`) follows `cristianku/opendbc@psa-torque-sunny`
  (`opendbc/car/psa/psacan.py`, MIT), confirmed on recorded frames.

## Models and weights

None yet. Any pretrained model added later (backbone, depth or detection network) must be listed
here with its license and source before use; weights with a non-commercial or unclear license
must not be published from this repository.

## Data

No recording is stored in this repository. The two 8-byte CAN frames in
`tests/test_psa_radar.py` are single radar frames (lead distance and status only), used as test
fixtures.
