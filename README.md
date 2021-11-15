# charm-prometheus-ovn-exporter

## Description

Subordinate charm to deploy a Prometheus stats exporter for OVN.

The charm provides relations to Prometheus to setup a scrape target, and to
Grafana to supply a dashboard.

## Usage

Deploy this charm as a subordinate of any OVN based principal charm, including
but not limited to:

* ovn-central
* ovn-chassis

```
juju deploy prometheus-ovn-exporter
```

## Relations

Subordinate:

```
juju relate ovn-central:juju-info prometheus-ovn-exporter
juju relate nova-compute:juju-info prometheus-ovn-exporter
```

Prometheus and Grafana:

```
juju relate prometheus2:target prometheus-ovn-exporter:ovn-exporter
juju relate grafana:dashboards prometheus-ovn-exporter:grafana
```

## Snap

This charm needs to install the prometheus-ovn-exporter snap.  This is
available at https://snapcraft.io/prometheus-ovn-exporter or you can build it
yourself from the source at
https://code.launchpad.net/snap-prometheus-ovn-exporter.  To build the snap,
download the source, install snapcraft, and run `snapcraft`.

To attach the built snap to the charm as a resource:

```
juju attach-resource prometheus-ovn-exporter prometheus-ovn-exporter=the_snap_file
```

## Contributing

TODO
