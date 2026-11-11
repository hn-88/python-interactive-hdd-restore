`diskimage.py` is a whole disk backup and restore tool which only uses the Python standard library.

**How to use it**

Open a terminal with “Run as administrator” (raw disk access needs it), then run one of these:
```
python diskimage.py                              (interactive menu)
python diskimage.py list
python diskimage.py backup 2 D:\waitingvideos.image.gz
python diskimage.py restore D:\waitingvideos.image.gz 2
python diskimage.py verify D:\waitingvideos.image.gz
```
The disk number is the one shown by list (the same number as in Disk Management).

**How it compares to dd commands on Linux**

Same format: the image is a normal multi-member .gz file, which is what pigz produces. Linux command gunzip -c ... | dd of=/dev/sdc can restore it, and the other direction works too.
Reads and writes: it reads \\.\PhysicalDriveN in 16 MB blocks, like bs=16M. It compresses on all CPU cores, much like pigz.

Progress: it shows percentage, MB/s and ETA.

Backup: it refuses to overwrite an existing image and writes to .part first. It stores a SHA-256 checksum in image.gz.info.json.
Verify: verify re-reads the image and checks it against that checksum, which dd doesn’t give you.

**Safety features**

On restore, the script refuses to touch the system or boot disk. You must type the disk number to confirm, and it refuses if the target is smaller than the image.
During restore it takes the target disk offline (Windows blocks raw writes to mounted volumes) and brings it back online afterwards.

**Caveats**

Unreadable sectors: a read error stops the backup, unlike `dd conv=noerror,sync`. (A mode that zero-fills bad sectors can be added if needed).

Duplicate signatures: if you restore a clone while the original disk is still connected, Windows may keep one of them offline because both have the same disk signature.

Speed: a Python single-reader pipeline should be fast enough for USB or SATA speeds. It won’t match a native tool on NVMe.
