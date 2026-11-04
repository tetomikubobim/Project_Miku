"""Windows Job Object: 서버 프로세스가 어떤 식으로든 죽으면(콘솔 X 버튼, 작업 관리자 종료, 크래시 포함)
여기에 등록된 자식 프로세스(GPT-SoVITS, RVC 와 그 하위 프로세스)도 OS 가 같이 끈다.
Windows 가 아니거나 실패하면 아무 것도 하지 않는다 (기존 stop()/taskkill 경로는 그대로 동작)."""
import os

_job = None


def _create_job():
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in (
            "ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

    class EXT(ctypes.Structure):
        _fields_ = [("Basic", BASIC), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]

    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    info = EXT()
    info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return k32, job


def attach(pid):
    """자식 프로세스를 job 에 넣는다. 성공하면 True."""
    global _job
    if os.name != "nt":
        return False
    try:
        import ctypes
        if _job is None:
            _job = _create_job()  # 핸들을 모듈에 붙들어 둔다 (서버가 죽으면 OS 가 닫고, 그 순간 자식도 종료)
        k32, job = _job
        h = k32.OpenProcess(0x0001 | 0x0100, False, int(pid))  # TERMINATE | SET_QUOTA
        if not h:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not k32.AssignProcessToJobObject(job, h):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            k32.CloseHandle(h)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[killjob] 자식 프로세스 묶기 실패 (정상 종료 때만 같이 꺼져요): {e!r}")
        return False
