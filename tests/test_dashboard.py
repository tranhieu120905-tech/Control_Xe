import queue
import time
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dieukhienxe import ArduinoLink, CarDashboard, count_fingers, parse_args, parse_distance


class ProtocolTests(unittest.TestCase):
    def test_distance_accepts_labelled_packets_only(self):
        for line in ("DIST:25.4", "distance=25.4 cm\r", "D:25.4"):
            self.assertEqual(parse_distance(line), 25.4)
        for line in ("1", "5", "-10", "DIST:-1", "DIST:nan", "DIST:10001", "debug:25"):
            self.assertIsNone(parse_distance(line))

    def test_finger_count_supports_both_mirrored_hands(self):
        points = [SimpleNamespace(x=0.5, y=0.5) for _ in range(21)]
        for tip in (8, 12, 16, 20):
            points[tip].y = 0.1
        points[4].x = 0.1
        self.assertEqual(count_fingers(points, "Right"), 5)
        self.assertEqual(count_fingers(points, "Left"), 4)
        points[4].x = 0.9
        self.assertEqual(count_fingers(points, "Left"), 5)

    def test_stop_replaces_pending_motion_without_speed_overwriting_it(self):
        link = ArduinoLink(queue.Queue())
        link.send("1")
        link.send("2")
        link.send("0")
        link.send_speed("V128\n")
        self.assertEqual(link.outgoing.get_nowait(), b"0")
        self.assertTrue(link.outgoing.empty())
        self.assertEqual(link.speed_outgoing.get_nowait(), b"V128\n")

    def test_disconnect_clears_queued_motion(self):
        link = ArduinoLink(queue.Queue())
        link.send("1")
        link.send_speed("V255\n")
        link.disconnect()
        self.assertTrue(link.halt.is_set())
        self.assertTrue(link.outgoing.empty())
        self.assertTrue(link.speed_outgoing.empty())

    def test_speed_template_is_explicit_and_decodes_newline(self):
        self.assertIsNone(parse_args([]).speed_template)
        args = parse_args(["--speed-template", r"V{value}\n", "--stop-command", "4"])
        self.assertEqual(args.speed_template.format(value=128), "V128\n")
        self.assertEqual(args.stop_command, "4")

    def test_serial_worker_initializes_stopped_parses_telemetry_and_stops_on_exit(self):
        events = queue.Queue()
        link = ArduinoLink(events)
        link.halt.wait = Mock(return_value=False)
        device = Mock()
        device.in_waiting = 20
        def read(_):
            device.in_waiting = 0
            link.halt.set()
            return b"DIST:25.4\n1\n"
        device.read.side_effect = read
        link.send("1")
        with patch.dict("sys.modules", {"serial": SimpleNamespace(Serial=Mock(return_value=device))}):
            link._run("COM_TEST", 9600)
        self.assertEqual([call.args[0] for call in device.write.call_args_list], [b"0", b"1", b"0"])
        received = []
        while not events.empty():
            received.append(events.get_nowait())
        self.assertEqual(received, [("connected", "COM_TEST"), ("distance", 25.4), ("disconnected", None)])
        device.close.assert_called_once()


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.app = CarDashboard(cls.root, parse_args([]))
        cls.root.update()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.close()

    def setUp(self):
        self.app.connected = True
        self.app.connecting = False
        self.app.ai.set(False)
        self.app.last_command = "0"
        self.app.manual_key = None
        self.app.candidate = None
        self.app.link.send = Mock()
        self.app.link.send_speed = Mock()
        self.app.link.disconnect = Mock()
        self.app.camera_running = False
        self.app.ai_available = False

    def test_manual_release_sends_stop_and_avoids_repeated_commands(self):
        self.app.drive("1")
        self.app.drive("1")
        self.app.stop()
        self.assertEqual([call.args[0] for call in self.app.link.send.call_args_list], ["1", "0"])

    def test_layout_keeps_all_controls_visible_at_minimum_size(self):
        self.root.geometry("1080x660")
        self.root.deiconify()
        self.root.update()
        pending = list(self.root.winfo_children())
        try:
            while pending:
                widget = pending.pop()
                children = widget.winfo_children()
                pending.extend(children)
                if not children:
                    self.assertTrue(widget.winfo_ismapped(), str(widget))
                    parent = widget.master
                    self.assertLessEqual(widget.winfo_y()+widget.winfo_height(), parent.winfo_height(), str(widget))
                    self.assertLessEqual(widget.winfo_x()+widget.winfo_width(), parent.winfo_width(), str(widget))
        finally:
            self.root.withdraw()

    def test_disconnected_cannot_drive(self):
        self.app.connected = False
        self.app.drive("1")
        self.app.link.send.assert_not_called()
        self.assertEqual(self.app.last_command, "0")

    def test_manual_does_not_override_ai(self):
        self.app.ai.set(True)
        self.app.drive("2")
        self.app.link.send.assert_not_called()

    def test_ai_requires_camera_detector_and_serial(self):
        self.app.ai.set(True)
        self.app.change_mode()
        self.assertFalse(self.app.ai.get())

    def test_ai_debounce_and_lost_hand_stop(self):
        self.app.ai.set(True)
        with patch("dieukhienxe.time.monotonic", side_effect=[10, 10.1, 10.3, 10.4]):
            self.app._gesture(1)
            self.app._gesture(1)
            self.app.link.send.assert_not_called()
            self.app._gesture(1)
            self.app.link.send.assert_called_once_with("1")
            self.app._gesture(None)
        self.assertEqual(self.app.link.send.call_args.args, ("0",))

    def test_emergency_stop_disarms_ai(self):
        self.app.ai.set(True)
        self.app.last_command = "5"
        self.app.stop(disarm=True)
        self.app.link.send.assert_called_once_with("0")
        self.assertFalse(self.app.ai.get())

    def test_camera_timeout_disarms_ai(self):
        self.app.ai.set(True)
        self.app.last_command = "1"
        self.app.frame_time = time.monotonic()-2
        self.app._poll()
        self.app.link.send.assert_called_once_with("0")
        self.assertFalse(self.app.ai.get())

    def test_disconnect_requests_stop_before_closing_link(self):
        calls = []
        self.app.last_command = "1"
        self.app.link.send.side_effect = lambda command: calls.append(command)
        self.app.link.disconnect.side_effect = lambda: calls.append("disconnect")
        self.app.disconnect()
        self.assertEqual(calls, ["0", "disconnect"])
        self.assertFalse(self.app.connected)

    def test_expired_distance_is_not_displayed_as_live(self):
        self.app.distance = 23
        self.app.distance_time = time.monotonic()-4
        self.app._poll()
        self.assertIsNone(self.app.distance)
        self.assertIn("--", self.app.distance_label.cget("text"))


if __name__ == "__main__":
    unittest.main()
