# Working rules for this fork

Standing rules from the fork's owner. They apply to every session.

## Roles

- The main session plans, investigates, reviews and orchestrates. It does not
  write the code changes itself.
- Every actual code change is implemented by an Opus 5.5 subagent, briefed with
  a self-contained task; the main session reviews the result before it is
  committed.

## Biometrics in the guest (fingerprint and face)

- Enrollment happens in One UI only. The guest never enrolls a fingerprint or a
  face of its own, and nothing is added that makes it able to.
- The guest recognises the enrollments One UI already has: guest_prepare.sh
  carries One UI's enrollment metadata across on every switch, the templates
  stay in the phone's TEE, and the guest unlocks with them through the phone's
  own HAL.
- Safety comes first. Nothing of One UI's credentials (PIN, gatekeeper handle,
  synthetic-password blobs) is read or written. Host-side writes into the
  guest's data go through the no_symlink guard. A biometric path that fails
  must fall back to the guest's PIN, and no change may be able to lock the
  owner out of One UI.
- A change is not called working until it was tried on a phone; say plainly
  what was only read or only built.

## Performance

- Native code, not interpreted: a patched APK or jar should run compiled
  (dex2oat / odex for the patched file) instead of falling back to the
  interpreter or JIT because its oat was hidden.
- Use the GPU where the work is rendering or model inference.
- HyperOS and ColorOS are the guests that matter most for this.

## Repository

- Work lands in this fork, never in vdcoolok/OneOS or its upstream.
- Follow the repository's own style: comments say why and name the phone or
  report the fix came from; one logical change per commit.
