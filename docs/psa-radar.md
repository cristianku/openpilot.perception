# PSA physical radar as a metric label

What the stock radar of a Peugeot 3008 (PSA AEE2010, ARTIV) gives, who else writes its CAN
messages, and how [`perception/radar/psa.py`](../perception/radar/psa.py) keeps only the genuine
measurements. Inspected on 2026-10-23 from the code below and the local recordings of
openpilot.distill; no repository and no car setting was changed for it.

## Sources inspected

| What | Where | Commit |
|---|---|---|
| PSA port with radar emulation | `cristianku/opendbc@psa-torque-sunny` | `52dda95` (2026-10-18) |
| openpilot build that pins it | `cristianku/openpilot@psa-torque-sunny` → `opendbc_repo` = `52dda95` | `e359e65` |
| Sunnypilot `distilled` | `cristianku/sunnypilot@distilled` → `opendbc_repo` = **`sunnypilot/opendbc` `5be7507`** (upstream, not the PSA fork) | `bb1b807` |
| Local recordings | `openpilot.distill/data/*/rlog.zst` (145 segments) | — |

`sunnypilot:distilled` does **not** contain the PSA fork: upstream PSA there has `dashcamOnly=True`,
`radarUnavailable=True` and no radar code at all. The radar emulation exists only in the
`psa-torque-sunny` line.

## What the stock radar (ARTIV) publishes on the ADAS bus (bus 1)

| ID | Name | Rate | Useful signals |
|---|---|---|---|
| 0x2F6 | `HS2_DYN_MDD_ETAT_2F6` | 50 Hz | `TARGET_DETECTED` (bit), `INTER_VEHICLE_DISTANCE` (10 bit, 0.25 m, 0..255.5 m), `DISPLAY_INTERVEHICLE_TIME`, `TARGET_POSITION` (3 bit enum POS1..POS7) |
| 0x4F6 | `HS2_DAT_ARTIV_V2_4F6` | 10 Hz | `DISTANCE_GAP` (1 m, 0..253), `RELATIVE_SPEED` (0.02 m/s, ±70), `TIME_GAP`, `TARGET_DETECTED`, `ARTIV_SENSOR_STATE`, `TRAFFIC_DIRECTION` |
| 0x2B6 | `HS2_DYN1_MDD_ETAT_2B6` | 50 Hz | ACC actuation (torque/decel requests) — not a perception signal |
| 0x796 | `HS2_SUPV_ARTIV_796` | 1 Hz | supervision/fault |

This is **one lead target**, not an object list or a point cloud: there is no lateral distance in
metres, no track id, no second object.

`TARGET_POSITION` must not be read as a lateral coordinate:
- the DBC defines only an enum `POS1..POS7` (7 = invalid), no unit, no scale;
- the PSA controller itself writes into it `self.bars`, a 0..3 *distance* bucket
  (`distance / (5 + vEgo)`) used by the cluster display (`carcontroller.py:_update_lead_display`);
- in the local logs it is always 0, also with a target detected (see the census below).

The 0x2F6 checksum seed is `CHK_INI = 0x8`: it matches all 434 975 radar frames of the 145 local
segments and the PSA fork's `psacan.psa_checksum`. The DBC comment says `0x7` and upstream
opendbc uses `0xB`; upstream also names the signal `CHECKSUM_TRANSM_DYN_ACC2`, so its `CANParser`
checks no checksum on 0x2F6 at all. `perception/radar/psa.py` decodes the few signals itself and
checks the checksum with `0x8`.

## Who writes 0x2F6/0x4F6/0x2B6/0x796, and when

Order inside `CarInterface.update` (`interface.py`): `CC.process_radar_can(can_packets)` runs
**before** `CarState.update`.

1. `process_radar_can` reads only `src == PSA_ADAS_BUS (1)` as genuine RX; `src 129` is the panda TX
   receipt (echo) of frames openpilot sent, `src 193` a TX the safety blocked.
2. While a radar session is active, it **rewrites the echoes `src 129 → src 1`** in the packets
   handed to the parsers, so `CarState` sees openpilot's own substitute frames as if they came
   from the radar. This remap is in memory only: the logged `can` events keep the original `src`.
3. With `openpilotLongitudinalControl` and `longitudinal_enabled` (needs `dashcamOnly=False`,
   not passive, safety bit `PSA_LONG_CONTROL`), after **1 s of valid CAN — also while moving —**
   the controller sends `0x6B6 02 10 02` (ARTIV programming session). The stock radar goes
   silent; the controller then emits 0x2B6/0x2F6 at 50 Hz, 0x4F6 at 10 Hz, 0x796 at 1 Hz, plus
   TesterPresent `0x6B6 02 3E 00`. The session stops on stock radar resuming, diagnostic refusal,
   bus/echo/TesterPresent timeouts or invalid CAN.
4. The substitute 0x2F6/0x4F6 lead values come from **`radarState.leadOne`, else
   `modelV2.leadsV3[0]`** (`_update_lead_display`). With `radarUnavailable=True`, radarState is
   itself vision-derived. These frames are therefore model output, **not measurements**: using
   them as supervision would train the model on itself.
5. Without longitudinal, openpilot still sends occasional 0x2F6 copies of the last stock frame with
   `REQUEST_TAKEOVER` changed (`create_request_takeover`, bus 1). They appear in the logs as
   `src 129` and must be ignored; the stock frames keep arriving on `src 1`.

The panda safety (`opendbc/safety/modes/psa.h`) lists 0x2F6/0x4F6/0x2B6/0x796/0x6B6 as TX on
bus 1 with `check_relay = false`: nothing is intercepted, openpilot frames are added alongside the
radar's ones.

## Passive collection needs no change to the car

`tasks/fetch.py` already downloads `rlog.zst` for every segment, and the rlog keeps every `can`
event with its `src`. A local census of one dashcam segment (`0000011e0c8825628a01`,
`PSA_PEUGEOT_3008`, `dashcamOnly=True`) shows only genuine frames:

```
('can', '0x2b6', 1) 3000    ('can', '0x2f6', 1) 3000
('can', '0x4f6', 1) 600     ('can', '0x796', 1) 60
```

Decoded (segments `…a09`): 0x2F6 target in 2605/3000 frames, 4.75–69.25 m; 0x4F6 5–69 m,
relative speed −3.8..+4.5 m/s; `ARTIV_SENSOR_STATE = 2 (Active)` throughout.
In `…a01` 0x2F6 reports a target at ~10 m while 0x4F6 reports none: the two messages do not always
agree, so the label should record which message it comes from.

Rules for a genuine radar label, offline, from the rlog (all in `perception/radar/psa.py`):
- keep 0x2F6/0x4F6 frames with `src == 1` only; `src 129/193` are openpilot's own frames and are
  only counted. In the logged `can` events openpilot's frames never carry `src 1`, so this rule
  alone excludes every substitute frame;
- record each `0x6B6 02 10 02` request (logged as `src 129`): from there the stock radar is silent
  and the log has no genuine radar until 0x2F6/0x4F6 come back on `src 1`;
- a label is valid only with `TARGET_DETECTED`, a good 0x2F6 checksum, 0x4F6
  `ARTIV_SENSOR_STATE = 2 (Active)` and values inside the DBC ranges; the `255.5 m`, `254 m`,
  `93.8 m/s`, `25.5 s` written without a target are never distances;
- no lateral position, no calibration, no timestamps are invented: only `logMonoTime` of the CAN
  event.

## Consequence for recording

Since `52dda95` (2026-10-18) the PSA branch has `dashcamOnly=False`. With the alpha-longitudinal
toggle on, every drive silences the stock radar about 1 s after start, so **those drives carry no
genuine radar data**. To collect radar labels, record with openpilot longitudinal **off**
(or in dashcam mode): the stock ACC radar then runs untouched and openpilot only listens.

## Census of the 145 local segments

All 145 segments are `PSA_PEUGEOT_3008`, `dashcamOnly=True` (recorded before the dashcam change),
so no frame was injected:

| | frames on `src 1` | with target |
|---|---|---|
| 0x2F6 (50 Hz) | 434 975 | 224 543 (51.6 %, ≈ 75 min) |
| 0x4F6 (10 Hz) | 86 996 | 43 904 (≈ 73 min) |

`src 129` echoes: 0, `src 193` blocked: 0, `0x6B6` diagnostics: 0, segments without radar: 0.
`python -m perception.radar.psa <rlog.zst>` prints the same counts for any log.
