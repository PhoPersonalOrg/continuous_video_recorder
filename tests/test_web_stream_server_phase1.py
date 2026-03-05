"""
Unit tests for Phase 1: Core Infrastructure Setup
Tests for WebStreamServer module structure, StreamBuffer class, and configuration validation.
"""

import pytest
import time
from src.web_stream_server import WebStreamServer, StreamBuffer


class TestStreamBuffer:
    """Tests for StreamBuffer class."""
    
    def test_buffer_initialization_valid_parameters(self):
        """Test buffer initialization with valid parameters."""
        buffer = StreamBuffer(camera_id=0, max_size=2, jpeg_quality=85)
        
        assert buffer.camera_id == 0
        assert buffer.max_size == 2
        assert buffer.jpeg_quality == 85
        assert buffer.frame_count == 0
        assert buffer.frames.qsize() == 0
        assert buffer.lock is not None
    
    def test_buffer_initialization_invalid_max_size(self):
        """Test buffer initialization rejects invalid max_size."""
        with pytest.raises(ValueError, match="max_size must be positive integer"):
            StreamBuffer(camera_id=0, max_size=0, jpeg_quality=85)
        
        with pytest.raises(ValueError, match="max_size must be positive integer"):
            StreamBuffer(camera_id=0, max_size=-1, jpeg_quality=85)
    
    def test_buffer_initialization_invalid_jpeg_quality(self):
        """Test buffer initialization rejects invalid jpeg_quality."""
        with pytest.raises(ValueError, match="jpeg_quality must be between 1-100"):
            StreamBuffer(camera_id=0, max_size=2, jpeg_quality=0)
        
        with pytest.raises(ValueError, match="jpeg_quality must be between 1-100"):
            StreamBuffer(camera_id=0, max_size=2, jpeg_quality=101)
    
    def test_buffer_timestamp_initialization(self):
        """Test that last_update is initialized to current time."""
        before = time.time()
        buffer = StreamBuffer(camera_id=0)
        after = time.time()
        
        assert before <= buffer.last_update <= after


class TestWebStreamServerInit:
    """Tests for WebStreamServer initialization."""
    
    def test_server_initialization(self):
        """Test WebStreamServer initialization with valid parameters."""
        camera_ids = [0, 1, 2]
        config = {
            "web_ui": {
                "enabled": True,
                "port": 5000,
                "host": "0.0.0.0",
                "jpeg_quality": 85,
                "max_buffer_size": 2
            }
        }
        
        server = WebStreamServer(camera_ids, config)
        
        assert server.camera_ids == camera_ids
        assert server.config == config
        assert server.stream_buffers == {}
        assert server.flask_app is None
        assert server.server_thread is None
        assert server.is_running_flag is False


class TestConfigurationValidation:
    """Tests for configuration loading and validation."""
    
    def test_valid_configuration(self):
        """Test loading valid web UI configuration."""
        config = {
            "web_ui": {
                "port": 5000,
                "host": "0.0.0.0",
                "jpeg_quality": 85,
                "max_buffer_size": 2
            }
        }
        
        server = WebStreamServer([0], config)
        validated = server.load_web_config()
        
        assert validated is not None
        assert validated["port"] == 5000
        assert validated["host"] == "0.0.0.0"
        assert validated["jpeg_quality"] == 85
        assert validated["max_buffer_size"] == 2
    
    def test_configuration_with_defaults(self):
        """Test configuration uses defaults for missing values."""
        config = {
            "web_ui": {}
        }
        
        server = WebStreamServer([0], config)
        validated = server.load_web_config()
        
        assert validated is not None
        assert validated["port"] == 5000
        assert validated["host"] == "0.0.0.0"
        assert validated["jpeg_quality"] == 85
        assert validated["max_buffer_size"] == 2
    
    def test_missing_web_ui_section(self):
        """Test configuration validation fails when web_ui section is missing."""
        config = {}
        
        server = WebStreamServer([0], config)
        validated = server.load_web_config()
        
        assert validated is None
    
    def test_invalid_port_too_low(self):
        """Test configuration validation rejects port below 1024."""
        config = {
            "web_ui": {
                "port": 1023
            }
        }
        
        server = WebStreamServer([0], config)
        validated = server.load_web_config()
        
        assert validated is None
    
    def test_invalid_port_too_high(self):
        """Test configuration validation rejects port above 65535."""
        config = {
            "web_ui": {
                "port": 65536
            }
        }
        
        server = WebStreamServer([0], config)
        validated = server.load_web_config()
        
        assert validated is None
    
    def test_invalid_port_not_integer(self):
        """Test configuration validation rejects non-integer port."""
        config = {
            "web_ui": {
                "port": "5000"
            }
        }
        
        server = WebStreamServer([0], config)
        validated = server.load_web_config()
        
        assert validated is None
    
    def test_invalid_jpeg_quality_too_low(self):
        """Test configuration validation rejects jpeg_quality below 1."""
        config = {
            "web_ui": {
                "jpeg_quality": 0
            }
        }
        
        server = WebStreamServer([0], config)
        validated = server.load_web_config()
        
        assert validated is None
    
    def test_invalid_jpeg_quality_too_high(self):
        """Test configuration validation rejects jpeg_quality above 100."""
        config = {
            "web_ui": {
                "jpeg_quality": 101
            }
        }
        
        server = WebStreamServer([0], config)
        validated = server.load_web_config()
        
        assert validated is None
    
    def test_invalid_max_buffer_size_zero(self):
        """Test configuration validation rejects max_buffer_size of 0."""
        config = {
            "web_ui": {
                "max_buffer_size": 0
            }
        }
        
        server = WebStreamServer([0], config)
        validated = server.load_web_config()
        
        assert validated is None
    
    def test_invalid_max_buffer_size_negative(self):
        """Test configuration validation rejects negative max_buffer_size."""
        config = {
            "web_ui": {
                "max_buffer_size": -1
            }
        }
        
        server = WebStreamServer([0], config)
        validated = server.load_web_config()
        
        assert validated is None
    
    def test_valid_port_boundary_values(self):
        """Test configuration accepts valid port boundary values."""
        # Test minimum valid port
        config_min = {
            "web_ui": {
                "port": 1024
            }
        }
        server_min = WebStreamServer([0], config_min)
        validated_min = server_min.load_web_config()
        assert validated_min is not None
        assert validated_min["port"] == 1024
        
        # Test maximum valid port
        config_max = {
            "web_ui": {
                "port": 65535
            }
        }
        server_max = WebStreamServer([0], config_max)
        validated_max = server_max.load_web_config()
        assert validated_max is not None
        assert validated_max["port"] == 65535
    
    def test_valid_jpeg_quality_boundary_values(self):
        """Test configuration accepts valid jpeg_quality boundary values."""
        # Test minimum valid quality
        config_min = {
            "web_ui": {
                "jpeg_quality": 1
            }
        }
        server_min = WebStreamServer([0], config_min)
        validated_min = server_min.load_web_config()
        assert validated_min is not None
        assert validated_min["jpeg_quality"] == 1
        
        # Test maximum valid quality
        config_max = {
            "web_ui": {
                "jpeg_quality": 100
            }
        }
        server_max = WebStreamServer([0], config_max)
        validated_max = server_max.load_web_config()
        assert validated_max is not None
        assert validated_max["jpeg_quality"] == 100
