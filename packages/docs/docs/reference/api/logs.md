---
title: Logs API
description: Stream and retrieve merged service logs from Cortex, Neuron, nginx, and Valkey in the all-in-one Docker deployment via the /api/v1/logs endpoint.
---

# Logs API

Access container service logs and service status. Available in the all-in-one Docker deployment where supervisord manages services.

**Base path:** `/api/v1/logs`

---

## Get All Logs

```
GET /api/v1/logs
```

Returns interleaved logs from all managed services (Cortex, Neuron, Nginx, Valkey), sorted by timestamp.

#### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `lines` | integer | No | Server default | Number of log lines to return (1-10000) |

#### Response

**Status:** `200 OK`

```json
{
  "service": null,
  "lines": [
    "2026-04-13 14:25:30 [cortex] INFO: Application startup complete",
    "2026-04-13 14:25:31 [neuron] INFO: Worker started, polling queues",
    "2026-04-13 14:25:31 [nginx] 127.0.0.1 - GET /api/v1/health 200"
  ],
  "total_lines": 3
}
```

#### curl Example

```bash
# Get recent logs from all services
curl -s http://localhost:8080/api/v1/logs

# Get last 100 lines
curl -s "http://localhost:8080/api/v1/logs?lines=100"
```

---

## Get Service Logs

```
GET /api/v1/logs/{service_name}
```

Returns logs for a specific service.

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `service_name` | string | Yes | Service name: `cortex`, `neuron`, `nginx`, or `valkey` |

#### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `lines` | integer | No | Server default | Number of log lines to return (1-10000) |

#### Response

**Status:** `200 OK`

```json
{
  "service": "cortex",
  "lines": [
    "2026-04-13 14:25:30 INFO: Application startup complete",
    "2026-04-13 14:25:35 INFO: Processing request GET /api/v1/health"
  ],
  "total_lines": 2
}
```

#### curl Example

```bash
curl -s http://localhost:8080/api/v1/logs/cortex
curl -s "http://localhost:8080/api/v1/logs/neuron?lines=50"
```

---

## Get Service Status

```
GET /api/v1/logs/status
```

Returns the status of all managed services including PID, uptime, and state.

#### Response

**Status:** `200 OK`

```json
{
  "available": true,
  "services": [
    {
      "name": "cortex",
      "state": "RUNNING",
      "pid": 42,
      "uptime_seconds": 3600,
      "start_time": "2026-04-13T13:25:30.000000",
      "description": ""
    },
    {
      "name": "neuron",
      "state": "RUNNING",
      "pid": 43,
      "uptime_seconds": 3600,
      "start_time": "2026-04-13T13:25:31.000000",
      "description": ""
    }
  ]
}
```

When supervisord is not reachable (e.g., multi-container deployment):

```json
{
  "available": false,
  "services": []
}
```

#### curl Example

```bash
curl -s http://localhost:8080/api/v1/logs/status
```

---

## Response Models Reference

### LogResponse

| Field | Type | Description |
|-------|------|-------------|
| `service` | string or null | Service name filter, `null` for all services |
| `lines` | string[] | Log lines |
| `total_lines` | integer | Total number of lines returned |

### ServiceStatusResponse

| Field | Type | Description |
|-------|------|-------------|
| `available` | boolean | Whether supervisord is reachable |
| `services` | ServiceStatus[] | List of managed services |

### ServiceStatus

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Service name |
| `state` | string | Current state (e.g., `RUNNING`, `STOPPED`, `FATAL`) |
| `pid` | integer or null | Process ID |
| `uptime_seconds` | integer or null | Seconds since last start |
| `start_time` | string or null | Last start timestamp |
| `description` | string | Additional description |
