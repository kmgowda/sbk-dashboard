import contextlib
import io
import unittest

from sbk_dashboard.config import parse_configuration
from sbk_dashboard.main import dashboard_links, main, print_effective, print_runtime


class MainTest(unittest.TestCase):
    def test_help_prints_python_runtime_and_options(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
            main(["-h"])
        self.assertEqual(0, stopped.exception.code)
        self.assertIn("Python version:", output.getvalue())
        self.assertIn("-continue", output.getvalue())

    def test_invalid_configuration_exits_with_usage_error(self):
        error = io.StringIO()
        with contextlib.redirect_stderr(error), self.assertRaises(SystemExit) as stopped:
            main(["-auth", "true"])
        self.assertEqual(2, stopped.exception.code)
        self.assertIn("reserved for a future release", error.getvalue())

    def test_runtime_and_effective_configuration_output(self):
        configuration = parse_configuration(["-port", "19721"], {})
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            print_runtime([])
            print_effective(configuration, configuration.monitoring)
        text = output.getvalue()
        self.assertIn("Supplied arguments: (none)", text)
        self.assertIn("port=19721 [command line]", text)
        self.assertIn("retention-days=7 [default]", text)

    def test_dashboard_links_always_include_loopback(self):
        links = dashboard_links(9721)
        self.assertEqual("http://localhost:9721/", links[0])
        self.assertEqual("http://127.0.0.1:9721/", links[1])


if __name__ == "__main__":
    unittest.main()
