# perception-web: Radar, Objects and the review API (design spec)

This spec covers perception-web (openpilot.perception, MIT). It answers four requests from the owner:
- "how do I see the radar";
- "in the Settings I want to be able to enter these calibrations";
- "recognise the objects and how far they are, and confirm them via web and via API, like the speed-limit site";
- "make the site intuitive".

It starts from the winning **newcomer** design and adds the strongest ideas of the other two:
- from **honesty**: blind review, number and provenance rules, calibration coverage, drive classification;
- from **reviewer**: a radar line that uses no depth, prefetch and keys, a neighbour filmstrip, records of what each reviewer was shown.

It fits what is already built (commit 99ed0ab):
- `/api/radar`, `/api/radar/drives/<route>`, `/picture/radar/<segment>/<t_ms>.jpg`, `/api/comma/recordings`;
- `/api/radar/tests[/<id>]`, `/api/radar/calibration/apply`;
- the `radar_index` and `fetch_recordings` jobs, `RadarTests`, and `web/comma.py` (read-only on the comma).

## 0. Decisions taken, and the judges' findings they fix

| Topic | Decision |
| --- | --- |
| Tabs | 4 buttons that fit a 360 px phone: **Home · Radar · Objects · Lab**. Only the labels change: the `overview`, `experiments` and `results` panels and their hashes stay as they are, so no redirects are needed and `data-goto` keeps working. "Lab" is one bar button over the two existing panels. |
| Comma | Nothing is started on the comma: no live reading and no "recording" probe. Short recordings are listed and copied with the existing `fetch_recordings` job (rlog plus qcamera, so they have pictures). There is no comma address field: `comma_host()` already takes the one openpilot.distill uses. |
| Radar test | Reuses `RadarTests`, which needs both tapes for every test. There is no "skip the comma tape", because that would invent a measurement. A test whose recording is not reachable yet is saved as a small draft (tapes only), and the owner finishes it later by picking the recording. Nothing finishes on its own. |
| Pictures | The on-demand `/picture/radar/...` (ffmpeg, cached) is the only way to get a picture: there is no "make the pictures" job. The find-cars job only warms the same cache. |
| Bias (C1) | Review is **blind**. Before the answer the page shows no camera distance and no rule suggestion. Afterwards it reveals both in a 4-second strip over the *next* picture, so there is no fixed wait. The only aids shown before the answer use no depth: the lane band from the car's motion and the flat-road "radar line". |
| Speed | The next 3 pictures are prefetched and saving is optimistic. On a computer one key picks a box and Enter confirms; on a phone it is tap, then Confirm. There is no batch confirm and nothing is pre-selected (no rubber-stamping). |
| AI answers | Same rule as the speed-limit site: **an AI confirmation counts**, marked `ai_review`. An AI never changes a human answer. An optional "Check the AI" round measures agreement. Evaluation sets use human answers only, made without seeing the estimates. |
| Labels | Wording: "confirmed pairs" on the page; "radar-associated pseudo-label" in exports. A pair is *ready* only when the calibration has a test at 5 m or more, the chosen box is a vehicle, and it lies in the lane band. The current "close only" status is shown honestly. |
| Warnings | One calibration line per screen, never a chip on every number. There are 3 statuses, not 5. |
| Radar words | "reading" or "no reading" only. Never "too close", which the log cannot tell apart from "no target". The relative speed shows as "not sent", never 0. |
| Units | Metres with a sign everywhere, including hand entry (today's field is in cm). The uncertainty shown is the server's `uncertainty_m` (0.09 m for 2 tests), not a hard-coded ±0.13. |
| API token | `PERCEPTION_WEB_TOKEN`, in a header only. The API refuses (503) when the server made up a random code at start. `tasks/update.py` writes a fixed token if none exists, so no manual step is needed. There is no rotate button in v1. |
| Parallel agents | Like the sister site: no locks and no leases. `run_id` plus `base_revision` catch conflicts with a 409 that includes the current review. |
| Detector | RT-DETR (Apache-2.0), from the original code or the Hugging Face transformers port, pinned. Never the Ultralytics `RTDETR` class and never Ultralytics YOLO (AGPL-3.0). It is listed in THIRD_PARTY.md before use. |

### Shared rules (every new screen)

- **Number formats.** One helper, `fmtM(kind, v)`:
  - raw radar reading: as sent, 2 decimals, a multiple of 0.25 ("17.75 m");
  - distance from the comma while driving: 1 decimal ("18.4 m");
  - camera estimate: whole metres with ≈ ("≈ 18 m");
  - tape and calibration screens: 2 decimals ("2.82 m"); the correction always carries its sign ("−0.89 m");
  - differences: "0.5 m apart".
- **Provenance chips** (existing `.pill`; a tap opens the info sheet with one sentence):
  - `measured` (pill ok): "Measured by your car's radar.";
  - `≈ camera` (pill imp): "Guessed from the camera alone: an estimate.";
  - `you` (pill rev): "Confirmed by you.";
  - `AI` (pill todo): "Confirmed by an AI agent through the API."
- **Radar state words:** "reading", "no reading". The relative speed is "not sent by the radar".
- **Calibration line:** shown once per screen with a distance, in `.warn-text`: "Checked only up to 1.3 m so far: farther distances are not confirmed yet." It is hidden once a test at 5 m or more exists.
- **Drive names:**
  - first line: the GPS start, "Thu 23 Oct · 19:42";
  - when there is no GPS time (`start_from` is "comma clock" or none): "Drive no. 301 · time unknown";
  - second line, always: "drive no. 301", the decimal of the route counter (`int(route[:8], 16)`).
  - Every list is sorted by the counter, newest first, never by the comma's clock. This needs a small fix in `RadarIndex.drives`, which today sorts drives by start time.
- The page never shows paths, route hashes, shell commands or the token.
- **Empty states:** every one says why it is empty and offers one button: the step before.
- **Focus mode:** `body.focus` hides `.tabs` and `.gear`. A slim bar shows ✕ · progress · (History). The primary button is pinned above the safe area. The screen is kept awake with `navigator.wakeLock` where the browser has it.

---

## 1. Navigation

**Bar:** `Home · Radar · Objects (badge) · Lab`. Settings stays behind the gear.

| Bar button | Hash | Panel | Notes |
| --- | --- | --- | --- |
| Home | `#overview` | existing `panel-overview` | Only the label changes ("Overview" becomes "Home"); the page title becomes "Home". |
| Radar | `#radar`, `#radar/<route>`, `#radar/test` | new `panel-radar` | `<route>` is a drive (18 hex) or a recording (`8hex--10hex`); the existing `ROUTE_RE` takes both. |
| Objects | `#objects`, `#objects/find`, `#objects/confirm?drive=<route>&queue=new\|check_ai`, `#objects/answers` | new `panel-objects` | Badge: the existing `.count` pill showing `status.objects.to_confirm`, hidden at 0. |
| Lab | `#experiments`, `#results`, `#results/<id>` (and `#lab` → `#experiments`) | the existing two panels | `tab-lab` is selected when either panel shows. Each panel gets a `.segmented` switch at the top: "Start one \| Results (3)". |

**Settings order:** Drives · **Your car's radar** (redesigned) · **Agents and scripts** (new) · Administrator · Appearance · This server.

**Home changes.** A new "Next step" card goes first. The 4th drive tile becomes real. Plan step 2 links to Radar and Objects. The GPU/CPU/RAM/Disk tiles move to the bottom, under the heading "This server".

**How a newcomer finds each feature:**
1. Home › Next step always names the one thing to do and opens it.
2. Each empty state ends with the button for the step before it.
3. The Radar tab and Settings › Your car's radar open the same tape-test wizard.
4. The first confirmation shows a 3-card guide once; "Show the guide again" stays on Objects.
5. "?" buttons open the existing info sheet with one short explanation (texts in §2.15).
6. The Objects badge counts what waits for the owner.
7. On devices with a pointer, the answer buttons show their key as a small kbd chip, hidden under `@media (hover:none)`.

**Keyboard.** The tabs keep arrow-key navigation. On the confirm screen:
- A–I pick a lettered box, N none, U can't tell;
- Enter confirms, Z undoes;
- ←/→ step to the neighbouring pictures, Esc closes, ? lists the keys.

Keys work only when no text field has focus, and `preventDefault` applies only inside the confirm screen.

---

## 2. Screens

### 2.1 Home (`#overview`)

**Purpose:** the one next step first, then the plan, drives and jobs as today.

**Layout and copy**
- Page head: title "Home", sub "How far things in front of your car are, from the road camera alone."
- **Card "Next step".** It has an accent outline, a 96 px illustration in the style of the wizards, a heading, one line and one primary button. The server picks the first case that applies (`status.next.case`, computed from caches only and never starting a job); the copy lives in app.js:

  | case | Heading / line | Button |
  | --- | --- | --- |
  | `radar_not_set` | "Set up your car's radar" / "A tape measure and a wall turn the radar into a ruler for the camera. About 10 minutes." | [Start the test] |
  | `test_waiting` | "Finish your radar test" / "Your tape measurements of Thu 23 Oct are saved. Pick its recording when the comma is back on your Wi-Fi." | [Finish it] |
  | `radar_reading` | live job card "Read the radar · 2 of 6 segments · about 1 min left" | (Details, Stop) |
  | `no_radar_drive` | "Record a drive with the radar" / "Only drives recorded on the new software, from the evening of 23 October, carry the radar. Just drive: new drives show up here by themselves once openpilot.distill has downloaded and localized them." | [Why only new drives?] (sheet) |
  | `find_objects` | "Find the cars in your drive of Thu 23 Oct" / "The server draws a box around every car, bike and person. About 3 minutes on the graphics card." | [Find them] |
  | `objects_running` | live job card "Find the cars · 40% · about 2 min left" | (Details, Stop) |
  | `to_confirm` | "48 pictures to check" / "Tell the site which car the radar is measuring. About 4 minutes; stop whenever you like." | [Start checking] |
  | `far_test` | "Test the radar farther away" / "Your tests are all closer than 1.5 m. One test about 10 m behind a parked car tells whether the distances hold where the labels will be." | [Start the test] |
  | `caught_up` | "All caught up" / "New drives with radar show up here by themselves." | none |

  - Under the button, at most one muted line "Also waiting: …" with a text button. Examples: "Also waiting: test the radar farther away [How]", "Also waiting: check a few AI answers (35) [Check]".
- **The plan** (as today). Step 2, "Distances from the car's radar":
  - pill by calibration status: "not set" (bad), "checked close only" (todo), "checked at 5–30 m" (ok);
  - text: "The car's radar measures the vehicle ahead in your lane: the real ruler for the camera. 3 drives with radar · 112 pictures confirmed by you · far test missing.";
  - buttons [Open Radar] [Check pictures (48)].
- **Drive tiles.** The 4th tile becomes "3" / "drives with radar" / "recorded on the new software" and links to Radar. Before any radar has been read: "–" / "not read yet: open Radar".
- Running / Recent jobs: as today.
- Heading "This server": the four machine tiles, moved here.

**States**
- Loading: a skeleton line, "Checking what to do next…".
- Status unreachable: the live dot goes red as today; the card keeps its last content, with muted "not updated".
- When the job for the step ends, the card moves to the next case within one poll (4 s).

**Mobile:** Next step first, full width; button 48 px, full width. Drive tiles 2×2; machine tiles one per row, last.

### 2.2 Radar (`#radar`)

**Purpose:** "how do I see the radar". Is it set up? Which drives carry it? Which short tests exist?

**Layout and copy**
- Page head: title "Radar", sub "The radar behind your front bumper measures one thing: how far away the vehicle ahead in your lane is. Here is what it measured on your drives." A [?] opens "How to read the radar" (§2.15).
- **Card "Set-up"** (calibration):
  - Pill: "not set" (bad) / "checked close only" (todo) / "checked at 5–30 m" (ok) / "typed by hand" (todo).
  - Line: "Distance from the comma = reading + 0.61 m."
  - **Coverage strip** (inline SVG, 0–40 m axis, ticks every 10 m):
    - one dot per kept test at its tape distance (0.90, 1.32);
    - a shaded band at 5–30 m labelled "where the labels will be";
    - caption: "Tested distances. Confirmed pairs become labels after a test in the shaded band."
  - Buttons: primary [Test with a tape] (it reads [Test it farther away] when the status is close only); secondary [Change the numbers] (opens Settings › Your car's radar).
  - Not set: "The site doesn't know yet where your radar sits. Until then, readings are shown as the radar gives them: from the radar, not from the comma." Primary [Set it up, about 10 minutes].
  - When the status is "checked at 5–30 m", the card folds into one line: "Set up ✓ · tested 0.9–12 m ›".
- **Card "Drives with radar"** (`.route-card` rows, newest first by counter). Each row:
  - title "Thu 23 Oct · 19:42";
  - detail "drive no. 301 · 24 min · something ahead 61% of the time · 3.0–58.2 m from the comma";
  - a thin `.bar` showing the share of time with a reading;
  - right side: a pill "48 to check" (todo) once its cars are found, plus a chevron.
  - Tapping the row opens §2.3.
- **Folded row "Drives without radar readings (142)"** [Show]. It opens a muted list with no actions. Each item gives its reason:
  - "Same software as your drives with radar: nothing was ahead." The drive's `commit` has given readings in another drive.
  - "Most likely recorded before the new software." The commit never gave a reading.
  - Footer: "They still count for the camera experiments in Lab."
- **Card "Short tests"**:
  - Lead: "Recordings made standing still, for example in front of a wall. They stay on the comma until you get them; the site only reads them."
  - Status pill: "Comma reachable" (ok), "Comma not reachable" (muted), or "Not checked yet".
  - Button [Look on the comma] calls `GET /api/comma/recordings`, at most once every 30 s and never on the 4 s poll.
  - Rows merge the copied recordings (`kind: recording` in `/api/radar`) with the ones still on the comma. Sorted newest first by counter:
    - copied, with a standstill: "Recording no. 308 · 1 min 05 s · steady 2.25 m for 18 s" [Open] [Use for a test];
    - copied, none: "Recording no. 306 · 48 s · no steady reading" [Open];
    - only on the comma (short ones): "Recording no. 309 · 1 min · on the comma" [Get it] (job `fetch_recordings`, up to 6 at a time).
  - Note: "Listed in the order the comma made them: its clock can be wrong."

**States**
- Loading: three skeleton rows, "Looking for the radar in your drives…".
- New segments: `/api/radar` starts `radar_index` by itself, and the `.job-live[data-actions="radar_index,fetch_recordings"]` card shows "Read the radar · 12 of 30 segments · about 1 min left". Drives appear as their segments finish.
- No drives folder: "No drives on this server yet. Choose the drives folder in Settings." [Open Settings].
- Drives exist but none has a reading: illustration of an empty road with a dashed radar cone; "No drive with the radar yet"; "Only drives recorded on the new software, from the evening of 23 October, carry it. Just drive: they show up here by themselves once openpilot.distill has downloaded and localized them."
- Comma (on 503 `comma_unreachable`): "The comma doesn't answer. Is it switched on and on the same Wi-Fi as this server? The site uses the comma set up in openpilot.distill." [Try again]. Copied tests stay listed.
- Comma reachable, no short recordings: "No short recordings on the comma."
- Load error: a toast with the message, and the card says "Couldn't load the radar. Trying again in a few seconds."

**Mobile:** single column. Rows 64 px, the whole row tappable. Test row buttons go full width under the row.

### 2.3 Radar › one drive or recording (`#radar/<route>`)

**Purpose:** see the radar over a drive: the picture with the distance written on it, the distance over time, and how the number is made.

**Layout and copy**
- Back arrow "Radar". Title "Thu 23 Oct · 19:42"; sub "drive no. 301 · 24 min · something ahead 61% of the time". For a recording: "Recording no. 308 · 1 min 05 s · copied from the comma".
- **Picture card.** The `<img>` comes from `/picture/radar/<segment>/<t_ms>.jpg`; overlays are HTML over the image:
  - bottom centre, a large badge "18.4 m", the small line "to the vehicle ahead, from the comma", and the chip `measured`;
  - grey "No reading" when there is none;
  - without calibration: "Reading 17.75 m (from the radar)";
  - chip top left: "12:31" (time into the drive); chip top right: "48 km/h".
- **Controls**, three equal 48 px buttons: [◀ 1 s] [1 s ▶] [Next vehicle ahead ▶|] (jumps to the next stretch with readings).
- **Chart card "Distance to the vehicle ahead".**
  - An SVG step line of `chart.from_comma_m` (or `reading_m` without calibration). Y axis "m from the comma", from 0 up to the maximum rounded up to 10; X axis mm:ss.
  - Real gaps where there is no reading, and no line drawn across them.
  - A thin chart under it, "Your speed (km/h)".
  - A vertical cursor marks the picture's time. Tap or drag moves it; the picture is fetched 300 ms after the drag stops.
  - Tooltip: "12:41 · 20.6 m from the comma (reading 20.00 m) · 52 km/h".
  - A `.segmented` switch: "Whole drive | 2 minutes".
- The calibration line (shared rules), right under the chart.
- **Row "How this number is made"** (`<details>`): "Radar said 17.75 m − 0.89 m correction + 1.50 m to the comma = 18.4 m. The radar counts in steps of 0.25 m."
- **Facts** (`.kv`):
  - "Radar readings": 14 302
  - "Something ahead": 61% of the time
  - "Closest": 3.0 m
  - "Farthest": 58.2 m
  - "Speed of the vehicle ahead": "not sent by the radar" (when `relative_speed_valid` is 0)
  - "Video": "on this server" or "not on this server"
- **Bottom button:** [Find the cars in this drive] (§2.8 with this drive chosen), or [Check the pictures of this drive (48)] once found.
- **Recording variant:**
  - callout "Standing still 0:12–0:30 · steady 2.25 m for 18 s", one line per standstill;
  - primary [Use it for a radar test] (§2.4, starting at step 1 with this recording attached);
  - without qcamera: "No video for this recording: only its radar was copied."

**States**
- First picture: a dark 16:10 box with a spinner and "Preparing the picture…". ffmpeg can take a few seconds at the end of a minute. After that the old picture stays until the new one arrives, so it never flashes.
- 404 `no_video`: "No picture here: the video of this minute is not on the server."
- Chart loading: a grey band.
- Not calibrated: an amber `.callout` over the picture: "These are raw radar readings. Set up the radar to see distances from the comma." [Set it up].
- No reading in the whole drive: "The radar saw nothing ahead in this drive."
- 404 `unknown_drive`: "This drive is no longer on the server." [Back to Radar].

**Mobile:**
- The picture is full-bleed; the badge text is 28 px.
- The chart is 160 px with `touch-action: pan-y`: a sideways drag scrubs, a vertical drag still scrolls the page.
- The facts list has two columns.

### 2.4 Radar test (wizard, `#radar/test`, focus mode)

**Purpose:** two tapes plus one still minute become a test, using the existing `POST /api/radar/tests` (`segment`, `standstill`, `tape_radar_m`, `tape_comma_m`, plus an optional `target`).

The slim bar shows ✕ and "Step N of 6". Every step has one question, `.choice` cards or a big `.stepper`, and a pinned primary button. The wizard opens from Radar, Settings, Home's Next step or a recording (§2.3).

**Step 1 of 6: "What will the radar look at?"**
- Sub: "A test takes about 10 minutes and a tape measure."
- Choices:
  - [art: the nose of a car 1.5 m from a garage wall] **"A wall, close"** / "1.5 to 2 m from a garage wall or a closed door. The easiest one."
  - [art: a car 10 m behind a parked car] **"A parked car, farther"** / "About 10 m behind a parked car. The test still missing." It carries a pill "Needed" while no test at 5 m or more exists.
- Explain line: wall "Tells how much the radar reads too long, close up." / car "Checks the correction where the labels will be, at 5–30 m."
- Pre-selected: the wall when there are no tests; the car when only close tests exist.
- If a draft exists, a callout on top: "One test is still waiting for its recording. [Finish it]"

**Step 2 of 6: "Park facing the wall"** (car variant: "Park behind the other car")
- Sub: "Straight on, with the bumper about 1.5–2 m from the wall." (car: "In a straight line, about 10 m behind it, on flat ground.")
- Top-view illustration: the car square to the wall with a bracket "1.5–2 m", and a small crossed-out angled car.
- Checklist rows (wall):
  - "Stop straight, not at an angle."
  - "Nobody and nothing between the car and the wall."
  - "Once parked, switch the car off."
- Checklist rows (car):
  - "Both cars straight, one behind the other."
  - "The other car stays still; switched off is fine."
  - "You need a 10 m tape or a laser meter."
  - "Once parked, switch your car off."
- Primary [I'm parked, car off]. On tap the site looks on the comma for at most 10 s ("Looking for the comma…", with a link "Skip") and remembers the newest recording number as a marker. Inline result, then it moves on by itself after 1 s:
  - "Comma reachable ✓: the site will know which recording is new."
  - "Can't reach the comma from here. No problem: you'll pick the recording later."

**Step 3 of 6: "Switch on and keep still for one minute"**
- Sub: "Car on, foot off the pedals, nobody in front of it."
- Primary [Start the minute] starts a 200 px countdown ring, "0:47". The countdown runs from its start time, so it carries on after the phone locks.
- After 20 s a link "Finish early" appears.
- At 0:00 the title becomes "Now switch the car off" and the primary is [Done, it's off].
- Without wake lock, a line: "Keep the screen on until the end."

**Step 4 of 6: "How far is the wall from the radar?"** (car: "How far is the other car from the radar?")
- Sub: "Tape from the wall to the front of your bumper, right in front of the radar. Use the same spot every time." (car: "From the back of the other car to the front of your bumper, in front of the radar. Keep the tape straight and tight.")
- Side-view art with an arrow from the wall to the bumper.
- `.stepper` in metres: [−] "1.32 m" [+], 1 cm steps. Tapping the number opens the decimal keyboard; both "1,32" and "1.32" are accepted.
- Immediate checks (amber):
  - wall under 0.8 m: "Very close: the radar may see nothing. Back up to 1.5–2 m and measure again."
  - wall over 5 m: "Walls farther than a few metres are often not seen. Try the parked-car test."
  - car outside 3–40 m: "For this test, 5 to 30 m works best."
- Out of range, red: "Between 0.30 and 100 m." (the server's `TAPE_RANGE_M`).
- Empty: "0.00 m" in grey, and the button is disabled with "Write the distance first."

**Step 5 of 6: "And how far is the comma?"**
- Sub (wall): "From the wall to the comma behind the windshield, along the side of the car." (car: "From the back of the other car to the comma, along the side of your car.")
- Stepper "2.82 m".
- Live line, 17 px bold: "So the comma sits 1.50 m behind the radar."
- 3 cm or more from the saved value, amber: "Last time it was 1.50 m: this is 4 cm different. Check that the tape is straight and level."
- Comma not farther than the radar, red: "The comma must be farther from the wall than the radar."
- More than 5 m apart, red: "More than 5 m from the radar: check the tape."

**Step 6 of 6: "Which recording is this test?"**
- Sub: "Newest first, by the comma's own count: its clock can be wrong."
- Single-column cards (72 px). A recording made after the marker carries the pill "made after you parked" and is pre-selected when it is the only one. Each card: "Recording no. 308 · 1 min 05 s", and once read, "steady 2.25 m for 18 s".
- Picking one that is not copied yet starts `fetch_recordings` (which also reads its radar). The live card says "Getting the recording from the comma, then reading its radar…".
- Several standstills: chips "0:12–0:30 · 2.25 m" and "0:41–0:52 · 2.25 m".
- No standstill: "The radar never held a steady reading while the car stood still in this recording." [Pick another] [Record again] (back to step 3).
- Primary [Use this one].
- Comma not reachable: "The comma isn't reachable from here." [Try again], with secondary [Finish later]. That button does `POST /api/radar/drafts` and shows: "Saved for later" / "Your two tape measurements are kept. When the comma is back on your Wi-Fi, tap Finish it on Home or in Settings › Your car's radar." [OK]

**Result** (no step number)
- Title "The radar read 2.25 m"; sub "Your tape said 1.32 m."
- Large tile: "Reads 0.93 m too long" (car: "Reads 0.91 m too long at 10.20 m").
- `.checklist`:
  - "With all your tests: correction −0.90 m ± 0.07 m (3 tests)"
  - "From the comma: 2.25 m becomes 2.85 m. Tape: 2.82 m. 3 cm apart ✓"
  - "The radar counts in steps of 0.25 m, so one test is good to about ±0.13 m."
  - Car test that brings the first test at 5 m or more: "With this test, confirmed pairs can become labels."
- Outlier (more than 0.5 m from the mean of the other tests): an amber callout "This test is 0.6 m away from your other tests. Measure again before saving?" [Measure again] [Save anyway].
- Primary [Save the test]; secondary [Throw it away]; link "Test again".
  - Saving posts the test, deletes the draft if there was one, and re-applies the calibration when its source is "tests".
- Saved: check art, "Test saved" / "The correction is now −0.90 m, from 3 tests." [Do another test] [Back to Radar]. Toast: "Saved. A reading of 20.00 m is now 20.60 m from the comma."
- Source "typed": the saved screen adds "Your numbers were typed by hand. Use your tests instead?" [Use the tests] [Keep my numbers].
- Save error: the server's message as a `.reason` line under the button.

**Mobile:** cards stack (180 px, art on top). Steppers are 56×56 with a 36 px value. The button stays visible above the keyboard. Illustrations are 140 px.

### 2.5 Settings › Your car's radar (`#settings/radar` scrolls to it)

**Purpose:** enter and review the calibration as measurements, with the range it was checked over.

**Layout and copy**
- h2 "Your car's radar".
- Lead: "The radar measures from the front of the car, the comma from the windshield. Two numbers turn a radar reading into a distance from the comma. A tape measure and a wall are enough: the test shows how."
- Formula box (18 px): "From the comma = reading − 0.89 m + 1.50 m = reading + 0.61 m". Under it: "A reading of 20.00 m is 20.61 m from the comma."
- Status pill and the coverage strip (as in §2.2).
- `.kv`:
  - "From the radar to the comma": "1.50 m" / "from 2 tests: tapes 2.82 and 1.32 m, 2.40 and 0.90 m"
  - "Correction of the reading": "−0.89 m ± 0.09 m" / "the radar reads about 0.9 m long; tested at 0.90–1.32 m"
- Buttons: primary [Test with a tape]; secondary [Type the numbers].
- **Type the numbers** opens an inline editor in place of the buttons:
  - stepper "From the radar to the comma" [−] 1.50 m [+];
  - stepper "Correction of the reading" [−] −0.89 m [+] (−1.00 to +1.00, 1 cm steps);
  - the live sentence "The radar says 20.00 m → 20.61 m from the comma.";
  - [Save] [Cancel]. Saving sets the source to "typed"; the pill becomes "typed by hand", and a line "Your tests give −0.89 m." [Use the tests] appears.
- **Sub-heading "Tests at a known distance"**, `.checklist` rows; each ⋯ opens a sheet with "Leave it out" / "Use it again" / "Delete":
  - ✓ "Wall · tape 1.32 m · radar 2.25 m" / "reads 0.93 m long · 23 Oct · recording no. 305"
  - ✓ "Wall · tape 0.90 m · radar 1.75 m" / "reads 0.85 m long · 23 Oct · recording no. 306"
  - ⏳ "Wall · tape 1.40 m" / "waiting for its recording" [Finish it] (draft)
  - ! "Parked car at 5–30 m" / "not done yet: needed before confirmed pairs become labels" [Do it]
- When the kept tests disagree by more than 0.5 m, amber: "Your tests disagree by 0.6 m, more than the radar's 25 cm steps explain. Check the tape, or leave one test out."
- Muted link at the bottom: "My car has no radar". It asks "Turn the radar off? Drives will be used without it." and then clears both values.

**States**
- Not set: values read "not measured yet" in amber; the primary is [Set it up, about 10 minutes].
- Saving: a spinner in the button.
- Validation lines: "Between 0 and 5 m, for example 1.50." / "Between −1.00 and +1.00 m, for example −0.89."
- Deleting a test asks: "Delete this test? The correction is worked out again from the others."
- Saved: toast "Saved. The radar says 20.00 m → 20.61 m from the comma."
- Server error: the `.reason` line.

**Mobile:** the key–value list stacks label over value; steppers are full width at 48 px; ⋯ menus open the bottom sheet.

### 2.6 Settings › Agents and scripts (`#settings/agents`)

**Purpose:** let agents confirm through the API, control them and see who used it. No secret is ever shown.

**Layout and copy**
- h2 "Agents and scripts".
- Lead: "An AI agent or a script can look at the pictures and confirm cars for you, through the site's API, with the same rules as this page. Every answer records who gave it (you or an AI) and how (this page or the API)."
- `.segmented` "Agents can:" [Look and answer | Look only | Nothing]. The default is Look and answer.
- `.kv`:
  - "API address": "http://192.168.1.20:8767/api/v1" [Copy]. On loopback, the note: "The site listens only on this computer: agents must run on this server."
  - "Key": "The site's access token, set when the site was installed. It stays on the server; this page never shows it."
  - "Guide": link "Guide for agents" (docs/API.md on GitHub).
  - "Last used": "today 19:10 by ai/Claude · 120 answers through the API", or "Not used yet".
- Note: "AI answers count, marked as AI, as on the speed-limit site. An AI never changes your answers: yours always win."

**States**
- No fixed token (the server made a random code at start): amber callout "Agents can't connect yet: this server has no fixed access token. Update the code below: the update sets one." [Go to This server].
- Saved: toast "Agents can now look only." / "Agents can now look and answer." / "Agents are off."
- Copied: toast "Address copied."

**Mobile:** the segmented control is full width; the address wraps; the Copy button is 44 px.

### 2.7 Objects (`#objects`)

**Purpose:** start here for objects: the next step, progress, drives, and how far to trust the result.

**Layout and copy**
- Page head: title "Objects", sub "The site finds cars, bikes and people in your drives. You tell it which car the radar is measuring."
- **Card "Next step"** (one primary only):
  - no radar drive: "First you need a drive with the radar" [Go to Radar];
  - drive not looked at: "Find the cars in your drive of Thu 23 Oct" / "About 3 minutes on the graphics card." [Find them], link "Choose another drive";
  - job running: the live card "Find the cars · 40% · about 2 min left";
  - pictures waiting: large "48" / "pictures to check" / "About 4 minutes. Stop whenever you like: every answer is saved at once." [Start checking];
  - all done: "All checked ✓" / "Record another drive to get more."
- **Tiles 2×2:**
  - "112" / "confirmed by you"
  - "120" / "confirmed by an AI" / "check a few ›"
  - "23" / "none or can't tell" / "kept, never labels"
  - "0" / "ready as labels" / "after the far radar test" (once that test exists, the hint becomes "vehicles in your lane")
- **Card "How it works"**, three small illustrated steps:
  1. "The site draws a box around every car, bike and person." (sub: the detector and its licence from the run, e.g. "RT-DETR, a ready-made detector, unchanged (Apache-2.0)")
  2. "It guesses how far each one is, from the camera alone." (sub: "≈ an estimate, never a measurement")
  3. "You pick the car the radar is measuring: that pair teaches the camera real distances."

  Links: "Hide" and "Show the guide again". The card folds into one line after the first answer.
- **Card "Drives":** rows with "Thu 23 Oct · 19:42", "48 of 96 checked" and a `.bar`. A tap continues that drive. Rows not looked at yet show "Not looked at yet" [Find].
- **Card "Check the AI"** (when AI answers exist): "120 answers came from an AI. Check 20 of them, without seeing what it said, to know how far to trust it." [Check 20]. After a round: "You checked 20 AI answers: you agreed with 19."
- **Card "Camera vs radar"** (after 20 pairs confirmed by you, without estimates shown): "On the cars you confirmed, the camera's guess was 1.4 m from the radar (median) at 10–30 m, and 6.8 m at 30–60 m. A first look, not the experiment's result." Link "All the numbers in the Lab".
- Link [Every answer] (§2.13).

**States**
- Loading: tile skeletons and "Counting your pictures…".
- No drives folder: "No drives on this server yet. Choose the drives folder in Settings." [Open Settings].
- Car set as without radar: "Your car is set as having no radar: there is nothing to confirm against." [Open Settings].
- Job failed: a red job card "Failed after 1 min" [Details] [Try again].
- GPU busy: Find is disabled with the server's reason "the GPU is busy with another job".
- Error: a toast; the tiles keep their last values.

**Mobile:** Next step first, full width; tiles 2×2; drive rows 64 px.

### 2.8 Objects › Find the cars (wizard, `#objects/find`)

**Purpose:** start the GPU job `find_objects` in three one-question steps.

- **Step 1 of 3: "Which drive?"**
  - Sub: "Only drives with radar: the radar distance is what you'll confirm."
  - Cards in a vertical list: first "All new drives with radar (2)", then one card per drive, "Thu 23 Oct · 19:42" / "24 min · something ahead 61%".
  - A drive that already has answers shows the tag "Already done · 48 answers" and cannot be picked, with the reason "It has answers: finding again would need them all redone."
- **Step 2 of 3: "How many pictures?"**
  - Sub: "Only moments when the radar has a reading, at least a second apart, spread over every stretch of following a car."
  - `.choice-grid.three`: "Quick look" / "50 pictures · about 2 min to check" · "Standard" / "200 pictures · about 6 min" · "Every second" / "640 pictures · about 20 min".
  - The counts come from the drive's readings. The minutes use the owner's measured pace, or 1.8 s per picture before there is one.
- **Step 3 of 3: "Ready to start"**
  - Sub: "It runs in the background: you can close the page."
  - `.kv`:
    - "Drive": Thu 23 Oct · 19:42
    - "Pictures": about 200
    - "Finds the objects with": "RT-DETR, a ready-made detector, unchanged (Apache-2.0)"
    - "Guesses distance with": "Depth Anything V2 Small, unchanged (Apache-2.0); shown to you only after you answer"
    - "Leaves out": "half a second around each change of the radar's target, curves, moments without calibration"
    - "Runs on": "the graphics card (V100), alone; about 3 min"
  - Primary [Start]. The live card appears; when the job ends its button reads [Start checking].

**States:** disabled with the `.reason` from `status.actions.find_objects` (GPU busy, another job, no radar drive). Failed: red card, back to step 3 with [Try again].

**Mobile:** standard wizard; cards stacked; button pinned.

### 2.9 Objects › How to check (first time)

Full screen, shown once (localStorage); reopened from "Show the guide again". One card per screen; swipe or [Next].
1. **"The radar sees one vehicle"** / "The one straight ahead in your lane: a car, van, truck or bus. Never cars beside you or in the other lanes." Art: three cars, the middle one blue, the side ones grey and crossed out.
2. **"Tap the box of that vehicle"** / "The shaded band is your lane; the dashed line is where the radar distance falls on a flat road. The vehicle there is usually the one. The camera's guess appears only after you answer, so it can't sway you." Art: a finger on box A.
3. **"Not sure? Say so"** / "None of them: the radar sees something without a box, like a wall, a sign or a car hidden behind another. Can't tell: no label from this picture. A skipped picture is better than a wrong one." [Start checking]

On pointer devices a line is added: "Keys: A, B, C pick a box · N none · U can't tell · Enter confirm · Z undo."

### 2.10 Objects › Which car is the radar measuring? (`#objects/confirm?drive=…&queue=new`, focus mode)

**Purpose:** the core decision, one picture at a time, answered blind and saved at once.

**Layout and copy**
- Slim bar: ✕ · "12 of 48" with a thin bar · a clock icon (history sheet, §2.13) · "?" (pointer devices only).
- Title (22 px): "Which car is the radar measuring?"
- Radar line: a blue pill "Radar 18.4 m" with the chip `measured`; muted "reading 17.75 m · your speed 48 km/h". Below it, the calibration line when it applies.
- **Picture**, full width (`variant=plain` JPEG, overlay in SVG with `viewBox` set to the source pixels, so it scales with the image):
  - translucent band "your lane" (from the car's motion), from the bottom centre along the path;
  - dashed line across the band, captioned "≈ 18.4 m on a flat road";
  - vehicles (car, truck, bus, van): outlined boxes with letter chips A, B, C… left to right;
  - people, bikes and motorbikes: thin grey outlines with an icon, no letter, not selectable.
- **Hints** (only ones that use no depth; one line, `.warn-text`):
  - "The radar's reading jumped from 35 m to 20 m 0.4 s ago: it may have switched to another vehicle."
  - "No vehicle box in your lane: is the radar seeing something else?"
- **Choice cards**, two columns, 64 px; a tap on a box selects its card and the other way round:
  - "Car A" / "in your lane"
  - "Truck B" / "next to your lane"
  - "None of them" / "The radar sees something else"
  - "Can't tell" / "No label from this picture"
- After picking a vehicle, an optional toggle chip: "Its bottom is hidden".
- After "None of them", optional chips: "A wall or barrier" · "A sign or pole" · "A vehicle with no box" · "A hidden vehicle" · "A bike or motorbike" · "Something else".
- Secondary row: [◀ Before] [After ▶]. These show the moments 0.5 s and 1 s away (from `context`), each captioned with its reading ("20.25 m" or "no reading"), with a pill "showing −0.5 s" and the link "Back to this picture". The answer always applies to "now".
- Pinned primary [Confirm], disabled until a choice is made (hint "Pick a car, None of them or Can't tell").
- **After Confirm:** the next picture (prefetched) shows at once. A strip at the bottom stays 4 s, `aria-live`, with [Undo]:
  - "Saved · Car A · camera guessed ≈ 18 m · the rules picked A too"
  - "… · the rules picked B" / "… · the rules didn't choose"
  - "Saved · none of them · no label" / "Saved · can't tell"
  - "Saved · Car B is outside your lane, so it won't become a label."

**States**
- Picture loading: the old picture dimmed with "Loading the picture…"; choices and keys disabled until the shown image has loaded. The answer always carries the moment key of the image on screen.
- Picture missing: "This picture can't be shown." [Skip it] (nothing saved; it stays in the queue).
- 409 `revision_conflict`: "An AI answered this picture a moment ago, through the API: Car A." [Keep it] [Use mine] (sends again with the new `base_revision`; a human may replace an AI answer).
- 409 `stale_run`: "The cars of this drive were found again. Loading the new boxes…"
- Offline or a save error: red "Not saved: no connection. Try again."; the choice is kept, and the page retries with the same `base_revision`.
- Leaving with an unsaved answer asks: "One answer is not saved yet. Leave anyway?"
- Undo posts `withdraw` and reopens that picture first (up to 20 back).
- End of the queue: §2.12.

**Mobile:**
- Laid out for 390×844 with no scrolling: picture about 245 px, title, radar line, 2×2 choices, pinned button.
- Small far boxes get a hit area of at least 44×44 px, with the letter chip under the box.
- [Zoom] scales the picture 2× around the radar line with a CSS transform (no server crop). It is on by default when the radar is past 30 m.
- In landscape: picture 60% on the left, choices and button on the right. On desktop the keys of §1 work, and the existing `.player` grid puts the picture (3fr) beside the question (1fr).

### 2.11 Objects › Check the AI (`queue=check_ai`)

The same screen and the same blind question. The slim bar adds a pill "Checking an AI answer". The queue is a random sample of 20 AI-answered pictures. After Confirm, the strip says:
- "The AI (ai/Claude, through the API) said Car A: you agree."
- or "The AI said Car B. Your answer is the one that counts; the AI's stays in the history."

Empty: "No AI answers to check." [Back to Objects].

### 2.12 Objects › All done

- Check art. Title "All done for now"; sub "You answered 48 pictures: 41 cars, 5 none of them, 2 can't tell."
- When at least 20 pairs exist, a tile: "≈ 1.3 m" / "camera vs radar, median" / "on the cars you confirmed".
- Callout while the far test is missing: "These pairs become labels after one radar test farther away." [Start the test].
- Buttons: [Check another drive (2 left)], [Back to Objects].
- Leaving early with ✕ skips this screen; Objects shows what is left.

### 2.13 Every answer and the history sheet (`#objects/answers`)

- Head: back arrow; title "Every answer"; sub "Who answered, how, and what the radar and the camera said. Nothing is deleted: a change is a new line in the history."
- Filter chips (scroll sideways on a phone): All · By you · By an AI · None of them · Can't tell · a drive select.
- Rows (72 px): a crop of the chosen box (CSS background position on the plain JPEG), "Car A · radar 18.4 m · camera ≈ 18 m", "drive no. 301 · 12:31 · you, on this page · 23 Oct 21:10", and a pill (`you` / `AI` / "none of them" / "can't tell" / "not in your lane").
- Cursor paging with [Show more].
- **History sheet** (existing info sheet; also from the clock icon on §2.10):
  - title "Who answered", sub "Thu 23 Oct · 12:31 into the drive";
  - timeline, newest first:
    - "You · on this page · 23 Oct 19:40 — Car A"
    - "ai/Claude · through the API · 23 Oct 19:10 — Car A · camera guess not seen"
    - "You · on this page · 23 Oct 19:05 — answer taken back"
  - buttons [Change my answer] (opens §2.10 on this picture with the current answer pre-selected) and [Take it back] (danger; asks "Your answer is taken back; the history keeps it.").

**States:** empty "No answers yet." [Start checking]; the filter matches nothing: "No answers match this filter." [Show all]; loading: skeleton rows; error: a toast and [Try again].

### 2.14 Lab (`#experiments`, `#results/<id>`)

- Page head: title "Lab", sub "Experiments on your drives, and what they found."
- `.segmented` switch, full width: "Start one | Results (3)". The content is today's wizard and today's results, unchanged.
- Later: a result "Camera vs radar" with the error by distance band on ready labels confirmed by humans without seeing the estimates (`oem_radar` values only).

### 2.15 Info sheet texts ("?" buttons)

- **How to read the radar:**
  - "It follows one vehicle: the one ahead in your lane. Nothing beside you, nothing in other lanes."
  - "It gives one number: how far. No side position, no type of object."
  - "It counts in steps of 25 cm: 20.00, 20.25, 20.50 m."
  - "No reading when nothing is ahead, or when it is very close."
  - "The speed of the vehicle ahead has not been sent by your car so far, so the site doesn't show it."
  - "Nothing on this site ever drives the car."
- **What is the correction?** "The radar sits behind the bumper and reads a little long. Tests in front of a wall measured how much: about 0.9 m. The site takes it off every reading, then adds the 1.50 m from the radar to the comma."
- **Why only new drives?** "Your car sends the radar's reading only with the new software, installed on the evening of 23 October. Older drives, and drives where openpilot controls the speed, have no reading."
- **Why only the vehicle ahead?** "The radar gives no side position: a distance can only be paired with the vehicle straight ahead in your lane. Anything else would be a guess."
- **Your lane:** "The shaded band is where your car is heading, worked out from its motion: about one lane wide. It is drawn on a flat road, so on hills it is only a guide."
- **The four chips** (`measured`, `≈ camera`, `you`, `AI`): the sentences in §0.

---

## 3. REST API (`/api/v1`)

### 3.1 Base, authentication, protections

- Same service and port as the page: `http://<server>:8767/api/v1`. JSON only (except images), with `Cache-Control: no-store`. It is modelled on the speed-limit site's `/api/v1`.
- **Token:** `Authorization: Bearer <token>`, or `X-Perception-Token: <token>`.
  - The token is the site's access token, `PERCEPTION_WEB_TOKEN` (the service's env file and the clone's `.env`, mode 600), compared with `hmac.compare_digest`.
  - It is required even on 127.0.0.1.
  - `/api/v1` is dispatched **before** the session check: the page cookie is not accepted there, and the token is not accepted on page routes.
- If the server made up a random code at start (no `PERCEPTION_WEB_TOKEN`): `503 api_not_set_up`, "The API needs a fixed access token: update the site from Settings › This server." `tasks/update.py` writes one into the `.env` when missing. That is safe because, once the administrator exists, the setup code opens nothing.
- `?token=` anywhere in the URL: `400 token_in_url`.
- Missing or wrong token: `401 unauthorized` with `WWW-Authenticate: Bearer`. More than 10 failures a minute from one address: `429 too_many_attempts`.
- An `Origin` header other than the server's own, or `Sec-Fetch-Site: cross-site`: `403 cross_origin`. Terminal clients send no `Origin`. The existing loopback Host check stays (`403 host_not_allowed`).
- Settings › Agents: "Nothing" gives `403 api_off` on every call; "Look only" gives `403 api_read_only` on POSTs.
- POST: JSON only (415), `Content-Length` required (411), at most 64 KiB (413).
- Errors always have the form `{"ok": false, "error": "<code>", "message": "<one plain sentence>"}`.
- The server never writes the token in a message or a log, and logs no request URLs (as today).

### 3.2 Endpoints

| Method and path | What |
| --- | --- |
| `GET /api/v1` | version, MCP, access, calibration status |
| `GET /api/v1/schema` | answers, none reasons, filters, statuses, limits, errors, rules text |
| `GET /api/v1/calibration` | read-only calibration and tests (the API can never change the ruler) |
| `GET /api/v1/drives` | drives with radar, newest first by counter, with counts |
| `GET /api/v1/drives/{route}` | one drive, plus `first_to_confirm` |
| `GET /api/v1/drives/{route}/radar?from_s=&to_s=` | the radar track (from `drive_detail`) |
| `GET /api/v1/drives/{route}/moments?filter=&limit=&cursor=` | moments in order, paged |
| `GET /api/v1/drives/{route}/next?filter=&after=` | the next moment, full detail |
| `GET /api/v1/drives/{route}/moments/{seg}/{frame}?include_estimates=0&include_suggestion=0` | one moment |
| `GET …/moments/{seg}/{frame}/image?variant=plain\|boxes` | JPEG written by the job (`boxes` has lettered boxes, the lane and the radar line drawn) |
| `GET …/moments/{seg}/{frame}/context?before=2&after=2` | neighbours 0.5 s apart (0–6 per side) |
| `GET …/moments/{seg}/{frame}/history` | every revision |
| `POST …/moments/{seg}/{frame}/review` | save, change or take back an answer |
| `GET /api/v1/labels?basis=human\|any&ready=1&cursor=` | radar-associated pseudo-labels, for training and evaluation scripts |

`{route}` is the distill route (18 hex). `{seg}` is the segment number; `{frame}` is the index in that segment's road video (0–1199). The moment key is `"3/412"`.

### 3.3 Examples

`GET /api/v1`
```json
{"ok": true, "api": "perception-review", "version": 1, "mcp": false, "access": "on",
 "calibration": {"version": 3, "status": "close_only", "far_test_done": false}}
```

`GET /api/v1/schema` (shortened)
```json
{"ok": true,
 "answers": {"vehicle": "the radar measures this box (object_id of a selectable vehicle)",
             "none": "the radar measures something without a box", "unsure": "cannot tell: no label",
             "withdraw": "take back the latest answer"},
 "none_reasons": ["wall_or_barrier", "sign_or_pole", "vehicle_without_box", "hidden_vehicle", "bike_or_motorbike", "other"],
 "reviewer_kinds": ["human", "ai"],
 "filters": ["to_confirm", "answered", "by_human", "by_ai", "none", "unsure", "all"],
 "statuses": ["to_confirm", "confirmed", "none", "unsure"],
 "selectable_categories": ["car", "truck", "bus", "van"],
 "rules": ["Only the vehicle straight ahead in the ego lane.", "Never invent: answer unsure when in doubt.",
           "Ask for estimates only if you must: the review then records estimates_shown."],
 "limits": {"page": [1, 200], "context": [0, 6], "note_chars": 200},
 "errors": ["token_in_url", "unauthorized", "too_many_attempts", "cross_origin", "host_not_allowed", "api_off",
            "api_read_only", "api_not_set_up", "unknown_drive", "unknown_moment", "invalid_param", "missing_run_id",
            "stale_run", "base_revision_required", "revision_conflict", "human_review_exists", "invalid_answer",
            "unknown_object", "not_a_vehicle", "invalid_none_reason", "invalid_reviewer", "invalid_reviewer_kind"]}
```

`GET /api/v1/calibration`
```json
{"ok": true, "version": 3, "source": "tests", "status": "close_only", "far_test_done": false,
 "radar_to_camera_m": 1.50, "radar_correction_m": -0.89, "radar_to_reference_m": 0.61,
 "uncertainty_m": 0.09, "spread_m": 0.08, "reading_step_m": 0.25, "tested_m": [0.90, 1.32], "label_range_m": [5.0, 30.0],
 "tests": [{"id": "a1b2c3d4e5f6", "target": "wall", "recording_number": 305, "tape_radar_m": 1.32, "tape_comma_m": 2.82,
            "reading_m": 2.25, "correction_m": -0.93, "kept": true, "created": 1761237600.0}]}
```

`GET /api/v1/drives`
```json
{"ok": true, "drives": [
  {"route": "0000012d6552a45378", "number": 301, "start_unix": 1761241320, "time_source": "gps", "minutes": 24,
   "radar": {"share_with_reading": 0.61, "min_from_comma_m": 3.0, "max_from_comma_m": 58.2, "relative_speed_sent": false},
   "objects": {"run_id": "20261024T101500Z-ab12cd34", "moments": 96, "episodes": 14},
   "counts": {"to_confirm": 48, "confirmed": 40, "by_ai": 12, "none": 5, "unsure": 3}}]}
```

`GET /api/v1/drives/{route}/moments?filter=to_confirm&limit=50`
```json
{"ok": true, "total": 48, "moments": [
  {"moment_key": "3/412", "status": "to_confirm", "revision": 0, "radar_reading_m": 17.75, "objects": 3, "episode": 7}],
 "next_cursor": "eyJyIjoiMjAy..."}
```
The cursor is opaque and holds `run_id`, the filter and the last key. `next_cursor` is `null` on the last page. After a new run of the job, the cursor fails with `409 stale_run`.

`GET /api/v1/drives/{route}/next?filter=to_confirm&after=3/412` returns `{"ok": true, "moment": {…}, "wrapped": false}`, with `moment: null` when nothing is left. Without `after` it always restarts from the first moment still to confirm.

`GET /api/v1/drives/0000012d6552a45378/moments/3/412`
```json
{"ok": true, "moment": {
  "moment_key": "3/412", "route": "0000012d6552a45378", "drive_number": 301, "segment": 3, "frame": 412,
  "time_in_drive_s": 200.6, "camera": "fcamera", "width": 1928, "height": 1208,
  "run_id": "20261024T101500Z-ab12cd34", "status": "to_confirm", "revision": 0, "episode": 7,
  "radar": {"reading_m": 17.75, "step_m": 0.25, "skew_ms": 4.8, "relative_speed_mps": null, "provenance": "oem_radar",
            "from_comma_m": 18.36, "calibration": {"version": 3, "status": "close_only", "tested_m": [0.90, 1.32]}},
  "car": {"speed_mps": 13.3, "curvature_per_m": 0.0004},
  "recent_jump": null,
  "objects": [
    {"object_id": 0, "letter": "A", "category": "car", "score": 0.93, "box_xyxy": [902, 610, 1040, 712],
     "in_lane": true, "selectable": true, "provenance": "vision"},
    {"object_id": 1, "letter": "B", "category": "truck", "score": 0.88, "box_xyxy": [1180, 590, 1290, 668],
     "in_lane": false, "selectable": true, "provenance": "vision"},
    {"object_id": 2, "letter": null, "category": "person", "score": 0.71, "box_xyxy": [300, 600, 330, 680],
     "in_lane": false, "selectable": false, "provenance": "vision"}],
  "overlay": {"lane_source": "car_path", "lane_polygon_px": [[820, 1208], [1110, 1208], [985, 640], [940, 640]],
              "radar_line_px": {"y": 655, "x0": 905, "x1": 1030}, "approximate": true},
  "estimates": null, "suggestion": null, "review": null,
  "neighbours": {"previous": "3/392", "next": "3/432"},
  "image": {"plain_url": "/api/v1/drives/0000012d6552a45378/moments/3/412/image?variant=plain",
            "boxes_url": "/api/v1/drives/0000012d6552a45378/moments/3/412/image?variant=boxes"},
  "save": {"run_id": "20261024T101500Z-ab12cd34", "base_revision": 0}}}
```
- With `include_estimates=1`: `"estimates": [{"object_id": 0, "camera_x_m": 17.9, "provenance": "vision", "model": "depth-anything-v2-metric-vkitti-small@c725b85"}]`.
- With `include_suggestion=1`: `"suggestion": {"object_id": 0, "rule": "association.associate", "reason": "associated"}`, or `{"object_id": null, "reason": "two vehicles in the lane too close to tell apart"}`.
- Serving either is recorded per moment for 1 hour (§4.5). A review saved in that hour gets `estimates_shown` or `suggestion_shown` set to `true` by the server, whatever the client sends.

`GET …/context?before=2&after=2`
```json
{"ok": true, "frames": [{"frame_key": "3/392", "offset_s": -1.0, "radar_reading_m": 17.75, "reading": true,
  "image_url": "/api/v1/drives/0000012d6552a45378/moments/3/412/context/3/392.jpg"}]}
```
Context frames are not moments and cannot be reviewed.

`POST …/moments/3/412/review`
```json
{"run_id": "20261024T101500Z-ab12cd34", "base_revision": 0,
 "reviewer": "Claude", "reviewer_kind": "ai",
 "answer": "vehicle", "object_id": 0, "none_reason": null, "bottom_hidden": false,
 "note": "nearest car, centred in the lane", "override_human_review": false}
```
Response 200:
```json
{"ok": true, "status": "confirmed",
 "review": {"moment_key": "3/412", "revision": 1, "answer": "vehicle", "object_id": 0, "reviewer": "ai/Claude",
            "reviewer_kind": "ai", "channel": "rest", "estimates_shown": false, "suggestion_shown": false,
            "calibration_version": 3, "saved_at": "2026-10-24T10:20:31Z"},
 "label": {"kind": "radar_associated_pseudo_label", "ready": false, "why_not": "far_test_missing",
           "lead_surface_x_m": 18.36, "radar_reading_m": 17.75, "calibration_version": 3,
           "distance_provenance": "oem_radar", "box_provenance": "vision", "label_basis": "ai_review"},
 "revealed": {"suggestion": {"object_id": 0, "reason": "associated"},
              "estimates": [{"object_id": 0, "camera_x_m": 17.9}, {"object_id": 1, "camera_x_m": 41.2}]},
 "counts": {"to_confirm": 47, "confirmed": 41, "by_ai": 13},
 "next_moment_key": "3/432"}
```
- `label` is `null` for `none`, `unsure` and `withdraw`.
- `why_not` is one of `far_test_missing`, `calibration_typed`, `not_in_lane`, `not_a_vehicle`, `no_calibration`.

409 example (the current review is included, so a retry can see whether its own answer was already saved):
```json
{"ok": false, "error": "revision_conflict", "message": "This picture was answered meanwhile: read it again.",
 "current": {"revision": 1, "answer": "vehicle", "object_id": 0, "reviewer": "Cristian", "reviewer_kind": "human", "channel": "web"}}
```

`GET …/history`
```json
{"ok": true, "history": [
  {"revision": 2, "answer": "vehicle", "object_id": 0, "reviewer": "Cristian", "reviewer_kind": "human", "channel": "web",
   "queue": "check_ai", "estimates_shown": false, "suggestion_shown": false, "saved_at": "2026-10-24T19:40:02Z"},
  {"revision": 1, "answer": "vehicle", "object_id": 0, "reviewer": "ai/Claude", "reviewer_kind": "ai", "channel": "rest",
   "queue": "new", "estimates_shown": false, "suggestion_shown": false, "saved_at": "2026-10-24T10:20:31Z"}]}
```

`GET /api/v1/labels?basis=human&ready=1`
```json
{"ok": true, "labels": [
  {"kind": "radar_associated_pseudo_label", "route": "0000012d6552a45378", "moment_key": "3/412", "run_id": "20261024T101500Z-ab12cd34",
   "object_id": 0, "category": "car", "box_xyxy": [902, 610, 1040, 712], "bottom_hidden": false,
   "lead_surface_x_m": 18.36, "radar_reading_m": 17.75, "skew_ms": 4.8, "calibration_version": 3,
   "distance_provenance": "oem_radar", "camera_x_m": 17.9, "camera_provenance": "vision",
   "label_basis": "human_review", "reviewer_kind": "human", "channel": "web",
   "estimates_shown": false, "suggestion_shown": false, "episode": "0000012d6552a45378#7"}],
 "next_cursor": null, "counts": {"labels": 112, "episodes": 27, "drives": 4}}
```

### 3.4 Rules (one `ReviewService` shared by the page and the API)

- `run_id` is required (`400 missing_run_id`); a different one gives `409 stale_run`. `base_revision` is required (`428 base_revision_required`); a different one gives `409 revision_conflict`.
- `reviewer` is 1–100 printable characters. `reviewer_kind` is required on REST (`400 invalid_reviewer_kind`). AI names are stored as `ai/<name>`. A name starting with `ai/` sent with kind `human` gives `400 invalid_reviewer`. The page always saves `human` / `web` / the administrator's name and ignores any of these fields from the client.
- `vehicle` needs the `object_id` of a selectable vehicle of this moment (`400 unknown_object`, `400 not_a_vehicle`). `none` takes an optional `none_reason` from the list (`400 invalid_none_reason`). `unsure` takes neither. `withdraw` takes back the latest answer; the history keeps everything.
- An AI cannot change or take back a human answer: `409 human_review_exists`, unless `override_human_review: true`. That is only for when the owner asked for that picture, and it is recorded. A human may replace an AI answer.
- **An AI answer counts** (label basis `ai_review`), as on the speed-limit site.
- No locks: two reviewers on one moment find out at save time. Agents working in parallel should split by drive.
- Saving starts nothing: export and training stay separate, explicit steps.

### 3.5 Errors

| HTTP | Codes |
| --- | --- |
| 400 | `token_in_url`, `invalid_param`, `missing_run_id`, `invalid_answer`, `unknown_object`, `not_a_vehicle`, `invalid_none_reason`, `invalid_reviewer`, `invalid_reviewer_kind`, `invalid_json` |
| 401 | `unauthorized` |
| 403 | `cross_origin`, `host_not_allowed`, `api_off`, `api_read_only` |
| 404 | `unknown_drive`, `unknown_moment`, `no_picture` |
| 409 | `stale_run`, `revision_conflict` (with `current`), `human_review_exists` |
| 411 / 413 / 415 | `length_required` / `too_large` / `unsupported_media_type` |
| 428 | `base_revision_required` |
| 429 | `too_many_attempts` |
| 503 | `api_not_set_up` |

### 3.6 Page endpoints (session cookie, same origin, JSON; same service and database)

**Reused as they are:**
- `GET /api/status`, which gains `next` {case and its numbers} and `objects` {to_confirm, by_ai, …};
- `GET /api/radar`, `GET /api/radar/drives/{route}`, `GET /picture/radar/{segment}/{t_ms}.jpg`, `GET /api/comma/recordings`;
- `POST /api/radar/tests`, which gains an optional `"target": "wall"|"car"`;
- `POST /api/radar/tests/{id}` with `{"kept"}` or `{"delete"}`, and `POST /api/radar/calibration/apply` (it sets the source to "tests");
- `POST /api/settings/radar`, now in metres from the page; it sets the source to "typed";
- `POST /api/run/fetch_recordings`, `POST /api/run/radar_index`.

**New:**
- `POST /api/radar/drafts` with `{"target": "wall", "tape_radar_m": 1.40, "tape_comma_m": 2.90, "marker_counter": 307|null}` returns `{"draft": {…}}`. At most 5 drafts.
- `POST /api/radar/drafts/{id}` with `{"delete": true}`.
- `GET /api/objects` (summary for the tab), `GET /api/objects/next?drive=&queue=new|check_ai&after=`.
- `GET /api/objects/moments/{route}/{seg}/{frame}`: never returns estimates or the suggestion. Also `/image?variant=plain`, `/context`, `/history`.
- `POST /api/objects/moments/{route}/{seg}/{frame}/review` with `{"run_id", "base_revision", "answer", "object_id", "none_reason", "bottom_hidden", "queue", "decision_ms"}`. Its response carries `revealed`.
- `GET /api/objects/answers?filter=&drive=&cursor=`.
- `POST /api/run/find_objects` with `{"drives": ["0000012d6552a45378"] | "new", "size": "quick"|"standard"|"all"}`.
- `POST /api/settings/agents` with `{"access": "on"|"read_only"|"off"}`.

**Later:** MCP over the same service, with the optional SDK as on the speed-limit site. Tools: `list_drives`, `list_moments`, `next_moment`, `get_moment`, `get_moment_image`, `get_moment_context`, `get_review_history`, `save_review`, always saved as `ai`. `GET /api/v1` will then say `"mcp": true`.

---

## 4. Data model

### 4.1 Files (all under `outputs/`, git-ignored, mode 600)

| Path | Status | Content |
| --- | --- | --- |
| `outputs/radar/<segment>.json` | existing | radar series per segment (`perception.radar.series`) |
| `outputs/recordings/<route>--<seg>/` | existing | rlog.zst plus qcamera.ts copied from the comma |
| `outputs/web/pictures/<segment>-<frame>.jpg` | existing | picture cache (`web/pictures.py`); the find job warms it for its moments and their neighbours |
| `outputs/web/settings.json` | extended | plus `radar_source`, `radar_calibration_version`, `api_access` |
| `outputs/web/radar_tests.json` | existing | tests; plus optional `target` |
| `outputs/web/radar_drafts.json` | new | tape-only tests waiting for a recording |
| `outputs/web/radar_calibrations.json` | new, append-only | every calibration version |
| `outputs/objects/<run_id>/run.json`, `moments.json`, `pictures/` | new, written by the job | moments, boxes and estimates (read-only for the site) |
| `outputs/web/perception.sqlite` | new | reviews (WAL, `schema_version` in `meta`) |

### 4.2 Calibration

- `settings.json`:
  - `radar_to_camera_m` and `radar_correction_m` (existing, metres);
  - `radar_source`: `"tests"` or `"typed"`;
  - `radar_calibration_version`: an integer, +1 on every change.
- When the source is `tests`, adding, leaving out or deleting a test re-applies the means at once (in `App.add_radar_test` / `change_radar_test`).
- `radar_calibrations.json`: `[{version, at, radar_to_camera_m, radar_correction_m, source, test_ids}]`.
- `radar_drafts.json`: `[{id, created, target, tape_radar_m, tape_comma_m, marker_counter}]`. Drafts are deleted when completed or deleted from the page.
- **Status**, added to `RadarTests.summary()`:
  - `not_set`: no `radar_to_camera_m`;
  - `typed`: source typed;
  - `ready`: source tests, at least one kept test with `tape_radar_m` of 5 m or more, and the spread of kept corrections at most 0.5 m;
  - `close_only`: otherwise;
  - plus the flag `tests_disagree` when the spread is over 0.5 m.

  `far_test_done` means `ready`. The uncertainty shown is the server's `uncertainty_m`.

### 4.3 Runs and moments (files written by the `find_objects` job)

`run.json`:
```json
{"run_id": "20261024T101500Z-ab12cd34", "created": 1761300900, "code_version": "abc1234",
 "route": "0000012d6552a45378", "calibration_version": 3,
 "detector": {"name": "RT-DETR R50vd (COCO)", "source": "<repo or hub id>", "revision": "<pinned>", "sha256": "…", "licence": "Apache-2.0"},
 "depth_model": {"name": "Depth Anything V2 Metric VKITTI Small", "revision": "c725b858…", "sha256": "9203e5…", "licence": "Apache-2.0"},
 "sampling": {"min_gap_s": 1.0, "skip_around_jump_s": 0.5, "jump_m": 3.0, "max_frame_skew_ms": 25, "size": "standard"},
 "lane_source": "car_path", "association": {"version": 1, "config": {"gate_rel": 0.25, "...": "..."}},
 "counts": {"moments": 96, "episodes": 14}}
```

`moments.json`, one entry per moment:
- `moment_key`, `segment`, `frame`, `frame_t_ns`;
- `radar` {`reading_m`, `t_ns`, `skew_ms`, `relative_speed_mps`|null};
- `car` {`speed_mps`, `curvature_per_m`, `calibrated`};
- `episode` and `recent_jump` {`from_m`, `to_m`, `seconds_ago`}|null;
- `overlay` {`lane_polygon_px`, `radar_line_px`};
- `objects` [{`object_id`, `letter`, `category`, `score`, `box_xyxy`, `in_lane`, `selectable`, `camera_x_m`, `camera_model`}];
- `suggestion` {`object_id`|null, `reason`};
- `images` {`plain`, `boxes` sha256}.

How moments are chosen:
- only a valid reading within 25 ms of the frame, and a calibrated frame;
- at least 1 s apart, spread over episodes. An episode is a stretch of readings with no gap over 0.5 s and no jump over 3 m;
- none within 0.5 s of a jump;
- none where the road ahead curves away (the association's rule).

How the values are made:
- `in_lane`: the bottom centre of the box lies in `lane_polygon_px`, the car path ±1.5 m projected on a flat road. That is picture-only and uses no depth.
- `radar_line_px`: the image row of the radar distance from the comma on a flat road.
- `camera_x_m`: the median Depth Anything distance over the lower half of the box.
- `suggestion`: the existing `association.associate()`. It uses vision positions, which is the C1 bias, so it is shown only after the answer.

The current run of a drive is its newest finished run. A drive with answers is not run again in v1.

### 4.4 Reviews (SQLite, append-only)

```
reviews(id INTEGER PK, route TEXT, run_id TEXT, moment_key TEXT, revision INT,
        answer TEXT CHECK(answer IN ('vehicle','none','unsure','withdraw')),
        object_id INT NULL, none_reason TEXT NULL, bottom_hidden INT, note TEXT,
        reviewer TEXT, reviewer_kind TEXT CHECK IN ('human','ai'), channel TEXT CHECK IN ('web','rest','mcp'),
        queue TEXT CHECK IN ('new','check_ai'), estimates_shown INT, suggestion_shown INT,
        override_human_review INT, calibration_version INT, decision_ms INT NULL, saved_at TEXT,
        UNIQUE(route, run_id, moment_key, revision))
served(route, run_id, moment_key, what TEXT CHECK IN ('estimates','suggestion'), at REAL)   -- API only, kept 1 h
meta(key PRIMARY KEY, value)   -- schema_version
```

- Writes are serialised behind one lock in `ReviewService`, short, in one transaction, in WAL mode.
- **Current answer** = the highest revision. A `withdraw` revision makes the status `to_confirm` again.
- **Status:**
  - `confirmed`: current answer `vehicle`;
  - `none`, `unsure`: current answer of that kind;
  - `to_confirm`: no answer, or taken back.
- **By whom:** `by_human` or `by_ai` comes from the `reviewer_kind` of the current revision. A human revision in the `check_ai` queue records agreement with the AI revision before it, for the "you agreed with 19 of 20" line.

### 4.5 Provenance of every value

| Value | Provenance | Shown before the answer |
| --- | --- | --- |
| radar reading, distance from the comma | `oem_radar` (measured), plus `calibration_version` | yes |
| box, category, `in_lane`, lane band, radar line | `vision` / picture geometry (no depth) | yes |
| camera distance per box | `vision` (estimated, `camera_model`) | no (page); API only on request, then recorded |
| rule suggestion | association rule (uses vision depth) | no (page); API only on request, then recorded |
| identity of the radar target | `human_review` or `ai_review`, with `channel` | (this is the answer) |
| pair | `fused`: radar distance plus vision box | never stored as a typed value |

### 4.6 Labels (a view, computed at export, never typed in)

A label exists for every moment whose current answer is `vehicle` on a selectable vehicle. Its fields:
- `lead_surface_x_m` = reading + correction + radar→comma, at the **current** calibration (the raw reading is kept, so a new calibration never invalidates an answer);
- `distance_provenance: oem_radar`, `box_provenance: vision`, `label_basis`, `estimates_shown`, `suggestion_shown`, `episode`.

Gates:
- **Ready:** `in_lane` is true and the calibration status is `ready`.
- **Training:** ready labels, human or AI, AI marked as such.
- **Evaluation and headline metrics:** ready labels with basis `human_review`, `estimates_shown` false and `suggestion_shown` false, counted as labels / episodes / drives.

---

## 5. Build order (smallest useful slice first)

1. **See the radar** (day 1; only the existing radar API):
   - bar labels Home · Radar · Objects · Lab (Objects shows an empty state; Lab is the segmented switch over the existing panels);
   - the Radar tab (§2.2) and the drive and recording view (§2.3): chart, picture, facts;
   - drive order by counter, the 4th Home tile, the `fmtM` and provenance helpers, the Next step card with the radar cases.

   This answers "how do I see the radar" on its own.
2. **Enter and test the calibration** (day 2):
   - the Settings card (§2.5), in metres, with the tests list, coverage strip and status;
   - `radar_source` and automatic re-apply, `radar_calibration_version` and its history;
   - drafts; the tape-test wizard (§2.4) with `fetch_recordings`;
   - tests for the status rules and the draft endpoints.
3. **Find the cars** (day 3):
   - the `find_objects` action (GPU, `flock` on the GPU lock);
   - the detector pinned, with an entry in THIRD_PARTY.md first;
   - depth per box, the picture-only lane band and radar line, `associate()` as the hidden suggestion;
   - `run.json`, `moments.json`, the JPEGs, and warming the picture cache;
   - the Find wizard (§2.8).
4. **Confirm on the page** (day 4):
   - `ReviewService` and SQLite;
   - the Objects tab (§2.7), the guide (§2.9), the confirm screen (§2.10) with prefetch, keys and undo, All done (§2.12), the history sheet;
   - page endpoints; tests (revisions, withdraw, `stale_run`, nothing revealed before the answer).
5. **API for agents** (day 5):
   - `/api/v1` (§3) dispatched before the session check;
   - the token rules, plus the token written by `tasks/update.py`;
   - Settings › Agents (§2.6), `served` and `estimates_shown`, `/labels`, docs/API.md (English, the same structure as the sister site's guide);
   - tests: `token_in_url`, 401, cookie refused, ai/human rules, `human_review_exists`, 409 bodies, read-only and off.
6. **Later:**
   - Every answer list (§2.13 beyond the history sheet), Check the AI (§2.11), the Camera vs radar card;
   - MCP;
   - naming why a drive has no radar from the log (carParams `openpilotLongitudinalControl`, software);
   - lane lines from modelV2 (the C1 plan);
   - `crop` / `long_side` for images;
   - carrying answers over after re-running the detector.

---

## 6. Risks and open questions

1. **Pairing bias (C1).** The rule suggestion and the camera estimates are hidden until the answer is saved, and the API records when it served them. The lane band and the radar line still assume a flat road and the current calibration, which is checked only at 0.9–1.3 m. Both are captioned "≈" and "approximate". `association.py` needs a picture-only lane variant for `in_lane` (today it uses vision positions).
2. **Far test may show nothing.** ACC radars often ignore a car that stands still. A parked-car test can come back with no steady reading. The wizard says so ("Some radars ignore cars that stand still: try 5–10 m"). If it never works, a moving test with ground marks is needed; it is not designed here. Until then no pair is "ready".
3. **Calibration that depends on distance.** The correction may grow with distance. In v1, tests that disagree by more than 0.5 m block "ready", and the owner can leave the close tests out. A per-distance model is out of scope.
4. **Uncertainty copy.** Docs say ±0.13 m (half a step, one test); the code gives `0.125/√n` (0.09 m for 2 tests). The page shows the code's value per test set and "about ±0.13 m" per single test. Align docs/review-decisions.md.
5. **Radar timing (C2).** The radar's latency is unknown. A 25 ms skew is accepted, moments around jumps are left out, and the skew is shown in the API. At 10 m/s of closing speed, 100 ms is 1 m. These are pseudo-labels, not ground truth.
6. **Old drives look like "nothing ahead".** An old rlog read with the new schema is "never valid". v1 uses the commit heuristic and never claims a reason it can't know. Reading the software version and carParams from the log is a later step.
7. **Comma.** It may be unreachable from the car park: drafts and the list of recordings by counter cover that, but a wrong pick gives a wrong correction, which is why the outlier warning exists. Nothing is ever started or written on the comma. The route counter is assumed monotonic; a reflash may reset it (open: what to show then).
8. **AI answers.** They count like on the speed-limit site. An agent can be confidently wrong, so: the Check the AI rounds with agreement shown, evaluation human-only, and `ai/<name>` in the history. An agent could claim `human` through REST; the channel `rest` is always recorded and shown in Every answer.
9. **Token.** One long-lived shared secret, which is also the setup code. It must be written by install and update, and rotating it needs a restart, so there is no button in v1. Remote agents need the owner to copy it on the server; the page never shows it. Open: a separate API key shown once would break "no secrets on the page".
10. **Detector licence.** "YOLO" ("You Only Look Once") is a family of fast detectors:
    - Ultralytics YOLOv8/11 (and YOLOv10 built on them) are AGPL-3.0; the Ultralytics `RTDETR` class too;
    - YOLOv7/v9 are GPL-3.0; YOLO-NAS weights are non-commercial.

    None of them fits an MIT repository. Pick RT-DETR (Apache-2.0) from its original code or the transformers port; D-FINE (Apache-2.0) is the fallback. Code, weights (pinned, with SHA-256) and the COCO terms go in THIRD_PARTY.md (and docs/licenses.md when it exists). Open: adding `transformers` as a dependency, or fetching the original code at a pinned commit like Depth Anything. COCO has no "van"; people and bikes never get radar labels.
11. **Re-running the detector** changes the boxes. v1 forbids it on drives that have answers. Carrying answers over by box overlap is later, and if done it must be recorded as carried.
12. **Cost.** On-demand ffmpeg decodes up to the frame wanted, a few seconds at the end of a minute. The Radar view debounces scrubbing and keeps the old picture. The find job warms the cache for moments and neighbours. GPU decoding for the job needs NVDEC or CPU ffmpeg (open: which, on the V100 server).
13. **Phone details.** Boxes are in source pixels (1928×1208) and drawn in an SVG with that `viewBox`, so rotation re-places them for free. Far boxes need the larger hit areas and Zoom. Wake lock is not available everywhere, so the countdown runs from its start time.
14. **Navigation change.** Only labels change and Lab spans two panels. Check on a real 360 px phone that "Objects" with its badge stays fully visible and that the pinned Confirm bar doesn't collide with the home indicator.
15. **Privacy.** Frames show other cars, people and number plates. They stay under `outputs/` and go only to the signed-in administrator or a token holder: never into git, CI or the public repository.
16. **Scope left out on purpose:** drawing missing boxes, tracking across frames, batch confirm, leases and claims, live radar, MCP (later), and a "make the pictures" job.
