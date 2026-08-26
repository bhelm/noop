#!/usr/bin/env bash
# Build and atomically deploy the local NOOP Android staging APK.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ANDROID_DIR="$REPO_DIR/android"
BUILD_FILE="$ANDROID_DIR/app/build.gradle.kts"
ARTIFACT="$ANDROID_DIR/app/build/outputs/apk/full/release/app-full-release.apk"
DEPLOY_APK="${DEPLOY_APK:-/root/mc-landing/noop-sync.apk}"
ANDROID_HOME="${ANDROID_HOME:-/opt/android-sdk}"
JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-17-openjdk-amd64}"
WEB_CONTAINER="${WEB_CONTAINER:-mc-landing}"
BUILD_LOCK="${BUILD_LOCK:-/tmp/noop-native-test-slot.lock}"
AAPT="${AAPT:-$ANDROID_HOME/build-tools/35.0.0/aapt}"
APKSIGNER="${APKSIGNER:-$ANDROID_HOME/build-tools/35.0.0/apksigner}"

fail() { echo "error: $*" >&2; exit 1; }
version_code() {
  "$AAPT" dump badging "$1" | sed -n "s/^package:.*versionCode='\([0-9][0-9]*\)'.*/\1/p" | head -1
}
package_name() {
  "$AAPT" dump badging "$1" | sed -n "s/^package: name='\([^']*\)'.*/\1/p" | head -1
}
signer_sha256() {
  "$APKSIGNER" verify --print-certs "$1" |
    sed -n 's/^Signer #1 certificate SHA-256 digest: //p' | head -1
}

test -x "$JAVA_HOME/bin/java" || fail "Java not found at $JAVA_HOME"
test -x "$JAVA_HOME/bin/jlink" || fail "complete JDK required; jlink missing at $JAVA_HOME/bin/jlink"
test -x "$AAPT" || fail "aapt not found at $AAPT"
test -x "$APKSIGNER" || fail "apksigner not found at $APKSIGNER"
test -f "$BUILD_FILE" || fail "not a NOOP checkout: $BUILD_FILE missing"

source_code="$(sed -n 's/^[[:space:]]*versionCode = \([0-9][0-9]*\)$/\1/p' "$BUILD_FILE" | head -1)"
test -n "$source_code" || fail "could not read versionCode from $BUILD_FILE"
deployed_code=0
if test -f "$DEPLOY_APK"; then
  deployed_code="$(version_code "$DEPLOY_APK")"
  test -n "$deployed_code" || fail "could not read deployed versionCode from $DEPLOY_APK"
fi

# Every run must produce a real Android update. Persist the next code in source so the
# artifact remains reproducible and a later build cannot accidentally roll back.
next_code="$source_code"
if test "$next_code" -le "$deployed_code"; then next_code=$((deployed_code + 1)); fi
if test "$next_code" != "$source_code"; then
  sed -i "0,/versionCode = $source_code/s//versionCode = $next_code/" "$BUILD_FILE"
  echo "versionCode: $source_code -> $next_code (deployed: $deployed_code)"
else
  echo "versionCode: $next_code (deployed: $deployed_code)"
fi

echo "Building full staging release under $BUILD_LOCK"
flock "$BUILD_LOCK" env JAVA_HOME="$JAVA_HOME" ANDROID_HOME="$ANDROID_HOME" \
  "$ANDROID_DIR/gradlew" -p "$ANDROID_DIR" --no-daemon --max-workers=1 \
  -Pkotlin.daemon.jvmargs=-Xmx1536m -Dorg.gradle.jvmargs=-Xmx1536m \
  -PstagingRelease assembleFullRelease

test -f "$ARTIFACT" || fail "Gradle succeeded but artifact is missing: $ARTIFACT"
"$APKSIGNER" verify --verbose "$ARTIFACT" >/dev/null || fail "APK signature verification failed"
built_package="$(package_name "$ARTIFACT")"
built_code="$(version_code "$ARTIFACT")"
test "$built_package" = "com.noop.whoop.staging" ||
  fail "wrong application id: $built_package (expected com.noop.whoop.staging)"
test "$built_code" = "$next_code" || fail "wrong versionCode: $built_code (expected $next_code)"
test "$built_code" -gt "$deployed_code" ||
  fail "refusing non-update: built $built_code, deployed $deployed_code"

if test -f "$DEPLOY_APK"; then
  old_signer="$(signer_sha256 "$DEPLOY_APK")"
  new_signer="$(signer_sha256 "$ARTIFACT")"
  test -n "$old_signer" && test "$old_signer" = "$new_signer" ||
    fail "signer differs from deployed staging app; Android would reject the update"
fi

deploy_dir="$(dirname "$DEPLOY_APK")"
deploy_tmp="$deploy_dir/.noop-sync.apk.$$.tmp"
trap 'rm -f "$deploy_tmp"' EXIT
install -m 0644 "$ARTIFACT" "$deploy_tmp"
mv -f "$deploy_tmp" "$DEPLOY_APK"

artifact_hash="$(sha256sum "$ARTIFACT" | awk '{print $1}')"
deployed_hash="$(sha256sum "$DEPLOY_APK" | awk '{print $1}')"
test "$artifact_hash" = "$deployed_hash" || fail "deployed file hash mismatch"

if command -v docker >/dev/null 2>&1 && docker inspect "$WEB_CONTAINER" >/dev/null 2>&1; then
  container_hash="$(docker exec "$WEB_CONTAINER" sha256sum /usr/share/nginx/html/noop-sync.apk | awk '{print $1}')"
  test "$artifact_hash" = "$container_hash" || fail "web container serves a different APK"
  docker exec "$WEB_CONTAINER" wget -q --spider "http://127.0.0.1/noop-sync.apk?v=$built_code" ||
    fail "web container cannot serve the deployed APK"
fi

echo "Deployed com.noop.whoop.staging build $built_code"
echo "Path: $DEPLOY_APK"
echo "SHA-256: $artifact_hash"
echo "Download: https://mc.rw23.de/noop-sync.apk?v=$built_code"
