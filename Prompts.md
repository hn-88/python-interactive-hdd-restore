_Initial prompt for Sonnet 5.5 Medium on Claude.ai_

A colleague wanted software which can make whole disk backups of a bootable hard disk (Windows NTFS) and restore it. When I presented him with
```
sudo dd if=/dev/sdc bs=16M status=progress | pigz -c > waitingvideos.image.gz
gunzip -c waitingvideos.image.gz | dd of=/dev/sdc status=progress
```
he would prefer something which can run on Windows. He also has python on Windows. He seems to have had some problems with Acronis, hence his query.

Can we write a user-friendly python script to recreate the behaviour of dd on Windows?
