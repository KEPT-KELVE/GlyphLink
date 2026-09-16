"""Build with locally supplied Android SDK 35, JDK 17 and Kotlin compiler 2.0.x."""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SDK = Path(os.environ['ANDROID_HOME'])
JDK = Path(os.environ['JAVA_HOME'])
KLIB = Path(os.environ['KOTLIN_LIB_DIR'])
BT = SDK / 'build-tools/35.0.0'
PLATFORM = SDK / 'platforms/android-35'
MAIN = ROOT / 'android/app/src/main'
OUT = ROOT / 'build/android-offline'
OUT.mkdir(parents=True, exist_ok=True)
ENV = os.environ.copy()

def run(args):
    subprocess.run([str(a) for a in args], check=True, env=ENV)

manifest = ET.parse(MAIN / 'AndroidManifest.xml')
manifest.getroot().set('package', 'com.adam.glyphlink')
manifest.write(OUT / 'AndroidManifest.xml', encoding='utf-8', xml_declaration=True)
run([BT / 'aapt2', 'compile', '--dir', MAIN / 'res', '-o', OUT / 'resources.zip'])
run([BT / 'aapt2', 'link', '-o', OUT / 'base.apk', '-I', PLATFORM / 'android.jar',
     '--manifest', OUT / 'AndroidManifest.xml', '--min-sdk-version', '31',
     '--target-sdk-version', '35', '--version-code', '134', '--version-name', '1.3.4',
     OUT / 'resources.zip'])
java = OUT / 'generated/com/nothing/thirdparty/IGlyphService.java'
java.parent.mkdir(parents=True, exist_ok=True)
run([BT / 'aidl', '-I' + str(MAIN / 'aidl'), '-p' + str(PLATFORM / 'framework.aidl'),
     MAIN / 'aidl/com/nothing/thirdparty/IGlyphService.aidl', java])
classes = OUT / 'classes'
classes.mkdir(exist_ok=True)
run([JDK / 'bin/javac', '-source', '17', '-target', '17', '-classpath', PLATFORM / 'android.jar', '-d', classes, java])
compiler_cp = os.pathsep.join(str(p) for p in KLIB.glob('*.jar'))
stdlib = KLIB / 'kotlin-stdlib-2.0.20.jar'
classpath = os.pathsep.join(map(str, [PLATFORM / 'android.jar', classes, stdlib, KLIB / 'annotations-24.0.1.jar']))
run([JDK / 'bin/java', '-cp', compiler_cp, 'org.jetbrains.kotlin.cli.jvm.K2JVMCompiler',
     '-no-stdlib', '-no-reflect', '-jvm-target', '17', '-classpath', classpath, '-d', classes,
     *MAIN.glob('java/com/adam/glyphlink/*.kt')])
jar = OUT / 'app-classes.jar'
with zipfile.ZipFile(jar, 'w', zipfile.ZIP_DEFLATED) as z:
    for f in classes.rglob('*.class'): z.write(f, f.relative_to(classes))
dex = OUT / 'dex';dex.mkdir(exist_ok=True)
run([JDK / 'bin/java', '-cp', BT / 'lib/d8.jar', 'com.android.tools.r8.D8', '--release',
     '--min-api', '31', '--lib', PLATFORM / 'android.jar', '--output', dex, jar, stdlib])
unsigned = OUT / 'unsigned.apk'
shutil.copy2(OUT / 'base.apk', unsigned)
with zipfile.ZipFile(unsigned, 'a', zipfile.ZIP_DEFLATED) as z:
    for f in dex.glob('*.dex'):z.write(f, f.name)
run([BT / 'zipalign', '-f', '4', unsigned, OUT / 'aligned.apk'])
signing = ROOT / 'signing';signing.mkdir(exist_ok=True)
config_path = signing / 'private-signing.json'
if not config_path.exists():
    config_path.write_text(json.dumps({'alias': 'glyphlink', 'password': secrets.token_urlsafe(32)}), encoding='utf-8')
    config_path.chmod(0o600)
config = json.loads(config_path.read_text())
ENV['GLYPHLINK_KEY_PASSWORD'] = config['password']
key = signing / 'glyphlink-release.p12'
if not key.exists():
    run([JDK / 'bin/keytool', '-genkeypair', '-keystore', key, '-storepass:env', 'GLYPHLINK_KEY_PASSWORD',
         '-alias', config['alias'], '-keyalg', 'RSA', '-keysize', '3072', '-validity', '10000',
         '-dname', 'CN=GlyphLink, OU=Android companion, O=Adam Ali', '-storetype', 'PKCS12'])
    key.chmod(0o600)
final = ROOT / 'installer/payload/android/GlyphLink.apk';final.parent.mkdir(parents=True, exist_ok=True)
run([JDK / 'bin/java', '-jar', BT / 'lib/apksigner.jar', 'sign', '--ks', key,
     '--ks-key-alias', config['alias'], '--ks-pass', 'env:GLYPHLINK_KEY_PASSWORD', '--out', final, OUT / 'aligned.apk'])
run([JDK / 'bin/java', '-jar', BT / 'lib/apksigner.jar', 'verify', '--verbose', '--print-certs', final])
run([BT / 'aapt', 'dump', 'badging', final])
print('SIGNED APK:', final, final.stat().st_size, flush=True)
