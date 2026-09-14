// Minimal headless driver over libmgba: scripted input, screenshots, memory dumps, save states.
// script lines:  <frame> keys <mask>   |  <frame> shot <name>  |  <frame> dump <name>  |  <frame> save <name>  |  <frame> load <name>
// key mask bits: A=1 B=2 SELECT=4 START=8 RIGHT=16 LEFT=32 UP=64 DOWN=128 R=256 L=512
#include <mgba/core/core.h>
#include <mgba/core/config.h>
#include <mgba/core/log.h>
#include <mgba-util/vfs.h>
#include <mgba/internal/gba/gba.h>
#include <mgba/internal/gba/memory.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void writefile(const char* name, const void* data, size_t n) {
	FILE* f = fopen(name, "wb");
	if (!f) { perror(name); return; }
	fwrite(data, 1, n, f);
	fclose(f);
}

static void dumpRegion(struct mCore* core, size_t id, const char* prefix, const char* tag) {
	size_t size = 0;
	void* p = core->getMemoryBlock(core, id, &size);
	char name[512];
	snprintf(name, sizeof name, "%s_%s.bin", prefix, tag);
	if (p && size) writefile(name, p, size); else fprintf(stderr, "no block %zu\n", id);
}

int main(int argc, char** argv) {
	if (argc < 4) { fprintf(stderr, "usage: harness rom script outprefix\n"); return 1; }
	struct mCore* core = mCoreFind(argv[1]);
	if (!core) { fprintf(stderr, "no core\n"); return 1; }
	core->init(core);
	mCoreInitConfig(core, "harness");
	mCoreConfigSetDefaultValue(&core->config, "idleOptimization", "remove");
	mCoreLoadConfig(core);
	unsigned w, h;
	core->baseVideoSize(core, &w, &h);
	mColor* video = calloc(w * h, sizeof(mColor));
	core->setVideoBuffer(core, video, w);
	if (!mCoreLoadFile(core, argv[1])) { fprintf(stderr, "load failed\n"); return 1; }
	core->reset(core);

	FILE* sf = fopen(argv[2], "r");
	if (!sf) { perror("script"); return 1; }
	char line[512];
	long frame = 0;
	uint32_t keys = 0;
	while (fgets(line, sizeof line, sf)) {
		long at; char cmd[32]; char arg[256] = "";
		if (sscanf(line, "%ld %31s %255s", &at, cmd, arg) < 2) continue;
		while (frame < at) { core->setKeys(core, keys); core->runFrame(core); ++frame; }
		char name[512];
		if (!strcmp(cmd, "keys")) {
			keys = (uint32_t) strtoul(arg, NULL, 0);
		} else if (!strcmp(cmd, "shot")) {
			snprintf(name, sizeof name, "%s_%s.rgba", argv[3], arg);
			writefile(name, video, w * h * sizeof(mColor));
		} else if (!strcmp(cmd, "dump")) {
			char prefix[400]; snprintf(prefix, sizeof prefix, "%s_%s", argv[3], arg);
			dumpRegion(core, 2, prefix, "ewram"); dumpRegion(core, 3, prefix, "iwram");
			dumpRegion(core, 5, prefix, "pal"); dumpRegion(core, 6, prefix, "vram");
			dumpRegion(core, 7, prefix, "oam"); dumpRegion(core, 4, prefix, "io");
		} else if (!strcmp(cmd, "regs")) {
			struct GBA* gba = core->board;
			snprintf(name, sizeof name, "%s_%s_io.bin", argv[3], arg);
			writefile(name, gba->memory.io, sizeof gba->memory.io);
		} else if (!strcmp(cmd, "save")) {
			size_t n = core->stateSize(core); void* buf = malloc(n);
			core->saveState(core, buf);
			snprintf(name, sizeof name, "%s_%s.state", argv[3], arg);
			writefile(name, buf, n); free(buf);
		} else if (!strcmp(cmd, "load")) {
			snprintf(name, sizeof name, "%s_%s.state", argv[3], arg);
			FILE* f = fopen(name, "rb");
			if (f) { size_t n = core->stateSize(core); void* buf = malloc(n); fread(buf, 1, n, f); fclose(f); core->loadState(core, buf); free(buf); }
			else perror(name);
		}
	}
	fclose(sf);
	printf("done at frame %ld\n", frame);
	core->deinit(core);
	return 0;
}
