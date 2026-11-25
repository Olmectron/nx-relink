// The test game's "main" module: one of everything the relinker and the loader have to get right.
typedef unsigned long u64;
typedef unsigned u32;
void sdk_puts(const char *s);
void sdk_put_hex(const char *what, u64 v);
u64 sdk_ticks(void);
extern int sdk_counter;
extern const char *sdk_names[4];

static int ctor_ran;
__attribute__((constructor)) static void ctor(void) { ctor_ran = 0x600d; }   // the module's initializer array

static u64 fa(u64 x) { return x + 1; }
static u64 fb(u64 x) { return x * 3; }
static u64 fc(u64 x) { return x ^ 0xff; }
static u64 (*const table[3])(u64) = {fa, fb, fc};   // pointers to code, in relocated read-only data
static u64 big[4096];                              // bss past the first page
volatile int which = 5;

__attribute__((noinline)) static u64 pick(int k) {  // a jump table: code reached from read-only data
    switch (k) {
    case 0: return 11;
    case 1: return 7;
    case 2: return 1000;
    case 3: return 3;
    case 4: return 99999;
    case 5: return 0x42;
    case 6: return 5;
    case 7: return 123456;
    default: return 99;
    }
}

void nnMain(void) {
    sdk_puts("main: nnMain");
    sdk_put_hex("main: the initializer ran (600d) ", (u64)ctor_ran);
    sdk_put_hex("main: jump table, case 5 (42) ", pick(which));
    u64 v = 7;
    for (int k = 0; k < 3; k++) v = table[k](v);
    sdk_put_hex("main: three calls through pointers (e7) ", v);
    sdk_counter += 41;
    sdk_counter++;
    sdk_put_hex("main: the sdk's counter through the GOT (2a) ", (u64)sdk_counter);
    sdk_puts(sdk_names[2]);
    big[4095] = 0xb55;
    sdk_put_hex("main: bss (b55) ", big[4095]);
    sdk_put_hex("main: ticks in a 10 ms sleep (a real system call) ", sdk_ticks());
    sdk_puts("main: done");
}
