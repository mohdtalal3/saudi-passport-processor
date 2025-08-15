import gspread
import json
import os
from google.oauth2.service_account import Credentials
import config

class AuthManager:
    def __init__(self, credentials_file=None):
        self.credentials_file = credentials_file  # Keep for backward compatibility, but won't be used
        self.sheet_id = config.GOOGLE_SHEET_ID
        self.client = None
        self.sheet = None
        
    def setup_connection(self):
        """Setup connection to Google Sheets using config file"""
        try:
            if not self.sheet_id:
                raise ValueError("GOOGLE_SHEET_ID not found in config")
            
            # Define the scope from config
            scope = config.GOOGLE_SCOPES
            
            # Load credentials from config
            creds = Credentials.from_service_account_info(config.GOOGLE_CREDENTIALS, scopes=scope)
            
            # Create client
            self.client = gspread.authorize(creds)
            
            # Open the sheet
            self.sheet = self.client.open_by_key(self.sheet_id).sheet1
            
            return True
            
        except Exception as e:
            print(f"Error setting up Google Sheets connection: {str(e)}")
            return False
    
    def verify_user(self, email):
        """
        Verify if user is authorized
        Returns: (is_authorized, user_data)
        """
        try:
            if not self.sheet:
                return False, "Google Sheets connection not established"
            
            # Get all records
            records = self.sheet.get_all_records()
            
            # Find user by email
            for record in records:
                if record.get('email', '').lower() == email.lower():
                    status = str(record.get('status', '0')).strip()
                    
                    if status == '1':
                        return True, {
                            'email': record.get('email', ''),
                            'api_key': record.get('api_key', ''),
                            'password': record.get('password', ''),
                            'status': status
                        }
                    else:
                        return False, "Your account is deactivated. Please contact the administrator."
            
            return False, "Email not found in authorized users list. Please contact the administrator."
            
        except Exception as e:
            return False, f"Error verifying user: {str(e)}"
    
    def get_user_api_key(self, email):
        """Get API key for authorized user"""
        is_authorized, user_data = self.verify_user(email)
        if is_authorized and isinstance(user_data, dict):
            return user_data.get('api_key', '')
        return None
    
    def update_user_status(self, email, new_status):
        """Update user status (for admin use)"""
        try:
            if not self.sheet:
                return False, "Google Sheets connection not established"
            
            # Get all records with row numbers
            all_values = self.sheet.get_all_values()
            header_row = all_values[0] if all_values else []
            
            # Find email column index
            email_col_index = None
            status_col_index = None
            
            for i, header in enumerate(header_row):
                if header.lower() == 'email':
                    email_col_index = i
                elif header.lower() == 'status':
                    status_col_index = i
            
            if email_col_index is None or status_col_index is None:
                return False, "Required columns not found in sheet"
            
            # Find user row
            for row_index, row_data in enumerate(all_values[1:], start=2):  # Start from row 2
                if len(row_data) > email_col_index and row_data[email_col_index].lower() == email.lower():
                    # Update status
                    cell_address = f"{chr(65 + status_col_index)}{row_index}"
                    self.sheet.update(cell_address, new_status)
                    return True, "Status updated successfully"
            
            return False, "User not found"
            
        except Exception as e:
            return False, f"Error updating status: {str(e)}"
