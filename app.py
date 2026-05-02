import os
import random
import time
import logging
import json
from flask import Flask, jsonify, request, render_template_string
from prometheus_client import Counter, Histogram, generate_latest

app = Flask(__name__)

app.logger.setLevel(logging.INFO)

APP_VERSION = os.getenv("APP_VERSION", "0.1.0")
START_TIME = time.time()
READY = True

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests"
)

REQUEST_FAILURES = Counter(
    "http_request_failures_total",
    "Total failed HTTP requests"
)

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "Request latency"
)

HTML = """
<!doctype html>
<html>
<head>
  <title>SRE Flask Lab</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 760px; margin: 40px auto; }
    button { padding: 10px 14px; margin: 6px 4px; cursor: pointer; }
    pre { background: #f5f5f5; padding: 16px; border-radius: 8px; white-space: pre-wrap; }
    input { padding: 8px; width: 80px; }
  </style>
</head>
<body>
  <h1>SRE Flask Lab</h1>
  <p>Exercise health, readiness, and simulated work endpoints.</p>

  <button onclick="call('/healthz')">Check /healthz</button>
  <button onclick="call('/readyz')">Check /readyz</button>
  <button onclick="call('/metrics')">Check /metrics</button>
  <button onclick="call('/dashboard')">Check /dashboard</button>
  <button onclick="call('/alerts')">Check /alerts</button>

  <h3>/work simulation</h3>
  <label>Failure %:</label>
  <input id="fail" type="number" value="10" min="0" max="100">
  <label>Latency ms:</label>
  <input id="latency" type="number" value="250" min="0">

  <br><br>
  <button onclick="callWork()">Call /work</button>

  <h3>Result</h3>
  <pre id="result">No request yet.</pre>

  <script>
    async function call(path) {
      const started = performance.now();
      try {
        const res = await fetch(path);

        const contentType = res.headers.get("content-type");

        let data;
        if (contentType && contentType.includes("application/json")) {
          data = await res.json();
        } else {
          data = await res.text();
        }

        const elapsed = Math.round(performance.now() - started);
        document.getElementById("result").textContent =
          JSON.stringify({ status: res.status, elapsed_ms: elapsed, body: data }, null, 2);
      } catch (err) {
        document.getElementById("result").textContent = err.toString();
      }
    }

    async function callWork() {
      const fail = document.getElementById("fail").value;
      const latency = document.getElementById("latency").value;
      call(`/work?failure_rate=${fail}&latency_ms=${latency}`);
    }
  </script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/healthz")
def healthz():
    return jsonify({
        "status": "ok",
        "service": "sre-flask-lab",
        "version": APP_VERSION,
        "uptime_seconds": round(time.time() - START_TIME, 2)
    }), 200


@app.route("/readyz")
def readyz():
    if READY:
        return jsonify({
            "status": "ready",
            "message": "Service is ready to receive traffic"
        }), 200

    return jsonify({
        "status": "not_ready",
        "message": "Service is alive but should not receive traffic"
    }), 503


@app.route("/work")
def work():
    failure_rate = float(request.args.get("failure_rate", 0))
    latency_ms = int(request.args.get("latency_ms", 0))

    failure_rate = max(0, min(failure_rate, 100))
    latency_ms = max(0, latency_ms)

    started = time.time()

    if latency_ms:
        time.sleep(latency_ms / 1000)

    if random.random() < failure_rate / 100:
        return jsonify({
            "status": "error",
            "message": "Simulated failure",
            "failure_rate": failure_rate,
            "latency_ms": latency_ms,
            "duration_ms": round((time.time() - started) * 1000, 2)
        }), 500

    return jsonify({
        "status": "success",
        "message": "Work completed",
        "failure_rate": failure_rate,
        "latency_ms": latency_ms,
        "duration_ms": round((time.time() - started) * 1000, 2)
    }), 200

@app.route("/metrics")
def metrics():
    return generate_latest(), 200

@app.route("/dashboard")
def dashboard():
    total_requests = REQUEST_COUNT._value.get()
    failed_requests = REQUEST_FAILURES._value.get()

    successful_requests = total_requests - failed_requests

    success_rate = (
        round((successful_requests / total_requests) * 100, 2)
        if total_requests > 0 else 100.0
    )

    histogram_samples = REQUEST_LATENCY.collect()[0].samples

    latency_sum = 0
    latency_count = 0

    for sample in histogram_samples:
        if sample.name.endswith("_sum"):
            latency_sum = sample.value
        elif sample.name.endswith("_count"):
            latency_count = sample.value

    avg_latency_ms = (
        round((latency_sum / latency_count) * 1000, 2)
        if latency_count > 0 else 0
    )

    return jsonify({
        "service": "sre-flask-lab",
        "total_requests": int(total_requests),
        "successful_requests": int(successful_requests),
        "failed_requests": int(failed_requests),
        "success_rate_percent": success_rate,
        "average_latency_ms": avg_latency_ms
    }), 200

    @app.route("/alerts")
    def alerts():
        total_requests = REQUEST_COUNT._value.get()
        failed_requests = REQUEST_FAILURES._value.get()

        if total_requests == 0:
            return jsonify({
                "alert": False,
                "status": "insufficient_data",
                "message": "No requests recorded yet"
            }), 200

        failure_rate = failed_requests / total_requests
        success_rate = (1 - failure_rate) * 100

        SLO_TARGET = 99.0

        alert_triggered = success_rate < SLO_TARGET

        return jsonify({
            "service": "sre-flask-lab",
            "alert": alert_triggered,
            "slo_target_percent": SLO_TARGET,
            "success_rate_percent": round(success_rate, 2),
            "failed_requests": int(failed_requests),
            "total_requests": int(total_requests),
            "reason": (
                "SLO breached: success rate below target"
                if alert_triggered
                else "SLO healthy"
            )
        }), 200

@app.before_request
def before_request():
    request.start_time = time.time()

@app.after_request
def after_request(response):
    duration_ms = round((time.time() - request.start_time) * 1000, 2)

    log_entry = {
        "event": "request_completed",
        "method": request.method,
        "path": request.path,
        "status": response.status_code,
        "duration_ms": duration_ms,
        "remote_addr": request.headers.get("X-Forwarded-For", request.remote_addr),
        "user_agent": request.headers.get("User-Agent"),
    }

    app.logger.info(json.dumps(log_entry))

    REQUEST_COUNT.inc()
    REQUEST_LATENCY.observe(duration_ms / 1000)

    if response.status_code >= 500:
        REQUEST_FAILURES.inc()

    return response

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
