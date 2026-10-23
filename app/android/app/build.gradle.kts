import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

// Release signing comes from the environment: helpers/build_android.sh passes the key of the server
// (~/.config/distill-android, shared with openpilot.distill's app; never in git).
val releaseKeystore: String? = System.getenv("PERCEPTION_KEYSTORE")?.takeIf { it.isNotBlank() }

// The comma side of the radar screen is app/comma_listener.py of this repository: the build copies it into the
// assets of the app, so there is one listener only, never a second copy in the source tree of the app.
val listenerScript: File = rootProject.file("../comma_listener.py")

abstract class BundleCommaListener : DefaultTask() {
    @get:InputFile
    @get:PathSensitive(PathSensitivity.NONE)
    abstract val script: RegularFileProperty

    @get:OutputDirectory
    abstract val outputDir: DirectoryProperty

    @TaskAction
    fun copy() {
        val source = script.get().asFile
        check(source.isFile && source.length() > 0) { "app/comma_listener.py is missing: the app cannot read the radar without it" }
        val folder = outputDir.get().asFile
        folder.deleteRecursively()
        folder.mkdirs()
        source.copyTo(File(folder, "comma_listener.py"))
    }
}

val bundleCommaListener = tasks.register<BundleCommaListener>("bundleCommaListener") {
    script.set(listenerScript)
    outputDir.set(layout.buildDirectory.dir("generated/commaListener"))
}

android {
    namespace = "ch.bodhi.perception"
    compileSdk = 36

    defaultConfig {
        applicationId = "ch.bodhi.perception"
        minSdk = 26
        targetSdk = 36
        // Set by helpers/build_android.sh from the commits of the repository: -PperceptionVersionCode=… -PperceptionVersionName=…
        versionCode = (findProperty("perceptionVersionCode") as String?)?.toInt() ?: 1
        versionName = (findProperty("perceptionVersionName") as String?) ?: "0.1.0-dev"
    }

    signingConfigs {
        create("release") {
            if (releaseKeystore != null) {
                storeFile = file(releaseKeystore)
                storePassword = System.getenv("PERCEPTION_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("PERCEPTION_KEY_ALIAS")
                keyPassword = System.getenv("PERCEPTION_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.getByName("release")
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures {
        compose = true
    }
    packaging {
        resources {
            // Signatures and build notes of the jars of the libraries: nothing the app reads.
            excludes += setOf("META-INF/versions/9/OSGI-INF/MANIFEST.MF", "META-INF/DEPENDENCIES",
                "META-INF/LICENSE.md", "META-INF/NOTICE.md", "META-INF/INDEX.LIST")
        }
    }
    testOptions {
        unitTests.all {
            // The listener contract test reads the real script; CommaSshIT reads its target from the environment.
            it.systemProperty("perception.listener", listenerScript.absolutePath)
        }
    }
    lint {
        // The toolchain is pinned on purpose (build.gradle.kts); these only report newer versions.
        disable += "OldTargetApi"
        disable += "GradleDependency"
        disable += "AndroidGradlePluginVersion"
        disable += "NewerVersionAvailable"
    }
}

androidComponents {
    onVariants { variant ->
        variant.sources.assets?.addGeneratedSourceDirectory(bundleCommaListener, BundleCommaListener::outputDir)
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

// A release is never signed with a debug key or left unsigned: a phone that has the app would refuse the update.
tasks.matching { it.name == "preReleaseBuild" }.configureEach {
    doFirst {
        check(releaseKeystore != null) {
            "Release signing is missing: build with helpers/build_android.sh, which passes the key of the server " +
                "(PERCEPTION_KEYSTORE, PERCEPTION_KEYSTORE_PASSWORD, PERCEPTION_KEY_ALIAS, PERCEPTION_KEY_PASSWORD)"
        }
    }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2025.12.01"))
    implementation("androidx.activity:activity-compose:1.11.0")
    implementation("androidx.core:core-ktx:1.16.0")
    // Starts and stops the comma listener with the screen (LifecycleStartEffect).
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.9.4")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.ui:ui-tooling-preview")
    debugImplementation("androidx.compose.ui:ui-tooling")
    // SSH to the comma (Apache-2.0) and the cryptography it uses: Ed25519, X25519, ChaCha20-Poly1305 (MIT-style
    // Bouncy Castle licence). The version of Bouncy Castle is the one sshj declares.
    implementation("com.hierynomus:sshj:0.41.2")
    implementation("org.bouncycastle:bcprov-jdk18on:1.84")
    testImplementation("junit:junit:4.13.2")
    // The real org.json: the one of android.jar is a stub in JVM unit tests.
    testImplementation("org.json:json:20240303")
}
