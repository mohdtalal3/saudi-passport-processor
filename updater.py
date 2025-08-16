"""
Simple Auto-Updater for Saudi Passport Processor EXE
Downloads new exe file from GitHub releases and replaces the current one
"""

import os
import sys
import json
import requests
import subprocess
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from PyQt5.QtWidgets import QMessageBox, QProgressDialog, QApplication
from PyQt5.QtCore import QThread, pyqtSignal, Qt
import config
from version_manager import get_version, update_version


class UpdateInfo:
    """Class to hold update information"""
    def __init__(self, version: str, download_url: str, release_notes: str, assets: list):
        self.version = version
        self.download_url = download_url
        self.release_notes = release_notes
        self.assets = assets
        self.is_newer = False


class SimpleUpdateDownloadThread(QThread):
    """Thread for downloading exe updates in the background"""
    progress_changed = pyqtSignal(int)
    status_changed = pyqtSignal(str)
    download_completed = pyqtSignal(str)  # filepath
    error_occurred = pyqtSignal(str)

    def __init__(self, download_url: str, save_path: str):
        super().__init__()
        self.download_url = download_url
        self.save_path = save_path

    def run(self):
        try:
            self.status_changed.emit("Starting download...")
            response = requests.get(self.download_url, stream=True)
            response.raise_for_status()

            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0

            os.makedirs(os.path.dirname(self.save_path), exist_ok=True)

            with open(self.save_path, 'wb') as file:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        file.write(chunk)
                        downloaded += len(chunk)
                        
                        if total_size > 0:
                            progress = int((downloaded / total_size) * 100)
                            self.progress_changed.emit(progress)
                            self.status_changed.emit(f"Downloaded {downloaded:,} / {total_size:,} bytes")

            self.status_changed.emit("Download completed!")
            self.download_completed.emit(self.save_path)

        except Exception as e:
            self.error_occurred.emit(f"Download failed: {str(e)}")


class AutoUpdater:
    """Simple auto-updater for exe files"""
    
    def __init__(self, parent_widget=None):
        self.parent_widget = parent_widget
        self.current_version = get_version()  # Use version manager instead of config
        self.repo_owner = config.GITHUB_REPO_OWNER
        self.repo_name = config.GITHUB_REPO_NAME
        self.api_url = config.GITHUB_API_URL
        
        # Paths
        self.app_dir = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))
        self.settings_file = os.path.join(self.app_dir, "update_settings.json")
        
        # Load settings
        self.settings = self._load_settings()

    def _load_settings(self) -> Dict[str, Any]:
        """Load update settings from file"""
        default_settings = {
            "last_check": None,
            "auto_update": config.AUTO_UPDATE_CHECK,
            "check_interval": config.UPDATE_CHECK_INTERVAL,
            "skip_version": None
        }
        
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r') as f:
                    settings = json.load(f)
                    # Merge with defaults
                    for key, value in default_settings.items():
                        if key not in settings:
                            settings[key] = value
                    return settings
        except Exception as e:
            print(f"Error loading settings: {e}")
        
        return default_settings

    def _save_settings(self):
        """Save update settings to file"""
        try:
            with open(self.settings_file, 'w') as f:
                json.dump(self.settings, f, indent=4)
        except Exception as e:
            print(f"Error saving settings: {e}")

    def _compare_versions(self, version1: str, version2: str) -> int:
        """Compare two version strings. Returns 1 if v1 > v2, -1 if v1 < v2, 0 if equal"""
        def version_tuple(v):
            return tuple(map(int, (v.split("."))))
        
        try:
            v1_tuple = version_tuple(version1)
            v2_tuple = version_tuple(version2)
            
            if v1_tuple > v2_tuple:
                return 1
            elif v1_tuple < v2_tuple:
                return -1
            else:
                return 0
        except:
            return 0

    def should_check_for_updates(self) -> bool:
        """Check if it's time to check for updates"""
        if not self.settings["auto_update"]:
            return False
            
        last_check = self.settings["last_check"]
        if not last_check:
            return True
            
        try:
            last_check_date = datetime.fromisoformat(last_check)
            hours_since_check = (datetime.now() - last_check_date).total_seconds() / 3600
            return hours_since_check >= self.settings["check_interval"]
        except:
            return True

    def check_for_updates(self) -> Optional[UpdateInfo]:
        """Check GitHub for new releases"""
        try:
            print(f"Checking for updates from: {self.api_url}/releases/latest")
            response = requests.get(f"{self.api_url}/releases/latest", timeout=10)
            
            if response.status_code == 404:
                print("No releases found on GitHub")
                return None
                
            response.raise_for_status()
            release_data = response.json()
            
            latest_version = release_data["tag_name"].lstrip("v")
            download_url = None
            
            # Look for exe file in assets
            for asset in release_data.get("assets", []):
                if asset["name"].endswith(".exe"):
                    download_url = asset["browser_download_url"]
                    break
            
            if not download_url:
                print("No exe file found in release assets")
                return None
            
            update_info = UpdateInfo(
                version=latest_version,
                download_url=download_url,
                release_notes=release_data.get("body", "No release notes available"),
                assets=release_data.get("assets", [])
            )
            
            # Check if this is a newer version
            if self._compare_versions(latest_version, self.current_version) > 0:
                update_info.is_newer = True
            
            # Update last check time
            self.settings["last_check"] = datetime.now().isoformat()
            self._save_settings()
            
            return update_info
            
        except requests.exceptions.RequestException as e:
            print(f"Network error checking for updates: {e}")
            return None
        except Exception as e:
            print(f"Error checking for updates: {e}")
            return None

    def prompt_for_update(self, update_info: UpdateInfo) -> bool:
        """Show update prompt to user"""
        if not self.parent_widget:
            return False
            
        # Check if user has chosen to skip this version
        if self.settings.get("skip_version") == update_info.version:
            return False
        
        msg = QMessageBox(self.parent_widget)
        msg.setWindowTitle("Update Available")
        msg.setIcon(QMessageBox.Information)
        
        text = f"""
A new version of {config.APP_NAME} is available!

Current Version: {self.current_version}
Latest Version: {update_info.version}

Release Notes:
{update_info.release_notes[:500]}{'...' if len(update_info.release_notes) > 500 else ''}

Would you like to download and install the update?
        """
        
        msg.setText(text.strip())
        
        # Add custom buttons
        update_btn = msg.addButton("Update Now", QMessageBox.AcceptRole)
        skip_btn = msg.addButton("Skip This Version", QMessageBox.RejectRole)
        later_btn = msg.addButton("Remind Me Later", QMessageBox.RejectRole)
        
        msg.exec_()
        
        if msg.clickedButton() == update_btn:
            return True
        elif msg.clickedButton() == skip_btn:
            self.settings["skip_version"] = update_info.version
            self._save_settings()
        
        return False

    def download_and_install_update(self, update_info: UpdateInfo) -> bool:
        """Download and install the exe update"""
        try:
            # Create progress dialog
            progress = QProgressDialog("Downloading update...", "Cancel", 0, 100, self.parent_widget)
            progress.setWindowTitle("Auto Updater")
            progress.setWindowModality(Qt.WindowModal)
            progress.show()
            
            # Get current exe path and create new exe path
            if getattr(sys, 'frozen', False):
                current_exe = sys.executable
            else:
                current_exe = os.path.join(self.app_dir, "passport_gui.exe")  # Fallback
            
            current_exe_name = os.path.basename(current_exe)
            new_exe_name = f"passport_gui_v{update_info.version}.exe"
            new_exe_path = os.path.join(self.app_dir, new_exe_name)
            
            # Start download thread
            download_thread = SimpleUpdateDownloadThread(update_info.download_url, new_exe_path)
            
            def on_progress(value):
                progress.setValue(value)
                QApplication.processEvents()
            
            def on_status(status):
                progress.setLabelText(status)
                QApplication.processEvents()
            
            def on_completed(filepath):
                progress.setValue(100)
                progress.setLabelText("Download completed!")
            
            def on_error(error):
                progress.close()
                QMessageBox.critical(self.parent_widget, "Update Error", f"Failed to download update:\n{error}")
            
            download_thread.progress_changed.connect(on_progress)
            download_thread.status_changed.connect(on_status)
            download_thread.download_completed.connect(on_completed)
            download_thread.error_occurred.connect(on_error)
            
            download_thread.start()
            
            # Wait for download to complete
            while download_thread.isRunning():
                QApplication.processEvents()
                time.sleep(0.1)
                if progress.wasCanceled():
                    download_thread.terminate()
                    return False
            
            progress.close()
            
            if not os.path.exists(new_exe_path):
                QMessageBox.critical(self.parent_widget, "Update Error", "Download failed - file not found")
                return False
            
            # Update version.txt file with new version
            if update_version(update_info.version):
                print(f"Version updated from {self.current_version} to {update_info.version}")
            else:
                print(f"Warning: Failed to update version.txt file")
            
            # Show success message
            msg = QMessageBox(self.parent_widget)
            msg.setWindowTitle("Update Complete")
            msg.setIcon(QMessageBox.Information)
            msg.setText(f"""Update to version {update_info.version} downloaded successfully!

New file: {new_exe_name}

The application will now close. Please:
1. Close this application
2. Delete the old exe file: {current_exe_name}
3. Start the new exe file: {new_exe_name}

Would you like to open the folder containing the files?""")
            
            open_folder_btn = msg.addButton("Open Folder", QMessageBox.AcceptRole)
            close_btn = msg.addButton("Close Application", QMessageBox.RejectRole)
            
            msg.exec_()
            
            if msg.clickedButton() == open_folder_btn:
                # Open folder containing the files
                if os.name == 'nt':  # Windows
                    subprocess.Popen(['explorer', self.app_dir])
                elif os.name == 'posix':  # macOS/Linux
                    subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', self.app_dir])
            
            # Close application
            if self.parent_widget and hasattr(self.parent_widget, 'close'):
                self.parent_widget.close()
            QApplication.quit()
            
            return True
            
        except Exception as e:
            if 'progress' in locals():
                progress.close()
            QMessageBox.critical(self.parent_widget, "Update Error", f"Update failed: {str(e)}")
            return False

    def check_and_update_if_available(self, force_check: bool = False) -> bool:
        """Main method to check and update if available"""
        if not force_check and not self.should_check_for_updates():
            return False
        
        update_info = self.check_for_updates()
        
        if not update_info or not update_info.is_newer:
            if force_check and self.parent_widget:
                QMessageBox.information(self.parent_widget, "No Updates", 
                                      f"You are running the latest version ({self.current_version})")
            return False
        
        if self.prompt_for_update(update_info):
            return self.download_and_install_update(update_info)
        
        return False

    def force_check_for_updates(self):
        """Force check for updates (called from menu)"""
        return self.check_and_update_if_available(force_check=True)
