"""
Auto-updater module for Saudi Passport Processor
Handles checking for updates, downloading new versions, and replacing the current executable.
"""

import os
import sys
import json
import shutil
import tempfile
import subprocess
import requests
from datetime import datetime
from packaging import version
from PyQt5.QtWidgets import QMessageBox, QProgressDialog, QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout
from PyQt5.QtCore import QThread, pyqtSignal, Qt, QTimer
from PyQt5.QtGui import QFont
import config


class UpdateDownloadThread(QThread):
    """Thread for downloading update files"""
    progress_signal = pyqtSignal(int)
    status_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)  # success, path_or_error
    
    def __init__(self, download_url, temp_dir):
        super().__init__()
        self.download_url = download_url
        self.temp_dir = temp_dir
        
    def run(self):
        try:
            self.status_signal.emit("Downloading update...")
            
            response = requests.get(self.download_url, stream=True, timeout=60)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            # Extract filename from URL or use default
            filename = self.download_url.split('/')[-1]
            if not filename or '.' not in filename:
                filename = "update.exe" if sys.platform.startswith('win') else "update"
            
            temp_file_path = os.path.join(self.temp_dir, filename)
            
            with open(temp_file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        if total_size > 0:
                            progress = int((downloaded / total_size) * 100)
                            self.progress_signal.emit(progress)
            
            self.status_signal.emit("Download completed!")
            self.finished_signal.emit(True, temp_file_path)
            
        except Exception as e:
            self.finished_signal.emit(False, str(e))


class UpdateDialog(QDialog):
    """Dialog for showing update information and options"""
    
    def __init__(self, current_version, latest_version, release_notes, download_url, parent=None):
        super().__init__(parent)
        self.download_url = download_url
        self.temp_dir = tempfile.mkdtemp()
        self.update_result = None
        
        self.setWindowTitle("Update Available")
        self.setModal(True)
        self.setFixedSize(500, 400)
        self.setStyleSheet("""
            QDialog {
                background-color: #f5f5f5;
            }
            QLabel {
                color: #333;
                font-size: 12px;
            }
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                padding: 10px 20px;
                border-radius: 4px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:pressed {
                background-color: #3d8b40;
            }
            .danger {
                background-color: #f44336;
            }
            .danger:hover {
                background-color: #da190b;
            }
        """)
        
        self.setup_ui(current_version, latest_version, release_notes)
        
    def setup_ui(self, current_version, latest_version, release_notes):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Title
        title_label = QLabel("🔄 Update Available!")
        title_label.setFont(QFont("Arial", 16, QFont.Bold))
        title_label.setStyleSheet("color: #2c3e50; margin-bottom: 10px;")
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)
        
        # Version info
        version_info = f"""
        <div style="background-color: white; padding: 15px; border-radius: 8px; border: 1px solid #e0e0e0;">
            <p><b>Current Version:</b> {current_version}</p>
            <p><b>Latest Version:</b> <span style="color: #4CAF50;">{latest_version}</span></p>
        </div>
        """
        version_label = QLabel(version_info)
        version_label.setWordWrap(True)
        layout.addWidget(version_label)
        
        # Release notes
        notes_label = QLabel("<b>What's New:</b>")
        notes_label.setStyleSheet("margin-top: 10px;")
        layout.addWidget(notes_label)
        
        release_notes_display = QLabel(release_notes or "No release notes available.")
        release_notes_display.setWordWrap(True)
        release_notes_display.setStyleSheet("""
            background-color: white;
            padding: 10px;
            border-radius: 4px;
            border: 1px solid #ddd;
            max-height: 150px;
        """)
        layout.addWidget(release_notes_display)
        
        # Buttons
        button_layout = QHBoxLayout()
        
        self.update_btn = QPushButton("📥 Download & Install Update")
        self.update_btn.clicked.connect(self.start_update)
        
        self.later_btn = QPushButton("⏭️ Remind Me Later")
        self.later_btn.setProperty("class", "secondary")
        self.later_btn.setStyleSheet("background-color: #ff9800;")
        self.later_btn.clicked.connect(self.remind_later)
        
        self.skip_btn = QPushButton("❌ Skip This Version")
        self.skip_btn.setProperty("class", "danger")
        self.skip_btn.setStyleSheet("background-color: #f44336;")
        self.skip_btn.clicked.connect(self.skip_version)
        
        button_layout.addWidget(self.update_btn)
        button_layout.addWidget(self.later_btn)
        button_layout.addWidget(self.skip_btn)
        
        layout.addLayout(button_layout)
        
    def start_update(self):
        """Start the update download process"""
        # Show progress dialog
        self.progress_dialog = QProgressDialog("Preparing update...", "Cancel", 0, 100, self)
        self.progress_dialog.setWindowTitle("Downloading Update")
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.setAutoClose(False)
        self.progress_dialog.setAutoReset(False)
        self.progress_dialog.canceled.connect(self.cancel_update)
        self.progress_dialog.show()
        
        # Start download thread
        self.download_thread = UpdateDownloadThread(self.download_url, self.temp_dir)
        self.download_thread.progress_signal.connect(self.progress_dialog.setValue)
        self.download_thread.status_signal.connect(self.progress_dialog.setLabelText)
        self.download_thread.finished_signal.connect(self.on_download_finished)
        self.download_thread.start()
        
    def cancel_update(self):
        """Cancel the update process"""
        if hasattr(self, 'download_thread') and self.download_thread.isRunning():
            self.download_thread.terminate()
        self.cleanup_temp_files()
        self.reject()
        
    def on_download_finished(self, success, result):
        """Handle download completion"""
        self.progress_dialog.close()
        
        if success:
            try:
                self.install_update(result)
                self.update_result = "installed"
                self.accept()
            except Exception as e:
                QMessageBox.critical(self, "Update Error", f"Failed to install update:\n{str(e)}")
                self.cleanup_temp_files()
        else:
            QMessageBox.critical(self, "Download Error", f"Failed to download update:\n{result}")
            self.cleanup_temp_files()
            
    def install_update(self, update_file_path):
        """Install the downloaded update"""
        try:
            current_exe = sys.executable
            if getattr(sys, 'frozen', False):
                # Running as compiled executable
                current_exe = sys.argv[0]
            
            # Create backup of current version
            backup_path = current_exe + ".backup"
            if os.path.exists(backup_path):
                os.remove(backup_path)
            shutil.copy2(current_exe, backup_path)
            
            # On Windows, we need a special approach to replace running executable
            if sys.platform.startswith('win'):
                self.install_update_windows(update_file_path, current_exe, backup_path)
            else:
                self.install_update_unix(update_file_path, current_exe, backup_path)
                
        except Exception as e:
            raise Exception(f"Installation failed: {str(e)}")
            
    def install_update_windows(self, update_file_path, current_exe, backup_path):
        """Install update on Windows"""
        # Create a batch script to replace the executable
        batch_script = f"""
@echo off
echo Updating Saudi Passport Processor...
timeout /t 2 /nobreak > nul
copy /Y "{update_file_path}" "{current_exe}"
if errorlevel 1 (
    echo Update failed! Restoring backup...
    copy /Y "{backup_path}" "{current_exe}"
    pause
    exit /b 1
)
echo Update completed successfully!
start "" "{current_exe}"
del "{backup_path}"
del "{update_file_path}"
del "%~f0"
"""
        
        batch_file = os.path.join(self.temp_dir, "update.bat")
        with open(batch_file, 'w') as f:
            f.write(batch_script)
            
        # Schedule the update script to run after application closes
        QTimer.singleShot(1000, lambda: self.run_update_script(batch_file))
        
    def install_update_unix(self, update_file_path, current_exe, backup_path):
        """Install update on Unix/Linux/macOS"""
        # Make the new executable file executable
        os.chmod(update_file_path, 0o755)
        
        # Create a shell script to replace the executable
        shell_script = f"""#!/bin/bash
echo "Updating Saudi Passport Processor..."
sleep 2
cp "{update_file_path}" "{current_exe}"
if [ $? -eq 0 ]; then
    echo "Update completed successfully!"
    chmod +x "{current_exe}"
    "{current_exe}" &
    rm -f "{backup_path}"
    rm -f "{update_file_path}"
else
    echo "Update failed! Restoring backup..."
    cp "{backup_path}" "{current_exe}"
    exit 1
fi
rm -f "$0"
"""
        
        shell_file = os.path.join(self.temp_dir, "update.sh")
        with open(shell_file, 'w') as f:
            f.write(shell_script)
        os.chmod(shell_file, 0o755)
        
        # Schedule the update script to run after application closes
        QTimer.singleShot(1000, lambda: self.run_update_script(shell_file))
        
    def run_update_script(self, script_path):
        """Run the update script"""
        try:
            if sys.platform.startswith('win'):
                subprocess.Popen([script_path], creationflags=subprocess.CREATE_NEW_CONSOLE)
            else:
                subprocess.Popen(['/bin/bash', script_path])
        except Exception as e:
            QMessageBox.critical(self, "Update Error", f"Failed to run update script:\n{str(e)}")
            
    def remind_later(self):
        """User chose to be reminded later"""
        self.update_result = "later"
        self.cleanup_temp_files()
        self.reject()
        
    def skip_version(self):
        """User chose to skip this version"""
        self.update_result = "skip"
        self.cleanup_temp_files()
        self.reject()
        
    def cleanup_temp_files(self):
        """Clean up temporary files"""
        try:
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass  # Ignore cleanup errors
            
    def closeEvent(self, event):
        """Handle dialog close event"""
        self.cleanup_temp_files()
        super().closeEvent(event)


class AutoUpdater:
    """Main auto-updater class"""
    
    def __init__(self, github_repo, current_version, parent_window=None):
        """
        Initialize the auto-updater
        
        Args:
            github_repo (str): GitHub repository in format 'owner/repo'
            current_version (str): Current version of the application
            parent_window: Parent window for dialogs
        """
        self.github_repo = github_repo
        self.current_version = current_version
        self.parent_window = parent_window
        self.settings_file = os.path.join(os.path.dirname(__file__), "update_settings.json")
        self.settings = self.load_settings()
        
    def load_settings(self):
        """Load update settings from file"""
        default_settings = {
            "check_for_updates": True,
            "skipped_versions": [],
            "last_check": None,
            "auto_check_interval_days": 1
        }
        
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r') as f:
                    settings = json.load(f)
                    # Merge with defaults to ensure all keys exist
                    for key, value in default_settings.items():
                        if key not in settings:
                            settings[key] = value
                    return settings
        except Exception:
            pass
            
        return default_settings
        
    def save_settings(self):
        """Save update settings to file"""
        try:
            with open(self.settings_file, 'w') as f:
                json.dump(self.settings, f, indent=2)
        except Exception:
            pass  # Ignore save errors
            
    def should_check_for_updates(self):
        """Determine if we should check for updates"""
        if not self.settings.get("check_for_updates", True):
            return False
            
        last_check = self.settings.get("last_check")
        if not last_check:
            return True
            
        try:
            from datetime import datetime, timedelta
            last_check_date = datetime.fromisoformat(last_check)
            interval_days = self.settings.get("auto_check_interval_days", 1)
            
            return datetime.now() - last_check_date > timedelta(days=interval_days)
        except Exception:
            return True  # Check if we can't determine last check time
            
    def get_latest_release(self):
        """Get latest release information from GitHub"""
        try:
            url = f"https://api.github.com/repos/{self.github_repo}/releases/latest"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            
            release_data = response.json()
            
            return {
                "version": release_data["tag_name"].lstrip('v'),  # Remove 'v' prefix if present
                "release_notes": release_data.get("body", ""),
                "download_url": self.get_download_url(release_data),
                "published_at": release_data["published_at"]
            }
            
        except Exception as e:
            raise Exception(f"Failed to check for updates: {str(e)}")
            
    def get_download_url(self, release_data):
        """Extract appropriate download URL from release data"""
        assets = release_data.get("assets", [])
        
        if not assets:
            return None
            
        # Look for platform-specific executable
        platform_extensions = {
            "win": [".exe", ".msi"],
            "darwin": [".dmg", ".app.zip"],
            "linux": [".AppImage", ".tar.gz", ".deb", ".rpm"]
        }
        
        current_platform = "win" if sys.platform.startswith("win") else sys.platform
        preferred_extensions = platform_extensions.get(current_platform, [".exe"])
        
        # First, try to find platform-specific asset
        for asset in assets:
            asset_name = asset["name"].lower()
            for ext in preferred_extensions:
                if asset_name.endswith(ext.lower()):
                    return asset["browser_download_url"]
                    
        # Fallback to first asset
        return assets[0]["browser_download_url"]
        
    def is_newer_version(self, latest_version):
        """Check if the latest version is newer than current version"""
        try:
            return version.parse(latest_version) > version.parse(self.current_version)
        except Exception:
            # Fallback to string comparison if version parsing fails
            return latest_version != self.current_version
            
    def check_for_updates(self, manual_check=False):
        """
        Check for updates and show dialog if update is available
        
        Args:
            manual_check (bool): True if this is a manual check from user action
            
        Returns:
            str: "no_update", "update_available", "error", "skipped"
        """
        try:
            # Update last check time
            self.settings["last_check"] = datetime.now().isoformat()
            self.save_settings()
            
            # Get latest release info
            latest_release = self.get_latest_release()
            latest_version = latest_release["version"]
            
            # Check if this version was skipped
            if latest_version in self.settings.get("skipped_versions", []) and not manual_check:
                return "skipped"
                
            # Check if update is needed
            if not self.is_newer_version(latest_version):
                if manual_check:
                    QMessageBox.information(
                        self.parent_window,
                        "No Updates",
                        f"You are already running the latest version ({self.current_version})."
                    )
                return "no_update"
                
            # Check if download URL is available
            if not latest_release["download_url"]:
                if manual_check:
                    QMessageBox.warning(
                        self.parent_window,
                        "Update Available",
                        f"A new version ({latest_version}) is available, but no download link was found.\n"
                        f"Please visit the GitHub repository to download manually."
                    )
                return "no_download"
                
            # Show update dialog
            dialog = UpdateDialog(
                self.current_version,
                latest_version,
                latest_release["release_notes"],
                latest_release["download_url"],
                self.parent_window
            )
            
            dialog.exec_()
            
            # Handle user choice
            if dialog.update_result == "skip":
                # Add to skipped versions
                if latest_version not in self.settings.get("skipped_versions", []):
                    self.settings.setdefault("skipped_versions", []).append(latest_version)
                    self.save_settings()
                return "skipped"
            elif dialog.update_result == "installed":
                # Exit the application so the update can complete
                QTimer.singleShot(2000, self.exit_for_update)
                return "installing"
            else:
                return "later"
                
        except Exception as e:
            error_msg = f"Failed to check for updates: {str(e)}"
            if manual_check:
                QMessageBox.critical(self.parent_window, "Update Error", error_msg)
            return "error"
            
    def exit_for_update(self):
        """Exit the application to allow update installation"""
        if self.parent_window:
            self.parent_window.close()
        else:
            QApplication.quit()
            
    def manual_check(self):
        """Perform a manual update check"""
        return self.check_for_updates(manual_check=True)
        
    def disable_auto_updates(self):
        """Disable automatic update checks"""
        self.settings["check_for_updates"] = False
        self.save_settings()
        
    def enable_auto_updates(self):
        """Enable automatic update checks"""
        self.settings["check_for_updates"] = True
        self.save_settings()
        
    def clear_skipped_versions(self):
        """Clear the list of skipped versions"""
        self.settings["skipped_versions"] = []
        self.save_settings()


def create_updater(parent_window=None):
    """
    Factory function to create an updater instance
    
    Args:
        parent_window: Parent window for dialogs
        
    Returns:
        AutoUpdater: Configured updater instance
    """
    # Use GitHub repository from config
    github_repo = config.GITHUB_REPO
    current_version = config.VERSION
    
    return AutoUpdater(github_repo, current_version, parent_window)


if __name__ == "__main__":
    # Test the updater
    from PyQt5.QtWidgets import QApplication
    
    app = QApplication(sys.argv)
    
    updater = create_updater()
    result = updater.manual_check()
    print(f"Update check result: {result}")
    
    app.exec_()
