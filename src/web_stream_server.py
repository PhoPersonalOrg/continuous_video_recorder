"""
Web Stream Server Module

This module provides a web-based interface for viewing live camera streams
from the continuous video recorder application. It uses Flask to serve MJPEG
streams over HTTP, enabling browser-based viewing without plugins.
"""

import threading
import queue
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple, Generator
import cv2
import numpy as np
from flask import Flask, Response, jsonify, render_template, send_from_directory


logger = logging.getLogger(__name__)


@dataclass
class StreamBuffer:
    """
    Thread-safe buffer for storing encoded JPEG frames for a single camera.
    
    Attributes:
        camera_id: Unique identifier for the camera
        frames: Queue storing encoded JPEG frames (max 2 frames)
        max_size: Maximum number of frames to buffer
        last_update: Timestamp of last frame update
        frame_count: Total number of frames added to buffer
        jpeg_quality: JPEG encoding quality (1-100)
        lock: Thread lock for safe concurrent access
    """
    camera_id: int
    max_size: int = 2
    jpeg_quality: int = 85
    last_update: float = field(default_factory=time.time)
    frame_count: int = 0
    frames: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=2))
    lock: threading.Lock = field(default_factory=threading.Lock)
    
    def __post_init__(self):
        """Validate buffer parameters after initialization."""
        if self.max_size <= 0:
            raise ValueError(f"max_size must be positive integer, got {self.max_size}")
        if not (1 <= self.jpeg_quality <= 100):
            raise ValueError(f"jpeg_quality must be between 1-100, got {self.jpeg_quality}")


class WebStreamServer:
    """
    Manages frame buffers and generates MJPEG streams for web clients.
    
    This class integrates with the existing camera management system to provide
    real-time video streaming through a Flask web server.
    """
    
    def __init__(self, camera_ids: List[int], config: dict):
        """
        Initialize WebStreamServer with camera IDs and configuration.
        
        Args:
            camera_ids: List of camera IDs to create buffers for
            config: Configuration dictionary containing web_ui section
        """
        self.camera_ids = camera_ids
        self.config = config
        self.stream_buffers: Dict[int, StreamBuffer] = {}
        self.flask_app: Optional[Flask] = None
        self.server_thread: Optional[threading.Thread] = None
        self.is_running_flag = False
        
        logger.info(f"Initializing WebStreamServer for cameras: {camera_ids}")

    
    def load_web_config(self) -> Optional[dict]:
        """
        Load and validate web UI configuration from config dictionary.
        
        Returns:
            Dictionary with validated web UI configuration, or None if validation fails
        """
        if "web_ui" not in self.config:
            logger.error("web_ui section not found in configuration")
            return None
        
        web_config = self.config["web_ui"]
        validated_config = {}
        
        # Validate port (1024-65535)
        port = web_config.get("port", 5000)
        if not isinstance(port, int) or not (1024 <= port <= 65535):
            logger.error(f"Invalid port value: {port}. Must be integer between 1024-65535")
            return None
        validated_config["port"] = port
        
        # Validate host
        host = web_config.get("host", "0.0.0.0")
        if not isinstance(host, str):
            logger.error(f"Invalid host value: {host}. Must be string")
            return None
        validated_config["host"] = host
        
        # Validate jpeg_quality (1-100)
        jpeg_quality = web_config.get("jpeg_quality", 85)
        if not isinstance(jpeg_quality, int) or not (1 <= jpeg_quality <= 100):
            logger.error(f"Invalid jpeg_quality value: {jpeg_quality}. Must be integer between 1-100")
            return None
        validated_config["jpeg_quality"] = jpeg_quality
        
        # Validate max_buffer_size (positive integer)
        max_buffer_size = web_config.get("max_buffer_size", 2)
        if not isinstance(max_buffer_size, int) or max_buffer_size <= 0:
            logger.error(f"Invalid max_buffer_size value: {max_buffer_size}. Must be positive integer")
            return None
        validated_config["max_buffer_size"] = max_buffer_size
        
        logger.info(f"Web UI configuration validated: {validated_config}")
        return validated_config

    def initialize_buffers(self) -> bool:
        """
        Initialize stream buffers for all cameras.

        Creates one StreamBuffer instance per camera_id with configured
        jpeg_quality and max_size settings.

        Returns:
            True if buffers initialized successfully, False otherwise
        """
        web_config = self.load_web_config()
        if web_config is None:
            logger.error("Failed to load web configuration, cannot initialize buffers")
            return False

        jpeg_quality = web_config["jpeg_quality"]
        max_size = web_config["max_buffer_size"]

        for camera_id in self.camera_ids:
            try:
                buffer = StreamBuffer(
                    camera_id=camera_id,
                    max_size=max_size,
                    jpeg_quality=jpeg_quality,
                    last_update=time.time(),
                    frame_count=0
                )
                self.stream_buffers[camera_id] = buffer
                logger.debug(f"Initialized buffer for camera {camera_id}")
            except ValueError as e:
                logger.error(f"Failed to create buffer for camera {camera_id}: {e}")
                return False

        logger.info(f"Successfully initialized {len(self.stream_buffers)} stream buffers")
        return True


    def initialize_buffers(self) -> bool:
        """
        Initialize stream buffers for all cameras.
        
        Creates one StreamBuffer instance per camera_id with configured
        jpeg_quality and max_size settings.
        
        Returns:
            True if buffers initialized successfully, False otherwise
        """
        web_config = self.load_web_config()
        if web_config is None:
            logger.error("Failed to load web configuration, cannot initialize buffers")
            return False
        
        jpeg_quality = web_config["jpeg_quality"]
        max_size = web_config["max_buffer_size"]
        
        for camera_id in self.camera_ids:
            try:
                buffer = StreamBuffer(
                    camera_id=camera_id,
                    max_size=max_size,
                    jpeg_quality=jpeg_quality,
                    last_update=time.time(),
                    frame_count=0
                )
                self.stream_buffers[camera_id] = buffer
                logger.debug(f"Initialized buffer for camera {camera_id}")
            except ValueError as e:
                logger.error(f"Failed to create buffer for camera {camera_id}: {e}")
                return False
        
        logger.info(f"Successfully initialized {len(self.stream_buffers)} stream buffers")
        return True

    def encode_frame(self, frame: np.ndarray, quality: int) -> Tuple[bool, Optional[bytes]]:
        """
        Encode a frame to JPEG format.
        
        Args:
            frame: Numpy array in BGR format (OpenCV standard)
            quality: JPEG quality (1-100)
            
        Returns:
            Tuple of (success, jpeg_bytes). jpeg_bytes is None if encoding fails.
        """
        start_time = time.time()
        
        try:
            encode_params = [cv2.IMWRITE_JPEG_QUALITY, quality]
            success, encoded = cv2.imencode('.jpg', frame, encode_params)
            
            if not success:
                logger.warning("cv2.imencode returned False")
                return False, None
            
            jpeg_bytes = encoded.tobytes()
            
            # Log performance warning if encoding is slow
            elapsed = time.time() - start_time
            if elapsed > 0.1:  # 100ms threshold
                logger.warning(f"JPEG encoding took {elapsed:.3f}s (>100ms threshold)")
            
            return True, jpeg_bytes
            
        except Exception as e:
            logger.warning(f"Frame encoding failed: {e}")
            return False, None

    def update_frame(self, camera_id: int, frame: np.ndarray) -> bool:
        """
        Update the frame buffer for a specific camera.
        
        Args:
            camera_id: Camera identifier
            frame: Numpy array in BGR format with shape (height, width, channels)
            
        Returns:
            True if frame was successfully added to buffer, False otherwise
        """
        # Validate camera_id exists
        if camera_id not in self.stream_buffers:
            logger.warning(f"Camera {camera_id} not found in stream buffers")
            return False
        
        # Validate frame
        if frame is None:
            logger.warning(f"Received None frame for camera {camera_id}")
            return False
        
        if frame.ndim != 3:
            logger.warning(f"Invalid frame dimensions for camera {camera_id}: {frame.shape}")
            return False
        
        buffer = self.stream_buffers[camera_id]
        
        # Encode frame to JPEG
        success, jpeg_data = self.encode_frame(frame, buffer.jpeg_quality)
        if not success or jpeg_data is None:
            logger.warning(f"Failed to encode frame for camera {camera_id}")
            return False
        
        # Update buffer with thread safety
        with buffer.lock:
            try:
                # Remove oldest frame if buffer is full
                while buffer.frames.qsize() >= buffer.max_size:
                    try:
                        buffer.frames.get_nowait()
                    except queue.Empty:
                        break
                
                # Add new frame
                buffer.frames.put_nowait(jpeg_data)
                
                # Update metadata
                buffer.last_update = time.time()
                buffer.frame_count += 1
                
                return True
                
            except queue.Full:
                logger.warning(f"Buffer full for camera {camera_id}, could not add frame")
                return False
            except Exception as e:
                logger.error(f"Error updating buffer for camera {camera_id}: {e}")
                return False

    def create_flask_app(self) -> Flask:
        """
        Create and configure Flask application with all routes.
        
        Returns:
            Configured Flask application instance
        """
        app = Flask(__name__, 
                   template_folder='templates',
                   static_folder='static')
        
        # Store reference to WebStreamServer for route access
        app.web_stream_server = self
        
        # Configure Flask logging
        app.logger.handlers = logger.handlers
        app.logger.setLevel(logger.level)
        
        # Disable Flask development mode warnings
        app.config['ENV'] = 'production'
        
        # Register routes
        self._register_routes(app)
        
        logger.info("Flask application created and configured")
        return app

    def _register_routes(self, app: Flask):
        """Register all Flask routes."""
        
        @app.route('/')
        def index():
            """Serve the main web interface."""
            return render_template('index.html')
        
        @app.route('/stream/<int:camera_id>')
        def stream(camera_id):
            """Serve MJPEG stream for a specific camera."""
            web_server = app.web_stream_server
            
            # Validate camera exists
            if camera_id not in web_server.stream_buffers:
                return jsonify({'error': f'Camera {camera_id} not found'}), 404
            
            return Response(
                web_server.generate_mjpeg_stream(camera_id),
                mimetype='multipart/x-mixed-replace; boundary=frame'
            )
        
        @app.route('/api/cameras')
        def api_cameras():
            """Return status information for all cameras."""
            web_server = app.web_stream_server
            return jsonify(web_server.get_camera_status())
        
        @app.route('/api/status')
        def api_status():
            """Return system status information."""
            web_server = app.web_stream_server
            return jsonify({
                'is_running': web_server.is_running(),
                'total_cameras': len(web_server.stream_buffers),
                'server_uptime': time.time() - web_server.start_time if hasattr(web_server, 'start_time') else 0
            })
        
        @app.route('/static/<path:filename>')
        def static_files(filename):
            """Serve static files (CSS, JS, images)."""
            return send_from_directory('static', filename)
        
        # Error handlers
        @app.errorhandler(404)
        def not_found(error):
            """Handle 404 errors."""
            return jsonify({'error': 'Resource not found'}), 404
        
        @app.errorhandler(500)
        def internal_error(error):
            """Handle 500 errors."""
            logger.error(f"Internal server error: {error}", exc_info=True)
            return jsonify({'error': 'Internal server error'}), 500

    def generate_mjpeg_stream(self, camera_id: int) -> Generator[bytes, None, None]:
        """
        Generate MJPEG stream for a specific camera.
        
        Args:
            camera_id: Camera identifier
            
        Yields:
            MJPEG frame data with multipart headers
        """
        buffer = self.stream_buffers[camera_id]
        boundary = "frame"
        
        logger.info(f"Starting MJPEG stream for camera {camera_id}")
        
        try:
            while True:
                # Get latest frame from buffer (non-destructive read)
                frame_data = None
                with buffer.lock:
                    if buffer.frames.qsize() > 0:
                        # Peek at the latest frame without removing it
                        # Get all frames and put them back, keeping the last one
                        frames_list = []
                        while not buffer.frames.empty():
                            try:
                                frames_list.append(buffer.frames.get_nowait())
                            except queue.Empty:
                                break
                        
                        # Put all frames back
                        for frame in frames_list:
                            try:
                                buffer.frames.put_nowait(frame)
                            except queue.Full:
                                pass
                        
                        # Use the latest frame
                        if frames_list:
                            frame_data = frames_list[-1]
                
                # Handle missing frame
                if frame_data is None:
                    time.sleep(0.033)  # ~30 FPS
                    continue
                
                # Build MJPEG frame with multipart headers
                headers = f"--{boundary}\r\n"
                headers += "Content-Type: image/jpeg\r\n"
                headers += f"Content-Length: {len(frame_data)}\r\n\r\n"
                
                # Yield complete frame
                yield headers.encode() + frame_data + b"\r\n"
                
                # Rate limiting (~30 FPS)
                time.sleep(0.033)
                
        except (BrokenPipeError, ConnectionResetError) as e:
            logger.info(f"Client disconnected from camera {camera_id} stream: {e}")
        except Exception as e:
            logger.error(f"Error in MJPEG stream for camera {camera_id}: {e}", exc_info=True)

    def get_camera_status(self) -> dict:
        """
        Get status information for all cameras.
        
        Returns:
            Dictionary mapping camera_id to status information
        """
        status_dict = {}
        current_time = time.time()
        
        for camera_id, buffer in self.stream_buffers.items():
            with buffer.lock:
                # Calculate time since last update
                time_since_update = current_time - buffer.last_update
                
                # Determine connection status (5 second threshold)
                is_connected = time_since_update <= 5.0
                
                # Build status info
                status_dict[camera_id] = {
                    'camera_id': camera_id,
                    'is_connected': is_connected,
                    'last_frame_time': buffer.last_update,
                    'frame_count': buffer.frame_count,
                    'stream_url': f'/stream/{camera_id}'
                }
        
        return status_dict

    def start_server(self, port: Optional[int] = None, host: Optional[str] = None) -> bool:
        """
        Start the Flask web server.
        
        Args:
            port: Port to bind to (uses config if not specified)
            host: Host to bind to (uses config if not specified)
            
        Returns:
            True if server started successfully, False otherwise
        """
        # Load configuration
        web_config = self.load_web_config()
        if web_config is None:
            logger.error("Cannot start server without valid configuration")
            return False
        
        # Use provided values or fall back to config
        port = port or web_config["port"]
        host = host or web_config["host"]
        
        # Initialize buffers
        if not self.initialize_buffers():
            logger.error("Failed to initialize buffers")
            return False
        
        # Try to bind to port, with fallback attempts
        max_attempts = 5
        for attempt in range(max_attempts):
            try_port = port + attempt
            
            try:
                # Create Flask app
                self.flask_app = self.create_flask_app()
                
                # Record start time
                self.start_time = time.time()
                
                # Create and start server thread
                self.server_thread = threading.Thread(
                    target=self.flask_app.run,
                    kwargs={
                        'host': host,
                        'port': try_port,
                        'threaded': True,
                        'use_reloader': False,
                        'debug': False
                    },
                    daemon=True
                )
                self.server_thread.start()
                
                # Set running flag
                self.is_running_flag = True
                
                # Log success
                logger.info(f"Web UI available at http://{host}:{try_port}")
                return True
                
            except OSError as e:
                if attempt < max_attempts - 1:
                    logger.warning(f"Port {try_port} in use, trying {try_port + 1}")
                else:
                    logger.error(f"Failed to bind to any port after {max_attempts} attempts: {e}")
                    return False
            except Exception as e:
                logger.error(f"Failed to start server: {e}", exc_info=True)
                return False
        
        return False

    def stop_server(self) -> bool:
        """
        Stop the Flask web server and clean up resources.
        
        Returns:
            True when shutdown is complete
        """
        logger.info("Stopping web stream server...")
        
        # Set running flag to False
        self.is_running_flag = False
        
        # Wait for server thread to terminate
        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(timeout=5.0)
            
            if self.server_thread.is_alive():
                logger.warning("Server thread did not terminate within 5 seconds")
        
        # Release all buffer locks
        for camera_id, buffer in self.stream_buffers.items():
            try:
                # Acquire and immediately release to ensure cleanup
                with buffer.lock:
                    pass
            except Exception as e:
                logger.error(f"Error releasing lock for camera {camera_id}: {e}")
        
        logger.info("Web stream server stopped")
        return True

    def is_running(self) -> bool:
        """
        Check if the web server is running.
        
        Returns:
            True if server is running, False otherwise
        """
        if not self.is_running_flag:
            return False
        
        if self.server_thread is None:
            return False
        
        return self.server_thread.is_alive()
