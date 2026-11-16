"""CDS client options and progress-callback tests (offline)."""

from __future__ import annotations

import os
import threading
import unittest
from contextlib import redirect_stderr
from io import StringIO
from unittest import mock

from prometeo.download.client import CDSClient, _resolve_progress


def _build_client(progress: bool | None) -> tuple[CDSClient, mock.Mock]:
    """Build a CDSClient with mocked credentials and cdsapi.Client."""
    with (
        mock.patch(
            "prometeo.download.client.load_credentials",
            return_value=("https://cds.example/api", "uid:key"),
        ),
        mock.patch("prometeo.download.client.cdsapi.Client") as client_cls,
    ):
        client = CDSClient(progress=progress)
    return client, client_cls


class ProgressOptionTests(unittest.TestCase):
    """Verify the options forwarded to cdsapi.Client."""

    def test_progress_true_enables_bar_and_callbacks(self) -> None:
        _client, client_cls = _build_client(progress=True)
        kwargs = client_cls.call_args.kwargs

        self.assertFalse(kwargs["quiet"])
        self.assertTrue(kwargs["progress"])
        self.assertTrue(callable(kwargs["info_callback"]))
        self.assertTrue(callable(kwargs["warning_callback"]))
        self.assertTrue(callable(kwargs["error_callback"]))
        self.assertTrue(callable(kwargs["debug_callback"]))

    def test_progress_false_keeps_cds_quiet(self) -> None:
        _client, client_cls = _build_client(progress=False)
        kwargs = client_cls.call_args.kwargs

        self.assertTrue(kwargs["quiet"])
        self.assertFalse(kwargs["progress"])
        self.assertNotIn("info_callback", kwargs)

    def test_environment_can_force_progress(self) -> None:
        with mock.patch("sys.stderr.isatty", return_value=False):
            with mock.patch.dict(os.environ, {"PROMETEO_PROGRESS": "1"}):
                self.assertTrue(_resolve_progress(None))
            with mock.patch.dict(os.environ, {"PROMETEO_PROGRESS": "0"}):
                self.assertFalse(_resolve_progress(None))

    def test_terminal_detection_is_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PROMETEO_PROGRESS", None)
            with mock.patch("sys.stderr.isatty", return_value=True):
                self.assertTrue(_resolve_progress(None))
            with mock.patch("sys.stderr.isatty", return_value=False):
                self.assertFalse(_resolve_progress(None))


class CallbackTests(unittest.TestCase):
    """Verify queue/download callbacks stop the wait ticker correctly."""

    def setUp(self) -> None:
        self.client, _ = _build_client(progress=True)
        self.client._download_started = threading.Event()

    def test_downloading_stops_the_queue_ticker(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr):
            self.client._on_cds_info(
                "Downloading %s to %s (%s)", "url", "target", "10 MB"
            )
        self.assertTrue(self.client._download_started.is_set())
        self.assertIn("CDS: Downloading", stderr.getvalue())

    def test_queued_message_does_not_stop_the_ticker(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr):
            self.client._on_cds_info("Request is queued")
        self.assertFalse(self.client._download_started.is_set())
        self.assertIn("CDS: Request is queued", stderr.getvalue())

    def test_message_formatting_falls_back_on_bad_arguments(self) -> None:
        text = self.client._format_cds_message("value=%s", (1, 2))
        self.assertIn("value=%s", text)

    def test_wait_line_is_overwritten_in_place(self) -> None:
        self.client._interactive = True
        stderr = StringIO()
        with redirect_stderr(stderr):
            self.client._write_wait(5)
            self.client._write_wait(10)
            self.client._write_status("CDS: done")

        text = stderr.getvalue()
        self.assertIn("\r", text)
        self.assertIn("esperando respuesta de CDS... 10 s", text)
        self.assertTrue(text.endswith("CDS: done\n"))

    def test_non_interactive_wait_leaves_one_message(self) -> None:
        self.client._interactive = False
        stderr = StringIO()
        with redirect_stderr(stderr):
            self.client._wait_ticker(threading.Event(), threading.Event())

        self.assertEqual(
            stderr.getvalue(), "CDS: esperando respuesta de CDS...\n"
        )


if __name__ == "__main__":
    unittest.main()
