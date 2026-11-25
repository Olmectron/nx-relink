# nx-relink

Makes a **Nintendo Switch** game you own into a homebrew program (`.nro`) that runs it on a **Switch**: the game's own code,
relinked, not emulated.

> [!CAUTION]
> **Experimental software. Use it at your own risk.**
> This runs unofficial code on your console. Games can crash or freeze, saved data can be lost or corrupted, files on your SD
> card can be damaged, and running homebrew at all can have consequences with the console's maker, a ban included. It is provided
> "as is", with no warranty of any kind. **The authors take no responsibility** for any damage or loss: to your console, your card,
> your data or your accounts. If that isn't acceptable to you, don't use it.

> [!IMPORTANT]
> **Use your own backups only.**
> This project contains no games, no keys and no Nintendo files, and it won't get them for you. It works only with dumps of games
> you own, made by you from your own console, and with your own console's keys. Nothing leaves your computer or your card, and the
> tool never shows a key. Don't ask for game files or keys here, and don't post them: such issues are closed.

> [!TIP]
> **A game crashed or didn't start? There is a file for that.**
> After a run that ended badly, the port writes **`crash.txt`** in its own folder of the SD card:
>
> **`/nx-relink/<name>/crash.txt`**  (`<name>` is the port's short name)
>
> It says what happened and has the end of that run's log. **Open an issue and attach that file** (and `log.txt`, beside it), with
> the game's name and version: that is everything needed to look into it.
> It is plain text you can read before sending, and has no keys, account or serial number in it.

## What it does

A Switch game is already code for the Switch's processor. Nothing is translated: the game's modules go into the `.nro` as they are,
and what surrounds them is replaced.

| Part of the game | What happens to it |
|---|---|
| Code (`main`, `sdk`, `subsdk*`) | Goes into the `.nro` unchanged, relinked |
| `rtld`, the console's loader | Replaced by the port's own |
| System calls | Pass through the port first, which answers them or lets them through |
| The game's files (RomFS) and DLC | Served from the SD card |
| Saved data | A folder of the SD card |
| Graphics, sound, input | The console's real drivers, as in the game itself |

## Compatibility

No list of games is kept here. Nothing in the tool is written for a particular game: it reads the game's own modules (`main`,
`sdk`, `subsdk*`), relinks them, and stands in for the loader and the file system, while graphics, sound and input stay on the
console's real drivers. So a game that was never tried has a fair chance.

**How it was tried.** Several commercial games, built with this tool and run on a console from their start into play:

- SDK versions from 4 to 16.
- Both ways a game draws: NVN and OpenGL.
- With and without an update, and with DLC.

That was before release 0.1.0 moved a port's files to folders of their own; the present layout hasn't been through a console yet.

**Before building**, `python nxrelink.py check <game.nsp>` says what a game asks of the port.

**Not every game will run.** When one doesn't, the port leaves `crash.txt` in its folder on the card
(`/nx-relink/<name>/crash.txt`): open an issue with that file, and the game's name and version.

## What you need

- A Switch that runs homebrew (Atmosphère), and its homebrew menu **started over a game** (hold R while starting any game), not
  from the album: a port needs the whole of the console's memory.
- Your game as an **NSP**, and your console's keys (`prod.keys`, which Lockpick_RCM writes) at `~/.switch/prod.keys`.
- A computer with Python 3.10 or newer, and [devkitPro](https://devkitpro.org/wiki/Getting_Started) with the `switch-dev` group
  (devkitA64, libnx, switch-tools).

Then, in this folder:

```bash
python nxrelink.py setup
```

checks all of the above, installs the Python packages that are missing (`pycryptodomex`, `lz4`, `Pillow`), and says where to get
whatever it can't install itself.

## Use

With a window:

```bash
python nxrelink.py
```

Choose the game, the card, and press **Build and install**. The bottom of the window always shows the step, a bar, and in long
copies the speed and the time left.

From a terminal, everything in one go:

```bash
python nxrelink.py make game.nsp --update update.nsp --aoc dlc.nsp --name mygame --sd E:
```

| Command | What it does |
|---|---|
| `setup` | Checks what the tool needs; installs the Python packages that are missing |
| `check <game.nsp>` | Says what the game asks of the port, without building anything |
| `make <game.nsp>` | Extracts, checks and builds; with `--sd` or `--ftp`, installs too |
| `install <name>` | Puts a port already built on a card |
| `list` | The ports already built |

Options of `make`:

| Option | For |
|---|---|
| `--update <nsp>` | The game's update: its code, and its files over the game's |
| `--aoc <nsp>` | An add-on content (DLC); may be repeated |
| `--name <name>` | The port's short name: its folder on the card |
| `--keys <prod.keys>` | Your keys, if they aren't at `~/.switch/prod.keys` |
| `--title`, `--version`, `--icon <picture>` | What the homebrew menu shows; by default, the game's own |
| `--no-romfs` | The game's files aren't extracted again |
| `--nro-only` (of `install`) | Only the program (`.nro`) is copied: the game's files are on the card already |
| `--sd <drive>` or `--ftp <host>` | Installs on the card in this computer, or over the network (an FTP server on the console, port 5000) |
| `--sd-format auto \| fat32 \| exfat` | On FAT32 a RomFS of 4 GB or more goes in parts of 2 GB; on exFAT, in one file |

Every file copied is read back from the card and compared with what was sent.

On the console the ports show in the homebrew menu, all together in the folder `nx-relink`, each with the game's name, version and icon. Leaving a
port returns to the HOME menu.

## What is on the card: saves, logs, the crash report

A port is two things on the SD card: its program, **`/switch/nx-relink/<name>.nro`**, which the homebrew menu lists in the folder
`nx-relink` with nothing else around it, and a folder at the card's root, **`/nx-relink/<name>/`**, with everything else of it:

| In `/nx-relink/<name>/` | What it is |
|---|---|
| `romfs.bin` (or `romfs.bin.00`, `.01`, ...) | The game's files |
| `aoc<n>.romfs` | Each DLC's files |
| **`save/`** | **The game's saved data** |
| **`crash.txt`** | **Written when a run ended badly: the file to send with a report** |
| `log.txt`, `log.prev.txt` | The log of the last run, and of the one before |
| `debug` | If this file exists (empty will do), the log is the detailed one |

**Saved data.** A port keeps its saves in `/nx-relink/<name>/save/`, as loose files, exactly as the game writes them. They
are **apart from the console's own save data**: a port doesn't read or touch the saves of the installed game, and the installed
game doesn't see the port's. To back a port's saves up, copy that folder; to start over, delete it. Deleting a port's folder
deletes its saves with it.

Earlier installs kept these elsewhere: in `/nx2/` before 0.1.0 (saves in `/nx2/save/<name>/`), and in `/switch/nx-relink/<name>/`
in 0.1.0 and 0.1.1. Installing the port again with this release moves its files and its saves to the new folder; nothing is
copied again.

**When a game doesn't work.** `log.txt` always notes what usually ends a game: a request the system refuses, the first call to
each function of the SDK, the SDK's own closing message. When a run ends with an error (or was still running when the console was
turned off), the next thing the port does is write `crash.txt`: what ended it, and the last lines of that run's log. Attach it to
an issue, with the game's name and version.

For a harder case there is a **detailed log**: `--long-log` when installing, or an empty file named `debug` in the port's folder.
It writes every system call of the game's start. It is much slower, since each line is on the card before the game goes on.

## Advanced options

> [!WARNING]
> Advanced options change how a port runs. They are there for looking into a game that doesn't work, and are used at your own
> risk; say that you used one if you report a problem.

| Option | What it does |
|---|---|
| `--long-log` (of `make` and `install`) | The port writes its detailed log from its first run. Much slower, and a large file. Delete `debug` from the port's folder to turn it off |

The window has the same under **Advanced**.

## Known limits

- **Memory.** A game that asks for nearly all of the console's memory is left with a few MB to spare.
- **Host game.** To the system, the running application is the game the homebrew menu was started over. What depends on that
  game's permissions may vary with it.
- **Not read:** tickets tied to a console, NCA2, sparse RomFS.
- Only a Switch game on a Switch. A Switch game doesn't run on a Switch 2 through this tool.

## Related

[nx2-relink](https://github.com/Olmectron/nx2-relink) is a fork of this project that does the same for **Switch 2** games on a
Switch 2.

`python tools/build.py guest` builds a small test game of the project's own (`guest/`, no Nintendo code), to try a setup without a
game.

## License

[MIT](LICENSE). The software is provided "as is", without warranty of any kind.
