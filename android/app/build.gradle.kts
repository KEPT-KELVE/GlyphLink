plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.adam.glyphlink"
    compileSdk = 35

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }

    buildFeatures {
        aidl = true
    }

    defaultConfig {
        applicationId = "com.adam.glyphlink"
        minSdk = 31
        targetSdk = 35
        versionCode = 134
        versionName = "1.3.4"
    }

    buildTypes {
        getByName("release") {
            isMinifyEnabled = false
            // Sign official release APKs with the separately held release key.
        }
    }
}

dependencies {

}
