"""LSL marker stream for recording start/stop events."""
import logging
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    import pylsl
    LSL_AVAILABLE = True
except ImportError:
    LSL_AVAILABLE = False
    logger.warning("pylsl not available. LSL triggers will be disabled.")


class LSLTrigger:
    """LSL marker stream for sending recording events."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize LSL trigger system.
        
        Args:
            config: Configuration dictionary with LSL settings.
        """
        self.config = config.get("lsl", {})
        self.enabled = self.config.get("enabled", False) and LSL_AVAILABLE
        self.outlet: Optional[pylsl.StreamOutlet] = None
        
        if self.enabled:
            try:
                self._create_stream()
                logger.info("LSL marker stream created successfully")
            except Exception as e:
                logger.warning(f"Failed to create LSL stream: {e}. Continuing without LSL.")
                self.enabled = False
        else:
            if not LSL_AVAILABLE:
                logger.info("LSL not available (pylsl not installed)")
            else:
                logger.info("LSL disabled in configuration")
    
    def _create_stream(self) -> None:
        """Create LSL marker stream outlet."""
        if not LSL_AVAILABLE:
            return
        
        stream_name = self.config.get("stream_name", "VideoRecorderMarkers")
        stream_type = self.config.get("stream_type", "Markers")
        source_id = self.config.get("source_id", "continuous_video_recorder")
        
        info = pylsl.StreamInfo(
            name=stream_name,
            type=stream_type,
            channel_count=1,
            nominal_srate=pylsl.IRREGULAR_RATE,
            channel_format=pylsl.cf_string,
            source_id=source_id
        )
        
        # Add metadata
        desc = info.desc()
        desc.append_child_value("manufacturer", "ContinuousVideoRecorder")
        desc.append_child_value("description", "Recording start/stop markers")
        
        self.outlet = pylsl.StreamOutlet(info)
        logger.info(f"LSL stream '{stream_name}' created with source_id '{source_id}'")
    
    def send_start_marker(self, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Send recording start marker.
        
        Args:
            metadata: Optional metadata to include (e.g., filename, session_id).
        """
        if not self.enabled or self.outlet is None:
            return
        
        try:
            marker_value = self.config.get("marker_start", "RECORDING_START")
            marker = [marker_value]
            
            if self.config.get("include_metadata", False) and metadata:
                # Append metadata as JSON string
                import json
                metadata_str = json.dumps(metadata)
                marker.append(metadata_str)
            
            self.outlet.push_sample(marker)
            logger.debug(f"LSL marker sent: {marker_value}")
        except Exception as e:
            logger.warning(f"Failed to send LSL start marker: {e}")
    
    def send_stop_marker(self, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Send recording stop marker.
        
        Args:
            metadata: Optional metadata to include (e.g., filename, session_id, duration).
        """
        if not self.enabled or self.outlet is None:
            return
        
        try:
            marker_value = self.config.get("marker_stop", "RECORDING_STOP")
            marker = [marker_value]
            
            if self.config.get("include_metadata", False) and metadata:
                # Append metadata as JSON string
                import json
                metadata_str = json.dumps(metadata)
                marker.append(metadata_str)
            
            self.outlet.push_sample(marker)
            logger.debug(f"LSL marker sent: {marker_value}")
        except Exception as e:
            logger.warning(f"Failed to send LSL stop marker: {e}")
    
    def close(self) -> None:
        """Close LSL stream outlet."""
        if self.outlet is not None:
            try:
                # LSL outlets are automatically cleaned up when the object is deleted
                self.outlet = None
                logger.info("LSL stream closed")
            except Exception as e:
                logger.warning(f"Error closing LSL stream: {e}")

