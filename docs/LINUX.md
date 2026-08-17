# Building the Swift packages on Linux

`WhoopProtocol` and `OuraProtocol` have always been portable to Linux. With the setup below,
`WhoopStore`, `StrandAnalytics`, and `StrandImport` also build on Linux, and the full
`StrandAnalytics` test suite runs there.

## Toolchain and system packages

Install a Swift 6.0 or newer Linux toolchain from swift.org. Verify the downloaded tarball against
its published SHA-256 checksum before extracting it, then add its `usr/bin` directory to `PATH`.

On Ubuntu, the required system packages are:

```sh
sudo apt install build-essential libc6-dev libncurses-dev libxml2 libcurl4 \
  zlib1g-dev libedit2 pkg-config unzip
```

Some newer Ubuntu releases provide `libxml2.so.16` while a Swift tarball expects the older
`libxml2.so.2` name. If the toolchain reports that library as missing, create a compatibility
symlink to `libxml2.so.16` in a private directory and add that directory to `LD_LIBRARY_PATH`.
Do not replace a system library.

## Snapshot-enabled SQLite

Ubuntu's SQLite does not expose the `sqlite3_snapshot_*` symbols required by GRDB. Download a
versioned SQLite amalgamation from sqlite.org, verify its SHA-256 checksum, and build a private
shared library. The concrete recipe for 3.53.4 (the version the CI job pins — its `sqlite3.c`
hash lives next to the download step in `.github/workflows/swift-packages.yml`):

```sh
SQLITE_SNAPSHOT_DIR="$HOME/sqlite-snapshot"
mkdir -p "$SQLITE_SNAPSHOT_DIR"
curl --fail --location --proto '=https' --tlsv1.2 \
  https://sqlite.org/2026/sqlite-amalgamation-3530400.zip -O
unzip sqlite-amalgamation-3530400.zip
echo "b1dd5d74ec7f29055a6684fa06fb3c2f6821c87dd38f9a458dfd2e8a1db28189  sqlite-amalgamation-3530400/sqlite3.c" \
  | sha256sum --check --strict
gcc -shared -fPIC -DSQLITE_ENABLE_SNAPSHOT=1 \
  sqlite-amalgamation-3530400/sqlite3.c \
  -o "$SQLITE_SNAPSHOT_DIR/libsqlite3.so.0"
ln -s libsqlite3.so.0 "$SQLITE_SNAPSHOT_DIR/libsqlite3.so"
```

For a different amalgamation version, take the download URL and the `sqlite3.c` checksum from
sqlite.org's download page and substitute both.

**Do not install this library in `/usr/local/lib`, `/usr/lib`, or another system library path.**
Doing so can override the distribution SQLite for every host process. Keep it in its own directory
and select it only for the build or test being run.

From `Packages/StrandAnalytics`:

```sh
swift build -Xlinker -L"$SQLITE_SNAPSHOT_DIR"
LD_LIBRARY_PATH="$SQLITE_SNAPSHOT_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  swift test -Xlinker -L"$SQLITE_SNAPSHOT_DIR"
```

The same build and test flags apply to the other Linux-capable packages. The standalone
`WhoopStore` test target still references the Compression-backed raw-outbox API and therefore does
not compile on Linux; this does not affect its library build or the `StrandAnalytics` test suite.

`StrandDesign` and the app targets require macOS frameworks. BLE operation must be tested on real
supported hardware; Linux package tests do not exercise a physical device.
