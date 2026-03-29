"""LSL marker stream for recording start/stop events."""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from pylsl import StreamInfo, StreamOutlet

logger = logging.getLogger(__name__)

try:
    import pylsl
    from pylsl import StreamInfo, StreamOutlet
    from phopylslhelper.easy_time_sync import EasyTimeSyncParsingMixin
    lsl_available = True
except ImportError:
    LSL_AVAILABLE = False
    logger.warning("pylsl not available. LSL triggers will be disabled.")

    class EasyTimeSyncParsingMixin:
        def init_EasyTimeSyncParsingMixin(self) -> None:
            pass

        def EasyTimeSyncParsingMixin_add_lsl_outlet_info(self, info: Any) -> Any:
            return info


class LSLTrigger(EasyTimeSyncParsingMixin):
    """LSL marker stream for sending recording events."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize LSL trigger system.
        
        Args:
            config: Configuration dictionary with LSL settings.
        """
        self.config = config.get("lsl", {})
        self.enabled = self.config.get("enabled", False) and LSL_AVAILABLE
        self.outlet: Optional[StreamOutlet] = None
        
        if self.enabled:
            try:
                self.init_EasyTimeSyncParsingMixin()
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
    

    def add_lsl_outlet_info_common(self, info: StreamInfo) -> StreamInfo:
        """Adds common LSL metadata and phopylslhelper time-sync fields."""
        # Add some metadata
        info.desc().append_child_value("manufacturer", "ContinuousVideoRecorder")
        info.desc().append_child_value("version", "0.3.1")
        info.desc().append_child_value("description", "Recording start/stop markers with multi-camera support")

        ## add a custom timestamp field to the stream info:
        info = self.EasyTimeSyncParsingMixin_add_lsl_outlet_info(info=info)
        return info
    

    def get_lsl_outlet_camera_markers_stream_info(self) -> StreamInfo:
        """Build StreamInfo for the irregular-rate string marker outlet (optional JSON metadata channel)."""
        assert lsl_available and pylsl is not None and StreamInfo is not None
        stream_name = self.config.get("stream_name", "VideoRecorderMarkers")
        stream_type = self.config.get("stream_type", "Markers")
        source_id = self.config.get("source_id", "continuous_video_recorder")
        
        # Set channel count based on whether metadata will be included
        include_metadata = self.config.get("include_metadata", False)
        channel_count = 2 if include_metadata else 1
        
        info = StreamInfo(
            name=stream_name,
            type=stream_type,
            channel_count=channel_count,
            nominal_srate=pylsl.IRREGULAR_RATE,
            channel_format=pylsl.cf_string,
            source_id=source_id ## this should be the real camera source
        )

        info = self.add_lsl_outlet_info_common(info=info)
        return info


    def _build_marker_sample(self, marker_value: str, metadata: Optional[Dict[str, Any]]) -> list[str]:
        """One string channel, or marker + JSON when include_metadata is enabled."""
        marker: list[str] = [marker_value]
        if self.config.get("include_metadata", False):
            metadata_str = json.dumps(metadata) if metadata else "{}"
            marker.append(metadata_str)
        return marker


    def _create_stream(self) -> None:
        """Create LSL marker stream outlet."""
        if not lsl_available:
            return
        info = self.get_lsl_outlet_camera_markers_stream_info()
        self.outlet = pylsl.StreamOutlet(info)
        logger.info(f"LSL stream created with info: {info}") # '{stream_name}' created with source_id '{source_id}'
    

    def send_start_marker(self, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Send recording start marker.
        
        Args:
            metadata: Optional metadata to include (e.g., camera_id, filename, session_id).
        """
        if not self.enabled or self.outlet is None:
            return
        
        try:
            marker_value = str(self.config.get("marker_start", "RECORDING_START"))
            marker = self._build_marker_sample(marker_value, metadata)
            self.outlet.push_sample(marker)
            logger.debug(f"LSL marker sent: {marker_value}")
        except Exception as e:
            logger.warning(f"Failed to send LSL start marker: {e}")
    

    def send_stop_marker(self, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Send recording stop marker.
        
        Args:
            metadata: Optional metadata to include (e.g., camera_id, filename, session_id, duration).
        """
        if not self.enabled or self.outlet is None:
            return
        
        try:
            marker_value = str(self.config.get("marker_stop", "RECORDING_STOP"))
            marker = self._build_marker_sample(marker_value, metadata)
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
