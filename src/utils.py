"""Helper functions for logging, file management, and signal handling."""
import logging
import signal
import sys
from pathlib import Path
from typing import Optional, Callable
from datetime import datetime


def setup_logging(log_dir: Optional[Path] = None, log_level: int = logging.INFO) -> logging.Logger:
    """Set up logging to both console and file.
    
    Args:
        log_dir: Directory for log file. If None, logs only to console.
        log_level: Logging level (default: INFO).
        
    Returns:
        Configured logger instance.
    """
    logger = logging.getLogger("continuous_video_recorder")
    logger.setLevel(log_level)
    
    # Clear existing handlers
    logger.handlers.clear()
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_format = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)
    
    # File handler (if log_dir provided)
    if log_dir:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"recorder_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(log_level)
        file_format = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler.setFormatter(file_format)
        logger.addHandler(file_handler)
        logger.info(f"Logging to file: {log_file}")
    
    return logger


def setup_signal_handlers(shutdown_callback: Callable[[], None]) -> None:
    """Set up signal handlers for graceful shutdown.
    
    Args:
        shutdown_callback: Function to call on shutdown signal.
    """
    def signal_handler(signum, frame):
        logging.getLogger("continuous_video_recorder").info(f"Received signal {signum}, shutting down...")
        shutdown_callback()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    if sys.platform != "win32":
        signal.signal(signal.SIGHUP, signal_handler)


def ensure_directory(path: Path) -> Path:
    """Ensure directory exists, create if it doesn't.
    
    Args:
        path: Directory path.
        
    Returns:
        Path object (created if needed).
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def generate_timestamped_filename(prefix: str = "Record", extension: str = "mp4", output_dir: Optional[Path] = None) -> Path:
    """Generate a timestamped filename.
    
    Args:
        prefix: Filename prefix.
        extension: File extension (without dot).
        output_dir: Output directory. If None, returns just filename.
        
    Returns:
        Path to the generated filename.
    """
    timestamp = datetime.now().strftime("%Y-%m-%dT%H%M%S")
    filename = f"{prefix}_{timestamp}.{extension}"
    
    if output_dir:
        output_dir = ensure_directory(Path(output_dir))
        return output_dir / filename
    return Path(filename)


def format_duration(seconds: float) -> str:
    """Format duration in seconds to human-readable string.
    
    Args:
        seconds: Duration in seconds.
        
    Returns:
        Formatted string (e.g., "1h 23m 45s").
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    
    return " ".join(parts)

