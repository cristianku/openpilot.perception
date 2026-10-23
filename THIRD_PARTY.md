# Third-party code, data and models

The MIT license of this repository covers only its original code. Everything below keeps its own
license; check it again before adding or redistributing anything.

## Used at run time (not vendored)

| Component | Used for | License |
| --- | --- | --- |
| [numpy](https://numpy.org) | arrays | BSD-3-Clause |
| [PyTorch](https://pytorch.org) | depth loss | BSD-3-Clause |
| [openpilot](https://github.com/commaai/openpilot) (`tools.lib.logreader`, `common.transformations`) | reading comma `rlog.zst`, rotations | MIT |
| [opendbc](https://github.com/commaai/opendbc) `car.capnp` (through openpilot's cereal) | the `carState` schema | MIT |
| [OpenCV](https://opencv.org) (optional) | drawing the objects on a frame; the geometry audit (SIFT, essential matrix, RANSAC) | Apache-2.0 (4.5 and later) |
| [FFmpeg](https://ffmpeg.org) (the `ffmpeg` program, not linked) | decoding the HEVC frames for the geometry audit | LGPL/GPL, by build; called as a separate program |
| [safetensors](https://github.com/huggingface/safetensors) (optional) | reading openpilot.distill's `localizer.safetensors` | Apache-2.0 |
| [pycapnp](https://github.com/capnproto/pycapnp) (tests) | the synthetic `carState` versions | BSD-2-Clause |

## Fetched at pinned versions (not vendored)

| Component | Used for | License | Pin |
| --- | --- | --- | --- |
| [Depth Anything V2](https://github.com/DepthAnything/Depth-Anything-V2) `metric_depth` code | the frozen monocular baseline (`perception/models/depth_anything.py`) | Apache-2.0 | commit `a561b849ebae10a6f5ef49e26c83cbbcd36c71bf` |
| [Depth Anything V2 Metric VKITTI Small](https://huggingface.co/depth-anything/Depth-Anything-V2-Metric-VKITTI-Small) weights | same | Apache-2.0 (Small only: Base/Large/Giant are CC-BY-NC-4.0 for the project) | revision `c725b8589bdf6ab04072cab74c0467830db80d6d`, SHA-256 `9203e538d35255c90dda4b7fedb47ff33fe725497bcca3b1e53b3a65ee63f0cb` |

Both are downloaded on first use into `~/.cache/openpilot-perception`. The weights were trained on Virtual
KITTI 2, whose scenes are cloned from KITTI tracking sequences (KITTI is CC-BY-NC-SA 3.0): a KITTI-based
benchmark is not independent of this checkpoint.

## Android app (`app/android`)

Perception for Android (package `ch.bodhi.perception`, on Google Play as "Openpilot Perception") is original
code of this repository, under its MIT license, like `app/comma_listener.py`, which the build copies into the
app. It is built on the server only (`helpers/build_android.sh`), and its APK and AAB bundle these libraries:

| Component | Used for | License |
| --- | --- | --- |
| [sshj](https://github.com/hierynomus/sshj) 0.41.2, asn-one 0.6.0 (com.hierynomus) | SSH to the comma | Apache-2.0 (sshj's NOTICE ships with the app) |
| [Bouncy Castle](https://www.bouncycastle.org) 1.84: bcprov, bcpkix, bcutil (jdk18on) | Ed25519, X25519, ChaCha20-Poly1305 for sshj | Bouncy Castle Licence (MIT style) |
| [SLF4J](https://www.slf4j.org) API 2.0.17 | sshj's logging interface (no logger is bundled: the app logs nothing) | MIT |
| AndroidX, Jetpack Compose, Material 3 | the user interface | Apache-2.0 |
| Guava ListenableFuture 1.0 | an AndroidX dependency | Apache-2.0 |
| Kotlin standard library, kotlinx.coroutines | the language and its coroutines | Apache-2.0 |
| JetBrains annotations 23.0.0, JSpecify 1.0.0 | nullness annotations of the libraries | Apache-2.0 |

The texts these licences ask to ship (the Bouncy Castle and SLF4J notices, sshj's NOTICE with the JCraft and
Bouncy Castle notices it carries, the Apache License 2.0) are in the app,
[`app/android/app/src/main/assets/third_party_licences.txt`](app/android/app/src/main/assets/third_party_licences.txt),
shown in Settings › Licences. Keep that file and this table in step when a library changes.

Used to build, test and publish it, not in the app (fetched at pinned versions by `helpers/build_android.sh` and
Gradle's dependency verification, `app/android/gradle/verification-metadata.xml`):

| Component | Used for | License |
| --- | --- | --- |
| [Gradle](https://gradle.org) 8.14.3, Android Gradle Plugin 8.13.2, Kotlin Gradle plugin 2.2.21 | the build | Apache-2.0 |
| [Gradle Play Publisher](https://github.com/Triple-T/gradle-play-publisher) 3.12.1 (`com.github.triplet.play`) and the Google API client libraries it uses | the upload to Google Play | MIT; Apache-2.0 |
| [Eclipse Temurin](https://adoptium.net) JDK 17.0.21+9 | runs the build | GPL-2.0 with the Classpath Exception |
| Android SDK platform 36, build-tools 35 (Google) | compiling, signing (`apksigner`) | Android Software Development Kit License (accepted by `sdkmanager --licenses`) |
| JUnit 4.13.2, [org.json](https://github.com/stleary/JSON-java) 20240303 | the unit tests | EPL-1.0; public domain |

## Excluded

ManyDepth and Monodepth2 (Niantic): non-commercial licenses, "Patent Pending". No code or weights from them.
An independent implementation of a published method is not, by itself, free of patents.

## Models and weights

Only the Depth Anything V2 Small checkpoint above. Any pretrained model added later (backbone, depth or detection network) must be listed
here with its license and source before use; weights with a non-commercial or unclear license
must not be published from this repository.

## Data

No recording is stored in this repository. The tests build synthetic messages only.
