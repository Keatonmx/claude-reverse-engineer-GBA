// Game-loader decompression oracle (based on the stepping probe that worked).
// usage: oracle rom state.bin outdir blobaddr...
#include <mgba/core/core.h>
#include <mgba/core/config.h>
#include <mgba/internal/arm/arm.h>
#include <mgba/internal/arm/isa-inlines.h>
#include <stdio.h>
#include <stdlib.h>
#define LOADER 0x0801EC00u
#define RET    0x0801EBF8u
#define DST    0x02030000u
int main(int argc, char** argv) {
	struct mCore* core = mCoreFind(argv[1]); core->init(core); mCoreInitConfig(core, "p"); mCoreLoadConfig(core);
	unsigned w, h; core->baseVideoSize(core, &w, &h); core->setVideoBuffer(core, calloc(w * h, sizeof(mColor)), w);
	mCoreLoadFile(core, argv[1]); core->reset(core);
	size_t n = core->stateSize(core); void* st = malloc(n); FILE* f = fopen(argv[2], "rb"); fread(st, 1, n, f); fclose(f);
	int i;
	for (i = 4; i < argc; ++i) {
		uint32_t blob = (uint32_t) strtoul(argv[i], NULL, 0); if (blob < 0x08000000) blob += 0x08000000;
		fprintf(stderr, "[oracle] loading state (%zu bytes)\n", n);
		core->loadState(core, st);
		struct ARMCore* cpu = core->cpu;
		fprintf(stderr, "[oracle] state loaded pc=%08X mode=%d\n", cpu->gprs[15], cpu->executionMode);
		long k;
		/* re-prime the pipeline at the current instruction (gprs[15] = pc + 2*width), then step until the game
		   is back in ROM code outside IRQ/SWI mode so our call is not stolen by the pending interrupt */
		if (cpu->executionMode == MODE_THUMB) { cpu->gprs[15] -= 4; ThumbWritePC(cpu); } else { cpu->gprs[15] -= 8; ARMWritePC(cpu); }
		for (k = 0; k < 20000000; ++k) { core->step(core); if ((cpu->gprs[15] >> 24) == 8 && cpu->privilegeMode == MODE_SYSTEM) break; }
		fprintf(stderr, "[oracle] in game code after %ld steps pc=%08X mode=%d\n", k, cpu->gprs[15], cpu->privilegeMode);
		cpu->gprs[0] = blob; cpu->gprs[1] = DST; cpu->gprs[14] = RET | 1; cpu->gprs[15] = LOADER;
		_ARMSetMode(cpu, MODE_THUMB); ThumbWritePC(cpu);
		fprintf(stderr, "[oracle] pc set %08X\n", cpu->gprs[15]);
		int done = 0;
		for (k = 0; k < 50000000; ++k) { core->step(core); uint32_t pc = cpu->gprs[15] & ~1u; if (pc >= RET && pc <= RET + 4) { done = 1; break; } }
		uint32_t size = cpu->gprs[0];
		if (!done || size > 0x40000) { fprintf(stderr, "blob %08X: no return (done=%d size=%u pc=%08X)\n", blob, done, size, cpu->gprs[15]); continue; }
		unsigned char* buf = malloc(size); uint32_t j;
		for (j = 0; j < size; ++j) buf[j] = core->busRead8(core, DST + j);
		char name[512]; snprintf(name, sizeof name, "%s/oracle_%07X.bin", argv[3], blob - 0x08000000);
		FILE* o = fopen(name, "wb"); fwrite(buf, 1, size, o); fclose(o); free(buf);
		printf("blob %08X -> %u bytes (%ld steps) -> %s\n", blob, size, k, name);
	}
	return 0;
}
