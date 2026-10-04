"""Network tweaks aimed at lower, steadier latency (not lower ping).

Important honesty note carried into the summary: none of this lowers your base
ping to Epic's servers — that's distance and routing. What it does remove is
*local* jitter: a NIC briefly sleeping, interrupt moderation batching packets,
background downloads stealing bandwidth. Nagle's algorithm tweaks are included
because they're the classic request, but they only affect TCP, and Fortnite
gameplay runs over UDP — so they're labelled honestly.

Advanced-adapter properties are reverted with ``Reset-NetAdapterAdvancedProperty``
(back to the driver default) rather than a byte-exact previous value; that's the
reliable inverse the Windows API gives us.
"""

from __future__ import annotations

from .. import actions as A
from ..model import Category, Risk, Tweak
from ..sysinfo import SystemInfo

TCPIP_IF = r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"

# PowerShell: the set of physical adapters that are currently up.
PHYS_UP = ("Get-NetAdapter -Physical | "
           "Where-Object { $_.Status -eq 'Up' }")


def _adv_property(display_name: str, value: str) -> A.PowerShell:
    """Disable/enable one advanced property across all up physical adapters."""
    return A.PowerShell(
        f"adapter property: {display_name} = {value}",
        apply_script=(
            f"{PHYS_UP} | ForEach-Object {{ "
            f"Set-NetAdapterAdvancedProperty -Name $_.Name "
            f"-DisplayName '{display_name}' -DisplayValue '{value}' "
            f"-ErrorAction SilentlyContinue }}"
        ),
        revert_script=(
            f"{PHYS_UP} | ForEach-Object {{ "
            f"Reset-NetAdapterAdvancedProperty -Name $_.Name "
            f"-DisplayName '{display_name}' -ErrorAction SilentlyContinue }}"
        ),
        desired=value,
    )


def build(info: SystemInfo) -> list[Tweak]:
    tweaks: list[Tweak] = [
        Tweak(
            id="net.adapter_power_off",
            name="Disable NIC power management",
            category=Category.NETWORK,
            risk=Risk.SAFE,
            note="Stops the network card from sleeping (a cause of micro-lag).",
            actions=[A.PowerShell(
                "Disable-NetAdapterPowerManagement",
                apply_script=(
                    f"{PHYS_UP} | Disable-NetAdapterPowerManagement "
                    "-ErrorAction SilentlyContinue"),
                revert_script=(
                    f"{PHYS_UP} | Enable-NetAdapterPowerManagement "
                    "-ErrorAction SilentlyContinue"),
                desired="power mgmt off")],
        ),
        Tweak(
            id="net.eee_off",
            name="Disable Energy Efficient Ethernet / Green Ethernet",
            category=Category.NETWORK,
            risk=Risk.SAFE,
            note="Power-saving features that add latency spikes under load.",
            actions=[
                _adv_property("Energy Efficient Ethernet", "Disabled"),
                _adv_property("Green Ethernet", "Disabled"),
                _adv_property("Power Saving Mode", "Disabled"),
                _adv_property("Advanced EEE", "Disabled"),
                _adv_property("Ultra Low Power Mode", "Disabled"),
            ],
        ),
        Tweak(
            id="net.interrupt_moderation_off",
            name="Disable interrupt moderation",
            category=Category.NETWORK,
            risk=Risk.EXPERIMENTAL,
            note="Delivers packets immediately instead of batching them. Lowers "
                 "latency but raises CPU use slightly.",
            actions=[_adv_property("Interrupt Moderation", "Disabled")],
        ),
        Tweak(
            id="net.offloads_off",
            name="Disable flow control and large send offload",
            category=Category.NETWORK,
            risk=Risk.EXPERIMENTAL,
            note="Can smooth latency on some NICs; A/B test it.",
            actions=[
                _adv_property("Flow Control", "Disabled"),
                _adv_property("Large Send Offload V2 (IPv4)", "Disabled"),
                _adv_property("Large Send Offload V2 (IPv6)", "Disabled"),
            ],
        ),
        Tweak(
            id="net.rss_on",
            name="Enable Receive Side Scaling (RSS)",
            category=Category.NETWORK,
            risk=Risk.SAFE,
            note="Spreads network processing across CPU cores.",
            actions=[A.Command(
                "netsh int tcp set global rss=enabled",
                apply_args=["netsh", "int", "tcp", "set", "global",
                            "rss=enabled"],
                revert_args=["netsh", "int", "tcp", "set", "global",
                             "rss=default"],
                desired="RSS enabled")],
        ),
        Tweak(
            id="net.nagle_off",
            name="Disable Nagle's algorithm (TCP only)",
            category=Category.NETWORK,
            risk=Risk.PLACEBO,
            note="Classic 'tweak', but TCP-only — Fortnite gameplay is UDP, so "
                 "expect little to no effect. Included because you asked for "
                 "everything.",
            actions=[A.PowerShell(
                "Nagle off on all interfaces",
                apply_script=(
                    f"Get-ChildItem 'HKLM:\\{TCPIP_IF}' | ForEach-Object {{ "
                    "New-ItemProperty -Path $_.PSPath -Name TcpAckFrequency "
                    "-Value 1 -PropertyType DWord -Force; "
                    "New-ItemProperty -Path $_.PSPath -Name TCPNoDelay "
                    "-Value 1 -PropertyType DWord -Force }}"),
                revert_script=(
                    f"Get-ChildItem 'HKLM:\\{TCPIP_IF}' | ForEach-Object {{ "
                    "Remove-ItemProperty -Path $_.PSPath -Name TcpAckFrequency "
                    "-ErrorAction SilentlyContinue; "
                    "Remove-ItemProperty -Path $_.PSPath -Name TCPNoDelay "
                    "-ErrorAction SilentlyContinue }}"),
                desired="Nagle off")],
        ),
        Tweak(
            id="net.delivery_opt_off",
            name="Disable Delivery Optimization P2P uploads",
            category=Category.NETWORK,
            risk=Risk.SAFE,
            note="Stops Windows Update using your upload bandwidth to seed "
                 "updates to other PCs.",
            actions=[A.Reg(
                "HKLM",
                r"SOFTWARE\Policies\Microsoft\Windows\DeliveryOptimization",
                "DODownloadMode", 0)],
        ),
        Tweak(
            id="net.packet_coalescing_off",
            name="Disable packet coalescing",
            category=Category.NETWORK,
            risk=Risk.EXPERIMENTAL,
            note="Another latency-vs-CPU batching setting; A/B test it.",
            actions=[A.PowerShell(
                "Set-NetOffloadGlobalSetting PacketCoalescingFilter",
                apply_script="Set-NetOffloadGlobalSetting "
                             "-PacketCoalescingFilter Disabled",
                revert_script="Set-NetOffloadGlobalSetting "
                              "-PacketCoalescingFilter Enabled",
                desired="coalescing off")],
        ),
    ]

    # QoS DSCP tagging for the Fortnite exe (only helps if the router honors it).
    if info.fortnite_exe:
        tweaks.append(Tweak(
            id="net.qos_fortnite",
            name="Tag Fortnite traffic as high priority (DSCP 46)",
            category=Category.NETWORK,
            risk=Risk.EXPERIMENTAL,
            note="Only helps if your router/ISP honours DSCP; harmless if not.",
            actions=[
                A.PowerShell(
                    "New-NetQosPolicy Fortnite",
                    apply_script=(
                        "New-NetQosPolicy -Name 'Fortnite' -AppPathNameMatch "
                        f"'{info.fortnite_exe}' -DSCPAction 46 "
                        "-NetworkProfile All -ErrorAction SilentlyContinue"),
                    revert_script=(
                        "Remove-NetQosPolicy -Name 'Fortnite' -Confirm:$false "
                        "-ErrorAction SilentlyContinue"),
                    desired="DSCP 46 policy"),
                A.Reg("HKLM",
                      r"SOFTWARE\Policies\Microsoft\Windows\Psched",
                      "NonBestEffortLimit", 0),
            ],
        ))
    return tweaks
