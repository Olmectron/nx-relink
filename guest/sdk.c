// The test game's "sdk" module: the functions rtld starts a game with, and what the "main" module imports. No console SDK code:
// it stands in for one so the port's pipeline (relinking, loading, system calls) can be run without a game.
typedef unsigned long u64;
typedef unsigned u32;
u32 guest_svc_output(const char *s, u64 n);
u32 guest_svc_setheap(void **out, u64 size);
u64 guest_svc_tick(void);
void guest_svc_sleep(long ns);

int sdk_counter;                                                  // bss, imported by main through its GOT
const char *sdk_names[4] = {"zero", "one", "two", "three"};       // pointers in data: relative relocations
static char line[160];

void sdk_puts(const char *s) {
    u64 n = 0;
    while (s[n]) n++;
    guest_svc_output(s, n);
}
void sdk_put_hex(const char *what, u64 v) {
    int n = 0;
    while (what[n] && n < 100) line[n] = what[n], n++;
    for (int k = 60; k >= 0; k -= 4) line[n++] = "0123456789abcdef"[v >> k & 15];
    line[n] = 0;
    sdk_puts(line);
}
void nnosInitialize(u32 thread, void *argument) {
    (void)argument;
    sdk_put_hex("sdk: nnosInitialize, main thread handle ", thread);
}
void nninitStartup(void) {
    void *heap = 0;
    const u32 rc = guest_svc_setheap(&heap, 64u << 20);
    sdk_put_hex("sdk: SetHeapSize(64 MB) result ", rc);
    if (!rc) {
        volatile u32 *p = heap;
        p[0] = 0x12345678, p[(64u << 20) / 4 - 1] = 0x9abcdef0;
        sdk_put_hex("sdk: heap written and read back ", (u64)p[0] << 32 | p[(64u << 20) / 4 - 1]);
    }
}
u64 sdk_ticks(void) {
    const u64 a = guest_svc_tick();
    guest_svc_sleep(10000000);
    return guest_svc_tick() - a;
}
