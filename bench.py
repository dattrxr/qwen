# Qwen speed test, run inside the Qwen Kaggle session (uses the running app's helpers).
# Restarts only llama-server, never the Kaggle session. Restores Qwen (Fast) at the end.
import json, os, subprocess, time, traceback
from pathlib import Path
import requests

OUT = Path("/kaggle/working/qwen-agent/speedtest.json")
CTX = 114688
PROMPTS = [
    "Plan a 60-second hip-hop tribute edit about a rapper's comeback. Give the hook, 6 beats with timings, "
    "on-screen captions for each, and the music cue. Write it as a numbered plan.",
    "Write a Python function that reads a CSV of YouTube videos (title, views, likes, upload_date) and returns "
    "the 5 best videos by like rate per day since upload, with docstring and type hints.",
]
MAXTOK = 300


def say(*a):
    print("[speedtest]", *a, flush=True)


MEMLOG = Path("/kaggle/working/qwen-agent/speedtest-mem.txt")


def mem_state():
    """(used_GB, limit_GB) of this container, counting file cache, which is what gets a session killed."""
    for cur, lim in (("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.max"),
                     ("/sys/fs/cgroup/memory/memory.usage_in_bytes", "/sys/fs/cgroup/memory/memory.limit_in_bytes")):
        try:
            u = int(open(cur).read().strip())
            l_raw = open(lim).read().strip()
            l = int(l_raw) if l_raw.isdigit() else 0
            if not l or l > 1 << 50:
                l = int(open("/proc/meminfo").read().split()[1]) * 1024
            return u / 1e9, l / 1e9
        except Exception:
            continue
    return 0.0, 0.0


def memlog(tag):
    u, l = mem_state()
    with MEMLOG.open("a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {tag} used={u:.1f}GB limit={l:.1f}GB\n")
    return u, l


def dl(repo, name, dest):
    """Download in chunks and tell Linux to drop each written chunk from memory right away,
    so a 17 GB download doesn't fill the session's RAM."""
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 1_000_000:
        return dest
    tmp = Path(str(dest) + ".part")
    t0 = time.time()
    url = f"https://huggingface.co/{repo}/resolve/main/{name}"
    try:
        with requests.get(url, stream=True, timeout=60) as r, open(tmp, "wb") as f:
            r.raise_for_status()
            done = 0
            for chunk in r.iter_content(8 << 20):
                f.write(chunk)
                done += len(chunk)
                if done % (512 << 20) < (8 << 20):
                    f.flush()
                    os.fsync(f.fileno())
                    os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
                    u, l = memlog(f"download {done >> 30}GB")
                    if l and u > 0.85 * l:
                        raise RuntimeError(f"memory {u:.1f}/{l:.1f} GB, stopping to protect the session")
            f.flush()
            os.fsync(f.fileno())
            os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
    except Exception as e:
        say("download stopped:", name, str(e)[:200])
        tmp.unlink(missing_ok=True)
        return None
    tmp.replace(dest)
    say(f"downloaded {name} in {time.time() - t0:.0f}s")
    return dest


def cmd(gguf, np_, extra, proj):
    c = [str(_BOOT["bin"]), "-m", str(gguf), "--alias", "qwen", "--host", "127.0.0.1", "--port", "8091",
         "-ngl", "99", "--fit", "off", "--split-mode", "layer", "--tensor-split", "1,1",
         "-c", str(CTX), "-np", str(np_), "--kv-unified", "--flash-attn", "on",
         "--cache-type-k", "q8_0", "--cache-type-v", "q8_0", "--jinja", "--reasoning-format", "deepseek"]
    if proj:
        c += ["--mmproj", str(proj)]
    return c + list(extra)


def boot(c, wait=600):
    subprocess.run(["pkill", "-f", "llama-server"], check=False)
    time.sleep(4)
    try:
        open("/proc/sys/vm/drop_caches", "w").write("1")
    except Exception:
        pass
    log = open("/tmp/speedtest-server.log", "w")
    p = subprocess.Popen(c, env=_BOOT["env"], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    t0 = time.time()
    while time.time() - t0 < wait and p.poll() is None and not llm_alive():
        time.sleep(3)
    ok = p.poll() is None and llm_alive()
    if not ok:
        p.kill()
        tail = Path("/tmp/speedtest-server.log").read_text(errors="replace").strip().splitlines()[-4:]
        return None, " | ".join(tail)[-300:]
    return time.time() - t0, ""


def gen(prompt):
    body = {"model": "qwen", "messages": [{"role": "user", "content": prompt}], "max_tokens": MAXTOK,
            "temperature": 0.7, "top_p": 0.8, "top_k": 20, "chat_template_kwargs": {"enable_thinking": False}}
    t0 = time.time()
    r = requests.post(LLM_URL + "/v1/chat/completions", json=body, timeout=900).json()
    dt = time.time() - t0
    tm = r.get("timings") or {}
    n = tm.get("predicted_n") or (r.get("usage") or {}).get("completion_tokens") or 0
    tps = tm.get("predicted_per_second") or (n / dt if dt else 0)
    acc = None
    if tm.get("draft_n"):
        acc = round(100 * tm.get("draft_n_accepted", 0) / tm["draft_n"])
    return round(tps, 1), n, acc


def run_cfg(name, gguf, np_, extra, proj):
    say("testing:", name)
    u, l = memlog("before " + name)
    if l and u > 0.85 * l:
        return {"name": name, "error": f"skipped: memory {u:.1f}/{l:.1f} GB"}
    up, err = boot(cmd(gguf, np_, extra, proj))
    row = {"name": name, "file": Path(gguf).name, "slots": np_, "extra": " ".join(map(str, extra))}
    if up is None:
        row["error"] = err
        say("  failed:", err)
        return row
    gen("Say hi.")  # warm-up
    speeds, accs = [], []
    for p in PROMPTS:
        tps, n, acc = gen(p)
        speeds.append(tps)
        if acc is not None:
            accs.append(acc)
    row.update(load_s=round(up), tok_s=round(sum(speeds) / len(speeds), 1), per_prompt=speeds,
               accept_pct=(round(sum(accs) / len(accs)) if accs else None),
               gpu_free_mb=_gpu_free_mb())
    say(f"  {row['tok_s']} tok/s  (per prompt {speeds}, look-ahead accepted {row['accept_pct']}%)")
    return row


results = []
proj = None
orig_gguf = _BOOT["gguf"]
SERVER["switching"] = True
try:
    try:
        TG.send("🧪 Speed test running (~30 min). Qwen is paused until it's done.")
    except Exception:
        pass
    mm = sorted(MODEL_DIR.glob("mmproj*.gguf"))
    if mm:
        proj = mm[0]
    help_txt = subprocess.run([str(_BOOT["bin"]), "--help"], capture_output=True, text=True,
                              env=_BOOT["env"]).stdout
    spec_line = [ln.strip() for ln in help_txt.splitlines() if "spec-type" in ln or "dflash" in ln.lower()]
    say("llama.cpp spec options:", spec_line[:6])

    # free memory first: the running model + a 17 GB download together go past Kaggle's 30 GB RAM limit
    subprocess.run(["pkill", "-f", "llama-server"], check=False)
    time.sleep(5)
    try:
        open("/proc/sys/vm/drop_caches", "w").write("1")
    except Exception:
        pass
    uns = dl("unsloth/Qwen3.8-27B-GGUF", "Qwen3.8-27B-UD-Q4_K_M.gguf", MODEL_DIR / "unsloth-UD-Q4_K_M.gguf")
    dfl = dl("ggml-org/Qwen3.8-27B-GGUF", "dflash-Qwen3.8-27B-Q4_0.gguf", MODEL_DIR / "dflash-Q4_0.gguf")
    mtp_cur = _mtp_flags()                      # current file: separate look-ahead part
    uns_has_mtp = bool(uns and gguf_meta(uns, ".nextn_predict_layers").get("nextn_predict_layers"))
    say("unsloth file has look-ahead built in:", uns_has_mtp)
    mtp_uns = list(SPEC_MTP) if uns_has_mtp else [x for x in mtp_cur]

    cfgs = [
        ("current file, 3 slots, normal", orig_gguf, 3, []),
        ("current file, 3 slots, Fast (now)", orig_gguf, 3, mtp_cur),
        ("current file, 1 slot, Fast", orig_gguf, 1, mtp_cur),
    ]
    if uns:
        cfgs += [
            ("Unsloth file, 3 slots, normal", uns, 3, []),
            ("Unsloth file, 3 slots, Fast", uns, 3, mtp_uns),
            ("Unsloth file, 1 slot, Fast", uns, 1, mtp_uns),
            ("Unsloth file, 1 slot, Fast n=3", uns, 1, [x if x != "2" else "3" for x in mtp_uns]),
        ]
        if dfl and any("dflash" in ln.lower() for ln in spec_line):
            cfgs += [("Unsloth file, 1 slot, DFlash", uns, 1, ["--spec-type", "draft-dflash", "-md", str(dfl)]),
                     ("Unsloth file, 3 slots, DFlash", uns, 3, ["--spec-type", "draft-dflash", "-md", str(dfl)])]
        elif dfl:
            say("this llama.cpp has no DFlash option; skipping DFlash")
    for c in cfgs:
        try:
            results.append(run_cfg(*c, proj))
        except Exception as e:
            results.append({"name": c[0], "error": str(e)[-300:]})
            traceback.print_exc()
        OUT.write_text(json.dumps({"results": results, "spec_help": spec_line}, indent=1))
finally:
    say("restoring Qwen (Fast on)...")
    _BOOT["gguf"] = orig_gguf
    try:
        opt = _server_ok().get("fast_option") or {}
        ok = _boot(opt.get("ctx", CTX), proj, _server_ok().get("kv", "q8_0"), _mtp_flags())
        if not ok:
            start_server()
    except Exception:
        traceback.print_exc()
    SERVER["switching"] = False
    lines = [f"{r['name']}: " + (f"{r['tok_s']} tok/s" if "tok_s" in r else "failed") for r in results]
    say("\n" + "\n".join(lines))
    try:
        TG.send("🧪 Speed test done, Qwen is back on.\n" + "\n".join(lines))
    except Exception:
        pass
