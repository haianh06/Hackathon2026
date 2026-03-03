#!/usr/bin/env python3
"""
Hardware Daemon for Raspberry Pi 5 Delivery Bot
Runs as a standalone process, connects to Node.js backend via Socket.IO
Handles: Motor control, Camera WebRTC, Hardware status
"""

import asyncio
import json
import os
import sys
import signal
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger('hardware_daemon')

# Load config
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')
try:
    with open(CONFIG_PATH, 'r') as f:
        CONFIG = json.load(f)
except FileNotFoundError:
    logger.warning("config.json not found, using defaults")
    CONFIG = {}

# Import hardware modules
from motor_control import create_motor_controller, AutoNavigator
from camera_mjpeg import CameraManager
from line_follower import LineFollower

# Socket.IO client
try:
    import socketio
    SIO_AVAILABLE = True
except ImportError:
    SIO_AVAILABLE = False
    logger.error("python-socketio not installed: pip install python-socketio[asyncio_client] aiohttp")

# HTTP server for WebRTC signaling fallback
try:
    from aiohttp import web
    import aiohttp_cors
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False


class HardwareDaemon:
    def __init__(self):
        self.motor = create_motor_controller(CONFIG)
        self.camera = CameraManager(CONFIG)
        self.line_follower = LineFollower()

        # Wire AutoNavigator with camera + line-follower
        async def _cam_getter():
            return await self.camera.get_latest_frame()

        self.navigator = AutoNavigator(
            self.motor,
            camera_getter=_cam_getter,
            line_follower=self.line_follower,
        )
        self.running = False
        self.sio = None
        self.server_url = os.environ.get('SERVER_URL', 'http://localhost:5000')

    async def connect_to_server(self):
        """Connect to Node.js backend via Socket.IO"""
        if not SIO_AVAILABLE:
            logger.error("Cannot connect: python-socketio not installed")
            return

        self.sio = socketio.AsyncClient(reconnection=True, reconnection_delay=2)

        @self.sio.event
        async def connect():
            logger.info(f"Connected to server: {self.server_url}")
            await self.sio.emit('join-room', 'hardware')
            # Report hardware status
            await self.report_status()

        @self.sio.event
        async def disconnect():
            logger.warning("Disconnected from server")

        # Motor control commands from server
        @self.sio.on('motor-command')
        async def on_motor_command(data):
            command = data.get('command', 'stop')
            speed = data.get('speed', 50)
            logger.info(f"Motor command: {command} speed={speed}")

            if command == 'forward':
                self.motor.forward(speed)
            elif command == 'backward':
                self.motor.backward(speed)
            elif command == 'left':
                self.motor.turn_left(speed)
            elif command == 'right':
                self.motor.turn_right(speed)
            elif command == 'stop':
                self.motor.stop()

            await self.sio.emit('motor-status', self.motor.get_status())

        # Navigate to a waypoint (follow path) – legacy simple mode
        @self.sio.on('navigate-to')
        async def on_navigate(data):
            path = data.get('path', [])
            logger.info(f"Navigate path (legacy): {[p.get('pointId', p) for p in path]}")
            for i, point in enumerate(path):
                point_id = point.get('pointId', point) if isinstance(point, dict) else point
                await self.sio.emit('vehicle-position-update', {'pointId': point_id})
                logger.info(f"Reached waypoint: {point_id}")
                if i < len(path) - 1:
                    self.motor.forward(50)
                    await asyncio.sleep(2)
                    self.motor.stop()
                    await asyncio.sleep(0.5)
            self.motor.stop()
            await self.sio.emit('navigation-complete', {'destination': path[-1] if path else None})

        # ── Auto-navigate: servo-controlled path following with real-time logs ──
        @self.sio.on('auto-navigate')
        async def on_auto_navigate(data):
            path = data.get('path', [])
            logger.info(f"🚀 Auto-navigate requested: {[p.get('pointId', '?') for p in path]}")
            if len(path) < 2:
                logger.warning("Auto-navigate: path too short")
                return

            async def emit_cb(event, payload):
                """Forward events to backend server."""
                if self.sio and self.sio.connected:
                    await self.sio.emit(event, payload)

            # Run navigation in background so we can still receive stop commands
            asyncio.ensure_future(self.navigator.navigate_path(path, emit_cb))

        # ── Stop navigation ──
        @self.sio.on('stop-navigation')
        async def on_stop_navigation(data=None):
            logger.info("⏹ Stop navigation command received")
            self.navigator.stop_navigation()
            await self.sio.emit('navigation-log', {
                'type': 'cancelled',
                'timestamp': __import__('time').time(),
            })

        @self.sio.on('hardware-status-request')
        async def on_status_request(data):
            await self.report_status()

        try:
            await self.sio.connect(self.server_url)
        except Exception as e:
            logger.error(f"Failed to connect to server: {e}")

    async def report_status(self):
        """Report full hardware status to server"""
        status = {
            'motor': self.motor.get_status(),
            'camera': self.camera.get_status(),
            'platform': 'Raspberry Pi 5',
            'gpio_available': True,
            'line_follower': self.line_follower.is_ready,
        }
        if self.sio and self.sio.connected:
            await self.sio.emit('hardware-status', status)
        logger.info(f"Hardware status: {json.dumps(status)}")

    async def start_http_server(self):
        """HTTP API for MJPEG camera stream + hardware control"""
        if not AIOHTTP_AVAILABLE:
            return

        app = web.Application()
        cors = aiohttp_cors.setup(app, defaults={
            "*": aiohttp_cors.ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
                allow_methods="*"
            )
        })

        async def handle_status(request):
            return web.json_response({
                'motor': self.motor.get_status(),
                'camera': self.camera.get_status()
            })

        async def handle_motor(request):
            data = await request.json()
            command = data.get('command', 'stop')
            speed = data.get('speed', 50)
            getattr(self.motor, command, self.motor.stop)(speed) if command != 'stop' else self.motor.stop()
            return web.json_response(self.motor.get_status())

        # Register MJPEG camera routes (/stream, /snapshot, /camera/status)
        self.camera.add_routes(app, cors)

        resource_status = cors.add(app.router.add_resource('/hardware/status'))
        cors.add(resource_status.add_route('GET', handle_status))
        resource_motor = cors.add(app.router.add_resource('/hardware/motor'))
        cors.add(resource_motor.add_route('POST', handle_motor))

        # ── Dev / debug endpoints for line-following ──

        async def handle_lane_debug_stream(request):
            """MJPEG stream with lane-detection overlay for dev page."""
            response = web.StreamResponse(
                status=200,
                headers={
                    'Content-Type': 'multipart/x-mixed-replace; boundary=frame',
                    'Cache-Control': 'no-cache',
                    'Access-Control-Allow-Origin': '*',
                },
            )
            await response.prepare(request)
            boundary = b'--frame'
            try:
                while True:
                    frame_bytes = await self.camera.get_latest_frame()
                    if frame_bytes and self.line_follower.is_ready:
                        debug = self.line_follower.analyse_frame_debug(frame_bytes)
                        jpeg = debug.get('mask_jpeg') or frame_bytes
                    else:
                        jpeg = frame_bytes
                    if jpeg:
                        await response.write(
                            boundary + b'\r\n'
                            b'Content-Type: image/jpeg\r\n'
                            b'Content-Length: ' + str(len(jpeg)).encode() + b'\r\n'
                            b'\r\n' + jpeg + b'\r\n'
                        )
                    await asyncio.sleep(0.15)
            except (ConnectionResetError, asyncio.CancelledError):
                pass
            return response

        async def handle_lane_debug_json(request):
            """Single-shot JSON with lane correction + debug data."""
            frame_bytes = await self.camera.get_latest_frame()
            if not frame_bytes or not self.line_follower.is_ready:
                return web.json_response({'ready': False, 'correction': 0.0})
            debug = self.line_follower.analyse_frame_debug(frame_bytes)
            return web.json_response({
                'ready': True,
                'correction': debug['correction'],
                'raw_correction': debug.get('raw_correction', 0.0),
                'lane_cx': debug.get('lane_cx'),
                'frame_cx': debug.get('frame_cx'),
                'left_edge': debug.get('left_edge'),
                'right_edge': debug.get('right_edge'),
                'gap_center': debug.get('gap_center'),
                'lane_width': debug.get('lane_width'),
                'confidence': debug.get('confidence', 0.0),
                'borders_found': debug.get('borders_found', 0),
                'ema_lane_width': debug.get('ema_lane_width'),
                'roi_top': debug.get('roi_top'),
            })

        r_lane_stream = cors.add(app.router.add_resource('/lane/stream'))
        cors.add(r_lane_stream.add_route('GET', handle_lane_debug_stream))
        r_lane_json = cors.add(app.router.add_resource('/lane/debug'))
        cors.add(r_lane_json.add_route('GET', handle_lane_debug_json))

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', 8765)
        await site.start()
        logger.info("Hardware HTTP API running on :8765")

    async def run(self):
        """Main run loop"""
        self.running = True
        logger.info("Hardware Daemon starting...")

        # Connect to backend server
        await self.connect_to_server()

        # Start HTTP API (backup)
        await self.start_http_server()

        logger.info("Hardware Daemon running. Press Ctrl+C to stop.")

        # Keep running
        try:
            while self.running:
                await asyncio.sleep(1)
                # Periodic status report
        except asyncio.CancelledError:
            pass

    async def shutdown(self):
        """Graceful shutdown"""
        logger.info("Shutting down Hardware Daemon...")
        self.running = False
        self.motor.cleanup()
        await self.camera.cleanup()
        if self.sio and self.sio.connected:
            await self.sio.disconnect()
        logger.info("Hardware Daemon stopped.")


async def main():
    daemon = HardwareDaemon()

    # Handle signals
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(daemon.shutdown()))

    await daemon.run()


if __name__ == '__main__':
    print("""
╔══════════════════════════════════════════╗
║   🤖 Pi Delivery Bot - Hardware Daemon   ║
║   Platform: Raspberry Pi 5               ║
║   Camera: IMX219 (MJPEG HTTP)            ║
║   Motor: 2x Servo (lgpio PWM GPIO12,13)  ║
╚══════════════════════════════════════════╝
    """)
    asyncio.run(main())
