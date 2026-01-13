---
name: PyQt5 to PyQt6 Migration
overview: Convert the pyqt5_camera_new example repository from PyQt5 to PyQt6 by updating import statements, method calls, and regenerating the UI code.
todos: []
---

# PyQt5 to PyQt6 Migration Plan

## Overview

Convert the example repository from PyQt5 to PyQt6. The main changes involve updating import statements, method names, and regenerating the UI code.

## Files to Modify

### 1. [MainProgram.py](EXTERNAL/EXAMPLE_REPOS/pyqt5_camera_new/MainProgram.py)

- **Line 8**: Change `from PyQt5 import QtCore,QtWidgets,QtGui` → `from PyQt6 import QtCore,QtWidgets,QtGui`
- **Line 9**: Change `from PyQt5.QtWidgets import ...` → `from PyQt6.QtWidgets import ...`
- **Line 72**: Change `sys.exit(app.exec_())` → `sys.exit(app.exec())` (PyQt6 removed the underscore from `exec_()`)

### 2. [MainWindow.py](EXTERNAL/EXAMPLE_REPOS/pyqt5_camera_new/MainWindow.py)

- **Line 5**: Update comment from `# Created by: PyQt5 UI code generator 5.9.2` → `# Created by: PyQt6 UI code generator`
- **Line 9**: Change `from PyQt5 import QtCore, QtGui, QtWidgets` → `from PyQt6 import QtCore, QtGui, QtWidgets`

### 3. [MainWindow.ui](EXTERNAL/EXAMPLE_REPOS/pyqt5_camera_new/MainWindow.ui)

- No changes needed - the UI file format is compatible between PyQt5 and PyQt6. However, it should be regenerated using PyQt6's `uic` tool if available, or the generated Python code in MainWindow.py will work as-is after import updates.

### 4. [README.md](EXTERNAL/EXAMPLE_REPOS/pyqt5_camera_new/README.md)

- **Line 2**: Update "PyQT5 program" → "PyQt6 program"
- **Line 5**: Update "PyQT5" → "PyQt6" in required packages
- **Line 8**: Update image reference if needed (optional)

## Key PyQt5 to PyQt6 Changes

1. **Import paths**: All `PyQt5` imports become `PyQt6`
2. **Method names**: `exec_()` → `exec()` (underscore removed)
3. **UI compatibility**: The `.ui` XML format is compatible, but the generated Python code needs updated imports

## Notes

- The serial port code uses `pyserial`, not PyQt, so `read_all()` remains unchanged
- All other PyQt functionality (QTimer, QFileDialog, QMessageBox, etc.) works the same way in PyQt6
- The UI file can remain as-is since the XML format is compatible, but the generated Python code in MainWindow.py must have its imports updated