#!/usr/bin/env python3
# See LICENSE file for licensing details.

"""Charm to deploy prometheus-ovn-exporter."""
import logging
import os
import socket
import subprocess
from ipaddress import IPv4Address

import jinja2
from ops.charm import (CharmBase, RelationChangedEvent, RelationDepartedEvent,
                       RelationJoinedEvent)
from ops.framework import StoredState
from ops.main import main
from ops.model import (ActiveStatus, BlockedStatus, MaintenanceStatus,
                       ModelError)

logger = logging.getLogger(__name__)

DEFAULT_LISTEN_PORT = 9476


class PrometheusOvnExporterOperatorCharm(CharmBase):
    """Charm the service."""

    _stored = StoredState()

    def __init__(self, *args):
        super().__init__(*args)
        # hooks
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.upgrade_charm, self._on_install)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        # relation hooks
        self.framework.observe(
            self.on.ovn_exporter_relation_joined, self._on_ovn_exporter_relation_changed
        )
        self.framework.observe(
            self.on.ovn_exporter_relation_changed,
            self._on_ovn_exporter_relation_changed,
        )
        self.framework.observe(
            self.on.ovn_exporter_relation_departed,
            self._on_prometheus_relation_departed,
        )
        self.framework.observe(
            self.on.grafana_relation_joined, self._on_grafana_relation_joined
        )
        self.framework.observe(
            self.on.grafana_relation_changed, self._on_grafana_relation_joined
        )
        # initialise stored data
        self._stored.set_default(
            listen_port=self.config.get("exporter-listen-port"),
            snap_channel=self.config.get("snap-channel"),
        )
        self.config_file = "/var/snap/prometheus-ovn-exporter/current/daemon_arguments"
        self.unit.status = ActiveStatus("Unit is ready")

    def _manage_prometheus_ovn_exporter_service(self):
        """Manage the prometheus-ovn-exporter service."""
        logger.debug(
            "prometheus-ovn-exporter configuration in progress"
        )
        cmd = [
            "snap",
            "set",
            "prometheus-ovn-exporter",
            f"web.listen-address={self.private_address or ''}:"
            f"{self._stored.listen_port}",
        ]
        try:
            subprocess.check_call(cmd)
        except subprocess.CalledProcessError as e:
            logger.error(f"snap set failed with {e}")

        plugs = [
            "prometheus-ovn-exporter:kernel-module-observe",
            "prometheus-ovn-exporter:log-observe",
            "prometheus-ovn-exporter:network-observe",
            "prometheus-ovn-exporter:openvswitch",
            "prometheus-ovn-exporter:system-files",
            "prometheus-ovn-exporter:system-observe",
            "prometheus-ovn-exporter:netlink-audit",
        ]

        for plug in plugs:
            cmd = ["snap", "connect", plug]
            try:
                subprocess.check_call(cmd)
            except subprocess.CalledProcessError as e:
                logger.error(f"snap connect {plug} failed with {e}")
        cmd = ["snap", "restart", "prometheus-ovn-exporter"]
        try:
            subprocess.check_call(cmd)
        except subprocess.CalledProcessError as e:
            logger.error(f"snap restart failed with {e}")
        logger.info("prometheus-ovn-exporter has been reconfigured")

    def _render_grafana_dashboard(self) -> str:
        """Render jinja2 template for Grafana dashboard."""
        # NOTE (rgildein): After resolving the following bug [1], this function should
        # be replaced with a built-in one in Operator Framework.
        # [1]: https://github.com/canonical/operator/issues/228
        parent_app_name = self.model.get_relation("juju-info").app.name
        prometheus_app_name = self.model.get_relation("ovn-exporter").app.name

        context = {
            "datasource": f"{prometheus_app_name} - Juju generated source",
            "machine_name": socket.gethostname(),
            "app_name": self.app.name,
            "parent_app_name": parent_app_name,
            "prometheus_app_name": prometheus_app_name,
        }
        templates = jinja2.Environment(
            loader=jinja2.FileSystemLoader(self.charm_dir / "templates"),
            variable_start_string="<< ",
            variable_end_string=" >>",
        )
        template = templates.get_template("ovn-grafana-dashboard.json.j2")

        return template.render(context)

    @property
    def private_address(self) -> str:
        """Return the private address of unit."""
        # TODO check if we want private or public address, binding?
        address: IPv4Address = self.model.get_binding("juju-info").network.bind_address

        return str(address)

    def _on_install(self, _):
        """Installation hook that installs prometheus-ovn-exporter daemon."""
        self.unit.status = MaintenanceStatus("Installing prometheus-ovn-exporter")
        install_from_resource = True
        try:
            snap_file = self.model.resources.fetch("prometheus-ovn-exporter")
        except ModelError:
            # Snap resource not found
            install_from_resource = False

        if install_from_resource and os.path.getsize(snap_file) > 0:
            subprocess.check_call(["snap", "install", "--dangerous", snap_file])
        else:
            snap_file = "prometheus-ovn-exporter"
            # TODO handle refresh or install (changed channel)
            subprocess.check_call(
                [
                    "snap",
                    "install",
                    f"--channel={self._stored.snap_channel}",
                    snap_file,
                ]
            )
        # manage plugs

        self._manage_prometheus_ovn_exporter_service()
        self.unit.status = ActiveStatus("Unit is ready")

    def _on_config_changed(self, _):
        """Config change hook."""
        self.unit.status = MaintenanceStatus("prometheus-ovn-exporter configuration")
        self._stored.listen_port = self.config.get("exporter-listen-port")

        if self.config.get("snap-channel") != self._stored.snap_channel:
            # snap channel changed
            self._stored.snap_channel = self.config.get("snap-channel")
            self._on_install(_)
        self._manage_prometheus_ovn_exporter_service()

        # update relation data
        ovn_exporter_relation = self.model.get_relation("ovn-exporter")

        if ovn_exporter_relation:
            logger.info("Updating `ovn-exporter` relation data.")
            ovn_exporter_relation.data[self.unit].update(
                {
                    "hostname": self.private_address,
                    "port": str(self._stored.listen_port),
                }
            )

        self.unit.status = ActiveStatus("Unit is ready")

    def _on_ovn_exporter_relation_changed(self, event: RelationChangedEvent):
        """Prometheus relation changed hook.

        This hook will ensure the creation of a new target in Prometheus.
        """

        logger.info("Shared relation data with %s", self.unit.name)
        event.relation.data[self.unit].update(
            {"hostname": self.private_address, "port": str(self._stored.listen_port)}
        )

    def _on_prometheus_relation_departed(self, event: RelationDepartedEvent):
        """Prometheus relation departed hook.

        This hook will ensure the deletion of the target in Prometheus.
        """
        logger.info("Removing %s target from Prometheus." % self.unit)
        event.relation.data[self.unit].clear()

    def _on_grafana_relation_joined(self, event: RelationJoinedEvent):
        """Grafana relation joined hook.

        This hook will ensure the creation of a new dashboard in Grafana.
        """

        if not self.unit.is_leader():
            logger.debug(
                "Grafana relation must be run on the leader unit. Skipping Grafana "
                "configuration."
            )

            return

        if self.model.get_relation("ovn-exporter") is None:
            logger.warning(
                "ovn-exporter relation not available. Skipping Grafana configuration."
            )
            self.unit.status = BlockedStatus("ovn-exporter relation not available.")

            return

        event.relation.data[self.unit].update(
            {"dashboard": self._render_grafana_dashboard()}
        )


if __name__ == "__main__":
    main(PrometheusOvnExporterOperatorCharm)
