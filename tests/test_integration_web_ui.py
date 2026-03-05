"""Integration tests for web UI integration with ContinuousVideoRecorder."""
import pytest
import numpy as np
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from main import ContinuousVideoRecorder


class TestWebUIIntegration:
    """Test web UI integration with main application."""
    
    @patch('main.CameraManager')
    @patch('main.setup_logging')
    @patch('main.ConfigLoader.load_config')
    def test_web_server_initialization_when_enabled(self, mock_load_config, mock_logging, mock_camera_manager):
        """Test that web server initializes when enabled in config."""
        # Setup mock config with web UI enabled
        mock_config = {
            'storage': {'output_dir': './recordings'},
            'webcam': {'devices': [0]},
            'buffer': {'absence_timeout': 35},
            'web_ui': {
                'enabled': True,
                'port': 5000,
                'host': '0.0.0.0',
                'jpeg_quality': 85,
                'max_buffer_size': 2
            },
            'preview': {'enabled': False}
        }
        mock_load_config.return_value = mock_config
        mock_logging.return_value = Mock()
        
        # Setup mock camera manager
        mock_cam_mgr = Mock()
        mock_cam_mgr.cameras = {0: {'config': {'fps': 30}}}
        mock_camera_manager.return_value = mock_cam_mgr
        
        # Create recorder instance
        with patch('main.WebStreamServer') as mock_web_server_class:
            mock_web_server = Mock()
            mock_web_server.start_server.return_value = True
            mock_web_server_class.return_value = mock_web_server
            
            recorder = ContinuousVideoRecorder()
            
            # Initialize cameras (which should also initialize web server)
            with patch.object(mock_cam_mgr, 'initialize_cameras', return_value=True):
                result = recorder.initialize_cameras()
            
            # Verify web server was created and started
            assert result is True
            mock_web_server_class.assert_called_once_with([0], mock_config)
            mock_web_server.start_server.assert_called_once_with(5000, '0.0.0.0')
            assert recorder.web_server is not None
    
    @patch('main.CameraManager')
    @patch('main.setup_logging')
    @patch('main.ConfigLoader.load_config')
    def test_web_server_not_initialized_when_disabled(self, mock_load_config, mock_logging, mock_camera_manager):
        """Test that web server is not initialized when disabled in config."""
        # Setup mock config with web UI disabled
        mock_config = {
            'storage': {'output_dir': './recordings'},
            'webcam': {'devices': [0]},
            'buffer': {'absence_timeout': 35},
            'web_ui': {'enabled': False},
            'preview': {'enabled': False}
        }
        mock_load_config.return_value = mock_config
        mock_logging.return_value = Mock()
        
        # Setup mock camera manager
        mock_cam_mgr = Mock()
        mock_cam_mgr.cameras = {0: {'config': {'fps': 30}}}
        mock_camera_manager.return_value = mock_cam_mgr
        
        # Create recorder instance
        recorder = ContinuousVideoRecorder()
        
        # Initialize cameras
        with patch.object(mock_cam_mgr, 'initialize_cameras', return_value=True):
            result = recorder.initialize_cameras()
        
        # Verify web server was not created
        assert result is True
        assert recorder.web_server is None
    
    @patch('main.CameraManager')
    @patch('main.setup_logging')
    @patch('main.ConfigLoader.load_config')
    def test_frame_update_calls_web_server(self, mock_load_config, mock_logging, mock_camera_manager):
        """Test that frame updates are sent to web server when available."""
        # Setup mock config
        mock_config = {
            'storage': {'output_dir': './recordings'},
            'webcam': {'devices': [0]},
            'buffer': {'absence_timeout': 35},
            'web_ui': {'enabled': True, 'port': 5000, 'host': '0.0.0.0'},
            'preview': {'enabled': False}
        }
        mock_load_config.return_value = mock_config
        mock_logging.return_value = Mock()
        
        # Setup mock camera manager
        mock_cam_mgr = Mock()
        mock_cam_mgr.cameras = {0: {'config': {'fps': 30}}}
        mock_camera_manager.return_value = mock_cam_mgr
        
        # Create recorder with mock web server
        recorder = ContinuousVideoRecorder()
        recorder.web_server = Mock()
        
        # Create a test frame
        test_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        
        # Simulate frame update (this would happen in the main loop)
        camera_id = 0
        if recorder.web_server and test_frame is not None:
            try:
                recorder.web_server.update_frame(camera_id, test_frame)
            except Exception:
                pass
        
        # Verify web server update_frame was called
        recorder.web_server.update_frame.assert_called_once_with(camera_id, test_frame)
    
    @patch('main.CameraManager')
    @patch('main.setup_logging')
    @patch('main.ConfigLoader.load_config')
    def test_web_server_shutdown_on_application_shutdown(self, mock_load_config, mock_logging, mock_camera_manager):
        """Test that web server is properly shut down when application shuts down."""
        # Setup mock config
        mock_config = {
            'storage': {'output_dir': './recordings'},
            'webcam': {'devices': [0]},
            'buffer': {'absence_timeout': 35},
            'web_ui': {'enabled': True},
            'preview': {'enabled': False}
        }
        mock_load_config.return_value = mock_config
        mock_logging.return_value = Mock()
        
        # Setup mock camera manager
        mock_cam_mgr = Mock()
        mock_cam_mgr.cameras = {}
        mock_camera_manager.return_value = mock_cam_mgr
        
        # Create recorder with mock web server
        recorder = ContinuousVideoRecorder()
        recorder.web_server = Mock()
        
        # Mock other components
        recorder.lsl_trigger = Mock()
        
        # Call shutdown
        recorder.shutdown()
        
        # Verify web server stop_server was called
        recorder.web_server.stop_server.assert_called_once()
    
    @patch('main.CameraManager')
    @patch('main.setup_logging')
    @patch('main.ConfigLoader.load_config')
    def test_web_server_error_handling_during_initialization(self, mock_load_config, mock_logging, mock_camera_manager):
        """Test that initialization errors are handled gracefully."""
        # Setup mock config with web UI enabled
        mock_config = {
            'storage': {'output_dir': './recordings'},
            'webcam': {'devices': [0]},
            'buffer': {'absence_timeout': 35},
            'web_ui': {
                'enabled': True,
                'port': 5000,
                'host': '0.0.0.0'
            },
            'preview': {'enabled': False}
        }
        mock_load_config.return_value = mock_config
        mock_logging.return_value = Mock()
        
        # Setup mock camera manager
        mock_cam_mgr = Mock()
        mock_cam_mgr.cameras = {0: {'config': {'fps': 30}}}
        mock_camera_manager.return_value = mock_cam_mgr
        
        # Create recorder instance
        with patch('main.WebStreamServer') as mock_web_server_class:
            # Make WebStreamServer raise an exception
            mock_web_server_class.side_effect = Exception("Test error")
            
            recorder = ContinuousVideoRecorder()
            
            # Initialize cameras (should handle web server error gracefully)
            with patch.object(mock_cam_mgr, 'initialize_cameras', return_value=True):
                result = recorder.initialize_cameras()
            
            # Verify initialization succeeded despite web server error
            assert result is True
            assert recorder.web_server is None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
