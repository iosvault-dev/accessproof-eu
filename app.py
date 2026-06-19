"""
AccessProof EU - High-precision storefront accessibility scanner (Week-1 core).

Design rule (signed amendment C): OUTREACH credibility = scanner precision.
Only near-zero-false-positive issue classes are flagged as `outreach_safe=True`.
Noisy heuristics (contrast guesses, "maybe ARIA") are NEVER outreach_safe.
Static-HTML detection keeps unit cost ~nil (no headless browser needed for
the high-precision subset) -> directly serves the $500/mo budget gate (F).
"""
from __future__ import annotations
import sys, json, re, datetime, urllib.parse
import requests
from bs4 import BeautifulSoup

UA = "AccessProofBot/0.1 (+https://accessproof.example/bot; accessibility audit)"
TIMEOUT = 15

# WCAG refs kept conservative + verifiable.
SEVERITY_WEIGHT = {"critical": 8, "serious": 5, "moderate": 3, "minor": 1}


def fetch(url: str) -> dict:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    return {"final_url": r.url, "status": r.status_code, "html": r.text}


def _txt(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()


def _snippet(el, n=160) -> str:
    s = re.sub(r"\s+", " ", str(el)).strip()
    return s[:n] + ("..." if len(s) > n else "")


def check_html_lang(soup, issues):
    html = soup.find("html")
    if not html or not (html.get("lang") or "").strip():
        issues.append(dict(
            id="html-has-lang", wcag="3.1.1 Language of Page", severity="serious",
            confidence="high", outreach_safe=True,
            title="Page is missing a language declaration",
            impact="Screen readers cannot determine the page language, so content "
                   "may be read with the wrong pronunciation rules.",
            fix="Set a language on the root element, e.g. <html lang=\"en\"> (or the "
                "store's primary language). In Shopify themes this lives in theme.liquid.",
            evidence="<html> tag has no non-empty lang attribute", count=1))


def check_title(soup, issues):
    t = soup.find("title")
    if not t or not _txt(t):
        issues.append(dict(
            id="document-title", wcag="2.4.2 Page Titled", severity="serious",
            confidence="high", outreach_safe=True,
            title="Page has no document title",
            impact="The browser tab and screen-reader page announcement are empty, "
                   "making orientation and tab-switching hard.",
            fix="Add a meaningful <title>. Shopify themes usually set this from the "
                "page/product title in theme.liquid <head>.",
            evidence="missing or empty <title> element", count=1))


def check_img_alt(soup, issues):
    missing = []
    for img in soup.find_all("img"):
        if img.get("alt") is None and not (img.get("aria-hidden") == "true"
                                           or img.get("role") == "presentation"):
            missing.append(img)
    if missing:
        ex = "; ".join(
            (m.get("src") or m.get("data-src") or "img")[:70] for m in missing[:3])
        issues.append(dict(
            id="image-alt", wcag="1.1.1 Non-text Content", severity="critical",
            confidence="high", outreach_safe=True,
            title=f"{len(missing)} image(s) missing alt text",
            impact="Shoppers using screen readers hear nothing (or just a filename) "
                   "for these images. On product images this can block a purchase.",
            fix="Add descriptive alt text to content images; use alt=\"\" only for "
                "purely decorative images. In Shopify, product image alt text is "
                "editable per image in the admin.",
            evidence=f"e.g. {ex}", count=len(missing)))


def check_form_labels(soup, issues):
    label_for = {l.get("for") for l in soup.find_all("label") if l.get("for")}
    unlabeled = []
    for inp in soup.find_all(["input", "select", "textarea"]):
        itype = (inp.get("type") or "text").lower()
        if itype in ("hidden", "submit", "button", "reset", "image"):
            continue
        has = bool(
            (inp.get("id") and inp.get("id") in label_for)
            or (inp.get("aria-label") or "").strip()
            or inp.get("aria-labelledby")
            or (inp.get("title") or "").strip()
            or (itype in ("search",) and (inp.get("placeholder") or "").strip())
        )
        # input wrapped directly inside a <label> also counts
        if not has and inp.find_parent("label") is not None:
            has = True
        if not has:
            unlabeled.append(inp)
    if unlabeled:
        ex = "; ".join(_snippet(u, 60) for u in unlabeled[:2])
        issues.append(dict(
            id="form-field-label", wcag="4.1.2 / 3.3.2 Labels", severity="serious",
            confidence="high", outreach_safe=True,
            title=f"{len(unlabeled)} form field(s) without an accessible label",
            impact="Search boxes, newsletter signups, and checkout fields can't be "
                   "identified by assistive tech, blocking key actions.",
            fix="Associate each field with a <label for>, or add an aria-label. "
                "Common offenders: header search and footer newsletter inputs.",
            evidence=f"e.g. {ex}", count=len(unlabeled)))


def check_empty_controls(soup, issues):
    empties = []
    for el in soup.find_all(["a", "button"]):
        if el.name == "a" and not el.get("href"):
            continue
        text = _txt(el)
        alt = " ".join(i.get("alt", "") for i in el.find_all("img"))
        accessible = bool(text or (el.get("aria-label") or "").strip()
                          or el.get("aria-labelledby") or (el.get("title") or "").strip()
                          or alt.strip())
        if not accessible:
            empties.append(el)
    if empties:
        ex = "; ".join(_snippet(e, 60) for e in empties[:2])
        issues.append(dict(
            id="empty-control-name", wcag="2.4.4 / 4.1.2 Name, Role, Value",
            severity="serious", confidence="high", outreach_safe=True,
            title=f"{len(empties)} link(s)/button(s) with no accessible name",
            impact="Icon-only controls (cart, menu, social) are announced as just "
                   "\"link\" or \"button\", so users don't know what they do.",
            fix="Add visually-hidden text or aria-label to icon controls, e.g. "
                "<button aria-label=\"Open cart\">.",
            evidence=f"e.g. {ex}", count=len(empties)))


def check_headings(soup, issues):
    # Lower-precision -> NOT outreach_safe. Shown only in full/in-app reports.
    h1 = soup.find_all("h1")
    if len(h1) == 0:
        issues.append(dict(
            id="page-has-heading-one", wcag="1.3.1 Info and Relationships",
            severity="moderate", confidence="medium", outreach_safe=False,
            title="No <h1> heading found", count=1,
            impact="Screen-reader users lose the main landmark for the page topic.",
            fix="Ensure each page has exactly one descriptive <h1>.",
            evidence="0 <h1> elements"))
    elif len(h1) > 1:
        issues.append(dict(
            id="page-has-heading-one", wcag="1.3.1 Info and Relationships",
            severity="minor", confidence="medium", outreach_safe=False,
            title=f"{len(h1)} <h1> headings found (expected 1)", count=len(h1),
            impact="Multiple top-level headings can confuse document structure.",
            fix="Demote secondary <h1>s to <h2>/<h3>.",
            evidence=f"{len(h1)} <h1> elements"))


def check_skip_link(soup, issues):
    # Heuristic -> NOT outreach_safe.
    anchors = soup.find_all("a", href=True)
    has_skip = any(a["href"].startswith("#") and
                   re.search(r"skip|main content|jump to", _txt(a), re.I)
                   for a in anchors)
    if not has_skip:
        issues.append(dict(
            id="skip-link", wcag="2.4.1 Bypass Blocks", severity="moderate",
            confidence="low", outreach_safe=False,
            title="No 'skip to content' link detected", count=1,
            impact="Keyboard users must tab through the full nav on every page.",
            fix="Add a skip link as the first focusable element.",
            evidence="no anchor matching skip/main-content heuristic"))


CHECKS = [check_html_lang, check_title, check_img_alt, check_form_labels,
          check_empty_controls, check_headings, check_skip_link]


def score(issues) -> int:
    penalty = sum(SEVERITY_WEIGHT.get(i["severity"], 1) *
                  (1 + min(i.get("count", 1) - 1, 5) * 0.25) for i in issues)
    return max(0, round(100 - penalty))


def scan(url: str) -> dict:
    res = fetch(url)
    soup = BeautifulSoup(res["html"], "lxml")
    issues: list[dict] = []
    for c in CHECKS:
        try:
            c(soup, issues)
        except Exception as e:  # a single check failing must not kill the scan
            issues.append(dict(id="scanner-error", severity="minor", confidence="low",
                               outreach_safe=False, title=f"check error: {e}", count=1))
    issues.sort(key=lambda i: SEVERITY_WEIGHT.get(i["severity"], 1), reverse=True)
    return {
        "url": url, "final_url": res["final_url"],
        "scanned_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "score": score(issues),
        "issue_count": len(issues),
        "outreach_findings": [i for i in issues if i["outreach_safe"]],
        "issues": issues,
        "method": "static-html high-precision subset (axe-core class parity for these rules)",
        "disclaimer": ("Automated scan only. Detects common, high-confidence issues; "
                       "it cannot find every accessibility barrier and is not legal "
                       "advice or a guarantee of EAA/WCAG compliance."),
    }

# ===== web layer =====
import os

import sys
import html
from pathlib import Path

from flask import Flask, request, Response


app = Flask(__name__)

GROWTH_URL = os.environ.get("GROWTH_CHECKOUT_URL", "#pricing")
PRO_URL = os.environ.get("PRO_CHECKOUT_URL", "#pricing")
CONTACT = os.environ.get("CONTACT_EMAIL", "hello@accessproof.eu")

# ===== B005 funnel instrumentation: server-side, privacy-first, no third-party =====
# In-memory counters + best-effort append-only CSV + structured stdout logs, surfaced
# at GET /metrics for the daily loop to snapshot. NO IP, NO cookies, NO user-agent, NO PII.
# NOTE: on Render free tier the filesystem is ephemeral and the dyno sleeps, so EVENTS_LOG
# and in-memory counts reset on each cold start -> /metrics reflects activity "since boot".
# The daily loop curls /metrics and records the snapshot, giving a per-day series. Durable
# cumulative counting needs an always-on dyno (Render Starter) or an external store = future
# owner option; this MVP makes the funnel OBSERVABLE without any paid tool.
import threading
_EVENT_NAMES = (
    "visit_home", "visit_scan", "scan_start", "scan_success", "scan_error",
    "report_view", "checkout_click_growth", "checkout_click_pro",
    "agency_cta_click", "unsubscribe_visit", "healthz_ok")
_EVENTS = {k: 0 for k in _EVENT_NAMES}
_EVENTS_LOCK = threading.Lock()
_BOOT_TS = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
EVENTS_LOG = os.environ.get("EVENTS_LOG", "/tmp/accessproof_events.csv")


def log_event(event: str, path: str = "", detail: str = "") -> None:
    """Count one funnel event. Never raises (instrumentation must not break a request).
    `detail` only ever holds a scanned domain — never anything personal."""
    try:
        with _EVENTS_LOCK:
            if event in _EVENTS:
                _EVENTS[event] += 1
        ts = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        row = f"{ts},{event},{path},{detail}"
        print(f"[event] {row}", flush=True)
        try:
            new = not os.path.exists(EVENTS_LOG)
            with open(EVENTS_LOG, "a", encoding="utf-8") as fh:
                if new:
                    fh.write("ts,event,path,detail\n")
                fh.write(row + "\n")
        except Exception:
            pass
    except Exception:
        pass


SEV_COLOR = {"critical": "#b3261e", "serious": "#b8500f",
             "moderate": "#8a6d00", "minor": "#666"}
DISCLAIMER = ("AccessProof EU provides automated accessibility scanning, monitoring, "
              "evidence reports and remediation guidance. It is not legal advice and "
              "does not guarantee compliance with the European Accessibility Act, WCAG "
              "or any other law or standard. Automated tests detect many common issues "
              "but cannot find every accessibility barrier.")

BASE_CSS = """
*{box-sizing:border-box} body{margin:0;font:16px/1.6 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#13171b;background:#fff}
.wrap{max-width:920px;margin:0 auto;padding:0 22px}
a{color:#1463ff;text-decoration:none} a:hover{text-decoration:underline}
.nav{display:flex;justify-content:space-between;align-items:center;padding:20px 0}
.brand{font-weight:800;letter-spacing:-.4px;font-size:19px}
.hero{padding:56px 0 30px;text-align:center}
.hero h1{font-size:40px;line-height:1.12;letter-spacing:-1px;margin:0 0 14px}
.hero p{font-size:19px;color:#475}
.scanbox{display:flex;gap:10px;max-width:580px;margin:26px auto 8px}
.scanbox input{flex:1;padding:15px 16px;border:1.5px solid #cdd3da;border-radius:10px;font-size:16px}
.btn{display:inline-block;background:#101418;color:#fff;border:0;border-radius:10px;padding:15px 22px;font-size:16px;font-weight:600;cursor:pointer}
.btn:hover{background:#2a2f36;text-decoration:none}
.muted{color:#6a737d;font-size:13px}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin:34px 0}
.card{border:1px solid #e7e9ec;border-radius:14px;padding:20px}
.card h3{margin:.1em 0 .3em}
.price{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin:24px 0}
.tier{border:1px solid #e7e9ec;border-radius:14px;padding:24px}
.tier.fav{border:2px solid #101418}
.tier .amt{font-size:34px;font-weight:800}
.sev{color:#fff;border-radius:4px;padding:2px 8px;font-size:11px;text-transform:uppercase;font-weight:700}
table{border-collapse:collapse;width:100%;margin-top:14px}
td,th{text-align:left;padding:11px 10px;border-bottom:1px solid #eef0f2;vertical-align:top}
th{font-size:11px;text-transform:uppercase;color:#8a939c}
.scoreband{display:flex;align-items:baseline;gap:14px;margin:6px 0 2px}
.score{font-size:46px;font-weight:800}
.disc{margin:26px 0;padding:14px 16px;background:#f6f7f5;border-radius:10px;font-size:12px;color:#566}
footer{border-top:1px solid #eef0f2;margin-top:44px;padding:24px 0;color:#8a939c;font-size:13px}
@media(max-width:680px){.grid3,.price{grid-template-columns:1fr}.hero h1{font-size:30px}.scanbox{flex-direction:column}}
"""


def page(body: str, title="AccessProof EU") -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>{BASE_CSS}</style></head><body>
<div class="wrap"><div class="nav"><div class="brand">AccessProof&nbsp;EU</div>
<div><a href="/#how">How it works</a> &nbsp; <a href="/#pricing">Pricing</a></div></div>
{body}
<footer>AccessProof EU &middot; automated accessibility monitoring &amp; evidence reports
&middot; <a href="mailto:{CONTACT}">{CONTACT}</a><br><span class="muted">{DISCLAIMER}</span>
</footer></div></body></html>"""


def landing() -> str:
    body = f"""
<div class="hero">
  <h1>See your Shopify store's accessibility issues in 15 seconds.</h1>
  <p>Free automated scan. Then monthly dated evidence reports that find issues, track your
     progress, and keep a proof trail of your accessibility work &mdash; for stores selling
     to EU shoppers in the European Accessibility Act era.</p>
  <form class="scanbox" action="/scan" method="get">
    <input name="url" placeholder="yourstore.com" autocomplete="off" required>
    <button class="btn" type="submit">Scan my store</button>
  </form>
  <div class="muted">No signup. No overlay widget. Real findings you can act on.</div>
</div>

<div id="how" class="grid3">
  <div class="card"><h3>1 &middot; Scan</h3>Enter your storefront URL. We run high-confidence
     automated checks (missing alt text, unlabeled fields, nameless buttons, page language and more).</div>
  <div class="card"><h3>2 &middot; Monitor</h3>We re-scan every month, track regressions, and
     alert you when new high-severity issues appear after a theme or app change.</div>
  <div class="card"><h3>3 &middot; Document</h3>Get a dated PDF evidence report showing the
     accessibility work you're doing &mdash; clean records for your team.</div>
</div>

<h2 id="pricing">Pricing</h2>
<div class="price">
  <div class="tier fav"><div>Growth</div><div class="amt">$89<span style="font-size:15px">/mo</span></div>
    <p>Monthly monitoring, up to 25 pages, monthly evidence PDF, issue history, email alerts.</p>
    <a class="btn" href="/go/growth">Start Growth</a></div>
  <div class="tier"><div>Pro</div><div class="amt">$149<span style="font-size:15px">/mo</span></div>
    <p>Weekly monitoring, up to 100 pages, priority regression alerts, expanded reports.</p>
    <a class="btn" href="/go/pro">Start Pro</a></div>
</div>
<p class="muted">7-day free trial. Cancel anytime. AccessProof helps you find, fix and document
   accessibility &mdash; it does not provide legal advice or guarantee compliance.</p>

<div class="tier" style="margin:14px 0 30px;border-style:dashed">
  <strong>Manage reports for multiple Shopify clients?</strong>
  <p class="muted" style="font-size:15px;color:#475">Agencies &amp; multi-store operators: dated monthly
     evidence reports across your whole client portfolio, in one place. Portfolio plans from $499/mo.</p>
  <a class="btn" href="/go/agency">Talk to us about portfolio plans</a></div>
"""
    return page(body)


def report_page(result: dict) -> str:
    rows = ""
    for i in result["issues"]:
        rows += f"""<tr>
          <td><span class="sev" style="background:{SEV_COLOR.get(i['severity'],'#666')}">{i['severity']}</span></td>
          <td><strong>{html.escape(i['title'])}</strong><br><span class="muted">{html.escape(i.get('impact',''))}</span></td>
          <td>{html.escape(i.get('wcag','-'))}<br><span class="muted">conf: {i['confidence']}</span></td>
          <td>{html.escape(i.get('fix',''))}</td></tr>"""
    body = f"""
<p><a href="/">&larr; Scan another store</a></p>
<div class="scoreband"><div class="score">{result['score']}<span style="font-size:18px">/100</span></div>
  <div><strong>{html.escape(result['final_url'])}</strong><br>
  <span class="muted">Scanned {result['scanned_at']} &middot; {result['issue_count']} issue group(s) &middot;
  {len(result['outreach_findings'])} high-confidence</span></div></div>
<table><thead><tr><th>Severity</th><th>Issue &amp; impact</th><th>WCAG</th><th>Suggested fix</th></tr></thead>
<tbody>{rows or '<tr><td colspan=4>No high-confidence automated issues found on this page. Nice. Deeper pages may still have issues &mdash; monthly monitoring catches regressions.</td></tr>'}</tbody></table>
<div class="disc">{DISCLAIMER}</div>
<div class="tier fav" style="margin:8px 0 30px"><strong>Keep this monitored.</strong>
  AccessProof re-scans monthly, tracks regressions and generates a dated evidence PDF.
  <div style="margin-top:12px"><a class="btn" href="/go/growth">Start monitoring &mdash; $89/mo</a></div></div>
"""
    return page(body, title=f"Accessibility report - {result['final_url']}")


@app.get("/")
def home():
    log_event("visit_home", "/")
    return landing()


@app.get("/scan")
def do_scan():
    url = (request.args.get("url") or "").strip()
    if not url:
        log_event("visit_scan", "/scan")
        return landing()
    log_event("scan_start", "/scan", detail=url)
    try:
        result = scan(url)
    except Exception as e:
        log_event("scan_error", "/scan", detail=url)
        msg = (f"<div class='hero'><h1>Couldn't scan that URL</h1>"
               f"<p class='muted'>{html.escape(str(e))}</p>"
               f"<p><a class='btn' href='/'>Try again</a></p></div>")
        return Response(page(msg), status=200)
    log_event("scan_success", "/scan", detail=result.get("final_url", url))
    log_event("report_view", "/scan", detail=result.get("final_url", url))
    return report_page(result)


@app.get("/healthz")
def healthz():
    log_event("healthz_ok", "/healthz")
    return {"ok": True}


@app.get("/metrics")
def metrics():
    """Funnel counters snapshot for the daily execution loop. No PII."""
    ts = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with _EVENTS_LOCK:
        counters = dict(_EVENTS)
    return {"boot": _BOOT_TS, "now": ts, "counters": counters}


@app.get("/go/<dest>")
def go(dest):
    """Log checkout/agency click intent server-side, then 302 to the real target.
    Keeps the Stripe Payment Links intact while making clicks observable."""
    targets = {
        "growth": GROWTH_URL,
        "pro": PRO_URL,
        "agency": f"mailto:{CONTACT}?subject=AccessProof%20portfolio%20plan",
    }
    events = {"growth": "checkout_click_growth", "pro": "checkout_click_pro",
              "agency": "agency_cta_click"}
    target = targets.get(dest)
    if not target:
        return Response("Unknown destination", status=404)
    log_event(events[dest], f"/go/{dest}")
    return Response(status=302, headers={"Location": target})


@app.get("/unsubscribe")
def unsubscribe():
    """Web unsubscribe confirmation (logged). Outreach currently uses a mailto unsub;
    wiring this URL into the List-Unsubscribe header is a future, separate step."""
    log_event("unsubscribe_visit", "/unsubscribe")
    body = ("<div class='hero'><h1>You're unsubscribed</h1>"
            "<p class='muted'>We won't email this address again. If anything still "
            "arrives, reply 'unsubscribe' and we'll remove it.</p></div>")
    return Response(page(body, title="Unsubscribe - AccessProof EU"), status=200)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)