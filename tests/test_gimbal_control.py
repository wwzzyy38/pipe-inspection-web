"""Hardware-free request and ROS round-trip tests for the gimbal control."""
import http.client
import json
import os
from pathlib import Path
import tempfile
from threading import Thread
import time
import unittest

from live import Live
from server import InspectionHTTP
from web_auth import PasswordGate, save_password


@unittest.skipUnless(os.environ.get('ROS_LOCALHOST_ONLY') == '1' and os.environ.get('ROS_DOMAIN_ID') == '89',
                     'run only on the isolated loopback test domain 89')
class GimbalControlTest(unittest.TestCase):
    def test_authenticated_command_reaches_mock_ros_controller(self):
        import rclpy
        from std_msgs.msg import Bool, Float64

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        password_file = root / 'password.json'
        save_password(password_file, b'synthetic-gimbal-test-password')
        gate = PasswordGate(password_file)
        token = gate.login('127.0.0.1', b'synthetic-gimbal-test-password')[1]
        live = Live(root)
        self.addCleanup(live.close)
        server = InspectionHTTP(('127.0.0.1', 0), None, gate, None)
        self.addCleanup(server.server_close)
        server.live = live
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        h = {'Content-Type': 'application/json', 'X-Inspection-Action': '1',
             'Origin': f'http://127.0.0.1:{server.server_port}',
             'Cookie': gate.cookie(token).split(';', 1)[0]}

        def post(body, headers=h):
            c = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            try:
                c.request('POST', '/api/control/gimbal/pitch', body, headers=headers)
                r = c.getresponse()
                return r.status, json.loads(r.read())
            finally:
                c.close()

        self.assertEqual(post('{"angle":5}', {k: v for k, v in h.items() if k != 'Cookie'})[0], 401)
        self.assertEqual(post('{"angle":5}', {**h, 'Origin': 'https://other.invalid'})[0], 403)
        for body in ('{}', '{"angle":true}', '{"angle":26}', '{"angle":-91}', '{"angle":NaN}', '{"angle":"5"}'):
            self.assertEqual(post(body)[0], 400, body)
        self.assertEqual(post('{"angle":5}'), (409, {'ok': False, 'detail': 'no_controller'}))

        rclpy.init()
        self.addCleanup(rclpy.shutdown)
        peer = rclpy.create_node('mock_gimbal_controller')
        self.addCleanup(peer.destroy_node)
        angles = []
        command = peer.create_subscription(Float64, '/siyi/gimbal/set_pitch_deg', lambda msg: angles.append(msg.data), 10)
        pitch = peer.create_publisher(Float64, '/siyi/gimbal/pitch_deg', 10)
        connection = peer.create_publisher(Bool, '/siyi/gimbal/connected', 10)
        end = time.monotonic() + 6
        while time.monotonic() < end:
            connection.publish(Bool(data=True))
            pitch.publish(Float64(data=2.5))
            rclpy.spin_once(peer, timeout_sec=.05)
            with live.lock:
                if live.gimbal_status()['ready']:
                    break
        with live.lock:
            self.assertEqual(live.gimbal_status()['pitch'], 2.5)
        self.assertEqual(angles, [])
        self.assertEqual(post('{"angle":-10.2}'), (200, {'ok': True, 'detail': 'sent'}))
        self.assertEqual(post('{"angle":8}')[0], 429)
        end = time.monotonic() + 3
        while not angles and time.monotonic() < end:
            rclpy.spin_once(peer, timeout_sec=.1)
        self.assertEqual(angles, [-10.2])
        with live.lock:
            self.assertEqual(live.gimbal_status()['pitch'], 2.5)
        connection.publish(Bool(data=False))
        end = time.monotonic() + 3
        while time.monotonic() < end:
            with live.lock:
                if not live.gimbal_status()['ready']:
                    break
            rclpy.spin_once(peer, timeout_sec=.05)
        self.assertEqual(post('{"angle":5}'), (409, {'ok': False, 'detail': 'no_feedback'}))
        self.assertEqual(angles, [-10.2])
        peer.destroy_subscription(command)


if __name__ == '__main__':
    unittest.main()
