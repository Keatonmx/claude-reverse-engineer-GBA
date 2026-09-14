#!/bin/sh
# Build a headless libmgba and the three tools next to this script. Needs cmake, gcc, zlib and libpng headers.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
MGBA=${MGBA_DIR:-$HERE/mgba}
[ -d "$MGBA" ] || git clone --depth 1 https://github.com/mgba-emu/mgba.git "$MGBA"
mkdir -p "$MGBA/build" && cd "$MGBA/build"
cmake .. -DCMAKE_BUILD_TYPE=Release -DBUILD_QT=OFF -DBUILD_SDL=OFF -DBUILD_GL=OFF -DBUILD_GLES2=OFF -DBUILD_GLES3=OFF \
  -DUSE_FFMPEG=OFF -DUSE_SQLITE3=OFF -DUSE_ELF=OFF -DUSE_EPOXY=OFF -DUSE_LIBZIP=OFF -DUSE_LZMA=OFF -DUSE_EDITLINE=OFF \
  -DBUILD_STATIC=ON -DBUILD_SHARED=OFF -DBUILD_HEADLESS=ON > /dev/null
make -j"$(nproc)" > make.log 2>&1
DEFS=$(grep '^C_DEFINES' CMakeFiles/mgba-headless.dir/flags.make | cut -d= -f2-)
INCS=$(grep '^C_INCLUDES' CMakeFiles/mgba-headless.dir/flags.make | cut -d= -f2-)
for t in harness trace oracle; do
  gcc -O2 $DEFS $INCS "$HERE/$t.c" -o "$HERE/$t" "$PWD/libmgba.a" -lpng -lz -lm -lpthread
done
echo "built: $HERE/harness $HERE/trace $HERE/oracle"
