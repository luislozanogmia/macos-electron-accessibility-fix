#!/usr/bin/env python3
"""
macOS Accessibility Tree Initializer v1.1
=========================================

A lightweight helper utility to initialize accessibility (AX) trees for
Electron applications on macOS, addressing the common error
-25212 (kAXErrorCannotComplete) that often affects automation tools.

The utility "encourages" macOS to build complete accessibility trees by
reading the AXRole attribute from target applications. This forces the
`universalaccessd` daemon to create session-persistent accessibility state.

Release Notes:
  * Prefer 2-argument API for AXRole attribute access.
  * Skip background helper processes (e.g., those with "Helper", "Networking", or "Service" in their names).
  * Initialization must be run per app and re-run per request for reliability (cached per session but can reset on relaunch).
  * Treat error -25212 as partial rather than fatal, reflecting partial accessibility availability.

Usage:
    python macos_ax_initializer.py                       # Initialize known Electron apps
    python macos_ax_initializer.py --apps claude slack   # Initialize specific apps
    python macos_ax_initializer.py --all-running         # Initialize all running apps
    python macos_ax_initializer.py --list                # List running applications
    python macos_ax_initializer.py --quiet               # Minimal output (errors/warnings only)

Requirements:
    - macOS with accessibility permissions granted
    - PyObjC (pip install pyobjc-framework-ApplicationServices pyobjc-framework-AppKit)


This utility focuses solely on initializing macOS Accessibility (AX) state for running applications.
It does not manage application discovery, installation, or launching. Those responsibilities remain with the developer integrating it into their automation platform.

What this tool does
	•	Detects and enumerates currently running applications via NSWorkspace.
	•	Initializes their AX state by reading key attributes (e.g., AXRole).
	•	Ensures session-persistent accessibility trees are available for automation.

What this tool does not do
	•	Discover which applications are installed on the system (e.g., scanning /Applications).
	•	Auto-launch target apps before initialization.
	•	Maintain long-term state or schedules for initialization.

How developers can extend it

You are free to wrap or extend this initializer to fit your use case:
	•	Static list: Provide a fixed set of app names that you always want initialized.
	•	Dynamic detection: Scan for installed apps and only warm up those present.
	•	Auto-launch: Start specific apps before calling the initializer, then run the warm-up.
	•	Monitoring: Periodically re-run initialization to cover apps launched mid-session.

Author: Community contribution for AI automation developers
License: MIT
Repository: https://github.com/luislozanogmia/macos-electron-accessibility-fix
"""

import sys
import argparse
import logging
import time
from typing import List, Optional, Dict, Tuple
from dataclasses import dataclass

try:
    from ApplicationServices import (
        AXUIElementCreateApplication,
        AXUIElementCopyAttributeValue,
        AXIsProcessTrusted,
    )
    from AppKit import NSWorkspace
except ImportError:
    print("❌ Error: PyObjC frameworks not installed")
    print("Install with: pip install pyobjc-framework-ApplicationServices pyobjc-framework-AppKit")
    sys.exit(1)

# Handle different AX constant availability
try:
    from ApplicationServices import kAXRoleAttribute
except ImportError:
    kAXRoleAttribute = "AXRole"

@dataclass
class AppInfo:
    """Information about a running application"""
    name: str
    pid: int
    bundle_id: str = ""

class AXInitializer:
    """
    Handles accessibility tree initialization for macOS applications.
    
    The core mechanism reads the AXRole attribute from application elements,
    which forces macOS to build the complete accessibility tree structure
    in the universalaccessd daemon. This state persists across the user session.
    """
    
    # Applications known to benefit from AX initialization
    ELECTRON_APPS = {
        'claude', 'chatgpt', 'slack', 'discord', 'notion', 'cursor',
        'visual studio code', 'spotify', 'whatsapp', 'telegram',
        'figma', 'obsidian', 'typora', 'mark text'
    }
    
    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.logger = self._setup_logging()
        
    def _setup_logging(self) -> logging.Logger:
        """Configure logging for the initializer"""
        logger = logging.getLogger('ax_initializer')
        handler = logging.StreamHandler()
        
        if self.verbose:
            logger.setLevel(logging.INFO)
            formatter = logging.Formatter('%(message)s')
        else:
            logger.setLevel(logging.WARNING)
            formatter = logging.Formatter('[%(levelname)s] %(message)s')
            
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        return logger
    
    def check_accessibility_permissions(self) -> bool:
        """Check if accessibility permissions are granted"""
        if not AXIsProcessTrusted():
            self.logger.error("❌ Accessibility permissions not granted")
            self.logger.error("Go to: System Preferences → Security & Privacy → Privacy → Accessibility")
            self.logger.error("Add and enable your terminal application or Python")
            return False
        
        self.logger.info("✅ Accessibility permissions confirmed")
        return True
    
    def get_running_applications(self) -> List[AppInfo]:
        """Get list of all running applications"""
        workspace = NSWorkspace.sharedWorkspace()
        running_apps = workspace.runningApplications()
        
        apps = []
        for app in running_apps:
            if app.localizedName():
                apps.append(AppInfo(
                    name=app.localizedName(),
                    pid=app.processIdentifier(),
                    bundle_id=app.bundleIdentifier() or ""
                ))
        
        return sorted(apps, key=lambda x: x.name.lower())
    
    def find_apps_by_names(self, target_names: List[str]) -> List[AppInfo]:
        """Find running applications by name (case-insensitive partial matching)"""
        all_apps = self.get_running_applications()
        found_apps = []
        
        for target in target_names:
            target_lower = target.lower()
            for app in all_apps:
                if target_lower in app.name.lower():
                    found_apps.append(app)
                    break
        
        return found_apps
    
    def _ax_get_role_robust(self, app_element) -> Tuple[int, Optional[str]]:
        """
        Robust AX role attribute getter using 2-argument API.
        
        Handles the AXUIElementCopyAttributeValue signature that accepts two arguments,
        per updated macOS API usage and PyObjC bindings.
        """
        try:
            result = AXUIElementCopyAttributeValue(app_element, kAXRoleAttribute)
            
            if isinstance(result, tuple) and len(result) == 2:
                error_code, role_value = result
                return error_code, role_value
            else:
                return 0, result
                
        except Exception as e:
            self.logger.debug(f"AX role read failed: {e}")
            return -25212, None  # Return the specific error code we're addressing
    
    def initialize_app_accessibility(self, app_info: AppInfo) -> str:
        """
        Initialize accessibility tree for a specific application.
        
        This is the core mechanism that addresses error -25212 by forcing
        the accessibility system to build and cache the UI element tree.
        
        Returns:
            'success' if fully initialized,
            'partial' if error -25212 encountered,
            'failure' otherwise.
        """
        try:
            self.logger.info(f"🎯 Initializing accessibility for {app_info.name} (PID: {app_info.pid})")
            
            # Create accessibility application element
            app_element = AXUIElementCreateApplication(app_info.pid)
            
            # Force accessibility tree initialization by reading the role attribute
            # This is the critical step that creates session-persistent state
            error_code, role = self._ax_get_role_robust(app_element)
            
            if error_code == 0 and role:
                self.logger.info(f"✅ Accessibility initialized successfully: {role}")
                return 'success'
            elif error_code == -25212:
                self.logger.warning(f"⚠️  Error -25212 detected - accessibility may be partial")
                return 'partial'
            else:
                self.logger.warning(f"⚠️  Accessibility init returned error code: {error_code}")
                return 'failure'
                
        except Exception as e:
            self.logger.error(f"❌ Failed to initialize accessibility for {app_info.name}: {e}")
            return 'failure'
    
    def initialize_multiple_apps(self, app_infos: List[AppInfo]) -> Dict[str, str]:
        """Initialize accessibility for multiple applications"""
        results = {}
        
        if not app_infos:
            self.logger.warning("No applications to initialize")
            return results
        
        # Filter out background helper processes
        filtered_apps = [
            app for app in app_infos
            if not any(substr.lower() in app.name.lower() for substr in ("Helper", "Networking", "Service"))
        ]
        skipped_apps = [app for app in app_infos if app not in filtered_apps]
        
        self.logger.info(f"🔧 Initializing accessibility for {len(filtered_apps)} applications (skipped {len(skipped_apps)} helper/background apps)...")
        
        for app_info in filtered_apps:
            status = self.initialize_app_accessibility(app_info)
            results[app_info.name] = status
            
            # Small delay between initializations to avoid overwhelming the system
            time.sleep(0.1)
        
        # Summary
        successful = [name for name, stat in results.items() if stat == 'success']
        partials = [name for name, stat in results.items() if stat == 'partial']
        failures = [name for name, stat in results.items() if stat == 'failure']
        skipped = [app.name for app in skipped_apps]
        
        self.logger.info(f"🎉 Accessibility initialization complete: {len(successful)}/{len(filtered_apps)} successful")
        if successful:
            self.logger.info(f"✅ Successes: {', '.join(successful)}")
        if partials:
            self.logger.info(f"⚠️  Partial: {', '.join(partials)}")
        if failures:
            self.logger.info(f"❌ Failures: {', '.join(failures)}")
        if skipped:
            self.logger.info(f"⏭️  Skipped helper/background apps: {', '.join(skipped)}")
        
        return results
    
    def initialize_electron_apps(self) -> Dict[str, str]:
        """Initialize accessibility for known Electron applications"""
        all_apps = self.get_running_applications()
        
        electron_apps = []
        for app in all_apps:
            if any(electron_name in app.name.lower() for electron_name in self.ELECTRON_APPS):
                # Skip background helper processes
                if any(substr.lower() in app.name.lower() for substr in ("Helper", "Networking", "Service")):
                    continue
                electron_apps.append(app)
        
        if not electron_apps:
            self.logger.info("ℹ️  No known Electron applications running")
            return {}
        
        self.logger.info(f"Found {len(electron_apps)} Electron applications: {', '.join(app.name for app in electron_apps)}")
        return self.initialize_multiple_apps(electron_apps)

def main():
    """Command-line interface for the AX initializer"""
    parser = argparse.ArgumentParser(
        description="Initialize macOS accessibility trees for Electron applications",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                          # Initialize known Electron apps
  %(prog)s --apps claude slack      # Initialize specific apps
  %(prog)s --all-running            # Initialize all running apps
  %(prog)s --list                   # List running applications
  %(prog)s --quiet                  # Run with minimal output

This utility addresses the notorious error -25212 (kAXErrorCannotComplete) 
that affects automation tools working with Electron applications.
        """
    )
    
    parser.add_argument(
        '--apps', 
        nargs='+', 
        help='Specific application names to initialize (case-insensitive partial matching)'
    )
    parser.add_argument(
        '--all-running', 
        action='store_true',
        help='Initialize accessibility for all running applications'
    )
    parser.add_argument(
        '--list', 
        action='store_true',
        help='List all running applications and exit'
    )
    parser.add_argument(
        '--quiet', 
        action='store_true',
        help='Minimize output (errors and warnings only)'
    )
    
    args = parser.parse_args()
    
    # Initialize the AX initializer
    initializer = AXInitializer(verbose=not args.quiet)
    
    # Check accessibility permissions first
    if not initializer.check_accessibility_permissions():
        return 1
    
    # Handle list command
    if args.list:
        apps = initializer.get_running_applications()
        print("\n📱 Running Applications:")
        print("-" * 50)
        for app in apps:
            print(f"{app.name:<30} (PID: {app.pid})")
        print(f"\nTotal: {len(apps)} applications")
        return 0
    
    # Handle specific apps
    if args.apps:
        target_apps = initializer.find_apps_by_names(args.apps)
        
        if not target_apps:
            initializer.logger.error(f"❌ No running applications found matching: {', '.join(args.apps)}")
            return 1
        
        results = initializer.initialize_multiple_apps(target_apps)
        
    # Handle all running apps
    elif args.all_running:
        all_apps = initializer.get_running_applications()
        results = initializer.initialize_multiple_apps(all_apps)
        
    # Default: initialize known Electron apps
    else:
        results = initializer.initialize_electron_apps()
    
    # Return appropriate exit code
    if results and any(status == 'success' for status in results.values()):
        return 0  # At least one success
    else:
        return 1  # No successes or no apps processed

if __name__ == "__main__":
    sys.exit(main())

__all__ = ["AXInitializer", "AppInfo", "main"]