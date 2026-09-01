#!/usr/bin/env python3
"""
Module Export Manager

This class is used to send files to either a controller or NAS running a samba server.

Files are typically recorded to /var/lib/saviour/recordings/pending/
Upon completion they are moved to /var/lib/saviour/recordings/to_export/
Once exported they are moved to /var/lib/saviour/recordings/exported/
Old, exported files may be deleted from there when required.

Author: Andrew SG
Created: 12/06/2025
"""

import datetime
import logging
import os
import pathlib
import shutil
import subprocess
import threading
import time

from src.modules.config import Config


class Export:
    """Manages Samba based file exports"""
    def __init__(self, module_id: str, config: Config):
        self.module_id = module_id
        self.config = config
        self.logger = logging.getLogger(__name__)

        # Local SD card context
        # pending_folder mirrors Recording.recording_folder (recording.py) --
        # both derive it the same way from the same config key, rather than
        # Export reaching into Recording for it. Recording owns creating it;
        # Export only needs it to read, for summarize_recording_state() below.
        self.pending_folder = f'{self.config.get("recording.recording_folder")}/pending'
        self.to_export_folder = f'{self.config.get("recording.recording_folder")}/to_export'
        self.exported_folder = f'{self.config.get("recording.recording_folder")}/exported'
        os.makedirs(self.to_export_folder, exist_ok=True)
        os.makedirs(self.exported_folder, exist_ok=True)
        self.mount_point = "/mnt/export"  # This is where the samba share gets mounted

        # Samba details (configurable)
        self.samba_share_ip = None
        self.samba_share_path = None # The name of the top level folder on the samba share
        self.samba_share_username = None
        self.samba_share_password = None
        # Last traffic-shaping (tc) failure, if any; None once shaping applies
        # cleanly. Set + logged at ERROR by _apply_traffic_control_filter() so a
        # config push that changed max_bitrate_mb but silently failed to re-rate
        # the egress qdisc leaves a trace, rather than the save just reporting
        # success. Kept as an attribute for a future health/readiness surface.
        self.tc_last_error: str | None = None
        self._update_samba_settings()

        self.exporting = False # Flag to indicate whether export in progress
        self._export_lock = threading.Lock()  # Guards exporting flag and staged_for_export

        # Staged files for export
        self.session_files = [] # Record of all recorded files in the session
        self.session_name = None
        self.staged_for_export = []
        self.recording_name = None # Should be set by recording manager - expect something akin to habitat6_cohort3/140126/camera_dc71/
        self.export_path = None # Set here - the full export path e.g. /mnt/export/habitat6_cohort3/140126/camera_dc71/

        # Create mount point directory if it doesn't exist
        try:
            os.makedirs(self.mount_point, exist_ok=True)
            self.logger.info(f"Created mount point directory: {self.mount_point}")
        except Exception as e:
            self.logger.error(f"Failed to create mount point directory: {e}")


    def _setup_export(self, export_path: str) -> bool:
        """Set up an export
        - Mount samba share
        - Create the export folder on the mounted share
        - Ensure it has write permissions
        """
        # Mount the share
        if not self._mount_share():
            return False

        # Create export folder on mounted share
        export_path = self._format_export_path(export_path)
        self._create_export_path(export_path)

        self.logger.info(f"Attempting to set permissions on {export_path}")

        # Set write permissions on export path
        try:
            os.chmod(export_path, 0o777)  # rwxrwxrwx - full permissions
            self.logger.info(f"Set permissions on experiment folder: {export_path}")
            return export_path
        except Exception as e:
            self.logger.warning(f"Could not set permissions on experiment folder: {e}")
            return False


    def _setup_recovered_export(self, session_name: str):
        """Like _setup_export but writes to _recovered/{session}/{date}/{module}/.

        Used when files from an interrupted session cannot be routed to their
        original session directory (e.g. due to permissions or the directory
        not existing on the NAS).
        """
        if not self._mount_share():
            return False
        date_str = self.facade.get_utc_date(time.time())
        module_name = self.facade.get_module_name()
        path = os.path.join(
            self.mount_point, "_recovered", session_name, date_str, module_name
        )
        if not self._create_export_path(path):
            return False
        try:
            os.chmod(path, 0o777)
        except Exception:
            pass
        self.logger.info(f"Using recovered export path: {path}")
        return path

    def _extract_session_from_filename(self, filename: str):
        """Extract the session name prefix from a recorded filename.

        Filenames follow the pattern:
            {session_name}_{animal_id}_{short_module_id}_{rest}
        e.g. habitat6-20260415-111025_A1_a349_(0_20260415-111025).ts

        The short module ID is the last dash-separated component of self.module_id
        (e.g. "a349" from "camera-module-a349").  Filenames use the short form
        even though self.module_id is the full identifier.
        """
        short_id = self.module_id.split("-")[-1]
        for strip_animal_id, marker in (
            (False, f"_{self.module_id}_"),
            (True,  f"_{short_id}_"),
        ):
            idx = filename.find(marker)
            if idx > 0:
                prefix = filename[:idx]
                if strip_animal_id:
                    # prefix is "{session_name}_{animal_id}"; strip the animal_id suffix
                    last_sep = prefix.rfind("_")
                    if last_sep > 0:
                        return prefix[:last_sep]
                return prefix
        return None


    def summarize_recording_state(self, session_name: str = None) -> dict:
        """Summarize local recording-pipeline state (pending/to_export/exported),
        grouped by session, without ever returning raw filenames.

        This is what the controller polls to give the frontend visibility
        into a module's *local* state -- data that never touches the NAS
        (remote or otherwise) until export actually happens, so today's
        share-side file browser has no way to see it at all. A habitat
        session can have 16 modules producing files continuously for weeks,
        so this must stay small and cheap regardless of how many files are
        actually on disk -- counts/sizes/timestamps only, never filenames.

        Pass session_name to get just that session's breakdown per stage;
        omit it to get every session currently represented on disk (useful
        for spotting files stuck under an unexpected/old session name, e.g.
        if the exported folder didn't auto-clear).
        """
        folders = {
            "pending": self.pending_folder,
            "to_export": self.to_export_folder,
            "exported": self.exported_folder,
        }
        result = {}
        for stage, folder in folders.items():
            try:
                filenames = os.listdir(folder)
            except OSError:
                filenames = []
            by_session: dict = {}
            for fn in filenames:
                full_path = os.path.join(folder, fn)
                if not os.path.isfile(full_path):
                    continue
                session = self._extract_session_from_filename(fn) or "_unknown"
                by_session.setdefault(session, []).append(full_path)

            if session_name is not None:
                result[stage] = self._summarize_paths(by_session.get(session_name, []))
            else:
                result[stage] = {
                    session: self._summarize_paths(paths)
                    for session, paths in by_session.items()
                }
        return result

    def _summarize_paths(self, paths: list) -> dict:
        """Reduce a list of file paths to counts/sizes/timestamps only --
        never the paths/filenames themselves. See summarize_recording_state()."""
        sizes = []
        mtimes = []
        for p in paths:
            try:
                st = os.stat(p)
            except OSError:
                continue
            sizes.append(st.st_size)
            mtimes.append(st.st_mtime)
        return {
            "count": len(paths),
            "total_bytes": sum(sizes),
            "oldest_mtime": min(mtimes) if mtimes else None,
            "newest_mtime": max(mtimes) if mtimes else None,
        }


    def export_staged(self, export_path: str = None) -> dict:
        """Export all files in to_export/, routing each file to its correct
        session folder on the NAS (derived from the filename prefix).

        Returns:
            dict mapping session name → True (success) / False (failure).
            The triggered session (first component of export_path) is always
            present in the result so callers can check it directly.
        """
        all_files = os.listdir(self.to_export_folder)
        triggered_session = export_path.split('/', maxsplit=1)[0] if export_path and '/' in export_path else export_path

        with self._export_lock:
            if self.exporting:
                self.logger.warning("Export already in progress; ignoring concurrent call")
                return {triggered_session: False} if triggered_session else {}
            self.staged_for_export = all_files
            self.exporting = True

        self.logger.info(f"Attempting to export {all_files}")

        try:
            # Group files by the session they belong to
            session_file_map: dict[str, list[str]] = {}
            for filename in all_files:
                session = self._extract_session_from_filename(filename) or export_path
                session_file_map.setdefault(session, []).append(filename)

            source_folder = self.to_export_folder
            exported_count = 0
            exported: list[str] = []
            session_results: dict[str, bool] = {}

            for session, files in session_file_map.items():
                session_export_path = self._setup_export(session)
                if not session_export_path:
                    # Primary path failed — try routing to _recovered/ on the share
                    # so files from interrupted sessions aren't silently dropped
                    session_name = session.split("/")[0] if "/" in session else session
                    self.logger.warning(
                        f"Could not set up export path for '{session}'; "
                        f"attempting _recovered/ fallback for {len(files)} file(s)"
                    )
                    session_export_path = self._setup_recovered_export(session_name)
                if not session_export_path:
                    self.logger.error(
                        f"All export paths failed for session '{session}'; "
                        f"skipping {len(files)} file(s)"
                    )
                    session_results[session] = False
                    continue

                self.logger.info(f"Exporting {len(files)} file(s) to {session_export_path}")
                session_ok = True

                for filename in files:
                    filename = pathlib.Path(filename).name
                    source_path      = f"{source_folder}/{filename}"
                    temp_source_path = f"{source_folder}/PENDING_{filename}"
                    temp_dest_path   = f"{session_export_path}/PENDING_{filename}"
                    dest_path        = f"{session_export_path}/{filename}"

                    try:
                        os.rename(source_path, temp_source_path)
                    except OSError as e:
                        self.logger.error(f"Failed to stage {filename} for export: {e}")
                        session_ok = False
                        continue

                    try:
                        shutil.copy2(temp_source_path, temp_dest_path)
                        with open(temp_dest_path, "rb") as _f:
                            os.fsync(_f.fileno())
                        os.rename(temp_dest_path, dest_path)
                        os.rename(temp_source_path, source_path)
                        shutil.move(source_path, f"{self.exported_folder}/{filename}")
                        self.logger.info(f"Exported: {dest_path}")
                        exported_count += 1
                        exported.append(filename)
                    except Exception as e:
                        self.logger.error(f"Failed to export {filename}: {e}")
                        # Roll back PENDING_ rename so the source file is recoverable
                        try:
                            if os.path.exists(temp_source_path):
                                os.rename(temp_source_path, source_path)
                        except OSError:
                            self.logger.error(f"Could not restore {temp_source_path} after failed export")
                        # Remove any partial NAS copy
                        try:
                            if os.path.exists(temp_dest_path):
                                os.remove(temp_dest_path)
                        except OSError:
                            pass
                        session_ok = False

                session_results[session] = session_ok

                # Create export manifest per session if enabled
                if self.config.get("export.manifest_enabled", False):
                    manifest_filename = self._create_export_manifest(files, session_export_path, session)
                    if not manifest_filename:
                        self.logger.error(f"Failed to create export manifest for session '{session}'")

            # Delete locally-moved copies
            if self.config.get("export.delete_on_export", True):
                self._delete_local_files(exported)

            self.logger.info(f"Successfully exported {exported_count} file(s) across {len(session_file_map)} session(s)")

            # Ensure triggered session always has an entry (handles empty to_export case)
            if triggered_session and triggered_session not in session_results:
                session_results[triggered_session] = True

            return session_results

        except Exception as e:
            self.logger.error(f"Export error: {e}")
            return {triggered_session: False} if triggered_session else {}

        finally:
            with self._export_lock:
                self.staged_for_export = []
                self.exporting = False


    def _delete_local_files(self, files: list) -> None:
        deleted_count = 0
        if len(files) == 0:
            self.logger.error("No files provided to delete")
            return
        for filename in files:
            try:
                if os.path.exists(f"{self.exported_folder}/{filename}"):
                    os.remove(f"{self.exported_folder}/{filename}")
                    deleted_count += 1
            except Exception as e:
                self.logger.error(f"Error exporting {filename}: {e}")
        self.logger.info(f"Deleted {deleted_count} files")


    def _create_export_path(self, export_path: str) -> bool:
        """Create the path for files to be exported to, which is the share mount point and the current export folder filename."""
        try:
            os.makedirs(export_path, exist_ok=True)
            return True
        except Exception as e:
            self.logger.error(f"Error creating export path: {e}")
            return False


    def stage_file_for_export(self, filename: str) -> bool:
        """Take a filename and stage it for export"""
        current_path = os.path.abspath(filename)
        path = pathlib.Path(current_path)
        filename = path.name
        destination_path = f"{self.to_export_folder}/{filename}"
        try:
            self.logger.info(f"Moving {filename}, from {current_path} to {destination_path}")
            shutil.move(current_path, destination_path)
            return True
        except Exception as e:
            self.logger.error(f"Error moving {filename} to {destination_path}: {e}")


    def add_session_file(self, filename: str) -> bool:
        """Register a file as part of the current session.

        The file does not need to exist yet — video files are created by the
        encoder on the first frame, so they are not on disk at registration time.
        Existence is checked at export time by stage_file_for_export.
        """
        abspath = os.path.abspath(filename)
        self.session_files.append(abspath)
        # Was two INFO lines per call, the second re-dumping the whole (growing)
        # list — O(n^2) journal text over a long session. One line, count only.
        self.logger.info(
            f"Registered session file {os.path.basename(abspath)} "
            f"({len(self.session_files)} total)"
        )
        return True


    def set_session_name(self, session_name: str) -> bool:
        """Take a foldername e.g. habitat6_cohortA2/140126/, set it and create it."""
        safe_folder_name = "".join(c for c in session_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
        safe_folder_name = safe_folder_name.replace(' ', '_')
        self.session_name = safe_folder_name


    def clear_session_files(self) -> None:
        self.session_files = []


    def clear_staged_for_export(self) -> None:
        self.staged_for_export = []


    def when_recording_starts(self) -> bool:
        """To be called by recording object when a new recording session starts.

        The config file will be exported (as it is expected not to change during recording) and session/staged for export files will be cleared.
        """
        self.logger.info("export object received notification that new recording has started.")
        self.clear_session_files()
        self.clear_staged_for_export()
        self._export_config_file()


    def _ensure_export_folder_exists(self):
        if not self._create_export_path(): # Failed to create export path; maybe an issue mounting share?
            return False
        return True


    def _format_export_path(self, export_path: str):
        """Build the full export path: mount_point/session_name/date/module_name/

        If export_path already contains slashes it is treated as a full relative
        path (session/date/module) produced by recording.py and only the mount
        point is prepended.  A bare session name (no slashes) has the current
        date and module name appended as before.
        """
        if '/' in export_path:
            return os.path.join(self.mount_point, export_path)
        date_str = self.facade.get_utc_date(time.time())
        module_name = self.facade.get_module_name()
        return os.path.join(self.mount_point, export_path, date_str, module_name)


    def _export_config_file(self) -> bool:
        """Export the module's active config for traceability."""
        try:
            export_path = self.facade.get_current_session_name()
            export_path = self._setup_export(export_path)
            if not export_path:
                return False

            dest_path = os.path.join(export_path, "config.json")
            if os.path.exists(dest_path):
                self.logger.info("Config already exported for this session; skipping")
                return True

            source = self.config.active_config_path
            if os.path.exists(source):
                shutil.copy2(source, dest_path)
                self.logger.info(f"Exported active config: {dest_path}")
                return True

            self.logger.warning(f"Active config not found at {source}")
            return False

        except Exception as e:
            self.logger.error(f"Error exporting config file: {e}")
            return False


    def _create_export_manifest(self, files_to_export: list, export_folder: str, session_name: str = None) -> str:
        """Create an export manifest file listing all files to be exported
        
        Args:
            files_to_export: List of filenames that will be exported
            destination: Where the files will be exported to (string or enum)
            export_folder: Path to the folder where files will be exported
            session_name: Optional session_name for the export
            
        Returns:
            str: Name of the created manifest file
        """
        try:
            manifest_timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            manifest_filename = f"export_manifest_{manifest_timestamp}.txt"
            manifest_path = os.path.join(export_folder, manifest_filename)

            with open(manifest_path, 'w') as f:
                f.write(f"Export Manifest - {manifest_timestamp}\n")
                f.write(f"Module ID: {self.module_id}\n")
                f.write(f"Destination: //{self.samba_share_ip}/{self.samba_share_path}\n")
                f.write(f"Export Folder: {os.path.basename(export_folder)}\n")
                if session_name:
                    f.write(f"session_name: {session_name}\n")
                f.write("Files to be exported:\n")
                for file in files_to_export:
                    f.write(f"- {file}\n")
                    # Add file size and modification time from source
                    file_path = os.path.join(self.facade.get_recording_folder(), file)
                    if os.path.exists(file_path):
                        stat = os.stat(file_path)
                        size_mb = stat.st_size / (1024 * 1024)  # Convert to MB
                        mod_time = datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                        f.write(f"  Size: {size_mb:.2f} MB\n")
                        f.write(f"  Modified: {mod_time}\n")

            self.logger.info(f"Created export manifest: {manifest_filename}")
            return manifest_filename

        except Exception as e:
            self.logger.error(f"Failed to create export manifest: {e}")
            return None


    """Samba Methods"""
    def _update_samba_settings(self):
        """Check for updated samba settings from config"""
        old_samba_ip = self.samba_share_ip

        self.samba_share_ip = self.config.get("export.share_ip")
        self.samba_share_path = self.config.get("export.share_path", "controller_share")
        self.samba_share_username = self.config.get("export.share_username", "saviour_module")
        self.samba_share_password = self.config.get("export.share_password", "")

        # If IP has changed, reconfigure traffic control rules
        if self.samba_share_ip != old_samba_ip:
            self._clear_traffic_control_filter()
            self._apply_traffic_control_filter()


    def _samba_settings_changed(self):
        self.samba_share_ip = self.config.get("export.share_ip")
        self.samba_share_path = self.config.get("export.share_path", "controller_share")
        self.samba_share_username = self.config.get("export.share_username", "saviour_module")
        self.samba_share_password = self.config.get("export.share_password", "")
        self._clear_traffic_control_filter()
        self._apply_traffic_control_filter()


    def _clear_traffic_control_filter(self):
        self.logger.info("Clearing traffic control filters")
        try:
            # Check if the qdisc exists
            check_cmd = ["sudo", "tc", "qdisc", "show", "dev", "eth0"]
            check_result = subprocess.run(check_cmd, check=True, text=True, capture_output=True)

            if "htb" in check_result.stdout:
                # Proceed to delete the qdisc if it exists
                cmd = [
                    "sudo", "tc", "qdisc", "del",
                    "dev", "eth0",
                    "root"
                ]
                self._run_shell_command(cmd)
                self.logger.info("Traffic control filters cleared successfully")
            else:
                self.logger.info("No traffic control filters found; nothing to clear")

        except Exception as e:
            self.logger.error(f"Unexpected error: {e}")


    def _run_shell_command(self, cmd: list) -> bool:
        """Run *cmd*, logging stdout/stderr. Returns True on exit 0, False otherwise
        (including if the binary is missing) so callers can react to a failure
        rather than silently continuing."""
        try:
            result = subprocess.run(cmd, check=True, text=True, capture_output=True)
            self.logger.info(f"Command succeeded: {result.stdout}")
            return True
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Command failed ({' '.join(cmd)}): {e.stderr.strip()}")
            return False
        except (FileNotFoundError, OSError) as e:
            self.logger.error(f"Command could not be run ({' '.join(cmd)}): {e}")
            return False


    def _apply_traffic_control_filter(self) -> bool:
        """(Re)build the egress HTB qdisc/class/filter that rate-limits Samba
        export traffic to the share on port 445.

        Returns True only if the rate-limiting class AND the classifying filter
        both applied. The root qdisc `add` is allowed to fail benignly (it
        already exists if a prior _clear_traffic_control_filter() didn't run) as
        long as the class/filter then succeed. On failure, self.tc_last_error is
        set and logged loudly -- otherwise a config push that changed the rate
        would report a successful save while the OS-level shaping never changed.
        """
        self.logger.info(f"Applying new traffic control filter for {self.samba_share_ip} on samba port 445")

        if not self.samba_share_ip:
            self.tc_last_error = "no share IP configured — traffic shaping not applied"
            self.logger.warning(self.tc_last_error)
            return False

        add_qdisc_cmd = [
            "sudo", "tc", "qdisc", "add",
            "dev", "eth0",
            "root", # Create the root level queueing discipling
            "handle", "1:0",  # Create new queueing discipline with handle 1:0
            "htb", # Hierarchical token bucket
            "default", "10" # Default class for packets that don't match any criteria
        ]
        # Non-fatal: a leftover root qdisc from a prior run means the class/filter
        # below still land on it. _clear_traffic_control_filter() normally removes
        # it first on the reconfigure path.
        self._run_shell_command(add_qdisc_cmd)

        max_bitrate_mb = self.config.get("export.max_bitrate_mb", 100)
        max_burst_kb = self.config.get("export.max_burst_kb", 30)
        add_class_cmd = [
            "sudo", "tc", "class", "add",
            "dev", "eth0",
            "parent", "1:0",  # Apply to parent queueing discipline with handle 1:0 (root)
            "classid", "1:1",  # Create new class with handle 1:1
            "htb", # Hierarchical token bucket
            "rate", f"{max_bitrate_mb}mbit",
            "burst", f"{max_burst_kb}k"
        ]
        class_ok = self._run_shell_command(add_class_cmd)

        add_filter_cmd = [
            "sudo", "tc", "filter", "add",
            "dev", "eth0",
            "protocol", "ip",
            "parent", "1:0", # Apply to parent queueing discipline with handle 1:0 (root)
            "u32", # Use u32 packet classifier
            "match", "ip", "dst", self.samba_share_ip,
            "match", "ip", "dport", "445",
            "0xffff", # Bitmask for header - match exactly on port 445
            "flowid", "1:1" # Direct matching traffic to the class with identifier 1:1 (the one that rate limits traffic)
        ]
        filter_ok = self._run_shell_command(add_filter_cmd)

        if class_ok and filter_ok:
            self.tc_last_error = None
            self.logger.info(
                f"Export traffic shaping active: {max_bitrate_mb} mbit to "
                f"{self.samba_share_ip}:445"
            )
            return True

        self.tc_last_error = (
            f"tc failed (class_ok={class_ok}, filter_ok={filter_ok}) — export "
            f"bandwidth to {self.samba_share_ip} is NOT rate-limited"
        )
        self.logger.error(self.tc_last_error)
        return False

    _MOUNT_MAX_ATTEMPTS = 3
    _MOUNT_RETRY_DELAY_S = 2.0
    _MOUNT_TIMEOUT_S = 30

    def _mount_share(self) -> bool:
        """Mount Samba share, retrying up to _MOUNT_MAX_ATTEMPTS times with a
        per-attempt timeout so a unreachable NAS never hangs the export thread."""
        try:
            self._update_samba_settings()
            self.logger.info(
                f"Attempting to mount share: //{self.samba_share_ip}/{self.samba_share_path} "
                f"as user {self.samba_share_username}"
            )

            if os.path.ismount(self.mount_point):
                self.logger.info(f"Unmounting existing mount at {self.mount_point}")
                subprocess.run(
                    ['sudo', 'umount', self.mount_point],
                    check=True,
                    timeout=self._MOUNT_TIMEOUT_S,
                )

            auth_opts = (
                f'username={self.samba_share_username},password={self.samba_share_password}'
                if self.samba_share_username
                else 'guest'
            )
            mount_cmd = [
                'sudo', 'mount', '-t', 'cifs',
                f'//{self.samba_share_ip}/{self.samba_share_path}',
                self.mount_point,
                '-o', f'{auth_opts},uid=pi,gid=pi,file_mode=0664,dir_mode=0775,cache=none',
            ]

            for attempt in range(1, self._MOUNT_MAX_ATTEMPTS + 1):
                try:
                    result = subprocess.run(
                        mount_cmd, capture_output=True, text=True,
                        timeout=self._MOUNT_TIMEOUT_S,
                    )
                    if result.returncode == 0:
                        self.logger.info(f"Successfully mounted controller share at {self.mount_point}")
                        return True
                    self.logger.warning(
                        f"Mount attempt {attempt}/{self._MOUNT_MAX_ATTEMPTS} failed: "
                        f"{result.stderr.strip()}"
                    )
                except subprocess.TimeoutExpired:
                    self.logger.warning(
                        f"Mount attempt {attempt}/{self._MOUNT_MAX_ATTEMPTS} timed out "
                        f"after {self._MOUNT_TIMEOUT_S}s"
                    )
                if attempt < self._MOUNT_MAX_ATTEMPTS:
                    time.sleep(self._MOUNT_RETRY_DELAY_S)

            self.logger.error(
                f"Failed to mount //{self.samba_share_ip}/{self.samba_share_path} "
                f"after {self._MOUNT_MAX_ATTEMPTS} attempts"
            )
            return False

        except Exception as e:
            self.logger.warning(f"Error mounting share: {e}")
            return False


    def unmount(self) -> bool:
        """Unmount current destination"""
        try:
            if self.current_mount:
                # Unmount using umount
                # Example: umount /mnt/export
                self.current_mount = None
                return True
            return True
        except Exception as e:
            self.logger.error(f"Unmount failed: {e}")
            return False
