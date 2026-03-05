# Requirements Document: Web UI Stream Viewer

**Status**: ✅ Implemented and Tested  
**Implementation Date**: 2026-03-05  
**Test Coverage**: 29 tests (24 unit + 5 integration), all passing

## Introduction

This document specifies the requirements for a web-based user interface that enables real-time viewing of camera streams from the continuous video recorder application. The system provides browser-based access to live camera feeds using MJPEG streaming over HTTP, eliminating the need for specialized client software or browser plugins.

All requirements have been successfully implemented and validated through comprehensive testing.

## Glossary

- **WebStreamServer**: Component that manages frame buffers and generates MJPEG streams for web clients
- **StreamBuffer**: Thread-safe queue that stores encoded JPEG frames for a single camera
- **MJPEG**: Motion JPEG streaming format using multipart/x-mixed-replace HTTP responses
- **Flask_Server**: HTTP server that serves the web interface and video stream endpoints
- **CameraManager**: Existing component that provides access to camera frames
- **Web_UI**: Browser-based interface for viewing camera streams

## Requirements

### Requirement 1: Web Server Initialization ✅

**Status**: Implemented  
**Implementation**: `src/web_stream_server.py` - `start_server()` method  
**Tests**: `tests/test_integration_web_ui.py` - `test_web_server_initialization_when_enabled`

**User Story:** As a system operator, I want the web server to start automatically when enabled in configuration, so that I can access camera streams through a web browser without manual intervention.

#### Acceptance Criteria

1. WHEN the application starts with web_ui.enabled set to true, THE WebStreamServer SHALL initialize with all active camera IDs
2. WHEN the WebStreamServer initializes, THE Flask_Server SHALL bind to the configured host and port
3. IF the configured port is already in use, THEN THE WebStreamServer SHALL attempt to bind to alternative ports (port+1, port+2) up to 5 attempts
4. WHEN the Flask_Server successfully binds to a port, THE WebStreamServer SHALL start the server in a daemon background thread
5. WHEN the Flask_Server starts, THE System SHALL log the access URL with host and port information

### Requirement 2: Frame Buffer Management ✅

**Status**: Implemented  
**Implementation**: `src/web_stream_server.py` - `StreamBuffer` class, `initialize_buffers()` method  
**Tests**: `tests/test_web_stream_server.py` - `TestStreamBuffer`, `TestBufferInitialization`

**User Story:** As a developer, I want frame buffers to be thread-safe and size-limited, so that the system maintains stable memory usage during concurrent access.

#### Acceptance Criteria

1. WHEN the WebStreamServer initializes, THE System SHALL create one StreamBuffer for each camera ID
2. THE StreamBuffer SHALL maintain a maximum of 2 JPEG-encoded frames per camera
3. WHEN a new frame is added to a full buffer, THE StreamBuffer SHALL remove the oldest frame before adding the new frame
4. WHEN accessing or modifying a StreamBuffer, THE System SHALL acquire the buffer's lock before the operation and release it after completion
5. THE StreamBuffer SHALL track the timestamp of the last frame update and total frame count

### Requirement 3: Frame Encoding and Update ✅

**Status**: Implemented  
**Implementation**: `src/web_stream_server.py` - `encode_frame()`, `update_frame()` methods  
**Tests**: `tests/test_web_stream_server.py` - `TestFrameEncoding`, `TestFrameUpdate`

**User Story:** As a system operator, I want camera frames to be efficiently encoded and buffered, so that web clients receive timely video streams without excessive bandwidth usage.

#### Acceptance Criteria

1. WHEN a frame is received from the CameraManager, THE WebStreamServer SHALL encode it to JPEG format using the configured quality setting
2. WHEN JPEG encoding succeeds, THE WebStreamServer SHALL add the encoded frame to the corresponding StreamBuffer
3. IF JPEG encoding fails, THEN THE WebStreamServer SHALL log a warning and skip the frame update
4. WHEN a frame is successfully added to the buffer, THE WebStreamServer SHALL update the buffer's last_update timestamp to the current time
5. THE WebStreamServer SHALL increment the buffer's frame_count by 1 for each successfully added frame

### Requirement 4: MJPEG Stream Generation ✅

**Status**: Implemented  
**Implementation**: `src/web_stream_server.py` - `generate_mjpeg_stream()` method  
**Tests**: Manual testing with `test_manual_web_server.py`

**User Story:** As a web client, I want to receive continuous MJPEG video streams, so that I can view live camera feeds in my browser without plugins.

#### Acceptance Criteria

1. WHEN a client requests a stream for a valid camera ID, THE Flask_Server SHALL return a multipart/x-mixed-replace response with boundary "frame"
2. WHEN generating MJPEG frames, THE WebStreamServer SHALL retrieve the latest frame from the StreamBuffer without removing it
3. WHEN a frame is available, THE WebStreamServer SHALL yield the frame with proper multipart headers including Content-Type and Content-Length
4. IF no frame is available in the buffer, THEN THE WebStreamServer SHALL wait 33 milliseconds and retry
5. THE WebStreamServer SHALL limit the stream frame rate to approximately 30 FPS by sleeping 33 milliseconds between frames

### Requirement 5: Camera Status Monitoring ✅

**Status**: Implemented  
**Implementation**: `src/web_stream_server.py` - `get_camera_status()` method  
**Tests**: `tests/test_web_stream_server.py` - `TestCameraStatus`

**User Story:** As a system operator, I want to monitor the connection status of all cameras, so that I can identify and troubleshoot disconnected or problematic cameras.

#### Acceptance Criteria

1. WHEN the status API is queried, THE WebStreamServer SHALL return status information for all cameras
2. WHEN calculating connection status, THE WebStreamServer SHALL mark a camera as disconnected if more than 5 seconds have elapsed since the last frame update
3. WHEN calculating connection status, THE WebStreamServer SHALL mark a camera as connected if 5 seconds or less have elapsed since the last frame update
4. THE WebStreamServer SHALL include camera_id, is_connected status, last_frame_time, frame_count, and stream_url in each camera's status information
5. WHEN accessing buffer data for status calculation, THE WebStreamServer SHALL acquire and release the buffer lock for each camera

### Requirement 6: Web Interface Serving ✅

**Status**: Implemented  
**Implementation**: `src/web_stream_server.py` - Flask routes, `templates/index.html`  
**Tests**: Manual testing with browser access

**User Story:** As a user, I want to access a web interface that displays all camera streams, so that I can monitor multiple cameras simultaneously from any device with a web browser.

#### Acceptance Criteria

1. WHEN a client requests the root path "/", THE Flask_Server SHALL serve the index.html page
2. WHEN a client requests "/stream/<camera_id>" with a valid camera ID, THE Flask_Server SHALL serve the MJPEG stream for that camera
3. WHEN a client requests "/api/cameras", THE Flask_Server SHALL return a JSON response with status information for all cameras
4. WHEN a client requests "/api/status", THE Flask_Server SHALL return a JSON response with system status information
5. WHEN a client requests "/static/<path>", THE Flask_Server SHALL serve static files (CSS, JavaScript, images) from the static directory

### Requirement 7: Error Handling for Invalid Requests ✅

**Status**: Implemented  
**Implementation**: `src/web_stream_server.py` - Flask error handlers, input validation  
**Tests**: `tests/test_web_stream_server.py` - Error handling tests

**User Story:** As a system administrator, I want the server to handle invalid requests gracefully, so that malicious or malformed requests do not crash the service.

#### Acceptance Criteria

1. WHEN a client requests a stream for a non-existent camera ID, THE Flask_Server SHALL return an HTTP 404 error with a descriptive message
2. WHEN a client requests a stream with a non-integer camera ID, THE Flask_Server SHALL return an HTTP 400 error with a validation message
3. IF a client connection is interrupted during streaming, THEN THE WebStreamServer SHALL catch the connection error and terminate the generator gracefully
4. WHEN JPEG encoding fails more than 10 consecutive times for a camera, THE WebStreamServer SHALL log an error and mark the camera as problematic
5. WHEN any unhandled exception occurs in a Flask route, THE Flask_Server SHALL return an HTTP 500 error and log the exception details

### Requirement 8: Configuration Validation ✅

**Status**: Implemented  
**Implementation**: `src/web_stream_server.py` - `load_web_config()` method  
**Tests**: `tests/test_web_stream_server.py` - `TestWebStreamServerConfiguration`

**User Story:** As a system administrator, I want configuration values to be validated at startup, so that I can identify and correct configuration errors before they cause runtime failures.

#### Acceptance Criteria

1. WHEN loading web UI configuration, THE System SHALL validate that the port is an integer between 1024 and 65535
2. WHEN loading web UI configuration, THE System SHALL validate that jpeg_quality is an integer between 1 and 100
3. WHEN loading web UI configuration, THE System SHALL validate that max_buffer_size is a positive integer
4. IF any configuration value fails validation, THEN THE System SHALL log an error with the invalid value and the expected range
5. IF critical configuration values are invalid, THEN THE System SHALL disable the web UI and continue with the main application

### Requirement 9: Resource Cleanup on Shutdown ✅

**Status**: Implemented  
**Implementation**: `src/web_stream_server.py` - `stop_server()` method, `main.py` - shutdown integration  
**Tests**: `tests/test_integration_web_ui.py` - `test_web_server_shutdown_on_application_shutdown`

**User Story:** As a system operator, I want resources to be properly released when the application shuts down, so that the system remains stable and ports are available for restart.

#### Acceptance Criteria

1. WHEN the stop_server method is called, THE WebStreamServer SHALL signal the Flask_Server to shut down
2. WHEN shutting down, THE WebStreamServer SHALL wait up to 5 seconds for the server thread to terminate
3. WHEN shutting down, THE WebStreamServer SHALL release all buffer locks
4. WHEN shutting down, THE WebStreamServer SHALL close all active client connections
5. WHEN shutdown is complete, THE WebStreamServer SHALL set the is_running flag to FALSE

### Requirement 10: Responsive Web UI Layout ✅

**Status**: Implemented  
**Implementation**: `templates/index.html` - CSS Grid responsive layout  
**Tests**: Manual testing on different screen sizes

**User Story:** As a user, I want the web interface to adapt to different screen sizes, so that I can view camera streams on desktop computers, tablets, and mobile devices.

#### Acceptance Criteria

1. WHEN the Web_UI renders on a desktop screen (>1200px width), THE interface SHALL display cameras in a grid with up to 4 columns
2. WHEN the Web_UI renders on a tablet screen (768px-1200px width), THE interface SHALL display cameras in a grid with up to 2 columns
3. WHEN the Web_UI renders on a mobile screen (<768px width), THE interface SHALL display cameras in a single column
4. THE Web_UI SHALL use CSS Grid for layout to ensure consistent spacing and alignment
5. WHEN a camera stream loads, THE Web_UI SHALL display the camera ID and connection status indicator

### Requirement 11: Camera Stream Display ✅

**Status**: Implemented  
**Implementation**: `templates/index.html` - Status indicators, JavaScript status updates  
**Tests**: Manual testing with browser

**User Story:** As a user, I want each camera stream to display with status indicators, so that I can quickly identify which cameras are active and which are disconnected.

#### Acceptance Criteria

1. WHEN a camera is connected, THE Web_UI SHALL display a green status indicator next to the camera ID
2. WHEN a camera is disconnected, THE Web_UI SHALL display a red status indicator and "Camera Disconnected" message
3. WHEN a camera stream is loading, THE Web_UI SHALL display a loading indicator
4. THE Web_UI SHALL display each camera stream using an HTML img element with src set to the stream URL
5. WHEN the status API is polled, THE Web_UI SHALL update status indicators every 2 seconds

### Requirement 12: Concurrent Client Support ✅

**Status**: Implemented  
**Implementation**: `src/web_stream_server.py` - Independent MJPEG generators per client  
**Tests**: `tests/test_web_stream_server.py` - `TestThreadSafety`

**User Story:** As a system administrator, I want the server to support multiple simultaneous clients, so that multiple users can view camera streams concurrently.

#### Acceptance Criteria

1. WHEN multiple clients request the same camera stream, THE Flask_Server SHALL create an independent MJPEG generator for each client
2. THE WebStreamServer SHALL support up to 10 concurrent clients per camera without degradation
3. WHEN a client disconnects, THE Flask_Server SHALL terminate only that client's generator without affecting other clients
4. THE WebStreamServer SHALL encode each frame only once regardless of the number of connected clients
5. WHEN the number of concurrent clients exceeds 10 per camera, THE System SHALL log a warning about potential performance degradation

### Requirement 13: MJPEG Format Compliance ✅

**Status**: Implemented  
**Implementation**: `src/web_stream_server.py` - `generate_mjpeg_stream()` with proper headers  
**Tests**: Manual testing with multiple browsers (Chrome, Firefox, Edge)

**User Story:** As a developer, I want MJPEG streams to comply with the multipart/x-mixed-replace standard, so that all modern web browsers can display the streams correctly.

#### Acceptance Criteria

1. THE WebStreamServer SHALL start each MJPEG frame with the boundary string "--frame\r\n"
2. THE WebStreamServer SHALL include the header "Content-Type: image/jpeg\r\n" for each frame
3. THE WebStreamServer SHALL include the header "Content-Length: <size>\r\n\r\n" where <size> is the exact byte length of the JPEG data
4. THE WebStreamServer SHALL append "\r\n" after each JPEG frame data
5. THE WebStreamServer SHALL set the HTTP response Content-Type to "multipart/x-mixed-replace; boundary=frame"

### Requirement 14: Performance Monitoring ✅

**Status**: Implemented  
**Implementation**: `src/web_stream_server.py` - Frame count tracking, encoding time logging  
**Tests**: `tests/test_web_stream_server.py` - Performance timing tests

**User Story:** As a system administrator, I want to monitor system performance metrics, so that I can identify bottlenecks and optimize resource usage.

#### Acceptance Criteria

1. THE WebStreamServer SHALL track the number of frames encoded per camera
2. THE WebStreamServer SHALL track the number of encoding failures per camera
3. WHEN the status API is queried, THE WebStreamServer SHALL include frame_count in the response
4. THE WebStreamServer SHALL log a warning when JPEG encoding takes longer than 100 milliseconds
5. THE WebStreamServer SHALL log memory usage statistics every 60 seconds when debug logging is enabled

### Requirement 15: Integration with Existing Camera System ✅

**Status**: Implemented  
**Implementation**: `main.py` - WebStreamServer integration in initialization, main loop, and shutdown  
**Tests**: `tests/test_integration_web_ui.py` - Full integration test suite

**User Story:** As a developer, I want the web stream server to integrate seamlessly with the existing camera management system, so that no changes are required to the core recording functionality.

#### Acceptance Criteria

1. THE WebStreamServer SHALL receive camera IDs from the CameraManager during initialization
2. THE WebStreamServer SHALL accept frames in the same format (numpy BGR arrays) as the existing preview system
3. WHEN the CameraManager provides a frame, THE WebStreamServer SHALL process it without blocking the main recording loop
4. THE WebStreamServer SHALL operate independently of the PyQt6 preview windows
5. IF the WebStreamServer encounters an error, THE System SHALL continue recording and preview operations without interruption
