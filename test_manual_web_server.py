"""
Manual test script for WebStreamServer.
Tests basic functionality without pytest.
"""

import numpy as np
import time
from src.web_stream_server import WebStreamServer, StreamBuffer


def test_buffer_creation():
    """Test StreamBuffer creation."""
    print("Testing StreamBuffer creation...")
    
    try:
        buffer = StreamBuffer(camera_id=0, max_size=2, jpeg_quality=85)
        assert buffer.camera_id == 0
        assert buffer.max_size == 2
        assert buffer.jpeg_quality == 85
        print("✓ StreamBuffer creation successful")
        return True
    except Exception as e:
        print(f"✗ StreamBuffer creation failed: {e}")
        return False


def test_invalid_buffer():
    """Test StreamBuffer with invalid parameters."""
    print("Testing StreamBuffer with invalid parameters...")
    
    try:
        buffer = StreamBuffer(camera_id=0, max_size=0, jpeg_quality=85)
        print("✗ Should have raised ValueError for max_size=0")
        return False
    except ValueError:
        print("✓ Correctly rejected invalid max_size")
        return True
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
        return False


def test_config_validation():
    """Test configuration validation."""
    print("Testing configuration validation...")
    
    config = {
        "web_ui": {
            "port": 5000,
            "host": "0.0.0.0",
            "jpeg_quality": 85,
            "max_buffer_size": 2
        }
    }
    
    try:
        server = WebStreamServer(camera_ids=[0, 1], config=config)
        web_config = server.load_web_config()
        
        assert web_config is not None
        assert web_config["port"] == 5000
        assert web_config["jpeg_quality"] == 85
        print("✓ Configuration validation successful")
        return True
    except Exception as e:
        print(f"✗ Configuration validation failed: {e}")
        return False


def test_buffer_initialization():
    """Test buffer initialization."""
    print("Testing buffer initialization...")
    
    config = {
        "web_ui": {
            "port": 5000,
            "jpeg_quality": 85,
            "max_buffer_size": 2
        }
    }
    
    try:
        server = WebStreamServer(camera_ids=[0, 1, 2], config=config)
        result = server.initialize_buffers()
        
        assert result is True
        assert len(server.stream_buffers) == 3
        assert 0 in server.stream_buffers
        assert 1 in server.stream_buffers
        assert 2 in server.stream_buffers
        print("✓ Buffer initialization successful")
        return True
    except Exception as e:
        print(f"✗ Buffer initialization failed: {e}")
        return False


def test_frame_encoding():
    """Test frame encoding."""
    print("Testing frame encoding...")
    
    config = {"web_ui": {"port": 5000}}
    
    try:
        server = WebStreamServer(camera_ids=[0], config=config)
        
        # Create a test frame (100x100 blue image)
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        frame[:, :] = [255, 0, 0]  # BGR blue
        
        success, jpeg_data = server.encode_frame(frame, quality=85)
        
        assert success is True
        assert jpeg_data is not None
        assert isinstance(jpeg_data, bytes)
        assert len(jpeg_data) > 0
        print(f"✓ Frame encoding successful (size: {len(jpeg_data)} bytes)")
        return True
    except Exception as e:
        print(f"✗ Frame encoding failed: {e}")
        return False


def test_frame_update():
    """Test frame update."""
    print("Testing frame update...")
    
    config = {
        "web_ui": {
            "port": 5000,
            "jpeg_quality": 85,
            "max_buffer_size": 2
        }
    }
    
    try:
        server = WebStreamServer(camera_ids=[0], config=config)
        server.initialize_buffers()
        
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        
        result = server.update_frame(0, frame)
        
        assert result is True
        
        buffer = server.stream_buffers[0]
        assert buffer.frame_count == 1
        assert buffer.frames.qsize() == 1
        print("✓ Frame update successful")
        return True
    except Exception as e:
        print(f"✗ Frame update failed: {e}")
        return False


def test_buffer_size_limit():
    """Test buffer size limit enforcement."""
    print("Testing buffer size limit...")
    
    config = {
        "web_ui": {
            "port": 5000,
            "jpeg_quality": 85,
            "max_buffer_size": 2
        }
    }
    
    try:
        server = WebStreamServer(camera_ids=[0], config=config)
        server.initialize_buffers()
        
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        
        # Add 5 frames to a buffer with max_size=2
        for i in range(5):
            server.update_frame(0, frame)
        
        buffer = server.stream_buffers[0]
        
        # Buffer should never exceed max_size
        assert buffer.frames.qsize() <= 2
        assert buffer.frame_count == 5  # But count should track all frames
        print(f"✓ Buffer size limit enforced (size: {buffer.frames.qsize()}, count: {buffer.frame_count})")
        return True
    except Exception as e:
        print(f"✗ Buffer size limit test failed: {e}")
        return False


def test_camera_status():
    """Test camera status retrieval."""
    print("Testing camera status retrieval...")
    
    config = {"web_ui": {"port": 5000}}
    
    try:
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
        assert 'camera_id' in status[0]
        assert 'stream_url' in status[0]
        print("✓ Camera status retrieval successful")
        return True
    except Exception as e:
        print(f"✗ Camera status retrieval failed: {e}")
        return False


def test_disconnected_camera():
    """Test disconnected camera detection."""
    print("Testing disconnected camera detection...")
    
    config = {"web_ui": {"port": 5000}}
    
    try:
        server = WebStreamServer(camera_ids=[0], config=config)
        server.initialize_buffers()
        
        # Set last_update to 6 seconds ago
        buffer = server.stream_buffers[0]
        buffer.last_update = time.time() - 6.0
        
        status = server.get_camera_status()
        
        assert status[0]['is_connected'] is False
        print("✓ Disconnected camera detection successful")
        return True
    except Exception as e:
        print(f"✗ Disconnected camera detection failed: {e}")
        return False


def main():
    """Run all tests."""
    print("=" * 60)
    print("WebStreamServer Manual Test Suite")
    print("=" * 60)
    print()
    
    tests = [
        test_buffer_creation,
        test_invalid_buffer,
        test_config_validation,
        test_buffer_initialization,
        test_frame_encoding,
        test_frame_update,
        test_buffer_size_limit,
        test_camera_status,
        test_disconnected_camera,
    ]
    
    results = []
    for test in tests:
        result = test()
        results.append(result)
        print()
    
    print("=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("✓ All tests passed!")
    else:
        print(f"✗ {total - passed} test(s) failed")
    
    print("=" * 60)
    
    return passed == total


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
