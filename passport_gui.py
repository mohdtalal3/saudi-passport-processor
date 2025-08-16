import sys
import os
import json
import shutil
import time
import re
from datetime import datetime, date
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                            QTextEdit, QFileDialog, QCheckBox, QMessageBox,
                            QProgressBar, QFrame, QScrollArea, QListWidget,
                            QListWidgetItem, QDialog, QDialogButtonBox, QTableWidget,
                            QTableWidgetItem, QHeaderView, QComboBox)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QPalette, QColor, QPixmap, QIcon
import google.generativeai as genai
import requests
from seleniumbase import SB
import config
from login_dialog import LoginDialog
from auth_manager import AuthManager
from updater import AutoUpdater

class CompanionMappingDialog(QDialog):
    """Dialog for mapping child passports to companion passport numbers"""
    
    def __init__(self, passport_files, parent=None):
        super().__init__(parent)
        self.passport_files = passport_files
        self.companion_mappings = {}  # {passport_file: companion_passport_number}
        self.initUI()
    
    def initUI(self):
        self.setWindowTitle("Child Companion Mapping")
        self.setModal(True)
        self.resize(600, 400)
        
        layout = QVBoxLayout(self)
        
        # Instructions
        instructions = QLabel(
            "For each child passport, enter their companion's passport number.\n"
            "Leave empty if the person is not a child or doesn't have a companion."
        )
        instructions.setStyleSheet("color: #666; margin-bottom: 15px; font-size: 11px;")
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        
        # Table for mapping
        self.table = QTableWidget(len(self.passport_files), 2)
        self.table.setHorizontalHeaderLabels(["Passport File", "Companion Passport Number"])
        
        # Set column widths
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        self.table.setColumnWidth(1, 200)
        
        # Populate table
        for i, file_path in enumerate(self.passport_files):
            filename = os.path.basename(file_path)
            
            # Passport file name (read-only)
            file_item = QTableWidgetItem(filename)
            file_item.setFlags(file_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 0, file_item)
            
            # Companion passport number input
            companion_input = QLineEdit()
            companion_input.setPlaceholderText("Enter companion's passport number")
            self.table.setCellWidget(i, 1, companion_input)
        
        layout.addWidget(self.table)
        
        # Buttons
        button_layout = QHBoxLayout()
        
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(self.accept)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        
        button_layout.addStretch()
        button_layout.addWidget(cancel_button)
        button_layout.addWidget(ok_button)
        
        layout.addLayout(button_layout)
    
    def get_companion_mappings(self):
        """Get the companion mappings from the dialog"""
        mappings = {}
        for i in range(len(self.passport_files)):
            file_path = self.passport_files[i]
            companion_input = self.table.cellWidget(i, 1)
            companion_passport = companion_input.text().strip()
            if companion_passport:
                mappings[file_path] = companion_passport
        return mappings

class TokenExtractor:
    """Handle automatic token extraction using Selenium"""
    
    def __init__(self):
        self.base_dir = os.path.abspath(os.getcwd())
        self.chrome_data_path = os.path.join(self.base_dir, "chromedata 14-47-30-348")
        self.TARGET_KEYS = {
            "authorization": "bearer_token",
            "entity-id": "entity_id",
            "activeentityid": "active_entity_id",
            "activeentitytypeid": "active_entity_type_id",
            "contractid": "contract_id",
        }
    
    def _maybe_parse_json(self, text):
        try:
            return json.loads(text)
        except Exception:
            return None

    def _harvest_from_headers(self, headers, out):
        """Grab fields from any headers dict (case-insensitive)."""
        if not isinstance(headers, dict):
            return
        lower_map = {str(k).lower(): v for k, v in headers.items()}
        # Authorization needs Bearer
        if "authorization" in lower_map:
            val = lower_map["authorization"]
            if isinstance(val, str) and val.strip().lower().startswith("bearer "):
                out["bearer_token"] = val
        # Other target keys
        for k_lower, out_key in self.TARGET_KEYS.items():
            if k_lower == "authorization":
                continue
            if k_lower in lower_map and lower_map[k_lower]:
                out[out_key] = str(lower_map[k_lower])

    def _harvest_from_body(self, body_text, out):
        """If request body is JSON, try to pick values from there too."""
        if not body_text:
            return
        data = self._maybe_parse_json(body_text)
        if not isinstance(data, (dict, list)):
            return
        # Normalize to list of dicts for easy scanning
        dicts = data if isinstance(data, list) else [data]
        for d in dicts:
            if not isinstance(d, dict):
                continue
            lower_map = {str(k).lower(): v for k, v in d.items()}
            for k_lower, out_key in self.TARGET_KEYS.items():
                if k_lower in lower_map and lower_map[k_lower]:
                    out[out_key] = str(lower_map[k_lower])

    def extract_fields(self, json_file_path):
        """Extract Bearer token and other fields from CDP performance logs"""
        out = {
            "bearer_token": None,
            "entity_id": None,
            "active_entity_id": None,
            "active_entity_type_id": None,
            "contract_id": None,
        }

        with open(json_file_path, "r") as f:
            cdp_logs = json.load(f)

        for entry in cdp_logs:
            msg_str = entry.get("message")
            if not msg_str:
                continue

            parsed = self._maybe_parse_json(msg_str)
            if not parsed or "message" not in parsed:
                continue

            message = parsed["message"]
            params = message.get("params", {})

            # 1) Request headers (Network.requestWillBeSent)
            if "request" in params and isinstance(params["request"], dict):
                req = params["request"]
                self._harvest_from_headers(req.get("headers", {}), out)

                # Some drivers include postData; if present, scan it
                post_data = req.get("postData") or req.get("postDataEntries")
                if isinstance(post_data, list):  # Chrome sometimes splits it
                    for p in post_data:
                        if isinstance(p, dict):
                            self._harvest_from_body(p.get("bytes") or p.get("data") or "", out)
                        elif isinstance(p, str):
                            self._harvest_from_body(p, out)
                elif isinstance(post_data, str):
                    self._harvest_from_body(post_data, out)

            # 2) Extra request headers (Network.requestWillBeSentExtraInfo)
            if message.get("method") == "Network.requestWillBeSentExtraInfo":
                self._harvest_from_headers(params.get("headers", {}), out)

            # 3) Response headers (Network.responseReceived / ExtraInfo)
            if "response" in params and isinstance(params["response"], dict):
                resp = params["response"]
                self._harvest_from_headers(resp.get("headers", {}), out)

            if message.get("method") == "Network.responseReceivedExtraInfo":
                self._harvest_from_headers(params.get("headers", {}), out)

        return out

    def get_tokens_from_browser(self):
        """Open browser, let user login, and extract tokens"""
        try:
            with SB(uc=True, headless=False, log_cdp_events=True, user_data_dir=self.chrome_data_path) as sb:
                sb.open("https://masar.nusuk.sa/pub/login")
                
                # Show message to user
                msg = QMessageBox()
                msg.setWindowTitle("Login Required")
                msg.setText("Please complete the login process in the browser window that just opened.\n\nClick 'Continue' when you have successfully logged in.")
                msg.setStandardButtons(QMessageBox.Ok)
                msg.setDefaultButton(QMessageBox.Ok)
                msg.exec_()
                
                # Get CDP logs
                cdp_logs = sb.driver.get_log("performance")
                logs_file_path = os.path.abspath("cdp_logs.json")
                with open(logs_file_path, "w", encoding="utf-8") as f:
                    json.dump(cdp_logs, f, indent=2, ensure_ascii=False)

                # Extract tokens
                extracted = self.extract_fields(logs_file_path)
                # Clean up log file
                if os.path.exists(logs_file_path):
                    os.remove(logs_file_path)
                
                return extracted
                
        except Exception as e:
            raise Exception(f"Failed to extract tokens: {str(e)}")


class PassportProcessor(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal()
    error_signal = pyqtSignal(str)
    auth_required_signal = pyqtSignal()
    
    def __init__(self, passport_files, email, phone_number, create_group, group_name, token_data, user_data, use_separate_iqama=False, iqama_image_path=None, iqama_number=None, companion_mappings=None):
        super().__init__()
        self.passport_files = passport_files
        self.email = email
        self.phone_number = phone_number
        self.create_group = create_group
        self.group_name = group_name
        self.user_data = user_data  # Store user data from authentication
        self.use_separate_iqama = use_separate_iqama
        self.iqama_image_path = iqama_image_path
        self.iqama_number = iqama_number
        self.companion_mappings = companion_mappings or {}  # Store companion mappings
        # Use extracted token data passed from the GUI
        self.token_data = token_data
        self.processed_folder = os.path.join(os.getcwd(), "processed_passports")
        self.under_age_folder = os.path.join(os.getcwd(), "under_age_passports")
        self.error_folder = os.path.join(os.getcwd(), "error_passports")
        self.mutamer_ids = []
        self.should_stop_processing = False
        self.processed_count = 0
        self.error_count = 0
        self.under_age_count = 0
        
        # Configure Gemini API with user's API key from Google Sheets
        user_api_key = self.user_data.get('api_key') if self.user_data else None
        if user_api_key:
            # Convert to string and check if it's valid
            user_api_key_str = str(user_api_key).strip()
            if user_api_key_str and user_api_key_str != '0':
                # Use API key from Google Sheets (authenticated user's data)
                genai.configure(api_key=user_api_key_str)
                self.model = genai.GenerativeModel("gemini-2.5-flash-lite")
                print("Using API key from Google Sheets user data")
            else:
                # Only fallback to config if user has no valid API key in Google Sheets
                genai.configure(api_key=config.GENAI_API_KEY)
                self.model = genai.GenerativeModel("gemini-2.5-flash-lite")
                print("Using fallback API key from config (user has no valid API key in Google Sheets)")
        else:
            # Only fallback to config if user has no API key in Google Sheets
            genai.configure(api_key=config.GENAI_API_KEY)
            self.model = genai.GenerativeModel("gemini-2.5-flash-lite")
            print("Using fallback API key from config (user has no API key in Google Sheets)")
        
        # Create processed and under-age folders if they don't exist
        if not os.path.exists(self.processed_folder):
            os.makedirs(self.processed_folder)
        if not os.path.exists(self.under_age_folder):
            os.makedirs(self.under_age_folder)
        if not os.path.exists(self.error_folder):
            os.makedirs(self.error_folder)
        
        # Set up common headers with extracted token data
        self.headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate, zstd",
            "Accept-Language": "en",
            "Connection": "keep-alive",
            "Entity-Id": self.token_data.get("entity_id"),
            "Host": "masar.nusuk.sa",
            "Origin": "https://masar.nusuk.sa",
            "Authorization": self.token_data.get("bearer_token", ""),
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
            "activeentityid": self.token_data.get("active_entity_id"),
            "activeentitytypeid": self.token_data.get("active_entity_type_id"),
            "contractId": self.token_data.get("contract_id"),
            "sec-ch-ua": "\"Not)A;Brand\";v=\"8\", \"Chromium\";v=\"138\", \"Google Chrome\";v=\"138\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"macOS\""
        }
    
    def calculate_age(self, birth_date_str):
        """Calculate age from birth date string"""
        try:
            if not birth_date_str:
                return None
            
            # Try different date formats
            date_formats = ["%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%d/%m/%Y"]
            birth_date = None
            
            for fmt in date_formats:
                try:
                    birth_date = datetime.strptime(birth_date_str, fmt).date()
                    break
                except ValueError:
                    continue
            
            if not birth_date:
                return None
            
            today = date.today()
            age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
            return age
            
        except Exception as e:
            self.log_signal.emit(f"Error calculating age: {str(e)}")
            return None
    
    def move_under_age_file(self, file_path, passport_data):
        """Move under-age passport to separate folder"""
        try:
            filename = os.path.basename(file_path)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Include person's name in filename if available
            name_part = ""
            if passport_data.get('first_name') and passport_data.get('last_name'):
                name_part = f"_{passport_data['first_name']}_{passport_data['last_name']}"
            
            new_filename = f"{timestamp}{name_part}_UNDERAGE_{filename}"
            destination = os.path.join(self.under_age_folder, new_filename)
            shutil.move(file_path, destination)
            self.log_signal.emit(f"Moved under-age passport to: {destination}")
            return True
        except Exception as e:
            self.log_signal.emit(f"Warning: Could not move under-age file {file_path}: {str(e)}")
            return False
    
    def move_error_file(self, file_path, error_message):
        """Move error passport to separate folder"""
        try:
            filename = os.path.basename(file_path)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Clean error message for filename (remove special characters)
            error_part = re.sub(r'[^\w\s-]', '', error_message.replace(' ', '_'))[:50]
            
            new_filename = f"{timestamp}_ERROR_{error_part}_{filename}"
            destination = os.path.join(self.error_folder, new_filename)
            shutil.move(file_path, destination)
            self.log_signal.emit(f"Moved error passport to: {destination}")
            return True
        except Exception as e:
            self.log_signal.emit(f"Warning: Could not move error file {file_path}: {str(e)}")
            return False
    
    def run(self):
        try:
            self.log_signal.emit("Starting passport processing with API requests...")
            self.log_signal.emit("Note: Any passport files with errors will be automatically moved to 'error_passports' folder for re-processing later.")
            self.log_signal.emit("")  # Empty line for readability
            # Process each passport
            total_passports = len(self.passport_files)
            for i, passport_file in enumerate(self.passport_files):
                try:
                    # Check if processing should be stopped
                    if self.should_stop_processing:
                        self.log_signal.emit("Processing stopped due to authentication error.")
                        break
                    
                    self.log_signal.emit(f"\nProcessing passport {i+1}/{total_passports}: {os.path.basename(passport_file)}")
                    
                    # Extract data using Gemini
                    passport_data = self.extract_passport_data(passport_file)
                    print(passport_data)
                    if not passport_data:
                        self.log_signal.emit(f"Failed to extract data from {os.path.basename(passport_file)}")
                        continue
                    
                    self.log_signal.emit(f"Extracted data: {passport_data.get('first_name', 'Unknown')} {passport_data.get('last_name', 'Unknown')}")
                    
                    # Check age
                    birth_date = passport_data.get('date_of_birth')
                    if birth_date:
                        age = self.calculate_age(birth_date)
                        if age is not None:
                            self.log_signal.emit(f"Calculated age: {age} years")
                            
                            if age < 0:
                                self.log_signal.emit(f"UNDER-AGE DETECTED: Person is {age} years old (under 18). Moving to under-age folder.")
                                if self.move_under_age_file(passport_file, passport_data):
                                    # Update progress and continue to next passport
                                    self.under_age_count += 1
                                    progress = int((i + 1) / total_passports * 100)
                                    self.progress_signal.emit(progress)
                                    continue
                        else:
                            self.log_signal.emit("Could not calculate age from birth date")
                    else:
                        self.log_signal.emit("No birth date found in passport data")
                    
                    # Clean special characters from English names
                    passport_data = self.clean_passport_data(passport_data)
                    
                    # Process using API requests
                    mutamer_id = self.process_passport_api(passport_file, passport_data, self.use_separate_iqama, self.iqama_image_path, self.iqama_number)
                    if mutamer_id:
                        self.mutamer_ids.append(mutamer_id)
                        self.log_signal.emit(f"Created mutamer with ID: {mutamer_id}")
                    
                    # Move processed file
                    self.move_processed_file(passport_file)
                    
                    # Update progress
                    progress = int((i + 1) / total_passports * 100)
                    self.progress_signal.emit(progress)
                    
                    self.processed_count += 1
                    self.log_signal.emit(f"Successfully processed {os.path.basename(passport_file)}")
                    
                except requests.exceptions.HTTPError as e:
                    if e.response.status_code == 401:
                        self.log_signal.emit("AUTHENTICATION FAILED: 401 Unauthorized")
                        self.error_signal.emit("Authorization failed. Please try again with a valid token.")
                        self.should_stop_processing = True
                        break
                    else:
                        error_msg = f"HTTP Error {e.response.status_code}: {str(e)}"
                        self.log_signal.emit(f"Error processing {os.path.basename(passport_file)}: {error_msg}")
                        self.move_error_file(passport_file, error_msg)
                        self.error_count += 1
                        continue
                except Exception as e:
                    error_msg = str(e)
                    if "401" in error_msg or "Unauthorized" in error_msg:
                        self.log_signal.emit("AUTHENTICATION FAILED: Unauthorized access")
                        self.error_signal.emit("Authorization failed. Please try again with a valid token.")
                        self.should_stop_processing = True
                        break
                    else:
                        self.log_signal.emit(f"Error processing {os.path.basename(passport_file)}: {error_msg}")
                        self.move_error_file(passport_file, error_msg)
                        self.error_count += 1
                        continue
            
            # Handle group creation if requested
            if self.create_group and self.mutamer_ids:
                self.log_signal.emit(f"\nCreating group '{self.group_name}' with {len(self.mutamer_ids)} mutamers...")
                self.create_group_api()
            
            # Show processing summary
            self.log_signal.emit(f"\n" + "="*50)
            self.log_signal.emit(f"PROCESSING SUMMARY:")
            self.log_signal.emit(f"Total passports: {total_passports}")
            self.log_signal.emit(f"Successfully processed: {self.processed_count}")
            self.log_signal.emit(f"Under-age (moved): {self.under_age_count}")
            self.log_signal.emit(f"Errors (moved to error folder): {self.error_count}")
            if self.error_count > 0:
                self.log_signal.emit(f"Error passports moved to: {self.error_folder}")
                self.log_signal.emit(f"You can fix issues and re-run the error passports.")
            self.log_signal.emit(f"="*50)
            
            if self.processed_count > 0:
                self.log_signal.emit(f"\n✅ Successfully completed processing {self.processed_count} passports!")
            else:
                self.log_signal.emit(f"\n⚠️ No passports were processed successfully.")
            
        except Exception as e:
            self.error_signal.emit(f"Critical error: {str(e)}")
        finally:
            self.finished_signal.emit()
    
    def clean_passport_data(self, passport_data):
        """Remove special characters from English names in passport data"""
        if not passport_data:
            return passport_data
        
        # Names that need special character removal
        name_fields = ['first_name', 'last_name', 'husband_name', 'father_name']
        
        for field in name_fields:
            if field in passport_data:
                field_value = passport_data[field]
                
                # Handle None, null, empty string, or whitespace-only values
                if field_value is None or field_value == "null" or field_value == "":
                    passport_data[field] = ""
                    continue
                
                # Convert to string and check if it's meaningful
                field_str = str(field_value).strip()
                if not field_str or field_str.lower() == "null" or field_str.lower() == "none":
                    passport_data[field] = ""
                    continue
                
                # Remove special characters, keep only letters and spaces
                cleaned_name = re.sub(r'[^A-Za-z\s]', '', field_str)
                # Remove extra spaces and strip
                cleaned_name = ' '.join(cleaned_name.split())
                
                # If after cleaning there's nothing left, set to empty string
                passport_data[field] = cleaned_name if cleaned_name else ""
                
        return passport_data
    
    def extract_passport_data(self, image_path):
        try:
            with open(image_path, "rb") as f:
                image_bytes = f.read()
            
            prompt = """
        You are an expert OCR and document extraction model.

        Analyze the uploaded passport image and return the information in the following JSON format only:

        {
        "document_type": "passport",
        "country": "",
        "passport_number": "",
        "first_name": "",         // remove any special characters 
        "last_name": "",           // remove any special characters 
        "arabic_first_name": "",  // Convert English name to Arabic
        "arabic_last_name": "",  // Convert English name to Arabic
        "date_of_birth": "",
        "place_of_birth": "",
        "city": "",              // Extract city from place of birth if available
        "nationality": "",
        "sex": "",
        "date_of_issue": "",
        "date_of_expiry": "",
        "issuing_authority": "",
        "cnic_number": "",       // Computerized National Identity Card number if available
        "tracking_number": "",   // Tracking number if available
        "booklet_number": "",    // Booklet number if available
        "husband_name": "",      // If "Husband Name" field contains husband name. // remove any special characters  
        "husband_arabic_name": "",  // Convert English name to Arabic
        "father_name": "",       // If "Father Name" field contains father name. // remove any special characters
        "father_arabic_name": "",   // Convert English name to Arabic
        "married": ""            // True if husband name exists, False if father name exists
        }

        - Do not include any explanations or text outside the JSON.
        - If a field is not found, leave it as an empty string.
        - Date must be in the format of YYYY-MM-DD.
        - Use English unless explicitly asked to translate to Arabic (like for full name).
        """
            
            response = self.model.generate_content([prompt, {"mime_type": "image/jpeg", "data": image_bytes}])
            response_text = response.text.strip()
            
            # Clean response
            if response_text.startswith('```json'):
                response_text = response_text[7:]
            if response_text.startswith('```'):
                response_text = response_text[3:]
            if response_text.endswith('```'):
                response_text = response_text[:-3]
            
            response_text = response_text.replace('`', '').strip()
            #print(f"Extracted response: {response_text}")  # Debug log
            return json.loads(response_text)
            
        except Exception as e:
            self.log_signal.emit(f"OCR Error: {str(e)}")
            return None
    
    def process_passport_api(self, image_path, passport_data, use_separate_iqama=False, iqama_image_path=None, iqama_number=None):
        """Process passport using API requests instead of selenium"""
        try:
            # Step 1: Scan passport
            self.log_signal.emit("Step 1: Scanning passport with API...")
            scanned_data = self.scan_passport_api(image_path)
            if not scanned_data:
                return None
            # Step 2: Submit initial passport info
            self.log_signal.emit("Step 2: Submitting passport information...")
            initial_response = self.submit_initial_info_api(scanned_data,passport_data)
            mutamer_id = initial_response["response"]["data"]["id"]
            # Step 3: Look up companion ID if this passport has a companion mapping
            companion_id = None
            companion_gender = None
            if image_path in self.companion_mappings:
                companion_passport_number = self.companion_mappings[image_path]
                self.log_signal.emit(f"Looking up companion for passport: {companion_passport_number}")
                companion_id, companion_gender = self.find_companion_id_by_passport(companion_passport_number, mutamer_id)
                if companion_id:
                    self.log_signal.emit(f"Found companion ID: {companion_id}")
                else:
                    self.log_signal.emit(f"Warning: Could not find companion with passport number: {companion_passport_number}")
            
            # Step 4: Upload additional documents
            self.log_signal.emit("Step 4: Uploading additional documents...")
            
            # Use separate Iqama image if checkbox is checked and image is provided
            if use_separate_iqama and iqama_image_path:
                self.log_signal.emit("Using separate Iqama image...")
                iqama_data = self.upload_attachment_api(iqama_image_path, 2)  # Use separate Iqama image
            else:
                self.log_signal.emit("Using passport image for Iqama...")
                iqama_data = self.upload_attachment_api(image_path, 2)  # Use passport image for Iqama
                
            vaccine_data = self.upload_attachment_api(image_path, 3)  # Vaccine certificate
            
            # Step 5: Submit full personal info (with companion ID if available)
            self.log_signal.emit("Step 5: Submitting personal and contact information...")
            self.submit_full_info_api(mutamer_id, scanned_data, passport_data, iqama_data, vaccine_data, use_separate_iqama, iqama_number, companion_id, companion_gender)
            
            # Step 6: Submit disclosure form
            self.log_signal.emit("Step 6: Submitting disclosure form...")
            self.submit_disclosure_api(mutamer_id)
            
            return mutamer_id
            
        except Exception as e:
            raise Exception(f"API processing error: {str(e)}")
    
    def scan_passport_api(self, image_path):
        """Step 1: Scan passport using API"""
        if not os.path.exists(image_path):
            raise Exception(f"Image file not found: {image_path}")
        
        url = "https://masar.nusuk.sa/umrah/groups_apis/api/Mutamer/ScanPassport"
        headers = self.headers.copy()
        headers["Referer"] = "https://masar.nusuk.sa/umrah/mutamer/add-mutamer"
        
        try:
            with open(image_path, 'rb') as f:
                files = {'passportImage': (os.path.basename(image_path), f, 'image/jpeg')}
                response = requests.post(url, headers=headers, files=files, timeout=30)
                
                if response.status_code == 401:
                    raise requests.exceptions.HTTPError("401 Unauthorized - Invalid authentication token", response=response)
                
                response.raise_for_status()
                return response.json()
                
        except requests.exceptions.Timeout:
            raise Exception("Request timeout - API server is not responding")
        except requests.exceptions.ConnectionError:
            raise Exception("Connection error - Unable to connect to API server")
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                raise e  # Re-raise 401 errors to be handled by main loop
            else:
                raise Exception(f"HTTP {e.response.status_code}: {e.response.text}")
        except Exception as e:
            raise Exception(f"Passport scanning failed: {str(e)}")

    def submit_initial_info_api(self, scanned_data, passport_data):
        """Step 2: Submit initial passport information"""
        try:
            passport = scanned_data["response"]["data"]["passportResponse"]
            
            payload = {
                "id": None,
                "firstName": {"en": passport.get("firstNameEn")},
                "familyName": {"en": passport.get("familyNameEn")},
                "previousNationalityId": None,
                "gender": passport.get("gender"),
                "passportTypeId": 1,
                "birthDate": passport.get("birthDate"),
                "passportExpiryDate": passport.get("passportExpiryDate"),
                "passportIssueDate": passport.get("passportIssueDate"),
                "nationalityId": passport.get("nationalityId"),
                "issueCountryId": passport.get("countryId"),
                "passportNumber": passport.get("passportNumber"),
                "issueCityName": passport_data.get("city") or "".strip(),
                "personalPicture": None,
                "passportImage": {
                    "fileName": passport["passportImage"]["fileName"],
                    "fileSize": passport["passportImage"]["fileSize"],
                    "fileExtension": passport["passportImage"]["fileExtension"],
                    "id": passport["passportImage"]["id"]
                },
                "passportPictureId": passport["passportImage"]["id"],
                "personalPictureId": passport["personalPicture"]["id"],
                "signature": passport.get("signature")
            }
            
            url = "https://masar.nusuk.sa/umrah/groups_apis/api/Mutamer/SubmitPassportInforamtionWithNationality"
            headers = self.headers.copy()
            headers["Content-Type"] = "application/json"
            
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            
            if response.status_code == 401:
                raise requests.exceptions.HTTPError("401 Unauthorized - Invalid authentication token", response=response)
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.Timeout:
            raise Exception("Request timeout - API server is not responding")
        except requests.exceptions.ConnectionError:
            raise Exception("Connection error - Unable to connect to API server")
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                raise e  # Re-raise 401 errors to be handled by main loop
            else:
                raise Exception(f"HTTP {e.response.status_code}: {e.response.text}")
        except KeyError as e:
            raise Exception(f"Missing required data in API response: {str(e)}")
        except Exception as e:
            raise Exception(f"Failed to submit initial passport info: {str(e)}")
    
    def upload_attachment_api(self, file_path, file_type_code):
        """Upload attachment (iqama/vaccine certificate)"""
        if not os.path.exists(file_path):
            raise Exception(f"Attachment file not found: {file_path}")
        
        url = "https://masar.nusuk.sa/umrah/common_apis/api/Attachment/Upload"
        headers = self.headers.copy()
        headers["Referer"] = "https://masar.nusuk.sa/umrah/mutamer/add-mutamer"
        
        try:
            with open(file_path, 'rb') as f:
                files = {
                    'type': (None, str(file_type_code)),
                    'file': (os.path.basename(file_path), f, 'application/octet-stream')
                }
                response = requests.post(url, headers=headers, files=files, timeout=30)
                
                if response.status_code == 401:
                    raise requests.exceptions.HTTPError("401 Unauthorized - Invalid authentication token", response=response)
                
                response.raise_for_status()
                result = response.json().get("response", {}).get("data", {}).get("attachmentResponse")
                
                if not result:
                    raise Exception("No attachment data returned from API")
                
                return result
                
        except requests.exceptions.Timeout:
            raise Exception("Request timeout - API server is not responding")
        except requests.exceptions.ConnectionError:
            raise Exception("Connection error - Unable to connect to API server")
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                raise e  # Re-raise 401 errors to be handled by main loop
            else:
                raise Exception(f"HTTP {e.response.status_code}: {e.response.text}")
        except Exception as e:
            raise Exception(f"Failed to upload attachment: {str(e)}")
    
    def submit_full_info_api(self, mutamer_id, scanned_data, personal_data, iqama_data, vaccine_data, use_separate_iqama=False, custom_iqama_number=None, companion_id=None, companion_gender=None):
        """Step 3: Submit full personal and contact information"""
        passport = scanned_data["response"]["data"]["passportResponse"]
        
        # Extract iqama number - use custom number if provided, otherwise extract from passport
        if use_separate_iqama and custom_iqama_number:
            iqama_number = custom_iqama_number.strip()
            self.log_signal.emit(f"Using custom Iqama number: {iqama_number}")
        else:
            match = re.search(r'(\d+)(?!.*\d)', personal_data.get("passport_number", ""))
            iqama_number = match.group(1) if match else ""
            self.log_signal.emit(f"Using extracted Iqama number from passport: {iqama_number}")
        
        # Marital status & profession rules
        martial_status_map = {"single": 1, "married": 2}
        if personal_data.get("sex") == "M":
            martial_status_id = martial_status_map["single"]
            profession = "Business"
        elif personal_data.get("sex") == "F" and personal_data.get("married"):
            martial_status_id = martial_status_map["married"]
            profession = "Housewife"
        else:
            martial_status_id = martial_status_map["single"]
            profession = "Household"
        
        # Handle last name logic - if empty, use father's name or husband's name
        last_name_en = (personal_data.get("last_name") or "").strip()
        last_name_ar = (personal_data.get("arabic_last_name") or "").strip()
        
        if not last_name_en:
            # Check if married and husband name is available
            if personal_data.get("married") and personal_data.get("husband_name"):
                husband_name = (personal_data.get("husband_name") or "").strip()
                if husband_name:
                    # Split by space and use first part
                    last_name_en = husband_name
                    # Use husband's Arabic first name if available, otherwise keep empty
                    husband_arabic = personal_data.get("husband_arabic_name")
                    if husband_arabic:
                        last_name_ar = (husband_arabic or "")
            # If not married or no husband name, use father's name
            elif personal_data.get("father_name"):
                father_name = (personal_data.get("father_name") or "").strip()
                if father_name:
                    # Split by space and use first part
                    last_name_en = father_name
                    # Use father's Arabic first name if available, otherwise keep empty
                    father_arabic = personal_data.get("father_arabic_name")
                    if father_arabic:
                        last_name_ar = (father_arabic or "")
        last_name_en = re.sub(r"[^A-Za-z\s]", "", last_name_en)
        payload = {
            "id": mutamer_id,
            "firstName": {"en": personal_data.get("first_name"), "ar": personal_data.get("arabic_first_name")},
            "secondName": {"en": None, "ar": None},
            "thirdName": {"en": None, "ar": None},
            "familyName": {"en": last_name_en, "ar": last_name_ar},
            "martialStatusId": martial_status_id,
            "birthDate": personal_data.get("date_of_birth"),
            "profession": profession,
            "gender": 1 if personal_data.get("sex") == "M" else 2,
            "personalPictureId": passport["personalPicture"]["id"],
            "personalPicture": {
                "id": passport["personalPicture"]["id"],
                "fileName": passport["personalPicture"]["fileName"],
                "fileExtension": passport["personalPicture"]["fileExtension"],
                "mimeType": passport["personalPicture"]["mimeType"],
                "fileSize": passport["personalPicture"]["fileSize"],
                "type": passport["personalPicture"]["type"]
            },
            "residencyPictureId": iqama_data["id"],
            "residencyPicture": {
                "fileName": iqama_data["fileName"],
                "fileSize": iqama_data["fileSize"],
                "id": iqama_data["id"],
                "fileExtension": iqama_data["fileExtension"],
                "showDelete": iqama_data.get("showDelete", True)
            },
            "residencyNumber": iqama_number,
            "residencyExpirationDate": personal_data.get("date_of_expiry"),
            "vaccinationPictureId": vaccine_data["id"],
            "vaccinationPicture": {
                "fileName": vaccine_data["fileName"],
                "fileSize": vaccine_data["fileSize"],
                "id": vaccine_data["id"],
                "fileExtension": vaccine_data["fileExtension"],
                "showDelete": vaccine_data.get("showDelete", True)
            },
            "email": self.email,
            "phone": {"countryCode": 92, "phoneNumber": self.phone_number},
            "mobileCountryKey": 92,
            "mobileNo": self.phone_number,
            "postalCode": "",
            "poBox": "",
            "birthCountryId": 92,
            "birthCityName": personal_data.get("city")
        }
        
        # Add companion ID if provided
        if companion_id:
            payload["companionId"] = companion_id
            
            # Set relationship ID based on companion's gender
            # 1 = Male companion (brother, father, husband, etc.)
            # 2 = Female companion (sister, mother, wife, etc.)
            if companion_gender == 1:  # Male companion
                payload["relativeRelationId"] = "1"
                self.log_signal.emit(f"Adding male companion ID: {companion_id} (relativeRelationId: 1)")
            elif companion_gender == 2:  # Female companion
                payload["relativeRelationId"] = "2"
                self.log_signal.emit(f"Adding female companion ID: {companion_id} (relativeRelationId: 2)")
            else:
                # Default to 1 if gender is unknown
                payload["relativeRelationId"] = "1"
                self.log_signal.emit(f"Adding companion ID: {companion_id} (unknown gender, defaulting relativeRelationId: 1)")
        
        url = "https://masar.nusuk.sa/umrah/groups_apis/api/Mutamer/SubmitPersonalAndContactInfos"
        headers = self.headers.copy()
        headers["Content-Type"] = "application/json"
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            
            if response.status_code == 401:
                raise requests.exceptions.HTTPError("401 Unauthorized - Invalid authentication token", response=response)
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.Timeout:
            raise Exception("Request timeout - API server is not responding")
        except requests.exceptions.ConnectionError:
            raise Exception("Connection error - Unable to connect to API server")
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                raise e  # Re-raise 401 errors to be handled by main loop
            else:
                raise Exception(f"HTTP {e.response.status_code}: {e.response.text}")
        except Exception as e:
            raise Exception(f"Failed to submit personal and contact info: {str(e)}")
    
    def submit_disclosure_api(self, mutamer_id):
        """Step 4: Submit disclosure form"""
        url = "https://masar.nusuk.sa/umrah/groups_apis/api/Mutamer/SubmitDisclosureForm"
        headers = self.headers.copy()
        headers["Content-Type"] = "application/json"
        
        # Base questions 1–11
        answers = [{"questionId": i, "answer": False, "simpleReason": None, "detailedAnswers": []} for i in range(1, 12)]
        
        # Question 12
        answers.append({
            "questionId": 12,
            "answer": False,
            "simpleReason": None,
            "detailedAnswers": [{"relativeName": None, "relationId": None}]
        })
        
        # Question 13
        answers.append({
            "questionId": 13,
            "answer": False,
            "simpleReason": None,
            "detailedAnswers": [{"travelFromDate": None, "travelToDate": None, "reasonOfTravel": None, "countryId": None}]
        })
        
        # Questions 14–16
        for i in range(14, 17):
            answers.append({
                "questionId": i,
                "answer": False,
                "simpleReason": None,
                "detailedAnswers": []
            })
        
        payload = {
            "muamerInformationId": mutamer_id,
            "answers": answers
        }
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            
            if response.status_code == 401:
                raise requests.exceptions.HTTPError("401 Unauthorized - Invalid authentication token", response=response)
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.Timeout:
            raise Exception("Request timeout - API server is not responding")
        except requests.exceptions.ConnectionError:
            raise Exception("Connection error - Unable to connect to API server")
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                raise e  # Re-raise 401 errors to be handled by main loop
            else:
                raise Exception(f"HTTP {e.response.status_code}: {e.response.text}")
        except Exception as e:
            raise Exception(f"Failed to submit disclosure form: {str(e)}")
    
    def get_mutamer_companions(self, mutamer_id):
        """Get list of companions for a mutamer and find companion ID by passport number"""
        try:
            url = "https://masar.nusuk.sa/umrah/groups_apis/api/Mutamer/GetListOfMutamerCompanions"
            headers = self.headers.copy()
            headers["Content-Type"] = "application/json"
            
            payload = {"id": mutamer_id}
            
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            
            if response.status_code == 401:
                raise requests.exceptions.HTTPError("401 Unauthorized - Invalid authentication token", response=response)
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.Timeout:
            raise Exception("Request timeout - API server is not responding")
        except requests.exceptions.ConnectionError:
            raise Exception("Connection error - Unable to connect to API server")
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                raise e  # Re-raise 401 errors to be handled by main loop
            else:
                raise Exception(f"HTTP {e.response.status_code}: {e.response.text}")
        except Exception as e:
            raise Exception(f"Failed to get mutamer companions: {str(e)}")
    
    def find_companion_id_by_passport(self, companion_passport_number, mutamer_id):
        """Find companion ID and gender by passport number"""
        try:
            self.log_signal.emit(f"Looking up companion with passport number: {companion_passport_number}")
            
            # Get companions list
            companions_response = self.get_mutamer_companions(mutamer_id)
            
            if not companions_response.get("response", {}).get("status"):
                self.log_signal.emit("Failed to get companions list")
                return None, None
            
            companions = companions_response.get("response", {}).get("data", {}).get("companions", [])
            
            # Find companion by passport number
            for companion in companions:
                if companion.get("passportNumber") == companion_passport_number:
                    companion_id = companion.get("id")
                    companion_gender = companion.get("gender")  # 1 = Male, 2 = Female
                    companion_name = companion.get("name", {}).get("en", "Unknown")
                    gender_text = "Male" if companion_gender == 1 else "Female" if companion_gender == 2 else "Unknown"
                    self.log_signal.emit(f"Found companion: {companion_name} (ID: {companion_id}, Gender: {gender_text})")
                    return companion_id, companion_gender
            
            self.log_signal.emit(f"No companion found with passport number: {companion_passport_number}")
            return None, None
            
        except Exception as e:
            self.log_signal.emit(f"Error finding companion: {str(e)}")
            return None, None
    
    def move_processed_file(self, file_path):
        try:
            filename = os.path.basename(file_path)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            new_filename = f"{timestamp}_{filename}"
            destination = os.path.join(self.processed_folder, new_filename)
            shutil.move(file_path, destination)
            self.log_signal.emit(f"Moved processed file to: {destination}")
        except Exception as e:
            self.log_signal.emit(f"Warning: Could not move file {file_path}: {str(e)}")
    
    def create_group_api(self):
        """Create group using API requests"""
        try:
            # Step 1: Create the group
            self.log_signal.emit(f"Creating group: {self.group_name}")
            url = "https://masar.nusuk.sa/umrah/groups_apis/api/Groups/CreateGroup"
            payload = {
                "name": self.group_name,
                "embassyId": 210,  # Islamabad embassy
                "note": "",
                "id": None
            }
            
            headers = self.headers.copy()
            headers["Content-Type"] = "application/json"
            headers["Referer"] = "https://masar.nusuk.sa/umrah/mutamer-group/add-group/create-group"
            
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            
            if response.status_code == 401:
                raise requests.exceptions.HTTPError("401 Unauthorized - Invalid authentication token", response=response)
            
            response.raise_for_status()
            group_data = response.json()
            group_id = group_data.get("response", {}).get("data", {}).get("id")
            
            if not group_id:
                self.log_signal.emit("Failed to create group - no group ID returned")
                return
            
            self.log_signal.emit(f"Group created successfully with ID: {group_id}")
            
            # Step 2: Assign mutamers to the group
            self.log_signal.emit(f"Assigning {len(self.mutamer_ids)} mutamers to group...")
            self.assign_mutamers_to_group(group_id)
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                self.log_signal.emit("AUTHENTICATION FAILED during group creation: 401 Unauthorized")
                self.error_signal.emit("Authorization failed during group creation. Please try again with a valid token.")
            else:
                self.log_signal.emit(f"Group creation HTTP error: {str(e)}")
        except Exception as e:
            self.log_signal.emit(f"Group creation error: {str(e)}")
    
    def assign_mutamers_to_group(self, group_id):
        """Assign mutamers to the created group"""
        try:
            url = "https://masar.nusuk.sa/umrah/groups_apis/api/Groups/AssignMutamers"
            payload = {
                "groupId": group_id,
                "mutamerIds": self.mutamer_ids
            }
            
            headers = self.headers.copy()
            headers["Content-Type"] = "application/json"
            headers["Referer"] = "https://masar.nusuk.sa/umrah/mutamer-group/add-group/create-group"
            
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            if response.status_code == 401:
                raise requests.exceptions.HTTPError("401 Unauthorized - Invalid authentication token", response=response)
            
            response.raise_for_status()
            
            self.log_signal.emit(f"Successfully assigned {len(self.mutamer_ids)} mutamers to group '{self.group_name}'")
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                self.log_signal.emit("AUTHENTICATION FAILED during mutamer assignment: 401 Unauthorized")
                self.error_signal.emit("Authorization failed during mutamer assignment. Please try again with a valid token.")
            else:
                self.log_signal.emit(f"Mutamer assignment HTTP error: {str(e)}")
        except Exception as e:
            self.log_signal.emit(f"Error assigning mutamers to group: {str(e)}")
    


class PassportGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.passport_files = []
        self.iqama_image_path = None  # Store separate Iqama image path
        self.token_data = None
        self.token_extractor = TokenExtractor()
        self.user_data = None  # Store authenticated user data
        self.auth_manager = None  # Authentication manager
        self.companion_mappings = {}  # Store companion mappings: {passport_file: companion_passport_number}
        
        # Initialize auto-updater
        self.updater = AutoUpdater(parent_widget=self)
        
        # Show login dialog first
        if not self.authenticate_user():
            sys.exit()  # Exit if authentication fails
        
        self.initUI()
        
        # Check for updates after UI is initialized
        self.check_for_updates_on_startup()
    
    def authenticate_user(self):
        """Show login dialog and authenticate user"""
        login_dialog = LoginDialog(self)
        
        if login_dialog.exec_() == QDialog.Accepted:
            self.user_data = login_dialog.get_user_data()
            
            # Create auth manager instance for later use
            self.auth_manager = AuthManager()
            self.auth_manager.setup_connection()
            
            return True
        else:
            return False
    
    def initUI(self):
        self.setWindowTitle("Saudi Passport Processor")
        self.setGeometry(100, 100, 1000, 500)
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f5f5f5;
            }
            QFrame {
                background-color: white;
                border-radius: 8px;
                border: 1px solid #e0e0e0;
            }
            QLabel {
                color: #333;
                font-size: 12px;
            }
            QLineEdit {
                padding: 8px;
                border: 2px solid #ddd;
                border-radius: 4px;
                font-size: 12px;
            }
            QLineEdit:focus {
                border-color: #4CAF50;
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
            QPushButton:disabled {
                background-color: #cccccc;
                color: #666666;
            }
            QTextEdit {
                border: 1px solid #ddd;
                border-radius: 4px;
                background-color: #fafafa;
                font-family: 'Consolas', 'Monaco', monospace;
                font-size: 11px;
            }
            QListWidget {
                border: 1px solid #ddd;
                border-radius: 4px;
                background-color: white;
            }
            QCheckBox {
                font-size: 12px;
                color: #333;
            }
            QProgressBar {
                border: 2px solid #ddd;
                border-radius: 4px;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
                border-radius: 2px;
            }
            QScrollArea {
                border: none;
                background-color: #f5f5f5;
            }
            QScrollBar:vertical {
                background-color: #f0f0f0;
                width: 12px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical {
                background-color: #c0c0c0;
                min-height: 20px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #a0a0a0;
            }
        """)
        
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Create menu bar
        self.create_menu_bar()
        
        # Main layout
        main_layout = QHBoxLayout(central_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        # Left panel
        left_panel = self.create_left_panel()
        main_layout.addWidget(left_panel, 1)
        
        # Right panel (logs)
        right_panel = self.create_right_panel()
        main_layout.addWidget(right_panel, 2)
    
    def create_left_panel(self):
        # Create scroll area for left panel
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        # Create the content widget that will go inside the scroll area
        content_widget = QWidget()
        left_layout = QVBoxLayout(content_widget)
        left_layout.setSpacing(15)
        left_layout.setContentsMargins(20, 20, 20, 20)
        
        # Title and user info
        title_label = QLabel("Passport Processor")
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #2c3e50; margin-bottom: 5px;")
        left_layout.addWidget(title_label)
        
        # User info section
        if self.user_data:
            user_frame = QFrame()
            user_frame.setStyleSheet("background-color: #e8f5e8; border: 1px solid #4CAF50; border-radius: 4px; padding: 5px;")
            user_layout = QVBoxLayout(user_frame)
            user_layout.setContentsMargins(10, 8, 10, 8)
            
            user_email_label = QLabel(f"👤 Logged in as: {self.user_data.get('email', 'Unknown')}")
            user_email_label.setStyleSheet("font-size: 11px; color: #2c5e2c; font-weight: bold;")
            
            api_status = "✓ API Key Configured" if self.user_data.get('api_key') else "⚠ No API Key"
            api_status_label = QLabel(f"🔑 {api_status}")
            api_status_label.setStyleSheet("font-size: 10px; color: #2c5e2c;")
            
            user_layout.addWidget(user_email_label)
            user_layout.addWidget(api_status_label)
            
            left_layout.addWidget(user_frame)
        
        # Logout button
        logout_btn = QPushButton("Logout & Switch User")
        logout_btn.setStyleSheet("background-color: #FF5722; font-size: 10px; padding: 5px;")
        logout_btn.clicked.connect(self.logout_user)
        left_layout.addWidget(logout_btn)
        
        # Authentication section
        auth_frame = QFrame()
        auth_layout = QVBoxLayout(auth_frame)
        auth_layout.setContentsMargins(0, 0, 0, 10)
        
        auth_label = QLabel("Authentication:")
        auth_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        auth_layout.addWidget(auth_label)
        
        # Login button
        self.login_btn = QPushButton("Login to Masar")
        self.login_btn.setStyleSheet("background-color: #FF9800; font-size: 12px; padding: 10px;")
        self.login_btn.clicked.connect(self.perform_login)
        auth_layout.addWidget(self.login_btn)
        
        # Authentication status
        self.auth_status_label = QLabel("Status: Not authenticated")
        self.auth_status_label.setStyleSheet("color: #f44336; font-size: 11px; margin: 5px;")
        auth_layout.addWidget(self.auth_status_label)
        
        left_layout.addWidget(auth_frame)
        
        # Phone number input
        phone_label = QLabel("Phone Number (10 digits only):")
        phone_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        self.phone_input = QLineEdit()
        self.phone_input.setPlaceholderText("e.g., 1234567890")
        self.phone_input.textChanged.connect(self.validate_phone)
        left_layout.addWidget(phone_label)
        left_layout.addWidget(self.phone_input)
        
        # Email input
        email_label = QLabel("Email Address:")
        email_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("example@gmail.com")
        left_layout.addWidget(email_label)
        left_layout.addWidget(self.email_input)
        
        # Passport upload section
        passport_label = QLabel("Upload Passport Images:")
        passport_label.setStyleSheet("font-weight: bold; margin-top: 20px;")
        left_layout.addWidget(passport_label)
        
        # Upload button
        upload_btn = QPushButton("Select Passport Files")
        upload_btn.clicked.connect(self.upload_passports)
        left_layout.addWidget(upload_btn)
        
        # File list
        self.file_list = QListWidget()
        self.file_list.setMaximumHeight(150)
        left_layout.addWidget(self.file_list)
        
        # Clear files button
        clear_btn = QPushButton("Clear All Files")
        clear_btn.setStyleSheet("background-color: #f44336;")
        clear_btn.clicked.connect(self.clear_files)
        left_layout.addWidget(clear_btn)
        
        # Iqama section
        iqama_frame = QFrame()
        iqama_layout = QVBoxLayout(iqama_frame)
        iqama_layout.setContentsMargins(0, 10, 0, 0)
        
        self.iqama_checkbox = QCheckBox("Use Separate Iqama Image & Number")
        self.iqama_checkbox.setStyleSheet("margin-top: 20px; font-weight: bold;")
        self.iqama_checkbox.setToolTip("Check this box to upload a separate Iqama image and enter a custom Iqama number.\nWhen unchecked, the passport image will be used for Iqama and the Iqama number will be extracted from the passport number's last digits.")
        self.iqama_checkbox.toggled.connect(self.toggle_iqama_options)
        iqama_layout.addWidget(self.iqama_checkbox)
        
        # Iqama image upload (initially hidden)
        self.iqama_image_label = QLabel("Upload Iqama Image:")
        self.iqama_image_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        self.iqama_image_label.setVisible(False)
        
        self.iqama_upload_btn = QPushButton("Select Iqama Image")
        self.iqama_upload_btn.setStyleSheet("background-color: #9C27B0;")
        self.iqama_upload_btn.clicked.connect(self.upload_iqama_image)
        self.iqama_upload_btn.setVisible(False)
        
        self.iqama_file_label = QLabel("No Iqama image selected")
        self.iqama_file_label.setStyleSheet("color: #666; font-size: 10px; margin: 5px;")
        self.iqama_file_label.setVisible(False)
        
        # Iqama number input (initially hidden)
        self.iqama_number_label = QLabel("Iqama Number:")
        self.iqama_number_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        self.iqama_number_label.setVisible(False)
        
        self.iqama_number_input = QLineEdit()
        self.iqama_number_input.setPlaceholderText("Enter Iqama number...")
        self.iqama_number_input.setVisible(False)
        
        iqama_layout.addWidget(self.iqama_image_label)
        iqama_layout.addWidget(self.iqama_upload_btn)
        iqama_layout.addWidget(self.iqama_file_label)
        iqama_layout.addWidget(self.iqama_number_label)
        iqama_layout.addWidget(self.iqama_number_input)
        
        left_layout.addWidget(iqama_frame)
        
        # Child companion section
        companion_frame = QFrame()
        companion_layout = QVBoxLayout(companion_frame)
        companion_layout.setContentsMargins(0, 10, 0, 0)
        
        self.companion_checkbox = QCheckBox("Enable Child Companion Mapping")
        self.companion_checkbox.setStyleSheet("margin-top: 20px; font-weight: bold;")
        self.companion_checkbox.setToolTip("Check this box to map child passports to their companion's passport numbers.\nThis is useful when processing children who need to be linked to their adult companions.")
        self.companion_checkbox.toggled.connect(self.toggle_companion_options)
        companion_layout.addWidget(self.companion_checkbox)
        
        # Companion mapping button (initially hidden)
        self.companion_mapping_btn = QPushButton("Set Companion Mappings")
        self.companion_mapping_btn.setStyleSheet("background-color: #673AB7; font-size: 12px; padding: 8px;")
        self.companion_mapping_btn.clicked.connect(self.show_companion_mapping_dialog)
        self.companion_mapping_btn.setVisible(False)
        self.companion_mapping_btn.setToolTip("Click to map each child passport to their companion's passport number")
        
        # Companion status label (initially hidden)
        self.companion_status_label = QLabel("No companion mappings set")
        self.companion_status_label.setStyleSheet("color: #666; font-size: 10px; margin: 5px;")
        self.companion_status_label.setVisible(False)
        
        companion_layout.addWidget(self.companion_mapping_btn)
        companion_layout.addWidget(self.companion_status_label)
        
        left_layout.addWidget(companion_frame)
        
        # Group creation section
        group_frame = QFrame()
        group_layout = QVBoxLayout(group_frame)
        group_layout.setContentsMargins(0, 10, 0, 0)
        
        self.group_checkbox = QCheckBox("Create Group After Processing")
        self.group_checkbox.setStyleSheet("margin-top: 20px;")
        self.group_checkbox.toggled.connect(self.toggle_group_name)
        group_layout.addWidget(self.group_checkbox)
        
        # Group name input (initially hidden)
        self.group_name_label = QLabel("Group Name:")
        self.group_name_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        self.group_name_label.setVisible(False)
        
        self.group_name_input = QLineEdit()
        self.group_name_input.setPlaceholderText("Enter group name...")
        self.group_name_input.setVisible(False)
        
        group_layout.addWidget(self.group_name_label)
        group_layout.addWidget(self.group_name_input)
        
        left_layout.addWidget(group_frame)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        left_layout.addWidget(self.progress_bar)
        
        # Start processing button
        self.start_btn = QPushButton("Start Processing")
        self.start_btn.setStyleSheet("background-color: #2196F3; font-size: 14px; padding: 15px;")
        self.start_btn.clicked.connect(self.start_processing)
        left_layout.addWidget(self.start_btn)
        
        left_layout.addStretch()
        
        # Set the content widget to the scroll area
        scroll_area.setWidget(content_widget)
        return scroll_area
    
    def create_right_panel(self):
        right_frame = QFrame()
        right_layout = QVBoxLayout(right_frame)
        right_layout.setContentsMargins(20, 20, 20, 20)
        
        # Logs title
        logs_label = QLabel("Processing Logs")
        logs_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #2c3e50; margin-bottom: 10px;")
        right_layout.addWidget(logs_label)
        
        # Info about error handling
        info_label = QLabel("ℹ️ Error passports are automatically moved to 'error_passports' folder for re-processing")
        info_label.setStyleSheet("color: #666; font-size: 10px; margin-bottom: 10px; font-style: italic;")
        info_label.setWordWrap(True)
        right_layout.addWidget(info_label)
        
        # Logs text area
        self.logs_text = QTextEdit()
        self.logs_text.setReadOnly(True)
        right_layout.addWidget(self.logs_text)
        
        # Clear logs button
        clear_logs_btn = QPushButton("Clear Logs")
        clear_logs_btn.setStyleSheet("background-color: #ff9800;")
        clear_logs_btn.clicked.connect(self.clear_logs)
        right_layout.addWidget(clear_logs_btn)
        
        return right_frame
    
    def create_menu_bar(self):
        """Create menu bar with update options"""
        menubar = self.menuBar()
        
        # Help menu
        help_menu = menubar.addMenu('Help')
        
        # Check for updates action
        check_updates_action = help_menu.addAction('Check for Updates')
        check_updates_action.triggered.connect(self.manual_check_for_updates)
        
        # About action
        about_action = help_menu.addAction('About')
        about_action.triggered.connect(self.show_about_dialog)
        
        # Settings menu
        settings_menu = menubar.addMenu('Settings')
        
        # Update settings action
        update_settings_action = settings_menu.addAction('Update Settings')
        update_settings_action.triggered.connect(self.show_update_settings)
    
    def check_for_updates_on_startup(self):
        """Check for updates when the application starts"""
        # Use QTimer to delay the check slightly to ensure UI is fully loaded
        QTimer.singleShot(1000, self._delayed_update_check)
    
    def _delayed_update_check(self):
        """Perform the actual update check"""
        try:
            if self.updater.should_check_for_updates():
                # Run update check in a separate thread to avoid blocking UI
                self.updater.check_and_update_if_available()
        except Exception as e:
            print(f"Error during startup update check: {e}")
    
    def manual_check_for_updates(self):
        """Manually check for updates from menu"""
        try:
            self.updater.force_check_for_updates()
        except Exception as e:
            QMessageBox.critical(self, "Update Error", f"Failed to check for updates:\n{str(e)}")
    
    def show_about_dialog(self):
        """Show about dialog"""
        QMessageBox.about(self, "About Saudi Passport Processor", 
                         f"{config.APP_NAME}\nVersion: {config.VERSION}\n\n"
                         "Automated passport processing application\n"
                         "with Google Sheets integration and AI processing.")
    
    def show_update_settings(self):
        """Show update settings dialog"""
        from PyQt5.QtWidgets import QCheckBox, QSpinBox
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Update Settings")
        dialog.setModal(True)
        dialog.resize(300, 200)
        
        layout = QVBoxLayout(dialog)
        
        # Auto-update checkbox
        auto_update_cb = QCheckBox("Enable automatic update checks")
        auto_update_cb.setChecked(self.updater.settings.get("auto_update", True))
        layout.addWidget(auto_update_cb)
        
        # Check interval
        interval_label = QLabel("Check for updates every:")
        layout.addWidget(interval_label)
        
        interval_spinbox = QSpinBox()
        interval_spinbox.setRange(1, 168)  # 1 hour to 1 week
        interval_spinbox.setValue(self.updater.settings.get("check_interval", 24))
        interval_spinbox.setSuffix(" hours")
        layout.addWidget(interval_spinbox)
        
        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        if dialog.exec_() == QDialog.Accepted:
            # Save settings
            self.updater.settings["auto_update"] = auto_update_cb.isChecked()
            self.updater.settings["check_interval"] = interval_spinbox.value()
            self.updater._save_settings()
            
            QMessageBox.information(self, "Settings Saved", 
                                  "Update settings have been saved successfully.")
    
    def validate_phone(self, text):
        # Remove all non-digit characters
        cleaned = re.sub(r'[^\d]', '', text)
        
        # Check if it's exactly 10 digits
        if len(cleaned) == 10 and cleaned.isdigit():
            self.phone_input.setStyleSheet("QLineEdit { border-color: #4CAF50; }")
            return True
        
        self.phone_input.setStyleSheet("QLineEdit { border-color: #f44336; }")
        return False
    
    def toggle_companion_options(self, checked):
        """Show/hide companion options based on checkbox state"""
        self.companion_mapping_btn.setVisible(checked)
        self.companion_status_label.setVisible(checked)
        
        if not checked:
            # Clear companion mappings when unchecked
            self.companion_mappings.clear()
            self.companion_status_label.setText("No companion mappings set")
            self.companion_status_label.setStyleSheet("color: #666; font-size: 10px; margin: 5px;")
    
    def show_companion_mapping_dialog(self):
        """Show dialog for mapping companions to child passports"""
        if not self.passport_files:
            QMessageBox.warning(self, "Warning", "Please upload passport files first.")
            return
        
        dialog = CompanionMappingDialog(self.passport_files, self)
        
        if dialog.exec_() == QDialog.Accepted:
            self.companion_mappings = dialog.get_companion_mappings()
            
            # Update status label
            if self.companion_mappings:
                count = len(self.companion_mappings)
                self.companion_status_label.setText(f"✓ {count} companion mapping(s) set")
                self.companion_status_label.setStyleSheet("color: #4CAF50; font-size: 10px; margin: 5px;")
                self.log_message(f"Set {count} companion mappings")
                
                # Log the mappings for debugging
                for passport_file, companion_passport in self.companion_mappings.items():
                    filename = os.path.basename(passport_file)
                    self.log_message(f"  {filename} → Companion: {companion_passport}")
            else:
                self.companion_status_label.setText("No companion mappings set")
                self.companion_status_label.setStyleSheet("color: #666; font-size: 10px; margin: 5px;")
    
    def toggle_group_name(self, checked):
        """Show/hide group name input based on checkbox state"""
        self.group_name_label.setVisible(checked)
        self.group_name_input.setVisible(checked)
    
    def toggle_iqama_options(self, checked):
        """Show/hide Iqama options based on checkbox state"""
        self.iqama_image_label.setVisible(checked)
        self.iqama_upload_btn.setVisible(checked)
        self.iqama_file_label.setVisible(checked)
        self.iqama_number_label.setVisible(checked)
        self.iqama_number_input.setVisible(checked)
        
        if not checked:
            # Clear Iqama data when unchecked
            self.iqama_image_path = None
            self.iqama_file_label.setText("No Iqama image selected")
            self.iqama_number_input.clear()
    
    def upload_iqama_image(self):
        """Upload separate Iqama image"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, 
            "Select Iqama Image", 
            "", 
            "Image Files (*.jpg *.jpeg *.png *.bmp *.tiff)"
        )
        
        if file_path:
            self.iqama_image_path = file_path
            filename = os.path.basename(file_path)
            self.iqama_file_label.setText(f"Selected: {filename}")
            self.iqama_file_label.setStyleSheet("color: #4CAF50; font-size: 10px; margin: 5px;")
            self.log_message(f"Selected Iqama image: {filename}")
    
    def upload_passports(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, 
            "Select Passport Images", 
            "", 
            "Image Files (*.jpg *.jpeg *.png *.bmp *.tiff)"
        )
        
        if files:
            self.passport_files.extend(files)
            self.update_file_list()
            self.log_message(f"Added {len(files)} passport files")
    
    def update_file_list(self):
        self.file_list.clear()
        for file_path in self.passport_files:
            filename = os.path.basename(file_path)
            self.file_list.addItem(filename)
    
    def clear_files(self):
        self.passport_files.clear()
        self.file_list.clear()
        # Also clear Iqama data
        self.iqama_image_path = None
        self.iqama_file_label.setText("No Iqama image selected")
        self.iqama_file_label.setStyleSheet("color: #666; font-size: 10px; margin: 5px;")
        self.iqama_number_input.clear()
        # Clear companion mappings
        self.companion_mappings.clear()
        self.companion_status_label.setText("No companion mappings set")
        self.companion_status_label.setStyleSheet("color: #666; font-size: 10px; margin: 5px;")
        self.log_message("Cleared all passport files, Iqama data, and companion mappings")
    
    def clear_logs(self):
        self.logs_text.clear()
    
    def log_message(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted_message = f"[{timestamp}] {message}"
        self.logs_text.append(formatted_message)
        self.logs_text.ensureCursorVisible()
    
    def perform_login(self):
        """Handle automatic login and token extraction"""
        try:
            self.login_btn.setEnabled(False)
            self.login_btn.setText("Logging in...")
            self.log_message("Starting automatic login process...")
            
            # Extract tokens using the TokenExtractor
            self.token_data = self.token_extractor.get_tokens_from_browser()
            
            if self.token_data and self.token_data.get("bearer_token"):
                self.auth_status_label.setText("Status: ✓ Authenticated")
                self.auth_status_label.setStyleSheet("color: #4CAF50; font-size: 11px; margin: 5px;")
                self.login_btn.setText("Re-login")
                self.login_btn.setStyleSheet("background-color: #4CAF50; font-size: 12px; padding: 10px;")
                self.log_message("✓ Login successful! Tokens extracted.")
                
                # Log extracted information (without showing sensitive tokens)
                if self.token_data.get("entity_id"):
                    self.log_message(f"Entity ID: {self.token_data['entity_id']}")
                if self.token_data.get("active_entity_id"):
                    self.log_message(f"Active Entity ID: {self.token_data['active_entity_id']}")
                if self.token_data.get("contract_id"):
                    self.log_message(f"Contract ID: {self.token_data['contract_id']}")
                
            else:
                self.auth_status_label.setText("Status: ❌ Login failed")
                self.auth_status_label.setStyleSheet("color: #f44336; font-size: 11px; margin: 5px;")
                self.login_btn.setText("Retry Login")
                self.log_message("❌ Login failed - no tokens extracted")
                QMessageBox.warning(self, "Login Failed", "Could not extract authentication tokens. Please try logging in again.")
            
        except Exception as e:
            self.auth_status_label.setText("Status: ❌ Login error")
            self.auth_status_label.setStyleSheet("color: #f44336; font-size: 11px; margin: 5px;")
            self.login_btn.setText("Retry Login")
            self.log_message(f"❌ Login error: {str(e)}")
            QMessageBox.critical(self, "Login Error", f"An error occurred during login:\n{str(e)}")
        
        finally:
            self.login_btn.setEnabled(True)
    
    def refresh_tokens_if_needed(self):
        """Refresh tokens if they become invalid"""
        if not self.token_data or not self.token_data.get("bearer_token"):
            self.log_message("No valid tokens found, prompting for login...")
            reply = QMessageBox.question(
                self,
                "Token Refresh Required",
                "Authentication tokens are missing or invalid.\nWould you like to login again?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self.perform_login()
                return self.token_data is not None and self.token_data.get("bearer_token") is not None
            return False
        return True
    
    def start_processing(self):
        # Validate inputs
        if not self.passport_files:
            QMessageBox.warning(self, "Warning", "Please upload at least one passport image.")
            return
        
        if not self.email_input.text().strip():
            QMessageBox.warning(self, "Warning", "Please enter an email address.")
            return
        
        if not self.validate_phone(self.phone_input.text()):
            QMessageBox.warning(self, "Warning", "Please enter a valid 10-digit phone number.")
            return
        
        # Validate Iqama settings if checkbox is checked
        if self.iqama_checkbox.isChecked():
            if not self.iqama_image_path:
                QMessageBox.warning(self, "Warning", "Please upload an Iqama image when 'Use Separate Iqama' is checked.")
                return
            if not self.iqama_number_input.text().strip():
                QMessageBox.warning(self, "Warning", "Please enter an Iqama number when 'Use Separate Iqama' is checked.")
                return
        
        # Validate group name if group creation is selected
        if self.group_checkbox.isChecked() and not self.group_name_input.text().strip():
            QMessageBox.warning(self, "Warning", "Please enter a group name.")
            return
        
        # Validate companion mappings if checkbox is checked
        if self.companion_checkbox.isChecked():
            if not self.companion_mappings:
                reply = QMessageBox.question(
                    self,
                    "No Companion Mappings",
                    "Child companion mapping is enabled but no mappings are set.\n\n"
                    "Do you want to:\n"
                    "• Yes: Set mappings now\n"
                    "• No: Continue without companion mappings\n"
                    "• Cancel: Stop and review settings",
                    QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel
                )
                
                if reply == QMessageBox.Yes:
                    self.show_companion_mapping_dialog()
                    if not self.companion_mappings:
                        return  # User cancelled the dialog
                elif reply == QMessageBox.Cancel:
                    return
                # If No, continue without mappings (companion_mappings will be empty)
        
        # Check authentication
        if not self.token_data or not self.token_data.get("bearer_token"):
            QMessageBox.warning(
                self, 
                "Authentication Required", 
                "Please login first by clicking the 'Login to Masar' button."
            )
            return
        
        # Disable start button and show progress
        self.start_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        
        # Start processing thread
        self.processor = PassportProcessor(
            self.passport_files.copy(),
            self.email_input.text().strip(),
            self.phone_input.text().strip(),
            self.group_checkbox.isChecked(),
            self.group_name_input.text().strip() if self.group_checkbox.isChecked() else "",
            self.token_data,
            self.user_data,  # Pass user data to processor
            self.iqama_checkbox.isChecked(),  # Use separate Iqama
            self.iqama_image_path,  # Iqama image path
            self.iqama_number_input.text().strip() if self.iqama_checkbox.isChecked() else None,  # Iqama number
            self.companion_mappings.copy() if self.companion_checkbox.isChecked() else {}  # Companion mappings
        )
        
        self.processor.log_signal.connect(self.log_message)
        self.processor.progress_signal.connect(self.progress_bar.setValue)
        self.processor.error_signal.connect(self.show_error)
        self.processor.finished_signal.connect(self.processing_finished)
        
        self.processor.start()
        
        # Log companion mapping info
        if self.companion_checkbox.isChecked() and self.companion_mappings:
            self.log_message(f"Started passport processing with {len(self.companion_mappings)} companion mappings...")
        else:
            self.log_message("Started passport processing...")
    
    def show_error(self, error_message):
        self.log_message(f"ERROR: {error_message}")
        
        # Only show pop-up for authorization errors, just log other errors
        if "Authorization failed" in error_message or "401" in error_message:
            # Re-enable the start button so user can try again
            self.start_btn.setEnabled(True)
            self.progress_bar.setVisible(False)
            
            # Update auth status
            self.auth_status_label.setText("Status: ❌ Token expired")
            self.auth_status_label.setStyleSheet("color: #f44336; font-size: 11px; margin: 5px;")
            self.login_btn.setText("Re-login Required")
            self.login_btn.setStyleSheet("background-color: #f44336; font-size: 12px; padding: 10px;")
            
            reply = QMessageBox.question(
                self,
                "Authentication Error",
                f"{error_message}\n\nYour authentication token has expired or is invalid.\nWould you like to login again automatically?",
                QMessageBox.Yes | QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                self.perform_login()
        # For other errors, just log them - no pop-up needed since files are moved to error folder
    

    
    def processing_finished(self):
        self.start_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        
        # Check if processing was stopped due to authentication error
        if hasattr(self.processor, 'should_stop_processing') and self.processor.should_stop_processing:
            self.log_message("Processing stopped due to authentication error.")
        else:
            self.log_message("Processing completed!")
            # Show more detailed completion message
            if hasattr(self.processor, 'processed_count') and hasattr(self.processor, 'error_count'):
                total_files = len(self.processor.passport_files)
                processed = self.processor.processed_count
                errors = self.processor.error_count
                under_age = getattr(self.processor, 'under_age_count', 0)
                
                if errors > 0:
                    QMessageBox.information(self, "Processing Complete", 
                        f"Processing completed!\n\n"
                        f"Successfully processed: {processed} passports\n"
                        f"Under-age moved: {under_age} passports\n"
                        f"Errors (moved to error folder): {errors} passports\n\n"
                        f"Check the error_passports folder to re-process failed files.")
                else:
                    QMessageBox.information(self, "Success", 
                        f"All {processed} passports processed successfully!")
            else:
                QMessageBox.information(self, "Success", "Processing completed!")

    def logout_user(self):
        """Logout current user and show login dialog"""
        reply = QMessageBox.question(
            self,
            "Logout Confirmation",
            "Are you sure you want to logout and switch to a different user?",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            # Clear current user data
            self.user_data = None
            self.auth_manager = None
            
            # Show login dialog again
            if self.authenticate_user():
                # Refresh the UI with new user info
                self.close()
                self.__init__()  # Reinitialize the main window
                self.show()
            else:
                # If authentication fails, exit the application
                sys.exit()

def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')  # Modern look
    
    # Set application icon if available
    app.setApplicationName("Saudi Passport Processor")
    
    window = PassportGUI()
    window.show()
    
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
