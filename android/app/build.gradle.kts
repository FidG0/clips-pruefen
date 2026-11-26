plugins {
    id("com.android.application")
}

android {
    namespace = "de.clipwreck.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "de.clipwreck.app"
        minSdk = 26
        targetSdk = 35
        // In CI wird versionCode aus der Laufnummer gesetzt, damit jede neue APK das alte installiert.
        versionCode = (System.getenv("VERSION_CODE") ?: "1").toInt()
        versionName = System.getenv("VERSION_NAME") ?: "1.0.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // Unsigniert bauen. Signiert wird lokal auf Marvins PC, der Schlüssel kommt nie nach GitHub.
            signingConfig = null
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
