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

FOUNDING_URL = os.environ.get("FOUNDING_CHECKOUT_URL", "#pricing")
GROWTH_URL = os.environ.get("GROWTH_CHECKOUT_URL", "#pricing")
PRO_URL = os.environ.get("PRO_CHECKOUT_URL", "#pricing")
CONTACT = os.environ.get("CONTACT_EMAIL", "hello@accessproof.eu")
SITE_URL = os.environ.get("SITE_URL", "https://accessproof-eu.onrender.com").rstrip("/")

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
    "report_view", "sample_view", "seo_page_view",
    "checkout_click_founding", "checkout_click_growth", "checkout_click_pro",
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
.urgent{background:#fff5f0;border:1px solid #f3c8b3;border-radius:12px;padding:13px 16px;margin:18px auto;max-width:660px;font-size:14px;color:#7a3210;text-align:center}
.urgent strong{color:#a8350c}
.btn.alt{background:#fff;color:#101418;border:1.5px solid #cdd3da}
.btn.alt:hover{background:#f4f6f8}
.tier.solo{max-width:440px;margin:22px auto}
.badge{display:inline-block;background:#eef4ff;color:#1463ff;border-radius:999px;padding:3px 11px;font-size:12px;font-weight:700;margin-bottom:8px}
.faq details{border-bottom:1px solid #eef0f2;padding:12px 2px}
.faq summary{font-weight:600;cursor:pointer}
.faq p{color:#475;margin:.5em 0 0}
.cap{background:#f6f8fa;border-left:3px solid #1463ff;border-radius:0 10px 10px 0;padding:14px 18px;margin:14px 0;font-size:17px}
.seo-cta{display:flex;gap:10px;max-width:560px;margin:22px 0}
.seo-cta input{flex:1;padding:14px 15px;border:1.5px solid #cdd3da;border-radius:10px;font-size:16px}
.crumb{font-size:13px;color:#8a939c;margin:8px 0}
@media(max-width:680px){.grid3,.price{grid-template-columns:1fr}.hero h1{font-size:30px}.scanbox,.seo-cta{flex-direction:column}}
"""


def page(body: str, title="AccessProof EU - independent Shopify accessibility evidence",
         desc="Free Shopify EAA readiness check. Independent, dated accessibility evidence "
              "reports and monthly monitoring for stores selling to the EU.",
         canonical="/", extra_head="") -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc)}">
<link rel="canonical" href="{SITE_URL}{canonical}">
<meta property="og:title" content="{html.escape(title)}">
<meta property="og:description" content="{html.escape(desc)}">
<meta property="og:type" content="website"><meta property="og:url" content="{SITE_URL}{canonical}">
{extra_head}<style>{BASE_CSS}</style></head><body>
<div class="wrap"><div class="nav"><div class="brand"><a href="/" style="color:inherit">AccessProof&nbsp;EU</a></div>
<div><a href="/#how">How&nbsp;it&nbsp;works</a> &nbsp; <a href="/shopify-eaa-readiness-check">EAA&nbsp;check</a> &nbsp; <a href="/#pricing">Pricing</a> &nbsp; <a href="/sample-report">Sample</a></div></div>
{body}
<footer>AccessProof EU &middot; independent automated accessibility monitoring &amp; dated evidence reports
&middot; <a href="mailto:{CONTACT}">{CONTACT}</a><br>
<span class="muted"><a href="/shopify-eaa-readiness-check">EAA readiness</a> &middot;
<a href="/eaa-compliance-shopify">EAA &amp; Shopify</a> &middot;
<a href="/bfsg-shopify">BFSG (Germany)</a> &middot;
<a href="/shopify-accessibility-monitoring">Monitoring</a></span><br>
<span class="muted">{DISCLAIMER}</span>
</footer></div></body></html>"""


def landing() -> str:
    jsonld = ('<script type="application/ld+json">'
              '{"@context":"https://schema.org","@type":"SoftwareApplication",'
              '"name":"AccessProof EU","applicationCategory":"BusinessApplication",'
              '"operatingSystem":"Web","offers":{"@type":"Offer","price":"0","priceCurrency":"USD",'
              '"description":"Free Shopify accessibility (EAA) readiness scan"},'
              '"description":"Independent, dated accessibility evidence reports and monthly '
              'monitoring for Shopify stores selling to the EU under the European Accessibility Act."}'
              '</script>')
    body = f"""
<div class="hero">
  <span class="badge">For Shopify stores selling to the EU</span>
  <h1>Free Shopify EAA readiness check</h1>
  <p>Get a dated accessibility evidence report for your store in under a minute.
     <strong>No overlay. No install. No legal advice.</strong></p>
  <form class="scanbox" action="/scan" method="get">
    <input name="url" placeholder="yourstore.com" autocomplete="off" required>
    <button class="btn" type="submit">Scan my store free</button>
  </form>
  <div class="muted">Independent, third-party scan &middot; real findings you can act on &middot;
     <a href="/sample-report">see a sample report</a></div>
</div>

<div class="urgent">&#9888; <strong>EU accessibility enforcement is here.</strong> A French court has
  ordered a major retailer to make its site accessible or pay a fine for every day it is late, and
  regulators in several EU countries are now inspecting online stores. Independent, dated proof of
  where your store stands puts you on far stronger footing &mdash; we give you that proof.</div>

<div id="how" class="grid3">
  <div class="card"><h3>1 &middot; Scan</h3>Enter your storefront URL. We run high-confidence automated
     checks (missing alt text, unlabeled fields, nameless buttons, page language and more).</div>
  <div class="card"><h3>2 &middot; Monitor</h3>We re-scan every month, track regressions, and flag new
     high-severity issues after a theme or app change.</div>
  <div class="card"><h3>3 &middot; Document</h3>You get a dated evidence report showing the accessibility
     work you are doing &mdash; an independent, time-stamped record for your team.</div>
</div>

<h2 id="pricing">Pricing</h2>
<div class="tier fav solo">
  <span class="badge">Founding offer</span>
  <div>Founding Monitor</div><div class="amt">$29<span style="font-size:15px">/mo</span></div>
  <p>Monthly automated scan + a dated evidence report for one Shopify store, with regression alerts.
     Cancel anytime. Founding price while we refine the product.</p>
  <a class="btn" href="/go/founding">Start monitoring &mdash; $29/mo</a>
  <div class="muted" style="margin-top:10px">Or <a href="/scan">run the free scan first</a>.</div>
</div>
<p class="muted" style="text-align:center">Bigger store or more frequent checks? Growth and Pro plans
   arrive as we grow. AccessProof is an independent evidence layer &mdash; not an overlay widget, and
   not legal advice. It helps you find, fix and document accessibility.</p>

<div class="tier" style="margin:18px 0 10px;border-style:dashed">
  <strong>Run a Shopify agency or manage multiple stores?</strong>
  <p class="muted" style="font-size:15px;color:#475">Add a recurring accessibility evidence service to your
     care plans: independent dated reports across your whole client portfolio, in one place.
     Portfolio plans from $499/mo.</p>
  <a class="btn alt" href="/go/agency">Talk to us about portfolio plans</a></div>

<p class="crumb">Learn more: <a href="/shopify-eaa-readiness-check">Shopify EAA readiness check</a> &middot;
  <a href="/eaa-compliance-shopify">EAA compliance for Shopify</a> &middot;
  <a href="/bfsg-shopify">BFSG for Shopify (Germany)</a> &middot;
  <a href="/shopify-accessibility-monitoring">Shopify accessibility monitoring</a></p>
"""
    return page(body, extra_head=jsonld)


def report_page(result: dict, banner: str = "") -> str:
    rows = ""
    for i in result["issues"]:
        rows += f"""<tr>
          <td><span class="sev" style="background:{SEV_COLOR.get(i['severity'],'#666')}">{i['severity']}</span></td>
          <td><strong>{html.escape(i['title'])}</strong><br><span class="muted">{html.escape(i.get('impact',''))}</span></td>
          <td>{html.escape(i.get('wcag','-'))}<br><span class="muted">conf: {i['confidence']}</span></td>
          <td>{html.escape(i.get('fix',''))}</td></tr>"""
    body = f"""
<p><a href="/">&larr; Scan another store</a></p>
{banner}
<div class="scoreband"><div class="score">{result['score']}<span style="font-size:18px">/100</span></div>
  <div><strong>{html.escape(result['final_url'])}</strong><br>
  <span class="muted">Scanned {result['scanned_at']} &middot; {result['issue_count']} issue group(s) &middot;
  {len(result['outreach_findings'])} high-confidence</span></div></div>
<table><thead><tr><th>Severity</th><th>Issue &amp; impact</th><th>WCAG</th><th>Suggested fix</th></tr></thead>
<tbody>{rows or '<tr><td colspan=4>No high-confidence automated issues found on this page. Nice. Deeper pages may still have issues &mdash; monthly monitoring catches regressions.</td></tr>'}</tbody></table>
<div class="disc">{DISCLAIMER}</div>
<div class="tier fav" style="margin:8px 0 30px"><strong>Keep this monitored.</strong>
  AccessProof re-scans monthly, tracks regressions and generates a dated, independent evidence report.
  <div style="margin-top:12px"><a class="btn" href="/go/founding">Start monitoring &mdash; $29/mo</a>
  &nbsp; <a class="btn alt" href="/sample-report">See a sample report</a></div></div>
"""
    return page(body, title=f"Accessibility report - {result['final_url']}",
                desc="Automated accessibility scan result with WCAG references and suggested fixes.",
                canonical="/scan")


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
        "founding": FOUNDING_URL,
        "growth": GROWTH_URL,
        "pro": PRO_URL,
        "agency": f"mailto:{CONTACT}?subject=AccessProof%20portfolio%20plan",
    }
    events = {"founding": "checkout_click_founding", "growth": "checkout_click_growth",
              "pro": "checkout_click_pro", "agency": "agency_cta_click"}
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


@app.get("/sample-report")
def sample_report():
    log_event("sample_view", "/sample-report")
    demo = {
        "url": "demo-store.example", "final_url": "https://demo-store.example",
        "scanned_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "score": 71, "issue_count": 4,
        "issues": [
            dict(severity="critical", confidence="high", outreach_safe=True,
                 wcag="1.1.1 Non-text Content", title="7 images missing alt text",
                 impact="Screen-reader shoppers hear nothing for these images; on product images this can block a purchase.",
                 fix="Add descriptive alt text to content images; use alt=\"\" for decorative ones. In Shopify, product image alt text is editable per image in the admin.",
                 count=7),
            dict(severity="serious", confidence="high", outreach_safe=True,
                 wcag="4.1.2 / 3.3.2 Labels", title="2 form fields without an accessible label",
                 impact="Header search and newsletter inputs can't be identified by assistive tech.",
                 fix="Associate each field with a <label for> or add an aria-label.", count=2),
            dict(severity="serious", confidence="high", outreach_safe=True,
                 wcag="2.4.4 / 4.1.2 Name, Role, Value", title="3 links/buttons with no accessible name",
                 impact="Icon-only cart and menu controls are announced as just \"button\".",
                 fix="Add visually-hidden text or an aria-label, e.g. <button aria-label=\"Open cart\">.",
                 count=3),
            dict(severity="serious", confidence="high", outreach_safe=True,
                 wcag="3.1.1 Language of Page", title="Page is missing a language declaration",
                 impact="Screen readers may read content with the wrong pronunciation rules.",
                 fix="Set a language on the root element, e.g. <html lang=\"en\">; in Shopify this lives in theme.liquid.",
                 count=1),
        ],
    }
    demo["outreach_findings"] = [i for i in demo["issues"] if i["outreach_safe"]]
    banner = ('<div class="badge">Sample report</div><p class="muted" style="margin-top:0">This is an '
              'anonymized example of the dated evidence report you receive. '
              '<a href="/scan">Run the free scan</a> to see your own store.</p>')
    return Response(report_page(demo, banner=banner), status=200)


# ===== B037: SEO / GEO content pages (answer-capsule + table + FAQ schema) =====
def _faq_jsonld(faqs) -> str:
    items = ",".join(
        '{"@type":"Question","name":%s,"acceptedAnswer":{"@type":"Answer","text":%s}}'
        % (json.dumps(q), json.dumps(a)) for q, a in faqs)
    return ('<script type="application/ld+json">'
            '{"@context":"https://schema.org","@type":"FAQPage","mainEntity":['
            + items + ']}</script>')


def seo_page(slug, title, desc, h1, capsule_html, table_head, table_rows, faqs,
             intro_html="") -> str:
    rows = "".join(
        f"<tr><td>{html.escape(a)}</td><td>{html.escape(b)}</td></tr>" for a, b in table_rows)
    faq_html = "".join(
        f"<details><summary>{html.escape(q)}</summary><p>{html.escape(a)}</p></details>"
        for q, a in faqs)
    body = f"""
<p class="crumb"><a href="/">Home</a> &rsaquo; {html.escape(h1)}</p>
<h1>{html.escape(h1)}</h1>
<div class="cap">{capsule_html}</div>
{intro_html}
<form class="seo-cta" action="/scan" method="get">
  <input name="url" placeholder="yourstore.com" autocomplete="off" required>
  <button class="btn" type="submit">Run the free EAA scan</button>
</form>
<table><thead><tr><th>{html.escape(table_head[0])}</th><th>{html.escape(table_head[1])}</th></tr></thead>
<tbody>{rows}</tbody></table>
<h2>Frequently asked questions</h2>
<div class="faq">{faq_html}</div>
<div class="disc">{DISCLAIMER}</div>
<p style="margin:18px 0 30px"><a class="btn" href="/scan">Scan your store free</a>
   &nbsp; <a class="btn alt" href="/sample-report">See a sample report</a></p>
"""
    log_event("seo_page_view", "/" + slug)
    return page(body, title=title, desc=desc, canonical="/" + slug, extra_head=_faq_jsonld(faqs))


@app.get("/shopify-eaa-readiness-check")
def seo_eaa_check():
    return seo_page(
        "shopify-eaa-readiness-check",
        "Shopify EAA Readiness Check (Free) - AccessProof EU",
        "Free check of your Shopify store for common European Accessibility Act (EAA / WCAG) "
        "issues. Get a dated, independent evidence report in under a minute.",
        "Shopify EAA readiness check",
        "The <strong>European Accessibility Act (EAA)</strong> has applied to most online stores "
        "selling to EU consumers since <strong>28 June 2025</strong>. This free check scans your "
        "Shopify storefront for common, high-confidence accessibility issues (tied to WCAG and "
        "EN 301 549) and gives you a dated, independent evidence report. It is not legal advice.",
        ("What we check", "Why it matters for the EAA"),
        [("Image alt text (WCAG 1.1.1)", "Screen-reader shoppers can't perceive product images without it"),
         ("Form field labels (WCAG 4.1.2)", "Search, newsletter and checkout fields must be identifiable"),
         ("Link & button names (WCAG 4.1.2)", "Icon-only cart and menu controls need accessible names"),
         ("Page language (WCAG 3.1.1)", "Screen readers need the page language to pronounce content"),
         ("Page title (WCAG 2.4.2)", "Orientation and tab-switching rely on a meaningful title")],
        [("Does the EAA apply to my Shopify store?",
          "If you sell goods or services to consumers in the EU and are not a micro-enterprise "
          "(fewer than 10 staff and under EUR 2M turnover), the EAA generally applies. Confirm your "
          "own situation with a qualified advisor."),
         ("Is an automated scan enough for the EAA?",
          "No. Automated scans catch many common issues quickly but cannot find every barrier. The "
          "EAA expects ongoing effort, and a dated monitoring record helps you show that work."),
         ("Does AccessProof make my store compliant?",
          "No. AccessProof is an independent evidence and monitoring layer. It helps you find, fix "
          "and document accessibility - it does not guarantee compliance and is not legal advice."),
         ("How is this different from an accessibility overlay or widget?",
          "Overlays sit on top of your site, and overlay compliance claims have been criticised by "
          "the FTC and EU regulators. AccessProof does not modify your site - it gives you an "
          "independent, dated record of issues and progress.")],
        "<p>Run the scan to see your store's high-confidence issues with WCAG references and "
        "suggested fixes, then keep an independent dated record as you remediate.</p>")


@app.get("/eaa-compliance-shopify")
def seo_eaa_compliance():
    return seo_page(
        "eaa-compliance-shopify",
        "EAA Compliance for Shopify Stores - What to Know - AccessProof EU",
        "What the European Accessibility Act means for Shopify stores selling to the EU, and how "
        "independent dated evidence and monitoring help you show good-faith effort.",
        "EAA compliance for Shopify stores",
        "The <strong>European Accessibility Act</strong> sets accessibility requirements for "
        "e-commerce sold to EU consumers, in force since <strong>28 June 2025</strong>. Shopify "
        "gives you the storefront; making it accessible, and keeping a record of that work, is on "
        "the merchant. AccessProof provides an independent, dated evidence trail. Not legal advice.",
        ("Topic", "What to know"),
        [("In force since", "28 June 2025, across the EU"),
         ("Who it covers", "Online stores selling to EU consumers (micro-enterprises may be exempt)"),
         ("Standard referenced", "WCAG 2.1 AA via EN 301 549 (moving toward WCAG 2.2)"),
         ("What helps your case", "An accessibility statement, ongoing monitoring, dated evidence of remediation"),
         ("Enforcement so far", "Court orders, regulator inspections and law-firm warning letters have begun")],
        [("Is Shopify EAA-compliant out of the box?",
          "No platform makes a store automatically compliant - it depends on your theme, apps, "
          "content and images. You are responsible for your storefront's accessibility."),
         ("What should I keep as evidence?",
          "An accessibility statement, recent scan or audit results, a record of fixes over time, and "
          "a channel for accessibility feedback. AccessProof produces the dated scan/monitoring part."),
         ("Will an overlay widget make me compliant?",
          "Regulators and the FTC have criticised overlay compliance claims, and the EU Commission has "
          "said overlays are not an adequate substitute for fixing issues at the source."),
         ("Does AccessProof guarantee compliance?",
          "No. It is an independent evidence and monitoring tool, not legal advice, and does not "
          "guarantee compliance.")],
        "")


@app.get("/bfsg-shopify")
def seo_bfsg():
    return seo_page(
        "bfsg-shopify",
        "BFSG for Shopify (Germany's EAA) - AccessProof EU",
        "The BFSG is Germany's transposition of the European Accessibility Act. What it means for "
        "Shopify stores selling to German consumers, and how dated evidence helps.",
        "BFSG for Shopify stores (Germany)",
        "The <strong>BFSG (Barrierefreiheitsstaerkungsgesetz)</strong> is Germany's law implementing "
        "the European Accessibility Act, in force since <strong>28 June 2025</strong>. Germany is a "
        "notable market because private law firms have begun sending accessibility-related warning "
        "letters. An independent, dated evidence trail helps you show good-faith effort. Not legal advice.",
        ("Topic", "What to know"),
        [("What BFSG is", "Germany's national implementation of the EU Accessibility Act"),
         ("In force since", "28 June 2025"),
         ("Who it covers", "Online stores selling to consumers in Germany (small/micro exemptions may apply)"),
         ("German specific", "Private law-firm warning letters (Abmahnung) are an additional exposure"),
         ("What helps", "Accessibility statement + ongoing monitoring + dated evidence of fixes")],
        [("Is BFSG different from the EAA?",
          "BFSG is Germany's national version of the same EU Accessibility Act, so the accessibility "
          "expectations are aligned; Germany's enforcement culture is the main difference."),
         ("What is an Abmahnung?",
          "A formal warning letter, often from a law firm, asserting a violation. Keeping dated "
          "evidence of your accessibility work helps you demonstrate good-faith effort."),
         ("Does AccessProof provide legal advice?",
          "No. AccessProof is an independent evidence and monitoring layer, not a law firm, and it "
          "does not guarantee compliance.")],
        "")


@app.get("/shopify-accessibility-monitoring")
def seo_monitoring():
    return seo_page(
        "shopify-accessibility-monitoring",
        "Shopify Accessibility Monitoring & Evidence Reports - AccessProof EU",
        "Continuous accessibility monitoring for Shopify stores: monthly automated scans and dated, "
        "independent evidence reports that track regressions over time.",
        "Shopify accessibility monitoring",
        "A one-time audit goes stale the moment you change a theme, add an app, or upload new "
        "products. <strong>Continuous monitoring</strong> re-scans your Shopify store on a schedule, "
        "tracks regressions, and produces a <strong>dated, independent evidence report</strong> of "
        "the accessibility work you are doing. Not legal advice; not an overlay.",
        ("Capability", "What you get"),
        [("Monthly automated scan", "High-confidence checks across your storefront"),
         ("Regression tracking", "Catch new high-severity issues after a theme or app change"),
         ("Dated evidence report", "A time-stamped, independent record of issues and progress"),
         ("No overlay", "We don't modify your site; we measure and document it"),
         ("Portfolio option", "Agencies can monitor many client stores from one place")],
        [("Why monitor instead of a one-time audit?",
          "Accessibility drifts as your store changes. Monitoring keeps your evidence current and "
          "shows ongoing effort, which is what the EAA expects."),
         ("What is in the evidence report?",
          "A dated score, the issues found with WCAG references and suggested fixes, and your history "
          "over time so you can show progress."),
         ("Can agencies monitor multiple stores?",
          "Yes. Portfolio plans cover multiple client stores with an independent dated report for each."),
         ("Does monitoring guarantee compliance?",
          "No. It is an independent evidence tool, not legal advice, and does not guarantee compliance.")],
        "")


@app.get("/sitemap.xml")
def sitemap():
    paths = ["/", "/shopify-eaa-readiness-check", "/eaa-compliance-shopify",
             "/bfsg-shopify", "/shopify-accessibility-monitoring", "/sample-report"]
    today = datetime.date.today().isoformat()
    urls = "".join(
        f"<url><loc>{SITE_URL}{p}</loc><lastmod>{today}</lastmod>"
        f"<changefreq>weekly</changefreq></url>" for p in paths)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + urls + "</urlset>")
    return Response(xml, mimetype="application/xml")


@app.get("/robots.txt")
def robots():
    return Response(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n",
                    mimetype="text/plain")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)