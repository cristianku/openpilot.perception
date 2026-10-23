// The toolchain of openpilot.distill's app (and nav2pilot's), pinned: JDK 17, Android SDK platform 36,
// Gradle 8.14.3 (helpers/build_android.sh installs them). Upgrading one means upgrading them together.
plugins {
    id("com.android.application") version "8.13.2" apply false
    id("org.jetbrains.kotlin.android") version "2.2.21" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.2.21" apply false
    // Gradle Play Publisher, the version of openpilot.distill's app: uploads the bundle (and the store listing)
    // to Google Play from helpers/build_android.sh.
    id("com.github.triplet.play") version "3.12.1" apply false
}
