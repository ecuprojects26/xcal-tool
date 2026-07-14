"""xcaltool - a small GUI + library for converting Cummins Calterm/INSITE
.xcal calibration files to raw .bin flash images and back.

The package is intentionally split into small pieces so new features
(especially live ECU read/write) can be added later without touching the
conversion or GUI code:

    codec.py       -> file format conversion (xcal <-> bin)
    checksum.py    -> checksum helpers used by the codec
    comms.py       -> abstract interface for future ECU read/write
    gui.py         -> the Tkinter user interface
"""

__version__ = "0.1.0"
