#!/usr/bin/env python3
"""
Version Update Script for Saudi Passport Processor
This script helps update the version number and create releases
"""

import os
import sys
import re
import subprocess
import argparse
from datetime import datetime
from version_manager import get_version, update_version

def update_version_in_file(new_version):
    """Update version in version.txt using version manager"""
    try:
        if update_version(new_version):
            print(f"✓ Updated version in version.txt to {new_version}")
            return True
        else:
            print(f"✗ Error updating version.txt")
            return False
        
    except Exception as e:
        print(f"✗ Error updating version: {e}")
        return False

def create_git_tag(version):
    """Create and push git tag"""
    tag_name = f"v{version}"
    
    try:
        # Check if we're in a git repository
        subprocess.run(["git", "status"], check=True, capture_output=True)
        
        # Add all changes
        subprocess.run(["git", "add", "."], check=True)
        
        # Commit changes
        commit_msg = f"Release version {version}"
        subprocess.run(["git", "commit", "-m", commit_msg], check=True)
        
        # Create tag
        subprocess.run(["git", "tag", tag_name], check=True)
        
        # Push changes and tag
        subprocess.run(["git", "push"], check=True)
        subprocess.run(["git", "push", "origin", tag_name], check=True)
        
        print(f"✓ Created and pushed git tag: {tag_name}")
        print(f"✓ GitHub Actions will automatically create a release")
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"✗ Git error: {e}")
        return False

def validate_version(version):
    """Validate version format (semantic versioning)"""
    pattern = r'^\d+\.\d+\.\d+$'
    return re.match(pattern, version) is not None

def get_current_version():
    """Get current version from version manager"""
    try:
        return get_version()
    except Exception:
        return "Unknown"

def main():
    parser = argparse.ArgumentParser(description="Update version and create release")
    parser.add_argument("version", help="New version number (e.g., 1.0.1)")
    parser.add_argument("--no-git", action="store_true", help="Skip git operations")
    
    args = parser.parse_args()
    
    # Validate version format
    if not validate_version(args.version):
        print("✗ Invalid version format. Use semantic versioning (e.g., 1.0.1)")
        sys.exit(1)
    
    current_version = get_current_version()
    print(f"Current version: {current_version}")
    print(f"New version: {args.version}")
    
    # Confirm with user
    confirm = input("Continue with version update? (y/N): ")
    if confirm.lower() != 'y':
        print("Cancelled.")
        sys.exit(0)
    
    # Update version in version.txt
    if not update_version_in_file(args.version):
        sys.exit(1)
    
    # Git operations
    if not args.no_git:
        if not create_git_tag(args.version):
            print("\n⚠ Version updated but git operations failed.")
            print("You can manually commit and tag:")
            print(f"  git add .")
            print(f"  git commit -m 'Release version {args.version}'")
            print(f"  git tag v{args.version}")
            print(f"  git push origin main v{args.version}")
            sys.exit(1)
    
    print(f"\n✓ Successfully updated to version {args.version}")
    
    if not args.no_git:
        print("\n📦 Release Process:")
        print("1. GitHub Actions will automatically build and create a release")
        print("2. The auto-updater in deployed apps will detect the new version")
        print("3. Users will be prompted to update when they start the app")
        print(f"\nRelease URL: https://github.com/mohdtalal3/saudi-passport-processor/releases/tag/v{args.version}")

if __name__ == "__main__":
    main()
