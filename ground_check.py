import io,json,re,sys,httpx
sys.path.insert(0,'.')
from backend.app.tools.legistar import LegistarFeed
from strands import Agent
from backend.app.agents.model import get_model

P="""Below is a real public meeting agenda. List accessibility accommodations it needs.
For EACH one you must copy an EXACT phrase from the agenda text as "quote".
Do not paraphrase. Do not use outside knowledge. JSON only:
{{"a":[{{"type":"...","quote":"exact text from agenda"}}]}}
--- AGENDA ---
{t}"""

def text(u):
    r=httpx.get(u,timeout=45,follow_redirects=True,headers={"User-Agent":"AccessFlow/0.1"})
    r.raise_for_status()
    if r.content[:5].startswith(b"%PDF"):
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(r.content)).pages)
    return re.sub(r"<[^>]+>"," ",r.text)

n=lambda s:re.sub(r"\s+"," ",s or "").strip().lower()
f=LegistarFeed(); ms=[]
for c in f.clients:
    try: ms+=[m for m in f.upcoming(c,60) if m.agenda_url]
    except Exception: pass
    if len(ms)>=6: break

ok=tot=0; profiles=[]
for m in ms[:6]:
    try: t=text(m.agenda_url)
    except Exception as e: print("fetch fail",e); continue
    if len(t.strip())<400: continue
    raw=str(Agent(model=get_model())(P.format(t=t[:12000])))
    g=re.search(r"\{.*\}",raw,re.S)
    if not g: print("PARSE FAIL"); continue
    accs=json.loads(g.group(0)).get("a",[])
    hay=n(t); profiles.append(tuple(sorted(n(a.get("type","")) for a in accs)))
    print(f"\n{m.body_name[:40]} ({m.client})")
    for a in accs:
        tot+=1; q=n(a.get("quote","")); hit=len(q)>12 and q in hay; ok+=hit
        print(f"  {'OK        ' if hit else 'UNGROUNDED'} {a.get('type','?')[:22]:22s} | {(a.get('quote') or '')[:55]}")

print("\n"+"="*60)
print(f"DISTINCT PROFILES : {len(set(profiles))} of {len(profiles)}")
print(f"GROUNDED          : {ok}/{tot} = {ok/tot*100:.0f}%" if tot else "no claims")
print("="*60)
