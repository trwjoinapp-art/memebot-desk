"""persist.py · keeps the PAPER desk state durable on free hosts with ephemeral disks, and keeps free hosts awake.
restore: pull desk_state.json + hype_state.json from a PRIVATE Hugging Face dataset repo (STATE_REPO), each only if newer than the local copy.
loop:    push whichever of them changed there every SNAP_SEC (one commit); ping our own public URL so idle-sleep hosts stay up.
Needs env HF_TOKEN + STATE_REPO (e.g. "mohamed-trader/memebot-desk-state"); without them it does nothing (state resets on restart).
Never prints tokens. Touches no wallets/keys/signing: it only copies a JSON file."""
import os, sys, json, time, shutil, urllib.request
D = os.environ.get("DESK_DIR", "/workspace/desk")
FILES = [os.path.join(D, "desk_state.json"), os.path.join(D, "hype_state.json")]   # copier desk + hype sniper (each optional remotely)
TOKEN = os.environ.get("HF_TOKEN", "").strip(); REPO = os.environ.get("STATE_REPO", "").strip()
SNAP = int(os.environ.get("SNAP_SEC", "300")); PING = int(os.environ.get("KEEPALIVE_SEC", "540"))
def log(*a): print("[persist]", time.strftime("%H:%M:%S"), *a, flush=True)
def fresh(p):
    try:
        s = json.load(open(p)); eq = s.get("equity") or [[0]]
        return max(float(eq[-1][0] or 0), float(s.get("last_poll") or 0))
    except Exception: return -1
def api():
    from huggingface_hub import HfApi
    a = HfApi(token=TOKEN); a.create_repo(REPO, repo_type="dataset", private=True, exist_ok=True); return a
def restore():
    if not (TOKEN and REPO): log("no HF_TOKEN/STATE_REPO -> state is NOT durable (resets on restart)"); return
    try:
        from huggingface_hub import hf_hub_download
        api()
    except Exception as e:
        log("restore skipped:", type(e).__name__, str(e).replace(TOKEN, "<HF_TOKEN>")[:200]); return
    for SF in FILES:
        name = os.path.basename(SF)
        try:
            p = hf_hub_download(REPO, name, repo_type="dataset", token=TOKEN, force_download=True)
            r, l = fresh(p), fresh(SF)
            if r > l: shutil.copy(p, SF); log(f"restored {name} from {REPO} (remote {r:.0f} > local {l:.0f})")
            else: log(f"kept local {name} (local {l:.0f} >= remote {r:.0f})")
        except Exception as e:   # e.g. hype_state.json not pushed yet -> start fresh
            log(f"restore {name} skipped:", type(e).__name__, str(e).replace(TOKEN, "<HF_TOKEN>")[:160])
def public_url():
    u = os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or ""
    if not u and os.environ.get("SPACE_HOST"): u = "https://" + os.environ["SPACE_HOST"]
    return u.rstrip("/")
def loop():
    last_m, last_push, last_ping = {}, 0, 0; a = None; url = public_url()
    log("snapshots", "ON -> " + REPO if (TOKEN and REPO) else "OFF", "| keepalive", url or "OFF")
    while True:
        now = time.time()
        if TOKEN and REPO and now - last_push >= SNAP:
            try:
                from huggingface_hub import CommitOperationAdd
                ops, ms = [], {}
                for SF in FILES:
                    if not os.path.exists(SF): continue
                    m = os.path.getmtime(SF)
                    if m != last_m.get(SF):
                        tmp = "/tmp/snap_" + os.path.basename(SF); shutil.copy(SF, tmp)   # copy first: writers replace the file atomically
                        ops.append(CommitOperationAdd(path_in_repo=os.path.basename(SF), path_or_fileobj=tmp)); ms[SF] = m
                if ops:
                    a = a or api()
                    a.create_commit(repo_id=REPO, repo_type="dataset", operations=ops, commit_message="desk snapshot")
                    last_m.update(ms); log("snapshot pushed:", ", ".join(o.path_in_repo for o in ops))
            except Exception as e:
                log("snapshot failed:", type(e).__name__, (str(e).replace(TOKEN, "<HF_TOKEN>"))[:200])
            last_push = now
        if url and now - last_ping >= PING:
            try: urllib.request.urlopen(url + "/healthz", timeout=20).read()
            except Exception as e: log("keepalive ping failed:", type(e).__name__)
            last_ping = now
        time.sleep(15)
if __name__ == "__main__":
    {"restore": restore, "loop": loop}[sys.argv[1]]()
