"""
Version management utilities for the Saudi Passport Processor application.
Handles reading and updating version from version.txt file.
"""

import os


def get_version():
    """Read version from version.txt file. Create with default version if not exists."""
    version_file = os.path.join(os.path.dirname(__file__), "version.txt")
    default_version = "1.0.0"
    
    try:
        if os.path.exists(version_file):
            with open(version_file, 'r', encoding='utf-8') as f:
                version = f.read().strip()
                if version:
                    return version
        
        # Create version.txt with default version if it doesn't exist or is empty
        update_version(default_version)
        return default_version
        
    except Exception as e:
        print(f"Error reading version: {e}")
        return default_version


def update_version(new_version):
    """Update version in version.txt file."""
    version_file = os.path.join(os.path.dirname(__file__), "version.txt")
    
    try:
        with open(version_file, 'w', encoding='utf-8') as f:
            f.write(new_version.strip())
        print(f"Version updated to: {new_version}")
        return True
    except Exception as e:
        print(f"Error updating version: {e}")
        return False


def get_version_file_path():
    """Get the path to the version.txt file."""
    return os.path.join(os.path.dirname(__file__), "version.txt")
