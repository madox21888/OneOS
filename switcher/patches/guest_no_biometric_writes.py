"""Never let the guest enroll or remove a fingerprint or a face on the phone's sensors.

The container shares the host's biometric HALs (Samsung's IFingerprint and
IFace), and the guest's user 0 is One UI's user 0 there. The rule of this fork:
fingerprints and faces are enrolled in One UI only; the guest unlocks with them
and never enrolls, changes or deletes one. guest_no_biometric_cleanup.py stops
the automatic removal at every sensor start, but the requested one was open
(read in the ColorOS 17 framework, image 1105): "Delete fingerprint" in the
guest's Settings, or any guest app holding MANAGE_FINGERPRINT / MANAGE_BIOMETRIC,
ends in FingerprintRemovalClient.startHalOperation() -> ISession.removeEnrollments(),
which needs no auth token: One UI's template is gone from the TEE.
FaceRemovalClient is the same for the face, and
FingerprintEnrollClient / FaceEnrollClient.startHalOperation() hand the HAL
whatever token they were given (guest_local_lock.py gives none today; this is
the second lock on that door).

These four are the only callers of ISession.removeEnrollments / enroll* in
services.jar, for both transports: on a HIDL HAL the session is
HidlToAidlSessionAdapter, reached through the same four methods. So each
startHalOperation() ends the operation at its entry, before getFreshDaemon(),
the way the class itself ends one that failed:

- Enroll: the method's own `catch RemoteException` branch, onError(2, 0) and
  mCallback.onClientFinished(this, false). onError is the client's: the app's
  receiver gets BIOMETRIC_ERROR_UNABLE_TO_PROCESS, the overlays and state
  listeners are told, and AcquisitionClient finishes the client; the second
  onClientFinished is the framework's own and the scheduler ignores it
  ("[Ignoring Finish]").
- Removal: the method's `catch` branch only calls onClientFinished(this, false),
  which frees the scheduler but tells the caller nothing, so whoever asked
  would wait for an answer. RemovalClient has its own failure report, in onRemoved()
  for a null identifier: getListener().onError(sensorId, cookie,
  6 = BIOMETRIC_ERROR_UNABLE_TO_REMOVE, 0) inside a RemoteException catch, then
  onClientFinished(this, false). That sequence is used, with a null check on
  the listener (InternalCleanupClient makes its removal clients with none). A
  build without those getters gets the bare finish.

The stock body stays behind the return as dead code. Authentication, detect,
enumerate, authenticator id, challenge and lockout reset are not touched.

A framework without the four classes is left as it is (nothing printed). One
that has them in another shape, or that calls the HAL's enroll or remove from
anywhere else under com/android/server/biometrics, is refused: a guard that
does not cover a writer is worse than a build that stops.

Usage: guest_no_biometric_writes.py <smali root dir>
Prints the smali_* directories (dex files) it changed, one per line; nothing
on a second run.
"""
import os
import re
import sys

MARK = "guest_no_biometric_writes"
PKG = os.path.join("com", "android", "server", "biometrics")
S = "Lcom/android/server/biometrics/sensors/"
BASE = S + "BaseClientMonitor;"
CB = S + "ClientMonitorCallback;"
CONV = S + "ClientMonitorCallbackConverter;"
# (modality, class, what it does to the HAL)
TARGETS = [
    ("fingerprint", "FingerprintRemovalClient", "remove"),
    ("face", "FaceRemovalClient", "remove"),
    ("fingerprint", "FingerprintEnrollClient", "enroll"),
    ("face", "FaceEnrollClient", "enroll"),
]
SAY = {
    "remove": "OneOS: the %ss on this phone are One UI's; the guest removes none",
    "enroll": "OneOS: a %s is enrolled in One UI only; the guest enrolls none",
}

HEADER = re.compile(r"^\.method (?:protected |public |final )*startHalOperation\(\)V$")
# enumerateEnrollments is not one: the name must start right after the arrow.
HAL_WRITE = re.compile(r"^\s*invoke-interface(?:/range)? \{[^}]*\}, Landroid/hardware/biometrics/"
                       r"(?:fingerprint|face)/ISession;->(?:removeEnrollments|enroll\w*)\(")
HIDL_WRITE = re.compile(r"^\s*invoke-interface(?:/range)? \{[^}]*\}, L[\w/]+/IBiometrics(?:Fingerprint|Face);->"
                        r"(?:remove|enroll)\(")
FINISH = re.compile(r"^\s*invoke-interface \{v\d+, p0, v\d+\}, " + re.escape(CB) +
                    r"->onClientFinished\(" + re.escape(BASE) + r"Z\)V$")
CALLBACK = re.compile(r"^\s*iget-object v\d+, p0, (L[\w/$]+;)->mCallback:" + re.escape(CB) + r"$")
ON_ERROR = re.compile(r"^\s*invoke-virtual \{p0, v\d+, v\d+\}, (L[\w/$]+;)->onError\(II\)V$")


def fail(msg):
    sys.exit(MARK + ": " + msg)


def methods(lines):
    """[(first line, last line, flags, name and signature)] of a smali class."""
    out, start = [], None
    for i, l in enumerate(lines):
        if l.startswith(".method "):
            start = i
        elif l == ".end method" and start is not None:
            words = lines[start].split()
            out.append((start, i, words[1:-1], words[-1]))
            start = None
    return out


def reaches_hal(lines, cls):
    """Methods of the class that reach a HAL write without going through startHalOperation()."""
    all_ = methods(lines)
    hit = {sig for a, b, _, sig in all_ if any(HAL_WRITE.match(l) for l in lines[a:b])}
    grown = True
    while grown:
        grown = False
        for a, b, _, sig in all_:
            if sig in hit:
                continue
            # startHalOperation() is the gate: what calls it is stopped there.
            if any(f"{cls}->{h}" in l for l in lines[a:b] for h in hit if h != "startHalOperation()V"):
                hit.add(sig)
                grown = True
    return hit, {sig: flags for _, _, flags, sig in all_}


def has_method(path, *sigs):
    lines = open(path).read().split("\n")
    return all(any(re.match(r"^\.method public (?:final )?" + re.escape(s) + "$", l) for l in lines) for s in sigs)


def block(cls, tag, kind, what, owner, on_error, report):
    out = [
        f"    # OneOS (patches/{MARK}.py): the sensors and their templates are the phone's (One UI's).",
        "    # The operation ends here as a failed one; the HAL is not called.",
        f'    const-string v0, "{tag}"',
        "",
        '    const-string v1, "%s"' % (SAY[kind] % what).replace("'", "\\'"),
        "",
        "    invoke-static {v0, v1}, Landroid/util/Slog;->w(Ljava/lang/String;Ljava/lang/String;)I",
        "",
    ]
    if kind == "enroll":
        # As the method's own catch branch: BIOMETRIC_ERROR_UNABLE_TO_PROCESS to the caller.
        out += [
            "    const/4 v0, 0x2",
            "",
            "    const/4 v1, 0x0",
            "",
            f"    invoke-virtual {{p0, v0, v1}}, {on_error}->onError(II)V",
            "",
            f"    iget-object v0, p0, {owner}->mCallback:{CB}",
            "",
        ]
    else:
        if report:
            # As RemovalClient.onRemoved() for a null identifier: BIOMETRIC_ERROR_UNABLE_TO_REMOVE.
            out += [
                f"    invoke-virtual {{p0}}, {cls}->getListener(){CONV}",
                "",
                "    move-result-object v0",
                "",
                "    if-eqz v0, :oneos_bio_finish",
                "",
                "    :oneos_bio_try",
                f"    invoke-virtual {{p0}}, {cls}->getSensorId()I",
                "",
                "    move-result v1",
                "",
                f"    invoke-virtual {{p0}}, {cls}->getCookie()I",
                "",
                "    move-result v2",
                "",
                "    const/4 v3, 0x6",
                "",
                "    const/4 v4, 0x0",
                "",
                f"    invoke-virtual {{v0, v1, v2, v3, v4}}, {CONV}->onError(IIII)V",
                "    :oneos_bio_try_end",
                "    .catch Landroid/os/RemoteException; {:oneos_bio_try .. :oneos_bio_try_end} :oneos_bio_catch",
                "",
                "    goto :oneos_bio_finish",
                "",
                "    :oneos_bio_catch",
                "    move-exception v0",
                "",
                "    :oneos_bio_finish",
            ]
        out += [
            f"    iget-object v0, p0, {owner}->mCallback:{CB}",
            "",
            "    const/4 v1, 0x0",
            "",
        ]
    return out + [
        f"    invoke-interface {{v0, p0, v1}}, {CB}->onClientFinished({BASE}Z)V",
        "",
        "    return-void",
        "",
    ]


def patch(path, rel, cls, tag, kind, what, report):
    """The patched text, or None when the method already has the block."""
    lines = open(path).read().split("\n")
    heads = [i for i, l in enumerate(lines) if HEADER.match(l)]
    if len(heads) != 1:
        fail(f"{rel}: startHalOperation() found {len(heads)} times, expected once")
    i = heads[0]
    end = lines.index(".end method", i)
    body = lines[i + 1:end]
    # The log line, not the comment: a decoded built jar has no comments
    # (scripts/patch-built-jar.sh on a set that already has the block).
    if any(l.lstrip().startswith('const-string v1, "OneOS: ') for l in body):
        return None
    m = re.match(r"(\s*)\.locals (\d+)$", lines[i + 1])
    if not m:
        fail(f"{rel}: unexpected startHalOperation() header")
    # This must be the method the HAL write sits behind, and the only way to it.
    hit, flags = reaches_hal(lines, cls)
    if "startHalOperation()V" not in hit:
        fail(f"{rel}: startHalOperation() does not reach ISession's {kind}, unexpected build")
    loose = sorted(s for s in hit if s != "startHalOperation()V" and not {"private", "synthetic"} & set(flags[s]))
    if loose:
        fail(f"{rel}: {', '.join(loose)} reaches ISession's {kind} past startHalOperation(), unexpected build")
    if kind == "remove" and sum(1 for l in body if HAL_WRITE.match(l)) != 1:
        fail(f"{rel}: startHalOperation() does not call removeEnrollments() once")
    # The failure branch the block is made from must be the one this build
    # has, with the fields named as it names them (the class on ColorOS, the
    # declaring class in R8 code; guest_no_biometric_cleanup.py).
    if not any(FINISH.match(l) for l in body):
        fail(f"{rel}: startHalOperation() has no onClientFinished() failure branch")
    owners = {c.group(1) for c in map(CALLBACK.match, body) if c}
    if len(owners) != 1:
        fail(f"{rel}: mCallback read through {len(owners)} classes in startHalOperation(), expected one")
    on_error = None
    if kind == "enroll":
        errors = {c.group(1) for c in map(ON_ERROR.match, body) if c}
        if len(errors) != 1:
            fail(f"{rel}: startHalOperation() has no onError(II) failure branch")
        on_error = errors.pop()
    need = 5 if kind == "remove" and report else 2
    lines[i + 1] = f"{m.group(1)}.locals {max(int(m.group(2)), need)}"
    j = i + 2
    while lines[j].strip() == "" or lines[j].lstrip().startswith((".param", ".end param")):
        j += 1
    if lines[j].lstrip().startswith(".annotation"):
        fail(f"{rel}: unexpected startHalOperation() header")
    lines[j:j] = block(cls, tag, kind, what, owners.pop(), on_error, report)
    return "\n".join(lines)


def main(root):
    # Every class of the biometrics package, in whichever dex it sits.
    files = {}
    for d in sorted(os.listdir(root)):
        top = os.path.join(root, d, PKG)
        if not d.startswith("smali") or not os.path.isdir(top):
            continue
        for sub, _, fs in os.walk(top):
            for f in fs:
                if f.endswith(".smali"):
                    files.setdefault(os.path.relpath(os.path.join(sub, f), os.path.join(root, d)), []).append(d)
    found, missing = [], []
    for modality, name, kind in TARGETS:
        rel = os.path.join(PKG, "sensors", modality, "aidl", name + ".smali")
        dirs = files.get(rel, [])
        if len(dirs) > 1:
            fail(f"{name}.smali found {len(dirs)} times")
        (found if dirs else missing).append((modality, name, kind, rel, dirs[0] if dirs else None))
    # No writer outside the four: the HIDL calls only in the session adapters,
    # which the four reach through ISession.
    ours = {rel for _, _, _, rel, _ in found}
    for rel, dirs in sorted(files.items()):
        for d in dirs:
            lines = open(os.path.join(root, d, rel)).read().split("\n")
            if rel not in ours and any(HAL_WRITE.match(l) for l in lines):
                fail(f"{d}/{rel}: calls ISession's enroll or removeEnrollments, and is not a class this patch knows")
            if os.path.basename(rel) != "HidlToAidlSessionAdapter.smali" and any(HIDL_WRITE.match(l) for l in lines):
                fail(f"{d}/{rel}: calls the HIDL HAL's enroll or remove, and is not a class this patch knows")
    if not found:
        print("no AIDL fingerprint or face enroll and removal clients in this framework, nothing to do",
              file=sys.stderr)
        return
    if missing:
        fail("no " + ", ".join(name for _, name, _, _, _ in missing) + " next to the others, unexpected build")

    def one(name):
        rel = os.path.join(PKG, "sensors", name + ".smali")
        return os.path.join(root, files[rel][0], rel) if len(files.get(rel, [])) == 1 else None
    base, conv = one("BaseClientMonitor"), one("ClientMonitorCallbackConverter")
    report = bool(base and conv and has_method(base, f"getListener(){CONV}", "getSensorId()I", "getCookie()I")
                  and has_method(conv, "onError(IIII)V"))
    if not report:
        print("BaseClientMonitor's getters or ClientMonitorCallbackConverter.onError(IIII) are not there: "
              "a refused removal finishes without an error to its caller", file=sys.stderr)
    # All four are read before the first is written: never half patched.
    todo = []
    for modality, name, kind, rel, d in found:
        path = os.path.join(root, d, rel)
        text = patch(path, rel, f"{S}{modality}/aidl/{name};", name, kind, modality, report)
        if text is None:
            print(f"{name}.startHalOperation: already patched", file=sys.stderr)
        else:
            todo.append((path, text, d, f"{name}.startHalOperation: fails at its entry, "
                                        f"the HAL is not asked to {kind}"))
    changed = []
    for path, text, d, note in todo:
        open(path, "w").write(text)
        print(note, file=sys.stderr)
        if d not in changed:
            changed.append(d)
    for d in changed:
        print(d)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
