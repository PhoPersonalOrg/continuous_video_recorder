"""Configuration loading and validation."""
import yaml
from pathlib import Path
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class ConfigLoader:
    """Load and validate configuration from YAML file."""
    
    DEFAULT_CONFIG = {
        "video": {
            "resolution": [1280, 720],
            "fps": 24,
            "codec": "mp4v",
            "quality": "medium",
            "compression_mode": False,
        },
        "detection": {
            "face_confidence": 0.5,
            "motion_threshold": 30,
            "face_check_interval": 5,
        },
        "buffer": {
            "absence_timeout": 35,
        },
        "storage": {
            "output_dir": "M:\\ScreenRecordings\\EyeTrackerVR_Recordings",
            "min_duration": 5,
            "auto_split_duration": 3600,  # 1 hour in seconds
            "filename_format": "CAM_%YYYY%-%MM%-%DD%T%HH%%MIN%%SS%",
            "output_extension": "mkv",  # mkv is crash-safe; use "mp4" for traditional finalize-at-end
        },
        "webcam": {
            "device_index": 0,  # Legacy single camera support
            "devices": [0],  # List of camera device indices
        },
        "lsl": {
            "enabled": True,
            "auto_source_id": True,
            "stream_name": "VideoRecorderMarkers",
            "stream_type": "Markers",
            "source_id": "continuous_video_recorder",
            "marker_start": "RECORDING_START",
            "marker_stop": "RECORDING_STOP",
            "include_metadata": True,
        },
        "preview": {
            "enabled": False,
            "update_interval_ms": 30,
            "show_timestamp": True,
            "preview_resolution": [640, 480],  # Optional downscaling for preview
        },
    }
    
    @classmethod
    def load_config(cls, config_path: Optional[Path] = None) -> Dict[str, Any]:
        """Load configuration from YAML file or use defaults.
        
        Args:
            config_path: Path to config.yaml file. If None, looks for config.yaml in current directory.
            
        Returns:
            Dictionary containing configuration values.
        """
        if config_path is None:
            config_path = Path("config.yaml")
        
        config = cls.DEFAULT_CONFIG.copy()
        
        if config_path.exists():
            try:
                with open(config_path, "r") as f:
                    user_config = yaml.safe_load(f)
                    if user_config:
                        config = cls._merge_config(config, user_config)
                logger.info(f"Loaded configuration from {config_path}")
            except Exception as e:
                logger.warning(f"Failed to load config from {config_path}: {e}. Using defaults.")
        else:
            logger.info(f"Config file not found at {config_path}. Using defaults.")
        
        cls._validate_config(config)
        return config
    
    @staticmethod
    def _merge_config(default: Dict, user: Dict) -> Dict:
        """Recursively merge user config into default config."""
        result = default.copy()
        for key, value in user.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = ConfigLoader._merge_config(result[key], value)
            else:
                result[key] = value
        return result
    
    @staticmethod
    def _validate_config(config: Dict[str, Any]) -> None:
        """Validate configuration values."""
        # Validate video settings
        assert isinstance(config["video"]["resolution"], list) and len(config["video"]["resolution"]) == 2
        assert config["video"]["fps"] > 0
        assert config["video"]["codec"] in ["mp4v", "XVID", "MJPG", "H264"]
        
        # Validate detection settings
        assert 0.0 <= config["detection"]["face_confidence"] <= 1.0
        assert config["detection"]["motion_threshold"] > 0
        assert config["detection"]["face_check_interval"] > 0
        
        # Validate buffer settings
        assert config["buffer"]["absence_timeout"] > 0
        
        # Validate storage settings
        assert config["storage"]["min_duration"] >= 0
        assert config["storage"]["auto_split_duration"] > 0
        assert config["storage"].get("output_extension", "mkv") in ["mkv", "mp4"]
        
        # Validate webcam settings
        webcam_config = config["webcam"]
        # Support both legacy device_index and new devices list
        if "device_index" in webcam_config:
            assert isinstance(webcam_config["device_index"], int) and webcam_config["device_index"] >= 0
        if "devices" in webcam_config:
            assert isinstance(webcam_config["devices"], list) and len(webcam_config["devices"]) > 0
            for device_idx in webcam_config["devices"]:
                assert isinstance(device_idx, int) and device_idx >= 0
        # Ensure at least one camera is configured
        if "devices" not in webcam_config and "device_index" in webcam_config:
            # Convert legacy device_index to devices list
            config["webcam"]["devices"] = [webcam_config["device_index"]]
        elif "devices" not in webcam_config:
            # Default to single camera at index 0
            config["webcam"]["devices"] = [0]
        
        # Validate per-camera recording modes
        valid_modes = ["motion_detect", "usb_continuous"]
        for key, value in webcam_config.items():
            if key.startswith("camera_") and isinstance(value, dict):
                if "mode" in value:
                    assert value["mode"] in valid_modes, f"Invalid recording mode '{value['mode']}' for {key}. Must be one of: {valid_modes}"
        
        # Validate LSL settings
        assert isinstance(config["lsl"]["enabled"], bool)
        assert isinstance(config["lsl"].get("auto_source_id", True), bool)
        
        # Validate preview settings
        assert isinstance(config["preview"]["enabled"], bool)
        assert config["preview"]["update_interval_ms"] > 0
        assert isinstance(config["preview"]["show_timestamp"], bool)
        if config["preview"]["preview_resolution"]:
            assert isinstance(config["preview"]["preview_resolution"], list) and len(config["preview"]["preview_resolution"]) == 2
            assert all(isinstance(x, int) and x > 0 for x in config["preview"]["preview_resolution"])
        
        logger.info("Configuration validated successfully")

