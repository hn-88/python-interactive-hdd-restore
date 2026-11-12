#!/usr/bin/env python3
"""
diskimage.py - whole-disk backup / restore for Windows (a user-friendly
replacement for `dd | pigz` and `gunzip | dd`).

* Needs only the Python standard library (Python 3.8+).
* Must be run from an *Administrator* command prompt / PowerShell.
* Images are ordinary .gz files (multi-member gzip, like pigz produces), so
  they can also be restored on Linux with:  gunzip -c image.gz | dd of=/dev/sdX
* Compression runs on all CPU cores.
* Tested on Windows 11 ARM VM.

Usage:
    python diskimage.py                       (interactive menu)
    python diskimage.py list
    python diskimage.py backup  <disk#> <image.gz> [--level 6]
    python diskimage.py restore <image.gz> <disk#>
    python diskimage.py verify  <image.gz>
"""
import argparse
import ctypes
import gzip
import hashlib
import json
import os
import subprocess
import sys
import time
import zlib
from collections import deque
from concurrent.futures import ThreadPoolExecutor

CHUNK = 16 * 1024 * 1024          # same as bs=16M
SECTOR_PAD = 4096                 # pad a short final write to this multiple


# --------------------------------------------------------------------------
# Windows helpers
# --------------------------------------------------------------------------
def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def dev_path(n):
    return r"\\.\PhysicalDrive%d" % n


def powershell(cmd):
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "PowerShell command failed")
    return r.stdout


def list_disks():
    out = powershell(
        "Get-Disk | Select-Object Number,FriendlyName,Size,PartitionStyle,"
        "BusType,IsSystem,IsBoot | ConvertTo-Json")
    data = json.loads(out) if out.strip() else []
    return [data] if isinstance(data, dict) else data


def get_disk(n):
    for d in list_disks():
        if d["Number"] == n:
            return d
    raise SystemExit("Disk %d not found." % n)


def disk_length(f):
    """Size in bytes of an opened physical drive (IOCTL_DISK_GET_LENGTH_INFO)."""
    import msvcrt
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.DeviceIoControl.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID]
    out = ctypes.c_longlong(0)
    ret = wintypes.DWORD(0)
    ok = k32.DeviceIoControl(
        msvcrt.get_osfhandle(f.fileno()), 0x0007405C, None, 0,
        ctypes.byref(out), 8, ctypes.byref(ret), None)
    if not ok:
        raise OSError("Could not determine disk size: %s" % ctypes.WinError())
    return out.value


def set_offline(n, offline):
    powershell("Set-Disk -Number %d -IsOffline $%s" % (n, str(offline).lower()))


# --------------------------------------------------------------------------
# Progress display
# --------------------------------------------------------------------------
class Progress:
    def __init__(self, total, label):
        self.total, self.label = total, label
        self.start = self.last = time.time()

    def update(self, done, force=False):
        now = time.time()
        if not force and now - self.last < 0.5:
            return
        self.last = now
        el = max(now - self.start, 1e-6)
        rate = done / el
        pct = 100.0 * done / self.total if self.total else 0
        eta = (self.total - done) / rate if rate and self.total else 0
        sys.stdout.write(
            "\r%s %5.1f%%  %s / %s  %6.1f MB/s  ETA %s   " % (
                self.label, pct, human(done), human(self.total),
                rate / 1e6, time.strftime("%H:%M:%S", time.gmtime(eta))))
        sys.stdout.flush()

    def finish(self, done):
        self.update(done, force=True)
        print()


def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or u == "TB":
            return "%.1f %s" % (n, u) if u != "B" else "%d B" % n
        n /= 1000.0


# --------------------------------------------------------------------------
# Backup
# --------------------------------------------------------------------------
def _compress(block):
    c = zlib.compressobj(LEVEL, zlib.DEFLATED, 31)   # 31 = gzip container
    return c.compress(block) + c.flush()


def read_exact(f, n):
    parts = []
    while n:
        b = f.read(n)
        if not b:
            raise OSError("Unexpected end of disk")
        parts.append(b)
        n -= len(b)
    return b"".join(parts)


def backup(disk_no, image, level=6):
    global LEVEL
    LEVEL = level
    d = get_disk(disk_no)
    if d.get("IsSystem") or d.get("IsBoot"):
        print("WARNING: disk %d is the disk Windows is running from. The image "
              "will not be consistent." % disk_no)
    if os.path.exists(image):
        raise SystemExit("%s already exists - refusing to overwrite." % image)
    workers = max(1, os.cpu_count() or 1)
    tmp = image + ".part"
    sha = hashlib.sha256()
    print("Backing up disk %d (%s, %s) -> %s" %
          (disk_no, d["FriendlyName"], human(d["Size"]), image))
    with open(dev_path(disk_no), "rb", buffering=0) as src:
        size = disk_length(src)
        prog = Progress(size, "Backup")
        pending, pos = deque(), 0
        with ThreadPoolExecutor(workers) as ex, open(tmp, "wb") as dst:
            while pos < size:
                block = read_exact(src, min(CHUNK, size - pos))
                sha.update(block)
                pending.append(ex.submit(_compress, block))
                pos += len(block)
                while len(pending) >= workers * 2:
                    dst.write(pending.popleft().result())
                prog.update(pos)
            while pending:
                dst.write(pending.popleft().result())
        prog.finish(pos)
    os.replace(tmp, image)
    with open(image + ".info.json", "w") as f:
        json.dump({"bytes": size, "sha256": sha.hexdigest(),
                   "source": d["FriendlyName"]}, f, indent=2)
    print("Done. Image: %s (%s)" % (image, human(os.path.getsize(image))))


# --------------------------------------------------------------------------
# Restore
# --------------------------------------------------------------------------
def read_info(image):
    try:
        with open(image + ".info.json") as f:
            return json.load(f)
    except OSError:
        return {}


def restore(image, disk_no, assume_yes=False):
    if not os.path.isfile(image):
        raise SystemExit("Image not found: %s" % image)
    d = get_disk(disk_no)
    if d.get("IsSystem") or d.get("IsBoot"):
        raise SystemExit("Refusing: disk %d is the system/boot disk of this PC."
                         % disk_no)
    info = read_info(image)
    if info.get("bytes") and info["bytes"] > d["Size"]:
        raise SystemExit("Target disk (%s) is smaller than the image (%s)." %
                         (human(d["Size"]), human(info["bytes"])))
    print("\n*** ALL DATA ON DISK %d WILL BE DESTROYED ***" % disk_no)
    print("    %s, %s" % (d["FriendlyName"], human(d["Size"])))
    if not assume_yes:
        if input("Type the disk number (%d) to confirm: " % disk_no).strip() \
                != str(disk_no):
            raise SystemExit("Cancelled.")

    print("Taking disk offline ...")
    set_offline(disk_no, True)
    total = info.get("bytes") or d["Size"]
    written = 0
    try:
        with gzip.open(image, "rb") as src, \
                open(dev_path(disk_no), "r+b", buffering=0) as dst:
            size = disk_length(dst)
            prog = Progress(total, "Restore")
            while True:
                block = src.read(CHUNK)
                if not block:
                    break
                if len(block) % SECTOR_PAD:
                    block += b"\0" * (SECTOR_PAD - len(block) % SECTOR_PAD)
                if written + len(block) > size:
                    raise SystemExit("\nImage is larger than the target disk.")
                dst.write(block)
                written += len(block)
                prog.update(written)
            prog.finish(written)
    finally:
        print("Bringing disk back online ...")
        try:
            set_offline(disk_no, False)
            powershell("Update-HostStorageCache")
        except Exception as e:
            print("Could not bring disk online automatically: %s" % e)
    print("Restore finished.")


# --------------------------------------------------------------------------
# Verify (image integrity + checksum from the sidecar file)
# --------------------------------------------------------------------------
def verify(image):
    info = read_info(image)
    sha, n = hashlib.sha256(), 0
    total = info.get("bytes") or os.path.getsize(image) * 3
    prog = Progress(total, "Verify")
    with gzip.open(image, "rb") as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            sha.update(b)
            n += len(b)
            prog.update(n)
    prog.finish(n)
    if info.get("sha256"):
        ok = sha.hexdigest() == info["sha256"]
        print("Checksum:", "OK - image is intact" if ok else "MISMATCH - image is damaged!")
        return ok
    print("Gzip stream is readable (no .info.json checksum available).")
    return True


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------
def show_disks():
    print("\n  #  %-34s %10s  %-6s %-5s %s" %
          ("Name", "Size", "Style", "Bus", ""))
    for d in list_disks():
        flag = "<-- SYSTEM DISK" if d.get("IsSystem") or d.get("IsBoot") else ""
        print("%3d  %-34s %10s  %-6s %-5s %s" % (
            d["Number"], str(d["FriendlyName"])[:34], human(d["Size"]),
            d["PartitionStyle"], d["BusType"], flag))
    print()


def ask_disk():
    show_disks()
    return int(input("Disk number: ").strip())


def menu():
    while True:
        print("=== Disk imaging ===\n 1) List disks\n 2) Backup a disk to an image"
              "\n 3) Restore an image to a disk\n 4) Verify an image\n 0) Quit")
        c = input("> ").strip()
        try:
            if c == "1":
                show_disks()
            elif c == "2":
                n = ask_disk()
                backup(n, input("Image file (e.g. D:\\disk.image.gz): ").strip('" '))
            elif c == "3":
                img = input("Image file: ").strip('" ')
                restore(img, ask_disk())
            elif c == "4":
                verify(input("Image file: ").strip('" '))
            elif c == "0":
                return
        except (SystemExit, Exception) as e:
            print("\nERROR:", e, "\n")


def main():
    if os.name != "nt":
        sys.exit("This script is for Windows. On Linux use dd + pigz.")
    if not is_admin():
        sys.exit("Please run this from an Administrator terminal "
                 "(right-click -> Run as administrator).")
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("list")
    b = sub.add_parser("backup")
    b.add_argument("disk", type=int)
    b.add_argument("image")
    b.add_argument("--level", type=int, default=6, choices=range(1, 10))
    r = sub.add_parser("restore")
    r.add_argument("image")
    r.add_argument("disk", type=int)
    r.add_argument("--yes", action="store_true", help="skip confirmation")
    v = sub.add_parser("verify")
    v.add_argument("image")
    a = p.parse_args()
    if a.cmd == "list":
        show_disks()
    elif a.cmd == "backup":
        backup(a.disk, a.image, a.level)
    elif a.cmd == "restore":
        restore(a.image, a.disk, a.yes)
    elif a.cmd == "verify":
        sys.exit(0 if verify(a.image) else 1)
    else:
        menu()


LEVEL = 6

if __name__ == "__main__":
    main()
