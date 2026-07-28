"""Guards the production camera-freeze fix (iOS Safari pausing the inline <video>
during enrolment, re-capturing the same frozen frame as samples 2 and 3).

These are static-source assertions - the fix lives in browser JS that the Python
suite can't drive - but they stop a future edit from silently dropping the
watchdog / fresh-frame gate, or from bumping a script version without busting
the service-worker cache (which would strand the fix behind stale caches).
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


# Every browser surface that captures from a live camera must (a) install the
# resume-on-pause watchdog when the stream starts, and (b) gate every capture on
# a genuinely fresh frame.
CAPTURE_SURFACES = ("static/app.js", "static/enroll.js", "static/admin.js")


def test_every_capture_surface_has_the_watchdog_and_fresh_frame_gate():
    for f in CAPTURE_SURFACES:
        src = _read(f)
        assert "function keepVideoAlive" in src, f"{f}: missing keepVideoAlive watchdog"
        assert "async function ensureLiveVideo" in src, f"{f}: missing ensureLiveVideo gate"
        # the watchdog is armed when the camera starts
        assert "keepVideoAlive(" in src.replace("function keepVideoAlive", ""), \
            f"{f}: keepVideoAlive defined but never called"
        # a fresh frame is awaited before capture
        assert "await ensureLiveVideo(" in src, f"{f}: ensureLiveVideo never awaited before capture"
        # the watchdog resumes a paused MUTED video (the only kind iOS lets us replay)
        assert ".play()" in src and "'pause'" in src, f"{f}: no pause->play resume path"
        # capture can never hang waiting for a frame
        assert "setTimeout(finish, 350)" in src, f"{f}: missing fresh-frame hard timeout"


def test_capture_calls_ensure_before_grabbing_a_frame():
    # app.js: enrol + verify both gate before grabFrame (allow a trailing comment)
    # enrol now captures a burst - it gates on a fresh frame on every iteration.
    app = _read("static/app.js")
    assert re.search(r"await ensureLiveVideo\(video\);.*\n\s*const f = grabFrame\(\)", app)
    assert re.search(r"await ensureLiveVideo\(video\);.*\n\s*const img0 = grabFrame\(\)", app)
    # enroll.js: the single capture choke point gates first
    enr = _read("static/enroll.js")
    assert re.search(r"async function captureBody\(\)\s*\{\s*\n\s*await ensureLiveVideo\(video\)", enr)
    # admin.js: gates before grab()
    adm = _read("static/admin.js")
    assert re.search(r"await ensureLiveVideo\(\$\('video'\)\);.*\n\s*const img = grab\(\)", adm)


def _script_version(html, name):
    m = re.search(rf"/static/{re.escape(name)}\?v=(\d+)", html)
    assert m, f"no versioned <script> for {name}"
    return m.group(1)


def test_service_worker_wont_strand_the_fix_behind_stale_cache():
    sw = _read("static/sw.js")
    index = _read("templates/index.html")
    # the app.js the SW pre-caches must be the exact version the page requests,
    # or returning users get the old cached HTML+JS forever (cache-first SW)
    page_v = _script_version(index, "app.js")
    m = re.search(r"/static/app\.js\?v=(\d+)", sw)
    assert m and m.group(1) == page_v, \
        f"sw.js pre-caches app.js?v={m and m.group(1)} but index.html requests v={page_v}"
    # a fix shipped through JS must ride a bumped cache name so activate() purges
    # the previous cache (which holds the stale scripts + shell HTML)
    cm = re.search(r"faceverify-v(\d+)", sw)
    assert cm and int(cm.group(1)) >= 16, "bump the SW CACHE version to purge stale cached JS"


def test_enroll_and_admin_scripts_are_version_bumped():
    # /enroll and /admin are under the SW scope too; their JS is runtime-cached,
    # so the fix only reaches returning users if the query version changed.
    assert _script_version(_read("templates/enroll.html"), "enroll.js") >= "5"
    assert _script_version(_read("templates/admin.html"), "admin.js") >= "7"
