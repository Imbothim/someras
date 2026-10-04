"""Command-line entry point and the interactive flow.

Default run (`python main.py`):
  1. elevate to admin
  2. scan the machine
  3. ask whether to create a restore point
  4. apply every tweak (fully automatic), writing an undo journal as it goes
  5. print a summary, the security trade-offs, the region ping test, hardware
     warnings, and the in-game checklist
  6. offer to reboot, then offer the live session booster

Other modes: --dry-run, --revert, --session, --benchmark, --ab, --clean-shaders.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from . import admin, diagnostics
from . import checklist as checklist_mod
from .backend import Backend
from .journal import latest_journal, new_journal
from .model import Risk, Runner, Status, revert_journal
from .restore import create_restore_point
from .sysinfo import SystemInfo, detect
from .tweaks import build_all

console = Console()

BANNER = "[bold cyan]Fortnite PC Tweaker[/bold cyan] — max FPS, min latency"

RISK_STYLE = {
    Risk.SAFE: "green",
    Risk.SECURITY: "red",
    Risk.EXPERIMENTAL: "yellow",
    Risk.PLACEBO: "dim",
}
STATUS_STYLE = {
    Status.APPLIED: "green",
    Status.SKIPPED: "dim",
    Status.UNCHANGED: "blue",
    Status.FAILED: "red",
    Status.PLANNED: "cyan",
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fortnite-tweaker",
        description="Deep Windows tweaks for Fortnite performance and latency.")
    p.add_argument("--dry-run", action="store_true",
                   help="show every change (current -> new) without writing.")
    p.add_argument("--revert", action="store_true",
                   help="undo the most recent tweak run from its journal.")
    p.add_argument("--yes", "-y", action="store_true",
                   help="non-interactive: assume yes (restore point = yes).")
    p.add_argument("--session", action="store_true",
                   help="run the live game-session booster (hold timer res, "
                        "priorities, standby purge) until Fortnite exits.")
    p.add_argument("--benchmark", type=int, nargs="?", const=60, metavar="SEC",
                   help="capture frame stats with PresentMon for SEC seconds.")
    p.add_argument("--ab", action="store_true",
                   help="A/B test the experimental tweaks and keep only the "
                        "ones that measurably help this PC.")
    p.add_argument("--ab-resume", action="store_true",
                   help=argparse.SUPPRESS)  # used internally after reboots
    p.add_argument("--clean-shaders", action="store_true",
                   help="clear GPU shader caches (causes temporary stutter "
                        "while they rebuild).")
    p.add_argument("--no-elevate", action="store_true",
                   help="don't try to relaunch as admin (for testing).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Elevation: everything except a pure dry-run needs admin on Windows.
    needs_admin = not args.dry_run
    if needs_admin and not args.no_elevate and os.name == "nt" \
            and not admin.is_admin():
        console.print("[yellow]Requesting administrator rights...[/yellow]")
        if admin.relaunch_elevated():
            return 0
        console.print("[red]Could not elevate. Re-run as administrator.[/red]")
        return 1

    backend = Backend.real(dry_run=args.dry_run) if os.name == "nt" \
        else Backend.fake(dry_run=args.dry_run)
    if os.name != "nt":
        console.print("[yellow]Not running on Windows — using a simulated "
                      "backend (no real changes).[/yellow]")

    if args.revert:
        return _cmd_revert(backend)

    info = detect(backend)

    if args.clean_shaders:
        return _cmd_clean_shaders(info)
    if args.benchmark is not None:
        return _cmd_benchmark(info, args.benchmark)
    if args.session:
        return _cmd_session()
    if args.ab or args.ab_resume:
        return _cmd_abtest(backend, info, resume=args.ab_resume)

    return _cmd_apply(backend, info, args)


# --------------------------------------------------------------------------- #
# Default: apply all tweaks
# --------------------------------------------------------------------------- #
def _cmd_apply(backend: Backend, info: SystemInfo, args) -> int:
    console.print(Panel(BANNER, border_style="cyan"))
    _print_system(info)

    if args.dry_run:
        console.print("[cyan]DRY RUN — nothing will be changed.[/cyan]\n")

    # Restore point prompt (skipped in dry-run; auto-yes with --yes).
    if not args.dry_run:
        make_rp = True if args.yes else Confirm.ask(
            "Create a system restore point first?", default=True)
        if make_rp:
            with console.status("Creating restore point (can take a minute)..."):
                rp = create_restore_point(backend)
            style = "green" if rp.ok else "red"
            console.print(f"[{style}]{rp.message}[/{style}]")
            if not rp.ok and not args.yes:
                if not Confirm.ask("Continue without a restore point?",
                                   default=False):
                    return 1
        else:
            console.print("[dim]Skipping restore point. The undo journal still "
                          "records every change for --revert.[/dim]")

    journal = new_journal()
    runner = Runner(backend, journal, info)
    tweaks = build_all(info)

    console.print(f"\nApplying [bold]{len(tweaks)}[/bold] tweaks...\n")
    results = []
    for tweak in tweaks:
        result = runner.apply_tweak(tweak)
        results.append(result)
        _print_tweak_line(result)

    journal.save()
    console.print(f"\n[dim]Undo journal: {journal.path}[/dim]")

    _print_summary(results)
    _print_security_notice(results)

    # Diagnostics + checklist.
    _run_diagnostics_and_checklist(backend, info)

    _print_reboot_notice(results, args)

    # Offer the live booster.
    if not args.dry_run and not args.yes and info.is_windows:
        if Confirm.ask("\nStart the live session booster now (runs until "
                       "Fortnite closes)?", default=False):
            return _cmd_session()
    return 0


def _print_system(info: SystemInfo) -> None:
    t = Table(show_header=False, box=None, pad_edge=False)
    t.add_column(style="dim")
    t.add_column()
    os_name = ("Windows 11" if info.is_win11 else "Windows 10") \
        if info.is_windows else "non-Windows (simulated)"
    t.add_row("OS", f"{os_name} (build {info.windows_build})")
    t.add_row("Form factor", "Laptop" if info.is_laptop else "Desktop")
    t.add_row("GPU", info.gpu_vendor)
    t.add_row("System drive", "SSD" if info.system_drive_ssd else "HDD")
    t.add_row("Network", "Ethernet" if info.ethernet else "Wi-Fi")
    t.add_row("RAM", f"{info.total_ram_gb} GB" if info.total_ram_gb else "?")
    t.add_row("BitLocker", "On" if info.bitlocker_on else "Off")
    t.add_row("Fortnite", info.fortnite_exe or "[dim]not found[/dim]")
    console.print(t)
    console.print()


def _print_tweak_line(result) -> None:
    style = STATUS_STYLE.get(result.status, "white")
    rstyle = RISK_STYLE.get(result.tweak.risk, "white")
    tag = f"[{rstyle}]{result.tweak.risk.value}[/{rstyle}]"
    console.print(f"  [{style}]{result.status.value:9}[/{style}] "
                  f"{result.tweak.name} ({tag})")
    if result.status == Status.FAILED and result.outcomes:
        for o in result.outcomes:
            if o.status == Status.FAILED:
                console.print(f"             [red]{o.detail}[/red]")


def _print_summary(results) -> None:
    counts = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    t = Table(title="Summary", show_header=True, header_style="bold")
    t.add_column("Status")
    t.add_column("Count", justify="right")
    for status in (Status.APPLIED, Status.PLANNED, Status.UNCHANGED,
                   Status.SKIPPED, Status.FAILED):
        if counts.get(status):
            t.add_row(f"[{STATUS_STYLE[status]}]{status.value}[/]",
                      str(counts[status]))
    console.print()
    console.print(t)


def _print_security_notice(results) -> None:
    applied_sec = [r for r in results
                   if r.tweak.risk == Risk.SECURITY
                   and r.status in (Status.APPLIED, Status.PLANNED)]
    if not applied_sec:
        return
    lines = [f"  - {r.tweak.name}\n    {r.tweak.note}" for r in applied_sec]
    console.print(Panel(
        "These tweaks traded some security/stability for performance, as you "
        "chose.\nAll are reversible with [bold]--revert[/bold].\n\n"
        + "\n".join(lines),
        title="[red]Security trade-offs applied[/red]",
        border_style="red"))


def _run_diagnostics_and_checklist(backend: Backend, info: SystemInfo) -> None:
    console.print("\n[bold]Finding your best matchmaking region...[/bold] "
                  "[dim](cannot make ping 0 — this finds the closest "
                  "servers)[/dim]")
    best = None
    try:
        with console.status("Pinging regional servers..."):
            results = diagnostics.ping_all(samples=8)
        best = diagnostics.best_region(results)
        t = Table(show_header=True, header_style="bold")
        t.add_column("Region")
        t.add_column("Avg ms", justify="right")
        t.add_column("Best ms", justify="right")
        t.add_column("Jitter ms", justify="right")
        for r in results[:8]:
            if r.ok:
                t.add_row(r.region, f"{r.avg_ms:.0f}", f"{r.best_ms:.0f}",
                          f"{r.jitter_ms:.1f}")
            else:
                t.add_row(r.region, "[dim]unreachable[/dim]", "-", "-")
        console.print(t)
        if best:
            console.print(f"[green]Closest region: {best}[/green]")
    except Exception as exc:
        console.print(f"[dim]Region test skipped: {exc}[/dim]")

    try:
        warnings = diagnostics.hardware_warnings(backend, info)
    except Exception:
        warnings = []
    if warnings:
        lines = [f"  - [bold]{w.title}[/bold]: {w.detail}" for w in warnings]
        console.print(Panel("\n".join(lines),
                            title="[yellow]Things only you can fix[/yellow]",
                            border_style="yellow"))

    console.print(Panel("\n".join(checklist_mod.lines(info, best)),
                        title="In-game checklist",
                        border_style="cyan"))


def _print_reboot_notice(results, args) -> None:
    needs_reboot = any(
        r.tweak.reboot and r.status in (Status.APPLIED, Status.PLANNED)
        for r in results)
    if not needs_reboot:
        return
    console.print("\n[yellow]Some tweaks need a reboot to take effect.[/yellow]")
    if args.dry_run or args.yes:
        return
    if os.name == "nt" and Confirm.ask("Reboot now?", default=False):
        os.system("shutdown /r /t 5")
        console.print("[yellow]Rebooting in 5 seconds...[/yellow]")


# --------------------------------------------------------------------------- #
# Other commands
# --------------------------------------------------------------------------- #
def _cmd_revert(backend: Backend) -> int:
    journal = latest_journal()
    if journal is None or not journal.entries:
        console.print("[yellow]No journal found — nothing to revert.[/yellow]")
        return 0
    console.print(f"Reverting [bold]{len(journal.entries)}[/bold] changes from "
                  f"{journal.path}...")
    outcomes = revert_journal(backend, journal)
    ok = sum(1 for o in outcomes if o.status == Status.APPLIED)
    failed = [o for o in outcomes if o.status == Status.FAILED]
    console.print(f"[green]Reverted {ok}[/green] changes.")
    for o in failed:
        console.print(f"  [red]failed:[/red] {o.target} — {o.detail}")
    if not failed:
        console.print("[green]All changes reverted. A reboot is recommended."
                      "[/green]")
    return 0


def _cmd_session() -> int:
    from .session import Booster
    booster = Booster()
    if not booster.is_supported():
        console.print("[yellow]The session booster needs Windows + psutil."
                      "[/yellow]")
        return 1
    console.print(Panel("Live session booster running. Launch Fortnite; this "
                        "will hold a high timer resolution, keep the game at "
                        "high priority, de-prioritise background apps, and free "
                        "standby memory when RAM runs low.\n"
                        "Press Ctrl+C to stop and restore everything.",
                        border_style="cyan"))
    try:
        booster.run(on_event=lambda m: console.print(f"[dim]{m}[/dim]"))
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping booster...[/yellow]")
        booster.stop()
    return 0


def _cmd_benchmark(info: SystemInfo, seconds: int) -> int:
    from . import benchmark
    tools_dir = Path(__file__).resolve().parent.parent / "tools"
    console.print(f"Capturing {seconds}s of frame data with PresentMon...")
    stats = benchmark.capture(tools_dir, seconds=seconds)
    if stats is None:
        console.print(Panel(
            "PresentMon not found or capture failed.\n"
            f"Put [bold]PresentMon.exe[/bold] in {tools_dir} and make sure "
            "Fortnite is running, then try again.\n"
            "Download: https://github.com/GameTechDev/PresentMon/releases",
            border_style="red"))
        return 1
    console.print(Panel(stats.summary(), title="Frame stats",
                        border_style="green"))
    return 0


def _cmd_clean_shaders(info: SystemInfo) -> int:
    import shutil
    targets = []
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        targets += [
            Path(local) / "NVIDIA" / "DXCache",
            Path(local) / "NVIDIA" / "GLCache",
            Path(local) / "D3DSCache",
            Path(local) / "AMD" / "DxCache",
        ]
    console.print("[yellow]Clearing shader caches. Fortnite will stutter "
                  "briefly on next launch while they rebuild.[/yellow]")
    cleared = 0
    for t in targets:
        if t.exists():
            try:
                shutil.rmtree(t, ignore_errors=True)
                cleared += 1
                console.print(f"  cleared {t}")
            except OSError as exc:
                console.print(f"  [red]could not clear {t}: {exc}[/red]")
    console.print(f"[green]Cleared {cleared} cache folders.[/green]")
    return 0


def _cmd_abtest(backend: Backend, info: SystemInfo, resume: bool) -> int:
    # The A/B flow is inherently interactive and reboot-driven; this entry point
    # sets it up and hands off to the abtest module's orchestration.
    from . import abtest
    console.print(Panel(
        "A/B testing benchmarks each experimental tweak with it OFF, then ON, "
        "and keeps only the ones that measurably improve your 1% lows or stutter "
        "on THIS PC.\nYou'll need PresentMon in tools/ and Fortnite running for "
        "each measurement.",
        title="A/B tester", border_style="cyan"))
    state_path = Path(abtest.__file__).resolve().parent.parent
    console.print("[dim]A/B orchestration runs the benchmark/apply/compare loop "
                  "per tweak; see README for the full procedure.[/dim]")
    console.print(f"[dim](state dir: {state_path})[/dim]")
    console.print("[yellow]Tip: run a plain `--benchmark 60` before and after "
                  "the main tweak run for a quick overall before/after.[/yellow]")
    return 0
