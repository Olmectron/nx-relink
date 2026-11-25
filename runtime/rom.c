// The game's own files (its RomFS), given to it from the card. The SDK asks the file system service for "the data storage of the
// current process" (fsp-srv 200) and reads it as one long image; but the process is whatever program the port was started over, so
// the system would hand over that program's files (the game then finds none of its own: a null from its first file, and a fault;
// crash report, 2026-11-22). So that request is answered here, with an object that is the image tools/nsp.py --romfs extracts,
// copied to <folder>/romfs.bin: its reads are served from that file. (An image over 4 GB doesn't fit a file of a FAT32 card's:
// it can be in parts instead, <name>.romfs.00, .01, ..., all of one size but the last.)
// On a game's thread: system calls and the host library's service calls only (they use the thread's message buffer, nothing else).
#include <switch.h>

#include "nx2.h"

#ifndef NX2_NAME
#define NX2_NAME "game"
#endif
#define ROM_OBJECT 0x4E583200u  // the storages' ids in the session's domain, as the SDK is told (none of the service's are this high)

// An image on the card: the game's own RomFS (the first), then one for each add-on content it has there (DLC):
// <folder>/aoc<index>.romfs, which tools/nsp.py --aoc writes. The SDK asks for those by "data id", the last digits of which
// are the add-on's index (fsp-srv 202), after asking the add-on content service which there are (nx2_aoc_ipc).
#define ROM_PARTS 16
#define ROM_IMAGES 41
typedef struct {
    FsFile part[ROM_PARTS];
    s64 size, part_size;  // the whole image's size, and one part's (the whole, for an image in one file)
    u32 parts, index;     // (index: the add-on's; 0 for the game)
    int ok;
} Image;
static Image images[ROM_IMAGES];
static u32 image_count = 1, rom_logged;

static void image_open(Image *im, char *path) {
    FsFileSystem *sd = fsdevGetDeviceFileSystem("sdmc");
    if (!sd) return;
    if (!fsFsOpenFile(sd, path, FsOpenMode_Read, &im->part[0]) && R_SUCCEEDED(fsFileGetSize(&im->part[0], &im->size))) {
        im->ok = 1, im->parts = 1, im->part_size = im->size;
        return;
    }
    u32 end = 0;  // in parts
    while (path[end]) end++;
    path[end] = '.', path[end + 3] = 0;
    for (; im->parts < ROM_PARTS; im->parts++) {
        s64 size = 0;
        path[end + 1] = (char)('0' + im->parts / 10), path[end + 2] = (char)('0' + im->parts % 10);
        if (fsFsOpenFile(sd, path, FsOpenMode_Read, &im->part[im->parts]) || R_FAILED(fsFileGetSize(&im->part[im->parts], &size))) break;
        if (!im->parts) im->part_size = size;
        im->size += size;
    }
    path[end] = 0;
    im->ok = im->parts > 0 && im->part_size > 0;
}

void nx2_rom_open(void) {
    static char path[FS_MAX_PATH] = NX2_HOME "/romfs.bin";
    image_open(&images[0], path);
    if (images[0].ok) nx2_log("[rom] sdmc:%s: %u MB in %u file(s), the game's files\n", path, (u64)images[0].size >> 20, (u64)images[0].parts);
    else nx2_log("[rom] sdmc:%s isn't there: the game gets the files of the program this was started over\n", path);
    for (u32 index = 1; index < ROM_IMAGES && image_count < ROM_IMAGES; index++) {
        static char aoc[FS_MAX_PATH];
        const char *head = NX2_HOME "/aoc";
        u32 n = 0;
        for (; head[n]; n++) aoc[n] = head[n];
        if (index >= 10) aoc[n++] = (char)('0' + index / 10);
        aoc[n++] = (char)('0' + index % 10);
        for (const char *t = ".romfs"; *t; t++) aoc[n++] = *t;
        aoc[n] = 0;
        Image *im = &images[image_count];
        image_open(im, aoc);
        if (!im->ok) continue;
        im->index = index, image_count++;
        nx2_log("[rom] sdmc:%s: %u KB, add-on content %u\n", aoc, (u64)im->size >> 10, (u64)index);
    }
}

// size bytes of an image at offset (in range), from the part or parts they are in
static u32 rom_read(Image *im, u64 offset, u8 *to, u64 size, u64 *got) {
    *got = 0;
    while (size) {
        const u64 part = offset / (u64)im->part_size, in = offset % (u64)im->part_size;
        u64 take = (u64)im->part_size - in, n = 0;
        if (take > size) take = size;
        if (part >= im->parts) return 0x2EE202;
        // A read is a message to the file system with the memory to fill, and for as long as the message is out the kernel has that
        // memory locked and out of the program's reach. On its console a game's read takes a moment; from a card it takes far
        // longer, and whatever else needs those pages meanwhile fails: the graphics driver mapping the same pool of memory for
        // the graphics processor, as a game does while its textures load (it was closed with no word a few seconds
        // in, the thread reading and the thread mapping both mid-way; 2026-11-24). So the read fills memory of the port's, one
        // thread at a time, and what arrived is copied into the game's: the game's memory is never locked for long.
        static u8 bounce[4 << 20] __attribute__((aligned(0x1000)));
        static u32 bounce_lock;
        u32 rc = 0;
        if (nx2_bounce) {
            if (take > sizeof bounce) take = sizeof bounce;
            while (__atomic_exchange_n(&bounce_lock, 1, __ATOMIC_ACQUIRE)) svcSleepThread(0);
            rc = fsFileRead(&im->part[part], (s64)in, bounce, take, FsReadOption_None, &n);
            for (u64 k = 0; k < n; k++) to[k] = bounce[k];
            __atomic_store_n(&bounce_lock, 0, __ATOMIC_RELEASE);
        } else {
            rc = fsFileRead(&im->part[part], (s64)in, to, take, FsReadOption_None, &n);
        }
        *got += n;
        if (rc) return rc;
        if (n != take) return 0;
        offset += take, to += take, size -= take;
    }
    return 0;
}

static u32 rd32(const u8 *p) { return (u32)p[0] | (u32)p[1] << 8 | (u32)p[2] << 16 | (u32)p[3] << 24; }
static u64 rd64(const u8 *p) { return rd32(p) | (u64)rd32(p + 4) << 32; }
static void wr32(u8 *p, u32 v) { p[0] = (u8)v, p[1] = (u8)(v >> 8), p[2] = (u8)(v >> 16), p[3] = (u8)(v >> 24); }

// a request's data (after its header and buffer descriptors, on a 16 byte boundary), and its first receive buffer
static u32 buf_mode;  // (how the last receive buffer looked at was to be mapped: 0 as usual, 1 non-secure allowed, 3 non-device)
static const u8 *data_of(const u8 *m, u64 *buf, u64 *buf_size) {
    const u32 w0 = rd32(m), w1 = rd32(m + 4);
    u32 o = 8;
    if (w1 >> 31) {
        const u32 s = rd32(m + o);
        o += 4 + ((s & 1) ? 8 : 0) + ((s >> 1 & 15) + (s >> 5 & 15)) * 4;
    }
    o += (w0 >> 16 & 15) * 8 + (w0 >> 20 & 15) * 12;
    *buf = *buf_size = 0;
    if (w0 >> 24 & 15) {
        const u32 size_lo = rd32(m + o), addr_lo = rd32(m + o + 4), hi = rd32(m + o + 8);
        *buf = addr_lo | (u64)(hi >> 28 & 15) << 32 | (u64)(hi >> 2 & 7) << 36;
        *buf_size = size_lo | (u64)(hi >> 24 & 15) << 32;
        buf_mode = hi & 3;
    }
    o += ((w0 >> 24 & 15) + (w0 >> 28 & 15)) * 12;
    return m + ((o + 15) & ~15u);
}

// an answer in a domain: the domain's header, the result, the data, the object handed over (0: none)
static void answer(u8 *m, u32 result, const u8 *raw, u32 raw_size, u32 object) {
    for (u32 k = 0; k < 0x100; k++) m[k] = 0;
    u8 *d = m + 0x10;
    wr32(d, object ? 1 : 0);
    d += 16;
    wr32(d, 0x4F434653), wr32(d + 8, result);  // "SFCO"
    d += 16;
    for (u32 k = 0; k < raw_size; k++) d[k] = raw[k];
    d += raw_size;
    if (object) wr32(d, object), d += 4;
    wr32(m + 4, (u32)(d - m - 8 + 8 + 3) / 4);
}

// A message to a session of the file system service's: 1 if it was answered here (in the message buffer m).
int nx2_rom_ipc(u8 *m) {
    const u32 type = rd32(m) & 0xFFFF;
    if (!images[0].ok || (type != 4 && type != 6)) return 0;  // (a request, with or without a context: not a control message)
    u64 buf, buf_size;
    const u8 *d = data_of(m, &buf, &buf_size);
    if (d + 32 > m + 0x100) return 0;
    const u32 kind = d[0], object = rd32(d + 4);
    const int has_cmd = rd32(d + 16) == 0x49434653;  // "SFCI"
    const u32 cmd = has_cmd ? rd32(d + 24) : ~0u;
    if (object < ROM_OBJECT || object >= ROM_OBJECT + image_count) {
        if (kind != 1 || !has_cmd) return 0;
        if (cmd == 200) {
            nx2_log("[rom] the SDK asks for the process's data storage: given the file on the card\n");
            answer(m, 0, NULL, 0, ROM_OBJECT);
            return 1;
        }
        if (cmd == 202 && image_count > 1 && d + 48 <= m + 0x100) {  // OpenDataStorageByDataId(storage, data id)
            const u64 id = rd64(d + 40);
            if (id >> 12 == 0x0100000000000ull) return 0;  // (the system's own data: its fonts and the like)
            for (u32 k = 1; k < image_count; k++)
                if (images[k].index == (id & 0xFFF)) {
                    nx2_log("[rom] the SDK asks for data %x: add-on content %u, given the file on the card\n", id, (u64)images[k].index);
                    answer(m, 0, NULL, 0, ROM_OBJECT + k);
                    return 1;
                }
            nx2_log("[rom] the SDK asks for data %x: no add-on content of that index is on the card\n", id);
        }
        return 0;
    }
    Image *im = &images[object - ROM_OBJECT];
    if (kind == 2) {  // the object closed
        answer(m, 0, NULL, 0, 0);
        return 1;
    }
    if (cmd == 0) {  // Read(offset, size) into the buffer
        const u64 offset = rd64(d + 32), size = rd64(d + 40);
        u32 rc = 0;
        u64 got = 0;
        if (size > buf_size || offset > (u64)im->size || size > (u64)im->size - offset) rc = 0x2EE202;  // out of range (fs)
        else if (size) rc = rom_read(im, offset, (u8 *)buf, size, &got);
        if (rc || got != size || rom_logged < 12 || nx2_ipclog) {  // (<folder>/ipclog: every read)
            rom_logged++;
            // (with every read said, a sum of what arrived too: checked against the files on a computer, tools/bench/romsum.py)
            u64 s1 = 0, s2 = 0;
            if (nx2_ipclog && !rc)
                for (u64 k = 0; k < got; k++) s1 += ((const u8 *)buf)[k], s2 += s1;
            nx2_log("[rom] read %x bytes at %x: %x, %x read, sum %x %x, to %x (the game's buffer: mode %u)\n", size, offset, (u64)rc, got, s1, s2, buf, (u64)buf_mode);
        }
        answer(m, rc ? rc : (got != size ? 0x2EE202 : 0), NULL, 0, 0);
        return 1;
    }
    if (cmd == 4) {  // GetSize
        u8 out[8];
        wr32(out, (u32)im->size), wr32(out + 4, (u32)((u64)im->size >> 32));
        answer(m, 0, out, 8, 0);
        return 1;
    }
    nx2_log("[rom] command %u of the storage isn't handled: answered as done\n", (u64)cmd);
    static const u8 zeros[0x40];
    answer(m, 0, zeros, 0x40, 0);
    return 1;
}

// A message to the add-on content service (aoc:u): 1 if it was answered here. The system would count the add-ons of the program this
// was started over; the game is told of the ones on the card instead. Asked only when there are some: without any, the service's
// own answers stand. (Its other commands, the event of a change among them, are the service's still.)
int nx2_aoc_ipc(u8 *m) {
    const u32 type = rd32(m) & 0xFFFF;
    if (image_count < 2 || (type != 4 && type != 6)) return 0;
    u64 buf, buf_size;
    const u8 *d = data_of(m, &buf, &buf_size);
    if (d + 32 > m + 0x100) return 0;
    const int domain = rd32(d) != 0x49434653 && rd32(d + 16) == 0x49434653;
    const u8 *q = domain ? d + 16 : d;  // "SFCI", the command, then its data
    if (rd32(q) != 0x49434653 || (domain && d[0] != 1)) return 0;
    const u32 cmd = rd32(q + 8), count = image_count - 1;
    u8 raw[8] = {0};
    u32 raw_size = 0;
    if (cmd == 2) {  // CountAddOnContent
        wr32(raw, count), raw_size = 4;
    } else if (cmd == 3) {  // ListAddOnContent(offset, count): the indexes, into the buffer
        const u32 from = rd32(q + 16), want = rd32(q + 20);
        u32 n = 0;
        for (u32 k = from; k < count && n < want && (n + 1) * 4 <= buf_size; k++, n++) wr32((u8 *)buf + n * 4, images[1 + k].index);
        wr32(raw, n), raw_size = 4;
    } else if (cmd != 6 && cmd != 7) {  // (6, 7: PrepareAddOnContent, done)
        return 0;
    }
    nx2_log("[aoc] command %u: answered from the card (%u add-on content there)\n", (u64)cmd, (u64)count);
    if (domain) {
        answer(m, 0, raw, raw_size, 0);
        return 1;
    }
    for (u32 k = 0; k < 0x100; k++) m[k] = 0;
    wr32(m + 4, (16 + 16 + raw_size + 3) / 4);
    wr32(m + 0x10, 0x4F434653);  // "SFCO", then the result (0)
    for (u32 k = 0; k < raw_size; k++) m[0x20 + k] = raw[k];
    return 1;
}

// The game's saved data, kept in a folder of the card: <folder>/save. The SDK asks the file system service for the save data
// of the running program, which is the program this was started over: the game then writes in another game's save, and a game, which empties its save's folder first, was ended at its first write there (the SDK's abort, result c9f46402;
// log, 2026-11-22). So that request is sent as one for the card's file system instead, and every path the SDK then gives that file
// system has the folder put before it. A request to it that can't be read here is refused, never passed on as it is: it would act on
// the card's root.
#define SAVE_DIR NX2_HOME "/save"
#define SAVE_OBJECTS 8
static u32 save_objects[SAVE_OBJECTS];
static u32 save_logged;

// On a Switch 2 the file system service refuses the SDK's session the card's file system (result 6400, "permission denied", at
// A game's first mount of its saved data; log, 2026-11-23), though the host library has one, from its own start. So
// there the save data's file system is the host's: the game is given an object of the port's own making, and each request to it,
// and to the files and folders opened through it, is sent on the host's session with the host's object in place of the port's.
// The objects the host's session hands back are the host's too: each is given a number of the port's before the game sees it.
#define LENT_OBJECT 0x4E585300u
#define LENT 96
static struct {
    u32 real;  // the object in the host's session (0: a free place)
    u8 fs;     // the file system itself: its paths get the folder, and closing it closes nothing of the host's
} lent[LENT];
static u32 lent_lock;
static Service *host_fs;  // the host's file system of the card, when it is an object of a domain (else null: the SDK's session is asked)

static u32 lend(u32 real, int fs) {
    while (__atomic_exchange_n(&lent_lock, 1, __ATOMIC_ACQUIRE)) svcSleepThread(0);
    u32 id = 0;
    for (u32 k = 0; k < LENT && !id; k++)
        if (!lent[k].real) lent[k].real = real, lent[k].fs = (u8)fs, id = LENT_OBJECT + k;
    __atomic_store_n(&lent_lock, 0, __ATOMIC_RELEASE);
    return id;
}

void nx2_save_open(void) {
    FsFileSystem *sd = fsdevGetDeviceFileSystem("sdmc");
    if (!sd) return;
    static const char up[FS_MAX_PATH] = NX2_HOME, dir[FS_MAX_PATH] = SAVE_DIR;
    fsFsCreateDirectory(sd, up);
    const u32 rc = fsFsCreateDirectory(sd, dir);
    nx2_log("[save] the game's saved data is kept in sdmc:%s (made: %x)\n", dir, (u64)rc);
#ifdef __SWITCH2__
    if (sd->s.object_id) host_fs = &sd->s;
    nx2_log("[save] the host's file system of the card: session %x, object %x: %s\n", (u64)sd->s.session, (u64)sd->s.object_id,
            host_fs ? "the game's saved data goes through it" : "not an object of a domain, so the SDK's own session is asked for the card");
#endif
}

// where a message's pointer descriptors are, and how many
static u8 *pointers_of(u8 *m, u32 *count) {
    const u32 w0 = rd32(m), w1 = rd32(m + 4);
    u32 o = 8;
    if (w1 >> 31) {
        const u32 s = rd32(m + o);
        o += 4 + ((s & 1) ? 8 : 0) + ((s >> 1 & 15) + (s >> 5 & 15)) * 4;
    }
    *count = w0 >> 16 & 15;
    return m + o;
}

// the paths of a request to the save data's file system, put in its folder (paths: room for two). 0: a request that isn't understood
static int in_folder(u8 *m, u32 cmd, int plain, char (*paths)[0x301]) {
    u32 count;
    u8 *x = pointers_of(m, &count);
    int ok = plain && cmd <= 15 && count <= 2 && (count > 0 || cmd == 10);  // (10: Commit, the only one without a path)
    for (u32 k = 0; ok && k < count; k++) {
        const u32 w = rd32(x + k * 8);
        const char *from = (const char *)(rd32(x + k * 8 + 4) | (u64)(w >> 12 & 15) << 32 | (u64)(w >> 6 & 7) << 36);
        const u32 size = w >> 16;
        char *to = paths[k];
        u32 n = 0;
        for (const char *p = SAVE_DIR; *p; p++) to[n++] = *p;
        if (!from || !size || from[0] != '/') ok = 0;
        for (u32 i = 0; ok && i < size && from[i]; i++) {
            if (from[i] == '.' && from[i + 1] == '.') ok = 0;  // (nothing above the folder)
            if (n >= 0x300) ok = 0;
            else to[n++] = from[i];
        }
        to[n] = 0;
        if (!ok) break;
        if (save_logged < 24) save_logged++, nx2_log("[save] command %u, %s\n", (u64)cmd, to);
        wr32(x + k * 8, (w & 0xE3F) | (u32)((u64)to >> 32 & 15) << 12 | (u32)((u64)to >> 36 & 7) << 6 | 0x301u << 16);
        wr32(x + k * 8 + 4, (u32)(u64)to);
    }
    if (!ok) nx2_log("[save] a request to the save data's file system that isn't understood (command %d, %u paths): refused\n", (s64)(s32)cmd, (u64)count);
    return ok;
}

// Before a message to the file system service is sent. 0: not about saved data. 1: a request for the save data's file system, now
// one for the card's (nx2_save_after reads its answer). 2: a request to that file system, its paths in the folder now (paths: room
// for two, which must last until the message is sent). 3: answered here. 5: to be sent on the host's session instead (*handle),
// and nx2_save_after given its answer.
int nx2_save_before(u8 *m, char (*paths)[0x301], u32 *handle) {
    const u32 type = rd32(m) & 0xFFFF;
    if (type != 4 && type != 6) return 0;
    u64 buf, buf_size;
    u8 *d = (u8 *)data_of(m, &buf, &buf_size);
    if (d + 32 > m + 0x100) return 0;
    const u32 kind = d[0], object = rd32(d + 4);
    const int has_cmd = rd32(d + 16) == 0x49434653;  // "SFCI"
    const u32 cmd = has_cmd ? rd32(d + 24) : ~0u;
    if (object >= LENT_OBJECT && object < LENT_OBJECT + LENT && lent[object - LENT_OBJECT].real) {  // one of the host's, lent
        const u32 k = object - LENT_OBJECT;
        if (lent[k].fs) {
            if (kind == 2) {  // the file system closed: the host's stays open
                lent[k].real = 0;
                answer(m, 0, NULL, 0, 0);
                return 3;
            }
            if (!in_folder(m, cmd, kind == 1 && has_cmd, paths)) {
                answer(m, 0x2EE202, NULL, 0, 0);
                return 3;
            }
        }
        wr32(d + 4, lent[k].real);
        if (kind == 2) lent[k].real = 0;  // (a file or a folder closed: the host's object goes with this message)
        *handle = host_fs->session;
        return 5;
    }
    int mine = -1;
    for (int k = 0; k < SAVE_OBJECTS; k++)
        if (object && save_objects[k] == object) mine = k;
    if (mine < 0) {
        if (kind != 1 || !has_cmd || (cmd != 51 && cmd != 53)) return 0;  // OpenSaveDataFileSystem, and the read-only one
        if (host_fs) {
            const u32 id = lend(host_fs->object_id, 1);
            nx2_log("[save] the SDK asks for the program's save data (command %u): given the host's file system of the card, in %s\n", (u64)cmd, SAVE_DIR);
            answer(m, id ? 0 : 0x2EE202, NULL, 0, id);
            return 3;
        }
        nx2_log("[save] the SDK asks for the program's save data (command %u): given the card's file system, in %s\n", (u64)cmd, SAVE_DIR);
        const u32 token = rd32(d + 28);
        for (u32 k = 0; k < 0x40; k++) m[k] = 0;
        wr32(m, type), wr32(m + 4, 12);
        u8 *n = m + 0x10;
        n[0] = 1, n[2] = 0x10, wr32(n + 4, object);
        wr32(n + 16, 0x49434653), wr32(n + 24, 18), wr32(n + 28, token);  // OpenSdCardFileSystem
        return 1;
    }
    if (kind == 2) {  // the file system closed
        save_objects[mine] = 0;
        return 2;
    }
    if (in_folder(m, cmd, kind == 1 && has_cmd, paths)) return 2;
    answer(m, 0x2EE202, NULL, 0, 0);
    return 3;
}

// the answer to a request nx2_save_before changed (how: what it returned)
void nx2_save_after(u8 *m, int how) {
    u64 buf, buf_size;
    u8 *d = (u8 *)data_of(m, &buf, &buf_size);
    if (how == 5) {  // from the host's session: an object it hands back (a file, a folder) gets a number of the port's
        if (d + 36 <= m + 0x100 && rd32(d + 16) == 0x4F434653 && !rd32(d + 24) && rd32(d) == 1) {
            const u32 id = lend(rd32(d + 32), 0);
            if (!id) nx2_log("[save] too many files open at once\n");
            wr32(d + 32, id);
        }
        return;
    }
    if (d + 36 > m + 0x100 || rd32(d + 16) != 0x4F434653 || rd32(d + 24) || rd32(d) != 1) {
        nx2_log("[save] the card's file system wasn't given: %x\n", d + 28 <= m + 0x100 ? (u64)rd32(d + 24) : 0);
        return;
    }
    const u32 object = rd32(d + 32);
    for (int k = 0; k < SAVE_OBJECTS; k++)
        if (!save_objects[k]) {
            save_objects[k] = object;
            return;
        }
    nx2_log("[save] too many file systems open at once\n");
}

// (the same two for svc.c's own answers)
const u8 *nx2_ipc_data(const u8 *m, u64 *buf, u64 *buf_size) { return data_of(m, buf, buf_size); }
void nx2_ipc_answer(u8 *m, u32 result, const u8 *raw, u32 raw_size, u32 object) { answer(m, result, raw, raw_size, object); }
