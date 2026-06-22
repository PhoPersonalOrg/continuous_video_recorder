"""Video recording management."""
import cv2
import logging
import time
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime
from vidgear.gears import WriteGear

logger = logging.getLogger(__name__)


class VideoRecorder:
    """Manages video recording sessions."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize video recorder.
        
        Args:
            config: Configuration dictionary with video and storage settings.
        """
        self.config = config
        self.video_config = config.get("video", {})
        self.storage_config = config.get("storage", {})
        
        self.resolution = tuple(self.video_config.get("resolution", [1280, 720]))
        self.fps = self.video_config.get("fps", 24)
        self.codec = self.video_config.get("codec", "avc1")
        self.compression_mode = self.video_config.get("compression_mode", False)
        self.output_dir = Path(self.storage_config.get("output_dir", "./recordings"))
        self.min_duration = self.storage_config.get("min_duration", 5)
        self.auto_split_duration = self.storage_config.get("auto_split_duration", 3600)  # Default 1 hour
        self.filename_format = self.storage_config.get("filename_format", None)
        self.output_extension = self.storage_config.get("output_extension", "mkv")
        
        self.writer: Optional[WriteGear] = None
        self.current_file: Optional[Path] = None
        self.start_time: Optional[float] = None
        self.frame_count = 0
        
        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)


    def _build_ffmpeg_output_params(self) -> Dict[str, Any]:
        """Build FFMPEG output_params for WriteGear compression mode from video config."""
        quality = self.video_config.get("quality", "medium")
        quality_map = {"low": (28, "fast"), "medium": (23, "medium"), "high": (18, "slow")}
        crf, preset = quality_map.get(quality, (23, "medium"))
        codec_to_ffmpeg = {"H264": "libx264", "avc1": "libx264", "h264": "libx264"}
        vcodec = codec_to_ffmpeg.get(self.codec, "libx264")
        params = {"-vcodec": vcodec, "-crf": crf, "-preset": preset, "-output_dimensions": self.resolution, "-input_framerate": self.fps}

        # Audio configuration via FFMPEG
        audio_enabled = self.storage_config.get("audio_enabled", False)
        audio_device = self.storage_config.get("audio_device")

        if audio_enabled and audio_device:
            import platform
            if platform.system() == "Windows":
                # FFMPEG input params for dshow audio on Windows
                params["-f"] = "dshow"
                params["-i"] = f"audio={audio_device}"
                params["-acodec"] = "aac"
            else:
                logger.warning("Audio capture is currently only supported via dshow on Windows. Ignoring audio configuration.")

        if self.output_extension == "mkv" and "-f" not in params:
            # Only set matroska format if we haven't overridden the input format (e.g. for dshow)
            # Actually, WriteGear output_params might mix input and output FFMPEG commands.
            # But WriteGear treats '-f' as the OUTPUT format if passed normally.
            # If we need FFMPEG input, we should use input FFmpeg parameters formatting supported by vidgear.
            # Vidgear WriteGear uses `"-input_framerate"` etc for inputs.
            # Standard FFMPEG commands like `-f` or `-i` are treated as output commands unless handled specially by WriteGear.
            # Let's pass FFMPEG output format.
            pass

        if self.output_extension == "mkv" and not audio_enabled:
             params["-f"] = "matroska"

        return params


    def _create_writer(self, file_path: Path) -> Optional[WriteGear]:
        """Create and return a WriteGear instance for the given path, or None on failure."""
        if self.compression_mode:
            output_params = self._build_ffmpeg_output_params()
            try:
                # Add '-disable_force_termination': True to allow FFMPEG errors to throw an exception
                output_params_copy = output_params.copy()
                # On Windows, missing dshow devices will cause FFMPEG to exit immediately, but sometimes WriteGear might hide the error.
                # WriteGear raises an exception during `write` or `__init__` depending on when the process dies.
                return WriteGear(output=str(file_path), compression_mode=True, logging=True, **output_params_copy)
            except Exception as e:
                logger.warning(f"Failed to open FFMPEG writer: {e}")

                # If audio was enabled and it failed, maybe it's because the audio device doesn't exist
                if "-i" in output_params and "audio=" in str(output_params.get("-i", "")):
                    logger.error(f"Audio device initialization failed. Details: {e}")
                    import builtins
                    # In some setups, we might not have a terminal to read from. Handle input gracefully.
                    try:
                        resp = builtins.input("Failed to initialize FFMPEG with audio. Press [Enter] to continue without audio, or [Ctrl+C] to abort...")
                    except EOFError:
                        pass

                    logger.warning("Attempting to record without audio...")
                    output_params.pop("-f", None)
                    output_params.pop("-i", None)
                    output_params.pop("-acodec", None)
                    if self.output_extension == "mkv":
                        output_params["-f"] = "matroska"

                    try:
                        return WriteGear(output=str(file_path), compression_mode=True, logging=True, **output_params)
                    except Exception as e2:
                        logger.warning(f"Failed to open FFMPEG writer without audio: {e2}, trying without preset")
                        output_params.pop("-preset", None)
                        try:
                            return WriteGear(output=str(file_path), compression_mode=True, logging=True, **output_params)
                        except Exception as e3:
                            logger.error(f"Failed to initialize FFMPEG video writer: {e3}")
                            return None
                else:
                    logger.warning("Trying without preset")
                    output_params.pop("-preset", None)
                    try:
                        return WriteGear(output=str(file_path), compression_mode=True, logging=True, **output_params)
                    except Exception as e2:
                        logger.error(f"Failed to initialize FFMPEG video writer: {e2}")
                        return None

        output_params = {"-fourcc": self.codec}
        try:
            return WriteGear(output=str(file_path), compression_mode=False, logging=True, **output_params)
        except Exception as e:
            logger.warning(f"Failed to open writer with codec {self.codec}, trying H264: {e}")
            output_params = {"-fourcc": "H264"}
            try:
                return WriteGear(output=str(file_path), compression_mode=False, logging=True, **output_params)
            except Exception as e2:
                logger.warning(f"Failed to open writer with codec H264, trying XVID: {e2}")
                output_params = {"-fourcc": "XVID"}
                try:
                    return WriteGear(output=str(file_path), compression_mode=False, logging=True, **output_params)
                except Exception as e3:
                    logger.error(f"Failed to initialize video writer with fallback codecs: {e3}")
                    return None


    def start_recording(self) -> Optional[Path]:
        """Start a new recording session.
        
        Returns:
            Path to the video file being created, or None if failed.
        """
        if self.writer is not None:
            logger.warning("Recording already in progress")
            return self.current_file
        
        try:
            # Generate filename
            from src.utils import generate_timestamped_filename
            self.current_file = generate_timestamped_filename(prefix="Record", extension=self.output_extension, output_dir=self.output_dir, filename_format=self.filename_format)
            
            self.writer = self._create_writer(self.current_file)
            if self.writer is None:
                self.current_file = None
                return None
            self.start_time = time.time()
            self.frame_count = 0
            logger.info(f"Started recording: {self.current_file.name}")
            return self.current_file
            
        except Exception as e:
            logger.error(f"Failed to start recording: {e}")
            self.writer = None
            self.current_file = None
            return None
    
    def write_frame(self, frame: cv2.typing.MatLike) -> bool:
        """Write a frame to the current video.
        
        Args:
            frame: Frame to write (will be resized to match resolution if needed).
            
        Returns:
            True if frame written successfully, False otherwise.
        """
        if self.writer is None:
            return False
        
        try:
            # Resize frame if needed
            if frame.shape[1] != self.resolution[0] or frame.shape[0] != self.resolution[1]:
                frame = cv2.resize(frame, self.resolution)
            
            self.writer.write(frame)
            self.frame_count += 1
            return True
        except Exception as e:
            # WriteGear throws ValueError with string message when there is broken pipe error in current version.
            error_str = str(e)
            logger.error(f"Failed to write frame: {e}")

            # If the FFMPEG pipe is broken because audio device initialization failed, WriteGear throws an exception.
            # We can detect this, try to alert the user, and restart recording without audio.
            if self.compression_mode and ("BrokenPipeError" in error_str or "Wrong values passed to FFmpeg Pipe" in error_str or "pipe broken" in error_str.lower() or "pipe" in error_str.lower() or e.__class__.__name__ == "ValueError" or "WriteGear" in error_str):
                # Also check if audio was enabled, as it is a common cause for failure
                if self.storage_config.get("audio_enabled", False):
                    logger.error("FFMPEG pipe broken, possibly due to audio device failure. Disabling audio and trying to restart...")

                    import builtins
                    try:
                        builtins.input("Failed to write to FFMPEG. Press [Enter] to continue without audio, or [Ctrl+C] to abort...")
                    except EOFError:
                        pass
                    except KeyboardInterrupt:
                        raise

                    # Disable audio for future
                    self.storage_config["audio_enabled"] = False

                    # Try restarting the recording completely
                    old_file = self.current_file
                    self.writer = None
                    if old_file and old_file.exists():
                        try:
                            old_file.unlink()
                        except:
                            pass

                    logger.info("Restarting recording without audio...")
                    from src.utils import generate_timestamped_filename
                    self.current_file = generate_timestamped_filename(prefix="Record", extension=self.output_extension, output_dir=self.output_dir, filename_format=self.filename_format)
                    self.writer = self._create_writer(self.current_file)
                    if self.writer is None:
                        self.current_file = None
                        return False

                    self.start_time = time.time()
                    self.frame_count = 0

                    try:
                        self.writer.write(frame)
                        self.frame_count += 1
                        return True
                    except Exception as e2:
                        logger.error(f"Failed to write frame even without audio: {e2}")
                        return False

            return False
    
    def stop_recording(self) -> Optional[Path]:
        """Stop the current recording session.
        
        Returns:
            Path to the recorded video file, or None if no recording or duration too short.
        """
        if self.writer is None:
            return None
        
        try:
            duration = time.time() - self.start_time if self.start_time else 0
            
            # Close writer
            try:
                self.writer.close()
            except Exception as e:
                logger.warning(f"Error closing writer: {e}")
            self.writer = None
            
            # Check minimum duration
            if duration < self.min_duration:
                logger.info(f"Recording too short ({duration:.1f}s < {self.min_duration}s), deleting file")
                if self.current_file and self.current_file.exists():
                    self.current_file.unlink()
                file_path = None
            else:
                file_path = self.current_file
                logger.info(f"Stopped recording: {self.current_file.name} (duration: {duration:.1f}s)")
            
            self.current_file = None
            self.start_time = None
            self.frame_count = 0
            
            return file_path
            
        except Exception as e:
            logger.error(f"Failed to stop recording: {e}")
            self.writer = None
            self.current_file = None
            return None
    
    def is_recording(self) -> bool:
        """Check if currently recording.
        
        Returns:
            True if recording, False otherwise.
        """
        return self.writer is not None
    
    def get_duration(self) -> float:
        """Get current recording duration in seconds.
        
        Returns:
            Duration in seconds, or 0 if not recording.
        """
        if self.start_time is None:
            return 0.0
        return time.time() - self.start_time
    
    def get_current_file(self) -> Optional[Path]:
        """Get path to current recording file.
        
        Returns:
            Path to current file, or None if not recording.
        """
        return self.current_file
    
    def should_split(self) -> bool:
        """Check if current recording should be split (reached auto_split_duration).
        
        Returns:
            True if recording duration >= auto_split_duration, False otherwise.
        """
        if self.start_time is None:
            return False
        duration = time.time() - self.start_time
        return duration >= self.auto_split_duration
    
    def split_recording(self) -> Optional[Path]:
        """Split current recording into a new file (for auto-split functionality).
        
        This stops the current recording and immediately starts a new one.
        The current file is saved and a new file is created.
        
        Returns:
            Path to the new recording file, or None if split failed.
        """
        if self.writer is None:
            return None
        
        # Save current file
        old_file = self.current_file
        duration = time.time() - self.start_time if self.start_time else 0
        
        try:
            # Close current writer
            try:
                self.writer.close()
            except Exception as e:
                logger.warning(f"Error closing writer during split: {e}")
            self.writer = None
            
            # Save old file if it meets minimum duration
            if duration >= self.min_duration:
                logger.info(f"Auto-split: Saved {old_file.name} (duration: {duration:.1f}s)")
            else:
                logger.info(f"Auto-split: Deleting short file {old_file.name} ({duration:.1f}s < {self.min_duration}s)")
                if old_file and old_file.exists():
                    old_file.unlink()
            
            # Start new recording immediately
            from src.utils import generate_timestamped_filename
            self.current_file = generate_timestamped_filename(prefix="Record", extension=self.output_extension, output_dir=self.output_dir, filename_format=self.filename_format)
            self.writer = self._create_writer(self.current_file)
            if self.writer is None:
                self.current_file = None
                return None
            # Reset timing for new file
            self.start_time = time.time()
            self.frame_count = 0
            
            logger.info(f"Auto-split: Started new recording: {self.current_file.name}")
            return self.current_file
            
        except Exception as e:
            logger.error(f"Failed to split recording: {e}")
            self.writer = None
            self.current_file = None
            return None

