use crate::FaceError;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

const MAGIC: &[u8; 8] = b"FHALLOK1";
const TIMED_LOCKOUT_EVERY: u32 = 5;
// From this many failures in a row each further lockout is the long one. There is no
// permanent lockout: a permanent one is cleared by resetLockout() after a credential
// unlock, and the guest's own screen lock (patches/guest_local_lock.py) answers a
// credential without the framework's biometric lockout reset, so nothing would ever
// clear it and face unlock would stay off until lockout.bin was deleted by hand.
const LONG_LOCKOUT_AT: u32 = 20;
const TIMED_LOCKOUT_MILLIS: u64 = 30_000;
const LONG_LOCKOUT_MILLIS: u64 = 5 * 60_000;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LockoutState {
    None,
    Timed { remaining_millis: u64 },
    /// Never produced any more (see LONG_LOCKOUT_AT); kept for the callers' match arms.
    Permanent,
}

#[derive(Clone, Copy, Debug, Default)]
struct LockoutRecord {
    failed_attempts: u32,
    timed_until_millis: u64,
    permanent: bool,
}

pub struct LockoutTracker {
    directory: PathBuf,
    path: PathBuf,
    record: Mutex<LockoutRecord>,
}

impl LockoutTracker {
    pub fn new(root: impl AsRef<Path>, user_id: i32) -> Result<Self, FaceError> {
        if user_id < 0 {
            return Err(FaceError::InvalidArgument);
        }
        let face_data_directory = root.as_ref().join(user_id.to_string()).join("facedata");
        fs::create_dir_all(&face_data_directory)?;
        fs::set_permissions(&face_data_directory, fs::Permissions::from_mode(0o770))?;
        let directory = face_data_directory.join("facehal");
        fs::create_dir_all(&directory)?;
        fs::set_permissions(&directory, fs::Permissions::from_mode(0o700))?;
        let path = directory.join("lockout.bin");
        let record = read_record(&path)?;
        Ok(Self {
            directory,
            path,
            record: Mutex::new(record),
        })
    }

    pub fn status(&self) -> Result<LockoutState, FaceError> {
        let mut record = self.record.lock().unwrap();
        let now = now_millis()?;
        // A record from a build that still had the permanent lockout: one long wait instead.
        if record.permanent {
            record.permanent = false;
            record.timed_until_millis = now.saturating_add(LONG_LOCKOUT_MILLIS);
            self.persist(*record)?;
        }
        // A deadline further away than the wait that belongs to the count (the clock was
        // set back) is cut to that wait.
        let maximum_timed_until = now.saturating_add(lockout_millis(record.failed_attempts));
        if !record.permanent && record.timed_until_millis > maximum_timed_until {
            record.timed_until_millis = maximum_timed_until;
            self.persist(*record)?;
        }
        if !record.permanent && record.timed_until_millis != 0 && record.timed_until_millis <= now {
            record.timed_until_millis = 0;
            self.persist(*record)?;
        }
        Ok(state_for(*record, now))
    }

    #[allow(clippy::manual_is_multiple_of)]
    pub fn record_failure(&self) -> Result<LockoutState, FaceError> {
        let mut record = self.record.lock().unwrap();
        let now = now_millis()?;
        record.failed_attempts = record.failed_attempts.saturating_add(1);
        record.permanent = false;
        if record.failed_attempts % TIMED_LOCKOUT_EVERY == 0 {
            record.timed_until_millis =
                now.saturating_add(lockout_millis(record.failed_attempts));
        }
        self.persist(*record)?;
        Ok(state_for(*record, now))
    }

    pub fn record_success(&self) -> Result<(), FaceError> {
        self.reset()
    }

    pub fn reset(&self) -> Result<(), FaceError> {
        let mut record = self.record.lock().unwrap();
        *record = LockoutRecord::default();
        self.persist(*record)
    }

    fn persist(&self, record: LockoutRecord) -> Result<(), FaceError> {
        let temp_path = self.path.with_extension("tmp");
        let mut file = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .mode(0o600)
            .open(&temp_path)?;
        file.write_all(MAGIC)?;
        file.write_all(&record.failed_attempts.to_le_bytes())?;
        file.write_all(&record.timed_until_millis.to_le_bytes())?;
        file.write_all(&[u8::from(record.permanent)])?;
        file.sync_all()?;
        fs::rename(temp_path, &self.path)?;
        fs::set_permissions(&self.path, fs::Permissions::from_mode(0o600))?;
        File::open(&self.directory)?.sync_all()?;
        Ok(())
    }
}

fn read_record(path: &Path) -> Result<LockoutRecord, FaceError> {
    if !path.exists() {
        return Ok(LockoutRecord::default());
    }
    let mut bytes = Vec::new();
    File::open(path)?.read_to_end(&mut bytes)?;
    if bytes.len() != 21 || &bytes[..8] != MAGIC {
        return Err(FaceError::CorruptStore);
    }
    let failed_attempts = u32::from_le_bytes(bytes[8..12].try_into().unwrap());
    let timed_until_millis = u64::from_le_bytes(bytes[12..20].try_into().unwrap());
    let permanent = match bytes[20] {
        0 => false,
        1 => true,
        _ => return Err(FaceError::CorruptStore),
    };
    if permanent && timed_until_millis != 0 {
        return Err(FaceError::CorruptStore);
    }
    Ok(LockoutRecord {
        failed_attempts,
        timed_until_millis,
        permanent,
    })
}

/// The wait after the given number of failures in a row.
fn lockout_millis(failed_attempts: u32) -> u64 {
    if failed_attempts >= LONG_LOCKOUT_AT {
        LONG_LOCKOUT_MILLIS
    } else {
        TIMED_LOCKOUT_MILLIS
    }
}

fn state_for(record: LockoutRecord, now: u64) -> LockoutState {
    if record.permanent {
        LockoutState::Permanent
    } else if record.timed_until_millis > now {
        LockoutState::Timed {
            remaining_millis: record.timed_until_millis - now,
        }
    } else {
        LockoutState::None
    }
}

fn now_millis() -> Result<u64, FaceError> {
    Ok(SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| FaceError::InvalidArgument)?
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_root() -> PathBuf {
        std::env::temp_dir().join(format!(
            "facehal-lockout-{}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ))
    }

    #[test]
    fn timed_lockout_persists_and_reset_clears_it() {
        let root = temp_root();
        let tracker = LockoutTracker::new(&root, 0).unwrap();
        for _ in 0..4 {
            assert_eq!(tracker.record_failure().unwrap(), LockoutState::None);
        }
        assert!(matches!(
            tracker.record_failure().unwrap(),
            LockoutState::Timed {
                remaining_millis: 1..
            }
        ));
        drop(tracker);

        let tracker = LockoutTracker::new(&root, 0).unwrap();
        assert!(matches!(
            tracker.status().unwrap(),
            LockoutState::Timed {
                remaining_millis: 1..
            }
        ));
        tracker.reset().unwrap();
        assert_eq!(tracker.status().unwrap(), LockoutState::None);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn twentieth_failure_is_a_long_wait_not_permanent() {
        let root = temp_root();
        let tracker = LockoutTracker::new(&root, 10).unwrap();
        for _ in 0..19 {
            tracker.record_failure().unwrap();
        }
        match tracker.record_failure().unwrap() {
            LockoutState::Timed { remaining_millis } => {
                assert!(remaining_millis > TIMED_LOCKOUT_MILLIS);
                assert!(remaining_millis <= LONG_LOCKOUT_MILLIS);
            }
            other => panic!("unexpected state {other:?}"),
        }
        drop(tracker);
        assert!(matches!(
            LockoutTracker::new(&root, 10).unwrap().status().unwrap(),
            LockoutState::Timed { .. }
        ));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn permanent_record_of_an_older_build_becomes_a_wait() {
        let root = temp_root();
        let tracker = LockoutTracker::new(&root, 0).unwrap();
        tracker
            .persist(LockoutRecord {
                failed_attempts: 20,
                timed_until_millis: 0,
                permanent: true,
            })
            .unwrap();
        drop(tracker);
        let tracker = LockoutTracker::new(&root, 0).unwrap();
        assert!(matches!(
            tracker.status().unwrap(),
            LockoutState::Timed { .. }
        ));
        tracker.record_success().unwrap();
        assert_eq!(tracker.status().unwrap(), LockoutState::None);
        fs::remove_dir_all(root).unwrap();
    }
}
