#!/bin/bash
# Сборка APK внутри образа ghcr.io/cirruslabs/flutter (см. README).
set -euo pipefail
cd "$(dirname "$0")"
git config --global --add safe.directory '*' 2>/dev/null || true

if [ ! -d android ]; then
  flutter create . --project-name povilika --org kz.povilika --platforms android
  rm -f test/widget_test.dart
fi

# Шаблон Flutter просит у Gradle 8 ГБ — на машине с 8 ГБ ОЗУ это вешает систему.
PROPS=android/gradle.properties
sed -i 's|^org.gradle.jvmargs=.*|org.gradle.jvmargs=-Xmx1536m -XX:MaxMetaspaceSize=512m -XX:ReservedCodeCacheSize=192m|' "$PROPS"
for kv in org.gradle.daemon=false org.gradle.workers.max=2 kotlin.compiler.execution.strategy=in-process; do
  grep -q "^${kv%%=*}=" "$PROPS" || echo "$kv" >> "$PROPS"
done

MANIFEST=android/app/src/main/AndroidManifest.xml
grep -q 'android.permission.INTERNET' "$MANIFEST" ||
  sed -i 's|<manifest xmlns:android="http://schemas.android.com/apk/res/android">|&\n    <uses-permission android:name="android.permission.INTERNET"/>|' "$MANIFEST"
# http:// разрешён для работы с сервером в локальной сети; через туннель всё идёт по https.
grep -q 'usesCleartextTraffic' "$MANIFEST" ||
  sed -i 's|android:label="povilika"|android:label="Повилика"\n        android:usesCleartextTraffic="true"|' "$MANIFEST"

flutter pub get
flutter analyze --no-fatal-infos
flutter test
flutter build apk --release --target-platform android-arm64

mkdir -p dist
cp build/app/outputs/flutter-apk/app-release.apk dist/povilika.apk
ls -la dist/povilika.apk
