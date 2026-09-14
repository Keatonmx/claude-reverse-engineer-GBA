// Watchpoint tracer over libmgba: load a save state, set write watchpoints, run N frames with keys held, log every hit.
// usage: trace rom state.bin frames keymask logfile lo:hi [lo:hi ...] [rlo:hi ...] [b<hexaddr> ...]
//   lo:hi = write watchpoint, rlo:hi = read watchpoint, b<addr> = breakpoint
#include <mgba/core/core.h>
#include <mgba/core/config.h>
#include <mgba/debugger/debugger.h>
#include <mgba/internal/arm/arm.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static struct mCore* core;
static FILE* logfile;
static long hits = 0, maxHits = 5000;
static long curFrame = 0;

struct TraceModule { struct mDebuggerModule d; };

static void entered(struct mDebuggerModule* module, enum mDebuggerEntryReason reason, struct mDebuggerEntryInfo* info) {
	struct ARMCore* cpu = core->cpu;
	if (reason == DEBUGGER_ENTER_BREAKPOINT && info && hits < maxHits) {
		fprintf(logfile, "BP f=%ld pc=%08X r0=%08X r1=%08X r2=%08X r3=%08X r4=%08X r5=%08X r6=%08X r7=%08X lr=%08X sp=%08X [sp]=%08X [sp+4]=%08X\n",
			curFrame, cpu->gprs[15], cpu->gprs[0], cpu->gprs[1], cpu->gprs[2], cpu->gprs[3], cpu->gprs[4], cpu->gprs[5], cpu->gprs[6], cpu->gprs[7], cpu->gprs[14], cpu->gprs[13],
			core->busRead32(core, cpu->gprs[13]), core->busRead32(core, cpu->gprs[13] + 4));
		++hits;
	}
	if (reason == DEBUGGER_ENTER_WATCHPOINT && info && hits < maxHits) {
		fprintf(logfile, "f=%ld addr=%08X val=%08X w=%d src=%d pc=%08X r0=%08X r1=%08X r2=%08X r3=%08X r4=%08X r5=%08X r6=%08X r7=%08X lr=%08X sp=%08X\n",
			curFrame, info->address, info->type.wp.newValue, info->width, (int) info->type.wp.accessSource,
			cpu->gprs[15], cpu->gprs[0], cpu->gprs[1], cpu->gprs[2], cpu->gprs[3], cpu->gprs[4], cpu->gprs[5], cpu->gprs[6], cpu->gprs[7], cpu->gprs[14], cpu->gprs[13]);
		++hits;
	}
	module->isPaused = false;          // resume immediately
	module->p->state = DEBUGGER_RUNNING;
}

int main(int argc, char** argv) {
	if (argc < 7) { fprintf(stderr, "usage: trace rom state frames keymask logfile lo:hi...\n"); return 1; }
	core = mCoreFind(argv[1]);
	core->init(core);
	mCoreInitConfig(core, "trace");
	mCoreConfigSetDefaultValue(&core->config, "idleOptimization", "remove");
	mCoreLoadConfig(core);
	unsigned w, h; core->baseVideoSize(core, &w, &h);
	mColor* video = calloc(w * h, sizeof(mColor)); core->setVideoBuffer(core, video, w);
	if (!mCoreLoadFile(core, argv[1])) { fprintf(stderr, "load failed\n"); return 1; }

	struct mDebugger dbg; mDebuggerInit(&dbg);
	struct TraceModule mod; memset(&mod, 0, sizeof mod);
	mod.d.type = DEBUGGER_CUSTOM; mod.d.entered = entered;
	mDebuggerAttach(&dbg, core);
	mDebuggerAttachModule(&dbg, &mod.d);
	core->reset(core);

	FILE* f = fopen(argv[2], "rb");
	if (!f) { perror("state"); return 1; }
	size_t n = core->stateSize(core); void* buf = malloc(n); fread(buf, 1, n, f); fclose(f);
	core->loadState(core, buf); free(buf);

	long frames = atol(argv[3]); uint32_t keys = (uint32_t) strtoul(argv[4], NULL, 0);
	logfile = fopen(argv[5], "w");
	int i;
	for (i = 6; i < argc; ++i) {
		unsigned lo, hi;
		if (argv[i][0] == 'b') {   // b<hexaddr> = breakpoint
			struct mBreakpoint bp; memset(&bp, 0, sizeof bp);
			bp.segment = -1; bp.address = (uint32_t) strtoul(argv[i] + 1, NULL, 16); bp.type = BREAKPOINT_HARDWARE;
			ssize_t id = dbg.platform->setBreakpoint(dbg.platform, &mod.d, &bp);
			fprintf(stderr, "breakpoint %08X id %zd\n", bp.address, id);
			continue;
		}
		int rd = argv[i][0] == 'r';
		if (sscanf(argv[i] + rd, "%x:%x", &lo, &hi) != 2) continue;
		struct mWatchpoint wp; memset(&wp, 0, sizeof wp);
		wp.segment = -1; wp.minAddress = lo; wp.maxAddress = hi; wp.type = rd ? WATCHPOINT_READ : WATCHPOINT_WRITE;
		ssize_t id = dbg.platform->setWatchpoint(dbg.platform, &mod.d, &wp);
		fprintf(stderr, "%s watchpoint %08X-%08X id %zd\n", rd ? "read" : "write", lo, hi, id);
	}
	dbg.state = DEBUGGER_RUNNING;
	for (curFrame = 0; curFrame < frames; ++curFrame) {
		core->setKeys(core, keys);
		uint32_t fc = core->frameCounter(core);
		while (core->frameCounter(core) == fc) {
			if (dbg.state != DEBUGGER_RUNNING) dbg.state = DEBUGGER_RUNNING;
			mDebuggerRunTimeout(&dbg, 50);
		}
	}
	fclose(logfile);
	fprintf(stderr, "done, %ld hits\n", hits);
	return 0;
}
