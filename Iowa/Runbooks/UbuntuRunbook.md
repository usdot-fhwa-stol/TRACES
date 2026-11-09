# Ubuntu Startup
**Created By: Zeferino Araiza-Flores**

---

## 1. Booting Up

1. **Power On:** Upon booting the laptop, the GRUB boot menu will appear.
2. **OS Selection:** 
   * **Ubuntu (Default):** Selected automatically after 5 seconds if no key is pressed.
   * **Windows:** Select the **third option** in the menu to boot into Windows.
3. **Login:** Once Ubuntu boots, log in using the default user account (`ubuntu`). The password has been provided securely outside of this document.

---

## 2. Mounting the Hard Drive

To optimize PostgreSQL read/write performance when working with external storage, mount the drive directly to the host filesystem.

1. **Identify the Partition:** Run `lsblk` to identify the drive identifier (typically `/dev/sda1`).
2. **Mount the Drive:** Run the mount command with explicit permissions assigned to the `postgres` user and `ubuntu` group:
   ```bash
   sudo mount -t ntfs3 -o uid=$(id -u postgres),gid=$(id -g ubuntu),dmask=027,fmask=137 /dev/sda1 /mnt/padlock
   ```
   > **Note:** The directory `/mnt/padlock` is designated as the primary mount point. If an alternate directory is used, update the target path in the command accordingly.

3. **Start PostgreSQL:** Once mounted, start the database daemon and verify its status:
   ```bash
   sudo systemctl start postgresql
   sudo systemctl status postgresql
   ```

*For detailed database operations and configuration, refer to the [PostgreSQL Runbook](PostgreSQLRunbook.md).*

---

## 3. Applications Installed on Ubuntu

Because this is a lightweight server edition, only essential development utilities and graphical tools are installed.

### GUI
This system runs a lightweight **XFCE** desktop environment managed by **LightDM**. If the graphical interface hangs or freezes during heavy processing:
1. Press `Ctrl` + `Alt` + `F3` to drop into a TTY terminal and log in.
2. Restart the display manager:
   ```bash
   sudo systemctl restart lightdm
   ```

### pgAdmin
Located under the **Development** desktop menu tab. Used for visual database administration and query inspection (detailed further in the PostgreSQL Runbook).

### PyCharm
Located under the **Development** tab (installed via Snap). Used for maintaining and executing Python ingestion pipelines and data processing scripts.

### VirtualBox
Located under the **System** tab.
* **Current Status:** A Windows Virtual Machine setup is pending due to a missing product key.
* **Next Steps:**
  1. Acquire a valid Windows OS product key.
  2. Mount the Windows ISO within VirtualBox Manager.
  3. Complete OS installation and configure resource allocations.

### Git
Pre-installed on the OS. Connect your GitHub credentials to enable repository management:
```bash
# Configure identity
git config --global user.name "Your Name"
git config --global user.email "your.email@example.com"

# Verify repository status
git status
```

### keep_moving.sh
A dedicated utility script used to prevent system sleep or idle suspension during long-running batch processes, such as multi-hour database compilations or large data imports.

---

## 4. Unmounting and Shutting Down

To prevent data corruption, always follow this order when shutting down:

1. **Close Active Connections:** Terminate all programs (pgAdmin, PyCharm, active terminal sessions) connected to PostgreSQL.
2. **Stop the PostgreSQL Service:**
   ```bash
   sudo systemctl stop postgresql
   ```
3. **Unmount the Drive:**
   ```bash
   sudo umount /mnt/padlock/
   ```
4. **Power Off:** Safely shut down the system.

> **Troubleshooting Note:** If `umount` returns a "device is busy" error, run `lsof +D /mnt/padlock` to identify and close processes holding open file handles on the drive.

---

## 5. Common Issues & Solutions

### Mounting Error ("Windows is hibernated / dirty bit")
* **Cause:** Occurs if Windows was not fully shut down (e.g., due to Windows Fast Startup) before booting into Ubuntu.
* **Solution:** Clear the filesystem flag using `ntfsfix`:
  ```bash
  sudo ntfsfix /dev/sda1
  ```
  *If the issue persists, boot into Windows, perform a full restart (not shutdown), and return to Ubuntu.*

### PostgreSQL Service Hanging on Startup
* **Cause:** The service cannot locate the data directory, usually caused by an unmounted drive or incorrect permissions on `/mnt/padlock`.
* **Solution:** Inspect the system logs to diagnose the exact failure:
  ```bash
  sudo journalctl -u postgresql -n 50 --no-pager
  ```

### Wi-Fi Configuration (Netplan)
* **Default Network:** Pre-configured for the `SaxtonLink` network.
* **Adding a New Network:** Edit the Netplan configuration file:
  ```bash
  sudo vim /etc/netplan/01-netcfg.yaml
  ```
  Update the configuration structure:
  ```yaml
  network:
    version: 2
    renderer: NetworkManager
    wifis:
      wlan0:
        dhcp4: true
        access-points:
          "network_ssid_name":
            password: "your_password_here"
  ```
  Apply the changes:
  ```bash
  sudo netplan apply
  ```