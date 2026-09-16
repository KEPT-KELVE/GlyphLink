# GlyphLink Setup 1.3.6

Open **GlyphLinkSetup-1.3.6.exe**. The first screen explains Developer options, USB debugging and the phone authorization prompt. Setup checks the phone automatically and enables **Install GlyphLink** only after the checks pass. Click it and keep the Nothing Phone (1) connected and unlocked. Both apps and ADB are included; setup does not download or compile anything, or ask for APK/build locations.

This companion preserves the existing project's Evolution X Glyph adapter integration. It requires the `com.nothing.thirdparty` adapter on the Nothing Phone (1), Android 12 or later, and 64-bit Windows 10/11. USB debugging must remain available for the desktop connection.

Setup installs a complete desktop runtime in `%LOCALAPPDATA%\Programs\GlyphLink\versions`, installs the signed Android package, opens it, requires a PING/PONG response through ADB, launches the Windows app, and creates Desktop/Start-menu shortcuts and a Windows startup entry. Existing patterns remain in `%APPDATA%\GlyphLink`. No automatic app uninstall or signing-key override is performed.

## New in setup 1.3.6

The opening screen shows a live checklist for USB connection, debugging authorization, the supported phone and Android version, the required Glyph adapter, and three USB response checks. Android must finish booting before Install becomes available.

Clicking Install triggers a fresh check of the same phone before copying the Windows app or installing the Android companion. Disconnecting or changing devices stops installation and returns to the checks. One worker owns checks and installation, so repeated clicks cannot start duplicate installs.

The progress screen explains the current step, marks completed steps and shows what comes next. The main error message stays concise; View details contains the complete log and a Copy details button. Retry requires a ready phone. The content scrolls while the action buttons remain visible.

Opening setup prepares verified USB tools in a support cache; neither app is installed at that point. The preinstallation checks confirm USB communication and required services, not physical LEDs. The companion connection is tested after installation.

## Preserved fix from setup 1.3.5

Opening setup 1.3.4 could create Python bytecode cache files inside the bundled runtime. Its strict file-list comparison then rejected its own runtime. This was reproduced with an actual Python import from the extracted 1.3.4 EXE.

Both launchers now pass Python's `-B` option. The validator tolerates only Python cache files whose source belongs to the release, still requires every manifested file and its original hash, and lists missing or unexpected files in detailed errors. Generated caches are not copied to the installed app, and the packaging gate refuses a payload containing generated caches.

The included Android companion remains version 1.3.4 (version code 134), with the identical APK bytes and signing key. This is an installer correction.

## What was verified

- The Android APK was compiled from the included Kotlin/AIDL source with Android SDK 35, JDK 17 and Kotlin 2.0.20. `apksigner verify` succeeded; application ID `com.adam.glyphlink`, version code 134, version 1.3.4, minimum API 31.
- Thirty-three automated installer/controller tests passed. They cover payload integrity and generated caches, USB-only preparation, readiness states, disconnect/device substitution before copying apps, duplicate clicks, retry, Android failures and companion responses.
- Native Linux Tk UI checks exercise Install readiness, disconnect, progress, concise errors, retry, completion and visible action buttons at the minimum window size, using simulated phone events. This is not a Windows execution test.
- The included Windows Python and extensions are x64. The required non-system runtime DLLs are included.
- The NSIS installer compiled successfully. Its extracted payload is compared with the release manifest before delivery.

A real Windows installation, Android USB installation, Windows audio capture and physical Glyph LEDs could not be tested in this environment. The EXE is not Authenticode-signed.

## Source and building

The downloadable EXE is the finished installer. This source tree is for maintenance.

`installer/setup.nsi` packages the UI, signed APK, ADB, and complete Windows runtime. `installer/launcher.nsi` builds the desktop launcher. Run `scripts/prepare_manifest.py` before compiling setup; it refuses a release missing any required payload file. Build with NSIS 3.09 or later from the installer directory.

The desktop payload uses official CPython 3.12.10 x64 files extracted from Python.org's `core.msi`, `exe.msi`, `lib.msi` and `tcltk.msi`, plus Windows wheels listed in `release/dependencies.txt`. All dependencies must be included before release. Python is not installed globally on the user's PC.

`scripts/build_android_offline.py` builds the APK using existing local SDK/JDK/compiler tools. Set `ANDROID_HOME`, `JAVA_HOME` and `KOTLIN_LIB_DIR`. This release used Android build-tools 35.0.0 and the Kotlin 2.0.20 compiler jars supplied with Gradle 8.11.1. Keep the original private signing files for future compatible Android updates; they are stored separately from the source/release.

Tests: `python -m unittest discover -s tests -v`

## References

- Nothing USB debugging instructions: https://support.nothing.tech/hc/en-us/articles/16770211339281-How-do-I-enable-USB-debugging
- Android ADB: https://developer.android.com/tools/adb
- Android APK signing and verification: https://developer.android.com/tools/apksigner
- Python no-bytecode option: https://docs.python.org/3.12/using/cmdline.html#cmdoption-B
- NSIS script reference: https://nsis.sourceforge.io/Docs/Chapter4.html
- CPython Windows distribution: https://www.python.org/ftp/python/3.12.10/amd64/


## GitHub and licensing

The source ZIP contains the editable Windows app, Android companion, installer, assets, tests and build instructions. Extract its GlyphLink-1.3.6 folder and upload the contents to your repository. Attach the EXE to a GitHub Release. Compiled binaries, downloaded runtime dependencies and private signing files are excluded from the source ZIP.

GlyphLink source is released under the [MIT License](LICENSE). Third-party components retain their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Detailed build instructions are in [BUILDING.md](BUILDING.md).
