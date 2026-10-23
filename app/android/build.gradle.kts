// The toolchain of openpilot.distill's app (and nav2pilot's), pinned: JDK 17, Android SDK platform 36,
// Gradle 8.14.3 (helpers/build_android.sh installs them). Upgrading one means upgrading them together.
plugins {
    id("com.android.application") version "8.13.2" apply false
    id("org.jetbrains.kotlin.android") version "2.2.21" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.2.21" apply false
}
