"""Lazy screen/OCR service; never imported by the Solo worker."""
import ctypes
from ctypes import wintypes
import threading
from pathlib import Path
import capture_utils

COD_PROCESS_NAMES={'cod.exe','codhq.exe','modernwarfare.exe','blackops.exe','blackops6.exe','blackops7.exe','callofduty.exe'}

def is_call_of_duty_window(process_name,title=''):
    name=Path(str(process_name or '')).name.casefold()
    title=str(title or '').casefold()
    return name in COD_PROCESS_NAMES or ('call of duty' in title and name not in ('python.exe','pythonw.exe','vintendocodtracker.exe'))

def select_game_output(outputs,windows):
    """Choose the DXGI output containing the largest visible Call of Duty window."""
    candidates=[]
    by_handle={int(o['monitor_handle']):o for o in outputs if o.get('monitor_handle') is not None}
    for window in windows:
        if not is_call_of_duty_window(window.get('process_name'),window.get('title')):continue
        output=by_handle.get(int(window.get('monitor_handle',-1)))
        if output is None:continue
        area=max(0,int(window.get('width',0)))*max(0,int(window.get('height',0)))
        if area:candidates.append((area,output,window))
    if not candidates:raise RuntimeError('Could not find a visible Call of Duty game window on a DXcam display. Start the game and try Ctrl + F8 again.')
    _,output,window=max(candidates,key=lambda item:item[0])
    return output,window

class CaptureEngine:
    def __init__(self):
        import dxcam
        self.dxcam=dxcam; capture_utils.dxcam=dxcam
        self.camera=None; self.camera_id=None; self.ocr=None; self.lock=threading.RLock()
    def outputs(self): return capture_utils.get_outputs()
    def game_windows(self):
        if not hasattr(ctypes,'windll'):raise RuntimeError('Automatic game-monitor detection is available only on Windows.')
        import psutil
        user32=ctypes.windll.user32
        class RECT(ctypes.Structure):_fields_=[('left',ctypes.c_long),('top',ctypes.c_long),('right',ctypes.c_long),('bottom',ctypes.c_long)]
        result=[];callback_type=ctypes.WINFUNCTYPE(ctypes.c_bool,ctypes.c_void_p,ctypes.c_void_p)
        user32.IsWindowVisible.argtypes=[wintypes.HWND];user32.IsWindowVisible.restype=wintypes.BOOL
        user32.GetWindowRect.argtypes=[wintypes.HWND,ctypes.POINTER(RECT)];user32.GetWindowRect.restype=wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)];user32.GetWindowThreadProcessId.restype=wintypes.DWORD
        user32.GetWindowTextLengthW.argtypes=[wintypes.HWND];user32.GetWindowTextLengthW.restype=ctypes.c_int
        user32.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int];user32.GetWindowTextW.restype=ctypes.c_int
        user32.MonitorFromWindow.argtypes=[wintypes.HWND,wintypes.DWORD];user32.MonitorFromWindow.restype=wintypes.HMONITOR
        user32.EnumWindows.argtypes=[callback_type,wintypes.LPARAM];user32.EnumWindows.restype=wintypes.BOOL
        def collect(hwnd,_):
            if not user32.IsWindowVisible(hwnd):return True
            rect=RECT()
            if not user32.GetWindowRect(hwnd,ctypes.byref(rect)):return True
            width,height=rect.right-rect.left,rect.bottom-rect.top
            if width<640 or height<360:return True
            pid=ctypes.c_ulong();user32.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
            try:process_name=psutil.Process(pid.value).name()
            except (psutil.Error,OSError):process_name=''
            if not is_call_of_duty_window(process_name):
                length=user32.GetWindowTextLengthW(hwnd)
                title=ctypes.create_unicode_buffer(min(max(length+1,2),1024));user32.GetWindowTextW(hwnd,title,len(title));title=title.value
            else:
                title=ctypes.create_unicode_buffer(1024);user32.GetWindowTextW(hwnd,title,len(title));title=title.value
            if not is_call_of_duty_window(process_name,title):return True
            monitor=user32.MonitorFromWindow(hwnd,2) # nearest monitor if window straddles displays
            result.append({'hwnd':int(hwnd),'pid':int(pid.value),'process_name':process_name,'title':title,
                           'monitor_handle':int(monitor),'width':width,'height':height})
            return True
        user32.EnumWindows(callback_type(collect),0)
        return result
    def game_output(self):return select_game_output(self.outputs(),self.game_windows())
    def _select_camera(self,identity):
        if self.camera_id!=identity:
            if self.camera:self.camera.release()
            self.camera=self.dxcam.create(device_idx=identity[0],output_idx=identity[1],output_color='RGB',backend='dxgi',max_buffer_len=2)
            self.camera_id=identity
    def grab(self,output,roi=None):
        with self.lock:
            identity=(output['device'],output['output'])
            self._select_camera(identity)
            frame=self.camera.grab(region=tuple(roi) if roi else None,new_frame_only=False)
            if frame is None:raise RuntimeError('No screen frame available. Keep the selected display awake.')
            return frame.copy()
    def grab_game_output(self,output):
        """Capture only the selected game monitor and restore OCR camera state."""
        with self.lock:
            identity=(output['device'],output['output']);previous=self.camera_id
            try:
                self._select_camera(identity)
                frame=self.camera.grab(new_frame_only=False)
                if frame is None:raise RuntimeError('DXcam returned no frame for the game display.')
                frame=frame.copy()
                if frame.ndim!=3 or frame.shape[0]<360 or frame.shape[1]<640 or not frame.size or not frame.any():
                    raise RuntimeError('The game display returned an empty or black frame; no screenshot was saved.')
                expected=(int(output['height']),int(output['width']))
                if frame.shape[:2]!=expected:
                    raise RuntimeError(f'Captured frame dimensions {frame.shape[1]}×{frame.shape[0]} do not match the display resolution {output["width"]}×{output["height"]}.')
                return frame
            finally:
                if previous is None:
                    if self.camera:self.camera.release()
                    self.camera=None;self.camera_id=None
                elif previous!=identity:self._select_camera(previous)
    def recognize(self,frame):
        with self.lock:
            if self.ocr is None:
                from rapidocr_onnxruntime import RapidOCR
                # Scoreboard text is horizontal; omit angle classification and
                # cap ONNX threads so background monitoring cannot saturate CPU.
                self.ocr=RapidOCR(use_cls=False,intra_op_num_threads=2,inter_op_num_threads=1)
            result,_=self.ocr(frame)
            text,lines,boxes=capture_utils.group_ocr_lines(result)
            return text,lines,boxes
    def close(self):
        with self.lock:
            if self.camera:self.camera.release()
            self.camera=None;self.camera_id=None

class StableDetector:
    def __init__(self,frames=3):self.frames=frames;self.key=None;self.count=0;self.empty=0;self.armed=False
    def observe(self,text,signal):
        if not signal:
            self.empty+=1;self.key=None;self.count=0
            if self.empty>=3:self.armed=True
            return False
        self.empty=0; self.count=self.count+1 if text==self.key else 1;self.key=text
        if self.armed and self.count>=self.frames:self.armed=False;self.count=0;return True
        return False
