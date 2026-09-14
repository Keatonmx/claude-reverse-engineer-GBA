# Headless emulation tools

Built against libmgba (static) so the game can be driven and inspected without a display.

    git clone --depth 1 https://github.com/mgba-emu/mgba && mkdir mgba/build && cd mgba/build
    cmake .. -DBUILD_QT=OFF -DBUILD_SDL=OFF -DBUILD_GL=OFF -DBUILD_GLES2=OFF -DBUILD_GLES3=OFF -DUSE_FFMPEG=OFF \
             -DUSE_SQLITE3=OFF -DUSE_ELF=OFF -DUSE_EPOXY=OFF -DUSE_LIBZIP=OFF -DUSE_LZMA=OFF -DUSE_EDITLINE=OFF \
             -DBUILD_STATIC=ON -DBUILD_SHARED=OFF -DBUILD_HEADLESS=ON && make -j4
    # compile with the same defines/includes as the mgba-headless target:
    DEFS=$(grep ^C_DEFINES CMakeFiles/mgba-headless.dir/flags.make | cut -d= -f2-)
    INCS=$(grep ^C_INCLUDES CMakeFiles/mgba-headless.dir/flags.make | cut -d= -f2-)
    gcc -O2 $DEFS $INCS harness.c -o harness $PWD/libmgba.a -lpng -lz -lm -lpthread
    gcc -O2 $DEFS $INCS trace.c   -o trace   $PWD/libmgba.a -lpng -lz -lm -lpthread

* `harness rom script outprefix` — scripted input (`<frame> keys <mask>`), screenshots (`shot`), memory dumps (`dump`:
  EWRAM/IWRAM/PAL/VRAM/OAM), I/O registers (`regs`), save/load states. `probe10.txt` is the route from the
  Create-a-Bod confirmation to the first district (state `s2` → `district`). `topng.py` converts `.rgba` screenshots.
* `trace rom state frames keymask log [lo:hi ...] [b<hex> ...]` — loads a state, sets write watchpoints and/or
  breakpoints, runs with keys held, logs registers on every hit. This is what resolved the tile-cache routine
  (`b0804FDA2`) and the VRAM fills (`06000000:06007fff`).
* `oracle rom state outdir blob...` — calls the game's own resource loader (0x0801EC00) on any ROM blob from inside a
  loaded state (registers hijacked, pipeline re-primed with ThumbWritePC, stepping until the return stub) and writes the
  decompressed bytes. Used to prove `urbz_codec.py` is byte-exact (type 6, type 6 + Diff16) and usable on anything
  the Python decoder cannot handle.
* `bgpal.bin` / `objpal.bin` — palette RAM captured in the first district (BG banks 0-15, OBJ banks 0-15).
