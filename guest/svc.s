// the test game's system calls, as the console SDK's wrappers make them
    .text
    .global guest_svc_output, guest_svc_setheap, guest_svc_tick, guest_svc_sleep
    .type guest_svc_output, %function
guest_svc_output:
    svc 0x27
    ret
guest_svc_setheap:
    str x0, [sp, #-16]!
    svc 0x1
    ldr x2, [sp], #16
    str x1, [x2]
    ret
guest_svc_tick:
    svc 0x1E
    ret
guest_svc_sleep:
    svc 0xB
    ret
