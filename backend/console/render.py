"""AccessFlow operations console — presentation layer.

Separated from handler.py so routing and data access stay untouched.
handler.py fetches; this module renders.

STRUCTURE
    The product is deadlines against a calendar, so the page is a docket, not a
    dashboard. Every case is plotted on a shared time axis: the meeting, the
    48-hour notice deadline that §35.160 imposes, and a NOW line running down
    the page. "What is about to be missed?" is answerable without reading a
    single row.

    §35.160 (meeting − 48h) is what goes on the axis. §35.200 conformance
    deadlines sit in 2027 and would flatten the scale to uselessness, so they
    are carried as a separate compact status. Two obligations, two horizons —
    plotting both on one axis would be dishonest about the data.

ACCESSIBILITY
    This is a product about accessibility compliance, so the interface is held
    to the standard it measures: semantic landmarks and tables, a skip link,
    visible focus, AA contrast in both themes, aria-live on operator actions,
    prefers-reduced-motion honoured, and the docket duplicated as a real table
    for anyone who cannot use the visual axis.
"""
from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone
from typing import Any

CATEGORY_LABELS = {
    "effective_communication": "Effective communication",
    "document_conformance": "Document conformance",
}
STATE_LABELS = {
    "NEW": "New", "COORDINATING": "Coordinating",
    "AWAITING_DECISION": "Awaiting decision", "AWAITING_PROVIDER": "Awaiting provider",
    "VERIFYING": "Verifying", "VERIFIED": "Verified",
    "CLOSED": "Closed", "CANCELLED": "Cancelled",
}
JURISDICTIONS = {
    "seattle": "Seattle, WA", "oakland": "Oakland, CA", "sanjose": "San José, CA",
    "kingcounty": "King County, WA", "alameda": "Alameda, CA", "sacramento": "Sacramento, CA",
}
TOOL_LABELS = {
    "create_case": "Case opened",
    "derive_obligations": "Obligations derived",
    "fetch_agenda_document": "Agenda fetched",
    "extract_accommodation_policy": "Agenda read — accommodations inferred",
    "search_providers": "Providers searched",
    "send_provider_request": "Provider request sent",
    "confirm_provider_request": "Provider response recorded",
    "request_human_decision": "Escalated to a human",
    "verify_fulfillment": "Fulfilment verified",
    "close_case": "Case closed",
}
MODEL_BEARING = {"extract_accommodation_policy"}


def esc(v: Any) -> str:
    return html.escape(str(v if v is not None else ""), quote=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(raw: Any) -> datetime | None:
    if not raw:
        return None
    text = str(raw).strip().replace("Z", "+0000")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _meeting_dt(event: dict[str, Any] | None) -> datetime | None:
    if not event:
        return None
    return _parse(event.get("date"))


def _notice_obl(case: dict[str, Any]) -> dict[str, Any] | None:
    for o in case.get("obligations") or []:
        if o.get("category") == "effective_communication":
            return o
    return None


def _conformance_obl(case: dict[str, Any]) -> dict[str, Any] | None:
    for o in case.get("obligations") or []:
        if o.get("category") == "document_conformance":
            return o
    return None


def deadline_phrase(raw: Any, fulfilled: bool = False) -> tuple[str, str]:
    """(short label, urgency class)."""
    dt = _parse(raw)
    if dt is None:
        return ("no date", "none")
    if fulfilled:
        return ("met", "met")
    days = (dt - _now()).total_seconds() / 86400
    if days < 0:
        return (f"missed by {abs(int(days)) or 1}d", "missed")
    if days < 1:
        return ("due today", "missed")
    if days <= 3:
        return (f"{int(days)}d left", "urgent")
    if days <= 14:
        return (f"{int(days)}d left", "soon")
    # Include year if not the current year
    fmt = "%-d %b" if dt.year == _now().year else "%-d %b %Y"
    return (dt.strftime(fmt), "distant")


CSS = """
:root{
  --paper:#F5F2EB; --raised:#FFFEFA; --sunk:#EDE8DE;
  --ink:#101317; --ink-2:#3E4650; --ink-3:#606975;
  --rule:#DED7C8; --rule-2:#C2B9A6; --mark:#8D8471;
  --green:#174A3D; --green-t:#E2EDE8;
  --amber:#8A5417; --amber-t:#F6EADA;
  --red:#8C2A18;   --red-t:#F7E2DD;
  --steel:#334153; --steel-t:#E7EBF0;
  --focus:#174A3D;
  --label-w:270px; --chip-w:112px;
}
@media (prefers-color-scheme:dark){
  :root{
    --paper:#0F1113; --raised:#161A1E; --sunk:#1C2126;
    --ink:#F2EFE8; --ink-2:#B6BDC6; --ink-3:#8B939D;
    --rule:#282E34; --rule-2:#3B434B; --mark:#646C74;
    --green:#7CCBAF; --green-t:#12302A;
    --amber:#E0A768; --amber-t:#33240F;
    --red:#F2937F;   --red-t:#3B1913;
    --steel:#9FB0C4; --steel-t:#1E252C;
    --focus:#7CCBAF;
  }
}
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0;background:var(--paper);color:var(--ink);
  font-family:"IBM Plex Sans",ui-sans-serif,system-ui,sans-serif;
  font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased;
}
.skip{position:absolute;left:-9999px;top:0;z-index:99;background:var(--ink);
  color:var(--paper);padding:.8rem 1.3rem;text-decoration:none;font-weight:600}
.skip:focus{left:0}
:focus-visible{outline:2.5px solid var(--focus);outline-offset:2px;border-radius:2px}
.sr{position:absolute!important;width:1px;height:1px;overflow:hidden;
  clip:rect(0 0 0 0);white-space:nowrap}
.shell{max-width:1240px;margin:0 auto;padding:0 2rem 6rem}

/* masthead */
.mast{padding:3rem 0 0;display:grid;grid-template-columns:1fr auto;
  gap:2rem;align-items:end;border-bottom:3px solid var(--ink);padding-bottom:1.4rem}
h1{font-family:"Newsreader",Georgia,serif;font-weight:400;
  font-size:clamp(2.4rem,5.5vw,3.9rem);line-height:.98;letter-spacing:-.025em;margin:0}
h1 .sub{display:block;font-weight:300;font-size:.46em;letter-spacing:.055em;
  text-transform:uppercase;color:var(--green);margin-top:.5rem;line-height:1}
.mast p{font-family:"Newsreader",Georgia,serif;font-size:1.06rem;line-height:1.5;
  color:var(--ink-2);max-width:54ch;margin:1rem 0 0}
.asof{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:.6875rem;
  letter-spacing:.13em;text-transform:uppercase;color:var(--ink-3);text-align:right;
  white-space:nowrap}
.asof b{display:block;font-weight:500;color:var(--green);font-size:.75rem;margin-bottom:.3rem}

/* running status line, not tiles */
.status{font-family:"Newsreader",Georgia,serif;font-size:clamp(1.05rem,2.2vw,1.42rem);
  line-height:1.5;padding:1.6rem 0 2.4rem;border-bottom:1px solid var(--rule);
  margin-bottom:3rem;color:var(--ink-2)}
.status b{font-weight:500;color:var(--ink);font-variant-numeric:tabular-nums}
.status .bad{color:var(--red)}
.status .good{color:var(--green)}

section{margin-bottom:3.75rem}
.head{display:flex;align-items:baseline;justify-content:space-between;gap:1rem;
  border-bottom:1px solid var(--rule-2);padding-bottom:.7rem;margin-bottom:1.4rem}
h2{font-family:"Newsreader",Georgia,serif;font-weight:500;font-size:1.55rem;
  letter-spacing:-.012em;margin:0}
.note{color:var(--ink-2);font-size:.86rem;margin:.3rem 0 0;max-width:64ch}
.tag{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:.65rem;
  letter-spacing:.13em;text-transform:uppercase;color:var(--ink-3);white-space:nowrap}

/* ── the docket ─────────────────────────────────────── */
.docket{border:1px solid var(--rule-2);background:var(--raised);overflow:hidden}
.axis{display:grid;grid-template-columns:var(--label-w) 1fr var(--chip-w);
  border-bottom:1px solid var(--rule-2);background:var(--sunk)}
.axis-pad{border-right:1px solid var(--rule-2)}
.axis-track{position:relative;height:34px}
.tick{position:absolute;top:0;bottom:0;border-left:1px solid var(--rule);
  padding-left:.4rem;font-family:"IBM Plex Mono",ui-monospace,monospace;
  font-size:.625rem;letter-spacing:.07em;color:var(--ink-3);
  display:flex;align-items:center;white-space:nowrap}
.tick.month{border-left-color:var(--rule-2);color:var(--ink-2)}

.row{display:grid;grid-template-columns:var(--label-w) 1fr var(--chip-w);
  border-bottom:1px solid var(--rule);text-decoration:none;color:inherit}
.row:last-child{border-bottom:0}
.row:hover{background:var(--sunk)}
.row:focus-visible{outline-offset:-3px}
.lab{padding:.62rem .85rem .62rem 1rem;border-right:1px solid var(--rule-2);min-width:0}
.lab .b{font-family:"Newsreader",Georgia,serif;font-size:1.02rem;line-height:1.2;
  display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.lab .j{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:.625rem;
  letter-spacing:.06em;color:var(--ink-3);display:block;margin-top:.15rem}

.track{position:relative;height:52px}
.now{position:absolute;top:0;bottom:0;width:1px;background:var(--red);z-index:3}
.axis-track .now{width:2px}
.axis-track .now::after{content:"NOW";position:absolute;top:9px;left:5px;
  font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:.6rem;
  letter-spacing:.14em;color:var(--red);font-weight:600}

/* the span from notice-deadline to meeting */
.link{position:absolute;top:50%;transform:translateY(-50%);height:1px;
  background:var(--mark);z-index:1}
.span{position:absolute;top:50%;transform:translateY(-50%);height:7px;
  border-radius:1px;z-index:1}
.span.s-done{background:var(--green);border:1px solid var(--green)}
.span.s-bad{border:1px solid var(--red);
  background:repeating-linear-gradient(135deg,var(--red-t) 0 3px,transparent 3px 6px)}
.span.s-warn{border:1px dashed var(--amber);background:var(--amber-t)}
.span.s-ok{border:1px solid var(--green);background:var(--green-t)}
.span.s-idle{border:1px dotted var(--mark);background:transparent}

.pt{position:absolute;top:50%;z-index:2}
.pt.notice{width:1px;height:20px;transform:translate(-50%,-50%)}
.pt.notice.c-ok{background:var(--green)} .pt.notice.c-warn{background:var(--amber)}
.pt.notice.c-bad{background:var(--red)}  .pt.notice.c-idle{background:var(--mark)}
.pt.meet{width:11px;height:11px;transform:translate(-50%,-50%) rotate(45deg)}
.pt.meet.c-ok{background:var(--green)} .pt.meet.c-warn{background:var(--amber)}
.pt.meet.c-bad{background:var(--red)}  .pt.meet.c-idle{background:var(--ink-3)}
.pt.check{transform:translate(-50%,-50%);font-family:"IBM Plex Mono",monospace;
  font-size:.72rem;color:var(--green);font-weight:600;line-height:1}

.chipcell{display:flex;align-items:center;justify-content:flex-end;
  padding:0 1rem 0 .5rem;border-left:1px solid var(--rule);min-width:0}
.axis-chip{border-left:1px solid var(--rule-2)}
.chip{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:.625rem;
  letter-spacing:.04em;padding:.14rem .42rem;border-radius:2px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;max-width:100%}
.chip.k-met{color:var(--green);background:var(--green-t)}
.chip.k-missed{color:var(--red);background:var(--red-t);font-weight:600}
.chip.k-urgent{color:var(--red);background:var(--red-t)}
.chip.k-soon{color:var(--amber);background:var(--amber-t)}
.chip.k-distant,.chip.k-none{color:var(--ink-3)}

.legend{display:flex;flex-wrap:wrap;gap:1.4rem;padding:.9rem 1rem;
  border-top:1px solid var(--rule);background:var(--sunk);
  font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:.625rem;
  letter-spacing:.08em;color:var(--ink-3);align-items:center}
.legend i{display:inline-block;vertical-align:middle;margin-right:.45rem}
.legend .i-notice{width:1px;height:12px;background:var(--ink-3)}
.legend .i-meet{width:9px;height:9px;background:var(--ink-3);transform:rotate(45deg)}
.legend .i-now{width:2px;height:12px;background:var(--red)}

/* ── case files ─────────────────────────────────────── */
details{border:1px solid var(--rule);background:var(--raised);margin-bottom:.6rem}
details[open]{border-color:var(--rule-2)}
summary{padding:.95rem 1.1rem;min-height:44px;cursor:pointer;display:flex;gap:1rem;
  align-items:baseline;justify-content:space-between;flex-wrap:wrap;list-style:none}
summary::-webkit-details-marker{display:none}
summary::before{content:"▸";color:var(--ink-3);margin-right:.5rem;
  display:inline-block;transition:transform .15s ease}
details[open] summary::before{transform:rotate(90deg)}
summary:hover{background:var(--sunk)}
.sum-t{font-family:"Newsreader",Georgia,serif;font-size:1.06rem;flex:1;min-width:0}
.pill{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:.65rem;
  letter-spacing:.06em;padding:.2rem .55rem;border-radius:2px;white-space:nowrap;
  border:1px solid transparent}
.pill.p-closed{background:var(--green-t);color:var(--green);border-color:var(--green);font-weight:600}
.pill.p-verified{background:var(--green-t);color:var(--green)}
.pill.p-awaiting_decision{background:var(--amber-t);color:var(--amber)}
.pill.p-awaiting_provider,.pill.p-new,.pill.p-coordinating,.pill.p-verifying{
  background:var(--steel-t);color:var(--steel)}
.pill.p-cancelled{background:transparent;color:var(--ink-3);border-color:var(--rule-2)}

.file{padding:0 1.1rem 1.2rem;border-top:1px solid var(--rule)}
.file-grid{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:2rem;padding-top:1.1rem}
@media(max-width:820px){.file-grid{grid-template-columns:1fr}}
.file h4{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:.625rem;
  letter-spacing:.14em;text-transform:uppercase;color:var(--ink-3);
  margin:0 0 .8rem;font-weight:500}

/* action trace */
.trace{list-style:none;margin:0;padding:0;position:relative}
.trace::before{content:"";position:absolute;left:5px;top:6px;bottom:6px;
  width:1px;background:var(--rule-2)}
.trace li{position:relative;padding:0 0 .85rem 1.5rem;font-size:.82rem}
.trace li::before{content:"";position:absolute;left:2px;top:.42rem;width:7px;height:7px;
  border-radius:50%;background:var(--ink-3);border:2px solid var(--raised)}
.trace li.ok::before{background:var(--green)}
.trace li.err::before{background:var(--red)}
.trace li.model::before{background:var(--amber);border-radius:50%;box-shadow:0 0 0 2px var(--amber)}
.trace .tn{display:block;line-height:1.35}
.trace .tm{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:.6875rem;
  color:var(--ink-3)}
.trace .badge{font-family:"IBM Plex Mono",monospace;font-size:.6rem;letter-spacing:.08em;
  color:var(--amber);border:1px solid var(--amber);padding:0 .3rem;margin-left:.4rem;
  border-radius:2px;vertical-align:1px}

.oblig{list-style:none;margin:0 0 1.2rem;padding:0}
.oblig li{border-left:3px solid var(--rule-2);padding:.15rem 0 .15rem .65rem;
  margin-bottom:.55rem}
.oblig li.k-met{border-left-color:var(--green)}
.oblig li.k-missed,.oblig li.k-urgent{border-left-color:var(--red)}
.oblig li.k-soon{border-left-color:var(--amber)}
.oblig .c{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:.72rem;font-weight:500}
.oblig .d{font-size:.8125rem;color:var(--ink-2);display:block}
.kv{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:.6875rem;
  color:var(--ink-3);line-height:1.7}
.kv b{color:var(--ink-2);font-weight:500}

/* decision queue + operator */
.cards{display:grid;gap:1rem}
.card{background:var(--raised);border:1px solid var(--rule);border-left:3px solid var(--amber);
  padding:1.2rem 1.35rem}
.card h3{font-family:"Newsreader",Georgia,serif;font-size:1.12rem;margin:0 0 .15rem;font-weight:500}
.card .why{color:var(--ink-2);font-size:.85rem;margin:.45rem 0 .9rem}
.opts{list-style:none;margin:0;padding:0;display:grid;gap:.45rem}
.opt{border:1px solid var(--rule);padding:.6rem .8rem;font-size:.85rem;background:var(--paper);
  display:flex;gap:.7rem;align-items:baseline}
.opt.rec{border-color:var(--green);background:var(--green-t)}
.opt .r{font-family:"IBM Plex Mono",monospace;font-size:.6rem;letter-spacing:.1em;
  text-transform:uppercase;color:var(--green);font-weight:600;white-space:nowrap}

.op{display:flex;justify-content:space-between;align-items:center;gap:1rem;
  flex-wrap:wrap;padding:.95rem 0;border-bottom:1px solid var(--rule)}
.op:last-of-type{border-bottom:0}
.op .m{font-size:.86rem}
.op .m code{font-family:"IBM Plex Mono",monospace;font-size:.8rem}
.acts{display:flex;gap:.55rem}
button{font-family:"IBM Plex Sans",ui-sans-serif,system-ui,sans-serif;font-size:.8125rem;
  font-weight:500;padding:.62rem 1rem;min-height:44px;border:1px solid var(--rule-2);background:var(--paper);
  color:var(--ink);cursor:pointer;border-radius:2px;
  transition:background .15s,border-color .15s,transform .1s}
button:hover{background:var(--raised);border-color:var(--ink-2)}
button:active{transform:translateY(1px)}
button.yes{border-color:var(--green);color:var(--green)}
button.yes:hover{background:var(--green-t)}
button.no{border-color:var(--red);color:var(--red)}
button.no:hover{background:var(--red-t)}
button[disabled]{opacity:.45;cursor:not-allowed}
.opstatus{font-family:"IBM Plex Mono",monospace;font-size:.75rem;color:var(--ink-2);
  min-height:1.4em;padding-top:.7rem}

.disc{border:1px solid var(--amber);background:var(--amber-t);color:var(--amber);
  padding:.85rem 1.05rem;font-size:.8125rem;margin-top:1.1rem}
.empty{border:1px dashed var(--rule-2);padding:1.8rem;text-align:center;
  color:var(--ink-3);font-size:.86rem}

footer{border-top:3px solid var(--ink);margin-top:3.5rem;padding-top:1.4rem;
  display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:1.6rem;
  font-size:.8125rem;color:var(--ink-2)}
footer h3{font-family:"IBM Plex Mono",monospace;font-size:.625rem;letter-spacing:.14em;
  text-transform:uppercase;color:var(--ink-3);margin:0 0 .5rem;font-weight:500}
footer a{color:var(--green);text-underline-offset:2px}

@media(max-width:860px){
  :root{--label-w:150px;--chip-w:88px}
  .shell{padding:0 1.1rem 4rem}
  .mast{grid-template-columns:1fr}
  .asof{text-align:left}
}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{transition:none!important;animation:none!important}}
"""


# ── docket geometry ────────────────────────────────────────────────────────

def _window(cases, events):
    now = _now()
    dates = []
    for c in cases:
        m = _meeting_dt(events.get(str(c.get("event_id"))))
        if m:
            dates.append(m)
        o = _notice_obl(c)
        d = _parse(o.get("deadline")) if o else None
        if d:
            dates.append(d)
    lo = min(dates + [now]) - timedelta(days=2)
    hi = max(dates + [now]) + timedelta(days=2)
    if (hi - lo).days < 10:
        hi = lo + timedelta(days=10)
    return lo, hi


def _pct(dt, lo, hi):
    return max(0.0, min(100.0, (dt - lo).total_seconds() / (hi - lo).total_seconds() * 100))


def _ticks(lo, hi):
    out, cur = [], lo.replace(hour=0, minute=0, second=0, microsecond=0)
    step = max(1, (hi - lo).days // 9)
    while cur <= hi:
        p = _pct(cur, lo, hi)
        if p <= 94.0:                      # keep the last label inside the track
            first = cur.day <= step
            out.append((p, cur.strftime("%-d %b") if first else cur.strftime("%-d"), first))
        cur += timedelta(days=step)
    return out


def _docket_row(case, event, lo, hi):
    ev = event or {}
    body = ev.get("body_name") or case.get("event_id") or "Unknown body"
    client = str(case.get("event_id") or "").split(":")[0]
    where = JURISDICTIONS.get(client, client.title())
    state = str(case.get("state") or "")
    cid = esc(case.get("case_id"))

    obl = _notice_obl(case) or {}
    fulfilled = bool(obl.get("fulfilled"))
    label, kind = deadline_phrase(obl.get("deadline"), fulfilled)
    dl = _parse(obl.get("deadline"))
    meet = _meeting_dt(ev)

    if state == "CLOSED":
        tone, cls = "s-done", "c-ok"
    elif state == "CANCELLED":
        tone, cls = "s-idle", "c-idle"
    elif kind in ("missed",):
        tone, cls = "s-bad", "c-bad"
    elif kind in ("urgent", "soon"):
        tone, cls = "s-warn", "c-warn"
    else:
        tone, cls = "s-ok", "c-ok"

    nowp = _pct(_now(), lo, hi)
    bits = [f'<div class="now" style="left:{nowp:.3f}%"></div>']

    dlp = _pct(dl, lo, hi) if dl else None
    mtp = _pct(meet, lo, hi) if meet else None

    # thin connector: notice deadline → meeting. Always 48h, so it carries no
    # magnitude — it is drawn as a hairline, not a bar, so it cannot read as data.
    if dlp is not None and mtp is not None:
        lo_p, hi_p = min(dlp, mtp), max(dlp, mtp)
        bits.append(f'<div class="link" style="left:{lo_p:.3f}%;width:{max(hi_p-lo_p,0.25):.3f}%"></div>')

    # the bar that means something: time remaining until the notice deadline,
    # or — past it — how far the obligation has overrun. Anchored at NOW so
    # every bar shares an origin and lengths compare directly.
    if dlp is not None and not fulfilled and state not in ("CLOSED", "CANCELLED"):
        a, b = min(nowp, dlp), max(nowp, dlp)
        w = max(b - a, 0.3)
        kindcls = "s-bad" if dlp < nowp else ("s-warn" if kind in ("urgent", "soon") else "s-ok")
        bits.append(f'<div class="span {kindcls}" style="left:{a:.3f}%;width:{w:.3f}%"></div>')

    if dlp is not None:
        bits.append(
            f'<div class="pt check" style="left:{dlp:.3f}%">✓</div>' if fulfilled
            else f'<div class="pt notice {cls}" style="left:{dlp:.3f}%"></div>')
    if mtp is not None:
        bits.append(f'<div class="pt meet {cls}" style="left:{mtp:.3f}%"></div>')

    when = meet.strftime("%-d %b") if meet else "—"
    spoken = (f"{body}, {where}, meeting {when}, "
              f"section 35.160 notice deadline {label}, state {STATE_LABELS.get(state, state)}")

    return f"""<a class="row" href="#f-{cid}">
  <div class="lab">
    <span class="b">{esc(body)}</span>
    <span class="j">{esc(where)} · {esc(when)}</span>
  </div>
  <div class="track">{''.join(bits)}<span class="sr">{esc(spoken)}</span></div>
  <div class="chipcell"><span class="chip k-{esc(kind)}">{esc(label)}</span></div>
</a>"""


def _trace_html(actions):
    if not actions:
        return '<p class="kv">No recorded actions.</p>'
    items = []
    for a in sorted(actions, key=lambda x: str(x.get("created_at") or "")):
        tool = str(a.get("tool_name") or "")
        ok = bool(a.get("success"))
        cls = "model" if tool in MODEL_BEARING else ("ok" if ok else "err")
        ts = _parse(a.get("created_at"))
        stamp = ts.strftime("%-d %b · %H:%M:%S") if ts else ""
        badge = '<span class="badge">model</span>' if tool in MODEL_BEARING else ""
        err = f' · <span style="color:var(--red)">{esc(a.get("error_code"))}</span>' if not ok else ""
        items.append(
            f'<li class="{cls}"><span class="tn">{esc(TOOL_LABELS.get(tool, tool))}{badge}</span>'
            f'<span class="tm">{esc(stamp)} · {esc(str(a.get("output_hash") or "")[:19])}{err}</span></li>'
        )
    return f'<ol class="trace">{"".join(items)}</ol>'


def _case_file(case, event, actions, decision, is_open=False):
    ev = event or {}
    cid = esc(case.get("case_id"))
    body = ev.get("body_name") or case.get("event_id")
    state = str(case.get("state") or "")
    meet = _meeting_dt(ev)
    when = meet.strftime("%a %-d %b %Y") if meet else "date unknown"
    client = str(case.get("event_id") or "").split(":")[0]

    obls = []
    for o in case.get("obligations") or []:
        lbl, kind = deadline_phrase(o.get("deadline"), bool(o.get("fulfilled")))
        cite = str(o.get("basis") or "").replace("28 CFR ", "§")
        obls.append(
            f'<li class="k-{esc(kind)}"><span class="c">{esc(cite)}</span> '
            f'<span class="d">{esc(CATEGORY_LABELS.get(str(o.get("category")), o.get("category")))}'
            f' — {esc(lbl)}</span></li>'
        )

    kv = [f"<b>case</b> {cid}", f"<b>event</b> {esc(case.get('event_id'))}"]
    if case.get("verification_id"):
        kv.append(f"<b>verification</b> {esc(case.get('verification_id'))}")
    if ev.get("agenda_url"):
        kv.append(f'<b>agenda</b> <a href="{esc(ev.get("agenda_url"))}" rel="noopener">PDF</a>')

    dec = ""
    if decision and decision.get("options"):
        dec = '<p class="kv" style="margin-top:1rem"><a href="#awaiting">See decision options ↑</a></p>'

    open_attr = " open" if is_open else ""
    return f"""<details id="f-{cid}"{open_attr}>
  <summary>
    <span class="sum-t">{esc(body)} <span class="kv">· {esc(when)} · {esc(JURISDICTIONS.get(client, client))}</span></span>
    <span class="pill p-{esc(state.lower())}">{esc(STATE_LABELS.get(state, state))}</span>
  </summary>
  <div class="file"><div class="file-grid">
    <div>
      <h4>What the agent did</h4>
      {_trace_html(actions)}
    </div>
    <div>
      <h4>Obligations</h4>
      <ul class="oblig">{''.join(obls) or '<li class="kv">none derived</li>'}</ul>
      <h4>Record</h4>
      <p class="kv">{'<br>'.join(kv)}</p>
      {dec}
    </div>
  </div></div>
</details>"""


def _op_row(req):
    rid = esc(req.get("request_id"))
    sent = _parse(req.get("sent_at"))
    return f"""<div class="op">
  <div class="m">Provider <code>{esc(req.get('provider_id'))}</code> ·
    case <code>{esc(req.get('case_id'))}</code><br>
    <span class="kv">requested {esc(sent.strftime('%-d %b, %H:%M') if sent else '—')} · awaiting response</span></div>
  <div class="acts">
    <button class="yes" data-req="{rid}" data-r="CONFIRMED">Provider confirms</button>
    <button class="no"  data-req="{rid}" data-r="DECLINED">Provider declines</button>
  </div>
</div>"""


# ── page ───────────────────────────────────────────────────────────────────

def page(cases, pending_requests=None, events=None, decisions=None, actions=None):
    cases = list(cases or [])
    pending = pending_requests or []
    events = events or {}
    decisions = decisions or {}
    actions = actions or {}

    # Docket shows only active cases — CLOSED belongs in case files, not on a
    # chart whose axis is "time remaining". CANCELLED cases are also excluded.
    live = [c for c in cases if c.get("state") not in ("CANCELLED", "CLOSED")]
    order = {"AWAITING_DECISION": 0, "AWAITING_PROVIDER": 1, "NEW": 2,
             "COORDINATING": 2, "VERIFYING": 3, "VERIFIED": 4, "CLOSED": 5, "CANCELLED": 6}

    # Derive jurisdiction count from the data (prefix of event_id before ":")
    jurisdictions = {str(c.get("event_id", "")).split(":")[0] for c in cases if c.get("event_id")}
    jurisdiction_count = len(jurisdictions)

    def sort_key(c):
        """Sort by time-to-deadline ascending: most overdue first, then soonest."""
        o = _notice_obl(c) or {}
        d = _parse(o.get("deadline"))
        return (d or _now() + timedelta(days=3650), order.get(str(c.get("state")), 9))

    live.sort(key=sort_key)
    lo, hi = _window(live or cases, events)

    obls = [o for c in cases for o in (c.get("obligations") or [])]
    missed = sum(1 for c in cases for o in [(_notice_obl(c) or {})]
                 if o and not o.get("fulfilled")
                 and deadline_phrase(o.get("deadline"))[1] == "missed"
                 and c.get("state") not in ("CLOSED", "CANCELLED"))
    soon = sum(1 for c in cases for o in [(_notice_obl(c) or {})]
               if o and not o.get("fulfilled")
               and deadline_phrase(o.get("deadline"))[1] in ("urgent", "soon"))
    closed = sum(1 for c in cases if c.get("state") == "CLOSED")

    rows = "".join(_docket_row(c, events.get(str(c.get("event_id"))), lo, hi) for c in live) or \
        f'<div class="empty">No live cases. The poller checks {jurisdiction_count} jurisdictions every 15 minutes.</div>'
    ticks = "".join(
        f'<div class="tick{" month" if first else ""}" style="left:{p:.3f}%">{esc(lbl)}</div>'
        for p, lbl, first in _ticks(lo, hi))

    # Decide which case files to open: one closed-and-verified, one escalated, one missed
    open_cases = set()
    for c in cases:
        state = c.get("state")
        if state == "CLOSED" and c.get("verification_passed") and len(open_cases) < 3:
            open_cases.add(c.get("case_id"))
            break
    for c in cases:
        if c.get("state") == "AWAITING_DECISION" and len(open_cases) < 3:
            open_cases.add(c.get("case_id"))
            break
    for c in cases:
        obl = _notice_obl(c) or {}
        if obl and not obl.get("fulfilled") and c.get("state") not in ("CLOSED", "CANCELLED"):
            _, kind = deadline_phrase(obl.get("deadline"))
            if kind == "missed":
                open_cases.add(c.get("case_id"))
                break

    files = "".join(
        _case_file(c, events.get(str(c.get("event_id"))),
                   actions.get(str(c.get("case_id"))) or [],
                   decisions.get(str(c.get("case_id"))),
                   is_open=c.get("case_id") in open_cases)
        for c in sorted(cases, key=sort_key))

    awaiting = [c for c in cases if c.get("state") == "AWAITING_DECISION"]
    cards = "".join(
        f'<article class="card"><h3>{esc((events.get(str(c.get("event_id"))) or {}).get("body_name") or c.get("event_id"))}</h3>'
        f'<p class="why">The agent stopped here and asked. It will not proceed on this case without a human decision.</p>'
        + ("".join(
            '<ul class="opts"><li class="opt{r}">{t}<span>{d}</span></li></ul>'.format(
                r=" rec" if o.get("recommended") else "",
                t='<span class="r">Recommended</span>' if o.get("recommended") else "",
                d=esc(o.get("description") or "—"))
            for o in ((decisions.get(str(c.get("case_id"))) or {}).get("options") or []))
           or '<p class="kv">No options recorded.</p>')
        + f'<p class="kv" style="margin-top:.8rem"><a href="#f-{esc(c.get("case_id"))}">Open case file →</a></p></article>'
        for c in awaiting) or '<div class="empty">Nothing awaiting a human decision.</div>'

    ops = "".join(_op_row(r) for r in pending) or \
        '<div class="empty">No provider requests awaiting a response.</div>'

    status = (
        f'Tracking <b>{len(obls)}</b> statutory obligations across <b>{len(cases)}</b> meetings in '
        f'<b>{jurisdiction_count}</b> jurisdictions. '
        + (f'<b class="bad">{missed}</b> notice deadline{"s" if missed != 1 else ""} already missed. '
           if missed else '')
        + (f'<b>{soon}</b> fall due within a fortnight. ' if soon else '')
        + (f'<b class="good">{closed}</b> closed against verified evidence.' if closed else '')
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AccessFlow — Deadline Docket</title>
<meta name="description" content="Accessibility obligations derived from real public meeting calendars, plotted against their statutory deadlines.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,300;6..72,400;6..72,500&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>
<a class="skip" href="#docket">Skip to the docket</a>
<div class="shell">

<header class="mast">
  <div>
    <h1>AccessFlow<br><span class="sub">The Deadline Docket</span></h1>
    <p class="standfirst">AccessFlow is an autonomous agent that watches public meeting calendars,
    works out what each meeting owes under federal accessibility law, and arranges it before the
    deadline — without waiting for anyone to ask.</p>
    <p>A public body schedules a meeting. Federal law already requires it to furnish auxiliary
    aids for that meeting — since 1991, no phase-in, no request needed. Every case below is a
    real meeting, its obligations, and the time left to meet them.</p>
  </div>
  <div class="asof"><b>Live</b>{esc(_now().strftime('%-d %b %Y · %H:%M UTC'))}<br>{jurisdiction_count} jurisdictions · 15 min</div>
</header>

<p class="status">{status}</p>

<main>

<section id="docket">
  <div class="head">
    <div><h2>The docket</h2>
      <p class="note">Every bar starts at the red NOW line; hatched bars to the left have overrun.</p></div>
    <span class="tag">{len(live)} live cases</span>
  </div>
  <div class="docket">
    <div class="axis">
      <div class="axis-pad"></div>
      <div class="axis-track">{ticks}<div class="now" style="left:{_pct(_now(), lo, hi):.3f}%"></div></div>
      <div class="chipcell axis-chip"><span class="tag" style="font-size:.58rem">§35.160</span></div>
    </div>
    {rows}
    <div class="legend">
      <span><i class="i-notice"></i>notice deadline (meeting −48h)</span>
      <span><i class="i-meet"></i>meeting</span>
      <span><i class="i-now"></i>now</span>
      <span><i class="i-warn"></i>bar = time left to the deadline</span>
      <span><i class="i-bad"></i>bar past NOW = overrun</span>
      <span>✓ met</span>
    </div>
  </div>
</section>

<section id="awaiting">
  <div class="head">
    <div><h2>Awaiting a human</h2>
      <p class="note">The agent stops rather than guessing. These are the cases it will not
      advance on its own.</p></div>
    <span class="tag">{len(awaiting)} waiting</span>
  </div>
  <div class="cards">{cards}</div>
</section>

<section>
  <div class="head">
    <div><h2>Provider responses</h2>
      <p class="note">The agent cannot confirm its own requests — that would let it manufacture
      the evidence it later verifies against. Confirmations are operator actions, taken here.</p></div>
    <span class="tag">{len(pending)} pending</span>
  </div>
  {ops}
  <p class="opstatus" id="opstatus" role="status" aria-live="polite"></p>
  <div class="disc"><strong>Simulated:</strong> the provider directory is six seeded fixtures, not
  real vendors, and no message reaches anyone. Meetings, bodies, dates, agenda documents,
  cancellations and every statutory deadline above are real.</div>
</section>

<section>
  <div class="head">
    <div><h2>Case files</h2>
      <p class="note">What the agent actually did, step by step, with timestamps and content
      hashes. Each step records a content hash rather than the content — tamper-evident.</p></div>
    <span class="tag">{len(cases)} cases</span>
  </div>
  {files}
</section>

</main>

<footer>
  <div><h3>Statutory basis</h3>
    28 CFR §35.160 — effective communication, in force since 26 July 1991, no phase-in.<br>
    28 CFR §35.200 — WCAG 2.1 AA conformance, due 2027-04-26 for entities serving 50,000+,
    otherwise 2028-04-26 (as extended).</div>
  <div><h3>Built with</h3>
    Strands Agents SDK on Amazon Bedrock AgentCore. Cedar policy as the authority layer —
    the agent cannot close a case it did not verify.</div>
  <div><h3>Data</h3>
    Live Legistar public APIs, no key required. <a href="/api/cases">JSON</a> ·
    <a href="https://github.com/hafsa-usmani/accessflow" rel="noopener">GitHub</a></div>
</footer>

</div>
<script>
document.querySelectorAll('button[data-req]').forEach(function(b){{
  b.addEventListener('click', async function(){{
    var s=document.getElementById('opstatus'), g=b.closest('.acts');
    g.querySelectorAll('button').forEach(function(x){{x.disabled=true}});
    s.textContent='Recording operator response…';
    try{{
      var r=await fetch('/api/simulate-provider-response',{{method:'POST',
        headers:{{'Content-Type':'application/json'}},
        body:JSON.stringify({{request_id:b.dataset.req,response_type:b.dataset.r}})}});
      if(!r.ok) throw new Error('HTTP '+r.status);
      s.textContent='Recorded. The agent has been re-queued for this case — reloading shortly.';
      setTimeout(function(){{location.reload()}},4000);
    }}catch(e){{
      s.textContent='Failed: '+e.message;
      g.querySelectorAll('button').forEach(function(x){{x.disabled=false}});
    }}
  }});
}});
</script>
</body>
</html>"""
