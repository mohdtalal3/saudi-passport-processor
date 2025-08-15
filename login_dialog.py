import sys
import os
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                            QLineEdit, QPushButton, QMessageBox, QFrame,
                            QProgressBar, QApplication)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QIcon
from auth_manager import AuthManager

class AuthWorker(QThread):
    """Worker thread for authentication to avoid blocking UI"""
    auth_result = pyqtSignal(bool, str, dict)  # success, message, user_data
    
    def __init__(self, email):
        super().__init__()
        self.email = email
    
    def run(self):
        try:
            auth_manager = AuthManager()
            
            # Setup connection (sheet ID comes from config file)
            if not auth_manager.setup_connection():
                self.auth_result.emit(False, "Failed to connect to Google Sheets. Please check the configuration.", {})
                return
            
            # Verify user
            is_authorized, result = auth_manager.verify_user(self.email)
            
            if is_authorized:
                self.auth_result.emit(True, "Authentication successful!", result)
            else:
                self.auth_result.emit(False, result, {})
                
        except Exception as e:
            self.auth_result.emit(False, f"Authentication error: {str(e)}", {})

class LoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.user_data = None
        self.setupUI()
        
    def setupUI(self):
        self.setWindowTitle("Saudi Passport Processor - Login")
        self.setFixedSize(800, 800)
        self.setModal(True)
        
        # Set stylesheet
        self.setStyleSheet("""
QDialog {
    background-color: #f5f5f5;
}

QFrame {
    background-color: white;
    border-radius: 8px;
    border: 1px solid #e0e0e0;
    padding: 20px;  /* Adds space inside the frame */
}

QLabel {
    color: #333;
    font-size: 14px; /* Slightly larger text */
    margin-bottom: 6px; /* Space below label */
}

QLineEdit {
    padding: 10px;
    border: 2px solid #ddd;
    border-radius: 4px;
    font-size: 14px;
    background-color: white;
    margin-bottom: 12px; /* Space between inputs */
    min-width: 280px;    /* Wider input fields */
}

QLineEdit:focus {
    border-color: #4CAF50;
}

QPushButton {
    background-color: #4CAF50;
    color: white;
    border: none;
    padding: 12px 24px;
    border-radius: 4px;
    font-size: 14px;
    font-weight: bold;
    margin: 8px; /* Adds spacing between buttons */
    min-width: 100px;
}

QPushButton:hover {
    background-color: #45a049;
}

QPushButton:pressed {
    background-color: #3d8b40;
}

QPushButton:disabled {
    background-color: #cccccc;
    color: #666666;
}

QProgressBar {
    border: 2px solid #ddd;
    border-radius: 4px;
    text-align: center;
    height: 20px;
    margin-top: 12px; /* Space above progress bar */
}

QProgressBar::chunk {
    background-color: #4CAF50;
    border-radius: 2px;
}

        """)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(20)
        layout.setContentsMargins(30, 30, 30, 30)
        
        # Main frame
        main_frame = QFrame()
        main_layout = QVBoxLayout(main_frame)
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(20, 20, 20, 20)
        
        # Title
        title_label = QLabel("User Authentication")
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #2c3e50; margin-bottom: 10px;")
        title_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_label)
        
        # Subtitle
        subtitle_label = QLabel("Enter your authorized email address to access the application")
        subtitle_label.setStyleSheet("font-size: 11px; color: #666; margin-bottom: 15px;")
        subtitle_label.setAlignment(Qt.AlignCenter)
        subtitle_label.setWordWrap(True)
        main_layout.addWidget(subtitle_label)
        
        # Email input
        email_label = QLabel("Email Address:")
        email_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        main_layout.addWidget(email_label)
        
        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("Enter your authorized email address...")
        self.email_input.returnPressed.connect(self.authenticate)
        main_layout.addWidget(self.email_input)
        
        # Progress bar (initially hidden)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setRange(0, 0)  # Indeterminate progress
        main_layout.addWidget(self.progress_bar)
        
        # Status label
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #666; font-size: 11px; margin: 5px;")
        self.status_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(self.status_label)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setStyleSheet("background-color: #f44336;")
        self.cancel_btn.clicked.connect(self.reject)
        
        self.login_btn = QPushButton("Login")
        self.login_btn.clicked.connect(self.authenticate)
        self.login_btn.setDefault(True)
        
        button_layout.addWidget(self.cancel_btn)
        button_layout.addWidget(self.login_btn)
        
        main_layout.addLayout(button_layout)
        
        # Help text
        help_label = QLabel("Need access? Contact your administrator to add your email to the authorized users list.\n\nNote: All configuration is handled automatically.")
        help_label.setStyleSheet("font-size: 10px; color: #888; margin-top: 15px;")
        help_label.setAlignment(Qt.AlignCenter)
        help_label.setWordWrap(True)
        main_layout.addWidget(help_label)
        
        layout.addWidget(main_frame)
        
    def authenticate(self):
        email = self.email_input.text().strip()
        
        if not email:
            QMessageBox.warning(self, "Warning", "Please enter your email address.")
            self.email_input.setFocus()
            return
            
        if "@" not in email:
            QMessageBox.warning(self, "Warning", "Please enter a valid email address.")
            self.email_input.setFocus()
            return
        
        # Disable inputs and show progress
        self.set_loading_state(True)
        self.status_label.setText("Authenticating...")
        
        # Start authentication in background thread
        self.auth_worker = AuthWorker(email)
        self.auth_worker.auth_result.connect(self.on_auth_result)
        self.auth_worker.start()
        
    def set_loading_state(self, loading):
        """Enable/disable inputs during loading"""
        self.email_input.setEnabled(not loading)
        self.login_btn.setEnabled(not loading)
        self.progress_bar.setVisible(loading)
        
        if loading:
            self.login_btn.setText("Authenticating...")
        else:
            self.login_btn.setText("Login")
    
    def on_auth_result(self, success, message, user_data):
        """Handle authentication result"""
        self.set_loading_state(False)
        
        if success:
            self.user_data = user_data
            self.status_label.setText("✓ Authentication successful!")
            self.status_label.setStyleSheet("color: #4CAF50; font-size: 11px; margin: 5px;")
            
            QMessageBox.information(
                self, 
                "Login Successful", 
                f"Welcome! You are now authenticated as:\n{user_data.get('email', 'Unknown')}"
            )
            self.accept()
        else:
            self.status_label.setText("❌ Authentication failed")
            self.status_label.setStyleSheet("color: #f44336; font-size: 11px; margin: 5px;")
            
            QMessageBox.critical(
                self, 
                "Authentication Failed", 
                message
            )
    
    def get_user_data(self):
        """Get authenticated user data"""
        return self.user_data

# Test the login dialog if run directly
if __name__ == "__main__":
    app = QApplication(sys.argv)
    dialog = LoginDialog()
    
    if dialog.exec_() == QDialog.Accepted:
        user_data = dialog.get_user_data()
        print(f"Login successful: {user_data}")
    else:
        print("Login cancelled")
    
    sys.exit()
