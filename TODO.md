# 2026-01-15 - Misc TODOs and improvements

- # continuous-video-recorder
	- should start recording on launch, and minimize to the menu bar icon after showing a quick preview.
	- hovering the menu bar icon should show a low-res thumbnail preview of the camera's stream
	- right-click should open a context menu with options like "Settings...", "Stop Recording", "Split Recording", "Open Video Output Folder", "Quit", etc.
	- double-clicking should open a full Qt-based window to control that camera and its recording stream/settings. Should show a live display of the actual camera stream that takes up most of the view, along with conventional video recording GUI interface around it.
	- each separate camera/source configured to be recorded from should appear as a separate item in the menu bar and have its own separate and independent process/window/state/etc. This is called that camera/source's `manager`.
	- If a recording is interrupted due to a system crash or something similar, the produced video file should be in a format such that it is recoverable and the entirety isn't lost.
	- The camera source is disconnected, or freezes such that no new frames are recieved within a fixed timeout interval (default 30 seconds), it should alert the user via GUI.

