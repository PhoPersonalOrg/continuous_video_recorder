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
        
        # Try cv2-enumerate-cameras first (best method for device names)
        # Then merge with standard OpenCV enumeration to get correct indices
        try:
            from cv2_enumerate_cameras import enumerate_cameras  # type: ignore
            enumerated_cams = list(enumerate_cameras())
            if enumerated_cams:
                # Get standard OpenCV cameras and try to match
                standard_cameras = self._list_cameras_basic(max_check)
                cameras = self._merge_camera_info(enumerated_cams, standard_cameras)
                if cameras:
                    logger.info("Using cv2-enumerate-cameras with standard OpenCV indices")
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
    
    def _merge_camera_info(self, enumerated_cams: List[Any], standard_cameras: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Merge cv2-enumerate-cameras info with standard OpenCV camera indices.
        
        For each standard OpenCV index, tries to find matching enumerated camera
        by testing if both can be opened and comparing properties.
        
        Args:
            enumerated_cams: List of camera info from cv2-enumerate-cameras
            standard_cameras: List of standard OpenCV cameras (with indices 0-10)
            
        Returns:
            Merged list with device names from enumerated_cams and standard OpenCV indices.
        """
        merged = []
        used_enumerated = set()
        
        # For each standard camera, try to find matching enumerated camera
        for std_cam in standard_cameras:
            std_index = std_cam["index"]
            matched_enum_cam = None
            
            # Try to open standard camera and get its properties
            # Try default backend first, then DirectShow
            std_cap = None
            try:
                std_cap = cv2.VideoCapture(std_index)
                if not std_cap.isOpened():
                    # Try DirectShow as fallback
                    std_cap = cv2.VideoCapture(std_index, cv2.CAP_DSHOW)
                    if not std_cap.isOpened():
                        continue
                
                std_ret, std_frame = std_cap.read()
                if not std_ret:
                    std_cap.release()
                    continue
                
                # Get standard camera properties
                std_width = int(std_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                std_height = int(std_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                std_cap.release()
                std_cap = None
                
                # Try each enumerated camera to find a match
                for enum_cam in enumerated_cams:
                    if enum_cam.index in used_enumerated:
                        continue
                    
                    try:
                        # Try opening enumerated camera
                        enum_cap = cv2.VideoCapture(enum_cam.index, cv2.CAP_DSHOW)
                        if enum_cap.isOpened():
                            enum_ret, enum_frame = enum_cap.read()
                            if enum_ret:
                                # Compare properties
                                enum_width = int(enum_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                                enum_height = int(enum_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                                
                                # Match if resolution is the same (simple heuristic)
                                if abs(enum_width - std_width) < 5 and abs(enum_height - std_height) < 5:
                                    matched_enum_cam = enum_cam
                                    enum_cap.release()
                                    break
                            enum_cap.release()
                    except Exception:
                        try:
                            enum_cap.release()
                        except Exception:
                            pass
                
            except Exception as e:
                if std_cap:
                    try:
                        std_cap.release()
                    except Exception:
                        pass
                logger.debug(f"Error processing standard camera {std_index}: {e}")
                continue
            
            # Build camera info
            if matched_enum_cam:
                camera_info = {
                    "index": std_index,
                    "name": matched_enum_cam.name if hasattr(matched_enum_cam, 'name') and matched_enum_cam.name else f"Camera {std_index}",
                    "vid": f"{matched_enum_cam.vid:04X}" if hasattr(matched_enum_cam, 'vid') and matched_enum_cam.vid and matched_enum_cam.vid != 0 else None,
                    "pid": f"{matched_enum_cam.pid:04X}" if hasattr(matched_enum_cam, 'pid') and matched_enum_cam.pid and matched_enum_cam.pid != 0 else None,
                    "backend": getattr(matched_enum_cam, 'backend_name', None) or "DirectShow",
                    "resolution": (std_width, std_height) if std_width > 0 and std_height > 0 else None,
                }
                used_enumerated.add(matched_enum_cam.index)
            else:
                # No match found - use standard camera info
                camera_info = {
                    "index": std_index,
                    "name": std_cam.get("name", f"Camera {std_index}"),
                    "vid": None,
                    "pid": None,
                    "backend": std_cam.get("backend", "Default"),
                    "resolution": std_cam.get("resolution"),
                }
            
            merged.append(camera_info)
        
        # Add any unmatched enumerated cameras (cameras not accessible via standard indices)
        for enum_cam in enumerated_cams:
            if enum_cam.index not in used_enumerated:
                try:
                    cap = cv2.VideoCapture(enum_cam.index, cv2.CAP_DSHOW)
                    if cap.isOpened():
                        ret, _ = cap.read()
                        if ret:
                            camera_info = {
                                "index": enum_cam.index,
                                "name": enum_cam.name if hasattr(enum_cam, 'name') and enum_cam.name else f"Camera {enum_cam.index}",
                                "vid": f"{enum_cam.vid:04X}" if hasattr(enum_cam, 'vid') and enum_cam.vid and enum_cam.vid != 0 else None,
                                "pid": f"{enum_cam.pid:04X}" if hasattr(enum_cam, 'pid') and enum_cam.pid and enum_cam.pid != 0 else None,
                                "backend": getattr(enum_cam, 'backend_name', None) or "DirectShow",
                                "resolution": None,
                            }
                            try:
                                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                                if width > 0 and height > 0:
                                    camera_info["resolution"] = (width, height)
                            except Exception:
                                pass
                            merged.append(camera_info)
                    cap.release()
                except Exception:
                    pass
        
        return merged
    
    def _list_cameras_cv2_enumerate(self, max_check: int = 10) -> List[Dict[str, Any]]:
        """List cameras using cv2-enumerate-cameras package.
        
        cv2-enumerate-cameras returns DirectShow filter indices (can be high numbers like 700, 1400).
        These indices work with VideoCapture when using DirectShow backend.
        We also check standard OpenCV indices (0-10) and try to match them.
        
        Args:
            max_check: Maximum standard OpenCV index to check.
            
        Returns:
            List of camera info dictionaries. Uses standard OpenCV indices when possible,
            otherwise uses cv2-enumerate-cameras indices.
        """
        from cv2_enumerate_cameras import enumerate_cameras  # type: ignore
        
        enumerated_cams = list(enumerate_cameras())
        if not enumerated_cams:
            return []
        
        cameras = []
        
        # Process each enumerated camera
        for cam_info in enumerated_cams:
            # Try to find matching standard OpenCV index first
            matched_opencv_index = None
            for i in range(max_check):
                try:
                    # Try opening with standard index
                    test_cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
                    if test_cap.isOpened():
                        ret, _ = test_cap.read()
                        if ret:
                            # Check if this might be the same camera by comparing name/VID/PID
                            # We can't directly compare, but if enumerated index also opens, 
                            # we'll prefer the standard index
                            test_cap.release()
                            # Try enumerated index to see if it's the same device
                            enum_cap = cv2.VideoCapture(cam_info.index, cv2.CAP_DSHOW)
                            if enum_cap.isOpened():
                                enum_ret, _ = enum_cap.read()
                                if enum_ret:
                                    # Both work - prefer standard index
                                    matched_opencv_index = i
                                    enum_cap.release()
                                    break
                            enum_cap.release()
                        else:
                            test_cap.release()
                    else:
                        test_cap.release()
                except Exception:
                    continue
            
            # Use matched OpenCV index or enumerated index
            if matched_opencv_index is not None:
                camera_index = matched_opencv_index
                cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
            else:
                # Use enumerated index directly
                camera_index = cam_info.index
                try:
                    cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
                except Exception:
                    continue
            
            if not cap.isOpened():
                continue
            
            # Verify camera works
            ret, _ = cap.read()
            if not ret:
                cap.release()
                continue
            
            # Build camera info
            camera_info = {
                "index": camera_index,
                "name": cam_info.name if hasattr(cam_info, 'name') and cam_info.name else f"Camera {camera_index}",
                "vid": f"{cam_info.vid:04X}" if hasattr(cam_info, 'vid') and cam_info.vid and cam_info.vid != 0 else None,
                "pid": f"{cam_info.pid:04X}" if hasattr(cam_info, 'pid') and cam_info.pid and cam_info.pid != 0 else None,
                "backend": getattr(cam_info, 'backend_name', None) or "DirectShow",
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
        
        More robust implementation with better error handling and resource cleanup.
        
        Args:
            max_check: Maximum device index to check.
            
        Returns:
            List of camera info dictionaries.
        """
        cameras = []
        import platform
        
        for i in range(max_check):
            cap = None
            cap_dshow = None
            
            try:
                # Try default backend first
                cap = cv2.VideoCapture(i)
                if not cap.isOpened():
                    # Try DirectShow on Windows as fallback
                    if platform.system() == "Windows":
                        try:
                            cap_dshow = cv2.VideoCapture(i, cv2.CAP_DSHOW)
                            if cap_dshow.isOpened():
                                cap = cap_dshow
                                cap_dshow = None  # Don't release separately
                            else:
                                cap_dshow.release()
                                cap_dshow = None
                                continue
                        except Exception as e:
                            logger.debug(f"Failed to open camera {i} with DirectShow: {e}")
                            continue
                    else:
                        continue
                
                # Verify camera works by reading a frame
                ret, frame = cap.read()
                if not ret or frame is None:
                    cap.release()
                    cap = None
                    continue
                
                camera_info = {
                    "index": i,
                    "name": f"Camera {i}",
                    "vid": None,
                    "pid": None,
                    "backend": None,
                    "resolution": None,
                }
                
                # Determine backend
                if platform.system() == "Windows":
                    # Check if we used DirectShow
                    if cap_dshow is None:
                        # Try to detect backend by testing DirectShow
                        try:
                            test_cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
                            if test_cap.isOpened():
                                test_ret, _ = test_cap.read()
                                if test_ret:
                                    camera_info["backend"] = "DirectShow"
                                test_cap.release()
                            else:
                                camera_info["backend"] = "Default"
                        except Exception:
                            camera_info["backend"] = "Default"
                    else:
                        camera_info["backend"] = "DirectShow"
                elif platform.system() == "Linux":
                    camera_info["backend"] = "V4L2"
                else:
                    camera_info["backend"] = "Default"
                
                # Get resolution with validation
                try:
                    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    # Validate resolution values
                    if width > 0 and height > 0 and width < 10000 and height < 10000:
                        camera_info["resolution"] = (width, height)
                    else:
                        logger.debug(f"Camera {i}: Invalid resolution {width}x{height}")
                except (ValueError, TypeError) as e:
                    logger.debug(f"Camera {i}: Failed to get resolution: {e}")
                except Exception as e:
                    logger.debug(f"Camera {i}: Unexpected error getting resolution: {e}")
                
                cameras.append(camera_info)
                
            except cv2.error as e:
                logger.debug(f"OpenCV error accessing camera {i}: {e}")
            except Exception as e:
                logger.debug(f"Unexpected error accessing camera {i}: {e}")
            finally:
                # Ensure resources are always released
                if cap is not None:
                    try:
                        cap.release()
                    except Exception as e:
                        logger.debug(f"Error releasing camera {i}: {e}")
                if cap_dshow is not None:
                    try:
                        cap_dshow.release()
                    except Exception as e:
                        logger.debug(f"Error releasing DirectShow camera {i}: {e}")
        
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
