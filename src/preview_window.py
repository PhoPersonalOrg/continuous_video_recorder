"""PyQt6-based camera preview windows for displaying live camera feeds."""
import logging
import queue
import threading
import time
import datetime
from typing import Dict, Optional, Any
import cv2
import numpy as np
from PyQt6.QtCore import QThread, QTimer, pyqtSignal, Qt, QMetaObject, Q_ARG, QEventLoop
from PyQt6.QtWidgets import QApplication, QMainWindow, QLabel, QVBoxLayout, QWidget
from PyQt6.QtGui import QPixmap, QImage, QFont

logger = logging.getLogger(__name__)


class CameraPreviewWindow(QMainWindow):
    """Individual preview window for a single camera feed."""
    
    def __init__(self, camera_id: int, show_timestamp: bool = True, preview_resolution: Optional[tuple] = None):
        """Initialize preview window for a camera.
        
        Args:
            camera_id: Camera identifier.
            show_timestamp: Whether to overlay timestamp on frames.
            preview_resolution: Optional (width, height) to downscale preview frames.
        """
        super().__init__()
        self.camera_id = camera_id
        self.show_timestamp = show_timestamp
        self.preview_resolution = preview_resolution
        
        # Frame buffer (thread-safe queue)
        self.frame_queue = queue.Queue(maxsize=2)  # Keep only latest 2 frames
        
        # Setup UI
        self.setWindowTitle(f"Camera {camera_id + 1} Preview")
        self.setMinimumSize(320, 240)
        
        # Central widget and layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Label for displaying video frames
        self.video_label = QLabel()
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet("background-color: black;")
        self.video_label.setText("Waiting for frames...")
        layout.addWidget(self.video_label)
        
        # Timer for periodic frame updates
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self._update_display)
        self.update_timer.start(30)  # ~33 FPS
        
        # Track last frame
        self.last_frame: Optional[np.ndarray] = None
    
    def add_frame(self, frame: np.ndarray) -> None:
        """Add a frame to the display queue (thread-safe).
        
        Args:
            frame: OpenCV frame (BGR format).
        """
        try:
            # Clear old frames if queue is full (keep only latest)
            while not self.frame_queue.empty():
                try:
                    self.frame_queue.get_nowait()
                except queue.Empty:
                    break
            self.frame_queue.put_nowait(frame.copy())
        except queue.Full:
            # Queue full, skip this frame
            pass
    
    def _update_display(self) -> None:
        """Update the display with the latest frame from queue."""
        try:
            # Get latest frame (non-blocking)
            frame = self.frame_queue.get_nowait()
            self.last_frame = frame
        except queue.Empty:
            # No new frame, use last frame if available
            if self.last_frame is None:
                return
            frame = self.last_frame
        
        # Process frame for display
        display_frame = self._process_frame(frame)
        
        # Convert to QPixmap and display
        pixmap = self._frame_to_pixmap(display_frame)
        if pixmap:
            self.video_label.setPixmap(pixmap.scaled(
                self.video_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            ))
    
    def _process_frame(self, frame: np.ndarray) -> np.ndarray:
        """Process frame for display (resize, add timestamp, etc.).
        
        Args:
            frame: Input frame (BGR format).
            
        Returns:
            Processed frame (RGB format).
        """
        # Resize if needed
        if self.preview_resolution:
            width, height = self.preview_resolution
            frame = cv2.resize(frame, (width, height))
        
        # Add timestamp overlay if enabled
        if self.show_timestamp:
            t_str = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
            color = (0, 0, 255)  # Red in BGR
            cv2.putText(frame, t_str, (20, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        
        # Convert BGR to RGB for Qt display
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return frame_rgb
    
    def _frame_to_pixmap(self, frame: np.ndarray) -> Optional[QPixmap]:
        """Convert OpenCV frame to QPixmap.
        
        Args:
            frame: RGB frame as numpy array.
            
        Returns:
            QPixmap or None if conversion fails.
        """
        try:
            height, width, channel = frame.shape
            bytes_per_line = 3 * width
            # Convert numpy array to bytes for QImage
            frame_bytes = frame.tobytes()
            q_image = QImage(frame_bytes, width, height, bytes_per_line, QImage.Format.Format_RGB888)
            return QPixmap.fromImage(q_image)
        except Exception as e:
            logger.warning(f"Failed to convert frame to pixmap for camera {self.camera_id}: {e}")
            return None
    
    def closeEvent(self, event) -> None:
        """Handle window close event."""
        self.update_timer.stop()
        event.accept()


class PreviewThread(QThread):
    """Background thread for running PyQt6 event loop."""
    
    # Signals for thread-safe operations
    camera_added = pyqtSignal(int, bool, object)  # camera_id, show_timestamp, preview_resolution
    camera_removed = pyqtSignal(int)  # camera_id
    
    def __init__(self):
        """Initialize preview thread."""
        super().__init__()
        self.app: Optional[QApplication] = None
        self.event_loop: Optional[QEventLoop] = None
        self.windows: Dict[int, CameraPreviewWindow] = {}
        self.frame_buffers: Dict[int, queue.Queue] = {}
        self.update_timer: Optional[QTimer] = None
        self.running = False
        
        # Connect signals to slots
        self.camera_added.connect(self._add_camera_slot)
        self.camera_removed.connect(self._remove_camera_slot)
    
    def run(self) -> None:
        """Run the Qt event loop in this thread."""
        # Get QApplication instance (should already exist, created in main thread)
        app_instance = QApplication.instance()
        if not app_instance or not isinstance(app_instance, QApplication):
            logger.error("QApplication not found! It must be created in the main thread before starting preview thread.")
            return
        self.app = app_instance
        
        self.running = True
        
        # Timer for updating windows from frame buffers
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self._update_windows)
        self.update_timer.start(30)  # ~33 FPS
        
        # Create and run event loop in this thread
        self.event_loop = QEventLoop()
        self.event_loop.exec()
    
    def _update_windows(self) -> None:
        """Update all preview windows with frames from buffers."""
        for camera_id, frame_buffer in self.frame_buffers.items():
            try:
                frame = frame_buffer.get_nowait()
                if camera_id in self.windows:
                    self.windows[camera_id].add_frame(frame)
            except queue.Empty:
                pass
    
    def _add_camera_slot(self, camera_id: int, show_timestamp: bool, preview_resolution: Optional[tuple]) -> None:
        """Slot for adding camera window (called in Qt thread).
        
        Args:
            camera_id: Camera identifier.
            show_timestamp: Whether to show timestamp overlay.
            preview_resolution: Optional preview resolution.
        """
        if camera_id in self.windows:
            return
        
        # Create window (must be done in Qt thread)
        window = CameraPreviewWindow(camera_id, show_timestamp, preview_resolution)
        self.windows[camera_id] = window
        
        # Create frame buffer for this camera
        self.frame_buffers[camera_id] = queue.Queue(maxsize=5)
        
        # Show window (must be done in Qt thread)
        window.show()
        logger.info(f"Preview window opened for camera {camera_id}")
    
    def _remove_camera_slot(self, camera_id: int) -> None:
        """Slot for removing camera window (called in Qt thread).
        
        Args:
            camera_id: Camera identifier.
        """
        if camera_id in self.windows:
            window = self.windows[camera_id]
            window.close()
            del self.windows[camera_id]
            if camera_id in self.frame_buffers:
                del self.frame_buffers[camera_id]
            logger.info(f"Preview window closed for camera {camera_id}")
    
    def add_camera(self, camera_id: int, show_timestamp: bool = True, preview_resolution: Optional[tuple] = None) -> None:
        """Add a preview window for a camera (thread-safe).
        
        Args:
            camera_id: Camera identifier.
            show_timestamp: Whether to show timestamp overlay.
            preview_resolution: Optional preview resolution.
        """
        # Emit signal to add camera in Qt thread
        self.camera_added.emit(camera_id, show_timestamp, preview_resolution)
    
    def remove_camera(self, camera_id: int) -> None:
        """Remove preview window for a camera (thread-safe).
        
        Args:
            camera_id: Camera identifier.
        """
        # Emit signal to remove camera in Qt thread
        self.camera_removed.emit(camera_id)
    
    def update_frame(self, camera_id: int, frame: np.ndarray) -> None:
        """Add a frame to the buffer for a camera (thread-safe).
        
        Args:
            camera_id: Camera identifier.
            frame: OpenCV frame (BGR format).
        """
        if camera_id in self.frame_buffers:
            try:
                # Keep only latest frame
                while not self.frame_buffers[camera_id].empty():
                    try:
                        self.frame_buffers[camera_id].get_nowait()
                    except queue.Empty:
                        break
                self.frame_buffers[camera_id].put_nowait(frame.copy())
            except queue.Full:
                pass
    
    def stop(self) -> None:
        """Stop the preview thread and close all windows."""
        self.running = False
        
        # Stop the update timer
        if self.update_timer:
            self.update_timer.stop()
        
        # Emit signals to close all windows (will be processed by Qt event loop)
        camera_ids = list(self.windows.keys())
        for camera_id in camera_ids:
            self.camera_removed.emit(camera_id)
        
        # Give a moment for signals to be processed
        time.sleep(0.1)
        
        # Quit the event loop (this will exit the run() method)
        if self.event_loop and self.event_loop.isRunning():
            self.event_loop.quit()
        
        # Wait for thread to finish (with timeout)
        if not self.wait(3000):  # Wait up to 3 seconds
            logger.warning("Preview thread did not stop within timeout")
            self.terminate()  # Force termination if needed
        
        logger.info("Preview thread stopped")


class CameraPreviewManager:
    """Manages camera preview windows with thread-safe frame updates."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize preview manager.
        
        Args:
            config: Configuration dictionary with preview settings.
        """
        self.config = config
        self.preview_config = config.get("preview", {})
        self.enabled = self.preview_config.get("enabled", False)
        self.show_timestamp = self.preview_config.get("show_timestamp", True)
        preview_res = self.preview_config.get("preview_resolution", [640, 480])
        self.preview_resolution = tuple(preview_res) if preview_res else None
        
        self.preview_thread: Optional[PreviewThread] = None
        self.active_cameras: set = set()
        self.app: Optional[QApplication] = None
        
        # Create QApplication in main thread when preview is enabled
        if self.enabled:
            import sys
            if not QApplication.instance():
                try:
                    self.app = QApplication(sys.argv)
                    logger.info("Created QApplication for preview windows in main thread")
                except Exception as e:
                    logger.error(f"Failed to create QApplication: {e}")
                    self.enabled = False
            else:
                app_instance = QApplication.instance()
                if app_instance and isinstance(app_instance, QApplication):
                    self.app = app_instance
                    logger.info("Using existing QApplication instance for preview windows")
                else:
                    logger.error("Existing QApplication instance is not a QApplication type")
                    self.enabled = False
    
    def start_preview(self, camera_ids: list) -> None:
        """Start preview windows for specified cameras.
        
        Args:
            camera_ids: List of camera IDs to preview.
        """
        if not self.enabled:
            return
        
        # Validate QApplication exists
        if not QApplication.instance():
            logger.error("QApplication not found! Cannot start preview windows. QApplication must be created in the main thread.")
            self.enabled = False
            return
        
        if self.preview_thread is None:
            self.preview_thread = PreviewThread()
            self.preview_thread.start()
            # Give thread time to initialize
            time.sleep(0.1)
        
        # Add windows for each camera
        for camera_id in camera_ids:
            if camera_id not in self.active_cameras:
                self.preview_thread.add_camera(
                    camera_id,
                    show_timestamp=self.show_timestamp,
                    preview_resolution=self.preview_resolution
                )
                self.active_cameras.add(camera_id)
    
    def stop_preview(self) -> None:
        """Stop all preview windows."""
        if self.preview_thread:
            self.preview_thread.stop()
            self.preview_thread = None
        self.active_cameras.clear()
        logger.info("Preview manager stopped")
    
    def update_frame(self, camera_id: int, frame: np.ndarray) -> None:
        """Update preview window with a new frame (thread-safe).
        
        Args:
            camera_id: Camera identifier.
            frame: OpenCV frame (BGR format).
        """
        if not self.enabled or self.preview_thread is None:
            return
        
        if camera_id in self.active_cameras:
            self.preview_thread.update_frame(camera_id, frame)
    
    def add_camera(self, camera_id: int) -> None:
        """Add a camera to preview.
        
        Args:
            camera_id: Camera identifier.
        """
        if not self.enabled:
            return
        
        if camera_id not in self.active_cameras:
            if self.preview_thread is None:
                self.start_preview([camera_id])
            else:
                self.preview_thread.add_camera(
                    camera_id,
                    show_timestamp=self.show_timestamp,
                    preview_resolution=self.preview_resolution
                )
                self.active_cameras.add(camera_id)
    
    def remove_camera(self, camera_id: int) -> None:
        """Remove a camera from preview.
        
        Args:
            camera_id: Camera identifier.
        """
        if camera_id in self.active_cameras:
            if self.preview_thread:
                self.preview_thread.remove_camera(camera_id)
            self.active_cameras.discard(camera_id)
