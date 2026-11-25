"""Configuration loading and validation."""
import copy
import re
import yaml
from pathlib import Path
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

_DDAGRAB_VIDEO_SIZE_RE = re.compile(r"^(\d+)x(\d+)$", re.IGNORECASE)
_DDAGRAB_ENCODERS = [
    "auto",
    "av1_nvenc",
    "hevc_nvenc",
    "av1_qsv",
    "hevc_qsv",
    "av1_amf",
    "hevc_amf",
    "h264_nvenc",
    "h264_qsv",
    "h264_amf",
]
_DDAGRAB_PRIORITIES = ["below_normal", "idle", "normal"]


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
            "audio_enabled": False,
            "audio_device": None,
            "audio_sample_rate": 44100,
            "audio_channels": 2,
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
        "hotkeys": {
            "manual_split_enabled": True,
        },
        "capture": {
            "backend": "frame_pipe",
            "rtbufsize": "512M",
            "write_timing_sidecar": True,
            "use_wallclock_timestamps": False,
        },
        "ddagrab": {
            "ffmpeg_path": None,
            "output_dir": "./desktop_recordings",
            "framerate": 4,
            "dup_frames": False,
            "output_idx": [0],
            "draw_mouse": True,
            "video_size": None,
            "offset_x": None,
            "offset_y": None,
            "encoder": "auto",
            "cq": 32,
            "gop": 1200,
            "segment_seconds": 3600,
            "filename_pattern": "%Y-%m-%d_%H-%M-%S_mon{idx}.mkv",
            "restart_backoff_initial": 2,
            "restart_backoff_max": 60,
            "restart_stable_reset_s": 300,
            "retention_max_gb": None,
            "retention_max_days": None,
            "process_priority": "below_normal",
        },
        "tray": {
            "enabled": True,
            "indicator_shape": "circle",  # hexagon | circle (not square — reserved for stop)
            "indicator_color": "#FF0000",
            "flash_interval_ms": 500,
            "minimize_to_tray": True,  # minimize button hides console to tray instead of taskbar
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
        
        config = copy.deepcopy(cls.DEFAULT_CONFIG)
        
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
        assert isinstance(config["storage"].get("audio_enabled", False), bool)
        assert int(config["storage"].get("audio_sample_rate", 44100)) > 0
        assert int(config["storage"].get("audio_channels", 2)) > 0
        if config["storage"].get("audio_enabled", False):
            audio_device = config["storage"].get("audio_device")
            assert audio_device is not None and str(audio_device).strip(), "storage.audio_device is required when storage.audio_enabled is true"

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
        
        assert isinstance(config.get("hotkeys", {}).get("manual_split_enabled", True), bool)

        capture_config = config.get("capture", {})
        assert capture_config.get("backend", "frame_pipe") in ["frame_pipe", "dshow_av", "ddagrab"]
        assert isinstance(capture_config.get("write_timing_sidecar", True), bool)
        assert isinstance(capture_config.get("use_wallclock_timestamps", False), bool)

        ddagrab_config = config.get("ddagrab", {})
        if ddagrab_config:
            assert ddagrab_config.get("encoder", "auto") in _DDAGRAB_ENCODERS, (
                f"ddagrab.encoder must be one of {_DDAGRAB_ENCODERS}"
            )
            framerate = ddagrab_config.get("framerate", 4)
            assert not isinstance(framerate, bool) and int(framerate) > 0
            assert isinstance(ddagrab_config.get("dup_frames", False), bool)
            assert isinstance(ddagrab_config.get("draw_mouse", True), bool)

            output_idx = ddagrab_config.get("output_idx", [0])
            if isinstance(output_idx, int) and not isinstance(output_idx, bool):
                assert output_idx >= 0
                ddagrab_config["output_idx"] = [output_idx]
            else:
                assert isinstance(output_idx, list) and len(output_idx) > 0
                for mon_idx in output_idx:
                    assert isinstance(mon_idx, int) and not isinstance(mon_idx, bool) and mon_idx >= 0
                ## END for mon_idx in output_idx....

            video_size = ddagrab_config.get("video_size")
            if video_size is not None:
                if isinstance(video_size, list):
                    assert len(video_size) == 2 and all(isinstance(v, int) and v > 0 for v in video_size)
                else:
                    match = _DDAGRAB_VIDEO_SIZE_RE.match(str(video_size).strip())
                    assert match is not None and int(match.group(1)) > 0 and int(match.group(2)) > 0

            for offset_key in ("offset_x", "offset_y"):
                offset_val = ddagrab_config.get(offset_key)
                if offset_val is not None:
                    assert isinstance(offset_val, int) and not isinstance(offset_val, bool)
            ## END for offset_key in ("offset_x", "offset_y")....

            cq = ddagrab_config.get("cq", 32)
            gop = ddagrab_config.get("gop", 1200)
            segment_seconds = ddagrab_config.get("segment_seconds", 3600)
            assert not isinstance(cq, bool) and int(cq) > 0
            assert not isinstance(gop, bool) and int(gop) > 0
            assert not isinstance(segment_seconds, bool) and int(segment_seconds) > 0

            pattern = str(ddagrab_config.get("filename_pattern", "%Y-%m-%d_%H-%M-%S_mon{idx}.mkv")).strip()
            assert pattern.lower().endswith(".mkv"), "ddagrab.filename_pattern must end with .mkv"
            if len(ddagrab_config.get("output_idx", [0])) > 1:
                assert "{idx}" in pattern, "ddagrab.filename_pattern must contain {idx} for multi-monitor capture"

            backoff_initial = float(ddagrab_config.get("restart_backoff_initial", 2))
            backoff_max = float(ddagrab_config.get("restart_backoff_max", 60))
            stable_reset = float(ddagrab_config.get("restart_stable_reset_s", 300))
            assert backoff_initial > 0
            assert backoff_max >= backoff_initial
            assert stable_reset > 0

            max_gb = ddagrab_config.get("retention_max_gb")
            if max_gb is not None:
                assert float(max_gb) > 0
            max_days = ddagrab_config.get("retention_max_days")
            if max_days is not None:
                assert float(max_days) > 0

            priority = str(ddagrab_config.get("process_priority", "below_normal")).strip().lower()
            assert priority in _DDAGRAB_PRIORITIES, f"ddagrab.process_priority must be one of {_DDAGRAB_PRIORITIES}"

        tray_config = config.get("tray", {})
        assert isinstance(tray_config.get("enabled", True), bool)
        shape = str(tray_config.get("indicator_shape", "circle")).strip().lower()
        assert shape != "square", "tray.indicator_shape must not be 'square' (reserved for stop)"
        assert shape in ["hexagon", "circle"], f"tray.indicator_shape must be hexagon or circle (got {shape!r})"
        color = str(tray_config.get("indicator_color", "#FF0000")).strip()
        assert color.startswith("#") and len(color) in (7, 9), "tray.indicator_color must be #RRGGBB or #RRGGBBAA"
        assert int(tray_config.get("flash_interval_ms", 500)) >= 100
        assert isinstance(tray_config.get("minimize_to_tray", True), bool)

        logger.info("Configuration validated successfully")

