"""
Unit tests for WebStreamServer module.

Tests cover buffer initialization, frame encoding, frame updates,
MJPEG stream generation, and configuration validation.
"""

import pytest
import numpy as np
import time
import threading
from unittest.mock import Mock, patch
from src.web_stream_server import WebStreamServer, StreamBuffer


class TestStreamBuffer:
    """Tests for StreamBuffer class."""
    
    def test_buffer_initialization_valid(self):
        """Test buffer initialization with valid parameters."""
        buffer = StreamBuffer(camera_id=0, max_size=2, jpeg_quality=85)
        
        assert buffer.camera_id == 0
        assert buffer.max_size == 2
        assert buffer.jpeg_quality == 85
        assert buffer.frame_count == 0
        assert buffer.frames.qsize() == 0
    
    def test_buffer_initialization_invalid_max_size(self):
        """Test buffer initialization rejects invalid max_size."""
        with pytest.raises(ValueError, match="max_size must be positive"):
            StreamBuffer(camera_id=0, max_size=0, jpeg_quality=85)
        
        with pytest.raises(ValueError, match="max_size must be positive"):
            StreamBuffer(camera_id=0, max_size=-1, jpeg_quality=85)
    
    def test_buffer_initialization_invalid_jpeg_quality(self):
        """Test buffer initialization rejects invalid jpeg_quality."""
        with pytest.raises(ValueError, match="jpeg_quality must be between 1-100"):
            StreamBuffer(camera_id=0, max_size=2, jpeg_quality=0)
        
        with pytest.raises(ValueError, match="jpeg_quality must be between 1-100"):
            StreamBuffer(camera_id=0, max_size=2, jpeg_quality=101)


class TestWebStreamServerConfiguration:
    """Tests for configuration loading and validation."""
    
    def test_load_valid_config(self):
        """Test loading valid web UI configuration."""
        config = {
            "web_ui": {
                "enabled": True,
                "port": 5000,
                "host": "0.0.0.0",
                "jpeg_quality": 85,
                "max_buffer_size": 2
            }
        }
        
        server = WebStreamServer(camera_ids=[0, 1], config=config)
        web_config = server.load_web_config()
        
        assert web_config is not None
        assert web_config["port"] == 5000
        assert web_config["host"] == "0.0.0.0"
        assert web_config["jpeg_quality"] == 85
        assert web_config["max_buffer_size"] == 2
    
    def test_load_config_missing_section(self):
        """Test loading config without web_ui section."""
        config = {}
        
        server = WebStreamServer(camera_ids=[0], config=config)
        web_config = server.load_web_config()
        
        assert web_config is None
    
    def test_load_config_invalid_port(self):
        """Test loading config with invalid port values."""
        # Port too low
        config = {"web_ui": {"port": 1023}}
        server = WebStreamServer(camera_ids=[0], config=config)
        assert server.load_web_config() is None
        
        # Port too high
        config = {"web_ui": {"port": 65536}}
        server = WebStreamServer(camera_ids=[0], config=config)
        assert server.load_web_config() is None
        
        # Port not an integer
        config = {"web_ui": {"port": "5000"}}
        server = WebStreamServer(camera_ids=[0], config=config)
        assert server.load_web_config() is None
    
    def test_load_config_invalid_jpeg_quality(self):
        """Test loading config with invalid jpeg_quality."""
        config = {"web_ui": {"port": 5000, "jpeg_quality": 0}}
        server = WebStreamServer(camera_ids=[0], config=config)
        assert server.load_web_config() is None
        
        config = {"web_ui": {"port": 5000, "jpeg_quality": 101}}
        server = WebStreamServer(camera_ids=[0], config=config)
        assert server.load_web_config() is None
    
    def test_load_config_with_defaults(self):
        """Test loading config uses defaults for missing values."""
        config = {"web_ui": {}}
        
        server = WebStreamServer(camera_ids=[0], config=config)
        web_config = server.load_web_config()
        
        assert web_config is not None
        assert web_config["port"] == 5000
        assert web_config["host"] == "0.0.0.0"
        assert web_config["jpeg_quality"] == 85
        assert web_config["max_buffer_size"] == 2


class TestBufferInitialization:
    """Tests for buffer initialization."""
    
    def test_initialize_buffers_success(self):
        """Test successful buffer initialization for multiple cameras."""
        config = {
            "web_ui": {
                "port": 5000,
                "jpeg_quality": 85,
                "max_buffer_size": 2
            }
        }
        
        server = WebStreamServer(camera_ids=[0, 1, 2], config=config)
        result = server.initialize_buffers()
        
        assert result is True
        assert len(server.stream_buffers) == 3
        assert 0 in server.stream_buffers
        assert 1 in server.stream_buffers
        assert 2 in server.stream_buffers
        
        # Verify buffer properties
        for camera_id in [0, 1, 2]:
            buffer = server.stream_buffers[camera_id]
            assert buffer.camera_id == camera_id
            assert buffer.jpeg_quality == 85
            assert buffer.max_size == 2
            assert buffer.frame_count == 0
    
    def test_initialize_buffers_invalid_config(self):
        """Test buffer initialization fails with invalid config."""
        config = {"web_ui": {"port": 99999}}  # Invalid port
        
        server = WebStreamServer(camera_ids=[0], config=config)
        result = server.initialize_buffers()
        
        assert result is False
        assert len(server.stream_buffers) == 0


class TestFrameEncoding:
    """Tests for frame encoding."""
    
    def test_encode_valid_frame(self):
        """Test encoding a valid BGR frame."""
        config = {"web_ui": {"port": 5000}}
        server = WebStreamServer(camera_ids=[0], config=config)
        
        # Create a simple test frame (100x100 blue image)
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        frame[:, :] = [255, 0, 0]  # BGR blue
        
        success, jpeg_data = server.encode_frame(frame, quality=85)
        
        assert success is True
        assert jpeg_data is not None
        assert isinstance(jpeg_data, bytes)
        assert len(jpeg_data) > 0
    
    def test_encode_different_qualities(self):
        """Test encoding with different quality settings."""
        config = {"web_ui": {"port": 5000}}
        server = WebStreamServer(camera_ids=[0], config=config)
        
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        
        # Higher quality should produce larger files
        success_low, data_low = server.encode_frame(frame, quality=10)
        success_high, data_high = server.encode_frame(frame, quality=95)
        
        assert success_low is True
        assert success_high is True
        assert len(data_high) >= len(data_low)


class TestFrameUpdate:
    """Tests for frame update functionality."""
    
    def test_update_frame_success(self):
        """Test successful frame update."""
        config = {"web_ui": {"port": 5000, "jpeg_quality": 85, "max_buffer_size": 2}}
        server = WebStreamServer(camera_ids=[0], config=config)
        server.initialize_buffers()
        
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        
        result = server.update_frame(0, frame)
        
        assert result is True
        
        buffer = server.stream_buffers[0]
        assert buffer.frame_count == 1
        assert buffer.frames.qsize() == 1
    
    def test_update_frame_invalid_camera(self):
        """Test frame update with non-existent camera."""
        config = {"web_ui": {"port": 5000}}
        server = WebStreamServer(camera_ids=[0], config=config)
        server.initialize_buffers()
        
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        
        result = server.update_frame(999, frame)
        
        assert result is False
    
    def test_update_frame_none_frame(self):
        """Test frame update with None frame."""
        config = {"web_ui": {"port": 5000}}
        server = WebStreamServer(camera_ids=[0], config=config)
        server.initialize_buffers()
        
        result = server.update_frame(0, None)
        
        assert result is False
    
    def test_update_frame_invalid_dimensions(self):
        """Test frame update with invalid frame dimensions."""
        config = {"web_ui": {"port": 5000}}
        server = WebStreamServer(camera_ids=[0], config=config)
        server.initialize_buffers()
        
        # 2D array instead of 3D
        frame = np.zeros((100, 100), dtype=np.uint8)
        
        result = server.update_frame(0, frame)
        
        assert result is False
    
    def test_buffer_size_limit(self):
        """Test that buffer respects max_size limit."""
        config = {"web_ui": {"port": 5000, "jpeg_quality": 85, "max_buffer_size": 2}}
        server = WebStreamServer(camera_ids=[0], config=config)
        server.initialize_buffers()
        
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        
        # Add 3 frames to a buffer with max_size=2
        server.update_frame(0, frame)
        server.update_frame(0, frame)
        server.update_frame(0, frame)
        
        buffer = server.stream_buffers[0]
        
        # Buffer should never exceed max_size
        assert buffer.frames.qsize() <= 2
        assert buffer.frame_count == 3  # But count should track all frames
    
    def test_frame_metadata_updates(self):
        """Test that frame metadata is updated correctly."""
        config = {"web_ui": {"port": 5000}}
        server = WebStreamServer(camera_ids=[0], config=config)
        server.initialize_buffers()
        
        buffer = server.stream_buffers[0]
        initial_time = buffer.last_update
        
        time.sleep(0.01)  # Small delay to ensure timestamp changes
        
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        server.update_frame(0, frame)
        
        # Verify metadata updated
        assert buffer.last_update > initial_time
        assert buffer.frame_count == 1


class TestCameraStatus:
    """Tests for camera status retrieval."""
    
    def test_get_camera_status_all_connected(self):
        """Test getting status when all cameras are connected."""
        config = {"web_ui": {"port": 5000}}
        server = WebStreamServer(camera_ids=[0, 1], config=config)
        server.initialize_buffers()
        
        # Add recent frames
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        server.update_frame(0, frame)
        server.update_frame(1, frame)
        
        status = server.get_camera_status()
        
        assert len(status) == 2
        assert status[0]['is_connected'] is True
        assert status[1]['is_connected'] is True
        assert status[0]['camera_id'] == 0
        assert status[1]['camera_id'] == 1
        assert '/stream/0' in status[0]['stream_url']
        assert '/stream/1' in status[1]['stream_url']
    
    def test_get_camera_status_disconnected(self):
        """Test getting status when camera is disconnected (>5s since last frame)."""
        config = {"web_ui": {"port": 5000}}
        server = WebStreamServer(camera_ids=[0], config=config)
        server.initialize_buffers()
        
        # Set last_update to 6 seconds ago
        buffer = server.stream_buffers[0]
        buffer.last_update = time.time() - 6.0
        
        status = server.get_camera_status()
        
        assert status[0]['is_connected'] is False
    
    def test_get_camera_status_includes_all_fields(self):
        """Test that status includes all required fields."""
        config = {"web_ui": {"port": 5000}}
        server = WebStreamServer(camera_ids=[0], config=config)
        server.initialize_buffers()
        
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        server.update_frame(0, frame)
        
        status = server.get_camera_status()
        
        required_fields = ['camera_id', 'is_connected', 'last_frame_time', 'frame_count', 'stream_url']
        for field in required_fields:
            assert field in status[0]


class TestThreadSafety:
    """Tests for thread-safe operations."""
    
    def test_concurrent_frame_updates(self):
        """Test that concurrent frame updates are thread-safe."""
        config = {"web_ui": {"port": 5000}}
        server = WebStreamServer(camera_ids=[0], config=config)
        server.initialize_buffers()
        
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        
        # Create multiple threads that update frames concurrently
        threads = []
        num_threads = 10
        updates_per_thread = 10
        
        def update_frames():
            for _ in range(updates_per_thread):
                server.update_frame(0, frame)
        
        for _ in range(num_threads):
            thread = threading.Thread(target=update_frames)
            threads.append(thread)
            thread.start()
        
        for thread in threads:
            thread.join()
        
        buffer = server.stream_buffers[0]
        
        # All updates should be counted
        assert buffer.frame_count == num_threads * updates_per_thread
        
        # Buffer size should still respect max_size
        assert buffer.frames.qsize() <= buffer.max_size


class TestServerLifecycle:
    """Tests for server start/stop lifecycle."""
    
    def test_is_running_initially_false(self):
        """Test that server is not running initially."""
        config = {"web_ui": {"port": 5000}}
        server = WebStreamServer(camera_ids=[0], config=config)
        
        assert server.is_running() is False
    
    def test_server_status_check(self):
        """Test server status check method."""
        config = {"web_ui": {"port": 5000}}
        server = WebStreamServer(camera_ids=[0], config=config)
        
        # Initially not running
        assert server.is_running() is False
        
        # Set flag manually for testing
        server.is_running_flag = True
        server.server_thread = Mock()
        server.server_thread.is_alive.return_value = True
        
        assert server.is_running() is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
