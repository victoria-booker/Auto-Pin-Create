"""Browser dashboard for the scheduled-pin queue.

GET /dashboard shows queued, posted, failed, and cancelled pins with image
thumbnails, and lets you reschedule, post now, cancel, or requeue them.

Login is a normal password form (DASHBOARD_PASSWORD, falling back to
PIN_API_KEY) that sets a signed cookie lasting SESSION_DAYS, so each device
logs in once and password managers can autofill it. Changing the password
signs every device out. These routes are excluded from the OpenAPI schema so
the GPT Action never sees them.
"""
import hashlib
import hmac
import html
import os
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from api_auth import API_KEY
from pinterest import pin_queue
from pinterest.pin_slots import slot_timezone
from pinterest.pinterest_pins import list_boards

SESSION_COOKIE = "pin_dashboard_session"
SESSION_DAYS = 90
FAILED_LOGIN_DELAY_SECONDS = 1  # slows down password guessing


def _password():
    return os.environ.get("DASHBOARD_PASSWORD") or API_KEY


def _signature(expires):
    # keyed on the password, so changing it invalidates every existing session
    key = hashlib.sha256(f"pin-dashboard-session:{_password()}".encode()).digest()
    return hmac.new(key, str(expires).encode(), hashlib.sha256).hexdigest()


def _new_session_value():
    expires = int(time.time()) + SESSION_DAYS * 86400
    return f"{expires}.{_signature(expires)}"


def _valid_session(value):
    expires, _, signature = (value or "").partition(".")
    if not expires.isdigit() or int(expires) < time.time():
        return False
    return hmac.compare_digest(signature, _signature(int(expires)))


def require_dashboard_login(request: Request) -> None:
    if not _valid_session(request.cookies.get(SESSION_COOKIE)):
        raise HTTPException(status_code=303, headers={"Location": "/dashboard/login"})


router = APIRouter(prefix="/dashboard", include_in_schema=False, dependencies=[Depends(require_dashboard_login)])
# login and logout must work without a session, so they live on their own router
login_router = APIRouter(prefix="/dashboard", include_in_schema=False)

TABS = {
    "upcoming": ("Upcoming", [pin_queue.STATUS_QUEUED, pin_queue.STATUS_POSTING], False),
    "posted": ("Posted", [pin_queue.STATUS_POSTED], True),
    "failed": ("Failed", [pin_queue.STATUS_FAILED], True),
    "cancelled": ("Cancelled", [pin_queue.STATUS_CANCELLED], True),
}
PAGE_SIZE = 100
BOARD_CACHE_SECONDS = 600

_board_names = {"fetched_at": 0.0, "names": {}}


# ---------- helpers ----------

def _board_name(board_id):
    """Board names aren't stored in the queue, so look them up (cached) for display."""
    if time.time() - _board_names["fetched_at"] > BOARD_CACHE_SECONDS:
        try:
            _board_names["names"] = {board["id"]: board.get("name", "") for board in list_boards()}
        except Exception:
            pass  # show IDs rather than break the page
        _board_names["fetched_at"] = time.time()
    return _board_names["names"].get(board_id) or board_id


def _parse_utc(iso):
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _local_display(iso):
    if not iso:
        return ""
    local = _parse_utc(iso).astimezone(slot_timezone())
    return local.strftime("%a %b %-d, %-I:%M %p ") + local.tzname()


def _local_input_value(iso):
    return _parse_utc(iso).astimezone(slot_timezone()).strftime("%Y-%m-%dT%H:%M")


def _from_local_input(value):
    """datetime-local form value (in the slot timezone) -> UTC ISO string."""
    try:
        local = datetime.strptime(value, "%Y-%m-%dT%H:%M").replace(tzinfo=slot_timezone())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Pick a valid date and time.") from exc
    return pin_queue.utc_iso(local)


def _check_same_origin(request: Request):
    # the browser resends Basic credentials automatically, so refuse form posts from other sites
    source = request.headers.get("origin") or request.headers.get("referer")
    if source and urlparse(source).netloc != request.headers.get("host"):
        raise HTTPException(status_code=403, detail="Cross-site request refused.")


def _back(tab, message):
    return RedirectResponse(f"/dashboard?tab={quote(tab)}&msg={quote(message)}", status_code=303)


def _pin_or_404(pin_id):
    row = pin_queue.get_pin(pin_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No scheduled pin with that ID.")
    return row


# ---------- rendering ----------

_STYLE = """
:root { --bg:#f6f6f4; --card:#fff; --text:#1d1d1b; --muted:#6b6b66; --line:#e3e3de; --accent:#c8102e;
        --ok:#1f7a3d; --warn:#9a6700; --bad:#b42318; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#151514; --card:#1f1f1d; --text:#ecece8; --muted:#a3a39d; --line:#33332f; --accent:#ff5a6e;
          --ok:#5cc982; --warn:#e3b341; --bad:#ff7b72; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:15px/1.45 -apple-system,system-ui,sans-serif; }
main { max-width:960px; margin:0 auto; padding:16px; }
h1 { font-size:20px; margin:0 0 4px; }
.sub { color:var(--muted); font-size:13px; margin-bottom:16px; }
nav { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:16px; }
nav a { padding:6px 12px; border-radius:999px; border:1px solid var(--line); color:var(--text); text-decoration:none; font-size:14px; }
nav a.on { background:var(--text); color:var(--bg); border-color:var(--text); }
.msg { background:var(--card); border-left:3px solid var(--accent); padding:10px 12px; margin-bottom:16px; border-radius:6px; }
.card { display:flex; gap:14px; background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px; margin-bottom:12px; }
.thumb { width:120px; flex:none; }
.thumb img { width:120px; aspect-ratio:2/3; object-fit:cover; border-radius:6px; background:var(--line); display:block; }
.info { min-width:0; flex:1; }
.title { font-weight:600; margin-bottom:4px; }
.meta { color:var(--muted); font-size:13px; overflow-wrap:anywhere; }
.meta a { color:inherit; }
.when { font-size:15px; margin:6px 0; }
.badge { display:inline-block; font-size:12px; padding:1px 8px; border-radius:999px; border:1px solid currentColor; margin-left:6px; }
.queued, .posting { color:var(--warn); } .posted { color:var(--ok); } .failed { color:var(--bad); } .cancelled { color:var(--muted); }
.err { color:var(--bad); font-size:13px; margin-top:6px; overflow-wrap:anywhere; }
.actions { display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-top:10px; }
.actions form { display:flex; gap:6px; align-items:center; margin:0; }
input[type=datetime-local] { font:inherit; padding:5px 6px; border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--text); }
button { font:inherit; font-size:14px; padding:6px 12px; border-radius:6px; border:1px solid var(--line); background:var(--bg); color:var(--text); cursor:pointer; }
button.primary { background:var(--text); color:var(--bg); border-color:var(--text); }
button.danger { color:var(--bad); }
.empty { color:var(--muted); padding:32px 0; text-align:center; }
header { display:flex; justify-content:space-between; align-items:center; gap:12px; }
header form { margin:0; }
.login { max-width:360px; padding-top:15vh; }
.login form { display:flex; flex-direction:column; gap:10px; margin:16px 0; }
.login input[type=password] { font:inherit; padding:10px 12px; border:1px solid var(--line); border-radius:8px; background:var(--card); color:var(--text); }
@media (max-width:560px) { .thumb, .thumb img { width:84px; } }
"""


def _render_card(row, tab):
    e = html.escape
    pin_id = row["id"]
    status = row["status"]
    when_label = "Posted" if status == pin_queue.STATUS_POSTED else "Posts"
    when_iso = row["posted_at"] if status == pin_queue.STATUS_POSTED else row["publish_at"]
    lines = [
        f'<div class="meta">Board: {e(_board_name(row["board_id"]))}</div>',
        f'<div class="meta">Link: <a href="{e(row["link"])}" target="_blank" rel="noopener">{e(row["link"])}</a></div>',
    ]
    if row["pin_id"]:
        pin_url = f"https://www.pinterest.com/pin/{row['pin_id']}/"
        lines.append(f'<div class="meta">Pin: <a href="{e(pin_url)}" target="_blank" rel="noopener">{e(pin_url)}</a></div>')
    if row["last_error"] and status != pin_queue.STATUS_POSTED:
        lines.append(f'<div class="err">Attempts: {row["attempts"]} · {e(row["last_error"])}</div>')

    action = f"/dashboard/pins/{pin_id}"
    hidden_tab = f'<input type="hidden" name="tab" value="{e(tab)}">'
    actions = ""
    if status == pin_queue.STATUS_QUEUED:
        actions = f"""
        <form method="post" action="{action}/reschedule">{hidden_tab}
          <input type="datetime-local" name="when" value="{_local_input_value(row['publish_at'])}" required>
          <button class="primary">Save time</button></form>
        <form method="post" action="{action}/post-now">{hidden_tab}<button>Post now</button></form>
        <form method="post" action="{action}/cancel" onsubmit="return confirm('Cancel this pin?')">{hidden_tab}
          <button class="danger">Cancel</button></form>"""
    elif status in (pin_queue.STATUS_FAILED, pin_queue.STATUS_CANCELLED):
        actions = f"""
        <form method="post" action="{action}/requeue">{hidden_tab}
          <input type="datetime-local" name="when" value="{_local_input_value(pin_queue.now_iso())}" required>
          <button class="primary">Requeue</button></form>"""

    return f"""
    <div class="card">
      <div class="thumb"><a href="/dashboard/images/{pin_id}" target="_blank"><img src="/dashboard/images/{pin_id}" alt="" loading="lazy"></a></div>
      <div class="info">
        <div class="title">{e(row['title'])}<span class="badge {status}">{status}</span></div>
        <div class="when">{when_label} {e(_local_display(when_iso))}</div>
        {''.join(lines)}
        <div class="actions">{actions}</div>
      </div>
    </div>"""


def _render_page(tab, rows, counts, message):
    tabs_html = "".join(
        f'<a class="{"on" if key == tab else ""}" href="/dashboard?tab={key}">'
        f'{label} ({sum(counts.get(s, 0) for s in statuses)})</a>'
        for key, (label, statuses, _) in TABS.items()
    )
    cards = "".join(_render_card(row, tab) for row in rows) or '<div class="empty">Nothing here.</div>'
    message_html = f'<div class="msg">{html.escape(message)}</div>' if message else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pin Schedule</title><style>{_STYLE}</style></head>
<body><main>
  <header><h1>Pin Schedule</h1>
    <form method="post" action="/dashboard/logout"><button>Sign out</button></form></header>
  <div class="sub">Times in {html.escape(slot_timezone().key)} · the worker checks every minute</div>
  {message_html}
  <nav>{tabs_html}</nav>
  {cards}
</main></body></html>"""


def _render_login(error):
    error_html = f'<div class="err">{html.escape(error)}</div>' if error else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pin Schedule</title><style>{_STYLE}</style></head>
<body><main class="login">
  <h1>Pin Schedule</h1>
  <form method="post" action="/dashboard/login">
    <input type="text" name="username" value="pin-dashboard" autocomplete="username" hidden>
    <input type="password" name="password" placeholder="Password" autocomplete="current-password" required autofocus>
    <button class="primary">Sign in</button>
    {error_html}
  </form>
  <div class="sub">You'll stay signed in on this device for {SESSION_DAYS} days.</div>
</main></body></html>"""


# ---------- routes ----------

@login_router.get("/login", response_class=HTMLResponse)
def dashboard_login_page(request: Request):
    if _valid_session(request.cookies.get(SESSION_COOKIE)):
        return RedirectResponse("/dashboard", status_code=303)
    return HTMLResponse(_render_login(""))


@login_router.post("/login")
def dashboard_login(request: Request, password: str = Form(...)):
    _check_same_origin(request)
    if not hmac.compare_digest(password.encode(), _password().encode()):
        time.sleep(FAILED_LOGIN_DELAY_SECONDS)
        return HTMLResponse(_render_login("Wrong password."), status_code=401)
    response = RedirectResponse("/dashboard", status_code=303)
    # Render terminates TLS at its proxy, so check the forwarded scheme too
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        SESSION_COOKIE,
        _new_session_value(),
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/dashboard",
    )
    return response


@login_router.post("/logout")
def dashboard_logout(request: Request):
    _check_same_origin(request)
    response = RedirectResponse("/dashboard/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/dashboard")
    return response

@router.get("", response_class=HTMLResponse)
def dashboard_page(tab: str = "upcoming", msg: str = ""):
    if tab not in TABS:
        tab = "upcoming"
    _, statuses, newest_first = TABS[tab]
    rows = pin_queue.list_pins(status=statuses, limit=PAGE_SIZE, newest_first=newest_first)
    return HTMLResponse(_render_page(tab, rows, pin_queue.status_counts(), msg))


@router.get("/images/{pin_id}")
def dashboard_image(pin_id: int):
    row = _pin_or_404(pin_id)
    if not os.path.exists(row["image_path"]):
        raise HTTPException(status_code=404, detail="Image file is missing.")
    return FileResponse(row["image_path"], media_type=row["content_type"])


@router.post("/pins/{pin_id}/reschedule")
def dashboard_reschedule(pin_id: int, request: Request, when: str = Form(...), tab: str = Form("upcoming")):
    _check_same_origin(request)
    _pin_or_404(pin_id)
    new_time = _from_local_input(when)
    if not pin_queue.reschedule_pin(pin_id, new_time):
        return _back(tab, "Only queued pins can be rescheduled.")
    return _back(tab, f"Rescheduled to {_local_display(new_time)}.")


@router.post("/pins/{pin_id}/post-now")
def dashboard_post_now(pin_id: int, request: Request, tab: str = Form("upcoming")):
    _check_same_origin(request)
    _pin_or_404(pin_id)
    if not pin_queue.reschedule_pin(pin_id, pin_queue.now_iso()):
        return _back(tab, "Only queued pins can be posted now.")
    return _back(tab, "Posting within about a minute.")


@router.post("/pins/{pin_id}/cancel")
def dashboard_cancel(pin_id: int, request: Request, tab: str = Form("upcoming")):
    _check_same_origin(request)
    _pin_or_404(pin_id)
    if not pin_queue.cancel_pin(pin_id):
        return _back(tab, "Only queued pins can be cancelled.")
    return _back(tab, "Pin cancelled.")


@router.post("/pins/{pin_id}/requeue")
def dashboard_requeue(pin_id: int, request: Request, when: str = Form(...), tab: str = Form("failed")):
    _check_same_origin(request)
    _pin_or_404(pin_id)
    new_time = _from_local_input(when)
    if not pin_queue.requeue_pin(pin_id, new_time):
        return _back(tab, "Only failed or cancelled pins can be requeued.")
    return _back("upcoming", f"Requeued for {_local_display(new_time)}.")
