from ntfsmnt.control.lock import OperationFileLock


def test_acquire_creates_file(tmp_path):
    path = tmp_path / "ntfsmnt.lock"
    lock = OperationFileLock(path)

    assert lock.acquire() is True
    try:
        assert path.exists()
    finally:
        lock.release()


def test_second_acquire_on_a_second_fd_is_busy(tmp_path):
    path = tmp_path / "ntfsmnt.lock"
    first = OperationFileLock(path)
    second = OperationFileLock(path)
    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.release()


def test_release_allows_reacquire(tmp_path):
    path = tmp_path / "ntfsmnt.lock"
    first = OperationFileLock(path)
    second = OperationFileLock(path)

    first.acquire()
    first.release()

    assert second.acquire() is True
    second.release()


def test_context_manager_releases_on_exception(tmp_path):
    path = tmp_path / "ntfsmnt.lock"

    try:
        with OperationFileLock(path):
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert OperationFileLock(path).acquire() is True


def test_missing_parent_is_busy_not_a_crash(tmp_path):
    lock = OperationFileLock(tmp_path / "nope" / "ntfsmnt.lock")

    assert lock.acquire() is False
    lock.release()  # harmless when nothing was acquired
