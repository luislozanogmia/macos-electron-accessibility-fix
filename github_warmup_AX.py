#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
macos_ax_warmup.py — Helper utility to initialize macOS Accessibility (AX) state.

Purpose
-------
Some macOS applications (especially many Electron-based apps) expose AX (accessibility)
elements lazily. Attempting to automate them before the AX tree is initialized can
result in kAXErrorCannotComplete (-25212). This helper performs a minimal "warm-up"
read (AXRole) on target applications to encourage the AX tree to initialize and be
cached by the system accessibility daemon for the current session.

What this is
------------
• A small, optional helper to improve reliability when scripting/automating UI via AX.
• Model-agnostic and application-agnostic; it simply performs a gentle initialization step.

What this is NOT
----------------
• It does NOT modify system settings or expand privileges beyond standard AX permissions.
• It does NOT guarantee element availability beyond what each app actually publishes via AX.
• It is not a framework; just a convenience utility you may run at app/automation startup.

Requirements
------------
• macOS with Accessibility permissions granted for the Python interpreter (or your app).
• Python 3.10 or 3.11 (others may work, but are not verified).
• PyObjC bridge (for AppKit/ApplicationServices) typically available on macOS Python.

License
-------
MIT
"""

from __future__ import annotations

import sys
import time
import logging
import argparse
from typing import List, Optional, Tuple

# Lazy import guards so this file fails gracefully on non-macOS environments.
try:
    from ApplicationServices import (
        AXUIElementCreateApplication,
        AXUIElementCopyAttributeValue,
        AXIsProcessTrusted,
    )
    try:
        # Symbol name can differ by environment; fall back to string.
        from ApplicationServices import kAXRoleAttribute  # type: ignore
    except Exception:
        kAXRoleAttribute = "AXRole"  # type: ignore

    from AppKit import NSWorkspace
except Exception as e:
    print("This utility requires macOS with ApplicationServices/AppKit available.")
    print(f"Import error: {e}")
    sys.exit(1)

logger = logging.getLogger("macos_ax_warmup")


def ax_copy_attribute_value_robust(element, attribute) -> Tuple[int, Optional[object]]:
    """
    Robust attribute getter that handles both 3-arg and 2-arg AX API signatures.

    Returns:
        (error_code, value) where error_code == 0 on success.
    """
    # Try 3-arg signature first (common in many environments)
    try:
        result = AXUIElementCopyAttributeValue(element, attribute, None)  # type: ignore
        if isinstance(result, tuple) and len(result) == 2:
            return int(result[0]), result[1]
        # Some bridges return just the value; interpret as success.
        return 0, result
    except TypeError:
        # Fall back to 2-arg signature
        try:
            result = AXUIElementCopyAttributeValue(element, attribute)  # type: ignore
            if isinstance(result, tuple) and len(result) == 2:
                return int(result[0]), result[1]
            return 0, result
        except Exception as e:
            logger.debug("2-arg AX getter failed: %r", e)
            return -1, None
    except Exception as e:
        logger.debug("3-arg AX getter failed: %r", e)
        return -1, None


def has_accessibility_permission() -> bool:
    """Check whether this process is trusted for Accessibility."""
    try:
        return bool(AXIsProcessTrusted())
    except Exception as e:
        logger.debug("AXIsProcessTrusted check failed: %r", e)
        return False


def list_running_apps() -> List[Tuple[str, int]]:
    """Return (localizedName, pid) for all running apps."""
    ws = NSWorkspace.sharedWorkspace()
    apps = []
    for app in ws.runningApplications():
        name = app.localizedName()
        if name:
            apps.append((str(name), int(app.processIdentifier())))
    return apps


def find_targets(running: List[Tuple[str, int]], fragments: List[str]) -> List[Tuple[str, int]]:
    """
    Filter running apps by case-insensitive substring match against provided fragments.

    Example fragments: ["claude", "slack", "notion", "discord", "chatgpt", "cursor"]
    """
    fragments_l = [f.lower() for f in fragments]
    matches: List[Tuple[str, int]] = []
    for name, pid in running:
        name_l = name.lower()
        if any(f in name_l for f in fragments_l):
            matches.append((name, pid))
    return matches


def warm_up_app(name: str, pid: int, delay_s: float = 0.0) -> bool:
    """
    Perform a minimal "warm-up" by reading the AXRole of the application's AX root element.

    Notes:
    • This read is generally sufficient to encourage initialization of the AX tree.
    • An optional delay can be used if the app just launched and needs a moment.
    """
    try:
        if delay_s > 0:
            time.sleep(delay_s)

        logger.info("Initializing AX for %s (PID %d)...", name, pid)
        app_el = AXUIElementCreateApplication(pid)
        err, role = ax_copy_attribute_value_robust(app_el, kAXRoleAttribute)  # type: ignore
        if err == 0 and role:
            logger.info("✓ AX initialized for %s (role=%r)", name, role)
            return True
        logger.warning("Partial/failed init for %s (err=%s, role=%r)", name, err, role)
        return False
    except Exception as e:
        logger.warning("Failed to warm up %s (PID %d): %r", name, pid, e)
        return False


def warm_up_targets(target_fragments: List[str], delay_s: float = 0.0) -> int:
    """
    Discover and warm up all matching running applications.

    Returns:
        count of successfully initialized applications.
    """
    running = list_running_apps()
    if not running:
        logger.info("No running applications found.")
        return 0

    targets = find_targets(running, target_fragments)
    if not targets:
        logger.info("No target applications matched: %s", ", ".join(target_fragments))
        return 0

    success_count = 0
    for name, pid in targets:
        if warm_up_app(name, pid, delay_s=delay_s):
            success_count += 1
    return success_count


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Helper to encourage initialization of macOS Accessibility (AX) state for target apps."
    )
    parser.add_argument(
        "-t",
        "--targets",
        nargs="*",
        default=["claude", "chatgpt", "slack", "notion", "discord", "cursor"],
        help="App name fragments to target (case-insensitive). Default: common Electron apps.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Optional delay (seconds) before each warm-up (useful right after app launch).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    logger.info("macOS AX Warm-Up Helper")
    logger.info("Note: grant Accessibility permission to your Python/app in System Settings -> Privacy & Security -> Accessibility.")

    if not has_accessibility_permission():
        logger.error("Accessibility permission not granted. Exiting.")
        return 1

    count = warm_up_targets(args.targets, delay_s=args.delay)
    if count > 0:
        logger.info("Initialized %d application(s). AX state should now be available for this session.", count)
        return 0
    else:
        logger.info("No applications were warmed up. (Nothing matched or initialization was not needed.)")
        return 0


if __name__ == "__main__":
    sys.exit(main())