// The start of a test module: what every NSO has. A word (unused here), then where the MOD0 header is; the header says where the
// dynamic section and the bss are, and reserves rtld's record of the module.
    .section .crt0, "ax", %progbits
    .global _start
_start:
    .word 0
    .word __nx_mod0 - _start

    .section .rodata.mod0, "a", %progbits
    .balign 4
__nx_mod0:
    .ascii "MOD0"
    .word _DYNAMIC - __nx_mod0
    .word __bss_start__ - __nx_mod0
    .word __bss_end__ - __nx_mod0
    .word 0
    .word 0
    .word __nx_module_runtime - __nx_mod0

    .section .bss.module_runtime, "aw", %nobits
    .balign 8
__nx_module_runtime:
    .space 0xD0
