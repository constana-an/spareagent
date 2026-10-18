# spareagent-worker

Outbound-only remote worker for SpareAgent (Week 2). Installs with the repository package:

```bash
pip install .
spareagent-worker register --api-url https://api.example:8443 --ca-file ca.pem \
  --resource-id <approved-resource-uuid> --name node-1 --enrollment-token sa_enr_...
spareagent-worker start --mock-step-delay 1
spareagent-worker status
spareagent-worker stop
```

* Needs only the API URL, a CA bundle and its own node key — never database or Redis
  credentials. State lives in `$SPAREAGENT_WORKER_HOME` (default `~/.spareagent-worker`,
  files mode 0600).
* Executes only the deterministic `MockProviderAdapter` in Week 2: no repository checkout,
  no shell commands.
* `start` runs in the foreground; use a process manager (systemd, compose) to daemonize.
