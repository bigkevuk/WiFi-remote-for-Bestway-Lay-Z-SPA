# Soak Monitoring

## 1) Start telemetry stack

From repo root:

```bash
INFLUX_TOKEN='JyXDuLzMT3dwxxi0afT57A430QvUHGRHQvReKz7Mj1uILpvltowPxwtoblIAaWWP' \
MQTT_BASE_TOPIC='layzspa' \
docker compose up -d
```

Default web UIs:

- InfluxDB: http://localhost:8086
- Grafana: http://localhost:3000 (default `admin` / `admin`)

## 2) Run `/info/` poller

```bash
python3 tools/soak/http_info_poller.py \
  --url http://<device-ip>/info/ \
  --interval 15 \
  --csv tools/soak/http_info_samples.csv
```

Example with tighter alerts:

```bash
python3 tools/soak/http_info_poller.py \
  --url http://<device-ip>/info/ \
  --interval 10 \
  --downtrend-window 30 \
  --downtrend-min-drop 6144 \
  --max-block-drop-bytes 6144 \
  --max-block-drop-percent 0.15 \
  --min-free-heap 18000
```

## 3) Dashboard

Grafana auto-loads:

- `Lay-Z-Spa Soak Starter`

If your Influx bucket is not `spa`, edit the dashboard variable `bucket`.
