"""Multi-camera management using VidGear CamGear."""
import logging
import cv2
from typing import Dict, Any, Optional, List, Tuple
from vidgear.gears import CamGear

logger = logging.getLogger(__name__)


class CameraManager:
    """Manages multiple camera streams using VidGear CamGear."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize camera manager.
        
        Args:
            config: Configuration dictionary with webcam and video settings.
        """
        self.config = config
        self.webcam_config = config.get("webcam", {})
        self.video_config = config.get("video", {})
        
        # Get list of camera devices
        self.device_indices = self.webcam_config.get("devices", [0])
        if not self.device_indices:
            # Fallback to legacy device_index
            device_index = self.webcam_config.get("device_index", 0)
            self.device_indices = [device_index]
        
        # Camera streams dictionary: {camera_id: {"stream": CamGear, "device_index": int, "config": dict}}
        self.cameras: Dict[int, Dict[str, Any]] = {}
        
        # USB connection tracking: {camera_id: {"is_connected": bool, "consecutive_failures": int}}
        self.usb_connection_state: Dict[int, Dict[str, Any]] = {}
        
        # Default video settings
        self.default_resolution = tuple(self.video_config.get("resolution", [1280, 720]))
        self.default_fps = self.video_config.get("fps", 24)
        
        # USB detection parameters
        self.usb_failure_threshold = 3  # Consecutive failures before marking as disconnected
    
    def get_camera_config(self, camera_id: int, device_index: int) -> Dict[str, Any]:
        """Get per-camera configuration, falling back to defaults.
        
        Args:
            camera_id: Camera identifier (0, 1, 2, ...)
            device_index: Physical device index
            
        Returns:
            Dictionary with camera configuration (resolution, fps).
        """
        # Check for per-camera config (camera_0, camera_1, etc.)
        camera_key = f"camera_{camera_id}"
        per_camera_config = self.webcam_config.get(camera_key, {})
        
        resolution = tuple(per_camera_config.get("resolution", self.default_resolution))
        fps = per_camera_config.get("fps", self.default_fps)
        
        return {
            "resolution": resolution,
            "fps": fps,
            "device_index": device_index,
        }
    
    def initialize_cameras(self) -> bool:
        """Initialize all configured camera streams.
        
        Returns:
            True if at least one camera initialized successfully, False otherwise.
        """
        success_count = 0
        
        for camera_id, device_index in enumerate(self.device_indices):
            try:
                camera_config = self.get_camera_config(camera_id, device_index)
                
                # Configure CamGear options
                options = {
                    "CAP_PROP_FRAME_WIDTH": camera_config["resolution"][0],
                    "CAP_PROP_FRAME_HEIGHT": camera_config["resolution"][1],
                    "CAP_PROP_FPS": camera_config["fps"],
                }
                
                # Initialize CamGear stream
                stream = CamGear(source=device_index, logging=True, **options).start()
                
                # Verify stream is working by reading a test frame
                test_frame = stream.read()
                if test_frame is None:
                    logger.warning(f"Camera {camera_id} (device {device_index}) failed to read test frame")
                    stream.stop()
                    continue
                
                self.cameras[camera_id] = {
                    "stream": stream,
                    "device_index": device_index,
                    "config": camera_config,
                }
                
                # Initialize USB connection state
                self.usb_connection_state[camera_id] = {
                    "is_connected": True,
                    "consecutive_failures": 0,
                }
                
                logger.info(f"Camera {camera_id} initialized (device {device_index}, {camera_config['resolution'][0]}x{camera_config['resolution'][1]}@{camera_config['fps']}fps)")
                success_count += 1
                
            except Exception as e:
                logger.error(f"Failed to initialize camera {camera_id} (device {device_index}): {e}")
                continue
        
        if success_count == 0:
            logger.error("No cameras initialized successfully")
            return False
        
        logger.info(f"Successfully initialized {success_count}/{len(self.device_indices)} cameras")
        return True
    
    def read_frame(self, camera_id: int) -> Optional[Any]:
        """Read frame from specific camera.
        
        Args:
            camera_id: Camera identifier.
            
        Returns:
            Frame (numpy array) or None if camera not available or frame read failed.
        """
        if camera_id not in self.cameras:
            return None
        
        try:
            stream = self.cameras[camera_id]["stream"]
            frame = stream.read()
            
            # Update USB connection state based on frame read success
            if camera_id in self.usb_connection_state:
                if frame is None:
                    self.usb_connection_state[camera_id]["consecutive_failures"] += 1
                    if self.usb_connection_state[camera_id]["consecutive_failures"] >= self.usb_failure_threshold:
                        self.usb_connection_state[camera_id]["is_connected"] = False
                else:
                    # Reset failure count on successful read
                    self.usb_connection_state[camera_id]["consecutive_failures"] = 0
                    self.usb_connection_state[camera_id]["is_connected"] = True
            
            return frame
        except Exception as e:
            logger.warning(f"Error reading frame from camera {camera_id}: {e}")
            # Update failure count
            if camera_id in self.usb_connection_state:
                self.usb_connection_state[camera_id]["consecutive_failures"] += 1
                if self.usb_connection_state[camera_id]["consecutive_failures"] >= self.usb_failure_threshold:
                    self.usb_connection_state[camera_id]["is_connected"] = False
            return None
    
    def get_available_cameras(self, max_check: int = 10) -> List[int]:
        """Detect available USB cameras by testing device indices.
        
        Args:
            max_check: Maximum device index to check.
            
        Returns:
            List of available camera device indices.
        """
        available = []
        for i in range(max_check):
            try:
                cap = cv2.VideoCapture(i)
                if cap.isOpened():
                    ret, _ = cap.read()
                    if ret:
                        available.append(i)
                    cap.release()
            except Exception:
                continue
        return available
    
    def list_cameras_with_info(self, max_check: int = 10) -> List[Dict[str, Any]]:
        """List all available cameras with their names and identifiers.
        
        Uses enhanced methods to get actual device names:
        1. Try cv2-enumerate-cameras package (provides name, VID, PID)
        2. Fallback to WMI on Windows (provides device names)
        3. Fallback to basic OpenCV enumeration
        
        Args:
            max_check: Maximum device index to check.
            
        Returns:
            List of dictionaries with camera information:
            - index: Device index (OpenCV index)
            - name: Camera name (actual device name if available)
            - vid: Vendor ID (if available)
            - pid: Product ID (if available)
            - backend: Backend used
            - resolution: Default resolution (if available)
        """
        import platform
        
        # Try cv2-enumerate-cameras first (best method)
        try:
            from cv2_enumerate_cameras import enumerate_cameras  # type: ignore
            cameras = self._list_cameras_cv2_enumerate(max_check)
            if cameras:
                logger.info("Using cv2-enumerate-cameras for camera identification")
                return cameras
        except ImportError:
            logger.debug("cv2-enumerate-cameras not available, trying fallback methods")
        except Exception as e:
            logger.warning(f"cv2-enumerate-cameras failed: {e}, trying fallback methods")
        
        # Fallback to WMI on Windows
        if platform.system() == "Windows":
            try:
                cameras = self._list_cameras_wmi(max_check)
                if cameras:
                    logger.info("Using WMI for camera identification")
                    return cameras
            except ImportError:
                logger.debug("WMI package not available")
            except Exception as e:
                logger.warning(f"WMI camera enumeration failed: {e}, using basic method")
        
        # Final fallback: basic OpenCV enumeration
        return self._list_cameras_basic(max_check)
    
    def _list_cameras_cv2_enumerate(self, max_check: int = 10) -> List[Dict[str, Any]]:
        """List cameras using cv2-enumerate-cameras package.
        
        Args:
            max_check: Maximum device index to check.
            
        Returns:
            List of camera info dictionaries.
        """
        from cv2_enumerate_cameras import enumerate_cameras  # type: ignore
        
        cameras = []
        enumerated = enumerate_cameras()
        
        # Create a mapping of OpenCV index to enumerated camera info
        index_to_camera = {}
        for cam in enumerated:
            if cam.index < max_check:
                index_to_camera[cam.index] = cam
        
        # Verify each camera works and get additional info
        for index, cam_info in index_to_camera.items():
            try:
                cap = cv2.VideoCapture(index, cam_info.backend if hasattr(cam_info, 'backend') else cv2.CAP_ANY)
                if not cap.isOpened():
                    continue
                
                # Verify camera works
                ret, _ = cap.read()
                if not ret:
                    cap.release()
                    continue
                
                camera_info = {
                    "index": index,
                    "name": cam_info.name if hasattr(cam_info, 'name') and cam_info.name else f"Camera {index}",
                    "vid": f"{cam_info.vid:04X}" if hasattr(cam_info, 'vid') and cam_info.vid else None,
                    "pid": f"{cam_info.pid:04X}" if hasattr(cam_info, 'pid') and cam_info.pid else None,
                    "backend": getattr(cam_info, 'backend_name', None) or "Unknown",
                    "resolution": None,
                }
                
                # Get resolution
                try:
                    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    if width > 0 and height > 0:
                        camera_info["resolution"] = (width, height)
                except Exception:
                    pass
                
                cameras.append(camera_info)
                cap.release()
                
            except Exception as e:
                logger.debug(f"Failed to verify camera {index} from cv2-enumerate-cameras: {e}")
                continue
        
        return cameras
    
    def _list_cameras_wmi(self, max_check: int = 10) -> List[Dict[str, Any]]:
        """List cameras using WMI on Windows.
        
        Args:
            max_check: Maximum device index to check.
            
        Returns:
            List of camera info dictionaries.
        """
        try:
            import wmi  # type: ignore
        except ImportError:
            return []
        
        w = wmi.WMI()
        camera_devices = []
        
        # Find camera devices in WMI
        for device in w.Win32_PnPEntity():
            name = getattr(device, 'Name', '')
            if name and ('camera' in name.lower() or 'webcam' in name.lower() or 'video' in name.lower() or 'imaging' in name.lower()):
                camera_devices.append({
                    'name': name,
                    'description': getattr(device, 'Description', ''),
                    'device_id': getattr(device, 'DeviceID', ''),
                })
        
        # Match WMI devices to OpenCV indices (heuristic approach)
        cameras = []
        for i in range(max_check):
            try:
                cap = cv2.VideoCapture(i)
                if not cap.isOpened():
                    continue
                
                ret, _ = cap.read()
                if not ret:
                    cap.release()
                    continue
                
                camera_info = {
                    "index": i,
                    "name": None,
                    "vid": None,
                    "pid": None,
                    "backend": None,
                    "resolution": None,
                }
                
                # Try to match to WMI device (simple heuristic: use first unmatched camera device)
                # This is imperfect but better than nothing
                if camera_devices:
                    # Use first available device name (could be improved with better matching)
                    camera_info["name"] = camera_devices[min(i, len(camera_devices) - 1)]['name']
                
                # Try DirectShow backend
                try:
                    backend = cv2.CAP_DSHOW
                    cap_dshow = cv2.VideoCapture(i, backend)
                    if cap_dshow.isOpened():
                        camera_info["backend"] = "DirectShow"
                        cap_dshow.release()
                    else:
                        camera_info["backend"] = "Default"
                except Exception:
                    camera_info["backend"] = "Default"
                
                # Get resolution
                try:
                    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    if width > 0 and height > 0:
                        camera_info["resolution"] = (width, height)
                except Exception:
                    pass
                
                if camera_info["name"] is None:
                    camera_info["name"] = f"Camera {i}"
                
                cameras.append(camera_info)
                cap.release()
                
            except Exception:
                continue
        
        return cameras
    
    def _list_cameras_basic(self, max_check: int = 10) -> List[Dict[str, Any]]:
        """Basic camera enumeration using OpenCV only (fallback method).
        
        Args:
            max_check: Maximum device index to check.
            
        Returns:
            List of camera info dictionaries.
        """
        cameras = []
        import platform
        
        for i in range(max_check):
            try:
                cap = cv2.VideoCapture(i)
                if not cap.isOpened():
                    continue
                
                ret, _ = cap.read()
                if not ret:
                    cap.release()
                    continue
                
                camera_info = {
                    "index": i,
                    "name": None,
                    "vid": None,
                    "pid": None,
                    "backend": None,
                    "resolution": None,
                }
                
                # Try to get backend info
                if platform.system() == "Windows":
                    try:
                        backend = cv2.CAP_DSHOW
                        cap_dshow = cv2.VideoCapture(i, backend)
                        if cap_dshow.isOpened():
                            camera_info["backend"] = "DirectShow"
                            cap_dshow.release()
                        else:
                            camera_info["backend"] = "Default"
                    except Exception:
                        camera_info["backend"] = "Default"
                elif platform.system() == "Linux":
                    camera_info["backend"] = "V4L2"
                
                # Get resolution
                try:
                    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    if width > 0 and height > 0:
                        camera_info["resolution"] = (width, height)
                except Exception:
                    pass
                
                camera_info["name"] = f"Camera {i}"
                cameras.append(camera_info)
                cap.release()
                
            except Exception:
                continue
        
        return cameras
    
    def is_camera_connected(self, device_index: int) -> bool:
        """Check if a camera device is connected and available.
        
        Args:
            device_index: Physical device index to check.
            
        Returns:
            True if camera can be opened and read, False otherwise.
        """
        try:
            cap = cv2.VideoCapture(device_index)
            if not cap.isOpened():
                cap.release()
                return False
            
            ret, _ = cap.read()
            cap.release()
            return ret
        except Exception:
            return False
    
    def check_usb_connection(self, camera_id: int) -> bool:
        """Check if camera's USB device is still connected.
        
        Uses hybrid detection:
        1. Primary: Frame read failures (tracked in read_frame)
        2. Secondary: Direct device availability check
        
        Args:
            camera_id: Camera identifier.
            
        Returns:
            True if camera is connected, False otherwise.
        """
        if camera_id not in self.cameras:
            return False
        
        # Check connection state from frame read tracking
        if camera_id in self.usb_connection_state:
            is_connected = self.usb_connection_state[camera_id]["is_connected"]
            
            # Secondary check: verify device is still accessible
            device_index = self.cameras[camera_id]["device_index"]
            device_available = self.is_camera_connected(device_index)
            
            # Update state if device check differs
            if device_available != is_connected:
                self.usb_connection_state[camera_id]["is_connected"] = device_available
                self.usb_connection_state[camera_id]["consecutive_failures"] = 0
            
            return device_available
        
        # Fallback: check device directly
        device_index = self.cameras[camera_id]["device_index"]
        return self.is_camera_connected(device_index)
    
    def reconnect_camera(self, camera_id: int) -> bool:
        """Attempt to reconnect a disconnected camera.
        
        Args:
            camera_id: Camera identifier.
            
        Returns:
            True if reconnection successful, False otherwise.
        """
        if camera_id not in self.cameras:
            return False
        
        device_index = self.cameras[camera_id]["device_index"]
        camera_config = self.cameras[camera_id]["config"]
        
        # Check if device is available
        if not self.is_camera_connected(device_index):
            return False
        
        try:
            # Stop old stream if it exists
            old_stream = self.cameras[camera_id]["stream"]
            try:
                old_stream.stop()
            except Exception:
                pass
            
            # Configure CamGear options
            options = {
                "CAP_PROP_FRAME_WIDTH": camera_config["resolution"][0],
                "CAP_PROP_FRAME_HEIGHT": camera_config["resolution"][1],
                "CAP_PROP_FPS": camera_config["fps"],
            }
            
            # Initialize new CamGear stream
            stream = CamGear(source=device_index, logging=True, **options).start()
            
            # Verify stream is working
            test_frame = stream.read()
            if test_frame is None:
                stream.stop()
                return False
            
            # Update camera stream
            self.cameras[camera_id]["stream"] = stream
            
            # Reset USB connection state
            self.usb_connection_state[camera_id] = {
                "is_connected": True,
                "consecutive_failures": 0,
            }
            
            logger.info(f"Camera {camera_id} reconnected successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to reconnect camera {camera_id}: {e}")
            return False
    
    def shutdown(self) -> None:
        """Shutdown all camera streams."""
        for camera_id, camera_info in self.cameras.items():
            try:
                stream = camera_info["stream"]
                stream.stop()
                logger.info(f"Camera {camera_id} stopped")
            except Exception as e:
                logger.warning(f"Error stopping camera {camera_id}: {e}")
        
        self.cameras.clear()
        self.usb_connection_state.clear()
        logger.info("All cameras shut down")
