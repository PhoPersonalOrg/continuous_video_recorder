"""Multi-camera management using VidGear CamGear."""
import logging
import cv2
from typing import Dict, Any, Optional, List, Tuple
from vidgear.gears import CamGear
import numpy as np
import pandas as pd
from cv2_enumerate_cameras import enumerate_cameras


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
        
        # Resolve camera names to device indices if names are provided
        self.device_indices = self._resolve_camera_names_to_indices()
        
        # Camera streams dictionary: {camera_id: {"stream": CamGear, "device_index": int, "config": dict}}
        self.cameras: Dict[int, Dict[str, Any]] = {}
        
        # USB connection tracking: {camera_id: {"is_connected": bool, "consecutive_failures": int}}
        self.usb_connection_state: Dict[int, Dict[str, Any]] = {}
        
        # Default video settings
        self.default_resolution = tuple(self.video_config.get("resolution", [1280, 720]))
        self.default_fps = self.video_config.get("fps", 24)
        
        # USB detection parameters
        self.usb_failure_threshold = 3  # Consecutive failures before marking as disconnected
    
    def _resolve_camera_names_to_indices(self) -> List[int]:
        """Resolve camera names to device indices if names are provided in config.
        
        Returns:
            List of resolved device indices (may be same as original if no names provided).
        """
        # Collect camera names from config
        camera_names = []
        for camera_id in range(len(self.device_indices)):
            camera_key = f"camera_{camera_id}"
            per_camera_config = self.webcam_config.get(camera_key, {})
            camera_name = per_camera_config.get("name")
            if camera_name:
                camera_names.append(camera_name)
            else:
                camera_names.append(None)
        
        # If no names provided, return original indices
        if not any(camera_names):
            return self.device_indices
        
        # Find cameras by name
        try:
            found_cams_df = self.try_find_cams(cam_names=[name for name in camera_names if name])
            if found_cams_df.empty:
                logger.warning("No cameras found by name, falling back to device indices")
                return self.device_indices
            
            # Map camera names to device indices
            resolved_indices = []
            for camera_id, camera_name in enumerate(camera_names):
                if camera_name:
                    # Find matching camera in found_cams_df
                    matching_cams = found_cams_df[found_cams_df['name'] == camera_name]
                    if not matching_cams.empty:
                        # Use the index from the dataframe (try_find_cams keeps highest index for duplicates)
                        # Since DataFrame is sorted ascending, get the last one (highest index)
                        device_index = matching_cams.iloc[-1]['index']
                        resolved_indices.append(device_index)
                        logger.info(f"Camera {camera_id} ('{camera_name}') resolved to device index {device_index}")
                    else:
                        # Fallback to original index if name not found
                        logger.warning(f"Camera name '{camera_name}' not found, using original device index {self.device_indices[camera_id]}")
                        resolved_indices.append(self.device_indices[camera_id])
                else:
                    # No name provided, use original index
                    resolved_indices.append(self.device_indices[camera_id])
            
            return resolved_indices
        except Exception as e:
            logger.warning(f"Failed to resolve camera names: {e}, falling back to device indices")
            return self.device_indices
    
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
        
        Uses simplified two-method approach:
        1. Try PyQt6 QMediaDevices.videoInputs() (provides camera names and device IDs)
        2. Fallback to cv2-enumerate-cameras (provides camera info with VID/PID)
        
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
        # Try PyQt6 first
        try:
            from PyQt6.QtMultimedia import QMediaDevices
            cameras = self._list_cameras_pyqt6(max_check)
            if cameras:
                logger.info("Using PyQt6 for camera enumeration")
                return cameras
        except ImportError:
            logger.debug("PyQt6 not available, trying cv2-enumerate-cameras")
        except Exception as e:
            logger.warning(f"PyQt6 camera enumeration failed: {e}, trying cv2-enumerate-cameras")
        
        # Fallback to cv2-enumerate-cameras
        try:
            cameras = self._list_cameras_cv2_enumerate_fallback(max_check)
            if cameras:
                logger.info("Using cv2-enumerate-cameras for camera enumeration")
                return cameras
        except ImportError:
            logger.debug("cv2-enumerate-cameras not available")
        except Exception as e:
            logger.warning(f"cv2-enumerate-cameras failed: {e}")
        
        # Return empty list if both methods fail
        logger.warning("No cameras found with either PyQt6 or cv2-enumerate-cameras")
        return []
    
    def _list_cameras_pyqt6(self, max_check: int = 10) -> List[Dict[str, Any]]:
        """List cameras using PyQt6 QMediaDevices.
        
        Args:
            max_check: Maximum device index to check.
            
        Returns:
            List of camera info dictionaries.
        """
        from PyQt6.QtMultimedia import QMediaDevices
        import platform
        import re
        
        cameras = []
        qt_cameras = QMediaDevices.videoInputs()
        
        if not qt_cameras:
            return []
        
        for qt_camera in qt_cameras:
            camera_name = qt_camera.description()
            device_id = qt_camera.id()
            
            # Extract VID/PID from device ID if available (Windows USB path format)
            vid = None
            pid = None
            if device_id:
                device_id_str = device_id.decode('utf-8', errors='ignore') if isinstance(device_id, bytes) else str(device_id)
                # Match pattern like: vid_046d&pid_082d
                vid_match = re.search(r'vid_([0-9a-fA-F]{4})', device_id_str, re.IGNORECASE)
                pid_match = re.search(r'pid_([0-9a-fA-F]{4})', device_id_str, re.IGNORECASE)
                if vid_match:
                    vid = vid_match.group(1).upper()
                if pid_match:
                    pid = pid_match.group(1).upper()
            
            # Find matching OpenCV index by testing indices
            matched_index = None
            backend = None
            resolution = None
            
            for i in range(max_check):
                cap = None
                try:
                    # Try default backend first
                    cap = cv2.VideoCapture(i)
                    if cap.isOpened():
                        backend = "Default"
                    elif platform.system() == "Windows":
                        # Try DirectShow on Windows
                        if cap:
                            cap.release()
                        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
                        if cap.isOpened():
                            backend = "DirectShow"
                    
                    if cap and cap.isOpened():
                        ret, frame = cap.read()
                        if ret and frame is not None:
                            # This index works, use it
                            matched_index = i
                            if backend is None:
                                backend = "DirectShow" if platform.system() == "Windows" else "Default"
                            
                            # Get resolution
                            try:
                                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                                if width > 0 and height > 0:
                                    resolution = (width, height)
                            except Exception:
                                pass
                            
                            cap.release()
                            break
                    
                    if cap:
                        cap.release()
                except Exception:
                    if cap:
                        try:
                            cap.release()
                        except Exception:
                            pass
                    continue
            
            # Only add if we found a matching OpenCV index
            if matched_index is not None:
                camera_info = {
                    "index": matched_index,
                    "name": camera_name,
                    "vid": vid,
                    "pid": pid,
                    "backend": backend,
                    "resolution": resolution,
                }
                cameras.append(camera_info)
        
        return cameras
    
    def _list_cameras_cv2_enumerate_fallback(self, max_check: int = 10) -> List[Dict[str, Any]]:
        """List cameras using cv2-enumerate-cameras as fallback.
        
        Args:
            max_check: Maximum device index to check (not used, but kept for compatibility).
            
        Returns:
            List of camera info dictionaries.
        """
        from cv2_enumerate_cameras import enumerate_cameras  # type: ignore
        import platform
        
        cameras = []
        enumerated_cams = list(enumerate_cameras())
        
        if not enumerated_cams:
            return []
        
        for cam_info in enumerated_cams:
            camera_index = cam_info.index
            camera_name = cam_info.name if hasattr(cam_info, 'name') and cam_info.name else f"Camera {camera_index}"
            
            # Get VID/PID
            vid = None
            pid = None
            if hasattr(cam_info, 'vid') and cam_info.vid and cam_info.vid != 0:
                vid = f"{cam_info.vid:04X}"
            if hasattr(cam_info, 'pid') and cam_info.pid and cam_info.pid != 0:
                pid = f"{cam_info.pid:04X}"
            
            # Get backend
            backend = getattr(cam_info, 'backend_name', None) or ("DirectShow" if platform.system() == "Windows" else "Default")
            
            # Verify camera works with OpenCV and get resolution
            resolution = None
            cap = None
            try:
                # Try DirectShow on Windows, default on other platforms
                if platform.system() == "Windows":
                    cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
                else:
                    cap = cv2.VideoCapture(camera_index)
                
                if cap.isOpened():
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        # Get resolution
                        try:
                            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                            if width > 0 and height > 0:
                                resolution = (width, height)
                        except Exception:
                            pass
                        
                        camera_info = {
                            "index": camera_index,
                            "name": camera_name,
                            "vid": vid,
                            "pid": pid,
                            "backend": backend,
                            "resolution": resolution,
                        }
                        cameras.append(camera_info)
                
                if cap:
                    cap.release()
            except Exception as e:
                logger.debug(f"Failed to verify camera {camera_index} ({camera_name}): {e}")
                if cap:
                    try:
                        cap.release()
                    except Exception:
                        pass
        
        return cameras
    
    @classmethod
    def try_find_cams(cls, cam_names: List[str] = ['HD Pro Webcam C920', "USB Camera"]) -> pd.DataFrame:
        """ my personal function that requires `cv2_enumerate_cameras`, but works


        devices = config['webcam'].get('devices', [0])
        device_labels = [f"camera_{i}" for i in devices]
        device_camera_configs = [config['webcam'][f"camera_{i}"] for i in devices]

        device_camera_names = [a_config['name'] for i, a_config in enumerate(device_camera_configs)]

        found_cams_df: pd.DataFrame = camera_manager.try_find_cams(cam_names=device_camera_names)
        target_open_cv_indicies = found_cams_df['open_cv_index'].to_numpy().astype(int)
        target_open_cv_indicies

        target_large_open_cv_indicies = found_cams_df['index'].astype(int).to_numpy()
        target_large_open_cv_indicies

        num_max_check: int = int(np.nanmax(target_open_cv_indicies) + 1)
        num_max_check


        """
        # found_cams = []
        found_cams = []
        found_cams_dict = {}

        col_names = ['index', 'name', 'path', 'vid', 'pid', 'backend']

        for camera_info in enumerate_cameras():
            print(f'{camera_info.index}: {camera_info.name}')
            if camera_info.name in cam_names:
                should_add: bool = False
                camera_info_record = {k:getattr(camera_info, k) for k in col_names}
                extant_found_cam_record = found_cams_dict.get(camera_info.name, None)
                if extant_found_cam_record is not None:
                    ## compare and only add the newest
                    is_new_record_index_greater = (extant_found_cam_record['index'] < camera_info_record['index'])
                    if is_new_record_index_greater:
                        should_add = True
                else:
                    should_add = True

                if should_add:
                    found_cams_dict[camera_info.name] = camera_info_record
                    found_cams.append(camera_info_record)
                
                # found_cams.append(camera_info_record)
                # found_cams.append(camera_info)

        # 1400: HD Pro Webcam C920
        # 700: HD Pro Webcam C920
        # 701: Basler GenICam Source
        # 702: Basler GenICam Source 2
        # 703: Basler GenICam Source 3
        # 704: Basler GenICam Source 4
        # 705: OBS Virtual Camera

        found_cams: pd.DataFrame = pd.DataFrame(found_cams)
        found_cams = found_cams.sort_values(by='index', ascending=True).reset_index(drop=True)
        found_cams['open_cv_index'] = found_cams.index.astype(int)
        return found_cams


    
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
