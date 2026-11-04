"""Windows Job Object: 런처가 어떤 식으로든 죽으면(창 X, 작업 관리자 종료, 크래시) 런처가 켠 프로세스(서버, 클라, 그 하위 프로세스)를
OS 가 같이 끈다. 서버가 GPT-SoVITS/RVC 를 묶는 killjob.py 와 같은 방식이다. Windows 가 아니거나 실패하면 아무것도 하지 않는다."""
import os


class JobObject:
    def __init__(self, log=print):
        self._k32 = None
        self._job = None
        self._log = log
        if os.name == "nt":
            try:
                self._create()
            except Exception as e:  # noqa: BLE001
                self._log(f"[launcher] 프로세스 묶기를 준비하지 못했어요 (런처를 정상 종료할 때만 같이 꺼져요): {e!r}")

    def _create(self):
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
            _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

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
        self._k32, self._job = k32, job   # 핸들을 들고 있다가 런처가 죽으면 OS 가 닫고, 그 순간 자식들이 종료된다

    @property
    def active(self):
        return self._job is not None

    def attach(self, pid):
        """pid 와 앞으로 그 프로세스가 만드는 하위 프로세스를 job 에 넣는다. 성공하면 True."""
        if self._job is None:
            return False
        try:
            import ctypes
            h = self._k32.OpenProcess(0x0001 | 0x0100, False, int(pid))  # TERMINATE | SET_QUOTA
            if not h:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if not self._k32.AssignProcessToJobObject(self._job, h):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                self._k32.CloseHandle(h)
            return True
        except Exception as e:  # noqa: BLE001
            self._log(f"[launcher] pid {pid} 를 묶지 못했어요 (런처를 정상 종료할 때만 같이 꺼져요): {e!r}")
            return False
