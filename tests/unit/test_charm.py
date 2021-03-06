import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import charm
from ops.model import Unit
from ops.testing import Harness


class TestCharm(unittest.TestCase):
    def assert_active_unit(self, unit: Unit):
        self.assertEqual(unit.status.name, "active")
        self.assertEqual(unit.status.message, "Unit is ready")


class TestInitCharm(TestCharm):
    def test_init(self):
        """Test initialization of charm."""
        harness = Harness(charm.PrometheusOvnExporterOperatorCharm)
        harness.begin()

        self.assert_active_unit(harness.charm.unit)


class TestCharmHooks(TestCharm):
    def patch(self, obj, method):
        """Mock the method."""
        _patch = mock.patch.object(obj, method)
        mock_method = _patch.start()
        self.addCleanup(_patch.stop)

        return mock_method

    def setUp(self):
        self.harness = Harness(charm.PrometheusOvnExporterOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        # mock hostname
        mock_socket = self.patch(charm, "socket")
        self.hostname = mock_socket.gethostname.return_value = "test-hostname"
        # mock os.stat
        mock_os = self.patch(charm, "os")
        mock_os.path.getsize.return_value = 1
        # mock subprocess
        self.mock_subprocess = self.patch(charm, "subprocess")
        # mock getting private address
        mock_get_binding = self.patch(self.harness.model, "get_binding")
        mock_get_binding.return_value = self.mock_binding = mock.MagicMock()
        self.mock_binding.network.bind_address = "127.0.0.1"
        # mock fetch resource
        self.mock_fetch = self.patch(self.harness.model.resources, "fetch")
        self.mock_fetch.return_value = "prometheus-ovn-exporter.snap"
        # required relations
        self.ovn_relation_id = self._add_relation("juju-info", "ovn-central")

    def _add_relation(self, relation_name: str, remote_app: str) -> int:
        """Help function to add relation and trigger <relation_name>_joined hook."""
        relation_id = self.harness.add_relation(relation_name, remote_app)
        self.harness.add_relation_unit(relation_id, f"{remote_app}/0")

        return relation_id

    def test_manage_prometheus_ovn_exporter_service(self):
        """Test manage the prometheus-ovn-exporter snap."""
        self.harness.charm._manage_prometheus_ovn_exporter_service()

        self.mock_subprocess.check_call.assert_has_calls(
            [
                mock.call(
                    [
                        "snap",
                        "set",
                        "prometheus-ovn-exporter",
                        "web.listen-address=127.0.0.1:9476",
                    ]
                ),
                mock.call(
                    ["snap", "connect", "prometheus-ovn-exporter:kernel-module-observe"]
                ),
                mock.call(["snap", "connect", "prometheus-ovn-exporter:log-observe"]),
                mock.call(
                    ["snap", "connect", "prometheus-ovn-exporter:network-observe"]
                ),
                mock.call(["snap", "connect", "prometheus-ovn-exporter:openvswitch"]),
                mock.call(["snap", "connect", "prometheus-ovn-exporter:system-files"]),
                mock.call(
                    ["snap", "connect", "prometheus-ovn-exporter:system-observe"]
                ),
                mock.call(["snap", "connect", "prometheus-ovn-exporter:netlink-audit"]),
                mock.call(["snap", "restart", "prometheus-ovn-exporter"]),
            ]
        )

    def test_render_grafana_dashboard(self):
        """Test render the Grafana dashboard template."""
        _ = self._add_relation("ovn-exporter", "prometheus2")
        template_dir = "templates"
        template_name = "ovn-grafana-dashboard.json.j2"
        test_template = {
            "datasource": "<< datasource >>",
            "machine_name": "<< machine_name >>",
            "app_name": "<< app_name >>",
            "parent_app_name": "<< parent_app_name >>",
            "prometheus_app_name": "<< prometheus_app_name >>",
        }
        with TemporaryDirectory() as tmp_dir:
            tmp_dir = Path(tmp_dir)
            tmp_templates = tmp_dir / template_dir
            tmp_templates.mkdir()
            with open(tmp_templates / template_name, mode="w") as file:
                json.dump(test_template, file)

            self.harness.charm.framework.charm_dir = tmp_dir
            dashboard = self.harness.charm._render_grafana_dashboard()

        self.assertDictEqual(
            json.loads(dashboard),
            {
                "datasource": "prometheus2 - Juju generated source",
                "machine_name": self.hostname,
                "app_name": "prometheus-ovn-exporter",
                "parent_app_name": "ovn-central",
                "prometheus_app_name": "prometheus2",
            },
        )

    def test_private_address(self):
        """Test help function to get private address."""
        address = self.harness.charm.private_address
        self.assertEqual("127.0.0.1", address)

    def test_on_install(self):
        """Test install hook."""
        exp_call = mock.call(
            ["snap", "install", "--dangerous", "prometheus-ovn-exporter.snap"]
        )
        self.harness.charm.on.install.emit()

        self.mock_fetch.assert_called_once_with("prometheus-ovn-exporter")
        self.assertIn(exp_call, self.mock_subprocess.check_call.mock_calls)
        self.assert_active_unit(self.harness.charm.unit)

    def test_on_config_changed(self):
        """Test config-changed hook."""
        # this will trigger self.harness.charm.on.config_changed.emit()
        self.harness.update_config({"exporter-listen-port": "9120"})

        self.assertEqual(self.harness.charm._stored.listen_port, "9120")
        self.mock_subprocess.check_call.assert_has_calls(
            [
                mock.call(
                    [
                        "snap",
                        "set",
                        "prometheus-ovn-exporter",
                        "web.listen-address=127.0.0.1:9120",
                    ]
                ),
                mock.call(
                    ["snap", "connect", "prometheus-ovn-exporter:kernel-module-observe"]
                ),
                mock.call(["snap", "connect", "prometheus-ovn-exporter:log-observe"]),
                mock.call(
                    ["snap", "connect", "prometheus-ovn-exporter:network-observe"]
                ),
                mock.call(["snap", "connect", "prometheus-ovn-exporter:openvswitch"]),
                mock.call(["snap", "connect", "prometheus-ovn-exporter:system-files"]),
                mock.call(
                    ["snap", "connect", "prometheus-ovn-exporter:system-observe"]
                ),
                mock.call(["snap", "connect", "prometheus-ovn-exporter:netlink-audit"]),
                mock.call(["snap", "restart", "prometheus-ovn-exporter"]),
            ]
        )
        self.assert_active_unit(self.harness.charm.unit)

    def test_on_config_changed_with_ovn_exporter_relation(self):
        """Test config-changed hook with existing ovn-exporter relation."""
        relation_id = self._add_relation("ovn-exporter", "prometheus2")
        self.harness.update_config({"exporter-listen-port": "9500"})

        relation_data = self.harness.get_relation_data(
            relation_id, self.harness.charm.unit.name
        )
        self.assertDictEqual(relation_data, {"hostname": "127.0.0.1", "port": "9500"})
        self.assert_active_unit(self.harness.charm.unit)

    def test_on_ovn_exporter_relation_changed(self):
        """Test Prometheus relation changed hook."""
        relation_id = self._add_relation("ovn-exporter", "prometheus2")
        # update relation -> trigger ovn_exporter_relation_changed hook
        self.harness.update_relation_data(relation_id, "prometheus2/0", {})

        relation_data = self.harness.get_relation_data(
            relation_id, self.harness.charm.unit.name
        )
        self.assertDictEqual(relation_data, {"hostname": "127.0.0.1", "port": "9476"})
        self.assert_active_unit(self.harness.charm.unit)

    def test_on_grafana_relation_joined(self):
        """Test Grafana relation joined hook."""
        mock_render_grafana_dashboard = self.patch(
            self.harness.charm, "_render_grafana_dashboard"
        )
        mock_render_grafana_dashboard.return_value = "test-dashboard"
        self.harness.set_leader(True)
        _ = self._add_relation("ovn-exporter", "prometheus2")
        # this will trigger the grafana_joined hook
        relation_id = self._add_relation("grafana", "grafana")

        mock_render_grafana_dashboard.assert_called_once()
        relation_data = self.harness.get_relation_data(
            relation_id, self.harness.charm.unit.name
        )
        self.assertDictEqual(relation_data, {"dashboard": "test-dashboard"})
        self.assert_active_unit(self.harness.charm.unit)

    def test_on_grafana_relation_joined_no_leader(self):
        """Test Grafana relation joined hook on no leader unit."""
        self.harness.set_leader(False)
        relation_id = self._add_relation("grafana", "grafana")

        relation_data = self.harness.get_relation_data(
            relation_id, self.harness.charm.unit.name
        )
        self.assertDictEqual(relation_data, {})
        self.assert_active_unit(self.harness.charm.unit)

    def test_on_grafana_relation_joined_missing_relation(self):
        """Test Grafana relation joined hook without ovn-exporter relation."""
        self.harness.set_leader(True)

        # test without bind-exporter relation
        relation_id = self._add_relation("grafana", "grafana")

        relation_data = self.harness.get_relation_data(
            relation_id, self.harness.charm.unit.name
        )
        self.assertDictEqual(relation_data, {})
