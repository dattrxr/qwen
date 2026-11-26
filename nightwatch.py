# Night watch: runs inside Qwen's Kaggle session (started from the console).
# Prints a status line the page script reads. When Qwen has had no work for IDLE_MIN minutes,
# it saves everything to the state dataset, tells Telegram, then prints STOP-NOW so the page turns Kaggle off.
import threading, time, traceback

IDLE_MIN = 5


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


def _watch():
    idle_since = None
    last_print = 0
    while True:
        try:
            why = _busy()
            now = time.time()
            if why:
                idle_since = None
                status = "busy " + ",".join(sorted(set(why)))
            else:
                idle_since = idle_since or now
                status = f"idle {int((now - idle_since) // 60)}m"
            if now - last_print > 55:
                print(f"[qwen-watch] {time.strftime('%H:%M')} {status}", flush=True)
                last_print = now
            if idle_since and now - idle_since >= IDLE_MIN * 60:
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
                try:
                    TG.send(("✅ Work done and saved. " if ok else "⚠️ Work done but saving failed (answers are above). ")
                            + "Turning Kaggle off to save GPU hours. Message me to wake Qwen again.")
                except Exception:
                    pass
                while True:  # keep repeating until the page turns the session off
                    print(f"[qwen-watch] {time.strftime('%H:%M')} STOP-NOW saved={ok}", flush=True)
                    time.sleep(30)
        except Exception:
            traceback.print_exc()
        time.sleep(20)


if not any(t.name == "nightwatch" for t in threading.enumerate()):
    threading.Thread(target=_watch, daemon=True, name="nightwatch").start()
    print("[qwen-watch] started, busy now:", _busy() or "nothing", flush=True)
else:
    print("[qwen-watch] already running", flush=True)
