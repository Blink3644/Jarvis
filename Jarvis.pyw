"""Starts Jarvis with the tray icon and no console window.
Used by the Start Menu shortcut and by 'Start with Windows'. Double-clicking this file works too."""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
if "--gui" not in sys.argv:
    sys.argv.append("--gui")
runpy.run_module("jarvis", run_name="__main__", alter_sys=True)
