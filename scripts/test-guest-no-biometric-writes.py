#!/usr/bin/env python3
"""The guest never enrolls or removes a fingerprint or a face: the services.jar patcher.

The container shares the phone's fingerprint and face HALs, and the templates in them are One
UI's. patches/guest_no_biometric_writes.py ends the four clients that ask the HAL to enroll or
to remove (Fingerprint/Face Removal/EnrollClient.startHalOperation) at their entry, as failed
operations.

Here, on small smali trees with the shapes of the ColorOS 17 framework (image 1105):
- the four methods get the block at their entry, the stock body stays behind it, the caller of a
  removal gets BIOMETRIC_ERROR_UNABLE_TO_REMOVE and the caller of an enrollment the client's own
  onError;
- a second run changes nothing, on the patched tree and on one as a decoded built jar shows it
  (no comments);
- a framework without the four classes is left alone; one with another shape, with one of the
  four missing, or with another caller of the HAL's enroll or remove is refused and not touched;
- R8's field owner and a framework without BaseClientMonitor's getters;
- make-overlays.sh runs the patcher next to guest_no_biometric_cleanup.py.
"""
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHER = ROOT / 'switcher/patches/guest_no_biometric_writes.py'

S = 'Lcom/android/server/biometrics/sensors/'
BASE = S + 'BaseClientMonitor;'
CB = S + 'ClientMonitorCallback;'
CONV = S + 'ClientMonitorCallbackConverter;'
PKG = 'com/android/server/biometrics/sensors'
FINISH = 'invoke-interface {%s, p0, %s}, ' + CB + '->onClientFinished(' + BASE + 'Z)V'


def cls(modality, name):
    return f'{S}{modality}/aidl/{name};'


def removal(modality, name, owner=None):
    c = cls(modality, name)
    return f'''.class public Lcom/android/server/biometrics/sensors/{modality}/aidl/{name};
.super {S}RemovalClient;

.method protected startHalOperation()V
    .locals 3

    .line 59
    :try_start_0
    invoke-virtual {{p0}}, {c}->getFreshDaemon()Ljava/lang/Object;

    move-result-object v0

    check-cast v0, {S}{modality}/aidl/AidlSession;

    invoke-virtual {{v0}}, {S}{modality}/aidl/AidlSession;->getSession()Landroid/hardware/biometrics/{modality}/ISession;

    move-result-object v0

    iget-object v1, p0, {c}->mBiometricIds:[I

    invoke-interface {{v0, v1}}, Landroid/hardware/biometrics/{modality}/ISession;->removeEnrollments([I)V
    :try_end_0
    .catch Landroid/os/RemoteException; {{:try_start_0 .. :try_end_0}} :catch_0

    .line 63
    goto :goto_0

    .line 60
    :catch_0
    move-exception v0

    .line 62
    iget-object v1, p0, {owner or c}->mCallback:{CB}

    const/4 v2, 0x0

    {FINISH % ('v1', 'v2')}

    .line 64
    :goto_0
    return-void
.end method
'''


def enroll(modality, name, locals_=4, entry='protected'):
    c = cls(modality, name)
    return f'''.class public Lcom/android/server/biometrics/sensors/{modality}/aidl/{name};
.super {S}EnrollClient;

.method public static synthetic $r8$lambda$abc({c})V
    .locals 0

    invoke-direct {{p0}}, {c}->lambda$doEnroll$0()V

    return-void
.end method

.method private doEnroll()V
    .locals 2

    invoke-virtual {{p0}}, {c}->getFreshDaemon()Ljava/lang/Object;

    move-result-object v0

    const/4 v1, 0x0

    invoke-interface {{v0, v1}}, Landroid/hardware/biometrics/{modality}/ISession;->enroll(Landroid/hardware/keymaster/HardwareAuthToken;)Landroid/hardware/biometrics/common/ICancellationSignal;

    return-void
.end method

.method private synthetic lambda$doEnroll$0()V
    .locals 2

    const/4 v0, 0x0

    const/4 v1, 0x0

    invoke-interface {{v0, v1}}, Landroid/hardware/biometrics/{modality}/ISession;->enrollWithContext(Landroid/hardware/keymaster/HardwareAuthToken;Landroid/hardware/biometrics/common/OperationContext;)Landroid/hardware/biometrics/common/ICancellationSignal;

    return-void
.end method

.method {entry} startHalOperation()V
    .locals {locals_}

    .line 236
    invoke-virtual {{p0}}, {c}->getContext()Landroid/content/Context;

    move-result-object v0

    .line 238
    :try_start_0
    invoke-direct {{p0}}, {c}->doEnroll()V
    :try_end_0
    .catch Landroid/os/RemoteException; {{:try_start_0 .. :try_end_0}} :catch_0

    .line 244
    goto :goto_0

    .line 239
    :catch_0
    move-exception v0

    .line 241
    const/4 v1, 0x2

    const/4 v2, 0x0

    invoke-virtual {{p0, v1, v2}}, {c}->onError(II)V

    .line 243
    iget-object v1, p0, {c}->mCallback:{CB}

    {FINISH % ('v1', 'v2')}

    .line 245
    :goto_0
    return-void
.end method
'''


BASE_MONITOR = f'''.class public abstract {BASE}
.super Ljava/lang/Object;

.field protected mCallback:{CB}

.method public getCookie()I
    .locals 1

    const/4 v0, 0x0

    return v0
.end method

.method public getListener(){CONV}
    .locals 1

    const/4 v0, 0x0

    return-object v0
.end method

.method public getSensorId()I
    .locals 1

    const/4 v0, 0x0

    return v0
.end method
'''

CONVERTER = f'''.class public {CONV}
.super Ljava/lang/Object;

.method public onError(IIII)V
    .locals 0

    return-void
.end method
'''

# The session a HIDL HAL gets: it calls the HAL itself, and is reached through ISession only.
ADAPTER = f'''.class public {S}fingerprint/hidl/HidlToAidlSessionAdapter;
.super Ljava/lang/Object;

.method public removeEnrollments([I)V
    .locals 3

    const/4 v0, 0x0

    invoke-interface {{v0, v1, v2}}, Landroid/hardware/biometrics/fingerprint/V2_1/IBiometricsFingerprint;->remove(II)I

    return-void
.end method
'''

# Enumeration and authentication stay as they are.
ENUMERATE = f'''.class public {S}fingerprint/aidl/FingerprintInternalEnumerateClient;
.super Ljava/lang/Object;

.method protected startHalOperation()V
    .locals 1

    const/4 v0, 0x0

    invoke-interface {{v0}}, Landroid/hardware/biometrics/fingerprint/ISession;->enumerateEnrollments()V

    return-void
.end method
'''

FILES = {
    'fingerprint/aidl/FingerprintRemovalClient': lambda: removal('fingerprint', 'FingerprintRemovalClient'),
    'face/aidl/FaceRemovalClient': lambda: removal('face', 'FaceRemovalClient'),
    'fingerprint/aidl/FingerprintEnrollClient': lambda: enroll('fingerprint', 'FingerprintEnrollClient'),
    'face/aidl/FaceEnrollClient': lambda: enroll('face', 'FaceEnrollClient', locals_=10),
    'BaseClientMonitor': lambda: BASE_MONITOR,
    'ClientMonitorCallbackConverter': lambda: CONVERTER,
    'fingerprint/hidl/HidlToAidlSessionAdapter': lambda: ADAPTER,
    'fingerprint/aidl/FingerprintInternalEnumerateClient': lambda: ENUMERATE,
}
FOUR = list(FILES)[:4]


class Tree:
    def __init__(self, test, change=None, dex=None):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        files = {k: v() for k, v in FILES.items()}
        for k, v in (change or {}).items():
            if v is None:
                files.pop(k)
            else:
                files[k] = v
        for k, v in files.items():
            p = self.root / (dex or {}).get(k, 'smali') / PKG / (k + '.smali')
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(v)
        # Something that is not the framework's biometrics package, in another dex.
        other = self.root / 'smali_classes2/com/android/server/Other.smali'
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_text('.class public Lcom/android/server/Other;\n.super Ljava/lang/Object;\n')

    def run(self):
        return subprocess.run([sys.executable, '-I', str(PATCHER), str(self.root)],
                              capture_output=True, text=True)

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_text() for p in sorted(self.root.rglob('*.smali'))}

    def method(self, key, dex='smali'):
        text = (self.root / dex / PKG / (key + '.smali')).read_text()
        m = re.search(r'^\.method [^\n]*startHalOperation\(\)V\n(.*?)^\.end method$', text, re.M | re.S)
        return [l.strip() for l in m.group(1).split('\n') if l.strip() and not l.strip().startswith('#')]


class Patcher(unittest.TestCase):
    def test_the_four_fail_at_their_entry(self):
        tree = Tree(self)
        before = tree.snapshot()
        done = tree.run()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, 'smali\n')
        after = tree.snapshot()
        self.assertEqual({k for k in after if after[k] != before[k]},
                         {f'smali/{PKG}/{k}.smali' for k in FOUR})

        for modality, name in (('fingerprint', 'FingerprintRemovalClient'), ('face', 'FaceRemovalClient')):
            c = cls(modality, name)
            body = tree.method(f'{modality}/aidl/{name}')
            # Five registers for the receiver's onError(sensor, cookie, 6, 0); the stock method had three.
            self.assertEqual(body[0], '.locals 5')
            self.assertEqual(body[1], f'const-string v0, "{name}"')
            self.assertTrue(body[2].startswith('const-string v1, "OneOS: '), body[2])
            end = body.index('return-void')
            block = body[:end + 1]
            self.assertEqual(block[3:], [
                'invoke-static {v0, v1}, Landroid/util/Slog;->w(Ljava/lang/String;Ljava/lang/String;)I',
                f'invoke-virtual {{p0}}, {c}->getListener(){CONV}',
                'move-result-object v0',
                'if-eqz v0, :oneos_bio_finish',
                ':oneos_bio_try',
                f'invoke-virtual {{p0}}, {c}->getSensorId()I',
                'move-result v1',
                f'invoke-virtual {{p0}}, {c}->getCookie()I',
                'move-result v2',
                'const/4 v3, 0x6',
                'const/4 v4, 0x0',
                f'invoke-virtual {{v0, v1, v2, v3, v4}}, {CONV}->onError(IIII)V',
                ':oneos_bio_try_end',
                '.catch Landroid/os/RemoteException; {:oneos_bio_try .. :oneos_bio_try_end} :oneos_bio_catch',
                'goto :oneos_bio_finish',
                ':oneos_bio_catch',
                'move-exception v0',
                ':oneos_bio_finish',
                f'iget-object v0, p0, {c}->mCallback:{CB}',
                'const/4 v1, 0x0',
                FINISH % ('v0', 'v1'),
                'return-void',
            ])
            # Nothing of the HAL before the return; the stock body is still there, behind it.
            self.assertFalse([l for l in block if 'ISession' in l or 'getFreshDaemon' in l])
            self.assertEqual(sum('->removeEnrollments([I)V' in l for l in body[end:]), 1)

        for modality, name, locals_ in (('fingerprint', 'FingerprintEnrollClient', 4), ('face', 'FaceEnrollClient', 10)):
            c = cls(modality, name)
            body = tree.method(f'{modality}/aidl/{name}')
            self.assertEqual(body[0], f'.locals {locals_}')
            end = body.index('return-void')
            self.assertEqual(body[1:end + 1][2:], [
                'invoke-static {v0, v1}, Landroid/util/Slog;->w(Ljava/lang/String;Ljava/lang/String;)I',
                'const/4 v0, 0x2',
                'const/4 v1, 0x0',
                f'invoke-virtual {{p0, v0, v1}}, {c}->onError(II)V',
                f'iget-object v0, p0, {c}->mCallback:{CB}',
                FINISH % ('v0', 'v1'),
                'return-void',
            ])
            self.assertEqual(body[1], f'const-string v0, "{name}"')
            self.assertEqual(sum('->doEnroll()V' in l for l in body[end:]), 1)

        # Apostrophes as a decoded jar writes them.
        text = (tree.root / 'smali' / PKG / 'face/aidl/FaceRemovalClient.smali').read_text()
        self.assertIn('"OneOS: the faces on this phone are One UI\\\'s; the guest removes none"', text)

    def test_a_second_run_changes_nothing(self):
        tree = Tree(self)
        self.assertEqual(tree.run().returncode, 0)
        once = tree.snapshot()
        again = tree.run()
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(again.stdout, '')
        self.assertEqual(again.stderr.count('already patched'), 4)
        self.assertEqual(tree.snapshot(), once)

    def test_a_decoded_built_jar_is_known_as_patched(self):
        # baksmali writes no comments and its own labels: the log line is what marks the block.
        tree = Tree(self)
        self.assertEqual(tree.run().returncode, 0)
        for k in FOUR:
            p = tree.root / 'smali' / PKG / (k + '.smali')
            text = '\n'.join(l for l in p.read_text().split('\n') if not l.strip().startswith('#'))
            p.write_text(text.replace('oneos_bio_try_end', 'try_end_9').replace('oneos_bio_try', 'try_start_9')
                         .replace('oneos_bio_catch', 'catch_9').replace('oneos_bio_finish', 'goto_9'))
        once = tree.snapshot()
        self.assertNotIn('guest_no_biometric_writes', ''.join(once.values()))
        again = tree.run()
        self.assertEqual((again.returncode, again.stdout), (0, ''), again.stderr)
        self.assertEqual(tree.snapshot(), once)

    def test_one_dex_per_changed_class(self):
        tree = Tree(self, dex={'face/aidl/FaceRemovalClient': 'smali_classes3',
                               'face/aidl/FaceEnrollClient': 'smali_classes3'})
        done = tree.run()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, 'smali\nsmali_classes3\n')
        self.assertEqual(tree.method('face/aidl/FaceRemovalClient', 'smali_classes3')[0], '.locals 5')

    def test_a_framework_without_the_clients_is_left_alone(self):
        tree = Tree(self, change={k: None for k in FOUR})
        before = tree.snapshot()
        done = tree.run()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, '')
        self.assertIn('nothing to do', done.stderr)
        self.assertEqual(tree.snapshot(), before)

    def refused(self, change, words):
        tree = Tree(self, change=change)
        before = tree.snapshot()
        done = tree.run()
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(done.stdout, '')
        self.assertIn(words, done.stderr)
        self.assertEqual(tree.snapshot(), before, 'a refused framework must not be half patched')

    def test_another_shape_is_refused(self):
        stock = removal('fingerprint', 'FingerprintRemovalClient')
        fp_enroll = enroll('fingerprint', 'FingerprintEnrollClient')
        key, ekey = 'fingerprint/aidl/FingerprintRemovalClient', 'fingerprint/aidl/FingerprintEnrollClient'
        # The last of the four is the odd one: the three before it must stay unwritten.
        face = 'face/aidl/FaceEnrollClient'
        face_enroll = enroll('face', 'FaceEnrollClient')
        cases = [
            ({key: stock.replace('    ' + FINISH % ('v1', 'v2') + '\n', '')}, 'no onClientFinished() failure branch'),
            ({key: stock.replace('.locals 3', '.registers 4')}, 'unexpected startHalOperation() header'),
            ({key: stock.replace('startHalOperation()V', 'startHalOperation(I)V')}, 'found 0 times'),
            ({key: stock.replace('->removeEnrollments([I)V', '->enumerateEnrollments()V')}, "does not reach ISession's remove"),
            ({key: stock.replace(f'iget-object v1, p0, {cls("fingerprint", "FingerprintRemovalClient")}->mCallback:{CB}',
                                 f'invoke-virtual {{p0}}, {BASE}->getCallback(){CB}\n\n    move-result-object v1')},
             'mCallback read through 0 classes'),
            ({ekey: fp_enroll.replace('.method private doEnroll()V', '.method public doEnroll()V')},
             'doEnroll()V reaches ISession\'s enroll past startHalOperation()'),
            ({face: face_enroll.replace(f'    invoke-virtual {{p0, v1, v2}}, {cls("face", "FaceEnrollClient")}->onError(II)V\n', '')},
             'no onError(II) failure branch'),
            ({face: None}, 'no FaceEnrollClient next to the others'),
        ]
        for change, words in cases:
            with self.subTest(words=words):
                self.refused(change, words)

    def test_another_caller_of_the_hal_is_refused(self):
        vendor = f'''.class public {S}fingerprint/aidl/VendorEraseClient;
.super Ljava/lang/Object;

.method public erase()V
    .locals 2

    invoke-interface {{v0, v1}}, Landroid/hardware/biometrics/fingerprint/ISession;->removeEnrollments([I)V

    return-void
.end method
'''
        self.refused({'fingerprint/aidl/VendorEraseClient': vendor}, 'VendorEraseClient.smali: calls ISession')
        hidl = ADAPTER.replace('HidlToAidlSessionAdapter', 'Fingerprint21RemovalClient')
        self.refused({'fingerprint/hidl/Fingerprint21RemovalClient': hidl},
                     "Fingerprint21RemovalClient.smali: calls the HIDL HAL's")
        # Also where the four are missing: such a framework is not "nothing to do".
        gone = {k: None for k in FOUR}
        self.refused({**gone, 'fingerprint/aidl/VendorEraseClient': vendor}, 'VendorEraseClient.smali: calls ISession')

    def test_the_field_owner_of_the_build_is_kept(self):
        # R8 code names mCallback through the class that declares it.
        key = 'fingerprint/aidl/FingerprintRemovalClient'
        tree = Tree(self, change={key: removal('fingerprint', 'FingerprintRemovalClient', owner=BASE)})
        self.assertEqual(tree.run().returncode, 0)
        body = tree.method(key)
        block = body[:body.index('return-void')]
        self.assertIn(f'iget-object v0, p0, {BASE}->mCallback:{CB}', block)

    def test_without_the_getters_a_removal_only_finishes(self):
        tree = Tree(self, change={'BaseClientMonitor': BASE_MONITOR.replace('getListener()', 'listener()')})
        done = tree.run()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('finishes without an error to its caller', done.stderr)
        c = cls('face', 'FaceRemovalClient')
        body = tree.method('face/aidl/FaceRemovalClient')
        self.assertEqual(body[0], '.locals 3')
        self.assertEqual(body[4:body.index('return-void') + 1], [
            f'iget-object v0, p0, {c}->mCallback:{CB}',
            'const/4 v1, 0x0',
            FINISH % ('v0', 'v1'),
            'return-void',
        ])
        # Enrollment does not need them.
        self.assertIn(f'invoke-virtual {{p0, v0, v1}}, {cls("face", "FaceEnrollClient")}->onError(II)V',
                      tree.method('face/aidl/FaceEnrollClient'))


class Wiring(unittest.TestCase):
    def test_make_overlays_runs_it_after_the_cleanup_guard(self):
        text = (ROOT / 'scripts/make-overlays.sh').read_text()
        line = 'KEY_DIRS="$KEY_DIRS $(python3 -I "$ROOT/switcher/patches/%s.py" "$WORK/services")"\n'
        cleanup, writes = line % 'guest_no_biometric_cleanup', line % 'guest_no_biometric_writes'
        self.assertEqual(text.count(writes), 1)
        self.assertLess(text.index(cleanup), text.index(writes))
        self.assertLess(text.index(writes), text.index(line % 'guest_force_aidl_fingerprint'))
        # A set records the guard in guards.txt's first line; nothing on the phone asks for the
        # word, so the sets built before it keep starting.
        header = next(l for l in text.split('\n') if 'echo "# framework overlays built by' in l)
        self.assertRegex(header, r'\bbiometric, biowrites,')
        for script in ('guest_prepare.sh', 'oneos_switch.sh', 'guest_google.sh'):
            self.assertNotIn('biowrites', (ROOT / 'switcher/assets' / script).read_text(), script)


if __name__ == '__main__':
    unittest.main()
