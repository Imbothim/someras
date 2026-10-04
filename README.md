# Fortnite PC Tweaker

A Python terminal tool that deep-tweaks **Windows** for the highest Fortnite FPS
and the lowest input latency — then proves what actually helped on *your* PC.

It is **fully automatic**: run it, answer one question (make a restore point?),
and it applies the whole tweak set. Every single change is written to an undo
journal first, so `python main.py --revert` puts your system back exactly the
way it was.

```bash
python main.py            # the full automatic tweak run (asks to make a restore point)
python main.py --dry-run  # preview every change (current -> new), write nothing
python main.py --revert   # undo the most recent run, change by change
python main.py --session  # live booster: run it, then launch Fortnite
python main.py --benchmark 60   # measure avg FPS + 1% lows (needs PresentMon)
python main.py --clean-shaders  # clear shader caches (optional; causes brief stutter)
```

You need **Windows 10/11**, **Python 3.9+**, and admin rights (the tool
self-elevates via UAC). Install dependencies with:

```bash
pip install -r requirements.txt
```

## Read this first — honest expectations

Some popular "zero ping / zero delay" claims are not physically possible. Here is
the truth, which the tool also prints at the end of a run:

- **Ping cannot be 0.** Your ping is mostly the distance and network route to
  Epic's servers. No registry edit changes the speed of light. What this tool
  *can* do is remove **local** causes of lag spikes and jitter (NIC power
  saving, interrupt batching, background downloads) and **measure which
  matchmaking region is actually closest to you**.
- **"Zero delay" is a feeling, not a setting.** The biggest real reductions in
  input latency come from: running your monitor at its **true max refresh rate**,
  a high and stable FPS, **NVIDIA Reflex On+Boost** (or AMD Anti-Lag) *in-game*,
  and turning off VBS/Memory Integrity. The tool does the Windows-side parts and
  prints an in-game checklist for the rest.
- **Some classic tweaks do almost nothing.** Disabling Nagle's algorithm, for
  example, only affects **TCP**, and Fortnite gameplay runs over **UDP**. It's
  included because you asked for everything, but it's labelled honestly as
  `placebo` in the output.
- **The tool never touches Fortnite's files, memory, or Easy Anti-Cheat.** Doing
  so is a ban risk. All changes are to Windows itself.

## What it changes

Tweaks are grouped by category and tagged by risk:

| Risk | Meaning |
|------|---------|
| `safe` | Reversible, no security cost. |
| `security` | Trades some security/stability for speed (your choice). |
| `experimental` | Effect varies per machine — use `--benchmark` to verify. |
| `placebo` | Included for completeness; little or no real effect. |

**Power** — Ultimate Performance plan, no CPU core parking, 100% min processor
state, USB selective suspend off, PCIe ASPM off, power throttling off,
hibernation/Fast Startup off, (desktop) CPU idle disable, aggressive boost and
EPP = 0.

**Input** — mouse acceleration off (the single most important one), fastest key
repeat, accessibility hotkey popups off, a conservative input-queue bump.

**Graphics & scheduling** — Game Mode on, Game DVR/capture off, fullscreen
optimization flags, HAGS on, MMCSS game priorities, `Win32PrioritySeparation`,
global timer resolution (Win11), **primary display forced to its max refresh
rate**, and Fortnite pinned to the discrete GPU at High CPU/IO priority.

**System (security trade-offs + low-level)** — VBS/Memory Integrity off,
Spectre/Meltdown mitigations off, Virtual Machine Platform off (skipped if you
use WSL/Docker/Hyper-V), a Defender exclusion for the Fortnite folder, bcdedit
timer tuning (BitLocker handled automatically), `DisablePagingExecutive`, NTFS
last-access/8.3 off, memory compression off (16 GB+), MPO off, kernel timer
distribution, and GPU driver keys to hold max clocks.

**Network** — NIC power management off, Energy-Efficient/Green Ethernet off,
interrupt moderation off, offloads off, RSS on, Delivery Optimization P2P off,
packet coalescing off, DSCP tagging for Fortnite, and Nagle off (`placebo`,
TCP-only).

**Background** — background apps off, visual effects set to performance,
transparency off, telemetry services (DiagTrack, etc.) disabled, telemetry
scheduled tasks disabled, SysMain off (SSD only).

**Diagnostics** (read-only, nothing changed) — a region ping/jitter test to find
your closest servers, plus warnings for the things only *you* can fix: RAM below
its rated speed (enable XMP/EXPO), single-channel RAM, Wi-Fi instead of Ethernet,
laptop on battery, an old GPU driver, or Fortnite on an HDD.

## Reverting

Two safety nets:

1. **Undo journal** — before changing any value, the tool records the old value
   to `%ProgramData%\FortniteTweaker\journal-<timestamp>.json`. `--revert` reads
   the latest journal and restores every value exactly (including deleting
   values that didn't exist before). This works even after a reboot.
2. **System restore point** — created at the start if you say yes.

After reverting, reboot so kernel-level changes fully reset.

## The live session booster

Some wins only exist while a process holds them, so run this, then launch
Fortnite:

```bash
python main.py --session
```

While Fortnite is open it holds a 0.5 ms timer resolution, keeps the game at High
priority with power throttling off, de-prioritises background hogs (browsers,
launchers — never kills them), and frees the standby memory list when RAM runs
low to prevent texture-streaming stutter. Everything is restored when Fortnite
closes or you press Ctrl+C.

## Measuring the difference (so you're not guessing)

Install [PresentMon](https://github.com/GameTechDev/PresentMon/releases) and drop
`PresentMon.exe` in the `tools/` folder. Then, with Fortnite running:

```bash
python main.py --benchmark 60   # before the tweaks
# ...run the tweaks, reboot...
python main.py --benchmark 60   # after
```

Compare the **1% low FPS** and spike count, not just the average — that's where
tweaks actually show up. Benchmark on a repeatable scene (the same Creative map
or a saved replay), not live matches.

## Safety notes

- The `security`-tagged tweaks genuinely lower some protections. They're applied
  because you asked for maximum performance; the summary lists exactly which ran,
  and `--revert` undoes them.
- Removing Spectre/Meltdown mitigations mostly helps older Intel CPUs; on recent
  hardware the FPS gain is negligible, but the security cost is the same.
- This tool makes no changes to Fortnite or Easy Anti-Cheat and does not inject
  into or read game memory.

## Development

Pure logic (capture → apply → revert, dry-run, guards, journal, stats) is unit
tested with a fake registry/command backend, so it runs on any OS:

```bash
pip install pytest
pytest
```

The Windows-only pieces (registry writes, ctypes display/timer calls, PowerShell)
are isolated behind the backend and degrade to no-ops off Windows.
