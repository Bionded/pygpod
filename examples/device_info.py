"""Show iPod device information and capabilities.

Detects the device model from a mount point and displays its
properties. Can also scan for connected iPods using auto-discovery.
"""

from __future__ import annotations

import argparse
import sys

import pygpod


def show_device(mountpoint: str) -> int:
    """Show detailed device info for a specific mount point."""
    device = pygpod.Device.from_mountpoint(mountpoint)

    if device.is_unknown:
        return _handle_unknown_device(mountpoint, device)

    if device.usb_enriched:
        print("NOTE: Device identified via USB (no SysInfo file found).")
        print("      Run with --write-sysinfo to save for future use.")
        print()

    _print_device_info(device)
    return 0


def _print_device_info(device) -> None:
    """Print all device properties."""
    print(f"Mount point:     {device.mountpoint}")
    print(f"Model:           {device.model}")
    print(f"Generation:      {device.generation.name}")
    print(f"FireWire GUID:   {device.firewire_guid or 'N/A'}")
    print()
    print("Capabilities:")
    print(f"  Artwork:       {'Yes' if device.supports_artwork else 'No'}")
    print(f"  Video:         {'Yes' if device.supports_video else 'No'}")
    print(f"  Podcast:       {'Yes' if device.supports_podcast else 'No'}")
    print(f"  Shuffle:       {'Yes' if device.is_shuffle else 'No'}")
    print()
    print("Hash/checksum:")
    print(f"  Requires hash: {'Yes' if device.requires_hash else 'No'}")
    print(f"  Checksum type: {device.checksum_type.name}")
    print(f"  DB version:    {device.db_version}")
    print()
    print("Paths:")
    print(f"  iTunes dir:    {device.itunes_dir}")
    print(f"  Music dir:     {device.music_dir}")
    print(f"  Artwork dir:   {device.artwork_dir}")
    print(f"  Music dirs:    {device.music_dirs}")


def _handle_unknown_device(mountpoint: str, device) -> int:
    """Show guidance when device model cannot be determined."""
    print("ERROR: Could not determine iPod model at: " + mountpoint)
    print()

    has_pyusb = False
    try:
        import usb.core  # noqa: F401

        has_pyusb = True
    except ImportError:
        pass

    if not has_pyusb:
        print("TIP: Install pyusb for automatic USB device detection:")
        print("  pip install pygpod[usb]")
        print()

    _print_sysinfo_instructions(mountpoint)
    return 1


def _print_sysinfo_instructions(mountpoint: str) -> None:
    """Print instructions for creating a SysInfo file manually."""
    sysinfo_path = mountpoint.rstrip("/") + "/iPod_Control/Device/SysInfo"

    print("To identify your iPod, create a SysInfo file:")
    print(f"  {sysinfo_path}")
    print()
    print("Step 1 - Find your model number:")
    print("  Look on the back of the iPod for 'Model Axxxx'")
    print("  The last 4 characters are the model code (e.g. 'A1238' -> B029)")
    print("  Or check Settings > About on the iPod itself")
    print()
    print("Step 2 - Find the FireWire GUID (needed for iPod Video and newer):")
    print("  Linux:  cat /sys/bus/usb/devices/*/serial")
    print("  macOS:  System Information > USB > iPod > Serial Number")
    print("  The first 16 hex characters of the USB serial = FireWire GUID")
    print()
    print("Step 3 - Create the file with this content:")
    print()
    print("  ModelNumStr: xB029")
    print("  FirewireGuid: 000A27001301297E")
    print()
    print("Common model numbers:")
    print("  B029/B147  iPod Classic 80GB (6G)")
    print("  B562/B565  iPod Classic 120GB")
    print("  C293/C297  iPod Classic 160GB")
    print("  A002/A146  iPod Video 30GB")
    print("  A444/A446  iPod Video 30GB (5.5G)")
    print("  A448/A450  iPod Video 80GB (5.5G)")
    print("  A978/A980  iPod Nano 3G")
    print("  B480/B598  iPod Nano 4G")
    print("  C027/C031  iPod Nano 5G")
    print("  9160       iPod Mini 4GB")
    print("  9724/9725  iPod Shuffle 1G")


def write_sysinfo_cmd(mountpoint: str) -> int:
    """Detect device and write SysInfo file."""
    device = pygpod.Device.from_mountpoint(mountpoint)

    if device.is_unknown:
        print("ERROR: Could not detect device info to write.")
        print("No model information available from USB or existing SysInfo.")
        print()
        _print_sysinfo_instructions(mountpoint)
        return 1

    path = device.write_sysinfo()
    print(f"SysInfo written to: {path}")
    print()
    _print_device_info(device)
    return 0


def discover_devices() -> int:
    """Scan for connected iPods."""
    print("Scanning for connected iPods...")
    results = pygpod.discover()

    if not results:
        print("No iPods found.")
        return 0

    print(f"Found {len(results)} iPod(s):\n")
    for mountpoint, device in results:
        status = ""
        if device.is_unknown:
            status = " [unknown model - run with mountpoint for details]"
        elif device.usb_enriched:
            status = " [detected via USB - no SysInfo]"
        print(f"  {mountpoint}")
        print(f"    Model:  {device.model}{status}")
        print(f"    GUID:   {device.firewire_guid or 'N/A'}")
        print()

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show iPod device information")
    parser.add_argument("mountpoint", nargs="?", help="iPod mount point")
    parser.add_argument("--discover", action="store_true", help="Scan for connected iPods")
    parser.add_argument(
        "--write-sysinfo",
        action="store_true",
        help="Write SysInfo file from detected device info (USB or existing data)",
    )
    args = parser.parse_args(argv)

    if args.discover:
        return discover_devices()

    if args.mountpoint:
        if args.write_sysinfo:
            return write_sysinfo_cmd(args.mountpoint)
        return show_device(args.mountpoint)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
