"""LSL marker stream for recording start/stop events."""
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

try:
    import pylsl
    from pylsl import StreamInfo, StreamOutlet
    from phopylslhelper.easy_time_sync import EasyTimeSyncParsingMixin, readable_dt_str, from_readable_dt_str
    LSL_AVAILABLE = True
except ImportError:
    LSL_AVAILABLE = False
    logger.warning("pylsl not available. LSL triggers will be disabled.")


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
        """ adds common LSL metadata
        """
        # Add some metadata
        info.desc().append_child_value("manufacturer", "ContinuousVideoRecorder")
        info.desc().append_child_value("version", "0.3.0")
        info.desc().append_child_value("description", "Recording start/stop markers with multi-camera support")

        ## add a custom timestamp field to the stream info:
        info = self.EasyTimeSyncParsingMixin_add_lsl_outlet_info(info=info)
        return info
    

    # def get_lsl_outlet_camera_markers_stream_info(self) -> StreamInfo:
    #     """Create LSL stream for EEG sensor data"""
    #     info = self.add_lsl_outlet_info_common(info=info)
    #     return info

    # def get_lsl_outlet_motion_stream_info(self) -> StreamInfo:
    #     """Create LSL stream info for motion sensor data (accelerometer + gyroscope)"""
    #     info = self.add_lsl_outlet_info_common(info=info)
    #     return info
    

    def get_lsl_outlet_camera_markers_stream_info(self) -> StreamInfo:
        """ 
        raw_packet_outlet = None
        if self.is_reverse_engineer_mode:
            raw_packet_outlet = StreamOutlet(self.get_lsl_outlet_raw_debugging_stream_info())
            print(f'Setup raw_packet_outlet (for reverse-engineering)')
            
        """

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




    def _create_stream(self) -> None:
        """Create LSL marker stream outlet."""
        if not LSL_AVAILABLE:
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
            metadata: Optional metadata to include (e.g., camera_id, filename, session_id, duration).
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

