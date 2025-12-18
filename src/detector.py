"""Face and motion detection logic."""
import cv2
import numpy as np
import logging
from typing import Tuple, Optional, Dict, Any
from collections import deque
from pathlib import Path

logger = logging.getLogger(__name__)


class PresenceDetector:
    """Hybrid face and motion detection for presence detection."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize presence detector.
        
        Args:
            config: Configuration dictionary with detection settings.
        """
        self.config = config.get("detection", {})
        self.face_confidence = self.config.get("face_confidence", 0.5)
        self.motion_threshold = self.config.get("motion_threshold", 30)
        self.face_check_interval = self.config.get("face_check_interval", 5)
        
        # Face detection
        self.face_net = self._load_face_detector()
        self.frame_count = 0
        
        # Motion detection
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=500, varThreshold=50, detectShadows=True
        )
        
        # State tracking
        self.face_detected_recently = False
        self.face_detection_history = deque(maxlen=60)  # Track last 60 face checks (for ~2 seconds at 30 FPS)
        self.recent_face_time = 0.0
        
    def _load_face_detector(self) -> Optional[cv2.dnn.Net]:
        """Load DNN face detector model.
        
        Returns:
            Loaded DNN network or None if loading fails.
        """
        try:
            # Try to load OpenCV DNN face detector
            model_path = Path(__file__).parent.parent / "models"
            model_path.mkdir(exist_ok=True)
            
            # Use OpenCV's built-in DNN face detector (requires downloading models)
            # For now, we'll use a simpler approach with Haar cascades as fallback
            prototxt_path = model_path / "deploy.prototxt"
            model_file = model_path / "res10_300x300_ssd_iter_140000.caffemodel"
            
            if prototxt_path.exists() and model_file.exists():
                net = cv2.dnn.readNetFromCaffe(str(prototxt_path), str(model_file))
                logger.info("Loaded DNN face detector")
                return net
            else:
                # Fallback to Haar cascade
                logger.info("DNN models not found, using Haar cascade face detector")
                return None
        except Exception as e:
            logger.warning(f"Failed to load DNN face detector: {e}. Using Haar cascade.")
            return None
    
    def detect_face_dnn(self, frame: np.ndarray) -> bool:
        """Detect faces using DNN model.
        
        Args:
            frame: Input frame (BGR format).
            
        Returns:
            True if face detected, False otherwise.
        """
        if self.face_net is None:
            return False
        
        try:
            h, w = frame.shape[:2]
            blob = cv2.dnn.blobFromImage(
                cv2.resize(frame, (300, 300)), 1.0,
                (300, 300), (104.0, 177.0, 123.0)
            )
            self.face_net.setInput(blob)
            detections = self.face_net.forward()
            
            for i in range(detections.shape[2]):
                confidence = detections[0, 0, i, 2]
                if confidence > self.face_confidence:
                    return True
            return False
        except Exception as e:
            logger.warning(f"Error in DNN face detection: {e}")
            return False
    
    def detect_face_haar(self, frame: np.ndarray) -> bool:
        """Detect faces using Haar cascade.
        
        Args:
            frame: Input frame (BGR format).
            
        Returns:
            True if face detected, False otherwise.
        """
        try:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            face_cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            )
            faces = face_cascade.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30)
            )
            return len(faces) > 0
        except Exception as e:
            logger.warning(f"Error in Haar face detection: {e}")
            return False
    
    def detect_motion(self, frame: np.ndarray) -> Tuple[bool, float]:
        """Detect motion using background subtraction.
        
        Args:
            frame: Input frame (BGR format).
            
        Returns:
            Tuple of (motion_detected, motion_amount).
        """
        try:
            fg_mask = self.bg_subtractor.apply(frame)
            
            # Calculate motion amount (percentage of foreground pixels)
            motion_pixels = np.sum(fg_mask > 0)
            total_pixels = fg_mask.size
            motion_amount = (motion_pixels / total_pixels) * 100
            
            # Apply threshold
            motion_detected = motion_amount > self.motion_threshold
            
            return motion_detected, motion_amount
        except Exception as e:
            logger.warning(f"Error in motion detection: {e}")
            return False, 0.0
    
    def detect_presence(self, frame: np.ndarray, timestamp: float) -> bool:
        """Detect if user is present using hybrid face + motion detection.
        
        Args:
            frame: Input frame (BGR format).
            timestamp: Current timestamp.
            
        Returns:
            True if user is present, False otherwise.
        """
        self.frame_count += 1
        
        # Check face every N frames
        face_detected = False
        if self.frame_count % self.face_check_interval == 0:
            if self.face_net is not None:
                face_detected = self.detect_face_dnn(frame)
            else:
                face_detected = self.detect_face_haar(frame)
            
            self.face_detection_history.append(face_detected)
            
            if face_detected:
                self.face_detected_recently = True
                self.recent_face_time = timestamp
            else:
                # Check if face was detected recently (within last 2 seconds)
                # Assuming ~30 FPS, 30 frames = ~1 second, so 60 frames = ~2 seconds
                # Convert deque to list for slicing (deque doesn't support slicing)
                history_list = list(self.face_detection_history)
                recent_faces = sum(history_list)  # Sum all items (deque already limited to last 60)
                if recent_faces == 0:
                    self.face_detected_recently = False
        
        # Always check motion
        motion_detected, motion_amount = self.detect_motion(frame)
        
        # Hybrid logic: user present if face detected OR (motion detected AND face detected recently)
        if face_detected:
            return True
        
        if motion_detected and self.face_detected_recently:
            # Motion detected and face was seen recently (within last 2 seconds)
            time_since_face = timestamp - self.recent_face_time
            if time_since_face < 2.0:  # 2 seconds
                return True
        
        return False
    
    def reset(self) -> None:
        """Reset detector state (useful when starting new session)."""
        self.face_detection_history.clear()
        self.face_detected_recently = False
        self.recent_face_time = 0.0
        # Reset background subtractor
        self.bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=500, varThreshold=50, detectShadows=True
        )

