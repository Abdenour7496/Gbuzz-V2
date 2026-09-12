# Human alert delivery activation runbook

External alerting is intentionally inactive pending an approved endpoint and named recipients. The local audit webhook remains the only deployed receiver.

The smallest M365 path is Alertmanager -> approved HTTPS Power Automate workflow -> Teams operations channel adaptive card. The card must show alert identity, severity, start time, runbook link, and an Acknowledge action that records the human identity and timestamp. Critical alerts unacknowledged for ten minutes escalate to a named secondary; resolved notifications close the loop.

Before activation, the owner must provide the HTTPS endpoint through the deployment secret facility, the primary and secondary recipients, the Teams channel, and retention/privacy approval for incident cards. Mount a production Alertmanager file via `ALERTMANAGER_CONFIG_FILE`; never commit the URL.

Test end to end with a dedicated synthetic `Watchdog` alert. Evidence must include Prometheus firing/resolved timestamps, Alertmanager delivery status, Teams message ID, acknowledging identity/time, and escalation behavior. Run once before production reliance and weekly thereafter. Platform Reliability owns the test and evidence; the primary on-call owns acknowledgement and triage; the secondary owns escalation when the ten-minute SLA expires.
