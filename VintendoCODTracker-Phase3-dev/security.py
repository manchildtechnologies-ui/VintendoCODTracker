import base64
import ctypes

def protect_text(value: str) -> str:
    """Encrypt a user-entered webhook for this Windows account with DPAPI."""
    if not value:
        return ""

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    raw = value.encode("utf-8")
    source = ctypes.create_string_buffer(raw)
    source_blob = DataBlob(len(raw), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
    target_blob = DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_wchar_p,
                                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.c_ulong, ctypes.POINTER(DataBlob)]
    crypt32.CryptProtectData.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    ok = crypt32.CryptProtectData(
        ctypes.byref(source_blob), "Vintendo Scoreboard Capture", None, None, None, 0,
        ctypes.byref(target_blob),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "Windows could not protect the webhook setting")
    try:
        encrypted = ctypes.string_at(target_blob.pbData, target_blob.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        kernel32.LocalFree(target_blob.pbData)

def unprotect_text(value: str) -> str:
    if not value:
        return ""

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    raw = base64.b64decode(value)
    source = ctypes.create_string_buffer(raw)
    source_blob = DataBlob(len(raw), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
    target_blob = DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_void_p,
                                           ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                           ctypes.c_ulong, ctypes.POINTER(DataBlob)]
    crypt32.CryptUnprotectData.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(source_blob), None, None, None, None, 0, ctypes.byref(target_blob)
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "Windows could not unlock the saved webhook")
    try:
        return ctypes.string_at(target_blob.pbData, target_blob.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree(target_blob.pbData)
