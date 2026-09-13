# Windows endpoint observability

The endpoint dashboard is opt-in. The base observability profile mounts an empty
file-discovery target, so Prometheus does not create a failing endpoint target on
hosts where `windows_exporter` has not been installed.

## Prerequisites

Install a reviewed, pinned `windows_exporter` MSI on the Windows host and bind it
so only the local host/Docker network can reach port 9182. The default collectors
used here are `cpu`, `logical_disk`, `memory`, `net`, `os`, `service`, and
`system`. The optional `update` collector is deliberately excluded initially
because it queries the Windows Update API and should be enabled only after its
runtime impact is measured.

Do not place event text, usernames, executable paths, tokens, or tenant data in
Prometheus labels. Use Genie MCP event-log queries for incident detail. Add a
dedicated log backend later only if searchable historical event bodies are an
approved requirement.

## Enable

After validating the exporter locally at `http://127.0.0.1:9182/metrics`, include
the endpoint overlay after the base observability overlay:

```powershell
docker compose -f docker-compose.yml -f docker-compose.observability.yml -f docker-compose.endpoint-observability.yml --profile observability config --quiet
docker compose -f docker-compose.yml -f docker-compose.observability.yml -f docker-compose.endpoint-observability.yml --profile observability up -d prometheus grafana alertmanager
```

Confirm `windows` is UP in Prometheus before relying on the provisioned
`Windows Endpoint Health` dashboard. The overlay introduces no public dashboard
binding; Grafana continues to use `OBSERVABILITY_BIND_ADDR`, which defaults to
`127.0.0.1`.

## Rollback

Redeploy without `docker-compose.endpoint-observability.yml`. Prometheus returns
to the empty discovery file, so endpoint alerts stop evaluating. This does not
uninstall the Windows service or delete any metrics volume.
