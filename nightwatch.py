# Night watch v2: runs inside Qwen's Kaggle session (started from the console).
# Serves Qwen's status at <qwen link>/qwen-status so the Kaggle page script can read it
# (v1 printed it to the console, which Kaggle wiped when "Continue editing" was clicked).
# When Qwen has had no work for IDLE_MIN minutes it saves everything, tells Telegram, then sets stop=True.
import threading, time, traceback

IDLE_MIN = 5
WATCH = {"busy": [], "idle_s": 0, "stop": False, "saved": None, "t": 0}


def _busy():
    why = []
    try:
        for cid, j in list(JOBS.items()):
            if j and j["thread"].is_alive():
                why.append("chat")
    except Exception:
        pass
    try:
        if any(a.get("status") == "working" for a in AGENTS.values()):
            why.append("helper")
    except Exception:
        pass
    try:
        if TG.held:
            why.append("telegram")
    except Exception:
        pass
    try:
        if SERVER.get("switching"):
            why.append("restarting")
    except Exception:
        pass
    return why


def _add_status_route():
    from fastapi.responses import JSONResponse

    def _status():
        return JSONResponse(dict(WATCH, now=time.time()),
                            headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"})

    if any(getattr(r, "path", "") == "/qwen-status" for r in _app.router.routes):
        return
    _app.add_api_route("/qwen-status", _status, methods=["GET"], include_in_schema=False)
    _app.router.routes.insert(0, _app.router.routes.pop())   # ahead of Gradio's catch-all routes


def _watch():
    idle_since = None
    last_print = 0
    while True:
        try:
            why = _busy()
            now = time.time()
            if why:
                idle_since = None
            else:
                idle_since = idle_since or now
            WATCH.update(busy=sorted(set(why)), idle_s=int(now - idle_since) if idle_since else 0, t=now)
            if now - last_print > 300:
                print(f"[qwen-watch] {time.strftime('%H:%M')} busy={WATCH['busy']} idle={WATCH['idle_s']}s", flush=True)
                last_print = now
            if idle_since and now - idle_since >= IDLE_MIN * 60 and not WATCH["stop"]:
                ok = False
                for attempt in range(3):
                    try:
                        STATE.save_state()
                        ok = True
                        break
                    except Exception as e:
                        print(f"[qwen-watch] save failed ({attempt + 1}/3): {str(e)[-150:]}", flush=True)
                        time.sleep(60)
                if _busy():  # new work arrived while saving: keep going
                    idle_since = None
                    continue
                WATCH.update(stop=True, saved=ok)
                print(f"[qwen-watch] {time.strftime('%H:%M')} STOP-NOW saved={ok}", flush=True)
                try:
                    TG.send(("✅ Work done and saved. " if ok else "⚠️ Work done but saving failed (answers are above). ")
                            + "Turning Kaggle off to save GPU hours. Message me to wake Qwen again.")
                except Exception:
                    pass
        except Exception:
            traceback.print_exc()
        time.sleep(15)


_add_status_route()
if not any(t.name == "nightwatch" for t in threading.enumerate()):
    threading.Thread(target=_watch, daemon=True, name="nightwatch").start()
    print("[qwen-watch] v2 started, busy now:", _busy() or "nothing", flush=True)
else:
    print("[qwen-watch] already running (v1?) - restart the session to use v2", flush=True)
