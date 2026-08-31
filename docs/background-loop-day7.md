# Day 7 — the background loop

**This is the day that makes the product's central claim true.**

Right now the poller runs on a laptop that sleeps: 53 polls in a 96-hour window, ~14% uptime. Every demo sentence about the agent working unattended depends on fixing that. Lambda runs whether the lid is open or not.

## The invariant — say this on camera

> **A poll where nothing moved produces zero agent invocations.**

That single line is the difference between an agent and a cron job, and the organizers' own guidance says the best entries *"run in the background and only surface when a human actually needs to decide something."* It has to be asserted in a test, not claimed in a README.

## Architecture — and the two traps

```
EventBridge (rate: 15 minutes)
      ↓
Lambda  poll_public_meetings → diff fingerprints → for each REAL change only:
      ↓
InvokeAgentRuntime  (AgentCore, per case)
      ↓
DynamoDB (case state)  ·  S3 (fingerprints + cached agenda text)
```

🔴 **Lambda must run OUTSIDE a VPC.** A NAT Gateway is $0.045/hr = **$47.52** over the judging window — 95% of the budget, before a byte of traffic. There is no reason for this Lambda to be in a VPC.

🔴 **Fingerprint state must move to S3 or DynamoDB.** The current `LegistarFeed._seen` dict is in-memory. Lambda is stateless — every invocation would see every meeting as new and fire the agent on all of them. That is the runaway loop again, except unattended, every 15 minutes, for 24 days. Load fingerprints at the start of each invocation, write them back at the end.

## Cost — the free-tier arithmetic

Lambda's always-free tier is **1M requests + 400,000 GB-seconds per month**, permanent, not a 12-month trial.

- 4 polls/hour × 24 × 44 days = **4,224 invocations** — trivially inside 1M
- 30s × 512MB = 15 GB-s per poll × 4,224 = **63,360 GB-s** — inside 400,000

**The poller costs $0.** Only the per-case AgentCore + Bedrock work costs anything, and that's the $0.09 you measured.

## Guards — this thing runs unattended for 24 days

1. **Per-invocation cap.** At most **3 cases per poll**. If a burst produces 20 changes, queue the rest. Without this, one busy day fires 20 × $0.09 in a single cycle.
2. **Daily spend ceiling.** `check_and_charge()` reads its ledger from DynamoDB, not a local file — Lambda's filesystem does not persist.
3. **Cache every agenda fetch to S3, keyed by sha256.** If Legistar rate-limits or 403s during Sep 15–Oct 8, the demo serves from cache and survives. `sfgov` already errors every cycle; assume others can too.
4. **Log every decision to CloudWatch** with the fingerprint before and after. That log *is* the demo beat — you will scroll it on camera.
5. **Structured logging, not prose.** `{"event":"change_detected","key":"seattle:6860","old_fp":"...","new_fp":"...","invoked_agent":true}` — greppable when you're looking for the one good example on Sep 9.

## Migration

Run both pollers in parallel for 24 hours. The local one keeps `feed-measure.jsonl` intact as a backup while you confirm Lambda is detecting the same changes. Then stop the local one — **but keep the file**, it is four days of evidence.

Seed S3 with the current in-memory fingerprints if you can; otherwise accept one noisy first cycle where everything reads as new, and note the timestamp so you can exclude it from the demo.

## Exit criterion

- [ ] EventBridge rule firing Lambda every 15 minutes
- [ ] Lambda **outside** any VPC (verify: no `VpcConfig` in the function config)
- [ ] Fingerprints persisted in S3 or DynamoDB, loaded and written each invocation
- [ ] **Test: a poll with no changes makes zero `InvokeAgentRuntime` calls.** Assert it
- [ ] Max 3 cases per invocation
- [ ] Agenda fetches cached to S3 by sha256
- [ ] CloudWatch shows structured decision logs
- [ ] 24 hours of parallel running confirms Lambda sees the same changes as local
- [ ] `aws lambda get-function-configuration` shows no VPC attached

## The line for the video

> *"It polls every fifteen minutes. Most polls, nothing has moved, and it does nothing — no model call, no cost. It only wakes up when a real meeting actually changed. Here's the log from Tuesday at 2:32am."*
