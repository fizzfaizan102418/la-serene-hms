# La Serene HMS — Local Backup Operations

La Serene HMS now has a three-layer local backup workflow for the Windows/PostgreSQL deployment:

1. **Automatic local backup:** Windows Task Scheduler runs a verified PostgreSQL backup every day at 02:00.
2. **Historical retention:** the local Backup & Restore directory keeps the latest **7 verified backups**. Each backup has a SHA-256 manifest so a damaged or incomplete file is not treated as a valid backup.
3. **External/network mirror:** when `HMS_BACKUP_MIRROR_DIR` is configured, every successful local backup is copied atomically to the external/NAS/network location and the mirror keeps the latest **30 verified backups**.

The scheduled backup directory is the same directory used by the Backup & Restore screen: `data\\backups`.

## Configure an external backup location

Do not commit the production `.env` file to Git. On the hotel production machine, add this line to:

`D:\\LaSereneHMS\\apps\\api\\.env`

```text
HMS_BACKUP_MIRROR_DIR=E:\\LaSereneHMS-Backups
```

The path can point to a USB/external drive or a durable network/NAS location. Use a stable path that is available to the Windows scheduled-task account.

## Install or refresh the scheduled task

Run PowerShell as Administrator on the hotel computer after pulling the latest `main` branch:

```powershell
cd D:\\LaSereneHMS
.\\scripts\\windows\\install-backup-task.ps1 `
  -InstallRoot D:\\LaSereneHMS `
  -RunAsSystem `
  -Retain 7 `
  -MirrorRetain 30
```

The script reads `HMS_BACKUP_MIRROR_DIR` from the production `.env` when `-MirrorDir` is not supplied.

## Test immediately

```powershell
Start-ScheduledTask -TaskName "La Serene HMS - PostgreSQL Backup"
```

Then inspect:

- `D:\\LaSereneHMS\\data\\backups\\` for the local `.dump` and `.manifest.json` files.
- The configured mirror directory for the copied `.dump` and manifest.
- `D:\\LaSereneHMS\\data\\backups\\backup.log` for scheduled-run details.

A successful run reports `status=verified`. A mirror failure does not erase the valid local backup; it is logged and the scheduled task returns failure so the issue can be noticed and corrected.

## Hotel staff workflow

Hotel staff do not need to understand PostgreSQL or `.dump` files.

- **Backup & Restore → Create backup:** manual backup when desired.
- **Automatic backup:** normally happens without staff action each night.
- **Download:** saves a selected verified backup for an additional copy when needed.
- **Restore:** replaces the current database from a verified backup and creates a safety backup before replacement.

The `.dump` file is only the technical storage format underneath the simple hotel workflow.

## Future cloud phase

The same backup engine can later be extended to a cloud object-storage target with scheduled uploads, retention, integrity verification, and one-click restore. The local workflow remains useful as an additional recovery layer.
