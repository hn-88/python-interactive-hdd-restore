The script copies raw bytes and doesn't care about the filesystem, so a Linux disk (ext4, EFI partition, GPT) images the same way as a Windows NTFS one. The real proof is whether the restored copy boots.

**Setup**

1. Create or reuse an **ARM64** Linux VM in Fusion (Ubuntu or Debian arm64), since x86 Linux won't boot on your Apple Silicon host. Give it a small disk, around 8 to 10 GB, installed normally with UEFI boot.
2. Boot it once and confirm it works, then **shut it down completely** (not suspend).
3. Open the Windows VM's settings and use *Add Device → Hard Disk → Use an existing virtual disk*. Pick the Linux VM's `.vmdk` file and choose the option to make a **separate copy**, not to share the original. That way the original stays untouched. It also avoids the file locking problem that comes from two VMs using one disk.

**Test sequence in Windows (Administrator terminal)**

```
python diskimage.py list
python diskimage.py backup 1 C:\linux.image.gz
python diskimage.py verify C:\linux.image.gz
```

Windows will probably show the ext4 partitions as unformatted or RAW. If it offers to initialize or format them, **decline**. Then:

1. Add a new blank virtual disk of the **same size or larger** to the Windows VM. Don't initialize it.
2. Run `python diskimage.py restore C:\linux.image.gz 2`, using the number of the blank disk.
3. Shut down the Windows VM. Create a new Linux VM in Fusion that uses the restored `.vmdk` as its disk, with UEFI firmware, or swap it into the original Linux VM. Boot it.

If it boots to the login screen with your files intact, the backup and restore are bit-correct.

**Things to watch for**
- **Never boot the original and the restored copy in the same VM at once.** They have identical filesystem UUIDs and partition GUIDs, which confuses the OS.
- **Restoring to a larger disk:** the GPT backup header is then not at the very end of the disk. Linux will usually warn about it and can fix it. If it complains, run `sudo sgdisk -e /dev/sda` (or `parted`) after booting. A restore to an identical-size disk avoids this.
- **A larger target disk:** the extra space stays unallocated, as with `dd`.

When you're ready to test the real use case, repeat the same steps with a **Windows 11 VM disk** as the source. That exercises NTFS, the EFI system partition and the BitLocker/TPM questions you would otherwise have to answer later. One caution there: if the source Windows install uses BitLocker or device encryption, the restored copy may ask for a recovery key, so turn that off for the test.
