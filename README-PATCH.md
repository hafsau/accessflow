# AccessFlow v2 patch bundle

Drop these into the AccessFlow repo alongside the existing master prompt.

| File | Purpose |
|---|---|
| `docs/SPEC-V2-PATCH.md` | The surgical diff to the Implementation Master Prompt — which sections to replace and with what. Read this first. |
| `policies/accessflow.cedar` | The ACT/ASK/BLOCK matrix from §5.2, expressed as an enforceable Cedar policy. 162 lines. |
| `policies/entities.json` | Cedar entity store stub. |
| `backend/app/agents/authority.py` | Wires Cedar + LLM steering onto the Case Orchestrator. APIs verified 2026-08-25. |
| `backend/app/tools/legistar.py` | The real inbound edge: live public meeting feed, genuine change detection, ADA Title II obligation derivation. |

## Before writing any other code

1. Confirm the feed still answers, from a machine that has egress:
   ```
   curl -s "https://webapi.legistar.com/v1/seattle/Events?\$top=3&\$orderby=EventDate+desc" | head -c 600
   ```
   Verified 200 + JSON with no key on 2026-08-25.

2. Pick 3–5 Legistar client namespaces and confirm each:
   ```
   curl -s -o /dev/null -w "%{http_code}\n" "https://webapi.legistar.com/v1/<client>/Bodies?\$top=1"
   ```
   `seattle` is confirmed. `mountainview` and `oakland` in `WATCHED_CLIENTS` are unverified placeholders — check them or replace them.

3. Run the feed for 48 hours and count: new meetings/day, agenda documents posted/day, real changes detected/day. That number replaces the persona's asserted rate in §2.1 and goes in the video. Do not pitch before it is measured.

4. `pip install 'strands-agents[cedar]'` for the Cedar intervention.

## Cost guards
- Poll on Lambda free tier, not inside AgentCore Runtime — continuous CPU there is ~$114 over 44 days.
- **Never provision a NAT Gateway.** $0.045/hr x 1,056 hrs = $47.52, 95% of the $50 budget, before a single GB.
- AgentCore does not bill idle, so a judge-clickable agent stays live to Oct 8 for effectively nothing.

## Still open
`docs/SPEC-V2-PATCH.md` closes with the one thing this patch does not fix: AccessFlow remains an accessibility product, which collides with the portfolio-diversity rule. Decide that deliberately.
