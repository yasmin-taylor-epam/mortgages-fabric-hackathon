"""The two demo web apps in one local server.

- /customer : a pretend HomeBridge customer website. Its buttons send real events to the Fabric Eventstream.
- /staff    : the staff adviser portal. It reads Fabric live: the Lakehouse SQL endpoint and the Eventhouse.

Run:  python -m portals            (live Fabric, needs config.local.yml and your own sign-in)
      python -m portals --offline  (rehearsal with the local synthetic files; every page says so)
"""
from __future__ import annotations

import argparse
import threading
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config, events, reasons
from .board import Board, day, when
from .sources import check_event_id, check_mortgage_id

HERE = Path(__file__).resolve().parent


def _money(v) -> str:
    return "-" if v is None else f"£{float(v):,.2f}"


def _pct(v, digits: int = 0) -> str:
    return "-" if v is None else f"{float(v) * 100:.{digits}f}%"


def create_app(settings: config.Settings, source) -> FastAPI:
    app = FastAPI(title="HomeBridge demo portals", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters.update(money=_money, pct=_pct, day=day, when=when)
    state = {"sent": {}, "job": "", "job_started": None, "job_done": None}
    lock = threading.Lock()

    def board() -> Board:
        return Board(settings, source)

    def links() -> dict:
        try:
            return source.links()
        except Exception:  # noqa: BLE001
            return {}

    def page(request: Request, template: str, **ctx) -> HTMLResponse:
        ctx.setdefault("problems", [])
        return templates.TemplateResponse(request, template, {"offline": source.kind == "offline", "links": links(),
                                                          "settings": settings, "family_text": reasons.CASE_FAMILIES, **ctx})

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return page(request, "index.html")

    # ----- customer portal
    @app.get("/customer", response_class=HTMLResponse)
    def customer_home(request: Request, who: str = ""):
        who = who or settings.personas[0].mortgage_id
        b = board()
        try:
            home = b.portal_home(who)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return page(request, "customer_home.html", home=home, problems=b.problems, actions=events.ACTIONS)

    @app.post("/customer/send")
    def customer_send(who: str = Form(...), action: str = Form(...), fault: str = Form("")):
        b = board()
        home = b.portal_home(check_mortgage_id(who))
        latest = home["latest"] or {}
        at = events.next_demo_time(settings, b.counts().get("last_live_occurred"))
        env = events.build(action, who, at, latest.get("application_id"), latest.get("stage"), fault == "on")
        errors = events.validate(env)
        if errors:
            raise HTTPException(400, "The event does not match the live_event contract: " + "; ".join(errors))
        source.send_event(env)
        with lock:
            state["sent"][env["event_id"]] = env
        return RedirectResponse(f"/customer/sent/{env['event_id']}", status_code=303)

    @app.post("/customer/resend/{event_id}")
    def customer_resend(event_id: str):
        env = state["sent"].get(check_event_id(event_id))
        if not env:
            raise HTTPException(404, "This portal did not send that event since it started.")
        source.send_event(env)  # the same event again: the duplicate test (T01)
        return RedirectResponse(f"/customer/sent/{event_id}?again=1", status_code=303)

    @app.get("/customer/sent/{event_id}", response_class=HTMLResponse)
    def customer_sent(request: Request, event_id: str, again: int = 0):
        env = state["sent"].get(check_event_id(event_id))
        if not env:
            raise HTTPException(404, "This portal did not send that event since it started.")
        return page(request, "customer_sent.html", env=env, again=bool(again), name=board().name(env["mortgage_id"]),
                    text=reasons.event_text(env["event_type"], env["payload"]))

    @app.get("/api/arrival/{event_id}")
    def arrival(event_id: str):
        try:
            eid = check_event_id(event_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        try:
            found = source.event_arrival(eid)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)[:300]}, status_code=502)
        env = state["sent"].get(eid)
        delay = found.get("delay_seconds")
        found["arrived_demo"] = (when(pd.Timestamp(env["occurred_at"]) + pd.Timedelta(seconds=delay))
                                 if env and delay is not None else None)
        return JSONResponse(found)

    # ----- staff portal
    @app.get("/staff", response_class=HTMLResponse)
    def staff_worklist(request: Request):
        b = board()
        data = b.worklist()
        return page(request, "staff_worklist.html", w=data, problems=b.problems, job=state["job"], job_started=state["job_started"],
                    job_done=state["job_done"])

    @app.post("/staff/process")
    def staff_process():
        from datetime import datetime, timezone
        ref = source.start_routing()
        with lock:
            state["job"], state["job_started"] = ref, datetime.now(timezone.utc).strftime("%H:%M:%S")
            state["job_done"] = None
        return RedirectResponse("/staff#process", status_code=303)

    @app.get("/api/routing")
    def routing():
        if not state["job"]:
            return JSONResponse({"status": "NotStarted"})
        try:
            body = source.routing_status(state["job"])
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"status": "Unknown", "error": str(exc)[:300]}, status_code=502)
        if body.get("status") == "Completed" and not state["job_done"]:
            from datetime import datetime, timezone
            with lock:
                state["job_done"] = datetime.now(timezone.utc).strftime("%H:%M:%S")
        return JSONResponse({"status": body.get("status"), "failureReason": body.get("failureReason"), "started": state["job_started"],
                             "finished": state["job_done"]})

    @app.get("/staff/customer/{mortgage_id}", response_class=HTMLResponse)
    def staff_customer(request: Request, mortgage_id: str):
        b = board()
        try:
            data = b.customer(mortgage_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return page(request, "staff_customer.html", c=data, problems=b.problems)

    @app.get("/staff/summary", response_class=HTMLResponse)
    def staff_summary(request: Request):
        b = board()
        return page(request, "staff_summary.html", s=b.summary(), problems=b.problems)

    return app


def main(argv: list[str] | None = None) -> None:
    import uvicorn
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="Use the local synthetic files, not Fabric.")
    ap.add_argument("--port", type=int, default=8360)
    args = ap.parse_args(argv)
    settings = config.load()
    if args.offline:
        from .sources import OfflineSource
        source = OfflineSource(settings)
    else:
        from .sources import LiveSource
        source = LiveSource(settings)
        print("Signing in and warming up the Fabric connections (about 30 seconds) ...")
        try:
            source.warm_up()  # the slow first steps happen now, not on the first click in the demo
        except Exception as exc:  # noqa: BLE001 - the pages show the problem in plain words
            print(f"Warm-up did not finish: {exc}")
    print(f"HomeBridge demo portals ({source.kind}): http://127.0.0.1:{args.port}/")
    uvicorn.run(create_app(settings, source), host="127.0.0.1", port=args.port, log_level="warning")
