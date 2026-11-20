from __future__ import annotations
import os,sys,json,time,threading,subprocess,shutil,logging,re,uuid,ctypes,sqlite3,queue
from datetime import datetime,timezone
from urllib.parse import quote
from ctypes import wintypes
from logging.handlers import RotatingFileHandler
from pathlib import Path
from PySide6.QtCore import Qt,QUrl,QTimer,QObject,QEvent,Signal,QRunnable,QThreadPool,QRect,QAbstractNativeEventFilter,QSize,QPoint,QPointF,QRectF,QPropertyAnimation,QEasingCurve
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtGui import QIcon,QPixmap,QImage,QPainter,QPainterPath,QPen,QColor,QBrush,QPalette,QAction,QFont,QFontDatabase,QLinearGradient,QRadialGradient,QDesktopServices,QMovie
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,QPushButton,QListWidget,QListWidgetItem,QStackedWidget,QLineEdit,QComboBox,QCheckBox,QPlainTextEdit,QTableWidget,QTableWidgetItem,QHeaderView,QFileDialog,QMessageBox,QFormLayout,QSpinBox,QDialog,QDialogButtonBox,QSystemTrayIcon,QMenu,QSplitter,QGroupBox,QScrollArea,QTabWidget,QFrame,QButtonGroup,QInputDialog,QSlider,QDoubleSpinBox,QProgressBar,QSizePolicy)
import engine as E
import updater
import meta_automation
import codmunity_player_stats as codmunity_player_stats
import codmunity_camo as codmunity_camo
from version import APP_VERSION

SCOREBOARD_HOTKEY_ID=0x5638
COD_AI_SCREENSHOT_HOTKEY_ID=0x5639
AUTO_OCR_INTERVAL_SECONDS=30
NO_CONSOLE_FLAG=getattr(subprocess,'CREATE_NO_WINDOW',0)
# Pixel bounds of the scoreboard in the supplied 2560×1440 reference image.
# Other supported resolutions scale these same bounds proportionally.
SCOREBOARD_REFERENCE_SIZE=(2560,1440)
SCOREBOARD_REFERENCE_BOUNDS=(202,487,2156,464)
def _scaled_scoreboard_bounds(size):
    scale=size[0]/SCOREBOARD_REFERENCE_SIZE[0]
    return tuple(int(value*scale+0.5) for value in SCOREBOARD_REFERENCE_BOUNDS)
SCOREBOARD_REGION_TEMPLATES={
    size:_scaled_scoreboard_bounds(size)
    for size in ((1920,1080),(2560,1440),(3840,2160))
}
# Multiplayer template captures only the left team table (player, score, kills,
# objective time), excluding the opposing team and match banner.
MULTIPLAYER_REFERENCE_SIZE=(2560,1440)
MULTIPLAYER_REFERENCE_BOUNDS=(126,408,1148,370)
def _scaled_multiplayer_bounds(size):
    scale=size[0]/MULTIPLAYER_REFERENCE_SIZE[0]
    return tuple(int(value*scale+0.5) for value in MULTIPLAYER_REFERENCE_BOUNDS)
MULTIPLAYER_REGION_TEMPLATES={
    size:_scaled_multiplayer_bounds(size)
    for size in ((1920,1080),(2560,1440),(3840,2160))
}

def worker_alive(worker):
    """True when a background thread worker exists and is running."""
    try:return bool(worker is not None and worker.is_alive())
    except (AttributeError,RuntimeError):return False
def visible_cinematic_rows(rows,selected_id=None):
    """Queue rows worth showing. Finished jobs leave the list once they have nothing left to post: completed jobs stay only while their MP4 file still exists, failed jobs stay only while selected so their error stays visible."""
    visible=[]
    for row in rows or []:
        status=str((row or {}).get('status') or '')
        if status not in ('completed','failed'):
            visible.append(row);continue
        if selected_id is not None and str(row.get('id'))==str(selected_id):
            visible.append(row);continue
        if status=='completed':
            video=str(row.get('video') or '')
            if video and Path(video).is_file():
                visible.append(row);continue
    return visible

def post_file_to_discord_webhook(webhook_url,file_path,timeout=120,attempts=3):
    """Post one MP4 to a Discord webhook, retrying transient network/TLS failures once. HTTP error statuses raise immediately."""
    import requests
    last_error=None
    separator='&' if '?' in webhook_url else '?'
    url=webhook_url+separator+'wait=true'
    for _ in range(max(1,attempts)):
        try:
            with open(file_path,'rb') as handle:
                response=requests.post(url,files={'files[0]':(Path(file_path).name,handle,'video/mp4')},headers={'User-Agent':'VintendoCODTracker/1.0'},timeout=timeout)
            if response.status_code in (200,204):return response.status_code
            raise RuntimeError('Discord returned HTTP %s: %s'%(response.status_code,response.text[:200]))
        except Exception as exc:last_error=exc
    raise last_error

def cinematic_thumbnail_source(row,thumb_file):
    """Best existing still for a grid tile: the source screenshot, else a cached extracted frame."""
    shot=str((row or {}).get('image') or '')
    if shot and Path(shot).is_file():return shot
    thumb=str(thumb_file or '')
    if thumb and Path(thumb).is_file():return thumb
    return ''

def format_cinematic_duration(seconds):
    """Format seconds as m:ss for thumbnail badges and the preview clock."""
    try:total=int(round(float(seconds)))
    except (TypeError,ValueError):return ''
    if total<0:return ''
    return f"{total//60}:{total%60:02d}"

def short_cinematic_name(name,limit=24):
    """Shorten a filename for a gallery tile, keeping its extension visible."""
    name=str(name or '')
    if len(name)<=limit:return name
    stem,dot,ext=name.rpartition('.')
    if dot and len(ext)<=5:
        keep=max(8,limit-len(ext)-2)
        return stem[:keep]+chr(8230)+'.'+ext
    return name[:max(8,limit-1)]+chr(8230)

def probe_cinematic_duration(path):
    """Best-effort video duration in seconds via ffprobe; None when unavailable."""
    try:
        import shutil,subprocess
        exe=shutil.which('ffprobe')
        if not exe or not Path(path).is_file():return None
        out=subprocess.run([exe,'-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(path)],capture_output=True,text=True,timeout=3,creationflags=NO_CONSOLE_FLAG)
        value=float((out.stdout or '').strip())
        return value if value>0 else None
    except Exception:return None

class CinematicTile(QFrame):
    """One gallery tile: thumbnail, duration badge, short name, own delete button."""
    def __init__(self,cid,video,title,badge,pixmap,on_select,on_delete,parent=None):
        super().__init__(parent);self.cid=cid;self.video=video;self.on_select=on_select
        self.setObjectName('cinematicTile');self.setCursor(Qt.PointingHandCursor);self.setProperty('selected',False)
        layout=QVBoxLayout(self);layout.setContentsMargins(6,6,6,6);layout.setSpacing(5)
        stage=QGridLayout();stage.setContentsMargins(0,0,0,0);stage.setSpacing(0)
        self.image=QLabel();self.image.setObjectName('cinematicThumb');self.image.setAlignment(Qt.AlignCenter);self.image.setMinimumSize(120,68)
        if pixmap is not None and not pixmap.isNull():self.image.setPixmap(pixmap.scaled(360,203,Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation))
        else:self.image.setText('No preview')
        stage.addWidget(self.image,0,0)
        self.delete_button=QPushButton(chr(10005));self.delete_button.setObjectName('cinematicDelete');self.delete_button.setFixedSize(24,24)
        self.delete_button.setToolTip('Delete this cinematic video');self.delete_button.setCursor(Qt.PointingHandCursor)
        self.delete_button.clicked.connect(lambda _checked=False,c=cid,v=video:on_delete(c,v))
        stage.addWidget(self.delete_button,0,0,Qt.AlignTop|Qt.AlignRight)
        if badge:
            badge_label=QLabel(badge);badge_label.setObjectName('cinematicBadge');stage.addWidget(badge_label,0,0,Qt.AlignBottom|Qt.AlignLeft)
        layout.addLayout(stage,1)
        name=QLabel(title);name.setObjectName('cinematicName');name.setToolTip(title);layout.addWidget(name)
    def mousePressEvent(self,event):
        super().mousePressEvent(event)
        if event.button()==Qt.LeftButton:self._select()
    def _select(self):
        # Stored by the window when the tile is created.
        callback=getattr(self,'on_select',None)
        if callback:callback(self.cid)
    def set_selected(self,value):
        self.setProperty('selected',bool(value));self.style().unpolish(self);self.style().polish(self)

class ScreenshotTile(QFrame):
    """One screenshot tile: thumbnail, own X delete button, short file name."""
    def __init__(self,path,title,badge,pixmap,on_select,on_delete,parent=None):
        super().__init__(parent);self.path=path;self.on_select=on_select
        self.setObjectName('cinematicTile');self.setCursor(Qt.PointingHandCursor);self.setProperty('selected',False)
        layout=QVBoxLayout(self);layout.setContentsMargins(6,6,6,6);layout.setSpacing(5)
        stage=QGridLayout();stage.setContentsMargins(0,0,0,0);stage.setSpacing(0)
        self.image=QLabel();self.image.setObjectName('cinematicThumb');self.image.setAlignment(Qt.AlignCenter);self.image.setMinimumSize(120,68)
        if pixmap is not None and not pixmap.isNull():self.image.setPixmap(pixmap.scaled(360,203,Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation))
        else:self.image.setText('No preview')
        stage.addWidget(self.image,0,0)
        self.delete_button=QPushButton(chr(10005));self.delete_button.setObjectName('cinematicDelete');self.delete_button.setFixedSize(24,24)
        self.delete_button.setToolTip('Delete this screenshot');self.delete_button.setCursor(Qt.PointingHandCursor)
        self.delete_button.clicked.connect(lambda _checked=False,p=path:on_delete(p))
        stage.addWidget(self.delete_button,0,0,Qt.AlignTop|Qt.AlignRight)
        if badge:
            badge_label=QLabel(badge);badge_label.setObjectName('cinematicBadge');stage.addWidget(badge_label,0,0,Qt.AlignBottom|Qt.AlignLeft)
        layout.addLayout(stage,1)
        name=QLabel(title);name.setObjectName('cinematicName');name.setToolTip(title);layout.addWidget(name)
    def mousePressEvent(self,event):
        super().mousePressEvent(event)
        if event.button()==Qt.LeftButton:self._select()
    def _select(self):
        # Stored by the window when the tile is created.
        callback=getattr(self,'on_select',None)
        if callback:callback(self.path)
    def set_selected(self,value):
        self.setProperty('selected',bool(value));self.style().unpolish(self);self.style().polish(self)

class FriendTile(QWidget):
    """One friend tile: headshot photo with the name underneath. Click to toggle. Selected tiles show a red X to remove."""
    def __init__(self,pid,on_toggle,parent=None):
        super().__init__(parent);self.pid=str(pid);self._on_toggle=on_toggle
        self.setCursor(Qt.PointingHandCursor)
        layout=QVBoxLayout(self);layout.setContentsMargins(4,4,4,4);layout.setSpacing(3)
        self.photo=QLabel();self.photo.setAlignment(Qt.AlignCenter);self.photo.setFixedSize(64,64)
        self.photo.setStyleSheet('background:#080a0e;border:1px solid #292f39;border-radius:6px;color:#77818f;')
        self.name=QLabel();self.name.setAlignment(Qt.AlignCenter);self.name.setWordWrap(True)
        self.name.setStyleSheet('font-size:11px;font-weight:700;color:#c4ccd8;')
        layout.addWidget(self.photo,0,Qt.AlignHCenter);layout.addWidget(self.name)
        self.remove_button=QPushButton('X',self)
        self.remove_button.setFixedSize(18,18)
        self.remove_button.setCursor(Qt.PointingHandCursor)
        self.remove_button.setToolTip('Remove this friend from the video')
        self.remove_button.setObjectName('tileRemove')
        self.remove_button.clicked.connect(self._on_remove_clicked)
        self.remove_button.hide()
        self.remove_button.raise_()
        self.set_selected(False)
    def _on_remove_clicked(self):
        if callable(self._on_toggle):self._on_toggle(self.pid)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        try:self.remove_button.move(max(0,self.width()-20),2)
        except RuntimeError:pass
    def set_selected(self,on):
        self._selected=bool(on);self._refresh()
        try:self.remove_button.setVisible(bool(on))
        except (AttributeError,RuntimeError):pass
    def enterEvent(self,event):
        self._hovered=True;self._refresh()
    def leaveEvent(self,event):
        self._hovered=False;self._refresh()
    def _refresh(self):
        if getattr(self,'_selected',False):color='#f97316'
        elif getattr(self,'_hovered',False):color='#8a93a3'
        else:color='#292f39'
        self.setStyleSheet('FriendTile{border:2px solid %s;border-radius:6px;background:#0b0e13;}'%color)
    def mousePressEvent(self,event):
        super().mousePressEvent(event)
        if event.button()==Qt.LeftButton and callable(self._on_toggle):self._on_toggle(self.pid)

class WindowsWindowHost:
    """Reparent one existing Windows top-level window into a QWidget surface."""
    GWL_STYLE=-16; WS_CHILD=0x40000000; WS_POPUP=0x80000000
    SWP_FRAMECHANGED=0x0020; SWP_SHOWWINDOW=0x0040
    def __init__(self):self.hwnd=None;self.old_parent=None;self.old_style=None;self.old_rect=None
    @staticmethod
    def _apis():
        user32=ctypes.WinDLL('user32',use_last_error=True);hwnd=wintypes.HWND;long_ptr=ctypes.c_ssize_t
        get_style=getattr(user32,'GetWindowLongPtrW',user32.GetWindowLongW);get_style.argtypes=(hwnd,ctypes.c_int);get_style.restype=long_ptr
        set_style=getattr(user32,'SetWindowLongPtrW',user32.SetWindowLongW);set_style.argtypes=(hwnd,ctypes.c_int,long_ptr);set_style.restype=long_ptr
        user32.GetParent.argtypes=(hwnd,);user32.GetParent.restype=hwnd
        user32.SetParent.argtypes=(hwnd,hwnd);user32.SetParent.restype=hwnd
        user32.SetFocus.argtypes=(hwnd,);user32.SetFocus.restype=hwnd
        user32.SetForegroundWindow.argtypes=(hwnd,);user32.SetForegroundWindow.restype=wintypes.BOOL
        user32.IsWindow.argtypes=(hwnd,);user32.IsWindow.restype=wintypes.BOOL
        user32.SetWindowPos.argtypes=(hwnd,hwnd,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.UINT);user32.SetWindowPos.restype=wintypes.BOOL
        user32.MoveWindow.argtypes=(hwnd,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.BOOL);user32.MoveWindow.restype=wintypes.BOOL
        user32.GetWindowRect.argtypes=(hwnd,ctypes.POINTER(wintypes.RECT));user32.GetWindowRect.restype=wintypes.BOOL
        return user32,get_style,set_style
    def attach(self,hwnd,parent_hwnd,activate=True):
        user32,get_style,set_style=self._apis()
        self.old_parent=user32.GetParent(hwnd);self.old_style=get_style(hwnd,self.GWL_STYLE);rect=wintypes.RECT()
        self.old_rect=(rect.left,rect.top,rect.right-rect.left,rect.bottom-rect.top) if user32.GetWindowRect(hwnd,ctypes.byref(rect)) else (100,100,1000,700)
        self.hwnd=hwnd
        style=(self.old_style|self.WS_CHILD)&~self.WS_POPUP
        set_style(hwnd,self.GWL_STYLE,style)
        ctypes.set_last_error(0)
        user32.SetParent(hwnd,parent_hwnd)
        set_parent_error=ctypes.get_last_error()
        user32.SetWindowPos(hwnd,0,0,0,0,0,self.SWP_FRAMECHANGED|self.SWP_SHOWWINDOW)
        if int(user32.GetParent(hwnd) or 0)!=int(parent_hwnd):
            self.detach();raise OSError(set_parent_error or ctypes.get_last_error(),'Windows did not attach the CODMunity window to the tab.')
        if activate:
            try:user32.SetForegroundWindow(hwnd);user32.SetFocus(hwnd)
            except Exception:pass
    def focus_embedded(self):
        if not self.hwnd:return False
        try:
            user32=self._apis()[0]
            if not user32.IsWindow(self.hwnd):self.hwnd=None;return False
            user32.SetFocus(self.hwnd);return True
        except Exception:return False
    def resize(self,width,height):
        if self.hwnd:self._apis()[0].MoveWindow(self.hwnd,0,0,max(1,width),max(1,height),True)
    def detach(self):
        if not self.hwnd:return
        user32,_,set_style=self._apis()
        user32.SetParent(self.hwnd,self.old_parent or 0)
        if self.old_style is not None:set_style(self.hwnd,self.GWL_STYLE,self.old_style)
        x,y,width,height=self.old_rect or (100,100,1000,700)
        user32.SetWindowPos(self.hwnd,0,x,y,width,height,self.SWP_FRAMECHANGED|self.SWP_SHOWWINDOW)
        self.hwnd=None;self.old_rect=None

class Signals(QObject):
    done=Signal(object);failed=Signal(str)
class Task(QRunnable):
    def __init__(self,fn):super().__init__();self.fn=fn;self.signals=Signals()
    def run(self):
        try:result=self.fn()
        except Exception as exc:
            try:self.signals.failed.emit(re.sub(r'(https://discord(?:app)?\.com)?/api/webhooks/\d+/[\w.\-]+','[webhook]',str(exc))[:400])
            except RuntimeError:pass
            return
        try:self.signals.done.emit(result)
        except RuntimeError:pass


class Sparkline(QWidget):
    """Tiny K/D progression line; empty state paints nothing but the track."""
    def __init__(self,parent=None):
        super().__init__(parent);self._values=[]
    def setValues(self,values):
        clean=[]
        for value in values or []:
            if isinstance(value,bool):continue
            if isinstance(value,(int,float)) and value==value and abs(value)!=float('inf'):clean.append(float(value))
        self._values=clean[-30:];self.update()
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(),QColor('#151922'))
        vals=self._values
        if len(vals)<2:return
        lo=min(vals);hi=max(vals);span=(hi-lo) or 1.0
        width,height=self.width(),self.height();pad=3
        points=[QPointF(pad+i*(width-2*pad)/max(1,len(vals)-1),height-pad-(v-lo)/span*(height-2*pad)) for i,v in enumerate(vals)]
        painter.setPen(QPen(QColor('#9fdc3a'),2));painter.drawPolyline(points)
        painter.setPen(QPen(QColor('#4ade80'),4));painter.drawPoint(points[-1])
PAGE_BANNERS={'Dashboard':'dashboard.png','Scoreboard Capture':'scoreboard.png','Match History':'match-history.png','Player Roster':'roster.png','AI Studio':'ai-studio.png','ScreenShots and Videos':'screenshots.png','Tracker Browser':'browser.png','Settings':'settings.png'}
DMZ_WEB_HOME = 'https://codmunity.gg/dmz'
DMZ_WEB_PAGES = (
    ('Overview', 'https://codmunity.gg/dmz'),
    ('Map', 'https://codmunity.gg/dmz/map'),
    ('Review pins', 'https://codmunity.gg/dmz/map/review'),
    ('How the map works', 'https://codmunity.gg/dmz/map/how-it-works'),
    ('Missions', 'https://codmunity.gg/dmz/missions'),
    ('Trait Planner', 'https://codmunity.gg/dmz/traits/planner'),
    ('Loot Tracker', 'https://codmunity.gg/dmz/loot-tracker'),
    ('Meta', 'https://codmunity.gg/mw4-dmz'),
    ('Traits', 'https://codmunity.gg/dmz/traits'),
    ('Items', 'https://codmunity.gg/dmz/items'),
    ('Crafting', 'https://codmunity.gg/dmz/crafting'),
    ('Bosses', 'https://codmunity.gg/dmz/bosses'),
    ('Progression', 'https://codmunity.gg/dmz/progression'),
    ('Beginner guide', 'https://codmunity.gg/dmz/guide'),
    ("What's New", 'https://codmunity.gg/dmz/whats-new'),
    ('News', 'https://codmunity.gg/dmz/news'),
)
DMZ_NAV_SECTIONS = (
    ('TOOLS', ('Map', 'Review pins', 'How the map works', 'Missions', 'Trait Planner', 'Loot Tracker', 'Meta')),
    ('DATABASE', ('Traits', 'Items', 'Crafting', 'Bosses', 'Progression')),
    ('GUIDES AND NEWS', ('Beginner guide', "What's New", 'News')),
)
DMZ_ADBLOCK_SUFFIXES = (
    'doubleclick.net', 'googlesyndication.com', 'googleadservices.com',
    'adservice.google.com', 'ads.yahoo.com', 'amazon-adsystem.com',
    'adsrvr.org', 'criteo.com', 'criteo.net', 'outbrain.com',
    'taboola.com', 'revcontent.com', 'mgid.com', 'pubmatic.com',
    'rubiconproject.com', 'openx.net', 'moatads.com',
)
def _dmz_host_blocked(host):
    try:h=str(host or '').lower().split(':')[0]
    except Exception:return False
    if not h or '.' not in h:return False
    return any(h==d or h.endswith('.'+d) for d in DMZ_ADBLOCK_SUFFIXES)


class ScoreboardHotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self,callbacks):super().__init__();self.callbacks=callbacks
    def nativeEventFilter(self,eventType,message):
        try:
            native=ctypes.cast(int(message),ctypes.POINTER(wintypes.MSG)).contents
            if native.message==0x0312 and native.wParam in self.callbacks:
                self.callbacks[native.wParam]();return True,0
        except (TypeError,ValueError,OSError):pass
        return False,0

class FullscreenExitFilter(QObject):
    """App-level exit for the fullscreen video surface, whose native child can swallow widget keys.

    Escape leaves fullscreen and a double-click toggles it, but only while
    fullscreen is active; every other event passes through untouched."""
    def __init__(self,is_fullscreen,on_escape,on_toggle,parent=None):
        super().__init__(parent);self._is_fullscreen=is_fullscreen;self._on_escape=on_escape;self._on_toggle=on_toggle
    def eventFilter(self,obj,event):
        try:
            if not self._is_fullscreen():return False
            if event.type()==QEvent.KeyPress and event.key()==Qt.Key_Escape:
                self._on_escape();return True
            if event.type()==QEvent.MouseButtonDblClick:
                self._on_toggle();return True
        except RuntimeError:return False
        return False

def frame_pixmap(frame):
    h,w,c=frame.shape;return QPixmap.fromImage(QImage(frame.data,w,h,w*c,QImage.Format_RGB888).copy())

def prepare_tab_preview_data(source_dir,target_dir):
    """Snapshot tracker preferences, history/roster, and headshots for a safe UI preview."""
    source=Path(source_dir).resolve();target=Path(target_dir).resolve()
    if source==target or source in target.parents or target in source.parents:raise ValueError('The preview data folder must be separate from the live tracker data folder.')
    if not source.is_dir():raise FileNotFoundError('Live tracker data folder was not found: '+str(source))
    target.mkdir(parents=True,exist_ok=True)
    source_settings=source/'settings.json'
    if source_settings.is_file():shutil.copy2(source_settings,target/'settings.json')
    for folder_name in ('Screenshots',):
        source_folder=source/folder_name
        if source_folder.is_dir():shutil.copytree(source_folder,target/folder_name,dirs_exist_ok=True)
    source_db=source/'history.sqlite3';target_db=target/'history.sqlite3'
    if source_db.is_file():
        with sqlite3.connect(source_db.as_uri()+'?mode=ro',uri=True) as source_conn,sqlite3.connect(target_db) as target_conn:
            source_conn.backup(target_conn)
        photo_folder=target/'headshots';photo_folder.mkdir(parents=True,exist_ok=True)
        source_photo_folder=source/'headshots'
        if source_photo_folder.is_dir():
            for photo in source_photo_folder.iterdir():
                if photo.is_file():shutil.copy2(photo,photo_folder/photo.name)
        with sqlite3.connect(target_db) as preview_conn:
            try:rows=preview_conn.execute('SELECT id,payload FROM roster').fetchall()
            except sqlite3.OperationalError:rows=[]
            for player_id,payload in rows:
                try:player=json.loads(payload)
                except (TypeError,ValueError):continue
                original=str(player.get('photo') or '').strip()
                if not original:continue
                original_path=Path(original)
                if not original_path.is_absolute():original_path=source/original_path
                if not original_path.is_file():continue
                safe_name=original_path.name
                target_photo=photo_folder/safe_name
                try:
                    if original_path.resolve().parent!=source_photo_folder.resolve():shutil.copy2(original_path,target_photo)
                except OSError:shutil.copy2(original_path,target_photo)
                player['photo']=str(target_photo)
                preview_conn.execute('UPDATE roster SET payload=? WHERE id=?',(json.dumps(player),player_id))
            preview_conn.commit()
    return target

STYLE='''
QWidget { background:transparent;color:#e8ebf0;font-family:'Segoe UI';font-size:13px; }
QMainWindow,QWidget#centralWindow,QStackedWidget {background:#0b0d12;} QLabel#title {font-size:30px;font-weight:800;color:#ffffff;} QLabel#eyebrow {color:#ff3b45;font-weight:700;letter-spacing:2px;}
QWidget#sidebar {background:#090b10;border-right:1px solid #45151c;}
QLabel#brandMark {font-size:54px;font-weight:900;color:#fb2636;padding:0;margin:0;}
QLabel#brandName {font-size:17px;font-weight:900;color:#f4f5f7;letter-spacing:1px;}
QLabel#brandSub {font-size:11px;font-weight:700;color:#9ca5b4;letter-spacing:2px;}
QLabel#sidebarFooter {font-size:10px;font-weight:700;color:#ff3b45;letter-spacing:2px;padding:12px;}
QWidget#dashboardHero {border:1px solid #70232c;border-radius:10px;background:transparent;}
QLabel#livePill {background:#10251d;border:1px solid #1c754b;border-radius:14px;color:#57f19b;font-size:12px;font-weight:900;padding:4px 14px;}
QFrame#metricRed {border:1px solid #8e2631;border-radius:9px;background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #37161d,stop:1 #171218);}
QFrame#metricBlue {border:1px solid #245b95;border-radius:9px;background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #102a43,stop:1 #111721);}
QFrame#metricPurple {border:1px solid #65409b;border-radius:9px;background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #27193c,stop:1 #15131d);}
QLabel#metricGlyph {font-size:29px;font-weight:900;color:#fa3544;}
QLabel#metricValue {font-size:33px;font-weight:900;color:#ffffff;}
QLabel#metricLabel {font-size:12px;font-weight:800;color:#bdc6d5;letter-spacing:1px;}
QFrame#dashboardPanel {border:1px solid #303642;border-radius:9px;background:#0e1118;}
QFrame#rankCard {border:1px solid #23444b;border-radius:10px;background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #0e1a1e,stop:1 #0b1116);}
QFrame#healthRow {border:1px solid #20252d;border-radius:6px;background:#141922;}
QLabel#healthDot {font-size:15px;color:#77818f;}
QLabel#healthValue {font-size:11px;font-weight:800;color:#c4ccd8;}
QLabel#dashTitle {font-size:17px;font-weight:900;color:#ffffff;letter-spacing:2px;}
QLabel#dashVersion {font-size:11px;font-weight:700;color:#8b95a3;}
QLabel#dashLogoSub {font-size:10px;font-weight:800;color:#ff5964;letter-spacing:3px;}
QLabel#dashDotName {font-size:9px;font-weight:800;color:#8b95a3;letter-spacing:1px;}
QLabel#dashDotValue {font-size:9px;font-weight:800;color:#c4ccd8;}
QPushButton#dashGear {background:transparent;border:1px solid #303642;border-radius:14px;color:#c4ccd8;font-size:15px;font-weight:900;}
QPushButton#dashGear:hover {border-color:#ed2938;color:#ffffff;}
QLabel#dashSection {font-size:12px;font-weight:900;color:#f2f4f7;letter-spacing:2px;}
QLabel#dashStatGame {font-size:11px;font-weight:700;color:#8b95a3;}
QPushButton#dashMini {background:#141922;border:1px solid #303642;border-radius:6px;color:#c4ccd8;font-size:11px;font-weight:800;padding:4px 10px;}
QPushButton#dashMini:hover {border-color:#ed2938;color:#ffffff;}
QFrame#dashStat {border:1px solid #8e2631;border-radius:9px;background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #2b1219,stop:1 #101218);}
QFrame#dashStat:hover {border:1px solid #ed2938;}
QLabel#dashStatIcon {font-size:20px;font-weight:900;color:#fa3544;}
QLabel#dashStatValue {font-size:24px;font-weight:900;color:#ffffff;}
QLabel#dashStatLabel {font-size:9px;font-weight:800;color:#bdc6d5;letter-spacing:1px;}
QLabel#dashResult {font-weight:900;}
QLabel#dashDetail {font-size:12px;font-weight:700;color:#cbd3df;}
QLabel#dashStats {font-size:11px;color:#99a8bc;}
QLabel#dashStatus {font-size:10px;color:#8b95a3;}
QLabel#dashHint {font-size:10px;color:#77818f;}
QFrame#dashSummaryRow {border:1px solid #20252d;border-radius:6px;background:#141922;}
QLabel#dashSummaryValue {font-size:19px;font-weight:900;color:#ffffff;}
QLabel#dashSummaryLabel {font-size:9px;font-weight:800;color:#bdc6d5;letter-spacing:1px;}
QLabel#dashSummarySub {font-size:13px;font-weight:800;color:#c4ccd8;}
QLabel#codmunityProfileName {font-size:20px;font-weight:900;color:#ffffff;}
QLabel#codmunityProfileSub {font-size:11px;color:#9da8b7;}
QLabel#codmunityProfileStats {font-size:11px;color:#c4ccd8;}
QLabel#dashProfileName {font-size:20px;font-weight:900;color:#ffffff;}
QLabel#dashProfileSub {font-size:11px;color:#9da8b7;}
QLabel#dashProfileCount {font-size:17px;font-weight:900;color:#ffffff;}
QLabel#dashProfileLabel {font-size:9px;font-weight:800;color:#bdc6d5;letter-spacing:1px;}
QLabel#profileAvatar {font-size:34px;color:#5b6572;background:#232b33;border-radius:6px;}
QFrame#dashboardFooter {border-top:1px solid #3a2026;background:#0d1016;}
QFrame#historySummaryCard {border:1px solid #49313a;border-radius:8px;background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #1a151b,stop:1 #10131a);}
QLabel#historySummaryValue {font-size:25px;font-weight:900;color:#ffffff;}
QLabel#historySummaryCaption {font-size:10px;font-weight:900;color:#ff5964;letter-spacing:1px;}
QLabel#historySummaryDetail {font-size:10px;color:#9da8b7;}
QFrame#boardCard {border:1px solid #1e3a2a;border-radius:10px;background:#0d1512;}
QLabel#boardTitle {font-size:20px;font-weight:900;color:#e9f2e9;}
QPushButton#boardGameTab {background:transparent;border:1px solid transparent;border-radius:7px;font-weight:900;color:#9aa8b8;padding:8px 14px;}
QPushButton#boardGameTab:checked {background:#1d3a2a;border:1px solid #2ea36b;color:#7bf0a8;}
QPushButton#boardMetricTab {background:transparent;border:1px solid transparent;border-radius:6px;font-weight:700;color:#9aa8b8;padding:6px 12px;}
QPushButton#boardMetricTab:checked {background:#1d3a2a;border:1px solid #2ea36b;color:#7bf0a8;}
QPushButton#dashGamePill {background:transparent;border:1px solid transparent;border-radius:7px;font-weight:900;color:#9aa8b8;padding:8px 14px;}
QLabel#dashRankCurrent {color:#ffffff;font-size:13px;font-weight:900;}
QLabel#dashCardName {color:#ffffff;font-size:32px;font-weight:900;}
QLabel#dashCardHandle {color:#8b95a3;font-size:12px;font-weight:700;}
QLabel#dashCardValue {color:#ffffff;font-size:26px;font-weight:900;}
QLabel#dashCardCaption {color:#8b95a3;font-size:10px;font-weight:800;letter-spacing:1px;}
QLabel#dashCardTier {color:#ffffff;font-size:54px;font-weight:900;}
QLabel#dashCardSmall {color:#8b95a3;font-size:11px;font-weight:600;}
QLabel#dashMeterTitle {font-size:10px;font-weight:800;color:#8b95a3;letter-spacing:1px;}
QLabel#dashMeterValue {font-size:26px;font-weight:900;color:#ffffff;}
QLabel#dashMeterSub {font-size:11px;color:#9da8b7;}
QLabel#dashMeterCaption {font-size:10px;color:#77818f;}
QFrame#dashMeter {background:#141922;border:1px solid #26303c;border-radius:9px;}
QLabel#dashTileValue {font-size:19px;font-weight:900;color:#ffffff;}
QLabel#dashTileBadge {background:#1d3a2a;border:1px solid #2ea36b;color:#7bf0a8;border-radius:9px;font-size:10px;font-weight:800;padding:2px 8px;}
QLabel#dashRankNext {color:#ff5d6c;font-size:11px;font-weight:800;}
QPushButton#monitorReady {background:#1d3a2a;border:1px solid #2ea36b;color:#7bf0a8;font-weight:700;border-radius:6px;padding:7px 12px;}
QPushButton#dashGamePill:checked {background:#37161d;border:1px solid #8e2631;color:#ff8b93;}
QFrame#boardYouRow {border:1px solid #2a3430;border-radius:8px;background:#141a17;}
QLabel#boardYouBadge {background:#35c759;color:#0b1410;font-weight:900;font-size:11px;border-radius:4px;padding:3px 7px;}
QLabel#boardYouName {font-size:17px;font-weight:900;color:#ffffff;}
QLabel#boardYouSub {font-size:11px;color:#8fa093;}
QLabel#boardYouCaption {font-size:10px;font-weight:800;color:#8fa093;}
QLabel#boardYouValue {font-size:16px;font-weight:900;color:#ffffff;}
QLabel#boardAvatar {font-size:22px;color:#5b6572;background:#232b33;border-radius:18px;}
QLabel#boardPlayerName {font-size:13px;font-weight:800;color:#f2f4f7;}
QLabel#boardPlayerHandle {font-size:11px;color:#8b95a3;}
QLabel#boardMedalGold {background:#f5b942;color:#3a2a00;font-weight:900;font-size:12px;border-radius:6px;}
QLabel#boardMedalSilver {background:#c7ccd4;color:#333a44;font-weight:900;font-size:12px;border-radius:6px;}
QLabel#boardMedalBronze {background:#d08a4e;color:#3a2410;font-weight:900;font-size:12px;border-radius:6px;}
QLabel#boardMedalPlain {background:#232b33;color:#9aa8b2;font-weight:800;font-size:12px;border-radius:6px;}
QCheckBox#boardSwitch::indicator {width:44px;height:24px;border-radius:12px;background:#2b303c;}
QCheckBox#boardSwitch::indicator:checked {background:#35c759;}
QCheckBox#boardSwitch::indicator:disabled {background:#1a1e24;}
QLabel#boardIdentity {font-size:12px;font-weight:800;color:#7bf0a8;}
QLabel#boardStatus {font-size:11px;color:#99a8bc;}
QFrame#camoStatusBar {border:1px solid #2b303c;border-radius:8px;background:#10141b;}
QLabel#camoStatusDot {font-size:14px;color:#57f19b;}
QLabel#camoStatusLine {font-size:14px;font-weight:900;color:#f2f4f7;}
QLabel#camoStatusLast {font-size:12px;color:#8fa093;}
QLabel#camoGrindLabel {font-size:11px;font-weight:900;color:#7bf0a8;}
QPushButton#camoGrindPill {background:transparent;border:1px solid transparent;border-radius:7px;font-weight:900;color:#9aa8b8;padding:8px 14px;}
QPushButton#camoGrindPill:checked {background:#3a2a10;border:1px solid #f0b429;color:#f0b429;}
QLabel#camoTitle {font-size:24px;font-weight:900;color:#e9f2e9;}
QLabel#camoSubtitle {font-size:12px;color:#8fa093;}
QLabel#camoMasteryLabel {font-size:11px;font-weight:900;color:#8fa093;}
QLabel#camoMasteryValue {font-size:13px;font-weight:800;color:#e9f2e9;}
QProgressBar#camoMasteryBar {border:1px solid #2b303c;border-radius:4px;background:#141920;max-height:8px;}
QProgressBar#camoMasteryBar::chunk {background:#7bf0a8;border-radius:4px;}
QFrame#camoCard {border:1px solid #2b303c;border-radius:8px;background:#141920;}
QLabel#camoCardBadge {font-size:11px;font-weight:900;color:#8b95a3;}
QLabel#camoCardTitle {font-size:15px;font-weight:900;color:#f2f4f7;}
QLabel#camoCardArt {font-size:18px;color:#5b6572;background:#141920;border-radius:4px;}
QLabel#camoCardSub {font-size:11px;color:#8b95a3;}
QLabel#camoCardCount {font-size:11px;font-weight:800;color:#7bf0a8;background:#1d3a2a;border-radius:6px;padding:2px 8px;}
QLabel#camoCardLocked {font-size:11px;font-weight:800;color:#c9a0a6;background:#2a1215;border-radius:6px;padding:2px 8px;}
QProgressBar#camoCardBar {border:none;background:#232b33;border-radius:3px;max-height:6px;}
QProgressBar#camoCardBar::chunk {background:#7bf0a8;border-radius:3px;}
QFrame#camoFilterBar {border:1px solid #2b303c;border-radius:10px;background:#10141b;}
QPushButton#camoFilterPill {background:transparent;border:1px solid transparent;border-radius:7px;font-weight:800;color:#9aa8b8;padding:7px 12px;font-size:12px;}
QPushButton#camoFilterPill:checked {background:#1d3a2a;border:1px solid #2ea36b;color:#7bf0a8;}
QPushButton#camoCategoryPill {background:#1a1f27;border:1px solid #2b303c;border-radius:6px;font-weight:700;color:#c4ccd8;padding:6px 12px;font-size:12px;}
QPushButton#camoCategoryPill:checked {background:#1d3a2a;border:1px solid #2ea36b;color:#7bf0a8;}
QLineEdit#camoSearch {background:#0b0e13;border:1px solid #2b303c;border-radius:7px;padding:8px 12px;color:#e8ebf0;}
QLabel#camoHint {font-size:11px;color:#77818f;}
QListWidget#camoWeaponList {background:transparent;border:none;}
QListWidget#camoWeaponList::item {background:transparent;border:none;padding:0px;margin:4px 0px;}
QListWidget#camoWeaponList::item:selected {background:transparent;border:none;}
QFrame#camoWeaponCard {border:1px solid #2b303c;border-radius:10px;background:#141920;}
QLabel#camoWeaponName {font-size:15px;font-weight:900;color:#f2f4f7;}
QLabel#camoWeaponClass {font-size:11px;color:#8b95a3;}
QLabel#camoThumb {font-size:16px;color:#5b6572;background:#232b33;border:2px solid #343c47;border-radius:6px;}
QLabel#camoThumb[camoState="done"] {border-color:#2ea36b;color:#7bf0a8;}
QLabel#camoThumb[camoState="locked"] {border-color:#3a2b30;color:#5b4a4e;}
QPushButton#camoExpander {background:#1a1f27;border:1px solid #343c47;border-radius:6px;font-weight:900;color:#c4ccd8;padding:0px;}
QLabel#camoNextCaption {font-size:10px;font-weight:900;color:#7bf0a8;}
QLabel#camoNextName {font-size:13px;font-weight:900;color:#f2f4f7;}
QLabel#camoDetailTitle {color:#ffffff;font-size:16px;font-weight:900;}
QLabel#camoGroupTitle {font-size:12px;font-weight:900;color:#f2f4f7;letter-spacing:2px;}
QFrame#camoDetailTile {border:1px solid #2a3340;border-radius:8px;background:#10141b;}
QLabel#camoDetailName {color:#ffffff;font-size:13px;font-weight:900;}
QLabel#camoDetailReq {color:#8b95a3;font-size:11px;}
QPushButton#camoDetailClose {color:#7bf0a8;border:1px solid #2ea36b;border-radius:8px;padding:8px 18px;font-weight:900;}
QLabel#camoModeMP {background:#3b82f6;color:#ffffff;border-radius:4px;padding:2px 8px;font-size:10px;font-weight:900;}
QLabel#camoModeDMZ {background:#22c55e;color:#ffffff;border-radius:4px;padding:2px 8px;font-size:10px;font-weight:900;}
QLabel#camoModeWZ {background:#38bdf8;color:#ffffff;border-radius:4px;padding:2px 8px;font-size:10px;font-weight:900;}
QLabel#camoNextLine {font-size:11px;color:#9aa8b2;}
QLabel#camoRing {font-size:13px;font-weight:900;color:#7bf0a8;border:2px solid #2ea36b;border-radius:26px;}
QLabel#camoChallengeLine {font-size:12px;color:#c4ccd8;}
QLabel#camoStatusLine {font-size:11px;color:#99a8bc;}
QPushButton#camoViewPill {background:transparent;border:1px solid #2b303c;border-radius:7px;font-weight:700;color:#9aa8b8;padding:6px 10px;font-size:13px;}
QPushButton#camoViewPill:checked {background:#1d3a2a;border:1px solid #2ea36b;color:#7bf0a8;}
QLabel#camoRingSmall {font-size:12px;font-weight:900;color:#7bf0a8;border:2px solid #2ea36b;border-radius:18px;min-width:36px;min-height:36px;}
QListWidget {background:#090b10;border:none;padding:10px 8px;font-size:14px;outline:0;}
QListWidget::item {padding:14px 12px;border:1px solid #1e222b;border-radius:7px;margin:4px 2px;color:#dce1e9;}
QListWidget::item:hover {background:#19151a;border-color:#70222b;color:#ffffff;}
QListWidget::item:selected {background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #40141b,stop:1 #171217);color:#ffffff;border:1px solid #ed2938;border-left:4px solid #ff3443;}
QPushButton {background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #20242d,stop:1 #14171e);border:1px solid #343945;padding:10px 15px;border-radius:7px;font-weight:700;color:#f0f2f5;}
QPushButton:hover {background:#28171c;border-color:#ed2938;color:#ffffff;}
QPushButton:pressed {background:#51131d;border-color:#ff3342;}
QPushButton:checked {background:#39161d;border:1px solid #ed2938;color:#ffffff;}
QPushButton:disabled {color:#717986;background:#171a20;border-color:#282c34;}
QPushButton#accent {background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #ff3947,stop:1 #bd101f);color:#ffffff;border:1px solid #ff6570;}
QPushButton#latestAutoJobButton[jobRunning="true"][pulsePhase="true"] {background:#38b766;color:#ffffff;border:2px solid #9cffb9;}
QPushButton#latestAutoJobButton[jobRunning="true"][pulsePhase="false"] {background:#176d3a;color:#ffffff;border:2px solid #53ec82;}
QPushButton[guideTarget="true"] {border:2px solid #4aa3ff;background:#123258;color:#ffffff;}
QPushButton#guideButton {background:#10243a;color:#72b8ff;border:1px solid #347bc2;border-radius:18px;font-weight:900;}
QPushButton#miniHelpButton {background:#10243a;color:#72b8ff;border:1px solid #347bc2;border-radius:10px;font-size:12px;font-weight:900;padding:0;}
QPushButton#miniHelpButton:hover {background:#1a3b60;border-color:#72b8ff;}
QPushButton#exitButton {background:#3a1116;color:#ff8b93;border:1px solid #ff6570;font-weight:800;padding:5px 20px;min-width:78px;min-height:28px;border-radius:15px;margin-right:0;}
QPushButton#exitButton:hover {background:#ff3947;color:#ffffff;}
QPushButton#accent:hover {background:#ff4854;border-color:#ffa0a5;}
QPushButton#metaLoginButton {background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #2785e8,stop:1 #1454ad);color:#ffffff;border:1px solid #69afff;font-weight:800;}
QPushButton#metaLoginButton:hover {background:#3698ff;border-color:#a4d2ff;}
QLineEdit,QComboBox,QSpinBox,QPlainTextEdit {background:#0a0d13;border:1px solid #353b47;padding:8px;border-radius:5px;selection-background-color:#8d1c2a;}
QComboBox::drop-down {width:24px;border:none;}
QTableWidget {background:#0d1016;alternate-background-color:#121720;gridline-color:#202630;border:1px solid #292f39;border-radius:6px;selection-background-color:#45151e;}
QHeaderView::section {background:#151a23;color:#bdc6d5;padding:9px 7px;border:none;border-bottom:1px solid #303744;font-weight:700;}
QTableWidget::item {padding:8px 6px;border-bottom:1px solid #191e26;}
QTableWidget::item:selected {background:#42151d;color:#ffffff;}
QGroupBox {border:1px solid #303642;border-radius:9px;margin-top:17px;padding:15px;background:#10131a;font-weight:700;}
QGroupBox::title {subcontrol-origin:margin;left:14px;padding:0 7px;color:#f4f5f7;}
QCheckBox {spacing:9px;padding:5px;} QScrollArea {border:none;} QSplitter::handle {background:#303642;}
QTabWidget::pane {border:1px solid #303642;border-radius:6px;top:-1px;background:#10131a;}
QTabWidget#appTabs::pane {border:none;border-radius:0;background:#0b0d12;}
QTabWidget#appTabs QTabBar {background:transparent;border:none;}
QTabWidget#appTabs QTabBar::tab:last {background:#123d68;color:#bde0ff;border-color:#398bd4;}
QTabWidget#appTabs QTabBar::tab:last:selected {background:#123d68;color:#ffffff;border-color:#398bd4;border-bottom-color:#123d68;}
QTabBar::tab {background:#151922;color:#aab3c1;border:1px solid #303642;padding:10px 20px;margin-right:4px;border-top-left-radius:5px;border-top-right-radius:5px;}
QTabBar::tab:selected {background:#37151b;color:#ffffff;border-color:#d72837;border-bottom-color:#37151b;}
QFrame#codAiSteps {border:1px solid #303642;border-radius:9px;background:#0e1118;padding:6px;}
QLabel#stepNum {background:#3d0d15;border:2px solid #ed2938;border-radius:16px;color:#ffffff;font-size:15px;font-weight:900;min-width:32px;min-height:32px;max-width:32px;max-height:32px;}
QLabel#stepTitle {font-size:13px;font-weight:900;color:#ffffff;letter-spacing:1px;}
QLabel#stepDesc {font-size:11px;color:#9da8b7;}
QLabel#stepArrow {font-size:18px;font-weight:900;color:#ed2938;padding:0 4px;}
QLabel#hotkeyPill {background:#0d2318;border:1px solid #2ea36b;border-radius:14px;color:#7bf0a8;font-size:12px;font-weight:800;padding:7px 13px;}
QLabel#hotkeyPillShort {background:#0d2318;border:1px solid #2ea36b;border-radius:14px;color:#7bf0a8;font-size:12px;font-weight:800;padding:7px 9px;}
QLabel#hotkeyPillShort:hover {background:#14392a;border:1px solid #7bf0a8;color:#ffffff;}
QFrame#cinematicTile {border:1px solid #2a2f3a;border-radius:8px;background:#0c0f15;}
QFrame#cinematicTile[selected="true"] {border:2px solid #ed2938;background:#180d12;}
QLabel#cinematicThumb {background:#050609;color:#77818f;border-radius:5px;}
QLabel#cinematicName {font-size:11px;color:#c4ccd8;padding:0 2px;}
QLabel#cinematicBadge {background:rgba(0,0,0,170);color:#ffffff;font-size:11px;font-weight:800;border-radius:4px;padding:2px 6px;margin:5px;}
QPushButton#cinematicDelete {background:#c8102e;color:#ffffff;border:1px solid #ff5b6a;border-radius:12px;font-size:12px;font-weight:900;padding:0;margin:4px;}
QPushButton#cinematicDelete:hover {background:#ed2938;}
QPushButton#tileRemove {background:#b91c1c;color:#ffffff;border:none;border-radius:9px;font-size:11px;font-weight:900;padding:0;margin:0;}
QPushButton#tileRemove:hover {background:#ef2b2b;}
QListWidget#cinematicGallery {background:#090b10;border:1px solid #2b303c;border-radius:8px;padding:6px;}
QListWidget#cinematicGallery::item {border:none;background:transparent;padding:2px;margin:2px;color:#dce1e9;}
QListWidget#cinematicGallery::item:selected {border:none;background:transparent;}
QSlider::groove:horizontal {height:5px;background:#262b35;border-radius:2px;}
QSlider::handle:horizontal {width:14px;height:14px;margin:-5px 0;background:#ed2938;border-radius:7px;}
QSlider::sub-page:horizontal {background:#8d1c2a;border-radius:2px;}
QListWidget#rosterCards {background:#090b10;border:1px solid #2b303c;border-radius:8px;padding:6px;}
QListWidget#rosterCards::item {border:1px solid #232833;border-radius:7px;margin:3px 2px;padding:0;background:#10131a;}
QListWidget#rosterCards::item:hover {border-color:#70222b;background:#19151a;}
QListWidget#rosterCards::item:selected {border:1px solid #ed2938;background:#1d1115;}
QLabel#rosterBigPhoto {border:2px solid #ed2938;border-radius:8px;background:#050609;}
QLabel#rosterPillEnabled {background:#0d2b1c;border:1px solid #2ea36b;border-radius:12px;color:#5df08f;font-size:11px;font-weight:900;padding:5px 12px;}
QLabel#rosterPillDisabled {background:#1a1d23;border:1px solid #3a404c;border-radius:12px;color:#8b93a1;font-size:11px;font-weight:900;padding:5px 12px;}
QLabel#rosterPhotoPreview {background:#050609;border:1px solid #2b303c;border-radius:8px;}
QLabel#rosterCardName {font-size:14px;font-weight:800;color:#f2f4f7;background:transparent;}
QLabel#rosterCardSub {font-size:11px;color:#8b93a1;background:transparent;}
QCheckBox#rosterSwitch {font-weight:700;color:#e8ebf0;}
QCheckBox#rosterSwitch::indicator {width:20px;height:20px;border-radius:10px;border:2px solid #3a404c;background:#14171e;}
QCheckBox#rosterSwitch::indicator:checked {background:#22c55e;border-color:#22c55e;}
QFrame#codAiSteps {border:1px solid #4a3320;background:#100c09;}
QLabel#stepNum {background:#2a1503;border:2px solid #f97316;color:#fdba74;}
QLabel#stepIcon {font-size:20px;color:#fdba74;padding:0 2px;}
QLabel#stepArrow {color:#f97316;}
QTabWidget#mediaTabs {background:transparent;}
QTabWidget#mediaTabs QTabBar::tab {background:#1a1410;color:#a8a29e;border:1px solid #3a2e22;padding:8px 22px;margin-right:6px;border-top-left-radius:6px;border-top-right-radius:6px;font-weight:800;}
QTabWidget#mediaTabs QTabBar::tab:selected {background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #fb923c,stop:1 #ea580c);color:#1c0f04;border-color:#fdba74;}
QFrame#cinematicTile:hover {border-color:#7b8494;}
QFrame#cinematicTile[selected="true"] {border:2px solid #f97316;background:#1c1008;}
QLabel#talentDot {background:#22c55e;border-radius:5px;min-width:10px;min-height:10px;max-width:10px;max-height:10px;}
QPushButton#codPrimary {background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #f87171,stop:0.5 #dc2626,stop:1 #991b1b);color:#ffffff;border:1px solid #fca5a5;padding:12px 28px;border-radius:8px;font-weight:900;font-size:14px;}
QPushButton#codPrimary:hover {background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #fb923c,stop:0.5 #ea580c,stop:1 #9a3412);border-color:#fdba74;}
QPushButton#codPrimary:disabled {color:#717986;background:#171a20;border-color:#282c34;}
QPushButton#codSecondary {background:#1c212b;border:1px solid #3f4754;padding:12px 28px;border-radius:8px;font-weight:800;font-size:14px;color:#f0f2f5;}
QPushButton#codSecondary:hover {border-color:#f97316;color:#ffffff;}
QLabel#codEmpty {color:#8b93a1;font-size:13px;font-weight:700;background:transparent;border:2px dashed #2e3542;border-radius:10px;padding:34px 10px;}
QLabel#codPanelTitle {font-size:13px;font-weight:900;color:#f2f4f7;letter-spacing:1px;}
QPushButton#codPlay {background:#f97316;color:#1c0f04;border:none;border-radius:8px;font-weight:900;padding:8px 22px;font-size:13px;}
QPushButton#codPlay:hover {background:#fdba74;}
QPushButton#codPlay:disabled {background:#2a2e36;color:#717986;}
QPushButton#codBlue {background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #2563eb,stop:1 #1d4ed8);color:#ffffff;border:1px solid #3b82f6;border-radius:6px;font-weight:800;padding:10px 14px;}
QPushButton#codBlue:hover {background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #3b82f6,stop:1 #2563eb);border-color:#93c5fd;}
QPushButton#codBlue:pressed {background:#1e40af;border-color:#1e3a8a;}
QPushButton#codBlue:disabled {color:#717986;background:#171a20;border-color:#282c34;}
'''

class TacticalBackdrop(QWidget):
    """Lightweight decorative hero background with optional external artwork."""
    def __init__(self,parent=None):
        super().__init__(parent);self.setObjectName('dashboardHero');self.setMinimumHeight(80);self.setMaximumHeight(100)
        self.art=None
        for path in (E.ROOT/'assets'/'dashboard-operator.webp',E.ROOT/'assets'/'dashboard-operator.png',E.ROOT/'assets'/'dashboard-atmosphere.webp',E.ROOT/'assets'/'dashboard-atmosphere.png'):
            if path.is_file():
                pix=QPixmap(str(path))
                if not pix.isNull():self.art=pix;break
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.Antialiasing)
        gradient=QLinearGradient(0,0,self.width(),self.height());gradient.setColorAt(0,QColor('#19151b'));gradient.setColorAt(.52,QColor('#10131a'));gradient.setColorAt(1,QColor('#1b0d13'))
        painter.setPen(Qt.NoPen);painter.setBrush(gradient);painter.drawRoundedRect(self.rect(),10,10)
        if self.art:
            painter.setOpacity(.20);painter.drawPixmap(self.rect(),self.art);painter.setOpacity(1)
        painter.setBrush(QColor(211,34,49,30));painter.drawRect(self.width()-150,0,150,self.height())
        painter.setPen(QPen(QColor('#e72a3b'),2));painter.drawLine(18,14,43,14);painter.drawLine(18,20,35,20)
        painter.end()

MOD_ALT=0x0001;MOD_CONTROL=0x0002;MOD_SHIFT=0x0004;MOD_NOREPEAT=0x4000
DEFAULT_SCOREBOARD_HOTKEY=(0x77,MOD_CONTROL|MOD_SHIFT)
DEFAULT_SCREENSHOT_HOTKEY=DEFAULT_SCOREBOARD_HOTKEY
VK_NAMES={0x70+i:f'F{i+1}' for i in range(24)}
VK_NAMES.update({0x30+i:str(i) for i in range(10)})
VK_NAMES.update({0x41+i:chr(0x41+i) for i in range(26)})
VK_NAMES.update({0x20:'Space',0x1B:'Esc',0x09:'Tab',0x0D:'Enter',0x2E:'Del',0x2D:'Insert',0x24:'Home',0x23:'End',0x21:'Page Up',0x22:'Page Down',0x25:'Left',0x26:'Up',0x27:'Right',0x28:'Down'})
def vk_name(vk):
    vk=int(vk or 0)
    if vk in VK_NAMES:return VK_NAMES[vk]
    if os.name=='nt':
        try:
            scan=ctypes.windll.user32.MapVirtualKeyW(vk,0)
            if scan:
                buf=ctypes.create_unicode_buffer(64)
                if ctypes.windll.user32.GetKeyNameTextW(scan<<16,buf,64):return buf.value
        except Exception:pass
    return f'VK {vk:#04x}'
def format_hotkey(vk,mod):
    parts=[]
    if mod&MOD_CONTROL:parts.append('Ctrl')
    if mod&MOD_ALT:parts.append('Alt')
    if mod&MOD_SHIFT:parts.append('Shift')
    parts.append(vk_name(vk));return ' + '.join(parts)
class HotkeyEdit(QLineEdit):
    def __init__(self,vk,mod,parent=None,confirm=True):
        super().__init__(parent);self.setReadOnly(True);self._waiting=False;self._confirm=bool(confirm);self.set_hotkey(vk,mod);self.setPlaceholderText('Click here, then press the new keys')
        self.setToolTip('Click this box and press the keys to use. Bare keys work but fire on every press.')
    def set_hotkey(self,vk,mod):
        self.vk=int(vk or 0);self.mod=int(mod or 0)&(MOD_CONTROL|MOD_SHIFT|MOD_ALT);self.setText(format_hotkey(self.vk,self.mod))
    def _set_waiting(self,on):
        self._waiting=bool(on)
        if self._waiting:self.setText('Press the new keys…');self.setStyleSheet('border:2px solid #f97316;border-radius:6px;')
        else:self.setStyleSheet('');self.setText(format_hotkey(self.vk,self.mod))
    @staticmethod
    def _focus_arms_waiting(reason):
        # Only an explicit user gesture (click or Tab) arms the
        # "press the new keys" prompt. Dialog-open auto-focus and focus
        # returning from a popup (e.g. the "Hotkeys Set" confirmation) must
        # leave the just-saved hotkey text alone, or the box looks like it is
        # asking for the hotkey again right after OK.
        return reason in (Qt.MouseFocusReason,Qt.TabFocusReason,Qt.BacktabFocusReason,Qt.ShortcutFocusReason)
    def focusInEvent(self,event):
        super().focusInEvent(event)
        try:reason=event.reason()
        except (AttributeError,RuntimeError):reason=Qt.OtherFocusReason
        if self._focus_arms_waiting(reason):self._set_waiting(True)
    def mousePressEvent(self,event):
        super().mousePressEvent(event);self._set_waiting(True)
    def focusOutEvent(self,event):
        super().focusOutEvent(event);self._set_waiting(False)
    def keyPressEvent(self,event):
        key=event.key()
        if key in (Qt.Key_Control,Qt.Key_Shift,Qt.Key_Alt,Qt.Key_Meta):return
        if key==Qt.Key_Escape:self.clearFocus();return
        mod=0;mods=event.modifiers()
        if mods&Qt.ControlModifier:mod|=MOD_CONTROL
        if mods&Qt.ShiftModifier:mod|=MOD_SHIFT
        if mods&Qt.AltModifier:mod|=MOD_ALT
        vk=0
        try:native=int(event.nativeVirtualKey() or 0)
        except Exception:native=0
        if native>0:vk=native
        elif Qt.Key_F1<=key<=Qt.Key_F24:vk=0x70+(key-Qt.Key_F1)
        elif 0x30<=key<=0x39 or 0x41<=key<=0x5A:vk=key
        elif key in (Qt.Key_Up,Qt.Key_Down,Qt.Key_Left,Qt.Key_Right):vk={Qt.Key_Up:0x26,Qt.Key_Down:0x28,Qt.Key_Left:0x25,Qt.Key_Right:0x27}[key]
        if vk:self.set_hotkey(vk,mod);self._set_waiting(False);self.clearFocus()
        if vk and self._confirm:QMessageBox.information(self,'Hotkeys Set','Hotkey updated to '+format_hotkey(self.vk,self.mod)+'.')
class _MeterBar(QWidget):
    def __init__(self,color,parent=None):
        super().__init__(parent);self.color=color;self.pct=0.0;self.setMinimumHeight(22)
    def set_value(self,pct):
        try:self.pct=max(0.0,min(100.0,float(pct)))
        except (TypeError,ValueError):self.pct=0.0
        self.update()
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.Antialiasing);painter.setPen(Qt.NoPen)
        w=float(max(1,self.width()));h=8.0;y=(float(self.height())-h)/2.0
        painter.setBrush(QColor('#232b33'));painter.drawRoundedRect(QRectF(2.0,y,w-4.0,h),4.0,4.0)
        for tenth in range(10,100,10):
            x=w*tenth/100.0
            painter.setBrush(QColor('#3a4356'));painter.drawRect(QRectF(x-1.0,y+1.0,2.0,h-2.0))
        fill=w*self.pct/100.0
        if fill>4.0:
            painter.setBrush(QColor(self.color));painter.drawRoundedRect(QRectF(2.0,y,fill-2.0,h),4.0,4.0)
            knob=2.0+fill-2.0
            painter.setBrush(QColor('#ffffff'));painter.drawEllipse(QPointF(knob,y+h/2.0),7.0,7.0)
            painter.setBrush(QColor('#0b0d12'));painter.drawEllipse(QPointF(knob,y+h/2.0),3.5,3.5)
        painter.end()


class _RingMeter(QWidget):
    def __init__(self,color,parent=None):
        super().__init__(parent);self.color=color;self.pct=0.0;self.text='';self.setFixedSize(64,64)
    def set_value(self,pct,text=''):
        try:self.pct=max(0.0,min(100.0,float(pct)))
        except (TypeError,ValueError):self.pct=0.0
        self.text=str(text or '');self.update()
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.Antialiasing)
        inset=5.0;size=float(min(self.width(),self.height()))-inset*2.0
        pen=QPen(QColor('#232b33'));pen.setWidth(7);pen.setCapStyle(Qt.RoundCap);painter.setPen(pen)
        painter.drawEllipse(QRectF(inset,inset,size,size))
        pen.setColor(QColor(self.color));painter.setPen(pen)
        painter.drawArc(QRectF(inset,inset,size,size),90*16,-int(self.pct/100.0*360.0*16))
        font=QFont('Segoe UI',11);font.setBold(True);painter.setFont(font)
        painter.setPen(QColor('#ffffff'));painter.drawText(self.rect(),Qt.AlignCenter,self.text)
        painter.end()


class _RankLadder(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.tiers=list(codmunity_player_stats.LADDER_TIERS)
        self.colors=dict(Window.LADDER_TIER_COLORS)
        self.tier_index=-1
        self.progress=0.0
        self.setMinimumHeight(52)
    def set_rank(self,tier_index,progress):
        try:self.tier_index=int(tier_index)
        except (TypeError,ValueError):self.tier_index=-1
        try:self.progress=max(0.0,min(1.0,float(progress)))
        except (TypeError,ValueError):self.progress=0.0
        self.update()
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.Antialiasing);painter.setPen(Qt.NoPen)
        count=len(self.tiers)
        if count<1:painter.end();return
        total=float(max(1,self.width()));gap=6.0;seg=(total-gap*(count-1))/count;bar_h=22.0;top=2.0;label_h=15.0
        dark=QColor('#262c36')
        for index,(_floor,name) in enumerate(self.tiers):
            x=index*(seg+gap);rect=QRectF(x,top,seg,bar_h)
            color=QColor(self.colors.get(name,'#e5e9ef'))
            if 0<=self.tier_index and index<self.tier_index:
                painter.setBrush(color);painter.drawRoundedRect(rect,6.0,6.0)
            elif index==self.tier_index and 0<=self.tier_index:
                painter.setBrush(dark);painter.drawRoundedRect(rect,6.0,6.0)
                fill=max(0.0,min(1.0,self.progress))*seg
                if fill>0.5:
                    clip=QPainterPath();clip.addRoundedRect(rect,6.0,6.0);painter.save();painter.setClipPath(clip)
                    painter.setBrush(color);painter.drawRect(QRectF(x,top,fill,bar_h));painter.restore()
            else:
                painter.setBrush(dark);painter.drawRoundedRect(rect,6.0,6.0)
        if 0<=self.tier_index<count:
            x=self.tier_index*(seg+gap)+max(0.0,min(1.0,self.progress))*seg
            center=QPointF(x,top+bar_h/2.0)
            glow=QRadialGradient(center,16.0)
            glow.setColorAt(0.0,QColor(255,255,255,110));glow.setColorAt(0.55,QColor(140,230,255,55));glow.setColorAt(1.0,QColor(140,230,255,0))
            painter.setBrush(glow);painter.drawEllipse(center,16.0,16.0)
            painter.setBrush(QColor('#ffffff'));painter.drawEllipse(center,7.0,7.0)
            painter.setBrush(QColor('#0b0d12'));painter.drawEllipse(center,3.5,3.5)
        font=QFont('Segoe UI',8);font.setBold(True);painter.setFont(font)
        for index,(_floor,name) in enumerate(self.tiers):
            reached=0<=self.tier_index and index<=self.tier_index
            painter.setPen(QColor(self.colors.get(name,'#ffffff')) if reached else QColor('#77818f'))
            painter.drawText(QRectF(index*(seg+gap),top+bar_h+3.0,seg,label_h),Qt.AlignCenter,str(name).upper())
        painter.end()


class _TrendUpIcon(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent);self.setFixedSize(16,16)
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.Antialiasing)
        pen=QPen(QColor('#ff5d6c'));pen.setWidth(2);pen.setCapStyle(Qt.RoundCap);painter.setPen(pen)
        painter.drawLine(QPointF(2,12),QPointF(6,8))
        painter.drawLine(QPointF(6,8),QPointF(10,10))
        painter.drawLine(QPointF(10,10),QPointF(14,3))
        painter.drawLine(QPointF(14,3),QPointF(10,3))
        painter.drawLine(QPointF(14,3),QPointF(14,7))
        painter.end()


class Window(QMainWindow):

    def __init__(self):
        super().__init__();self.settings=E.Settings();self.store=E.Store();self.pool=QThreadPool.globalInstance();self.tasks=set();self.session_started=time.time()
        self.codmunity_client=codmunity_player_stats.CodmunityPublicClient();self.codmunity_auth=None
        self._rebuild_player_service()
        if not self.settings.values.get('single_capture_hotkey_migrated'):
            vk,mod=self.hotkey_setting('scoreboard_hotkey_vk','scoreboard_hotkey_mod',DEFAULT_SCOREBOARD_HOTKEY)
            self.settings.values.update(scoreboard_hotkey_vk=vk,scoreboard_hotkey_mod=mod,screenshot_hotkey_vk=vk,screenshot_hotkey_mod=mod,single_capture_hotkey_migrated=True)
            self.settings.save()
        self.tracking=False;self.paused=False;self.solo=None;self.capture=None;self.capture_busy=False;self.screenshot_busy=False;self.poll_busy=False;self.active_id=None;self.last_capture=None;self.last_frame=None;self.outputs=[];self.browser=None;self.closing=False;self.autotrack_watcher=None;self.sync_worker=None;self.autotrack_state='idle';self._watcher_draft=None;self._watcher_match_id=None
        self.tab_prototype=os.environ.get('VINTENDO_TAB_PROTOTYPE')=='1'
        # Combined CODMunity + scoreboard tracking is the standard behavior every
        # install gets (the local launcher runs this way); set
        # VINTENDO_TRACK_BOTH=0 to opt out and restore the mode selector.
        self.track_both_startup=os.environ.get('VINTENDO_TRACK_BOTH','1')=='1'
        self.setWindowTitle('Vintendo COD Tracker — CODMunity Tab Preview' if self.tab_prototype else 'Vintendo COD Tracker'+self._build_tag());self._fit_to_screen();self.setMinimumSize(1100,730);self.setWindowIcon(QIcon(str(E.ROOT/'tracker.ico')))
    def _build_tag(self):
        try:
            out=__import__('subprocess').check_output(['git','-C',str(E.ROOT),'rev-parse','--short','HEAD'],timeout=5)
            tag=out.decode().strip()
            if tag:return ' \u00b7 build '+tag
        except Exception:pass
        try:tag=(E.ROOT/'BUILD').read_text().strip().split()[0]
        except (OSError,IndexError):return ''
        return (' \u00b7 build '+tag) if tag else ''
    def _fit_to_screen(self):
        try:avail=self.screen().availableGeometry()
        except (AttributeError,RuntimeError):avail=None
        if avail is None:self.resize(1600,1000);return
        self.resize(min(1600,max(1100,int(avail.width()*0.94))),min(1000,max(730,int(avail.height()*0.92))))
        self.move(avail.center()-self.rect().center())
        self.setStyleSheet(STYLE);central=QWidget();central.setObjectName('centralWindow');self.setCentralWidget(central);root=QVBoxLayout(central);root.setContentsMargins(0,0,0,0);root.setSpacing(0)
        self.app_tabs=QTabWidget();self.app_tabs.setObjectName('appTabs');self.app_tabs.setDocumentMode(True);self.app_tabs.tabBar().setMinimumHeight(60);root.addWidget(self.app_tabs)
        tracker_shell=QWidget();tracker_shell_layout=QHBoxLayout(tracker_shell);tracker_shell_layout.setContentsMargins(0,0,0,0);tracker_shell_layout.setSpacing(0);self.app_tabs.addTab(tracker_shell,'Vintendo COD Tracker')
        self.sidebar=QWidget();self.sidebar.setObjectName('sidebar');self.sidebar.setFixedWidth(275);side=QVBoxLayout(self.sidebar);side.setContentsMargins(10,16,10,12);side.setSpacing(8)
        logo_path=E.ROOT/'assets'/'banners'/'dashboard.png'
        logo_pixmap=QPixmap(str(logo_path))
        if not logo_pixmap.isNull():
            self.brand_logo=QLabel();self.brand_logo.setObjectName('brandLogo');self.brand_logo.setAlignment(Qt.AlignCenter);self.brand_logo.setFixedHeight(132)
            self.brand_logo.setPixmap(logo_pixmap.scaled(221,126,Qt.KeepAspectRatio,Qt.SmoothTransformation));side.addWidget(self.brand_logo)
        else:
            self.brand_mark=QLabel('V');self.brand_mark.setObjectName('brandMark');self.brand_mark.setAlignment(Qt.AlignCenter);self.brand_mark.setFixedHeight(68)
            self.brand_name=QLabel('VINTENDO');self.brand_name.setObjectName('brandName');self.brand_name.setAlignment(Qt.AlignCenter)
            self.brand_sub=QLabel('COD TRACKER');self.brand_sub.setObjectName('brandSub');self.brand_sub.setAlignment(Qt.AlignCenter)
            side.addWidget(self.brand_mark);side.addWidget(self.brand_name);side.addWidget(self.brand_sub)
        side.addSpacing(20)
        self.nav=QListWidget();self.nav.setSpacing(3);self.nav.setMinimumWidth(0);self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);self.nav.setTextElideMode(Qt.ElideRight);self.nav.setFrameShape(QFrame.NoFrame);side.addWidget(self.nav,1)
        self.dmz_nav=QListWidget();self.dmz_nav.setSpacing(3);self.dmz_nav.setMinimumWidth(0);self.dmz_nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);self.dmz_nav.setTextElideMode(Qt.ElideRight);self.dmz_nav.setFrameShape(QFrame.NoFrame);side.addWidget(self.dmz_nav,1);self.dmz_nav.hide()
        self.dmz_back=self.button('< Back',self._dmz_exit_mode);self.dmz_back.setObjectName('dmzNavBack');self.dmz_back.setStyleSheet("background:#2563eb;color:#ffffff;font-weight:700;border:1px solid #60a5fa;border-radius:6px;padding:8px 6px;");side.insertWidget(side.indexOf(self.dmz_nav),self.dmz_back);self.dmz_back.hide()
        self._dmz_mode=False;self._dmz_return_row=0
        try:self.dmz_nav.currentRowChanged.connect(self._dmz_nav_changed)
        except (AttributeError,RuntimeError):pass
        side.addSpacing(18);self.sidebar_mw4=QLabel();self.sidebar_mw4.setAlignment(Qt.AlignCenter)
        try:
            mw4_pix=QPixmap(str(E.ROOT/'assets'/'vintendo-cod-tracker-logo.png'))
            if not mw4_pix.isNull():self.sidebar_mw4.setPixmap(mw4_pix.scaledToWidth(210,Qt.SmoothTransformation))
        except Exception:pass
        side.addWidget(self.sidebar_mw4);self.sidebar_mw4.hide()
        self.sidebar_usage=QFrame();self.sidebar_usage.setObjectName('dashboardPanel');usage_layout=QVBoxLayout(self.sidebar_usage);usage_layout.setContentsMargins(12,10,12,10);usage_layout.setSpacing(2)
        usage_title=QLabel('App usage');usage_title.setObjectName('dashCardCaption');usage_layout.addWidget(usage_title)
        self.sidebar_usage_cpu=QLabel('—');self.sidebar_usage_gpu=QLabel('—');self.sidebar_usage_ram=QLabel('—')
        for _name,_value in (('CPU',self.sidebar_usage_cpu),('GPU',self.sidebar_usage_gpu),('RAM',self.sidebar_usage_ram)):
            _row=QHBoxLayout();_row.setSpacing(4)
            _lab=QLabel(_name);_lab.setStyleSheet('color:#8b95a3;font-size:11px;font-weight:700;');_row.addWidget(_lab);_row.addStretch(1)
            _value.setStyleSheet('color:#ffffff;font-size:11px;font-weight:700;');_row.addWidget(_value)
            usage_layout.addLayout(_row)
        side.addWidget(self.sidebar_usage)
        self.meta_login_button=self.button('Log into Meta AI',self.open_login_window);self.meta_login_button.setObjectName('metaLoginButton');self.meta_login_button.setMinimumHeight(40);self.meta_login_button.setToolTip('Open Meta AI so you can sign in for background poster and video jobs.');side.addWidget(self.meta_login_button)
        self.meta_verify_button=self.button('Verify Meta AI login',self.verify_login);self.meta_verify_button.setMinimumHeight(36);self.meta_verify_button.hide();side.addWidget(self.meta_verify_button)
        self.generate_sidebar_button=self.button('Generate AI End Game Picture',self.generate_and_approve);self.generate_sidebar_button.setObjectName('metaLoginButton');self.generate_sidebar_button.setMinimumHeight(44);self.generate_sidebar_button.setToolTip('Generate the AI End Game picture for the newest finished match, then approve the post.');side.addWidget(self.generate_sidebar_button)
        self.guide_button=self.button('?  Guide',self.show_sidebar_guide);self.guide_button.setObjectName('guideButton');self.guide_button.setMinimumHeight(36);self.guide_button.setMinimumWidth(90);self.guide_button.setToolTip('Walk through the sidebar and learn what each button does.');self.guide_button.hide()

        self.stack=QStackedWidget();tracker_shell_layout.addWidget(self.sidebar);tracker_shell_layout.addWidget(self.stack,1)
        self.codmunity_host=WindowsWindowHost();self.codmunity_process=None;self.codmunity_hwnd=None
        self.browser_host=WindowsWindowHost();self.browser_hwnd=None
        self.auto_browser_host=WindowsWindowHost()
        self.app_tabs.tabBar().hide()
        self.codmunity_poll=QTimer(self);self.codmunity_poll.setInterval(500);self.codmunity_poll.timeout.connect(lambda:None)
        self.browser_poll=QTimer(self);self.browser_poll.setInterval(500);self.browser_poll.timeout.connect(self.try_attach_tracker_chrome)
        self.exit_button=QPushButton('EXIT');self.exit_button.setObjectName('exitButton');self.exit_button.setToolTip('Close Vintendo COD Tracker completely');self.exit_button.clicked.connect(self.request_exit);_exit_sep=QFrame();_exit_sep.setFrameShape(QFrame.HLine);_exit_sep.setFrameShadow(QFrame.Plain);_exit_sep.setStyleSheet('color:#2a3340;');side.addWidget(_exit_sep);side.addWidget(self.exit_button)
        self.status=QLabel('\u25cf  STANDBY');self.status.setObjectName('livePill');self.status.setAlignment(Qt.AlignCenter);self.status.setFixedHeight(40)
        self._blink_on=True;self.blink_timer=QTimer(self);self.blink_timer.setInterval(700);self.blink_timer.timeout.connect(self._blink_live_indicator)
        self.status.setCursor(Qt.PointingHandCursor);self.status.setToolTip('Tracking starts automatically on launch. Click to start now.');self.status.mousePressEvent=lambda _event:self.start();self.status.hide()
        side.insertWidget(side.indexOf(self.nav),self.status)
        self.pages={};self.build_dashboard();self.build_solo();self.build_squad();self.build_capture();self.build_roster();self.build_studio();self.build_cod_ai();self.build_tracker_browser();self.build_discord();self.build_history();self.build_camo_tracker();self.build_leaderboards();self.build_stats();self.build_settings();self.build_logs();self.build_dmz_browser();self._add_dmz_nav_entry()
        try:self._autofill_stats_dir()
        except Exception:pass
        QTimer.singleShot(4000,self._boot_refresh_distributions);QTimer.singleShot(2000,self._boot_open_codmunity);QTimer.singleShot(6000,self._boot_autostart);QTimer.singleShot(12000,self._maybe_auto_upload)
        if os.environ.get('PYTEST_CURRENT_TEST') is None:
            self._usage_timer=QTimer(self);self._usage_timer.setInterval(5000);self._usage_timer.timeout.connect(self.refresh_app_usage);self._usage_timer.start()
        if self.tab_prototype:
            for control in (self.start_button,self.stop_button,self.auto_post,self.full_auto,self.allow_both,self.webhook,self.send_test_button,self.approve_post_button,self.generate_poster_button,self.generate_sidebar_button):control.setEnabled(False)
            self.auto_post.setChecked(False)
            self.full_auto.setChecked(False)
        if self.track_both_startup:
            # Keep the existing internal mode for OCR, but hide its redundant
            # manual mode page when the launcher already starts both sources.
            self.mode.setCurrentText('Squad Night');self.mode.setEnabled(False)
            if int(self.settings.values.get('scoreboard_hotkey_migration',0))<1:
                self.settings.values['auto_detect']=False;self.settings.values['scoreboard_hotkey_migration']=1;self.settings.save()
            self.auto_detect.setChecked(bool(self.settings.values.get('auto_detect',False)))
            for row in range(self.nav.count()):
                if self.nav.item(row).data(Qt.UserRole)=='Squad Night':self.nav.item(row).setHidden(True)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex);self.nav.currentRowChanged.connect(self.on_nav_row_changed);self.nav.setCurrentRow(0)
        for hidden_page in ('Solo Ops','Discord Settings','Match History','AI Studio','Stats','Leaderboards'):
            for row in range(self.nav.count()):
                if self.nav.item(row).data(Qt.UserRole)==hidden_page:self.nav.item(row).setHidden(True)
        self.tray=QSystemTrayIcon(self.windowIcon(),self);menu=QMenu();menu.addAction('Open Vintendo COD Tracker',self.showNormal);menu.addAction('Pause tracking',self.pause);menu.addAction('Quit',self.shutdown);self.tray.setContextMenu(menu);self.tray.activated.connect(lambda reason:self.showNormal() if reason==QSystemTrayIcon.DoubleClick else None)
        if QSystemTrayIcon.isSystemTrayAvailable():self.tray.show()
        self.timer=QTimer(self);self.timer.timeout.connect(self.tick);self.timer.start(1000);self.last_poll=0;self.last_auto=0;self.cinematic_refresh_timer=QTimer(self);self.cinematic_refresh_timer.timeout.connect(self.refresh_cinematic_results);self.cinematic_refresh_timer.start(3000)
        self.inapp_timer=QTimer(self);self.inapp_timer.timeout.connect(self.poll_inapp_cinematics);self.inapp_timer.start(10000)
        self._inapp_active=None
        self.refresh_history();self.refresh_roster();self.mode_changed(self.mode.currentText(),initial=True);self.refresh_hotkey_labels()
        try:self._start_codmunity_identity_lookup()
        except Exception:pass
        self.log('Ready. Combined CODMunity + scoreboard tracking will start automatically.' if self.track_both_startup else 'Ready. Solo Ops is the default. Start tracking when the old tracker is stopped.')
        if not self.tab_prototype:self.import_previous_settings()
        self.refresh_cod_ai_screenshots()
        self.register_global_hotkeys()
    def on_nav_row_changed(self,row):
        try:name=self.nav.item(row).data(Qt.UserRole)
        except (AttributeError,RuntimeError):return
        if name=='DMZ Command Center':
            try:self._dmz_enter_mode()
            except (AttributeError,RuntimeError):pass
            return
        if name=='Scoreboard Capture':QTimer.singleShot(0,self.setup_scoreboard_display)
        if name=='Camo Tracker':
            try:self._maybe_refresh_camo()
            except (AttributeError,RuntimeError):pass
        if name=='AI Studio':
            try:
                current=getattr(self,'active_id',None)
                job=self.store.get(current) if current else None
                if job and job['status'] not in ('delivered','solo_delivered'):return
                rows=self.store.history()
                nxt=next((r['id'] for r in rows if r['status'] not in ('baseline','solo_delivered','delivered')),None)
                if nxt:self.load_studio(nxt)
                else:
                    try:self._clear_studio_for_next()
                    except (AttributeError,RuntimeError):pass
            except (AttributeError,RuntimeError):pass
    def _validate_codmunity_attach(self):
        hwnd=getattr(self,'codmunity_hwnd',None)
        if not hwnd:return False
        try:alive=bool(ctypes.windll.user32.IsWindowVisible(hwnd))
        except Exception:alive=False
        if alive:return True
        self.codmunity_hwnd=None;self.codmunity_host.hwnd=None
        if hasattr(self,'codmunity_loading'):self.codmunity_loading.show();self.codmunity_loading.raise_()
        try:self.codmunity_poll.start()
        except (AttributeError,RuntimeError):pass
        self.codmunity_status.setText('CODMunity window closed. Restarting…')
        return False
    def _sync_codmunity_embed_size(self):
        try:
            hwnd=getattr(self.codmunity_host,'hwnd',None)
        except (AttributeError,RuntimeError):
            return
        if not hwnd:
            return
        try:
            import ctypes as _ct
            if _ct.windll.user32.IsWindowVisible(hwnd):
                self.codmunity_host.resize(self.codmunity_surface.width(),self.codmunity_surface.height())
        except Exception:
            pass

    def app_tab_changed(self,index):
        # Single-tab window: left for tests and embed shims. Sync content lives
        # in Settings > Sync & Account and refreshes through _settings_tab_changed.
        return
    def _settings_tab_changed(self,index):
        try:widget=self.settings_tabs.widget(index)
        except (AttributeError,RuntimeError):return
        if widget is not getattr(self,'sync_tab_page',None):return
        if hasattr(self,'sync_state_label'):
            try:self.refresh_codmunity_sync()
            except (AttributeError,RuntimeError):pass
        if not self.tab_prototype and not getattr(self,'poll_busy',False):
            try:self.poll_records(source='sync-tab')
            except (AttributeError,RuntimeError):pass
    def _boot_open_codmunity(self):
        # Window embedding retired: sync content lives under Settings now.
        # The tracker never launches the external desktop app.
        return
    def _close_codmunity_process(self):
        try:
            proc=getattr(self,'codmunity_process',None)
            if proc is not None and proc.poll() is None:proc.terminate()
        except Exception:pass
    def build_codmunity_sync(self,layout):
        account=QFrame();account.setObjectName('dashboardPanel');account_box=QVBoxLayout(account);account_box.setContentsMargins(14,12,14,12);account_box.setSpacing(8)
        account_box.addWidget(QLabel('LINKED ACCOUNT'))
        self.sync_account_label=QLabel('');self.sync_account_label.setWordWrap(True);account_box.addWidget(self.sync_account_label)
        account_row=QHBoxLayout()
        self.sync_pair_button=self.button('Pair new device',self.open_pair_page);account_row.addWidget(self.sync_pair_button)
        self.sync_profile_button=self.button('Copy profile link',self.copy_profile_link);account_row.addWidget(self.sync_profile_button)
        self.sync_site_button=self.button('Open CODMunity site',self.open_codmunity_site);account_row.addWidget(self.sync_site_button)
        self.sync_verify_button=self.button('Verify email',self.send_verification_email);account_row.addWidget(self.sync_verify_button);account_row.addStretch()
        account_box.addLayout(account_row);layout.addWidget(account)
        sync=QFrame();sync.setObjectName('dashboardPanel');sync_box=QVBoxLayout(sync);sync_box.setContentsMargins(14,12,14,12);sync_box.setSpacing(8)
        sync_box.addWidget(QLabel('SYNC OUTBOX (LOCAL ONLY)'))
        self.sync_state_label=QLabel('');self.sync_state_label.setWordWrap(True);sync_box.addWidget(self.sync_state_label)
        sync_row=QHBoxLayout()
        self.sync_now_button=self.button('Sync now',self.sync_now,True);sync_row.addWidget(self.sync_now_button)
        self.sync_export_button=self.button('Export matches JSON',self.export_matches_json);sync_row.addWidget(self.sync_export_button)
        self.sync_diag_button=self.button('Copy diagnostics',self.copy_diagnostics);sync_row.addWidget(self.sync_diag_button);sync_row.addStretch()
        sync_box.addLayout(sync_row);layout.addWidget(sync)
        self.sync_start_button=self.button('Start tracking',self.sync_start_tracking,True);self.sync_stop_button=self.button('Stop tracking',self.sync_stop_tracking);self.sync_start_button.hide();self.sync_stop_button.hide()
        self.row(layout,self.sync_start_button,self.sync_stop_button,self.button('Open Scoreboard Capture',self.goto_tracking))
        self.upload_pending_button=self.button('Upload pending matches',self.upload_pending_matches_ui)
        self.upload_lifetime_button=self.button('Upload lifetime stats',self.upload_lifetime_stats_ui)
        self.row(layout,self.upload_pending_button,self.upload_lifetime_button)
        self.app_usage_label=QLabel('');self.app_usage_label.setWordWrap(True);self.app_usage_label.setObjectName('camoHint')
        layout.addWidget(self.app_usage_label)
        detect=QFrame();detect.setObjectName('dashboardPanel');detect_box=QVBoxLayout(detect);detect_box.setContentsMargins(14,12,14,12);detect_box.setSpacing(8)
        detect_box.addWidget(QLabel('RECENT AUTO-DETECTIONS'))
        self.sync_detect_box=QVBoxLayout();self.sync_detect_box.setSpacing(4)
        detect_box.addLayout(self.sync_detect_box);layout.addWidget(detect)
        layout.addStretch(1)
        self.refresh_codmunity_sync()
    def _sync_user_dir(self):
        try:snapshot=self.store.meta('codmunity_stats') or {}
        except Exception:return ''
        user_dir=str(snapshot.get('user_dir') or '')
        return user_dir
    def refresh_upload_button(self):
        if not hasattr(self,'upload_pending_button'):return
        user_dir=self._sync_user_dir()
        try:
            import codmunity_player_stats as _cps
            pending=len(_cps.pending_matches(user_dir)) if user_dir else 0
        except Exception:pending=0
        self.upload_pending_button.setText(f'Upload pending matches ({pending})' if pending else 'Upload pending matches')
        self.upload_pending_button.setEnabled(bool(user_dir) and self.codmunity_player_service.configured)
        self.upload_pending_button.setToolTip('Sign in to upload.' if not self.codmunity_player_service.configured else ('Import local data first.' if not user_dir else 'POST unsent matches.json entries as your account.'))
    def upload_pending_matches_ui(self):
        if not self.codmunity_player_service.configured:self.notice('Sign in with your CODMunity account first.');return
        user_dir=self._sync_user_dir()
        if not user_dir:self.notice('Import local data first.');return
        try:auth=self.codmunity_auth
        except AttributeError:auth=None
        if auth is None:self.notice('Sign in with your CODMunity account first.');return
        self.sync_state_label.setText('Uploading pending matches…')
        def work():
            import codmunity_player_stats as _cps
            return _cps.upload_pending_matches(auth,user_dir)
        def done(summary):
            failed=len(summary.get('failed',[]))
            self.log(f"Upload complete: {summary.get('uploaded',0)} sent, {summary.get('skipped',0)} already sent, {failed} refused.")
            self.refresh_codmunity_sync()
        def failed(message):self.sync_state_label.setText(str(message));self.log('Match upload failed.')
        self.async_task(work,done,failed)
    def upload_lifetime_stats_ui(self):
        if not self.codmunity_player_service.configured:self.notice('Sign in with your CODMunity account first.');return
        user_dir=self._sync_user_dir()
        if not user_dir:self.notice('Import local data first.');return
        try:auth=self.codmunity_auth
        except AttributeError:auth=None
        if auth is None:self.notice('Sign in with your CODMunity account first.');return
        self.sync_state_label.setText('Uploading lifetime stats…')
        def work():
            import codmunity_player_stats as _cps
            return _cps.upload_lifetime_snapshot(auth,user_dir)
        def done(summary):
            failed=len(summary.get('failed',[]))
            self.log(f"Lifetime upload complete: {summary.get('uploaded',0)} sent, {summary.get('skipped',0)} already sent, {failed} refused.")
            self.refresh_codmunity_sync()
        def failed(message):self.sync_state_label.setText(str(message));self.log('Lifetime upload failed.')
        self.async_task(work,done,failed)
    @staticmethod
    def _usage_pct_text(value):
        try:number=float(value)
        except (TypeError,ValueError):return '—'
        return '<0.1%' if 0<=number<0.1 else f'{number:.1f}%'
    def refresh_app_usage(self):
        if not hasattr(self,'app_usage_label'):return
        try:
            import psutil as _psutil
            _proc=_psutil.Process()
            cpu=_proc.cpu_percent(interval=None)
            rss=_proc.memory_info().rss
            total=_psutil.virtual_memory().total
            text=f'App usage — CPU {self._usage_pct_text(cpu)} · RAM {int(rss)//1048576:,} / {int(total)//1048576:,} MB · v{APP_VERSION}'
        except Exception:
            cpu,rss,text=None,None,f'App usage unavailable · v{APP_VERSION}'
        self.app_usage_label.setText(text)
        if time.monotonic()-getattr(self,'_usage_gpu_at',0.0)>=30.0 or not hasattr(self,'_usage_gpu_text'):
            try:
                import subprocess as _sp
                probe=_sp.run(['nvidia-smi','--query-gpu=utilization.gpu','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=5,creationflags=getattr(_sp,'CREATE_NO_WINDOW',0))
                lines=(probe.stdout or '').strip().splitlines()
                gpu=float(lines[0].strip()) if probe.returncode==0 and lines else None
            except Exception:gpu=None
            self._usage_gpu_text=self._usage_pct_text(gpu) if gpu is not None else '—'
            self._usage_gpu_at=time.monotonic()
        try:
            if hasattr(self,'sidebar_usage_cpu'):self.sidebar_usage_cpu.setText(self._usage_pct_text(cpu) if cpu is not None else '—')
            if hasattr(self,'sidebar_usage_gpu'):self.sidebar_usage_gpu.setText(self._usage_gpu_text)
            if hasattr(self,'sidebar_usage_ram'):self.sidebar_usage_ram.setText(f'{int(rss)//1048576:,} MB' if isinstance(rss,(int,float)) else '—')
        except (AttributeError,RuntimeError,TypeError,ValueError):pass
    def sync_start_tracking(self):
        if getattr(self,'tab_prototype',False):self.notice('Tracking is disabled in preview mode.');return
        if self.tracking:self.log('Tracking is already running.');return
        self.start()
    def sync_stop_tracking(self):
        if not self.tracking:self.log('Tracking is not running.');return
        self.stop()
    def share_to(self,network):
        player=str(self.settings.values.get('player','vintendo') or 'vintendo').strip() or 'vintendo'
        profile=f'https://codmunity.gg/profile/{player}'
        try:snapshot=self.store.meta('codmunity_stats') or {}
        except Exception:snapshot={}
        text=codmunity_camo.summary_line(snapshot,'mw4','multiplayer') or 'My COD stats'
        urls=codmunity_camo.share_urls(profile,text)
        if network not in urls:self.notice('Unknown share target.');return
        QDesktopServices.openUrl(QUrl(urls[network]));self.log(f'Opened {network} share in the browser.')
    def refresh_codmunity_sync(self):
        if not hasattr(self,'sync_state_label'):return
        snapshot=self.store.meta('codmunity_stats') or {}
        account=codmunity_camo.linked_account(snapshot)
        if account:self.sync_account_label.setText(f"Linked cache account {account['uid'][:12]}… — pairing and login stay on their site; this tracker never uploads to CODMunity.")
        else:self.sync_account_label.setText('No linked cache account found. Pair on their site, then press Start tracking to import the baseline.')
        try:pending=self.store.pending_sync(limit=50)
        except Exception:pending=[]
        kinds={}
        for row in pending or []:kinds[str(row.get('kind') or '?')]=kinds.get(str(row.get('kind') or '?'),0)+1
        try:total_matches=len(self.store.history())
        except Exception:total_matches=0
        detail=', '.join(f'{k}: {v}' for k,v in sorted(kinds.items())) or 'empty'
        watcher=getattr(self,'autotrack_watcher',None)
        watching='watching' if worker_alive(watcher) else 'idle'
        last=codmunity_camo.last_detection(self.store,self._autotrack_feed_path() or None)
        if last and last.get('weapon'):
            ago=codmunity_camo.ago_text(last.get('at'))
            tracking=f"Auto tracking: {watching}. Last: {last['weapon']} · {last.get('camo','')}" + (f' · {ago}' if ago else '')
        else:tracking=f'Auto tracking: {watching}. No camo detections yet.'
        self.sync_state_label.setText(f'Outbox pending: {len(pending or [])} ({detail}). Matches stored: {total_matches}. {tracking} Discord posts still go through review + reserve.')
        self.sync_verify_button.setEnabled(self.codmunity_player_service.configured)
        self.sync_verify_button.setToolTip('Sign in to verify your email.' if not self.codmunity_player_service.configured else 'Send the account verification email.')
        self.refresh_upload_button()
        self.refresh_app_usage()
        self.refresh_detect_queue()
    def refresh_detect_queue(self):
        if not hasattr(self,'sync_detect_box'):return
        while self.sync_detect_box.count():
            child=self.sync_detect_box.takeAt(0)
            if child.widget():child.widget().deleteLater()
        try:rows=self.store.camo_detections(limit=8)
        except Exception:rows=[]
        if not rows:
            empty=QLabel('No auto-detections yet. Play with tracking on.');empty.setObjectName('camoHint')
            self.sync_detect_box.addWidget(empty);return
        for row in rows:
            if not isinstance(row,dict):continue
            line=QHBoxLayout()
            state=str(row.get('state') or 'saved')
            ago=codmunity_camo.ago_text(row.get('at'))
            label=QLabel(f"{row.get('weapon','?')} · {row.get('camo','?')} — {state}" + (f' · {ago}' if ago else ''))
            label.setObjectName('camoChallengeLine');label.setWordWrap(True);line.addWidget(label,1)
            if state=='saved':
                undo=self.button('Undo',lambda _on,weapon=str(row.get('weapon','')),camo=str(row.get('camo','')),grind=str(row.get('grind','mw4')):self.undo_camo_detection(weapon,camo,grind))
                line.addWidget(undo)
            wrap=QWidget();wrap.setLayout(line);self.sync_detect_box.addWidget(wrap)
    def undo_camo_detection(self,weapon,camo,grind='mw4'):
        det=codmunity_camo.mark_progress(self.store,weapon,camo,'dismissed',grind=grind)
        if det:
            self.log(f'Camo detection undone: {weapon} / {camo}.')
            self.refresh_detect_queue()
            if hasattr(self,'camo_weapon_list'):self._camo_progress=codmunity_camo.progress_map(self.store,self._autotrack_feed_path() or None);self._camo_apply_filters()
        else:self.notice('Could not undo that detection.')
    def send_verification_email(self):
        if not self.codmunity_player_service.configured:
            self.notice('Sign in with your CODMunity account first.');return
        try:auth=self.codmunity_auth
        except AttributeError:auth=None
        if auth is None:self.notice('Sign in with your CODMunity account first.');return
        self.async_task(auth.send_email_verification,lambda _r:self.log('Verification email sent. Check your inbox.'),lambda message:self.notice(str(message)))
    def open_pair_page(self):
        QDesktopServices.openUrl(QUrl(codmunity_camo.PAIR_URL));self.log('Pair page opened in the browser.')
    def sync_now(self):
        from autotrack import SyncWorker
        worker=SyncWorker(self.store,sender=None,on_log=self._on_autotrack_log)
        self.async_task(worker.step,lambda n:(self.log(f'Sync pass complete: {n} local row(s) settled.'),self.refresh_codmunity_sync()))
    def export_matches_json(self):
        folder=E.DATA/'exports';folder.mkdir(parents=True,exist_ok=True)
        path=folder/('matches-export-'+time.strftime('%Y-%m-%d_%H-%M-%S')+'.json')
        try:rows=self.store.history()
        except Exception as exc:self.notice('Export failed: '+str(exc));return
        path.write_text(json.dumps(rows,indent=2,default=str),encoding='utf-8')
        self.log(f'Exported {len(rows)} matches to {path.name}.')
    def copy_diagnostics(self):
        try:tail=self.store.autotrack_log_tail(50)
        except Exception:tail=[]
        try:pending=self.store.pending_sync(limit=50)
        except Exception:pending=[]
        try:matches=len(self.store.history())
        except Exception:matches=0
        text='\n'.join([f'Version {APP_VERSION}',f'Matches stored: {matches}',f'Outbox pending: {len(pending)}']+list(tail))
        QApplication.clipboard().setText(text);self.log('Diagnostics copied to the clipboard.')
    def codmunity_executable(self):
        override=os.environ.get('CODMUNITY_DESKTOP_EXE','').strip()
        candidates=[Path(override)] if override else []
        candidates.append(Path.home()/'AppData'/'Local'/'CODMunity Desktop'/'codmunity-desktop.exe')
        return next((path for path in candidates if path.is_file()),None)
    def start_or_attach_codmunity(self):
        if self.codmunity_host.hwnd and ctypes.windll.user32.IsWindowVisible(self.codmunity_host.hwnd):
            self.codmunity_host.resize(self.codmunity_surface.width(),self.codmunity_surface.height());return
        if self.codmunity_host.hwnd:self.codmunity_host.detach();self.codmunity_hwnd=None
        hwnd=self.find_codmunity_window()
        if hwnd:
            self.attach_codmunity(hwnd);return
        executable=self.codmunity_executable()
        if not executable:
            self.codmunity_status.setText('CODMunity Desktop was not found. Set CODMUNITY_DESKTOP_EXE and reopen the tracker.');return
        try:
            self.codmunity_process=subprocess.Popen([str(executable)],cwd=str(executable.parent),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.codmunity_status.setText('Starting CODMunity Desktop…');self.codmunity_poll.start()
        except OSError as exc:self.codmunity_status.setText('Could not start CODMunity Desktop: '+str(exc))
    def find_codmunity_window(self):
        user32=ctypes.windll.user32;candidates=[]
        callback_type=ctypes.WINFUNCTYPE(ctypes.c_bool,wintypes.HWND,wintypes.LPARAM)
        user32.EnumWindows.argtypes=[callback_type,wintypes.LPARAM];user32.EnumWindows.restype=wintypes.BOOL
        user32.GetWindowRect.argtypes=(wintypes.HWND,ctypes.POINTER(wintypes.RECT));user32.GetWindowRect.restype=wintypes.BOOL
        @callback_type
        def enum_callback(hwnd,lparam):
            if not user32.IsWindowVisible(hwnd):return True
            pid=wintypes.DWORD();user32.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
            match=False
            if self.codmunity_process and pid.value==self.codmunity_process.pid:match=True
            else:
                try:
                    import psutil
                    if psutil.Process(pid.value).name().lower()=='codmunity-desktop.exe':match=True
                except Exception:pass
            if match:
                rect=wintypes.RECT()
                if user32.GetWindowRect(hwnd,ctypes.byref(rect)):candidates.append((int(hwnd),rect.right-rect.left,rect.bottom-rect.top))
            return True
        user32.EnumWindows(enum_callback,0)
        return self._pick_codmunity_window(candidates)
    @staticmethod
    def _pick_codmunity_window(candidates):
        ranked=sorted(((w*h,hwnd) for hwnd,w,h in candidates if w>=200 and h>=200),reverse=True)
        return ranked[0][1] if ranked else None
    def try_attach_codmunity(self):
        hwnd=self.find_codmunity_window()
        if hwnd:self.codmunity_poll.stop();self.attach_codmunity(hwnd)
        elif self.codmunity_process and self.codmunity_process.poll() is not None:
            self.codmunity_poll.stop();self.codmunity_status.setText(f'CODMunity Desktop exited with code {self.codmunity_process.returncode}.')
            if hasattr(self,'codmunity_loading'):self.codmunity_loading.show();self.codmunity_loading.raise_()
    def attach_codmunity(self,hwnd):
        try:
            rect=wintypes.RECT();ctypes.windll.user32.GetWindowRect(hwnd,ctypes.byref(rect))
            if rect.right-rect.left<200 or rect.bottom-rect.top<200:
                self.codmunity_status.setText('CODMunity window is not ready yet. Waiting…');return
            self.codmunity_surface.winId();self.codmunity_host.attach(hwnd,int(self.codmunity_surface.winId()));self.codmunity_hwnd=hwnd
            self.codmunity_host.resize(self.codmunity_surface.width(),self.codmunity_surface.height());self.codmunity_status.setText('CODMunity Desktop is running in this tab.')
            if hasattr(self,'codmunity_loading'):self.codmunity_loading.hide()
        except Exception as exc:self.codmunity_status.setText('Window embedding failed: '+str(exc));self.log('CODMunity window embedding failed: '+str(exc))
    def resizeEvent(self,event):
        super().resizeEvent(event)
        browser_page=self.pages.get('Tracker Browser',(None,None))[0] if hasattr(self,'pages') else None
        try:
            if hasattr(self,'auto_browser_panel'):self._position_auto_browser_panel()
            if hasattr(self,'auto_browser_host') and self.auto_browser_host.hwnd and hasattr(self,'auto_browser_surface'):
                # Keep a normal browser viewport for Meta's responsive UI and automation,
                # while the sidebar clips the embedded window down to the requested 1×1 pixel.
                self.auto_browser_host.resize(max(800,self.auto_browser_surface.width()),max(600,self.auto_browser_surface.height()))
        except (AttributeError,RuntimeError):pass
        if (hasattr(self,'browser_host') and self.browser_host.hwnd and browser_page is not None
                and self.stack.currentWidget() is browser_page and hasattr(self,'browser_surface')):
            self.browser_host.resize(self.browser_surface.width(),self.browser_surface.height())
        if getattr(self,'codmunity_host',None) is not None and self.codmunity_host.hwnd:
            self.codmunity_host.resize(self.codmunity_surface.width(),self.codmunity_surface.height())
        if hasattr(self,'cinematic_results'):self.update_cinematic_grid()
        if hasattr(self,'cod_ai_screenshots'):self.update_screenshot_grid()
        if hasattr(self,'cod_ai_screenshots') and self.cod_ai_screenshots.currentItem():self.preview_cod_ai_screenshot(self.cod_ai_screenshots.currentItem())
    def _position_auto_browser_panel(self):
        panel=getattr(self,'auto_browser_panel',None)
        if panel is None:return
        # The panel is part of the sidebar layout, directly above the Meta login button.
        # Activating the layout lets Qt recalculate the embedded browser surface size.
        layout=getattr(getattr(self,'sidebar',None),'layout',lambda:None)()
        if layout is not None:layout.activate()
    def page(self,name,subtitle):
        page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(30,25,30,25);layout.setSpacing(14)

        banner_file=PAGE_BANNERS.get(name) if name=='Dashboard' else None;banner_path=(E.ROOT/'assets'/'banners'/banner_file) if banner_file else None
        banner_pix=QPixmap(str(banner_path)) if banner_path and banner_path.is_file() else QPixmap()
        title=QLabel()
        if not banner_pix.isNull():title.setObjectName('pageBanner');title.setPixmap(banner_pix.scaled(QSize(520,170),Qt.KeepAspectRatio,Qt.SmoothTransformation));title.setProperty('bannerFile',banner_file)
        else:title.setObjectName('title');title.setText(name)
        layout.addWidget(title,0,Qt.AlignHCenter if title.objectName()=='pageBanner' else Qt.Alignment());desc=QLabel(subtitle);desc.setWordWrap(True);desc.setStyleSheet('color:#99a8bc');layout.addWidget(desc)
        glyph={'Dashboard':'⌂','Solo Ops':'◎','Squad Night':'◈','Scoreboard Capture':'⊙','Player Roster':'♟','AI Studio':'▧','ScreenShots and Videos':'>','Discord Settings':'⚙','Match History':'☷','Settings':'⚙','Tracker Browser':'▣','Camo Tracker':'◍','Leaderboards':'♛','Stats':'◔'}.get(name,'›')
        item=QListWidgetItem(glyph+'   '+name);item.setData(Qt.UserRole,name);self.nav.addItem(item);self.stack.addWidget(page);self.pages[name]=(page,layout);return layout
    def button(self,text,fn,accent=False):
        b=QPushButton(text);b.clicked.connect(fn)
        if accent:b.setObjectName('accent')
        return b
    def row(self,layout,*widgets):
        row=QHBoxLayout()
        for w in widgets:row.addWidget(w)
        layout.addLayout(row);return row
    SAVE_FLASH_MS=1600
    SAVE_FLASH_OK_STYLE='background:#0d2318;border:1px solid #2ea36b;color:#7bf0a8;font-weight:800;'
    SAVE_FLASH_FAIL_STYLE='background:#2a1215;border:1px solid #ed2938;color:#ff8b93;font-weight:800;'
    def flash_save_button(self,button,base_text=None,ok=True):
        """Briefly swap a save button to a confirmation state so a click has visible feedback."""
        try:
            if button is None:return False
            base=base_text if base_text is not None else str(button.text() or '')
            if base and '✓' not in base and '✕' not in base:button.setProperty('saveBaseText',base)
            else:base=str(button.property('saveBaseText') or base)
            flash_text='✓ Saved!' if ok else '✕ Save failed'
            button.setProperty('saveFlashText',flash_text)
            button.setText(flash_text);button.setStyleSheet(self.SAVE_FLASH_OK_STYLE if ok else self.SAVE_FLASH_FAIL_STYLE)
            button.setProperty('saveFlashActive',True)
            QTimer.singleShot(self.SAVE_FLASH_MS,lambda:self.restore_save_button(button))
            return True
        except (AttributeError,RuntimeError):return False
    def restore_save_button(self,button):
        """Revert a flashed save button to its label. Safe to call when no flash is active."""
        try:
            if button is None:return False
            if not button.property('saveFlashActive'):return False
            button.setProperty('saveFlashActive',False)
            if button.text()==str(button.property('saveFlashText') or ''):
                base=str(button.property('saveBaseText') or '')
                if base:button.setText(base)
            button.setStyleSheet('')
            return True
        except (AttributeError,RuntimeError):return False
    def active_mode_label(self):
        return 'BOTH SOURCES' if self.track_both_startup else self.mode.currentText().upper()
    def edit(self,value=''):return QLineEdit(str(value))
    def notice(self,text):QMessageBox.information(self,'Vintendo COD Tracker',text)
    def ask(self,text):return QMessageBox.question(self,'Vintendo COD Tracker',text)==QMessageBox.Yes
    def show_codmunity_username_help(self):
        dialog=QDialog(self);dialog.setWindowTitle('Find your CODMunity username');dialog.setModal(True)
        layout=QVBoxLayout(dialog);layout.setContentsMargins(20,18,20,16);layout.setSpacing(12)
        heading=QLabel('Your Name without the @ Symbol');heading.setStyleSheet('font-size:18px;font-weight:900;color:#ffffff;');layout.addWidget(heading)
        detail=QLabel('On your CODMunity profile, find the username shown below your display name. Enter that name here without the @ symbol.')
        detail.setWordWrap(True);detail.setStyleSheet('font-size:13px;color:#cbd3df;');layout.addWidget(detail)
        image_label=QLabel();image_label.setAlignment(Qt.AlignCenter)
        example=QPixmap(str(E.ROOT/'assets'/'codmunity_username_example.png'))
        if not example.isNull():image_label.setPixmap(example.scaled(520,190,Qt.KeepAspectRatio,Qt.SmoothTransformation))
        layout.addWidget(image_label,0,Qt.AlignHCenter)
        close=QDialogButtonBox(QDialogButtonBox.Close);close.rejected.connect(dialog.accept);layout.addWidget(close)
        dialog.setMinimumWidth(540);dialog.exec()
    def show_sidebar_guide(self):
        descriptions={
            'Dashboard':'See your latest match, tracker status, and quick actions. Start a full automatic poster job from Match Center.',
            'Solo Ops':'Configure and review solo match tracking from CODMunity data.',
            'Squad Night':'Track squad matches and manage squad-specific match settings.',
            'Scoreboard Capture':'At The End of a match Capture a Screenshot using your hotkey and once loaded you can Generate a Ai picture to discord',
            'Player Roster':'Manage player names, CODMunity links, headshots, and Meta AI photo consent.',
            'AI Studio':'Review a match, its verified player data, and its generated poster.',
            'ScreenShots and Videos':'Capture gameplay screenshots and manage Meta AI videos.',
            'Discord Settings':'Set or test the Discord webhook used for match posts.',
            'Match History':'Browse saved matches and resolve failed or uncertain posts.',
            'Settings':'Adjust tracking, automation, browser, and application preferences.',
            'Tracker Browser':'The Meta AI browser appears here while an automated job is running.'}
        steps=[]
        for row in range(self.nav.count()):
            item=self.nav.item(row);name=str(item.data(Qt.UserRole) or '')
            if item.isHidden():continue
            steps.append(('nav',row,name,descriptions.get(name,'Open the '+name+' section.')))
        steps.append(('button',self.meta_login_button,'Log into Meta AI','Sign into Meta AI once. The tracker reuses this session for automatic poster and video jobs.'))
        if self.meta_verify_button.isVisible():steps.append(('button',self.meta_verify_button,'Verify Meta AI login','Confirm the Meta AI session is signed in and ready for automation.'))
        steps.append(('button',self.generate_sidebar_button,'Generate AI End Game Picture','Start a poster for the newest eligible match, then follow the approval and posting flow.'))
        if not steps:return
        original_row=self.nav.currentRow();index=[0];active=[None]
        dialog=QDialog(self);dialog.setWindowTitle('Sidebar Guide');dialog.setModal(True);dialog.setMinimumWidth(490)
        layout=QVBoxLayout(dialog);layout.setContentsMargins(22,20,22,18);layout.setSpacing(12)
        progress=QLabel();progress.setStyleSheet('color:#78baff;font-weight:800;');layout.addWidget(progress)
        heading=QLabel();heading.setStyleSheet('font-size:20px;font-weight:900;color:#ffffff;');layout.addWidget(heading)
        copy=QLabel();copy.setWordWrap(True);copy.setMinimumHeight(64);copy.setStyleSheet('font-size:14px;color:#cbd3df;');layout.addWidget(copy)
        controls=QHBoxLayout();controls.addStretch(1);back=self.button('Back',lambda:None);next_step=self.button('Next',lambda:None,True);close=self.button('Close Guide',dialog.reject);controls.addWidget(back);controls.addWidget(next_step);controls.addWidget(close);layout.addLayout(controls)
        def clear_highlight():
            kind,target=active[0] if active[0] else (None,None)
            if kind=='button' and target:
                target.setProperty('guideTarget',False);target.style().unpolish(target);target.style().polish(target)
            active[0]=None
        def show_step():
            clear_highlight();kind,target,title,description=steps[index[0]]
            progress.setText(f'STEP {index[0]+1} OF {len(steps)}')
            heading.setText(title);copy.setText(description)
            if kind=='nav':self.nav.setCurrentRow(target)
            else:
                active[0]=(kind,target);target.setProperty('guideTarget',True);target.style().unpolish(target);target.style().polish(target)
            back.setEnabled(index[0]>0);next_step.setText('Finish' if index[0]==len(steps)-1 else 'Next')
        def go_back():
            if index[0]>0:index[0]-=1;show_step()
        def go_next():
            if index[0]>=len(steps)-1:dialog.accept();return
            index[0]+=1;show_step()
        back.clicked.connect(go_back);next_step.clicked.connect(go_next)
        show_step()
        try:dialog.exec()
        finally:
            clear_highlight()
            if 0<=original_row<self.nav.count():self.nav.setCurrentRow(original_row)
    def request_exit(self):
        if self.ask('Exit Vintendo COD Tracker?\n\nTracking stops and all windows close.'):self.shutdown()
    def async_task(self,fn,done=None,failed=None):
        task=Task(fn);self.tasks.add(task)
        def finish(result):
            try:
                if done:done(result)
            except Exception as exc:self.log('UI update failed: '+str(exc))
            finally:self.tasks.discard(task)
        def error(msg):
            self.log(msg)
            if failed:failed(msg)
            self.tasks.discard(task)
        task.signals.done.connect(finish);task.signals.failed.connect(error);self.pool.start(task)
    def build_dashboard(self):
        l=self.page('Dashboard','')
        banner=l.itemAt(0).widget()
        if banner is not None and banner.objectName()=='pageBanner':banner.hide()
        desc=l.itemAt(1).widget()
        if desc is not None:desc.hide()
        header=QHBoxLayout();header.setSpacing(10)
        header.addStretch(1)
        header.addStretch(1)
        self.dashboard_health={}
        self.dash_gear=self.button('',self._goto_settings);self.dash_gear.setObjectName('dashGear');self.dash_gear.setFixedSize(30,30);self.dash_gear.setToolTip('Open Settings');self.dash_gear.setIcon(self._gear_icon());self.dash_gear.setIconSize(QSize(20,20));self.dash_gear.hide()
        l.addLayout(header)
        toolbar=QHBoxLayout();toolbar.setSpacing(8)
        toolbar.addStretch(1)
        self.mode=QComboBox();self.mode.addItems(['Solo Ops','Squad Night']);self.mode.setCurrentText(self.settings.values.get('mode','Solo Ops'));self.mode.currentTextChanged.connect(self.mode_changed);self.mode.setVisible(not self.track_both_startup)
        toolbar.addWidget(self.mode)
        l.addLayout(toolbar)
        self.dash_stat_head=stat_head=QHBoxLayout();stat_head.setSpacing(8)
        self._lifetime_game=('mw4','multiplayer')
        stat_head.addStretch(1)
        self.start_button=self.button('\u25b6   START',self.start,True);self.start_button.setFixedWidth(150);stat_head.addWidget(self.start_button);self.start_button.hide()
        self.stop_button=self.button('\u25a0   STOP',self.stop);self.stop_button.setFixedWidth(150);stat_head.addWidget(self.stop_button);self.stop_button.hide()
        self.dash_refresh_button=self._dash_mini('Refresh',self._refresh_dashboard_stats_view)
        l.addLayout(stat_head)
        self.dash_cod_profile_card=QFrame();self.dash_cod_profile_card.setObjectName('dashboardPanel');card_layout=QHBoxLayout(self.dash_cod_profile_card);card_layout.setContentsMargins(14,12,14,12);card_layout.setSpacing(12)
        self.dash_profile_avatar=QLabel('◉');self.dash_profile_avatar.setFixedSize(96,96);self.dash_profile_avatar.setAlignment(Qt.AlignCenter);self.dash_profile_avatar.setObjectName('profileAvatar');self.dash_profile_avatar.setStyleSheet('background:transparent;border:none;')
        card_layout.addWidget(self.dash_profile_avatar)
        idbox=QVBoxLayout();idbox.setSpacing(2)
        self.dash_card_name=QLabel('—');self.dash_card_name.setObjectName('dashCardName');idbox.addWidget(self.dash_card_name)
        self.dash_card_handle=QLabel('');self.dash_card_handle.setObjectName('dashCardHandle');idbox.addWidget(self.dash_card_handle)
        self.dash_card_stats=QHBoxLayout();self.dash_card_stats.setSpacing(14)
        for _attr,_caption in (('dash_card_kills','KILLS'),('dash_card_kd','K/D RATIO'),('dash_card_wins','WINS'),('dash_card_games','GAMES PLAYED')):
            _cell=QVBoxLayout();_cell.setSpacing(2)
            _value=QLabel('—');_value.setObjectName('dashCardValue');_cell.addWidget(_value)
            setattr(self,_attr,_value)
            _cap=QLabel(_caption);_cap.setObjectName('dashCardCaption');_cell.addWidget(_cap)
            self.dash_card_stats.addLayout(_cell)
        idbox.addLayout(self.dash_card_stats);card_layout.addLayout(idbox)
        card_layout.addStretch(1)
        rightbox=QVBoxLayout();rightbox.setSpacing(2)
        rank_cap=QLabel('YOUR LADDER RANK');rank_cap.setObjectName('dashCardCaption');rank_cap.setAlignment(Qt.AlignRight);rightbox.addWidget(rank_cap)
        self.dash_card_tier=QLabel('—');self.dash_card_tier.setObjectName('dashCardTier');self.dash_card_tier.setAlignment(Qt.AlignRight);rightbox.addWidget(self.dash_card_tier)
        self.dash_card_better=QLabel('');self.dash_card_better.setObjectName('dashCardSmall');self.dash_card_better.setAlignment(Qt.AlignRight);rightbox.addWidget(self.dash_card_better)
        self.dash_card_scope=QLabel('');self.dash_card_scope.setObjectName('dashCardSmall');self.dash_card_scope.setAlignment(Qt.AlignRight);rightbox.addWidget(self.dash_card_scope)
        card_btns=QHBoxLayout();card_btns.setSpacing(8);card_btns.addStretch(1)
        self.dash_card_refresh=self.button('Refresh',self._refresh_dashboard_stats_view);card_btns.addWidget(self.dash_card_refresh)
        self.dash_card_view=self.button('View on codmunity.gg',self._open_dash_profile);card_btns.addWidget(self.dash_card_view)
        self.dash_card_boards=self.button('Leaderboards',lambda:self._goto_nav_page('Leaderboards'));card_btns.addWidget(self.dash_card_boards)
        rightbox.addLayout(card_btns);card_layout.addLayout(rightbox)
        for _btn in (self.dash_card_refresh,self.dash_card_view,self.dash_card_boards):_btn.hide()
        l.addWidget(self.dash_cod_profile_card)
        self.dash_card_game_row=QHBoxLayout();self.dash_card_game_row.setSpacing(6)
        self.dash_profile_game_buttons={}
        for game,mode,label in codmunity_camo.LIFETIME_GAMES:
            pill=self.button(label,lambda _on=False,game=game,mode=mode:self._select_lifetime_game(game,mode));pill.setCheckable(True);pill.setObjectName('dashGamePill')
            if (game,mode)==('mw4','multiplayer'):pill.setChecked(True)
            self.dash_profile_game_buttons[(game,mode)]=pill;self.dash_card_game_row.addWidget(pill)
        self.dash_card_game_row.addStretch(1)
        l.addLayout(self.dash_card_game_row)
        self.dash_rank_card=QFrame();self.dash_rank_card.setObjectName('rankCard');rank_layout=QVBoxLayout(self.dash_rank_card);rank_layout.setContentsMargins(12,10,12,10);rank_layout.setSpacing(4)
        rank_title=QLabel('Your rank');rank_title.setObjectName('dashSection');rank_layout.addWidget(rank_title)
        self.dash_rank_bar=_RankLadder();rank_layout.addWidget(self.dash_rank_bar)
        self.dash_rank_current=QLabel('—');self.dash_rank_current.setObjectName('dashRankCurrent');rank_layout.addWidget(self.dash_rank_current)
        next_row=QHBoxLayout();next_row.setSpacing(6)
        self.dash_rank_trend=_TrendUpIcon();next_row.addWidget(self.dash_rank_trend)
        self.dash_rank_next=QLabel('');self.dash_rank_next.setObjectName('dashRankNext');next_row.addWidget(self.dash_rank_next,1)
        rank_layout.addLayout(next_row)
        l.addWidget(self.dash_rank_card)
        bottom=QHBoxLayout();bottom.setSpacing(12)
        center=QFrame();center.setObjectName('dashboardPanel');center_layout=QVBoxLayout(center);center_layout.setContentsMargins(12,10,12,10);center_layout.setSpacing(6)
        center_header=QHBoxLayout();center_title=QLabel('MATCH CENTER');center_title.setObjectName('dashSection');center_header.addWidget(center_title);center_header.addStretch(1);self.center_count=QLabel('0 matches tracked');self.center_count.setObjectName('dashStatGame');center_header.addWidget(self.center_count);center_layout.addLayout(center_header)
        self.latest_job_id=None
        self.latest_card=QFrame();self.latest_card.setObjectName('latestMatch');self.latest_card.setMaximumHeight(180)
        latest_layout=QHBoxLayout(self.latest_card);latest_layout.setContentsMargins(10,8,10,8);latest_layout.setSpacing(10)
        self.latest_result=QLabel('-');self.latest_result.setObjectName('dashResult');self.latest_result.setAlignment(Qt.AlignCenter);self.latest_result.setMinimumWidth(110);latest_layout.addWidget(self.latest_result,0)
        mid=QVBoxLayout();mid.setSpacing(2)
        self.latest_detail=QLabel('No matches tracked yet.');self.latest_detail.setObjectName('dashDetail');self.latest_detail.setWordWrap(True);mid.addWidget(self.latest_detail)
        self.latest_stats=QLabel('');self.latest_stats.setObjectName('dashStats');self.latest_stats.setWordWrap(True);mid.addWidget(self.latest_stats)
        latest_layout.addLayout(mid,1)
        right=QVBoxLayout();right.setSpacing(4)
        self.latest_status=QLabel('');self.latest_status.setObjectName('dashStatus');self.latest_status.setWordWrap(True);right.addWidget(self.latest_status)
        self.latest_review=self.button('START FULL AUTO JOB',self.start_latest_auto_job,True);self.latest_review.setObjectName('latestAutoJobButton');self.latest_review.setEnabled(False)
        details=self.button('Details',self.open_latest_match)
        right.addWidget(self.latest_review);right.addWidget(details)
        latest_layout.addLayout(right,0)
        center_layout.addWidget(self.latest_card)
        self.latest_auto_pulse_timer=QTimer(self);self.latest_auto_pulse_timer.setInterval(360);self.latest_auto_pulse_timer.timeout.connect(self._pulse_latest_auto_button);self._latest_auto_job_id=None;self._latest_auto_no_discord=False
        self.dash_profile_card=QFrame();self.dash_profile_card.setObjectName('dashboardPanel');profile_layout=QVBoxLayout(self.dash_profile_card);profile_layout.setContentsMargins(12,10,12,10);profile_layout.setSpacing(8)
        self.dash_profile_pages=QStackedWidget()
        meter_page=QWidget();meter_page_layout=QVBoxLayout(meter_page);meter_page_layout.setContentsMargins(0,0,0,0);meter_page_layout.setSpacing(8)
        self.dash_meters={}
        for key,title,color in (('kd','K/D RATIO','#f76b8a'),('wl','W/L RATIO','#62cdff'),('wr','WIN RATE','#62cdff')):
            card=QFrame();card.setObjectName('dashMeter');card_layout=QVBoxLayout(card);card_layout.setContentsMargins(12,10,12,10);card_layout.setSpacing(4)
            title_label=QLabel(title);title_label.setObjectName('dashMeterTitle');card_layout.addWidget(title_label)
            mid=QHBoxLayout();mid.setSpacing(10)
            value=QLabel('\u2014');value.setObjectName('dashMeterValue');value.setStyleSheet('color:'+color+';');mid.addWidget(value)
            ring=None
            if key=='wr':
                ring=_RingMeter(color);mid.addWidget(ring)
            mid.addStretch(1)
            card_layout.addLayout(mid)
            sub=QLabel('');sub.setWordWrap(True);sub.setObjectName('dashMeterSub');card_layout.addWidget(sub)
            bar=_MeterBar(color);card_layout.addWidget(bar)
            cap=QLabel('');cap.setWordWrap(True);cap.setObjectName('dashMeterCaption');card_layout.addWidget(cap)
            meter_page_layout.addWidget(card)
            self.dash_meters[key]={'value':value,'sub':sub,'bar':bar,'cap':cap,'ring':ring}
        self.dash_profile_pages.addWidget(meter_page)
        tile_page=QWidget();tile_grid=QGridLayout(tile_page);tile_grid.setContentsMargins(0,0,0,0);tile_grid.setSpacing(8)
        self.dash_profile_tiles={}
        for index,(key,title) in enumerate((('kills','KILLS'),('deaths','DEATHS'),('wins','WINS'),('losses','LOSSES'),('games','GAMES PLAYED'),('time','TIME PLAYED'),('streak','HIGHEST STREAK'),('best','MOST KILLS IN A GAME'),('spm','SCORE / MIN'),('avg','KILLS PER GAME'))):
            cell=QFrame();cell.setObjectName('dashMeter');cell_layout=QVBoxLayout(cell);cell_layout.setContentsMargins(12,8,12,8);cell_layout.setSpacing(2)
            lab=QLabel(title);lab.setObjectName('dashMeterTitle');cell_layout.addWidget(lab)
            row=QHBoxLayout();row.setSpacing(8)
            value=QLabel('\u2014');value.setObjectName('dashTileValue');row.addWidget(value)
            badge=QLabel('');badge.setObjectName('dashTileBadge');badge.setVisible(False);row.addWidget(badge)
            row.addStretch(1)
            cell_layout.addLayout(row)
            tile_grid.addWidget(cell,int(index/2),index%2)
            self.dash_profile_tiles[key]={'value':value,'badge':badge}
        self.dash_profile_pages.addWidget(tile_page)
        profile_layout.addWidget(self.dash_profile_pages)
        pager=QHBoxLayout();pager.setSpacing(8)
        pager.addStretch(1)
        self.dash_profile_prev=self.button('\u2039',lambda:self._dash_profile_page(-1));self.dash_profile_prev.setObjectName('dashMini');pager.addWidget(self.dash_profile_prev)
        self.dash_profile_next=self.button('\u203a',lambda:self._dash_profile_page(1));self.dash_profile_next.setObjectName('dashMini');pager.addWidget(self.dash_profile_next)
        pager.addStretch(1)
        profile_layout.addLayout(pager)
        refresh_row=QHBoxLayout();refresh_row.setSpacing(8);refresh_row.addStretch(1);refresh_row.addWidget(self.dash_refresh_button);profile_layout.addLayout(refresh_row)
        right=QWidget();right_layout=QVBoxLayout(right);right_layout.setContentsMargins(0,0,0,0);right_layout.setSpacing(12)
        right_layout.addWidget(self.dash_profile_card,1)
        self._match_holder=QWidget();self._match_holder.hide()
        match_holder_layout=QVBoxLayout(self._match_holder);match_holder_layout.setContentsMargins(0,0,0,0);match_holder_layout.setSpacing(0)
        center.setParent(self._match_holder);match_holder_layout.addWidget(center)
        bottom.addWidget(right,30)
        self.dash_bottom=bottom
        self.dash_right=right
        self._profile_avatar_url='';self._profile_avatar_failed=set()
        self._metrics_holder=QWidget();self._metrics_holder.hide()
        self.dashboard_metrics={}
        for key,initial in (('matches','0'),('squad_posts','0'),('reviews','0'),('games','\u2014'),('win_rate','\u2014'),('kd','\u2014'),('kills_per_game','\u2014')):label=QLabel(initial,self._metrics_holder);self.dashboard_metrics[key]=label
        l.addLayout(bottom,1)
        self.hotkey_hint=QLabel('At match end, press the capture hotkey to save a screenshot and prepare scoreboard OCR.');self.hotkey_hint.setObjectName('dashHint');self.hotkey_hint.setWordWrap(True);l.addWidget(self.hotkey_hint);self.hotkey_hint.hide()
        self._refresh_dashboard_stats()
    def _gear_icon(self,size=22):
        from math import cos,pi,sin
        pix=QPixmap(size,size);pix.fill(Qt.transparent)
        painter=QPainter(pix);painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen);painter.setBrush(QBrush(QColor('#c4ccd8')))
        center=size/2;outer=size/2-1.5
        for index in range(8):
            painter.save();painter.translate(center,center);painter.rotate(index*45.0)
            painter.drawRect(QRect(-2,-int(outer),4,6));painter.restore()
        painter.drawEllipse(QPointF(center,center),outer-3.5,outer-3.5)
        painter.setCompositionMode(QPainter.CompositionMode_Clear)
        painter.drawEllipse(QPointF(center,center),3.5,3.5)
        painter.end()
        return QIcon(pix)
    def _goto_settings(self):
        for row in range(self.nav.count()):
            try:
                if self.nav.item(row).data(Qt.UserRole)=='Settings':self.nav.setCurrentRow(row);break
            except (AttributeError,RuntimeError):break
    def _goto_nav_page(self,name):
        for row in range(self.nav.count()):
            try:
                if self.nav.item(row).data(Qt.UserRole)==name:self.nav.setCurrentRow(row);break
            except (AttributeError,RuntimeError):break
    @staticmethod
    def _dmz_web_url(label):
        try:return dict(DMZ_WEB_PAGES).get(str(label or ''),DMZ_WEB_HOME)
        except Exception:return DMZ_WEB_HOME
    def build_dmz_browser(self):
        page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(30,25,30,25);layout.setSpacing(10)
        bar=QHBoxLayout();bar.setSpacing(8)
        hist_back=self.button('<',lambda:self._dmz_web_go(-1));hist_back.setObjectName('dmzBrowserHistBack');bar.addWidget(hist_back)
        hist_fwd=self.button('>',lambda:self._dmz_web_go(1));hist_fwd.setObjectName('dmzBrowserHistFwd');bar.addWidget(hist_fwd)
        reload_btn=self.button('Reload',self._dmz_web_reload);reload_btn.setObjectName('dmzBrowserReload');bar.addWidget(reload_btn)
        home_btn=self.button('Overview',lambda:self._dmz_open_page('Overview'));home_btn.setObjectName('dmzBrowserHome');bar.addWidget(home_btn)
        bar.addStretch(1);layout.addLayout(bar)
        self.dmz_status=QLabel('Live DMZ: open a page from the DMZ menu.');self.dmz_status.setWordWrap(True);self.dmz_status.setObjectName('dmzBrowserStatus');layout.addWidget(self.dmz_status)
        self.dmz_view=None;self.dmz_view_fallback=None
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView
            from PySide6.QtWebEngineCore import QWebEnginePage,QWebEngineProfile
            profile=QWebEngineProfile('dmz',self)
            try:
                store_path=str(E.DATA/'dmz-webprofile');profile.setPersistentStoragePath(store_path);profile.setCachePath(store_path+'/cache')
                try:profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
                except (AttributeError,RuntimeError):pass
                try:
                    from PySide6.QtWebEngineCore import QWebEngineUrlRequestInterceptor as _BlockerBase
                    class _DmzRequestBlocker(_BlockerBase):
                        def interceptRequest(self,info):
                            try:host=info.requestUrl().host()
                            except (AttributeError,RuntimeError):return
                            if _dmz_host_blocked(host):
                                try:info.block(True)
                                except (AttributeError,RuntimeError):pass
                    self.dmz_adblock=_DmzRequestBlocker(profile)
                    profile.setUrlRequestInterceptor(self.dmz_adblock)
                except Exception:pass
            except Exception:pass
            view=QWebEngineView(page);view.setMinimumHeight(480)
            view.setPage(QWebEnginePage(profile,view))
            try:view.page().loadFinished.connect(lambda _ok: self._dmz_hide_site_rail())
            except (AttributeError,RuntimeError):pass
            layout.addWidget(view,1);self.dmz_view=view
        except Exception:
            self.dmz_view=None
            fallback=QLabel('Embedded browser unavailable: Qt WebEngine is not installed.');fallback.setWordWrap(True)
            fallback.setObjectName('dmzBrowserFallback');layout.addWidget(fallback,1);self.dmz_view_fallback=fallback
        self.stack.addWidget(page);self.pages['DMZ Browser']=(page,layout)
    def _add_dmz_nav_entry(self):
        try:
            item=QListWidgetItem('DMZ Command Center');item.setData(Qt.UserRole,'DMZ Command Center');self.nav.addItem(item)
        except (AttributeError,RuntimeError):pass
    def _dmz_enter_mode(self):
        try:
            if getattr(self,'_dmz_mode',False):return
            self._dmz_return_row=0
            for row in range(self.nav.count()):
                try:
                    if str(self.nav.item(row).data(Qt.UserRole) or '')=='Dashboard':self._dmz_return_row=row;break
                except (AttributeError,RuntimeError):continue
        except (AttributeError,RuntimeError):self._dmz_return_row=0
        try:
            self._dmz_mode=True
            nav=self.dmz_nav;nav.blockSignals(True);nav.clear()
            pass
            head=QListWidgetItem('DMZ');head.setData(Qt.UserRole,'__dmz_header');head.setFlags(head.flags()&~Qt.ItemIsEnabled&~Qt.ItemIsSelectable);nav.addItem(head)
            over=QListWidgetItem('Overview');over.setData(Qt.UserRole,'Overview');nav.addItem(over)
            for section,labels in DMZ_NAV_SECTIONS:
                h=QListWidgetItem(section);h.setData(Qt.UserRole,'__dmz_header');h.setFlags(h.flags()&~Qt.ItemIsEnabled&~Qt.ItemIsSelectable);nav.addItem(h)
                for label in labels:
                    it=QListWidgetItem(label);it.setData(Qt.UserRole,label);nav.addItem(it)
            nav.blockSignals(False)
            for _row in range(nav.count()):
                try:
                    if str(nav.item(_row).data(Qt.UserRole) or '')=='Overview':nav.setCurrentRow(_row);break
                except (AttributeError,RuntimeError):continue
            self.nav.hide();nav.show();self.dmz_back.show()
            self.stack.setCurrentWidget(self.pages['DMZ Browser'][0])
        except (AttributeError,RuntimeError,KeyError):pass
    def _dmz_exit_mode(self):
        try:
            if not getattr(self,'_dmz_mode',False):return
            self._dmz_mode=False
            try:self.dmz_nav.hide();self.dmz_back.hide()
            except (AttributeError,RuntimeError):pass
            try:self.nav.show()
            except (AttributeError,RuntimeError):pass
            try:
                row=int(getattr(self,'_dmz_return_row',0) or 0)
                if 0<=row<self.nav.count():self.nav.setCurrentRow(row)
                else:row=0;self.nav.setCurrentRow(row)
                shown=str(self.nav.item(row).data(Qt.UserRole) or '')
                if shown in self.pages:self.stack.setCurrentWidget(self.pages[shown][0])
            except (AttributeError,RuntimeError,TypeError,KeyError):pass
        except (AttributeError,RuntimeError):pass
    def _dmz_nav_changed(self,row):
        try:item=self.dmz_nav.item(row)
        except (AttributeError,RuntimeError):return
        if item is None:return
        try:data=item.data(Qt.UserRole)
        except (AttributeError,RuntimeError):return
        if not data or str(data).startswith('__dmz_'):
            if str(data)=='__dmz_back__':self._dmz_exit_mode()
            return
        self._dmz_open_page(str(data))
    def _dmz_hide_site_rail(self):
        try:page=self.dmz_view.page()
        except AttributeError:return
        if page is None:return
        js="(function(){function hide(){var s=document.getElementById('vintendo-hide-rail');if(!s){try{s=document.createElement('style');s.id='vintendo-hide-rail';s.textContent='.cm-side-menu{display:none!important}';(document.documentElement||document.head||document.body).appendChild(s);}catch(e){}}var bars=document.querySelectorAll?document.querySelectorAll('.cm-side-menu'):[];for(var i=0;i<bars.length;i++){try{bars[i].style.setProperty('display','none','important');}catch(e){}try{bars[i].setAttribute('hidden','');}catch(e){}}}hide();if(window.MutationObserver&&document.documentElement){try{new MutationObserver(hide).observe(document.documentElement,{childList:true,subtree:true});}catch(e){}}})()"
        try:page.runJavaScript(js)
        except (AttributeError,RuntimeError,TypeError):pass
    def _dmz_open_page(self,label):
        try:url=self._dmz_web_url(label)
        except Exception:url=DMZ_WEB_HOME
        try:self.dmz_status.setText('Live: '+str(label)+' - codmunity.gg')
        except (AttributeError,RuntimeError):pass
        try:view=self.dmz_view
        except AttributeError:view=None
        if view is None:return
        self._dmz_hide_site_rail()
        try:
            from PySide6.QtCore import QUrl
            view.load(QUrl(url))
        except (AttributeError,RuntimeError):pass
        try:self.stack.setCurrentWidget(self.pages['DMZ Browser'][0])
        except (AttributeError,RuntimeError,KeyError):pass
    def _dmz_web_go(self,delta):
        try:view=self.dmz_view
        except AttributeError:return
        if view is None:return
        try:
            if delta and delta<0:view.back()
            else:view.forward()
        except (AttributeError,RuntimeError):pass
    def _dmz_web_reload(self):
        try:
            if self.dmz_view is not None:self.dmz_view.reload()
        except (AttributeError,RuntimeError):pass
    @staticmethod
    @staticmethod
    @staticmethod
    @staticmethod
    @staticmethod
    @staticmethod
    @staticmethod
    @staticmethod
    @staticmethod
    def _dash_game_label(game,mode):
        head='Modern Warfare 4' if str(game)=='mw4' else ('Warzone' if str(game)=='warzone' else str(game))
        tail={'multiplayer':'Multiplayer','resurgence':'Resurgence','battle_royale':'Battle Royale'}.get(str(mode),str(mode))
        return (head+' · '+tail).strip(' ·')
    def _dash_profile_url(self):
        try:url=str(self.settings.values.get('profile_url') or '').strip()
        except (AttributeError,RuntimeError):url=''
        if url:return url
        try:username=str(self.settings.values.get('codmunity_username') or '').strip()
        except (AttributeError,RuntimeError):username=''
        return f'https://codmunity.gg/profile/{username}' if username else ''
    def _open_dash_profile(self):
        url=self._dash_profile_url()
        if not url:self.notice('Sign in with your CODMunity account to link the profile.');return
        QDesktopServices.openUrl(QUrl(url));self.log('Opened '+url[:120]+'.')
    def _refresh_dash_profile_card(self,game,mode,stats,rank):
        if not hasattr(self,'dash_card_name'):return
        try:username=str(self.settings.values.get('codmunity_username') or '').strip()
        except (AttributeError,RuntimeError):username=''
        try:display=str(self.settings.values.get('codmunity_display_name') or '').strip()
        except (AttributeError,RuntimeError):display=''
        try:self.dash_card_name.setText(display or username or '—')
        except (AttributeError,RuntimeError):pass
        try:self.dash_card_handle.setText('@'+username if username else '')
        except (AttributeError,RuntimeError):pass
        def _count(key):
            try:value=self._known_number(stats,key)
            except (AttributeError,RuntimeError,TypeError):value=None
            return value
        kills=_count('kills');wins=_count('wins')
        raw_games=(stats.get('games_played',stats.get('games')) if isinstance(stats,dict) else None)
        games=float(raw_games) if isinstance(raw_games,(int,float)) and not isinstance(raw_games,bool) and raw_games>=0 else None
        try:kd=codmunity_camo.lifetime_kd(stats or {})
        except Exception:kd=None
        def _fmt(number):
            return f'{int(number):,}' if isinstance(number,(int,float)) and not isinstance(number,bool) and number>=0 else '—'
        for label,text in ((self.dash_card_kills,_fmt(kills)),(self.dash_card_wins,_fmt(wins)),(self.dash_card_games,_fmt(int(games)) if games is not None else '—')):
            try:label.setText(text)
            except (AttributeError,RuntimeError):pass
        try:self.dash_card_kd.setText(f'{kd:.2f}' if kd is not None else '—')
        except (AttributeError,RuntimeError):pass
        try:self.dash_card_tier.setText(rank.tier if rank is not None else '—')
        except (AttributeError,RuntimeError):pass
        better=f"Better than {rank.percentile:.0f}% of CODMunity players · K/D {kd:.2f}" if (rank is not None and kd is not None) else ''
        try:self.dash_card_better.setText(better)
        except (AttributeError,RuntimeError):pass
        updated=''
        try:
            rec=(self.store.meta('codmunity_public_distributions') or {}).get('|'.join((str(game),str(mode),'lifetime'))) or {}
            stamp=rec.get('updated_at') if isinstance(rec,dict) else None
            parsed=datetime.fromisoformat(str(stamp).replace('Z','+00:00'))
            if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=timezone.utc)
            local=parsed.astimezone()
            updated=f"{local:%b} {local.day}, {local:%Y}, {local.hour%12 or 12}:{local.minute:02d} {'AM' if local.hour<12 else 'PM'}"
        except Exception:updated=''
        line=f'{self._dash_game_label(game,mode)} · Lifetime'+(f' · Updated {updated}' if updated else '')
        try:self.dash_card_scope.setText(line if (stats or username) else '')
        except (AttributeError,RuntimeError):pass
        try:self.dash_card_view.setEnabled(bool(self._dash_profile_url()))
        except (AttributeError,RuntimeError):pass
        try:
            avatar_url=str(self.settings.values.get('codmunity_avatar') or '')
            pix=self.dash_profile_avatar.pixmap()
            if avatar_url and (pix is None or pix.isNull()):self._fetch_profile_avatar(avatar_url)
        except (AttributeError,RuntimeError,TypeError):pass
    def _dash_mini(self,text,fn):
        b=self.button(text,fn);b.setObjectName('dashMini');return b
    def build_solo(self):
        self.page('Solo Ops','CODMunity integration settings are available under Settings.')
        self.solo_settings_panel=QWidget();l=QVBoxLayout(self.solo_settings_panel);l.setContentsMargins(12,12,12,12);l.setSpacing(10)
        self.player=self.edit(self.settings.values['player']);self.player.setFixedWidth(320);self.player.textChanged.connect(lambda _text:self._refresh_codmunity_stats_frame())
        self.stats_dir=self.edit(self.settings.values['stats_dir']);self.stats_dir.setFixedWidth(320)
        self.profile=self.edit(self.settings.values.get('profile_url',''))
        top=QHBoxLayout();top.setSpacing(10)
        account=QFrame();account.setObjectName('dashboardPanel');abox=QVBoxLayout(account);abox.setContentsMargins(14,12,14,12);abox.setSpacing(8)
        abox.addWidget(QLabel('CODMUNITY ACCOUNT'))
        cols=QHBoxLayout();cols.setSpacing(12)
        creds=QFormLayout();creds.setSpacing(8)
        self.codmunity_email_edit=self.edit(self.settings.codmunity_email());self.codmunity_email_edit.setPlaceholderText('you@example.com');self.codmunity_email_edit.setFixedWidth(300)
        self.codmunity_password_edit=self.edit('');self.codmunity_password_edit.setEchoMode(QLineEdit.Password);self.codmunity_password_edit.setPlaceholderText('Password (never stored)');self.codmunity_password_edit.setFixedWidth(300)
        creds.addRow('CODMunity account email',self.codmunity_email_edit);creds.addRow('CODMunity password',self.codmunity_password_edit)
        cols.addLayout(creds)
        actions=QVBoxLayout();actions.setSpacing(6)
        self.codmunity_signin_button=self.button('Sign in',self.codmunity_sign_in,True)
        self.codmunity_signout_button=self.button('Sign out',self.codmunity_sign_out)
        self.codmunity_reset_button=self.button('Reset password',self.codmunity_reset_password)
        for action_button in (self.codmunity_signin_button,self.codmunity_signout_button,self.codmunity_reset_button):action_button.setMinimumWidth(150);actions.addWidget(action_button)
        actions.addStretch(1)
        cols.addLayout(actions);cols.addStretch(1)
        abox.addLayout(cols)
        self.codmunity_account_status=QLabel('');self.codmunity_account_status.setWordWrap(True);self.codmunity_account_status.setStyleSheet('color:#99a8bc');abox.addWidget(self.codmunity_account_status)
        top.addWidget(account,1)
        l.addLayout(top)
        profile=QFrame();profile.setObjectName('dashboardPanel');pbox=QVBoxLayout(profile);pbox.setContentsMargins(14,12,14,12);pbox.setSpacing(6)
        pbox.addWidget(QLabel('CODMUNITY PROFILE'))
        self.codmunity_profile_status=QLabel('Sign in to load the CODMunity profile.');self.codmunity_profile_status.setWordWrap(True);self.codmunity_profile_status.setObjectName('dashStatGame');pbox.addWidget(self.codmunity_profile_status)
        self.codmunity_profile_name=QLabel('—');self.codmunity_profile_name.setObjectName('codmunityProfileName');pbox.addWidget(self.codmunity_profile_name)
        self.codmunity_profile_sub=QLabel('');self.codmunity_profile_sub.setWordWrap(True);self.codmunity_profile_sub.setObjectName('codmunityProfileSub');pbox.addWidget(self.codmunity_profile_sub)
        self.codmunity_profile_stats=QLabel('');self.codmunity_profile_stats.setWordWrap(True);self.codmunity_profile_stats.setObjectName('codmunityProfileStats');pbox.addWidget(self.codmunity_profile_stats)
        l.addWidget(profile)
        configbox=QFrame();configbox.setObjectName('dashboardPanel');cbox=QVBoxLayout(configbox);cbox.setContentsMargins(14,12,14,12);cbox.setSpacing(8)
        cform=QFormLayout();cform.setSpacing(8)
        cform.addRow('CODMunity Name',self.player)
        cform.addRow('CODMunity stats folder',self.stats_dir)
        cform.addRow('',self.button('Browse stats folder',self.pick_stats))
        cbox.addLayout(cform);cbox.addStretch(1)
        top.addWidget(configbox)
        form=QFormLayout()
        self.settings.values['warzone_enabled']=True
        try:self.settings.save()
        except Exception:pass
        form.addRow('CODMunity Profile',self.profile);l.addLayout(form)
        l.addStretch(1)
        self.refresh_codmunity_account_status()
    def squad_game_changed(self,*_):
        if not hasattr(self,'squad_mode'):return
        game=self.squad_game.currentData()
        modes=[('Resurgence','resurgence'),('Battle Royale','battle_royale'),('All modes','all')] if game=='warzone' else [('Multiplayer','multiplayer')]
        self.squad_mode.blockSignals(True);self.squad_mode.clear()
        for label,value in modes:self.squad_mode.addItem(label,value)
        self.squad_mode.blockSignals(False);self.refresh_squad_intel()
    def refresh_squad_intel(self,*_):
        if not hasattr(self,'squad_board'):return
        import player_stats_service as PSS
        game=self.squad_game.currentData() or 'warzone'
        mode=self.squad_mode.currentData() or 'resurgence'
        metric=self.squad_metric.currentData() or 'percentile'
        players=[p for p in self.store.roster() if p.get('enabled',True)]
        table=PSS.squad_table(self.store,players,game,mode,'lifetime',sort_by=metric)
        self.squad_table_rows=table
        self.squad_board.blockSignals(True);self.squad_board.setRowCount(0)
        for row in table:
            snap,totals=row['snapshot'],row['totals']
            name=snap.get('display_name') or snap.get('username') or '?'
            if row['available']:
                values=[name,f"{snap['kd']:.2f}",f"{snap['rank']['percentile']:.1f}%",snap['rank']['tier'],str(int(totals['wins'])),str(totals['matches'])]
            else:
                values=[name,'—','—','Unavailable',str(int(totals['wins'])),str(totals['matches'])]
            at=self.squad_board.rowCount();self.squad_board.insertRow(at)
            for col,text in enumerate(values):
                cell=QTableWidgetItem(text);cell.setFlags(cell.flags()&~Qt.ItemIsEditable);self.squad_board.setItem(at,col,cell)
        self.squad_board.blockSignals(False)
        for combo in self.squad_picks:
            current=combo.currentData()
            combo.blockSignals(True);combo.clear();combo.addItem('—',None)
            for p in players:combo.addItem(str(p.get('display_name') or p.get('name') or '?'),str(p.get('id')))
            index=combo.findData(current);combo.setCurrentIndex(index if index>=0 else 0);combo.blockSignals(False)
        self.refresh_squad_compare()
    def refresh_squad_compare(self,*_):
        if not hasattr(self,'squad_cmp_cards'):return
        import player_stats_service as PSS
        game=self.squad_game.currentData() or 'warzone'
        mode=self.squad_mode.currentData() or 'resurgence'
        by_id={str(p.get('id')):p for p in self.store.roster()}
        for slot,card in enumerate(self.squad_cmp_cards):
            pid=self.squad_picks[slot].currentData() if slot<len(self.squad_picks) else None
            player=by_id.get(str(pid)) if pid else None
            if player is None:
                card.setText('—');continue
            snap=PSS.snapshot_for_player(self.store,player,game,mode,'lifetime')
            totals=PSS.totals_for_player(self.store,player,game,mode)
            name=snap.get('display_name') or snap.get('username') or '?'
            if snap['rank'] is not None:
                color=self.LADDER_TIER_COLORS.get(snap['rank']['tier'],'#e5e9ef')
                card.setText(f"{name}\n{snap['rank']['tier']} · {snap['kd']:.2f} K/D\n{int(totals['wins'])}W / {totals['matches']} matches")
                card.setStyleSheet('background:#141923;border:1px solid '+color+';border-radius:6px;padding:8px;font-size:12px;font-weight:700;color:#e5e9ef;')
            else:
                card.setText(f"{name}\nRANK UNAVAILABLE")
                card.setStyleSheet('background:#141923;border:1px solid #343a46;border-radius:6px;padding:8px;font-size:11px;color:#9da8b7;')
    def build_squad(self):
        l=self.page('Squad Night','CODMunity match context plus the captured scoreboard become one verified, match-bound poster job.')
        for text in ['01  New CODMunity matches enter the queue with their recorded mode, result and placement.', '02  Capture the end-match scoreboard. OCR reads its visible columns; review names and values before marking them verified.', '03  AI Studio combines actual match context, verified player rows and conservative roster/photo matches. Ambiguous or unconsented photos are withheld.', '04  Use Log into Meta AI in the sidebar once to sign in. Verify the session; poster and video jobs then run minimized in the background.', '05  Generate AI End Game Picture submits the match prompt and consented reference photos, collects the result, and returns it to the review flow. The generated scene is prompted to include match results and verified statistics in-world.']:
            label=QLabel(text);label.setWordWrap(True);label.setStyleSheet('padding:15px;background:#1b2330;border-left:3px solid #ff9638');l.addWidget(label)
        self.row(l,self.button('Activate Squad Night',self.activate_squad_night,True))
        squad_title=QLabel('SQUAD LEADERBOARD');squad_title.setStyleSheet('font-size:15px;font-weight:900;color:#f2f4f7;letter-spacing:1px;');l.addWidget(squad_title)
        squad_controls=QHBoxLayout();squad_controls.setSpacing(8)
        self.squad_game=QComboBox();self.squad_game.addItem('Warzone','warzone');self.squad_game.addItem('MW4','mw4')
        self.squad_mode=QComboBox()
        self.squad_metric=QComboBox();self.squad_metric.addItem('Percentile','percentile');self.squad_metric.addItem('K/D','kd');self.squad_metric.addItem('Wins','wins');self.squad_metric.addItem('Win rate','win_rate');self.squad_metric.addItem('Kills','kills');self.squad_metric.addItem('Matches','matches')
        squad_controls.addWidget(QLabel('Game'));squad_controls.addWidget(self.squad_game);squad_controls.addWidget(QLabel('Mode'));squad_controls.addWidget(self.squad_mode)
        squad_controls.addWidget(QLabel('Sort'));squad_controls.addWidget(self.squad_metric)
        squad_controls.addWidget(self.button('Refresh board',self.refresh_squad_intel));squad_controls.addStretch(1)
        squad_controls_wrap=QWidget();squad_controls_wrap.setLayout(squad_controls);l.addWidget(squad_controls_wrap)
        self.squad_game.currentIndexChanged.connect(self.squad_game_changed)
        self.squad_mode.currentIndexChanged.connect(self.refresh_squad_intel)
        self.squad_metric.currentIndexChanged.connect(self.refresh_squad_intel)
        self.squad_board=QTableWidget(0,6);self.squad_board.setHorizontalHeaderLabels(['Player','K/D','Percentile','Tier','Wins','Matches']);self.squad_board.setEditTriggers(QTableWidget.NoEditTriggers);self.squad_board.setSelectionBehavior(QTableWidget.SelectRows);self.squad_board.verticalHeader().hide();self.squad_board.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch);self.squad_board.setMinimumHeight(180)
        l.addWidget(self.squad_board)
        compare_title=QLabel('FOUR-PLAYER COMPARE');compare_title.setStyleSheet('font-size:15px;font-weight:900;color:#f2f4f7;letter-spacing:1px;');l.addWidget(compare_title)
        picks_row=QHBoxLayout();picks_row.setSpacing(8)
        self.squad_picks=[]
        for slot in range(4):
            combo=QComboBox();combo.addItem('—',None);combo.currentIndexChanged.connect(self.refresh_squad_compare);picks_row.addWidget(combo,1);self.squad_picks.append(combo)
        picks_wrap=QWidget();picks_wrap.setLayout(picks_row);l.addWidget(picks_wrap)
        cmp_row=QHBoxLayout();cmp_row.setSpacing(8)
        self.squad_cmp_cards=[]
        for _ in range(4):
            card=QLabel('—');card.setAlignment(Qt.AlignCenter);card.setMinimumHeight(64)
            card.setStyleSheet('background:#141923;border:1px solid #343a46;border-radius:6px;padding:8px;font-size:11px;color:#9da8b7;')
            cmp_row.addWidget(card,1);self.squad_cmp_cards.append(card)
        cmp_wrap=QWidget();cmp_wrap.setLayout(cmp_row);l.addWidget(cmp_wrap)
        self.squad_game_changed()
        l.addStretch()
    def activate_squad_night(self):
        if self.mode.currentText()!='Squad Night':self.mode.setCurrentText('Squad Night')
        else:self.log('Squad Night is already active. Press Start to begin.')
        self.nav.setCurrentRow(0)
    def build_capture(self):
        l=self.page('Scoreboard Capture','At The End of a match Capture a Screenshot using your hotkey and once loaded you can Generate a Ai picture to discord');self.capture_subtitle=l.itemAt(1).widget()
        self.monitor=QComboBox();self.monitor.hide()
        self.capture_setup_button=self.button('Pick Game Monitor',lambda:self.setup_scoreboard_display(force=True))
        self.hotkey_status=QLabel('Global hotkey: starting…');self.hotkey_status.setStyleSheet('color:#99a8bc;font-weight:700;');self.hotkey_status.setCursor(Qt.PointingHandCursor);self.hotkey_status.setToolTip('Click to set the scoreboard hotkey');self.hotkey_status.mousePressEvent=lambda _event:self.open_hotkey_dialog('scoreboard')
        self.capture_now_button=self.button('Capture Now',self.capture_once,True)
        try:self.sidebar.layout().insertWidget(self.sidebar.layout().indexOf(self.nav)+1,self.hotkey_status)
        except (AttributeError,RuntimeError):self.sidebar.layout().addWidget(self.hotkey_status)
        display_row=QHBoxLayout();display_row.addWidget(self.capture_setup_button,2);display_row.addWidget(self.capture_now_button,1);l.addLayout(display_row)
        if str(self.settings.values.get('scoreboard_profile','auto'))!='auto':self.settings.values['scoreboard_profile']='auto';self.settings.save()
        self.capture_subtitle.setText(self._scoreboard_capture_instruction())

        self.auto_detect=QCheckBox();self.auto_detect.setChecked(False);self.auto_detect.hide()
        self.capture_preview=QLabel('No capture yet');self.capture_preview.setAlignment(Qt.AlignCenter);self.capture_preview.setMinimumSize(320,200)
        self.ocr_review_status=QLabel('Capture the final match scoreboard to read player rows.');self.ocr_review_status.setWordWrap(True);self.ocr_review_status.setStyleSheet('font-size:11px;color:#aab4c3;padding:2px;')
        self.ocr_stat_keys=['kills','damage','score','assists','deaths'];self.ocr_table=QTableWidget(0,1+len(self.ocr_stat_keys));self.ocr_table.setHorizontalHeaderLabels(['Player','Kills','Damage','Score','Assists','Deaths']);self.ocr_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.capture_review_split=QSplitter(Qt.Horizontal);self.capture_review_split.setChildrenCollapsible(False);self.capture_review_split.addWidget(self.capture_preview)
        results_panel=QWidget();results_layout=QVBoxLayout(results_panel);results_layout.setContentsMargins(0,0,0,0);results_layout.setSpacing(8);results_layout.addWidget(self.ocr_review_status);results_layout.addWidget(self.ocr_table,1)
        self.capture_review_split.addWidget(results_panel);self.capture_review_split.setStretchFactor(0,2);self.capture_review_split.setStretchFactor(1,5);self.capture_review_split.setSizes([620,1550]);l.addWidget(self.capture_review_split,1)
        self.verified=QCheckBox('I checked these names and numbers against the captured scoreboard');self.verified.setChecked(True);self.verified.hide()
        self.ocr_text=QPlainTextEdit();self.ocr_text.setPlaceholderText('Text From Screenshot');self.ocr_text.setToolTip('Automatically read from the captured scoreboard crop. Edit this only to correct an OCR mistake.')
        self.ocr_advanced=QWidget();advanced_layout=QVBoxLayout(self.ocr_advanced);advanced_layout.setContentsMargins(0,2,0,0);advanced_layout.addWidget(self.ocr_text)
        self.row(advanced_layout,self.button('Re-parse OCR Text',self.parse_ocr),self.button('Manual Player Add',self.add_ocr_row),self.button('Remove Row',self.remove_ocr_row),self.button('Keep matched only',self.keep_matched_ocr_rows))
        self.ocr_advanced.hide();self.ocr_advanced_toggle=QPushButton('Manual input');self.ocr_advanced_toggle.setCheckable(True);self.ocr_advanced_toggle.toggled.connect(self.ocr_advanced.setVisible);l.addWidget(self.ocr_advanced_toggle);l.addWidget(self.ocr_advanced)
        self.save_capture_button=self.button('Save and Generate AI Picture',self.save_capture_and_generate,True);self.save_capture_button.setMinimumHeight(48);self.save_capture_button.setToolTip('Save the reviewed scoreboard, then submit it to Meta AI to generate the picture.');l.addWidget(self.save_capture_button)
    def build_roster(self):
        l=self.page('Player Roster','Manage player names, CODMunity links, and headshots for Meta AI artwork.')
        split=QSplitter();split.setChildrenCollapsible(False)
        left=QFrame();left.setObjectName('dashboardPanel')
        left_layout=QVBoxLayout(left);left_layout.setContentsMargins(14,14,14,14);left_layout.setSpacing(10)
        left_head=QHBoxLayout();left_head.setSpacing(8)
        self.roster_count=QLabel('PLAYERS (0)');self.roster_count.setStyleSheet('font-size:15px;font-weight:900;color:#f2f4f7;letter-spacing:1px;')
        left_head.addWidget(self.roster_count,1);left_layout.addLayout(left_head)
        self.roster_search=self.edit();self.roster_search.setPlaceholderText('Search players...');self.roster_search.setClearButtonEnabled(True)
        self.roster_search.textChanged.connect(lambda _:self.refresh_roster())
        left_layout.addWidget(self.roster_search)
        self.roster_list=QListWidget();self.roster_list.setObjectName('rosterCards');self.roster_list.setSpacing(2)
        self.roster_list.currentItemChanged.connect(lambda cur,prev:self.on_roster_item_changed(cur))
        left_layout.addWidget(self.roster_list,1)
        left_layout.addWidget(self.button('+  New Player',self.roster_new_player))
        left.setMinimumWidth(400);split.addWidget(left)
        right_scroll=QScrollArea();right_scroll.setWidgetResizable(True)
        right=QWidget();right_layout=QVBoxLayout(right);right_layout.setContentsMargins(4,2,10,2);right_layout.setSpacing(12)
        header=QFrame();header.setObjectName('dashboardPanel')
        header_layout=QHBoxLayout(header);header_layout.setContentsMargins(18,16,18,16);header_layout.setSpacing(16)
        self.roster_big_photo=QLabel();self.roster_big_photo.setFixedSize(120,120);self.roster_big_photo.setAlignment(Qt.AlignCenter);self.roster_big_photo.setObjectName('rosterBigPhoto')
        header_layout.addWidget(self.roster_big_photo)
        header_text=QVBoxLayout();header_text.setSpacing(4)
        name_row=QHBoxLayout();name_row.setSpacing(10)
        self.roster_big_name=QLabel('No player selected');self.roster_big_name.setStyleSheet('font-size:26px;font-weight:900;color:#ffffff;')
        name_row.addWidget(self.roster_big_name,1)
        self.roster_enabled_pill=QLabel('');self.roster_enabled_pill.setObjectName('rosterPillDisabled')
        name_row.addWidget(self.roster_enabled_pill,0,Qt.AlignVCenter)
        header_text.addLayout(name_row)
        self.roster_big_sub=QLabel('');self.roster_big_sub.setStyleSheet('font-size:12px;color:#9da8b7;');self.roster_big_sub.setWordWrap(True)
        header_text.addWidget(self.roster_big_sub);header_text.addStretch(1)
        header_layout.addLayout(header_text,1)
        rank_card=QFrame();rank_card.setObjectName('dashboardPanel');rank_card.setMinimumWidth(300)
        rank_layout=QVBoxLayout(rank_card);rank_layout.setContentsMargins(14,10,14,10);rank_layout.setSpacing(3)
        rank_top=QHBoxLayout();rank_top.setSpacing(8)
        rank_caption=QLabel('LADDER RANK');rank_caption.setStyleSheet('font-size:10px;font-weight:700;color:#9da8b7;letter-spacing:1px;')
        rank_top.addWidget(rank_caption);rank_top.addStretch(1)
        self.roster_rank_pct=QLabel('—');self.roster_rank_pct.setStyleSheet('font-size:18px;font-weight:900;color:#ffffff;')
        rank_top.addWidget(self.roster_rank_pct)
        rank_layout.addLayout(rank_top)
        rank_mid=QHBoxLayout();rank_mid.setSpacing(8)
        self.roster_rank_tier=QLabel('Select a player');self.roster_rank_tier.setStyleSheet('font-size:16px;font-weight:900;color:#6b7280;')
        rank_mid.addWidget(self.roster_rank_tier,1)
        self.roster_rank_kd=QLabel('');self.roster_rank_kd.setStyleSheet('font-size:13px;font-weight:700;color:#e5e9ef;')
        rank_mid.addWidget(self.roster_rank_kd,0,Qt.AlignVCenter)
        rank_layout.addLayout(rank_mid)
        self.roster_rank_kdbar=QProgressBar();self.roster_rank_kdbar.setRange(0,1200);self.roster_rank_kdbar.setValue(0);self.roster_rank_kdbar.setTextVisible(False);self.roster_rank_kdbar.setFixedHeight(7)
        self.roster_rank_kdbar.setStyleSheet('QProgressBar{background:#1a1f2a;border:none;border-radius:3px;}QProgressBar::chunk{background:#4ade80;border-radius:3px;}')
        rank_layout.addWidget(self.roster_rank_kdbar)
        seg_row=QHBoxLayout();seg_row.setSpacing(3)
        self.roster_rank_segs=[]
        for _seg_name in [tier_name for _,tier_name in codmunity_player_stats.LADDER_TIERS]:
            seg_box=QVBoxLayout();seg_box.setSpacing(2)
            seg_block=QLabel();seg_block.setFixedHeight(8);seg_block.setStyleSheet('background:#232833;border-radius:2px;')
            seg_box.addWidget(seg_block)
            seg_label=QLabel(_seg_name);seg_label.setAlignment(Qt.AlignCenter);seg_label.setStyleSheet('font-size:8px;color:#6b7280;')
            seg_box.addWidget(seg_label)
            seg_wrap=QWidget();seg_wrap.setLayout(seg_box);seg_row.addWidget(seg_wrap,1)
            self.roster_rank_segs.append((seg_block,seg_label))
        rank_layout.addLayout(seg_row)
        self.roster_rank_next=QLabel('');self.roster_rank_next.setStyleSheet('font-size:11px;font-weight:700;color:#9da8b7;')
        rank_layout.addWidget(self.roster_rank_next)
        header_layout.addWidget(rank_card,1)
        right_layout.addWidget(header)
        self.roster_tabs=QTabWidget()
        info_tab=QWidget();info_form=QFormLayout(info_tab);info_form.setSpacing(10)
        self.roster_name=self.edit();self.roster_display_name=self.edit()
        self.roster_enabled=self.roster_switch('Enabled — include this player in the roster and match processing.')
        self.roster_profile_url=self.edit();self.roster_profile_url.setPlaceholderText('https://codmunity.gg/profile/username')
        self.codmunity_username=self.edit();self.codmunity_username.setPlaceholderText('Enter CODMunity username (for example, vintendopower)')
        info_form.addRow('Enter Players Call of Duty Name from the Game',self.roster_name)
        info_form.addRow('Player Nick name (Shown in AI Pictures and Videos)',self.roster_display_name)
        username_label=QWidget();username_label_layout=QHBoxLayout(username_label);username_label_layout.setContentsMargins(0,0,0,0);username_label_layout.setSpacing(6)
        username_label_layout.addWidget(QLabel('CODMunity username'))
        self.codmunity_username_help=self.button('?',self.show_codmunity_username_help);self.codmunity_username_help.setObjectName('miniHelpButton');self.codmunity_username_help.setFixedSize(20,20);self.codmunity_username_help.setToolTip('Show where to find your CODMunity username.');username_label_layout.addWidget(self.codmunity_username_help);username_label_layout.addStretch(1)
        info_form.addRow(username_label,self.codmunity_username)
        info_form.addRow('CODMunity profile link',self.roster_profile_url)
        self.roster_profile_url.hide()
        try:info_form.labelForField(self.roster_profile_url).hide()
        except (AttributeError,RuntimeError):pass
        info_form.addRow('',self.roster_enabled)
        self.photo_consent=self.roster_switch('This player agreed to their photo being used with Meta AI')
        info_form.addRow('',self.photo_consent)
        consent_hint=QLabel('Allows this headshot in AI-generated artwork. Players without consent are shown generically.');consent_hint.setWordWrap(True);consent_hint.setStyleSheet('font-size:11px;color:#9da8b7;')
        info_form.addRow('',consent_hint)
        self.consent_status=QLabel('');self.consent_status.setWordWrap(True);info_form.addRow('',self.consent_status)
        self.roster_tabs.addTab(info_tab,'PLAYER INFO')
        codmunity_tab=QWidget();codmunity_layout=QVBoxLayout(codmunity_tab);codmunity_layout.setContentsMargins(12,10,12,10);codmunity_layout.setSpacing(8)
        codmunity_controls=QHBoxLayout();codmunity_controls.setSpacing(7)
        self.codmunity_game=QComboBox();self.codmunity_game.addItem('Warzone','warzone');self.codmunity_game.addItem('MW4','mw4')
        self.codmunity_mode=QComboBox()
        self.codmunity_scope=QComboBox();self.codmunity_scope.addItem('Lifetime','lifetime');self.codmunity_scope.addItem('Recent','recent')
        codmunity_controls.addWidget(QLabel('Comparison'));codmunity_controls.addWidget(self.codmunity_game);codmunity_controls.addWidget(self.codmunity_mode);codmunity_controls.addWidget(self.codmunity_scope)
        self.codmunity_resolve_button=self.button('Resolve profile',self.resolve_codmunity_identity)
        self.codmunity_stats_button=self.button('Refresh player stats',self.refresh_codmunity_player_stats)
        self.codmunity_refresh_button=self.button('Refresh community data',self.refresh_codmunity_distribution)
        self.codmunity_refresh_all_button=self.button('Refresh all verified data',self.refresh_all_distributions)
        codmunity_controls.addWidget(self.codmunity_resolve_button);codmunity_controls.addWidget(self.codmunity_stats_button);codmunity_controls.addWidget(self.codmunity_refresh_button);codmunity_controls.addWidget(self.codmunity_refresh_all_button);codmunity_layout.addLayout(codmunity_controls)
        self.codmunity_status=QLabel('Select a roster player to link CODMunity data.');self.codmunity_status.setObjectName('codmunityPlayerStatus');self.codmunity_status.setWordWrap(True);self.codmunity_status.setStyleSheet('font-size:12px;font-weight:800;color:#f0ad4e;padding:6px 8px;background:#171a20;border:1px solid #343a46;border-radius:5px;')
        codmunity_layout.addWidget(self.codmunity_status)
        self.codmunity_identity_label=QLabel('Username —  ·  Display name —  ·  UID —');self.codmunity_identity_label.setObjectName('codmunityIdentity');self.codmunity_identity_label.setWordWrap(True);self.codmunity_identity_label.setStyleSheet('font-size:11px;color:#d4dae4;')
        codmunity_layout.addWidget(self.codmunity_identity_label)
        self.codmunity_profile_label=QLabel('Profile link —');self.codmunity_profile_label.setObjectName('codmunityProfileLink');self.codmunity_profile_label.setWordWrap(True);self.codmunity_profile_label.setStyleSheet('font-size:11px;color:#9da8b7;')
        codmunity_layout.addWidget(self.codmunity_profile_label)
        comparison_grid=QGridLayout();comparison_grid.setHorizontalSpacing(18);comparison_grid.setVerticalSpacing(6)
        self.codmunity_game_mode_value=self._codmunity_value_label()
        self.codmunity_kd_value=self._codmunity_value_label()
        self.codmunity_rank_value=self._codmunity_value_label()
        self.codmunity_percentile_value=self._codmunity_value_label()
        self.codmunity_progress_value=self._codmunity_value_label()
        self.codmunity_next_kd_value=self._codmunity_value_label()
        self.codmunity_median_value=self._codmunity_value_label()
        self.codmunity_updated_value=self._codmunity_value_label()
        for row,(title,value) in enumerate((
            ('Game / mode',self.codmunity_game_mode_value),
            ('K/D source',self.codmunity_kd_value),
            ('Ladder Rank',self.codmunity_rank_value),
            ('Community percentile',self.codmunity_percentile_value),
            ('Progress to next rank',self.codmunity_progress_value),
            ('Next-rank K/D',self.codmunity_next_kd_value),
            ('Community median K/D',self.codmunity_median_value),
            ('Last updated',self.codmunity_updated_value),
        )):
            comparison_grid.addWidget(QLabel(title),row//2,(row%2)*2)
            comparison_grid.addWidget(value,row//2,(row%2)*2+1)
        codmunity_layout.addLayout(comparison_grid)
        self.codmunity_distribution_label=QLabel('No community distribution loaded.');self.codmunity_distribution_label.setObjectName('codmunityDistribution');self.codmunity_distribution_label.setWordWrap(True);self.codmunity_distribution_label.setStyleSheet('font-size:10px;color:#9da8b7;padding:6px 8px;background:#10131a;border:1px solid #2b303c;border-radius:5px;')
        codmunity_layout.addWidget(self.codmunity_distribution_label);codmunity_layout.addStretch(1)
        self._codmunity_live_key=None
        self.codmunity_client=codmunity_player_stats.CodmunityPublicClient()
        self.codmunity_auth=None
        self._rebuild_player_service()
        self._codmunity_tab_index=self.roster_tabs.addTab(codmunity_tab,'CODMUNITY')
        self.codmunity_game.currentIndexChanged.connect(self.codmunity_game_changed)
        self.codmunity_mode.currentIndexChanged.connect(self._codmunity_comparison_changed)
        self.codmunity_scope.currentIndexChanged.connect(self._codmunity_comparison_changed)
        self.codmunity_game_changed()
        photo_tab=QWidget();photo_layout=QVBoxLayout(photo_tab);photo_layout.setSpacing(10)
        self.photo_preview=QLabel('No headshot');self.photo_preview.setAlignment(Qt.AlignCenter);self.photo_preview.setMinimumHeight(220);self.photo_preview.setObjectName('rosterPhotoPreview')
        checker=QPixmap(16,16);checker.fill(QColor('#1c2029'))
        checker_painter=QPainter(checker);checker_painter.fillRect(0,0,8,8,QColor('#12151c'));checker_painter.fillRect(8,8,8,8,QColor('#12151c'));checker_painter.end()
        checker_palette=self.photo_preview.palette();checker_palette.setBrush(QPalette.Window,QBrush(checker));self.photo_preview.setPalette(checker_palette);self.photo_preview.setAutoFillBackground(True)
        photo_layout.addWidget(self.photo_preview,1)
        self.headshot_filename=QLabel('No headshot selected');self.headshot_filename.setStyleSheet('font-size:11px;color:#77818f;')
        photo_layout.addWidget(self.headshot_filename)
        photo_buttons=QHBoxLayout();photo_buttons.setSpacing(10)
        photo_buttons.addWidget(self.button('Choose Headshot',self.pick_photo))
        photo_buttons.addWidget(self.button('Remove Photo',self.remove_roster_photo))
        photo_layout.addLayout(photo_buttons)
        self.roster_tabs.addTab(photo_tab,'HEADSHOT')
        profile_tab=QWidget();profile_layout=QVBoxLayout(profile_tab);profile_layout.setSpacing(10)
        profile_nav=QHBoxLayout();profile_nav.setSpacing(6)
        self.profile_back_button=self.button('‹ Back',self.profile_page_back);self.profile_reload_button=self.button('⟳ Reload',self.profile_page_reload)
        profile_nav.addWidget(self.profile_back_button);profile_nav.addWidget(self.profile_reload_button)
        self.profile_link_view=QLabel('No profile link set');self.profile_link_view.setWordWrap(True);self.profile_link_view.setStyleSheet('font-size:11px;color:#9da8b7;')
        profile_nav.addWidget(self.profile_link_view,1)
        self.profile_open_button=self.button('Open in Browser',self.open_roster_profile);profile_nav.addWidget(self.profile_open_button);profile_layout.addLayout(profile_nav)
        self.profile_view=None
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView
            self.profile_view=QWebEngineView(profile_tab);self.profile_view.setMinimumHeight(420)
            profile_layout.addWidget(self.profile_view,1)
        except Exception:
            self.profile_view=None
        self._profile_loaded_url='';self._profile_tab_index=self.roster_tabs.addTab(profile_tab,'PROFILE')
        self.roster_tabs.currentChanged.connect(self._on_roster_tab_changed)
        right_layout.addWidget(self.roster_tabs,1)
        action_row=QHBoxLayout();action_row.setSpacing(10)
        self.roster_status=QLabel('');self.roster_status.setStyleSheet('font-size:11px;color:#9da8b7;');self.roster_status.setWordWrap(True)
        action_row.addWidget(self.roster_status,1)
        action_row.addWidget(self.button('Delete',self.delete_player))
        self.roster_save_button=self.button('Save Player',self.save_player,True);action_row.addWidget(self.roster_save_button)
        right_layout.addLayout(action_row)
        right_scroll.setWidget(right)
        split.addWidget(right_scroll)
        split.setStretchFactor(0,4);split.setStretchFactor(1,6)
        l.addWidget(split,1)

        self.photo_path=self.edit();self.photo_path.setVisible(False)
        self.current_player=None;self.roster_rows=[];self.roster_visible_ids=[];self.roster_dirty=False;self._roster_loading=False;self.roster_photo_removed=False
        for widget in (self.roster_name,self.roster_display_name,self.codmunity_username):widget.textChanged.connect(lambda _:self.mark_roster_dirty())
        self.codmunity_username.textChanged.connect(self.update_codmunity_profile_link)
        self.roster_profile_url.textChanged.connect(lambda _: (self.refresh_profile_tab(),self.mark_roster_dirty()))
        self.roster_enabled.toggled.connect(lambda _:self.mark_roster_dirty())
        self.photo_consent.toggled.connect(lambda _:self.mark_roster_dirty())
    def roster_switch(self,text):
        box=QCheckBox(text);box.setObjectName('rosterSwitch');return box
    def _codmunity_value_label(self):
        label=QLabel('—');label.setStyleSheet('font-size:11px;font-weight:800;color:#f2f4f7;');label.setWordWrap(True);return label
    def codmunity_game_changed(self,*_):
        if not hasattr(self,'codmunity_mode'):return
        game=self.codmunity_game.currentData()
        old=self.codmunity_mode.currentData()
        modes=[('Resurgence','resurgence'),('Battle Royale','battle_royale'),('All modes','all')] if game=='warzone' else [('Multiplayer','multiplayer')]
        self.codmunity_mode.blockSignals(True);self.codmunity_mode.clear()
        for label,value in modes:self.codmunity_mode.addItem(label,value)
        index=self.codmunity_mode.findData(old)
        self.codmunity_mode.setCurrentIndex(index if index>=0 else 0);self.codmunity_mode.blockSignals(False)
        self.refresh_codmunity_panel()
        if hasattr(self,'consent_status') and hasattr(self,'photo_path'):self.refresh_roster()
    def _codmunity_player_record(self,pid=None):
        pid=str(pid or getattr(self,'current_player',None) or '')
        if not pid:return None
        return next((row for row in self.store.roster() if str(row.get('id'))==pid),None)
    def _save_codmunity_fields(self,pid,fields):
        player=self._codmunity_player_record(pid)
        if player is None:return False
        player.update(fields);self.store.save_player(player)
        self.roster_rows=self.store.roster()
        return True
    @staticmethod
    def _codmunity_timestamp(value):
        try:
            parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
            if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        except (TypeError,ValueError):return str(value or '—')
    def resolve_codmunity_identity(self):
        player=self._codmunity_player_record()
        username=self.codmunity_username.text().strip()
        if player is None:
            self.codmunity_status.setText('Save this roster entry before resolving its CODMunity profile.');return
        if not username:
            self.codmunity_status.setText('Enter a CODMunity username in Player Info first.');return
        pid=str(player['id']);self.codmunity_status.setText('Resolving public profile metadata.')
        self.async_task(lambda:self.codmunity_client.profile_identity(username),
                        lambda identity,pid=pid,username=username:self.on_codmunity_identity(pid,username,identity),
                        lambda message,pid=pid:self.on_codmunity_failure(pid,'profile',message))
    def on_codmunity_identity(self,pid,requested_username,identity):
        if str(self.current_player or '')!=str(pid) or self.codmunity_username.text().strip().casefold()!=requested_username.strip().casefold():return
        profile_url='https://codmunity.gg/profile/'+quote(identity.username,safe='')
        updated_at=datetime.now(timezone.utc).isoformat(timespec='seconds')
        self._save_codmunity_fields(pid,{'codmunity_username':identity.username,'codmunity_uid':identity.uid,
            'codmunity_display_name':identity.display_name,'profile_url':profile_url,
            'codmunity_identity_updated_at':updated_at})
        self._roster_loading=True
        self.roster_profile_url.setText(profile_url)
        self._roster_loading=False
        self.refresh_profile_tab();self.refresh_codmunity_panel();self.refresh_roster_rank_card();self.refresh_player_intel()
        self.codmunity_status.setText('Public profile identity resolved and saved.')
    def _boot_refresh_distributions(self,force=False):
        if getattr(self,'_boot_dist_done',False) and not force:return
        self._boot_dist_done=True
        try:
            cached=self.store.meta('codmunity_public_distributions',{}) or {}
            if not force and cached.get('warzone|resurgence|lifetime'):return
            self.log('Loading verified community data.')
            if self._codmunity_player_record() is None:
                wanted=str(self.settings.values.get('player','')).casefold()
                roster=self.store.roster()
                pick=next((p for p in roster if wanted and (str(p.get('name','')).casefold()==wanted or wanted in [str(a).casefold() for a in (p.get('aliases') or []) if isinstance(a,str)])),None)
                if pick is None:pick=next((p for p in roster if p.get('enabled',True)),None)
                if pick is None:return
                try:self.refresh_roster();self.load_roster_player(str(pick.get('id')))
                except (AttributeError,RuntimeError):return
            self.refresh_all_distributions()
        except (AttributeError,RuntimeError):pass
    def refresh_all_distributions(self):
        player=self._codmunity_player_record()
        if player is None:
            self.codmunity_status.setText('Save this roster entry before loading community data.');return
        import player_stats_service as PSS
        self._refresh_queue=[tuple(combo) for combo in PSS.VERIFIED_GLOBAL_COMBOS]
        self._refresh_total=len(self._refresh_queue);self._refresh_failed=0
        self._pump_refresh_queue()
    def _pump_refresh_queue(self):
        queue=getattr(self,'_refresh_queue',None)
        if not queue:return
        player=self._codmunity_player_record()
        if player is None:self._refresh_queue=[];return
        game,mode,scope=queue[0]
        try:self.codmunity_status.setText(f'Loading verified community data {self._refresh_total-len(queue)+1}/{self._refresh_total} ({game}/{mode}/{scope})…')
        except (AttributeError,RuntimeError):pass
        pid=str(player['id']);key='|'.join((str(game),str(mode),str(scope)))
        self.async_task(lambda:self.codmunity_client.community_distribution(game,mode,scope),
                        lambda distribution,pid=pid,key=key:self.on_codmunity_distribution(pid,key,distribution),
                        lambda message,pid=pid,key=key:self.on_codmunity_failure(pid,'distribution',message,key))
    def _advance_refresh_queue(self):
        queue=getattr(self,'_refresh_queue',None)
        if not queue:return False
        queue.pop(0)
        if queue:self._pump_refresh_queue()
        return not queue
    def refresh_codmunity_distribution(self):
        player=self._codmunity_player_record()
        if player is None:
            self.codmunity_status.setText('Save this roster entry before loading community data.');return
        game=self.codmunity_game.currentData();mode=self.codmunity_mode.currentData();scope=self.codmunity_scope.currentData()
        pid=str(player['id']);key='|'.join((str(game),str(mode),str(scope)))
        self.codmunity_status.setText('Loading public community distribution.')
        self.async_task(lambda:self.codmunity_client.community_distribution(game,mode,scope),
                        lambda distribution,pid=pid,key=key:self.on_codmunity_distribution(pid,key,distribution),
                        lambda message,pid=pid,key=key:self.on_codmunity_failure(pid,'distribution',message,key))
    def _rebuild_player_service(self):
        try:token=self.settings.codmunity_refresh_token()
        except Exception:token=''
        if token:
            self.codmunity_auth=codmunity_player_stats.CodmunityAuthClient(refresh_token=token)
            self.codmunity_player_service=codmunity_player_stats.ModularPlayerStatsService(
                codmunity_player_stats.build_authorized_fetcher(self.codmunity_auth))
        else:
            self.codmunity_auth=codmunity_player_stats.CodmunityAuthClient()
            self.codmunity_player_service=codmunity_player_stats.ModularPlayerStatsService()
    def refresh_codmunity_account_status(self):
        if not hasattr(self,'codmunity_account_status'):return
        try:signed_in=bool(self.settings.codmunity_refresh_token())
        except Exception:signed_in=False
        email=self.settings.codmunity_email()
        if signed_in:self.codmunity_account_status.setText(f"Signed in as {email or 'your CODMunity account'}. Live player stats use your account.")
        else:self.codmunity_account_status.setText('Not signed in. Live player stats stay unavailable until sign-in.')
    def startup_offline_requirements(self):
        issues=[]
        try:token=self.settings.codmunity_refresh_token()
        except Exception:token=''
        if not token:
            issues.append({'id':'codmunity','mode':'sign_in','title':'CODMunity sign-in required','detail':'Sign in with your CODMunity email and password so match uploads, lifetime stats, and profile linking keep working.'})
        try:meta_needed=bool(self.settings.values.get('meta_login_needed',False))
        except Exception:meta_needed=False
        if meta_needed:
            issues.append({'id':'meta_ai','mode':'browser_login','title':'Meta AI login required','detail':'Poster and video automation needs a Meta AI sign-in. Open the Meta AI login window, sign in there, then verify.'})
        return issues
    def startup_session_issue(self,validate=None):
        try:token=self.settings.codmunity_refresh_token()
        except Exception:token=''
        if not token:return None
        client=getattr(self,'codmunity_auth',None)
        try:
            if validate is not None:validate(client)
            elif client is not None:client.id_token()
            else:return None
        except Exception as exc:
            return {'id':'codmunity','mode':'relogin','title':'CODMunity session expired','detail':'Your saved CODMunity session no longer works. Sign in again below. ('+str(exc)[:140]+')'}
        return None
    def maybe_show_startup_requirements(self):
        if getattr(self,'tab_prototype',False):return False
        if is_smoke_run():return False
        if os.environ.get('PYTEST_CURRENT_TEST') is not None:return False
        issues=self.startup_offline_requirements()
        if any(item.get('id')=='codmunity' for item in issues):
            self.show_startup_requirements_dialog(issues,select='codmunity')
            return True
        def work():return (self.startup_session_issue(),[item for item in self.startup_offline_requirements() if item.get('id')!='codmunity'])
        def done(result):
            try:session_issue,rest=result
            except Exception:return
            if session_issue:self.show_startup_requirements_dialog([session_issue]+list(rest or []),select='codmunity')
            elif rest:self.show_startup_requirements_dialog(list(rest),select=rest[0].get('id'))
        try:self.async_task(work,done)
        except Exception:pass
        return True
    def build_startup_requirements_dialog(self,issues,select=None):
        dialog=QDialog(self);dialog.setWindowTitle('Startup requirements');dialog.setModal(True);dialog.setMinimumWidth(480)
        layout=QVBoxLayout(dialog);layout.setContentsMargins(22,20,22,18);layout.setSpacing(12)
        heading=QLabel('Finish signing in');heading.setStyleSheet('font-size:18px;font-weight:900;color:#ffffff;');layout.addWidget(heading)
        note=QLabel('The tracker found something that needs your login before everything works. Fix it here, or press Continue to skip for now.');note.setWordWrap(True);note.setStyleSheet('font-size:13px;color:#cbd3df;');layout.addWidget(note)
        codmunity_status=QLabel('');codmunity_status.setWordWrap(True);codmunity_status.setStyleSheet('font-size:13px;color:#99a8bc;')
        for issue in issues or []:
            if issue.get('id')=='codmunity':
                box=QFrame();box.setObjectName('dashboardPanel');form=QVBoxLayout(box);form.setContentsMargins(14,12,14,12);form.setSpacing(8)
                title=QLabel(str(issue.get('title') or 'CODMunity sign-in'));title.setStyleSheet('font-size:15px;font-weight:800;color:#ffffff;');form.addWidget(title)
                detail=QLabel(str(issue.get('detail') or ''));detail.setWordWrap(True);detail.setStyleSheet('font-size:13px;color:#cbd3df;');form.addWidget(detail)
                grid=QFormLayout();grid.setSpacing(8)
                email_edit=QLineEdit(str(self.settings.codmunity_email()));email_edit.setPlaceholderText('you@example.com');grid.addRow('CODMunity email',email_edit)
                password_edit=QLineEdit();password_edit.setEchoMode(QLineEdit.Password);password_edit.setPlaceholderText('Password (never stored)');grid.addRow('Password',password_edit)
                form.addLayout(grid);form.addWidget(codmunity_status)
                def do_sign_in(_checked=False):
                    email=email_edit.text().strip();password=password_edit.text()
                    if not email or not password:codmunity_status.setText('Enter your CODMunity email and password.');return
                    codmunity_status.setText('Signing in...')
                    def ok():
                        try:password_edit.clear()
                        except (AttributeError,RuntimeError):pass
                        try:dialog.accept()
                        except (AttributeError,RuntimeError):pass
                    self.codmunity_sign_in(email=email,password=password,on_signed_in=ok,on_failed=codmunity_status.setText)
                sign_button=self.button('Sign in',do_sign_in,True)
                row=QHBoxLayout();row.addStretch(1);row.addWidget(sign_button);form.addLayout(row)
                layout.addWidget(box)
                if select=='codmunity':
                    try:email_edit.setFocus()
                    except (AttributeError,RuntimeError):pass
            elif issue.get('id')=='meta_ai':
                box=QFrame();box.setObjectName('dashboardPanel');form=QVBoxLayout(box);form.setContentsMargins(14,12,14,12);form.setSpacing(8)
                title=QLabel(str(issue.get('title') or 'Meta AI login'));title.setStyleSheet('font-size:15px;font-weight:800;color:#ffffff;');form.addWidget(title)
                detail=QLabel(str(issue.get('detail') or ''));detail.setWordWrap(True);detail.setStyleSheet('font-size:13px;color:#cbd3df;');form.addWidget(detail)
                meta_row=QHBoxLayout();meta_row.addStretch(1)
                meta_row.addWidget(self.button('Open Meta AI login',self.open_login_window))
                meta_row.addWidget(self.button('Verify Meta AI login',self.verify_login))
                form.addLayout(meta_row)
                layout.addWidget(box)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok);buttons.button(QDialogButtonBox.Ok).setText('Continue');buttons.accepted.connect(dialog.accept);layout.addWidget(buttons)
        return dialog
    def show_startup_requirements_dialog(self,issues,select=None):
        try:dialog=self.build_startup_requirements_dialog(issues,select=select)
        except (AttributeError,RuntimeError):return False
        try:dialog.exec()
        except (AttributeError,RuntimeError):return False
        return True
    def codmunity_sign_in(self,email=None,password=None,on_signed_in=None,on_failed=None):
        if email is None or isinstance(email,bool):email=self.codmunity_email_edit.text().strip() if hasattr(self,'codmunity_email_edit') else ''
        if password is None or isinstance(password,bool):password=self.codmity_password_edit.text() if hasattr(self,'codmunity_password_edit') else ''
        if not email or not password:
            if hasattr(self,'codmunity_account_status'):self.codmunity_account_status.setText('Enter your CODMunity email and password.')
            return
        if hasattr(self,'codmunity_account_status'):self.codmunity_account_status.setText('Signing in…')
        def work():
            auth=codmunity_player_stats.CodmunityAuthClient()
            info=auth.sign_in(email,password)
            return auth,info
        def done(result):
            auth,info=result
            self.settings.values['codmunity_email']=info.get('email') or email
            self.settings.set_codmunity_refresh_token(auth.refresh_token)
            self.settings.save()
            if hasattr(self,'codmunity_email_edit'):self.codmunity_email_edit.setText(self.settings.values['codmunity_email'])
            if hasattr(self,'codmunity_password_edit'):self.codmunity_password_edit.clear()
            self._rebuild_player_service();self.refresh_codmunity_account_status()
            self.log('CODMunity sign-in complete; only the session token is stored.');self.refresh_codmunity_panel();self._start_codmunity_identity_lookup();self._auto_upload_done=False;self._maybe_auto_upload()
            if on_signed_in is not None:
                try:on_signed_in()
                except Exception:pass
        def failed(message):
            if hasattr(self,'codmunity_account_status'):self.codmunity_account_status.setText(str(message))
            self.log('CODMunity sign-in failed.')
            if on_failed is not None:
                try:on_failed(str(message))
                except Exception:pass
        self.async_task(work,done,failed)
    def codmunity_reset_password(self):
        email=self.codmunity_email_edit.text().strip() if hasattr(self,'codmunity_email_edit') else ''
        if not email:
            if hasattr(self,'codmunity_account_status'):self.codmunity_account_status.setText('Enter your CODMunity email first.')
            return
        if hasattr(self,'codmunity_account_status'):self.codmunity_account_status.setText('Sending reset email…')
        def work():
            import codmunity_player_stats as _cps
            _cps.request_password_reset(email)
            return True
        def done(_result):
            if hasattr(self,'codmunity_account_status'):self.codmunity_account_status.setText('Reset email sent. Check your inbox.')
            self.log('Password reset email sent.')
        def failed(message):
            if hasattr(self,'codmunity_account_status'):self.codmunity_account_status.setText(str(message))
            self.log('Password reset failed.')
        self.async_task(work,done,failed)
    def codmunity_sign_out(self):
        try:
            self.settings.values['protected_codmunity_refresh']='';self.settings.save()
        except Exception:pass
        self._rebuild_player_service();self.refresh_codmunity_account_status();self._clear_codmunity_identity()
        if hasattr(self,'codmunity_password_edit'):self.codmunity_password_edit.clear()
        self.log('Signed out of CODMunity in this tracker.')
    def _stored_codmunity_identity(self):
        email=self.settings.codmunity_email()
        username=str(self.settings.values.get('codmunity_username') or '').strip()
        if not username:return None
        if str(self.settings.values.get('codmunity_link_email') or '').casefold()!=email.casefold():return None
        def _num(key):
            try:value=int(self.settings.values.get(key) or 0)
            except (TypeError,ValueError):value=0
            return value if value>=0 else 0
        return {'username':username,'uid':str(self.settings.values.get('codmunity_uid') or ''),
                'display_name':str(self.settings.values.get('codmunity_display_name') or '') or None,'email':email,
                'avatar':str(self.settings.values.get('codmunity_avatar') or ''),
                'bio':str(self.settings.values.get('codmunity_bio') or ''),
                'followers':_num('codmunity_followers'),'loadouts':_num('codmunity_loadouts'),'likes':_num('codmunity_likes')}
    def _start_codmunity_identity_lookup(self):
        if getattr(self,'tab_prototype',False):return
        if os.environ.get('PYTEST_CURRENT_TEST') is not None:return
        if not hasattr(self,'codmunity_profile_status'):return
        if not self.settings.codmunity_refresh_token():
            self._clear_codmunity_identity();return
        stored=self._stored_codmunity_identity()
        if stored:
            self._apply_codmunity_identity(stored)
            if stored.get('avatar'):
                return
        ordered=self._identity_candidates()
        if stored and stored.get('username'):
            ordered=list(dict.fromkeys([stored['username']]+ordered))
        if not ordered:
            self.codmunity_profile_status.setText('Signed in. Set CODMunity Name to link the profile automatically.');return
        self.codmunity_profile_status.setText('Loading profile…')
        email=self.settings.codmunity_email()
        def work():return self._fetch_codmunity_identity(ordered,email)
        self.async_task(work,self._on_codmunity_identity,self._on_codmunity_identity_failed)
    def _identity_candidates(self):
        try:snapshot=self.store.meta('codmunity_stats') or {}
        except Exception:snapshot={}
        candidates=[]
        if isinstance(snapshot,dict):
            for hit in ((snapshot.get('identity') or {}).get('hits') or []):
                if isinstance(hit,dict) and str(hit.get('username') or '').strip():candidates.append(str(hit['username']).strip())
        player=(self.player.text().strip() if hasattr(self,'player') else '') or str(self.settings.values.get('player') or '')
        if player.strip():candidates.append(player.strip())
        return list(dict.fromkeys(candidates))
    def _fetch_codmunity_identity(self,candidates,email):
        for username in candidates:
            try:banner=self.codmunity_client.profile_banner(username)
            except Exception:banner=None
            if banner:
                banner['email']=email
                return banner
            try:ident=self.codmunity_client.profile_identity(username)
            except Exception:continue
            return {'username':ident.username,'uid':ident.uid,'display_name':ident.display_name,'email':email,
                    'avatar':'','bio':'','followers':0,'loadouts':0,'likes':0}
        raise RuntimeError('No matching CODMunity profile found')
    def _on_codmunity_identity(self,ident):
        username=str(ident.get('username') or '').strip()
        if not username:return
        email=self.settings.codmunity_email()
        def _count(value):
            return value if isinstance(value,int) and not isinstance(value,bool) and value>=0 else 0
        self.settings.values.update(codmunity_username=username,codmunity_uid=str(ident.get('uid') or ''),
            codmunity_display_name=str(ident.get('display_name') or ''),codmunity_link_email=email,
            codmunity_avatar=str(ident.get('avatar') or ''),codmunity_bio=str(ident.get('bio') or ''),
            codmunity_followers=_count(ident.get('followers')),codmunity_loadouts=_count(ident.get('loadouts')),codmunity_likes=_count(ident.get('likes')))
        url=f'https://codmunity.gg/profile/{username}'
        self.settings.values.update(player=username,profile_url=url)
        try:
            if hasattr(self,'player'):self.player.setText(username)
            if hasattr(self,'profile'):self.profile.setText(url)
            self.settings.save()
        except Exception:pass
        self._apply_codmunity_identity({'username':username,'uid':str(ident.get('uid') or ''),
            'display_name':str(ident.get('display_name') or '') or None,'email':email,
            'avatar':str(ident.get('avatar') or ''),'bio':str(ident.get('bio') or ''),
            'followers':ident.get('followers') if isinstance(ident.get('followers'),int) else 0,
            'loadouts':ident.get('loadouts') if isinstance(ident.get('loadouts'),int) else 0,
            'likes':ident.get('likes') if isinstance(ident.get('likes'),int) else 0})
        self.log('CODMunity profile linked for '+username+'.')
    def _on_codmunity_identity_failed(self,message):
        if hasattr(self,'codmunity_profile_status'):self.codmunity_profile_status.setText('Signed in, but profile lookup failed: '+str(message or 'unknown error'))
        self.log('CODMunity profile lookup failed: '+str(message or 'unknown error'))
    def _clear_codmunity_identity(self):
        try:
            self.settings.values.update(codmunity_username='',codmunity_uid='',codmunity_display_name='',codmunity_link_email='',codmunity_avatar='',codmunity_bio='',codmunity_followers=0,codmunity_loadouts=0,codmunity_likes=0)
            self.settings.save()
        except Exception:pass
        for attr,empty in (('codmunity_profile_name','—'),('codmunity_profile_sub',''),('codmunity_profile_stats','')):
            try:
                if hasattr(self,attr):getattr(self,attr).setText(empty)
            except (AttributeError,RuntimeError):pass
        for attr,empty in (('dash_profile_name','—'),('dash_profile_handle',''),('dash_profile_bio','')):
            try:
                if hasattr(self,attr):getattr(self,attr).setText(empty)
            except (AttributeError,RuntimeError):pass
        for slot in getattr(self,'dash_meters',{}).values():
            if not isinstance(slot,dict):continue
            for widget,key in ((slot.get('value'),'—'),(slot.get('sub'),'—'),(slot.get('cap'),'')):
                try:widget.setText(key)
                except (AttributeError,RuntimeError):pass
            try:slot['bar'].set_value(0)
            except (AttributeError,RuntimeError):pass
            if slot.get('ring') is not None:
                try:slot['ring'].set_value(0,'')
                except (AttributeError,RuntimeError):pass
        try:
            if hasattr(self,'dash_profile_avatar'):self.dash_profile_avatar.setPixmap(QPixmap());self.dash_profile_avatar.setText('◉')
        except (AttributeError,RuntimeError):pass
        for tile in getattr(self,'dash_profile_tiles',{}).values():
            if not isinstance(tile,dict):continue
            try:tile['value'].setText('\u2014')
            except (AttributeError,RuntimeError):pass
            try:
                if tile.get('badge') is not None:tile['badge'].setText('');tile['badge'].setVisible(False)
            except (AttributeError,RuntimeError):pass
        try:self._profile_avatar_url=''
        except (AttributeError,RuntimeError):pass
        try:
            if hasattr(self,'codmunity_profile_status'):self.codmunity_profile_status.setText('Sign in to load the CODMunity profile.')
        except (AttributeError,RuntimeError):pass
    def _apply_codmunity_identity(self,ident):
        username=str(ident.get('username') or '')
        display=str(ident.get('display_name') or '')
        uid=str(ident.get('uid') or '')
        url=f'https://codmunity.gg/profile/{username}' if username else ''
        try:
            if hasattr(self,'codmunity_profile_status'):self.codmunity_profile_status.setText('Profile linked'+(' for '+str(ident.get('email') or '') if ident.get('email') else '')+'.')
            if hasattr(self,'codmunity_profile_name'):self.codmunity_profile_name.setText(username or '—')
            if hasattr(self,'codmunity_profile_sub'):self.codmunity_profile_sub.setText(' · '.join(p for p in [display,'UID '+uid if uid else '',url] if p))
            if hasattr(self,'codmunity_profile_stats'):self.codmunity_profile_stats.setText(self._codmunity_identity_stats(username))
            if hasattr(self,'dash_profile_name'):self.dash_profile_name.setText(display or username or '—')
            if hasattr(self,'dash_profile_handle'):self.dash_profile_handle.setText('@'+username if username else '')
            if hasattr(self,'dash_profile_bio'):self.dash_profile_bio.setText(str(ident.get('bio') or ''))
            self._refresh_dashboard_stats()
            self._fetch_profile_avatar(str(ident.get('avatar') or ''))
        except (AttributeError,RuntimeError):pass
    def _fetch_profile_avatar(self,url):
        if not hasattr(self,'dash_profile_avatar'):return
        try:
            if not url or url in self._profile_avatar_failed:return
            if getattr(self,'_profile_avatar_url','')==url:
                current=self.dash_profile_avatar.pixmap()
                if current is not None and not current.isNull():return
        except (AttributeError,RuntimeError):return
        self._profile_avatar_url=url
        def work():
            import hashlib as _hashlib
            from urllib.request import Request as _Request,urlopen as _urlopen
            folder=E.DATA/'avatars';folder.mkdir(parents=True,exist_ok=True)
            path=folder/(_hashlib.sha256(url.encode()).hexdigest()[:24]+'.img')
            if not path.is_file():
                headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) VintendoCODTracker','Referer':'https://codmunity.gg/'}
                with _urlopen(_Request(url,headers=headers),timeout=20) as response:body=response.read(2*1024*1024)
                if not body:raise ValueError('empty avatar')
                path.write_bytes(body)
            return str(path)
        def done(path):
            try:label=self.dash_profile_avatar
            except (AttributeError,RuntimeError):return
            pixmap=QPixmap(path)
            if pixmap.isNull():
                try:self._profile_avatar_failed.add(url)
                except (AttributeError,RuntimeError):pass
                return
            size=96
            framed=QPixmap(size,size);framed.fill(Qt.transparent)
            painter=QPainter(framed);painter.setRenderHint(QPainter.Antialiasing)
            clip=QPainterPath();clip.addEllipse(3,3,size-6,size-6);painter.setClipPath(clip)
            painter.drawPixmap(0,0,size,size,pixmap.scaled(size,size,Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation))
            painter.setClipping(False);painter.setPen(QPen(QColor('#ed2938'),3));painter.drawEllipse(2,2,size-4,size-4)
            painter.end()
            try:label.setPixmap(framed);label.setText('')
            except (AttributeError,RuntimeError):pass
        self.async_task(work,done,lambda _message:getattr(self,'_profile_avatar_failed',set()).add(url))
    def _codmunity_identity_stats(self,username):
        try:snapshot=self.store.meta('codmunity_stats') or {}
        except Exception:return ''
        if not isinstance(snapshot,dict):return ''
        lines=[]
        for hit in ((snapshot.get('identity') or {}).get('hits') or []):
            if isinstance(hit,dict) and str(hit.get('username') or '').casefold()==username.casefold():
                lines.append(f"{hit.get('leaderboard')}: rank #{hit.get('rank')}")
        for game,mode,label in (('mw4','multiplayer','MW4 Multiplayer'),('warzone','resurgence','Warzone Resurgence')):
            try:stats=codmunity_camo.lifetime_stats(snapshot,game,mode)
            except Exception:stats={}
            if not stats:continue
            kd=codmunity_camo.lifetime_kd(stats)
            games=stats.get('games_played',stats.get('games'))
            lines.append(f"{label}: {codmunity_camo.format_count(games)} games, {codmunity_camo.format_count(stats.get('wins'))} wins"+(f", {kd:.2f} K/D" if kd is not None else ""))
        return '\n'.join(lines) if lines else 'No lifetime stats cached yet.'
    def refresh_codmunity_player_stats(self):
        player=self._codmunity_player_record()
        if player is None:
            self.codmunity_status.setText('Save this roster entry before checking live player stats.');return
        uid=str(player.get('codmunity_uid') or '').strip()
        if not uid:
            self.codmunity_status.setText('Resolve the public profile first to save its UID.');return
        if not self.codmunity_player_service.configured:
            self.codmunity_status.setText('Sign in with your CODMunity account to load live player stats.')
            return
        pid=str(player['id']);self.codmunity_status.setText('Checking configured provider.')
        # The default service has no endpoint or credentials. This only calls a
        # fetcher explicitly injected after an approved access method exists.
        self.async_task(lambda:self.codmunity_player_service.retrieve(uid),
                        lambda payload,pid=pid:self.on_codmunity_player_stats(pid,payload),
                        lambda message,pid=pid:self.on_codmunity_failure(pid,'player_stats',message))
    def on_codmunity_player_stats(self,pid,payload):
        if str(self.current_player or '')!=str(pid):return
        updated_at=datetime.now(timezone.utc).isoformat(timespec='seconds')
        self._save_codmunity_fields(pid,{'codmunity_player_stats':payload,
            'codmunity_player_stats_updated_at':updated_at,'codmunity_player_stats_source':'configured_provider'})
        self.refresh_codmunity_panel();self.refresh_roster();self.refresh_roster_rank_card();self.refresh_player_intel()
        self.codmunity_status.setText('Player statistics connected · configured provider response saved.')
    def on_codmunity_distribution(self,pid,key,distribution):
        finished=self._advance_refresh_queue()
        updated_at=datetime.now(timezone.utc).isoformat(timespec='seconds')
        record={'game':distribution.game,'mode':distribution.mode,'scope':distribution.scope,
            'kd':list(distribution.kd),'sampled':distribution.sampled,'users_total':distribution.users_total,
            'updated_at':updated_at,'source':'public_global_endpoint'}
        for attr,record_key in (('wl','wl'),('win_rate','winRate')):
            extra=getattr(distribution,attr,None)
            if extra:record[record_key]=list(extra)
        for extra_key,values in (getattr(distribution,'extra',None) or ()):
            if isinstance(extra_key,str) and isinstance(values,(list,tuple)) and len(values)==12:
                try:record[extra_key]=[float(v) for v in values]
                except (TypeError,ValueError):continue
        cached=dict(self.store.meta('codmunity_public_distributions',{}) or {});cached[key]=record
        self.store.set_meta('codmunity_public_distributions',cached)
        self._codmunity_live_key=(pid,key)
        self.refresh_codmunity_panel();self.refresh_roster();self.refresh_roster_rank_card();self.refresh_player_intel()
        self.codmunity_status.setText('Community distribution verified from the public endpoint.')
        if finished:
            try:ok=self._refresh_total-self._refresh_failed
            except Exception:ok=0
            self.codmunity_status.setText(f'Verified community data refreshed: {ok}/{self._refresh_total}.')
    def on_codmunity_failure(self,pid,kind,message,key=None):
        finished=self._advance_refresh_queue() if kind=='distribution' else False
        if kind=='distribution' and (finished or getattr(self,'_refresh_queue',None)):self._refresh_failed=getattr(self,'_refresh_failed',0)+1
        if str(self.current_player or '')!=str(pid):return
        self.refresh_codmunity_panel();self.refresh_roster_rank_card();self.refresh_player_intel()
        player=self._codmunity_player_record(pid) or {}
        cached=(self.store.meta('codmunity_public_distributions',{}) or {}).get(key) if key else None
        if kind=='distribution' and cached:
            self.codmunity_status.setText('Live refresh failed; showing saved community cache.')
        elif kind=='player_stats' and player.get('codmunity_player_stats'):
            self.codmunity_status.setText('Refresh failed; showing previously saved player data.')
        elif kind=='profile':
            self.codmunity_status.setText('Public profile metadata is unavailable.')
        else:
            self.codmunity_status.setText('Community distribution is unavailable.')
        if finished:
            try:ok=self._refresh_total-self._refresh_failed
            except Exception:ok=0
            self.codmunity_status.setText(f'Verified community data refreshed: {ok}/{self._refresh_total} (some combos failed; see log).')
    def refresh_codmunity_panel(self,*_):
        if not hasattr(self,'codmunity_identity_label'):return
        player=self._codmunity_player_record()
        game=self.codmunity_game.currentData() if hasattr(self,'codmunity_game') else 'warzone'
        mode=self.codmunity_mode.currentData() if hasattr(self,'codmunity_mode') else 'resurgence'
        scope=self.codmunity_scope.currentData() if hasattr(self,'codmunity_scope') else 'lifetime'
        self.codmunity_game_mode_value.setText(str(game or '—')+' / '+str(mode or '—'))
        if player is None:
            self.codmunity_identity_label.setText('Username —  ·  Display name —  ·  UID —')
            self.codmunity_profile_label.setText('Profile link —')
            for label in (self.codmunity_kd_value,self.codmunity_rank_value,self.codmunity_percentile_value,self.codmunity_progress_value,self.codmunity_next_kd_value,self.codmunity_median_value,self.codmunity_updated_value):label.setText('—')
            self.codmunity_distribution_label.setText('No community distribution loaded.')
            self.codmunity_status.setText('Select a roster player to link CODMunity data.')
            return
        username=str(player.get('codmunity_username') or self.codmunity_username.text().strip() or '—')
        display=str(player.get('codmunity_display_name') or '—')
        uid=str(player.get('codmunity_uid') or '—')
        profile=str(player.get('profile_url') or '—')
        self.codmunity_identity_label.setText(f'Username {username}  ·  Display name {display}  ·  UID {uid}')
        self.codmunity_profile_label.setText('Profile link '+profile)
        names=[username,str(player.get('name') or '')]+list(player.get('aliases') or [])
        if display!='—':names.append(display)
        sample=codmunity_player_stats.tracked_match_kd(self.store.history(),names,str(game or ''),str(mode or ''))
        stored_stats=player.get('codmunity_player_stats')
        player_kd=codmunity_player_stats.player_kd_for_mode(stored_stats,str(game or ''),str(mode or ''))
        if player_kd.kd is not None:
            kd_value=player_kd.kd
            kd_source=player_kd.source
            self.codmunity_kd_value.setText(f'{kd_value:.2f} · {kd_source}')
        elif sample.kd is None:
            kd_value=None
            self.codmunity_kd_value.setText('Unavailable (no verified local match sample)')
        else:
            kd_value=sample.kd
            kd_source=f'local tracked matches ({sample.matches}); not lifetime'
            self.codmunity_kd_value.setText(f'{kd_value:.2f} · {kd_source}')
        cache_key='|'.join((str(game),str(mode),str(scope)))
        saved=(self.store.meta('codmunity_public_distributions',{}) or {}).get(cache_key)
        if not isinstance(saved,dict):
            saved=(player.get('codmunity_distributions') or {}).get(cache_key)
        distribution=None
        if isinstance(saved,dict):
            try:
                distribution=codmunity_player_stats.CommunityDistribution.from_payload({'data':{
                    'game':saved.get('game'),'mode':saved.get('mode'),'scope':saved.get('scope'),
                    'sampled':saved.get('sampled'),'usersTotal':saved.get('users_total'),
                    'dist':{'kd':saved.get('kd')}}})
            except (TypeError,ValueError):
                distribution=None
        self.codmunity_rank_value.setText('—');self.codmunity_percentile_value.setText('—')
        self.codmunity_progress_value.setText('—');self.codmunity_next_kd_value.setText('—');self.codmunity_median_value.setText('—')
        if distribution is None:
            self.codmunity_distribution_label.setText('No saved distribution for this game/mode/scope. Use Refresh community data.')
            self.codmunity_updated_value.setText('—')
        else:
            percentile_values='  ·  '.join(f'P{p} {value:g}' for p,value in zip(codmunity_player_stats.PERCENTILE_ANCHORS,distribution.kd))
            self.codmunity_distribution_label.setText(
                f"{'Live public endpoint' if self._codmunity_live_key==(str(player['id']),cache_key) else 'Saved community cache'} · "
                f'{distribution.sampled if distribution.sampled is not None else "—"} sampled / '
                f'{distribution.users_total if distribution.users_total is not None else "—"} users · {percentile_values}')
            self.codmunity_median_value.setText(f'{distribution.kd[codmunity_player_stats.PERCENTILE_ANCHORS.index(50)]:.2f}')
            self.codmunity_updated_value.setText(self._codmunity_timestamp(saved.get('updated_at')))
            if kd_value is not None:
                rank=codmunity_player_stats.distribution_rank(kd_value,distribution)
                if rank is None:
                    self.codmunity_rank_value.setText('—');self.codmunity_percentile_value.setText('—')
                    self.codmunity_progress_value.setText('Sample too small — refresh community data');self.codmunity_next_kd_value.setText('—')
                else:
                    self.codmunity_rank_value.setText(f'{rank.tier} · {kd_source}')
                    self.codmunity_percentile_value.setText(f'{rank.percentile:.1f}%')
                    self.codmunity_progress_value.setText(f'{rank.progress_percent:.1f}% toward {rank.next_tier}' if rank.next_tier else 'Top tier')
                    self.codmunity_next_kd_value.setText(f'{rank.next_tier_kd:.2f}' if rank.next_tier_kd is not None else '—')
        if player_kd.updated_at and player_kd.kd is not None:
            self.codmunity_updated_value.setText(self._codmunity_timestamp(player_kd.updated_at))
        if isinstance(stored_stats,dict):
            self.codmunity_status.setText('Saved player statistics cache · '+self._codmunity_timestamp(player.get('codmunity_player_stats_updated_at')))
        else:
            parts=[]
            if str(player.get('codmunity_uid') or '').strip():parts.append('Profile linked')
            if distribution is not None:parts.append('Community data ready')
            parts.append('Live player stats need an approved provider')
            if len(parts)==1:parts.insert(0,'Resolve the profile, then load community data')
            self.codmunity_status.setText(' · '.join(parts))
        if not self.codmunity_status.text():
            self.codmunity_status.setText('Resolve the profile, then load community data')
    @staticmethod
    def default_avatar(size):
        pix=QPixmap(size,size);pix.fill(QColor('#181c24'))
        painter=QPainter(pix);painter.setPen(QColor('#5b6372'));font=painter.font();font.setPixelSize(int(size*.5));font.setBold(True);painter.setFont(font);painter.drawText(pix.rect(),Qt.AlignCenter,'?');painter.end();return pix
    @staticmethod
    def rounded_thumb(path,size):
        pix=QPixmap(path) if path else QPixmap()
        if pix.isNull():pix=Window.default_avatar(size)
        scaled=pix.scaled(size,size,Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation)
        cropped=scaled.copy(max(0,(scaled.width()-size)//2),max(0,(scaled.height()-size)//2),size,size)
        out=QPixmap(size,size);out.fill(Qt.transparent)
        painter=QPainter(out);painter.setRenderHint(QPainter.Antialiasing);clip=QPainterPath();clip.addRoundedRect(0,0,size,size,10,10);painter.setClipPath(clip);painter.drawPixmap(0,0,cropped);painter.end()
        return out
    def roster_card_widget(self,player,rank=None):
        card=QWidget()
        layout=QHBoxLayout(card);layout.setContentsMargins(14,12,14,12);layout.setSpacing(14)
        thumb=QLabel();thumb.setFixedSize(56,56)
        photo=str(player.get('photo') or '')
        thumb.setPixmap(self.rounded_thumb(photo,56) if photo and Path(photo).is_file() else self.default_avatar(56))
        layout.addWidget(thumb)
        text=QVBoxLayout();text.setSpacing(4)
        display=str(player.get('display_name') or player.get('name',''))
        name_label=QLabel(display);name_label.setObjectName('rosterCardName')
        sub=QLabel('COD: '+str(player.get('name','')));sub.setObjectName('rosterCardSub')
        text.addWidget(name_label);text.addWidget(sub)
        enabled=bool(player.get('enabled',True))
        status=QLabel(('\u25cf Enabled' if enabled else '\u25cb Disabled'));status.setObjectName('rosterEnabled');status.setStyleSheet('font-size:10px;font-weight:700;color:#4ade80;background:transparent;' if enabled else 'font-size:10px;font-weight:700;color:#6b7280;background:transparent;')
        text.addWidget(status)
        layout.addLayout(text,1)
        info=rank or {}
        ladder=info.get('rank')
        rank_col=QVBoxLayout();rank_col.setSpacing(4)
        if ladder is not None and self._is_local_roster_player(player):
            color=self.LADDER_TIER_COLORS.get(ladder['tier'],'#e5e9ef')
            badge=QLabel('●  '+ladder['tier']);badge.setObjectName('rosterRankTier');badge.setStyleSheet('font-size:13px;font-weight:900;color:'+color+';')
            kd_line=QLabel(f"{info['kd']:.2f} K/D  ·  {ladder['percentile']:.1f}%");kd_line.setObjectName('rosterRankKd');kd_line.setStyleSheet('font-size:11px;font-weight:700;color:#c4ccd8;')
            rank_col.addWidget(badge);rank_col.addWidget(kd_line)
            bottom=QHBoxLayout();bottom.setSpacing(6)
            bar=QProgressBar();bar.setRange(0,100);bar.setValue(int(round(ladder['progress'])));bar.setTextVisible(False);bar.setFixedHeight(6)
            bar.setStyleSheet('QProgressBar{background:#1a1f2a;border:none;border-radius:3px;}QProgressBar::chunk{background:'+color+';border-radius:3px;}')
            bottom.addWidget(bar,1)
            if ladder['next_tier'] is not None:next_label=QLabel(f"{ladder['next_tier']} @ {ladder['next_kd']:.2f}")
            else:next_label=QLabel('MAX RANK')
            next_label.setObjectName('rosterRankNext');next_label.setStyleSheet('font-size:10px;font-weight:700;color:#9da8b7;')
            bottom.addWidget(next_label);rank_col.addLayout(bottom)
        elif self._is_local_roster_player(player):
            if info.get('kd') is not None:na_text=f"{info['kd']:.2f} K/D · rank needs community data"
            else:na_text='RANK UNAVAILABLE'
            na=QLabel(na_text);na.setObjectName('rosterRankTier');na.setStyleSheet('font-size:11px;font-weight:700;color:#6b7280;')
            rank_col.addWidget(na)
        else:
            record=info.get('record') or {};played=int(record.get('matches') or 0);won=int(record.get('wins') or 0);best=record.get('best')
            title=QLabel('TRACKED RECORD');title.setObjectName('rosterRankTier');title.setStyleSheet('font-size:11px;font-weight:900;color:#9da8b7;')
            rank_col.addWidget(title)
            if played>0:
                line=QLabel(f"{won}W/{played-won}L · {played} tracked")
                bottom_text=f"Best {best:g} kills" if isinstance(best,(int,float)) else 'No kills recorded'
            else:
                line=QLabel('No tracked matches yet')
                bottom_text='Play, then review a scoreboard.'
            line.setObjectName('rosterRankKd');line.setStyleSheet('font-size:11px;font-weight:700;color:#c4ccd8;')
            rank_col.addWidget(line)
            bottom=QLabel(bottom_text);bottom.setObjectName('rosterRankNext');bottom.setStyleSheet('font-size:10px;font-weight:700;color:#9da8b7;')
            rank_col.addWidget(bottom)
        rank_wrap=QWidget();rank_wrap.setLayout(rank_col);rank_wrap.setMinimumWidth(168);layout.addWidget(rank_wrap,0)
        status=QLabel(('● Enabled' if enabled else '○ Disabled'));status.setStyleSheet('font-size:11px;font-weight:700;color:#4ade80;background:transparent;' if enabled else 'font-size:11px;font-weight:700;color:#6b7280;background:transparent;')
        return card
    def build_studio(self):
        l=self.page('AI Studio','CODMunity + verified scoreboard → prepared Meta AI inputs → Meta AI artwork with in-scene match results → Discord.')
        steps=QFrame();steps.setObjectName('codAiSteps');steps_layout=QHBoxLayout(steps);steps_layout.setContentsMargins(14,8,14,8);steps_layout.setSpacing(10)
        for index,(icon,title,desc) in enumerate((('☰','INPUT','Match Details'),('✦','GENERATE','Create AI Artwork'),('✓','REVIEW','Approve & Post'),('◉','DISCORD','Auto Post Results'))):
            num=QLabel(str(index+1));num.setObjectName('stepNum');num.setAlignment(Qt.AlignCenter);steps_layout.addWidget(num)
            step_icon=QLabel(icon);step_icon.setObjectName('stepIcon');step_icon.setAlignment(Qt.AlignCenter);steps_layout.addWidget(step_icon)
            copy=QVBoxLayout();copy.setSpacing(1)
            step_title=QLabel(title);step_title.setObjectName('stepTitle');copy.addWidget(step_title)
            step_desc=QLabel(desc);step_desc.setObjectName('stepDesc');copy.addWidget(step_desc)
            steps_layout.addLayout(copy)
            if index<3:
                arrow=QLabel(chr(187));arrow.setObjectName('stepArrow');steps_layout.addWidget(arrow);steps_layout.addStretch(1)
        l.addWidget(steps)
        self.studio_match=QLabel('Select a match or save a reviewed OCR scoreboard.');self.studio_match.setWordWrap(True);l.addWidget(self.studio_match)
        top=QHBoxLayout();top.setSpacing(10)
        input_panel=QFrame();input_panel.setObjectName('dashboardPanel');input_layout=QVBoxLayout(input_panel);input_layout.setContentsMargins(12,10,12,10);input_layout.setSpacing(8)
        input_title=QLabel('MATCH INPUT');input_title.setObjectName('codPanelTitle');input_layout.addWidget(input_title)
        self.prompt=QPlainTextEdit();self.prompt.setReadOnly(True);self.prompt.setPlaceholderText('A creative 16:9 prompt is prepared automatically from the verified scoreboard.');self.prompt.setMinimumHeight(110);input_layout.addWidget(self.prompt,1)
        top.addWidget(input_panel,5)
        opts_panel=QFrame();opts_panel.setObjectName('dashboardPanel');opts_layout=QVBoxLayout(opts_panel);opts_layout.setContentsMargins(12,10,12,10);opts_layout.setSpacing(8)
        opts_title=QLabel('MATCH OPTIONS');opts_title.setObjectName('codPanelTitle');opts_layout.addWidget(opts_title)
        self.opt_game_mode=QLabel('-');self.opt_result=QLabel('-');self.opt_placement=QLabel('-');self.opt_style=QLabel('Wild / Cinematic (Random)')
        for label_text,value_label in (('Game Mode',self.opt_game_mode),('Result',self.opt_result),('Placement',self.opt_placement),('Style / Vibe',self.opt_style)):
            option_row=QHBoxLayout();option_row.setSpacing(8)
            option_label=QLabel(label_text);option_label.setStyleSheet('color:#99a8bc;font-weight:700;');option_row.addWidget(option_label,1)
            value_label.setStyleSheet('color:#f2f4f7;font-weight:700;');value_label.setWordWrap(True);option_row.addWidget(value_label,1)
            opts_layout.addLayout(option_row)
        opts_layout.addStretch(1)
        top.addWidget(opts_panel,3)
        l.addLayout(top)
        self.generate_poster_button=self.button('Generate AI End Game Picture',self.prepare_artwork,True);self.generate_poster_button.setEnabled(False);self.generate_poster_button.setMinimumHeight(44);l.addWidget(self.generate_poster_button)
        bottom=QHBoxLayout();bottom.setSpacing(10)
        players_panel=QFrame();players_panel.setObjectName('dashboardPanel');players_layout=QVBoxLayout(players_panel);players_layout.setContentsMargins(12,10,12,10);players_layout.setSpacing(8)
        self.studio_heads_title=QLabel('PLAYERS IDENTIFIED FROM SCREENSHOTS');self.studio_heads_title.setObjectName('codPanelTitle');players_layout.addWidget(self.studio_heads_title)
        self.studio_heads=QGridLayout();self.studio_heads.setSpacing(12);players_layout.addLayout(self.studio_heads,1)
        bottom.addWidget(players_panel,3)
        preview_panel=QFrame();preview_panel.setObjectName('dashboardPanel');preview_layout=QVBoxLayout(preview_panel);preview_layout.setContentsMargins(12,10,12,10);preview_layout.setSpacing(8)
        preview_title=QLabel('AI PREVIEW');preview_title.setObjectName('codPanelTitle');preview_layout.addWidget(preview_title)
        self.art_preview=QLabel('Finished artwork preview');self.art_preview.setAlignment(Qt.AlignCenter);self.art_preview.setMinimumHeight(180);preview_layout.addWidget(self.art_preview,1)
        bottom.addWidget(preview_panel,5)
        squad_panel=QFrame();squad_panel.setObjectName('dashboardPanel');squad_layout=QVBoxLayout(squad_panel);squad_layout.setContentsMargins(12,10,12,10);squad_layout.setSpacing(8)
        squad_title=QLabel('SQUAD TOTALS');squad_title.setObjectName('codPanelTitle');squad_layout.addWidget(squad_title)
        self.photo_assignments=QLabel('Consented reference photos are matched automatically when available.');self.photo_assignments.setWordWrap(True);self.photo_assignments.setStyleSheet('color:#c4ccd8;');squad_layout.addWidget(self.photo_assignments,1)
        bottom.addWidget(squad_panel,3)
        l.addLayout(bottom,1)
        self.approve_post_button=self.button('APPROVE & POST',self.post_active,True)
        self.row(l,self.button('Cancel',self.cancel_browser),self.approve_post_button,self.button('Retry / review selected job',self.retry_job));self.job_status=QLabel('No active job');self.job_status.setWordWrap(True);l.addWidget(self.job_status)
    def build_cod_ai(self):
        l=self.page('ScreenShots and Videos','Capture a Call of Duty moment and send screenshots to Meta AI for cinematic videos.');l.setSpacing(9);l.setContentsMargins(24,14,24,14)
        steps=QFrame();steps.setObjectName('codAiSteps');steps_layout=QHBoxLayout(steps);steps_layout.setContentsMargins(14,8,14,8);steps_layout.setSpacing(10)
        for index,(icon,title,desc) in enumerate((('⌖','CAPTURE','Grab a gameplay screenshot.'),('▶','CREATE VIDEO','Send to Meta AI to make a cinematic.'),('↗','SHARE','Post the finished video to Discord.'))):
            num=QLabel(str(index+1));num.setObjectName('stepNum');num.setAlignment(Qt.AlignCenter);steps_layout.addWidget(num)
            step_icon=QLabel(icon);step_icon.setObjectName('stepIcon');step_icon.setAlignment(Qt.AlignCenter);steps_layout.addWidget(step_icon)
            copy=QVBoxLayout();copy.setSpacing(1)
            step_title=QLabel(title);step_title.setObjectName('stepTitle');copy.addWidget(step_title)
            step_desc=QLabel(desc);step_desc.setObjectName('stepDesc');copy.addWidget(step_desc)
            steps_layout.addLayout(copy)
            if index<2:
                arrow=QLabel(chr(187));arrow.setObjectName('stepArrow');steps_layout.addWidget(arrow);steps_layout.addStretch(1)
        hotkey_bar=QHBoxLayout();hotkey_bar.setContentsMargins(0,0,0,0);hotkey_bar.setSpacing(10);hotkey_bar.addStretch(1)
        self.cod_ai_hotkey_status=QLabel('Hotkey: Down');self.cod_ai_hotkey_status.setObjectName('hotkeyPill');self.cod_ai_hotkey_status.setAlignment(Qt.AlignCenter);self.cod_ai_hotkey_status.setCursor(Qt.PointingHandCursor);self.cod_ai_hotkey_status.setToolTip('Click to set the screenshot hotkey');self.cod_ai_hotkey_status.mousePressEvent=lambda _event:self.open_hotkey_dialog('screenshot');hotkey_bar.addWidget(self.cod_ai_hotkey_status);self.cod_ai_hotkey_status.hide()
        hotkey_wrap=QWidget();hotkey_wrap.setLayout(hotkey_bar);l.addWidget(hotkey_wrap)
        l.addWidget(steps)
        actions=QHBoxLayout();actions.setSpacing(10)
        self.open_screenshots_button=self.button('Open Screenshots Folder',self.open_screenshots_folder)
        actions.addWidget(self.button('Capture Game Screenshot',self.capture_cod_ai_screenshot,True),1)
        self.queue_cinematic_button=self.button('Send Selected Material to Meta Ai to Make A Video',self.queue_selected_cinematic);self.queue_cinematic_button.setObjectName('codBlue');actions.addWidget(self.queue_cinematic_button,1)
        l.addLayout(actions)
        self.cod_ai_capture_status=QLabel('')
        self.cod_ai_capture_status.setWordWrap(True);self.cod_ai_capture_status.setStyleSheet('color:#99a8bc');l.addWidget(self.cod_ai_capture_status)
        media_panel=QFrame();media_panel.setObjectName('dashboardPanel');media_layout=QVBoxLayout(media_panel);media_layout.setContentsMargins(12,12,12,12);media_layout.setSpacing(8)
        media_head=QHBoxLayout();media_title=QLabel('MEDIA LIBRARY');media_title.setObjectName('codPanelTitle');media_head.addWidget(media_title);media_head.addStretch(1)
        media_head.addWidget(self.button('Open Cinematics Folder',self.open_cinematics_folder));media_layout.addLayout(media_head)
        self.media_tabs=QTabWidget();self.media_tabs.setObjectName('mediaTabs');self.media_tabs.currentChanged.connect(lambda _index:(self.update_screenshot_grid(),self.update_cinematic_grid()))

        screenshots_tab=QWidget();screenshots_layout=QVBoxLayout(screenshots_tab);screenshots_layout.setContentsMargins(6,6,6,6);screenshots_layout.setSpacing(8)
        self.screenshot_tiles={}
        self.cod_ai_screenshots=QListWidget();self.cod_ai_screenshots.setObjectName('cinematicGallery');self.cod_ai_screenshots.setViewMode(QListWidget.IconMode);self.cod_ai_screenshots.setFlow(QListWidget.LeftToRight);self.cod_ai_screenshots.setMovement(QListWidget.Static);self.cod_ai_screenshots.setResizeMode(QListWidget.Adjust);self.cod_ai_screenshots.setWrapping(True);self.cod_ai_screenshots.setSpacing(6);self.cod_ai_screenshots.setMinimumHeight(220);self.cod_ai_screenshots.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);self.cod_ai_screenshots.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded);self.cod_ai_screenshots.currentItemChanged.connect(self.preview_cod_ai_screenshot);screenshots_layout.addWidget(self.cod_ai_screenshots,1)
        self.cod_ai_empty=QLabel('No screenshots yet\nCapture a screenshot or open your folder.');self.cod_ai_empty.setObjectName('codEmpty');self.cod_ai_empty.setAlignment(Qt.AlignCenter);self.cod_ai_empty.setWordWrap(True);self.cod_ai_empty.hide();screenshots_layout.addWidget(self.cod_ai_empty,1)
        self.media_tabs.addTab(screenshots_tab,'Screenshots')

        videos_tab=QWidget();videos_layout=QVBoxLayout(videos_tab);videos_layout.setContentsMargins(6,6,6,6);videos_layout.setSpacing(8)
        self.cinematic_tiles={};self.cinematic_duration_cache={}
        self.cinematic_results=QListWidget();self.cinematic_results.setObjectName('cinematicGallery');self.cinematic_results.setViewMode(QListWidget.IconMode);self.cinematic_results.setFlow(QListWidget.LeftToRight);self.cinematic_results.setMovement(QListWidget.Static);self.cinematic_results.setResizeMode(QListWidget.Adjust);self.cinematic_results.setWrapping(True);self.cinematic_results.setSpacing(6);self.cinematic_results.setMinimumHeight(220);self.cinematic_results.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);self.cinematic_results.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded);self.cinematic_results.currentItemChanged.connect(self.preview_cinematic_video);videos_layout.addWidget(self.cinematic_results,1)
        self.cinematic_empty=QLabel('No finished videos yet\nSend a screenshot to Meta AI to make a cinematic.');self.cinematic_empty.setObjectName('codEmpty');self.cinematic_empty.setAlignment(Qt.AlignCenter);self.cinematic_empty.setWordWrap(True);self.cinematic_empty.hide();videos_layout.addWidget(self.cinematic_empty,1)
        self.media_tabs.addTab(videos_tab,'Finished Videos');media_layout.addWidget(self.media_tabs,1)

        self.cinematic_status=QLabel('Choose an existing gameplay screenshot to test Meta AI Frame to Video. Nothing posts automatically.')
        self.cinematic_status.setWordWrap(True);self.cinematic_status.setStyleSheet('color:#99a8bc')
        main_split=QSplitter(Qt.Horizontal);main_split.setHandleWidth(10);main_split.setChildrenCollapsible(False)
        main_split.addWidget(media_panel)
        preview_panel=QFrame();preview_panel.setObjectName('dashboardPanel');right_layout=QVBoxLayout(preview_panel);right_layout.setContentsMargins(12,12,12,12);right_layout.setSpacing(8)
        preview_title=QLabel('PREVIEW');preview_title.setObjectName('codPanelTitle');right_layout.addWidget(preview_title)
        preview_sub=QLabel('Select a screenshot or finished video to preview it here.');preview_sub.setStyleSheet('color:#99a8bc');right_layout.addWidget(preview_sub)
        self.preview_sub=preview_sub
        self.preview_image=QLabel('Select a screenshot or finished video to preview it here');self.preview_image.setAlignment(Qt.AlignCenter);self.preview_image.setWordWrap(True);self.preview_image.setMinimumHeight(180);self.preview_image.setStyleSheet('background:#050609;border:1px solid #343a46;border-radius:6px;color:#77818f;');right_layout.addWidget(self.preview_image,1)
        self.cinematic_video_widget=QVideoWidget();self.cinematic_fullscreen_filter=FullscreenExitFilter(lambda:bool(getattr(self,'cinematic_video_widget',None) and self.cinematic_video_widget.isFullScreen()),self.exit_cinematic_fullscreen,self.toggle_cinematic_fullscreen)
        app_instance=QApplication.instance()
        if app_instance is not None:app_instance.installEventFilter(self.cinematic_fullscreen_filter)
        self.cinematic_video_widget.setMinimumHeight(180);self.cinematic_video_widget.setStyleSheet('background:#050609;border:1px solid #343a46;border-radius:6px;');right_layout.addWidget(self.cinematic_video_widget,1);self.cinematic_video_widget.hide()
        self.cinematic_player=QMediaPlayer(self);self.cinematic_audio=QAudioOutput(self);self.cinematic_audio.setVolume(.8);self.cinematic_player.setAudioOutput(self.cinematic_audio);self.cinematic_player.setVideoOutput(self.cinematic_video_widget);self.cinematic_player.errorOccurred.connect(lambda error,message:self.cinematic_status.setText('Video preview error: '+str(message)[:200]) if error else None)
        self.cinematic_seek=QSlider(Qt.Horizontal);self.cinematic_seek.setRange(0,0)
        self.cinematic_time=QLabel('0:00 / 0:00');self.cinematic_time.setStyleSheet('color:#c4ccd8;font-weight:700;')
        self.cinematic_player.durationChanged.connect(lambda duration:(self.cinematic_seek.setRange(0,duration),self.cinematic_time.setText(format_cinematic_duration(self.cinematic_player.position()/1000)+' / '+format_cinematic_duration(duration/1000))))
        self.cinematic_player.positionChanged.connect(lambda position:(self.cinematic_seek.setValue(position),self.cinematic_time.setText(format_cinematic_duration(position/1000)+' / '+format_cinematic_duration(self.cinematic_player.duration()/1000))))
        self.cinematic_seek.sliderMoved.connect(self.cinematic_player.setPosition)
        self.video_controls=QWidget();controls_layout=QVBoxLayout(self.video_controls);controls_layout.setContentsMargins(0,0,0,0);controls_layout.setSpacing(8)
        playback=QHBoxLayout();self.cinematic_play_button=self.button('Play',self.toggle_cinematic_playback);self.cinematic_play_button.setObjectName('codPlay');self.cinematic_play_button.setEnabled(False);playback.addWidget(self.cinematic_play_button);playback.addWidget(self.button('Stop preview',self.cinematic_player.stop));playback.addWidget(self.cinematic_seek,1);playback.addWidget(self.cinematic_time);controls_layout.addLayout(playback)
        volume_row=QHBoxLayout();volume_label=QLabel('Volume');volume_label.setStyleSheet('color:#99a8bc;font-weight:700;');volume_row.addWidget(volume_label)
        self.cinematic_volume=QSlider(Qt.Horizontal);self.cinematic_volume.setRange(0,100);self.cinematic_volume.setValue(80);self.cinematic_volume.setMaximumWidth(120);self.cinematic_volume.valueChanged.connect(lambda value:self.cinematic_audio.setVolume(value/100));volume_row.addWidget(self.cinematic_volume);volume_row.addStretch(1)
        controls_layout.addLayout(volume_row)
        self.cinematic_fullscreen_button=self.button('Fullscreen',self.toggle_cinematic_fullscreen);self.cinematic_fullscreen_button.setToolTip('Fullscreen - press Esc or double-click the video to exit');self.cinematic_fullscreen_button.setEnabled(False)
        self.refresh_cinematic_button=self.button('Refresh',self.refresh_cinematic_results)
        preview_actions=QHBoxLayout();preview_actions.addStretch(1);preview_actions.addWidget(self.cinematic_fullscreen_button);preview_actions.addWidget(self.refresh_cinematic_button);controls_layout.addLayout(preview_actions)
        right_layout.addWidget(self.video_controls);self.video_controls.hide()
        main_split.addWidget(preview_panel);main_split.setStretchFactor(0,5);main_split.setStretchFactor(1,6);main_split.setSizes([740,860]);l.addWidget(main_split,1)
        action_bar=QHBoxLayout();action_bar.setSpacing(10);action_bar.addWidget(self.cinematic_status,1)
        self.test_cinematic_button=None
        self.post_cinematic_button=self.button('Post to Discord',self.post_cinematic_video);self.post_cinematic_button.setObjectName('codSecondary');self.post_cinematic_button.setEnabled(False);action_bar.insertWidget(0,self.open_screenshots_button);action_bar.addWidget(self.post_cinematic_button)
        l.addLayout(action_bar)
        talent_friends_row=QHBoxLayout();talent_friends_row.setSpacing(10)
        talent_panel=QFrame();talent_panel.setObjectName('dashboardPanel');talent_layout=QVBoxLayout(talent_panel);talent_layout.setContentsMargins(12,10,12,10);talent_layout.setSpacing(8)
        talent_head=QHBoxLayout();talent_head.setSpacing(6);talent_icon=QLabel('◉');talent_icon.setStyleSheet('color:#f97316;font-size:14px;font-weight:900;');talent_head.addWidget(talent_icon)
        talent_title=QLabel("PLAYER CAMEO'S");talent_title.setObjectName('codPanelTitle');talent_head.addWidget(talent_title);talent_head.addStretch(1);talent_layout.addLayout(talent_head)
        talent_body=QHBoxLayout();talent_body.setSpacing(10)
        self.talent_preview=QLabel();self.talent_preview.setFixedSize(60,60);self.talent_preview.setAlignment(Qt.AlignCenter);self.talent_preview.setStyleSheet('background:#080a0e;border:1px solid #292f39;border-radius:6px;color:#77818f;');talent_body.addWidget(self.talent_preview)
        talent_meta=QVBoxLayout();talent_meta.setSpacing(6)
        talent_name_row=QHBoxLayout();talent_name_row.setSpacing(6)
        self.talent_dot=QLabel();self.talent_dot.setObjectName('talentDot');self.talent_dot.hide();talent_name_row.addWidget(self.talent_dot)
        self.talent_info=QLabel('No photo set');self.talent_info.setStyleSheet('color:#c4ccd8;font-weight:700;');talent_name_row.addWidget(self.talent_info,1);talent_meta.addLayout(talent_name_row)
        talent_btns=QHBoxLayout();talent_btns.setSpacing(6)
        talent_btns.addWidget(self.button('Change Photo',self.set_cinematic_talent));talent_btns.addWidget(self.button('Remove Photo',self.remove_cinematic_talent));talent_btns.addStretch(1);talent_meta.addLayout(talent_btns)
        talent_body.addLayout(talent_meta,1);talent_layout.addLayout(talent_body)
        self.talent_cameo_strip=QHBoxLayout();self.talent_cameo_strip.setContentsMargins(0,0,0,0);self.talent_cameo_strip.setSpacing(8);talent_layout.addLayout(self.talent_cameo_strip)
        self.talent_cameo_label=QLabel('Featuring: just you');self.talent_cameo_label.setStyleSheet('font-size:11px;font-weight:700;color:#9da8b7;');self.talent_cameo_label.setWordWrap(True);talent_layout.addWidget(self.talent_cameo_label)
        talent_friends_row.addWidget(talent_panel,2)
        friends_panel=QFrame();friends_panel.setObjectName('dashboardPanel');friends_layout=QVBoxLayout(friends_panel);friends_layout.setContentsMargins(12,10,12,10);friends_layout.setSpacing(8)
        friends_head=QHBoxLayout();friends_head.setSpacing(6)
        friends_title=QLabel('FRIENDS IN THIS VIDEO');friends_title.setObjectName('codPanelTitle');friends_head.addWidget(friends_title)
        friends_opt=QLabel('(Optional)');friends_opt.setStyleSheet('font-size:11px;color:#9da8b7;');friends_head.addWidget(friends_opt);friends_head.addStretch(1)
        self.talent_friends_status=QLabel('');self.talent_friends_status.setStyleSheet('font-size:11px;font-weight:700;color:#9da8b7;');friends_head.addWidget(self.talent_friends_status);friends_layout.addLayout(friends_head)
        self.talent_friends_box=QGridLayout();self.talent_friends_box.setSpacing(8);self.talent_friend_tiles={};friends_scroll=QScrollArea();friends_scroll.setWidgetResizable(True);friends_scroll.setMaximumHeight(176);friends_host=QWidget();friends_host.setLayout(self.talent_friends_box);friends_scroll.setWidget(friends_host);friends_layout.addWidget(friends_scroll,1)
        talent_friends_row.addWidget(friends_panel,3);l.addLayout(talent_friends_row)
        self.refresh_talent_friends()
        self.refresh_talent_preview()
        QTimer.singleShot(0,self.update_screenshot_grid);QTimer.singleShot(0,self.update_cinematic_grid)
        self.refresh_cinematic_results()
    def build_tracker_browser(self):
        l=self.page('Tracker Browser','Meta AI automation runs in a small in-app browser panel while a poster or video job is active.')
        for row in range(self.nav.count()):
            if self.nav.item(row).data(Qt.UserRole)=='Tracker Browser':self.nav.item(row).setHidden(True);break
        self.browser_status=QLabel('Tracker browser: checking…');self.browser_status.setWordWrap(True);l.addWidget(self.browser_status)
        self.browser_queue=QLabel('');self.browser_queue.setWordWrap(True);self.browser_queue.setStyleSheet('color:#99a8bc');l.addWidget(self.browser_queue)
        self.browser_surface=QFrame();self.browser_surface.setObjectName('dashboardPanel');self.browser_surface.setMinimumSize(800,450);l.addWidget(self.browser_surface,1)
        self.auto_browser_panel=QFrame(self.sidebar);self.auto_browser_panel.setObjectName('dashboardPanel');self.auto_browser_panel.setFixedSize(1,1)
        mini_layout=QVBoxLayout(self.auto_browser_panel);mini_layout.setContentsMargins(7,7,7,7);mini_layout.setSpacing(5)
        mini_head=QHBoxLayout();self.auto_mini_title=QLabel('META AI  ·  RUNNING');self.auto_mini_title.setObjectName('codPanelTitle');mini_head.addWidget(self.auto_mini_title);mini_head.addStretch(1)
        self.auto_browser_hide_button=self.button('×',self.auto_browser_panel.hide);self.auto_browser_hide_button.setObjectName('codSecondary');self.auto_browser_hide_button.setFixedSize(26,24);self.auto_browser_hide_button.setToolTip('Hide the mini browser while the Meta AI job continues.');mini_head.addWidget(self.auto_browser_hide_button);mini_layout.addLayout(mini_head)
        self.auto_browser_surface=QFrame();self.auto_browser_surface.setObjectName('dashboardPanel');self.auto_browser_surface.setMinimumSize(1,1);mini_layout.addWidget(self.auto_browser_surface,1)
        sidebar_layout=self.sidebar.layout()
        sidebar_layout.insertWidget(sidebar_layout.indexOf(self.meta_login_button),self.auto_browser_panel)
        self.auto_browser_panel.hide()
        self.row(l,self.button('Log into Meta AI',self.open_login_window),self.button('Show / hide browser',self.toggle_auto_browser),self.button('Refresh queue',self.refresh_auto_queue))
        self._init_mission_panel()
        self.refresh_tracker_browser()
    def _init_mission_panel(self):
        try:surface=self.browser_surface
        except (AttributeError,RuntimeError):return
        try:
            layout=surface.layout()
            if layout is None:layout=QVBoxLayout(surface);layout.setContentsMargins(14,12,14,12);layout.setSpacing(10)
        except (AttributeError,RuntimeError):return
        try:
            layout.addStretch(1)
            self.login_card=QFrame();self.login_card.setObjectName('dashboardPanel')
            login_layout=QVBoxLayout(self.login_card);login_layout.setContentsMargins(14,12,14,12);login_layout.setSpacing(8)
            login_title=QLabel('META AI NEEDS YOU TO LOG IN');login_title.setStyleSheet('font-size:13px;font-weight:900;color:#f2f4f7;');login_layout.addWidget(login_title)
            login_hint=QLabel('Log in once in the visible Meta AI window, then verify your session. Poster and video jobs use a small in-app browser panel.');login_hint.setWordWrap(True);login_hint.setStyleSheet('color:#99a8bc;');login_layout.addWidget(login_hint)
            self.row(login_layout,self.button('Open login window',self.open_login_window,True),self.button("I've logged in — verify",self.verify_login))
            self.login_card.hide();layout.addWidget(self.login_card)
            layout.addStretch(1)
        except (AttributeError,RuntimeError):pass
        self.auto_stage_timer=QTimer(self);self.auto_stage_timer.setInterval(1000);self.auto_stage_timer.timeout.connect(self._auto_stage_tick)
        self.auto_stage='ready';self.auto_stage_started=0;self.auto_browser_hwnd=None
    def set_automation_stage(self,stage,detail=''):
        order=('queued','submitting','generating','collected','posted')
        self.auto_stage=str(stage or 'ready');self.auto_stage_detail=str(detail or '')
        try:self.auto_core.set_stage('collected' if self.auto_stage=='collecting' else self.auto_stage)
        except (AttributeError,RuntimeError):pass
        pass
        try:
            if self.auto_stage in ('submitting','generating','collecting'):
                self._position_auto_browser_panel();self.auto_browser_panel.show();self.auto_browser_panel.raise_()
                self.auto_mini_title.setText('META AI  ·  '+self.auto_stage.upper())
            else:self.auto_browser_panel.hide()
        except (AttributeError,RuntimeError):pass
        try:
            if self.auto_stage=='failed':self.browser_status.setText('Automation stopped: '+self.auto_stage_detail[:240]);self.browser_status.setStyleSheet('color:#ef6b77;font-weight:700;')
            elif self.auto_stage_detail:self.browser_status.setText(self.auto_stage_detail[:240]);self.browser_status.setStyleSheet('')
            elif self.auto_stage=='ready':self.browser_status.setText('Meta AI poster and video jobs run in the small in-app panel.');self.browser_status.setStyleSheet('')
        except (AttributeError,RuntimeError):pass
        try:
            if self.auto_stage in ('submitting','generating','collecting'):
                import time as _time
                if not getattr(self,'auto_stage_started',0):self.auto_stage_started=_time.monotonic()
                if not self.auto_stage_timer.isActive():self.auto_stage_timer.start()
            else:
                try:self.auto_stage_timer.stop()
                except Exception:pass
                self.auto_stage_started=0
                try:self.auto_core.set_elapsed('')
                except (AttributeError,RuntimeError):pass
        except (AttributeError,RuntimeError):pass
        if self.auto_stage=='login_needed':
            try:self.login_card.show()
            except (AttributeError,RuntimeError):pass
    def _auto_stage_tick(self):
        try:
            import time as _time
            started=getattr(self,'auto_stage_started',0) or 0
            if not started:return
            seconds=int(_time.monotonic()-started)
            try:self.auto_core.set_elapsed(f'{seconds//60:02d}:{seconds%60:02d}')
            except (AttributeError,RuntimeError):pass
        except (AttributeError,RuntimeError):pass
    def refresh_auto_queue(self):
        if not hasattr(self,'browser_queue'):return
        try:rows=self.store.recent_cinematics(30)
        except Exception:rows=[]
        active=[row for row in rows if str((row or {}).get('status') or '') not in ('completed','failed')]
        ready=[row for row in rows if str((row or {}).get('status') or '')=='completed' and str((row or {}).get('video') or '')]
        try:self.browser_queue.setText(f'Cinematic queue: {len(active)} active, {len(ready)} finished video(s) ready in the COD AI tab.')
        except (AttributeError,RuntimeError):pass
    def _auto_browser_alive(self):
        hwnd=getattr(self,'auto_browser_hwnd',None)
        if not hwnd:return None
        try:
            if ctypes.windll.user32.IsWindow(hwnd):return hwnd
        except Exception:return None
        self.auto_browser_hwnd=None
        return None
    def _show_auto_browser(self):
        hwnd=getattr(self,'auto_browser_hwnd',None)
        if not hwnd:return
        try:
            user32=ctypes.windll.user32
            if not user32.IsWindow(hwnd):self.auto_browser_hwnd=None;return
            if user32.IsIconic(hwnd):user32.ShowWindow(hwnd,9)
        except Exception:pass

    def toggle_auto_browser(self):
        hwnd=self._auto_browser_alive()
        if hwnd is None:
            try:self.browser_status.setText('The automation browser is not open right now. It opens on its own when a job runs — or press Log into Meta AI.')
            except (AttributeError,RuntimeError):pass
            return
        host=getattr(self,'auto_browser_host',None)
        docked=getattr(host,'hwnd',None) if host is not None else None
        try:docked_alive=bool(docked and ctypes.windll.user32.IsWindow(docked))
        except Exception:docked_alive=False
        if docked_alive:
            try:host.detach()
            except Exception:pass
            try:self.browser_status.setText('Automation browser popped out into its own window. Press Show / hide browser to dock it back here.')
            except (AttributeError,RuntimeError):pass
            try:self.log('Automation browser popped out of the tab.')
            except Exception:pass
            return
        if host is not None and getattr(host,'hwnd',None):
            try:host.detach()
            except Exception:pass
        self._embed_auto_browser(hwnd)
    def open_login_window(self):
        if getattr(self,'_inapp_active',None) or getattr(self,'_inapp_poster_active',None):
            try:self.statusBar().showMessage('A Meta AI job is running. Wait for it to finish before opening the login window.',8000)
            except (AttributeError,RuntimeError):pass
            return
        if getattr(self,'_login_context',None) is not None:
            try:self._show_auto_browser()
            except (AttributeError,RuntimeError):pass
            try:self.meta_verify_button.show()
            except (AttributeError,RuntimeError):pass
            return
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            try:self.statusBar().showMessage('Meta AI login could not open: Playwright is not installed.',10000)
            except (AttributeError,RuntimeError):pass
            return
        try:self.statusBar().showMessage('Opening Meta AI sign-in…',8000)
        except (AttributeError,RuntimeError):pass
        def work():
            try:
                playwright=sync_playwright().start()
                context=meta_automation.ensure_context(playwright,E.DATA/'TrackerChromeInApp',minimized=False)
                page=context.new_page()
                try:meta_automation.MetaDriver(context,E.DATA/'meta-diagnostics').ensure_logged_in(page)
                except Exception:pass
                self._login_context=context;self._login_playwright=playwright;self._login_page=page
            except Exception as exc:
                detail=str(exc)[:180]
                QTimer.singleShot(0,lambda detail=detail:self.statusBar().showMessage('Could not open the Meta AI login window: '+detail,12000));return
            QTimer.singleShot(0,self._login_opened)
        thread=threading.Thread(target=work,daemon=True);thread.start()
    def _login_opened(self):
        try:self.meta_verify_button.show()
        except (AttributeError,RuntimeError):pass
        try:self.login_card.show()
        except (AttributeError,RuntimeError):pass
        try:self.statusBar().showMessage('Log into Meta AI in the opened window, then click Verify Meta AI login in the sidebar.',15000)
        except (AttributeError,RuntimeError):pass
    def verify_login(self):
        context=getattr(self,'_login_context',None)
        if context is None:
            try:self.statusBar().showMessage('Click Log into Meta AI first.',8000)
            except (AttributeError,RuntimeError):pass
            return
        try:self.statusBar().showMessage('Checking Meta AI login…',8000)
        except (AttributeError,RuntimeError):pass
        def work():
            try:
                page=getattr(self,'_login_page',None)
                if page is None:raise RuntimeError('The Meta AI login page is no longer available. Click Log into Meta AI to reopen it.')
                meta_automation.MetaDriver(context,E.DATA/'meta-diagnostics').ensure_logged_in(page)
            except Exception as exc:
                QTimer.singleShot(0,lambda:self._login_failed(str(exc)));return
            QTimer.singleShot(0,self._login_verified)
        self.async_task(work)
    def _login_failed(self,detail):
        try:self.statusBar().showMessage('Still signed out. Log into Meta AI, then verify again.',12000)
        except (AttributeError,RuntimeError):pass
    def _login_verified(self):
        try:
            context=getattr(self,'_login_context',None)
            if context is not None:
                try:context.close()
                except Exception:pass
            playwright=getattr(self,'_login_playwright',None)
            if playwright is not None:
                try:playwright.stop()
                except Exception:pass
        except Exception:pass
        self._login_context=None;self._login_playwright=None;self._login_page=None
        try:self.meta_verify_button.hide()
        except (AttributeError,RuntimeError):pass
        try:self.meta_login_button.setText('Log into Meta AI')
        except (AttributeError,RuntimeError):pass
        try:self.login_card.hide()
        except (AttributeError,RuntimeError):pass
        self.set_automation_stage('ready')
        try:self.settings.values['meta_login_needed']=False;self.settings.save()
        except Exception:pass
        try:self.statusBar().showMessage('Meta AI login verified. Poster and video jobs will run in the background.',10000)
        except (AttributeError,RuntimeError):pass
        self.log('Meta AI login verified; poster and video jobs will run in the background.')
    def _record_auto_browser(self,known):
        try:current=set(self.find_tracker_chrome_windows())
        except Exception:return
        fresh=[hwnd for hwnd in current if hwnd not in (known or set())]
        if fresh:self.auto_browser_hwnd=fresh[0]

        try:self._embed_auto_browser()
        except (AttributeError,RuntimeError):pass
    def _embed_auto_browser(self,hwnd=None):
        try:
            hwnd=hwnd or getattr(self,'auto_browser_hwnd',None)
            if not hwnd:return
            try:
                if not ctypes.windll.user32.IsWindow(hwnd):
                    self.auto_browser_hwnd=None
                    try:self.auto_browser_host.detach()
                    except Exception:pass
                    self.auto_browser_host.hwnd=None
                    return
            except Exception:return
            try:self.auto_browser_surface.winId()
            except (AttributeError,RuntimeError):return
            try:
                if getattr(self.auto_browser_host,'hwnd',None):self.auto_browser_host.detach()
            except Exception:pass
            try:
                _bh=getattr(self,'browser_host',None)
                if _bh is not None and getattr(_bh,'hwnd',None):
                    try:
                        import ctypes as _ct
                        if _ct.windll.user32.IsWindow(_bh.hwnd):_bh.detach()
                        else:_bh.hwnd=None
                    except Exception:pass
            except (AttributeError,RuntimeError):pass
            self.auto_browser_host.attach(hwnd,int(self.auto_browser_surface.winId()),activate=False)
            self.auto_browser_hwnd=self.auto_browser_host.hwnd
            self.auto_browser_host.resize(max(800,self.auto_browser_surface.width()),max(600,self.auto_browser_surface.height()))
            self._position_auto_browser_panel();self.auto_browser_panel.show();self.auto_browser_panel.raise_()
            try:self.browser_status.setText('Meta AI browser is running in the small in-app panel.')
            except (AttributeError,RuntimeError):pass
        except Exception as exc:
            try:self.browser_status.setText('Browser embedding failed, showing the window instead: '+str(exc)[:160])
            except (AttributeError,RuntimeError):pass
            try:self._show_auto_browser()
            except (AttributeError,RuntimeError):pass
    def _validate_auto_browser_attach(self):
        host=getattr(self,'auto_browser_host',None)
        hwnd=getattr(host,'hwnd',None) if host is not None else None
        if not hwnd:return False
        try:alive=bool(ctypes.windll.user32.IsWindow(hwnd))
        except Exception:alive=False
        if alive:return True
        try:host.detach()
        except Exception:pass
        try:host.hwnd=None
        except (AttributeError,RuntimeError):pass
        return False
    def _start_embed_poll(self):
        try:self._browser_known_before_launch=set(self.find_tracker_chrome_windows())
        except Exception:self._browser_known_before_launch=set()
        self._browser_embed_ticks=0
        try:QTimer.singleShot(1200,self._poll_auto_browser_embed)
        except (AttributeError,RuntimeError):pass
    def _poll_auto_browser_embed(self):
        if getattr(self,'closing',False):return False
        try:self._drain_stage_queue()
        except Exception:pass
        try:
            known=getattr(self,'_browser_known_before_launch',None)
            if known is None:return False
            if self._validate_auto_browser_attach():return True
            self._record_auto_browser(set(known))
            if self._validate_auto_browser_attach():return True
        except Exception:pass
        try:ticks=int(getattr(self,'_browser_embed_ticks',0) or 0)+1
        except (TypeError,ValueError):ticks=41
        self._browser_embed_ticks=ticks
        if ticks<=40:
            try:QTimer.singleShot(1200,self._poll_auto_browser_embed)
            except (AttributeError,RuntimeError):pass
        return False
    def _show_tracker_browser_tab(self):
        try:
            for row in range(self.nav.count()):
                if self.nav.item(row).data(Qt.UserRole)=='Tracker Browser':
                    self.nav.setCurrentRow(row);break
        except (AttributeError,RuntimeError,TypeError):pass
    def _show_login_card(self,message):
        try:self.meta_login_button.setText('Log into Meta AI · login required')
        except (AttributeError,RuntimeError):pass
        try:self.cinematic_status.setText('Meta AI needs you to sign in. Use Log into Meta AI in the sidebar, then verify the login.')
        except (AttributeError,RuntimeError):pass
        try:self.login_card.show()
        except (AttributeError,RuntimeError):pass
        try:self.browser_status.setText(message[:240]);self.browser_status.setStyleSheet('color:#fdba74;font-weight:700;')
        except (AttributeError,RuntimeError):pass
        self.set_automation_stage('failed',message)
        try:self.settings.values['meta_login_needed']=True;self.settings.save()
        except Exception:pass
    def _driver_stage(self,stage,detail=''):
        try:stage_queue=self._stage_queue
        except AttributeError:stage_queue=self._stage_queue=queue.Queue()
        try:stage_queue.put_nowait((stage,detail))
        except Exception:pass
    def _apply_driver_stage(self,stage,detail=''):
        mapping={'composing':'submitting','uploading':'submitting','generating':'generating','collecting':'collecting'}
        try:self.set_automation_stage(mapping.get(stage,stage),detail)
        except (AttributeError,RuntimeError):pass
    def _drain_stage_queue(self):
        try:stage_queue=getattr(self,'_stage_queue',None)
        except Exception:stage_queue=None
        if stage_queue is None:return
        while True:
            try:stage,detail=stage_queue.get_nowait()
            except Exception:break
            self._apply_driver_stage(stage,detail)
    def chrome_executable(self):
        candidates=[Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'Google'/'Chrome'/'Application'/'chrome.exe',
                    Path(os.environ.get('ProgramFiles(x86)',r'C:\Program Files (x86)'))/'Google'/'Chrome'/'Application'/'chrome.exe']
        return next((path for path in candidates if path.is_file()),None)
    def tracker_browser_mode(self):
        try:mode=str(self.settings.values.get('tracker_browser_mode') or 'dedicated').strip().lower()
        except Exception:mode='dedicated'
        return mode if mode in ('dedicated','attach') else 'dedicated'
    def tracker_chrome_data_dir(self):
        try:base=Path(E.DATA)
        except Exception:base=Path.home()/'.vintendo-cod-tracker'
        folder=base/'TrackerChrome'
        try:folder.mkdir(parents=True,exist_ok=True)
        except OSError:pass
        return folder
    def open_tracker_chrome(self):
        if getattr(self.browser_host,'hwnd',None):
            try:alive=ctypes.windll.user32.IsWindow(self.browser_host.hwnd)
            except Exception:alive=False
            if alive:self.browser_host.resize(self.browser_surface.width(),self.browser_surface.height());self.browser_status.setText('Tracker Chrome is embedded in this tab. Leave its Meta AI tab untouched while a video generates.');return
        QMessageBox.information(self,'Meta.ai Login Required','You must login to Meta.ai for the videos and screenshots to work before you can post to Discord. This is a free and easy way to use the AI features. A new browser window will now open where you must login. Your info is never stored by Vintendo Tracker or anything outside of Meta, like it normally would if you were logging in.')
        if self.tracker_browser_mode()=='attach':
            self.browser_hold_message=False
            try:windows=self.find_tracker_chrome_windows()
            except Exception:windows=[]
            if not windows:
                self.browser_status.setText('No Chrome windows found. Open Chrome to meta.ai in your own profile first, then press Open Tracker Chrome again.');return
            self.embed_tracker_chrome();return
        executable=self.chrome_executable()
        if not executable:
            self.browser_status.setText('Chrome was not found in the usual install folders.');return
        try:known=set(self.find_tracker_chrome_windows())
        except Exception:known=set()
        try:subprocess.Popen([str(executable),'--user-data-dir='+str(self.tracker_chrome_data_dir()),'--window-size=1440,900',meta_automation.META_HOME])
        except OSError as exc:self.browser_status.setText('Could not launch Chrome: '+str(exc)[:200]);return
        self.browser_known_windows=known;self.browser_poll_ticks=0
        try:self.browser_poll.stop()
        except Exception:pass
        self.browser_poll.start()
        self.browser_status.setText('Tracker Chrome (dedicated profile) is opening and will embed automatically. Leave its Meta AI tab untouched while a video generates.');self.browser_hold_message=False;self.refresh_tracker_browser()
    def try_attach_tracker_chrome(self):
        try:
            if not self.browser_poll.isActive():return
        except Exception:pass
        try:current=set(self.find_tracker_chrome_windows())
        except Exception:return
        if getattr(self.browser_host,'hwnd',None):
            try:alive=ctypes.windll.user32.IsWindow(self.browser_host.hwnd)
            except Exception:alive=False
            if alive:
                try:self.browser_poll.stop()
                except Exception:pass
                return
        fresh=[hwnd for hwnd in current if hwnd not in getattr(self,'browser_known_windows',set())]
        if fresh:self._stop_browser_poll();self.attach_browser_window(fresh[0]);return
        self.browser_poll_ticks=getattr(self,'browser_poll_ticks',0)+1
        if self.browser_poll_ticks>=24:
            self._stop_browser_poll()
            if len(current)==1:self.attach_browser_window(next(iter(current)));return
            self.browser_hold_message=True;self.refresh_tracker_browser();self.browser_status.setText('Chrome opened but the new window was not found. Press Embed front Chrome window, then click the Tracker Chrome window.')
    def _stop_browser_poll(self):
        try:self.browser_poll.stop()
        except Exception:pass
    def refresh_tracker_browser(self):
        if not hasattr(self,'browser_status'):return
        try:rows=self.store.recent_cinematics(30)
        except Exception:rows=[]
        active=[row for row in rows if str((row or {}).get('status') or '') not in ('completed','failed')]
        ready=[row for row in rows if str((row or {}).get('status') or '')=='completed' and str((row or {}).get('video') or '')]
        self.browser_queue.setText(f'Cinematic queue: {len(active)} active, {len(ready)} finished video(s) ready in the COD AI tab.')
        if getattr(self,'browser_hold_message',False):return
        try:alive=bool(getattr(self.browser_host,'hwnd',None) and ctypes.windll.user32.IsWindow(self.browser_host.hwnd))
        except Exception:alive=False
        if alive:
            try:self.browser_status.setText('Tracker Chrome is embedded in this tab. Leave its Meta AI tab untouched while a video generates.')
            except (RuntimeError,AttributeError):pass
    def find_tracker_chrome_windows(self):
        user32=ctypes.windll.user32;found=[]
        callback_type=ctypes.WINFUNCTYPE(ctypes.c_bool,wintypes.HWND,wintypes.LPARAM)
        user32.EnumWindows.argtypes=[callback_type,wintypes.LPARAM];user32.EnumWindows.restype=wintypes.BOOL
        @callback_type
        def enum_callback(hwnd,lparam):
            if user32.IsWindowVisible(hwnd):
                buf=ctypes.create_unicode_buffer(256)
                if user32.GetClassNameW(hwnd,buf,256) and buf.value=='Chrome_WidgetWin_1':found.append(hwnd)
            return True
        user32.EnumWindows(enum_callback,0)
        return found
    def attach_browser_window(self,hwnd):
        try:
            self.browser_surface.winId();self.browser_host.attach(hwnd=hwnd,parent_hwnd=int(self.browser_surface.winId()));self.browser_hwnd=hwnd;self.browser_hold_message=False
            self.browser_host.resize(self.browser_surface.width(),self.browser_surface.height())
            self.browser_status.setText('Tracker Chrome is embedded in this tab. Leave its Meta AI tab untouched while a video generates.')
        except Exception as exc:self.browser_status.setText('Window embedding failed: '+str(exc)[:200]);self.log('Tracker Chrome embedding failed: '+str(exc)[:200])
    def embed_tracker_chrome(self):
        if not hasattr(self,'browser_surface'):return
        try:targets=self.find_tracker_chrome_windows()
        except Exception as exc:self.browser_status.setText('Window search failed: '+str(exc)[:200]);return
        if not targets:
            self.browser_status.setText('No Chrome windows found. Press Open Tracker Chrome first, then embed.');return
        if len(targets)==1:
            self.attach_browser_window(targets[0]);return
        self.browser_status.setText('Click the Tracker Chrome window now - embedding it as soon as you do.')
        try:own=int(self.winId())
        except Exception:own=None
        state={'stable':None,'ticks':0};timer=QTimer(self)
        def watch():
            state['ticks']+=1
            try:user32=ctypes.windll.user32;foreground=user32.GetForegroundWindow()
            except Exception:foreground=None;user32=None
            hit=None
            if foreground and foreground!=own and user32 is not None:
                try:
                    buf=ctypes.create_unicode_buffer(256);user32.GetClassNameW(foreground,buf,256)
                    if buf.value=='Chrome_WidgetWin_1':hit=foreground
                except Exception:hit=None
            if hit is not None and hit==state['stable']:
                timer.stop();self.attach_browser_window(hit);return
            state['stable']=hit
            if state['ticks']>=40:
                timer.stop();self.browser_status.setText('Timed out waiting. Click Embed, then click the Tracker Chrome window within 8 seconds.')
        timer.timeout.connect(watch);timer.start(200)
    def release_tracker_chrome(self):
        try:self.browser_host.detach()
        except Exception:pass
        self.browser_hwnd=None
        if hasattr(self,'browser_status'):self.browser_status.setText('Chrome window released. Press Open Tracker Chrome to launch it again.')
    def build_discord(self):
        self.page('Discord Settings','Discord posting settings are available under Settings.')
        self.discord_settings_panel=QWidget();l=QVBoxLayout(self.discord_settings_panel);l.setContentsMargins(12,12,12,12);l.setSpacing(12)
        self.webhook=self.edit();self.webhook.setEchoMode(QLineEdit.Password)
        try:self.webhook.setText(self.settings.webhook())
        except Exception:self.webhook.setPlaceholderText('Saved webhook could not be unlocked; enter it again')
        self.cinematic_webhook=self.edit();self.cinematic_webhook.setEchoMode(QLineEdit.Password)
        self.webhook_channel=self.edit();self.webhook_channel.setPlaceholderText('#match-clips');self.webhook_channel.setText(str(self.settings.values.get('webhook_channel') or ''))
        self.cinematic_webhook_channel=self.edit();self.cinematic_webhook_channel.setPlaceholderText('#cod-cinema');self.cinematic_webhook_channel.setText(str(self.settings.values.get('cinematic_webhook_channel') or ''))
        try:self.cinematic_webhook.setText(self.settings.cinematic_webhook())
        except Exception:self.cinematic_webhook.setPlaceholderText('Saved cinematic webhook could not be unlocked; enter it again')
        form=QFormLayout();form.addRow('Match stats webhook',self.webhook);form.addRow('Posts to channel',self.webhook_channel);form.addRow('Cinematic videos webhook',self.cinematic_webhook);form.addRow('Posts to channel',self.cinematic_webhook_channel);l.addLayout(form)
        self.allow_both=QCheckBox('Allow an additional Squad Night post for a match already posted in Solo Ops');self.allow_both.setChecked(self.settings.values.get('allow_both',False));l.addWidget(self.allow_both)
        self.send_test_button=self.button('Send test message',self.test_discord);self.row(l,self.send_test_button,self.button('Import existing tracker config',self.import_config));l.addStretch()
    def build_history(self):
        l=self.page('Match History','Match results and summaries are calculated from records saved by CODMunity and reviewed scoreboard captures.')
        period_row=QHBoxLayout();period_row.addWidget(QLabel('GAMES'));period_row.addStretch();self.history_period='recent';self.history_filter_group=QButtonGroup(self);self.history_filter_group.setExclusive(True)
        for index,(key,label) in enumerate((('recent','Recent'),('today','Today'),('session','This session'))):
            button=QPushButton(label);button.setCheckable(True);self.history_filter_group.addButton(button,index);period_row.addWidget(button)
            if key=='session':button.setToolTip('Shows matches whose recorded match time is after this tracker session started.')
            if key=='recent':button.setChecked(True)
        self.history_filter_group.idClicked.connect(lambda value:self.set_history_period(('recent','today','session')[value]));l.addLayout(period_row)
        self.history_summary={};summary_row=QHBoxLayout();summary_row.setSpacing(10)
        for key,title in (('games','GAMES PLAYED'),('win_rate','WIN RATE'),('kd','K/D RATIO'),('kills_per_game','KILLS PER GAME')):
            card=QFrame();card.setObjectName('historySummaryCard');box=QVBoxLayout(card);box.setContentsMargins(14,11,14,11);box.setSpacing(3)
            value=QLabel('—');value.setObjectName('historySummaryValue');caption=QLabel(title);caption.setObjectName('historySummaryCaption');detail=QLabel('');detail.setObjectName('historySummaryDetail');box.addWidget(value);box.addWidget(caption);box.addWidget(detail);summary_row.addWidget(card,1);self.history_summary[key]=(value,detail)
        l.addLayout(summary_row)
        self.history=QTableWidget(0,12);self.history.setHorizontalHeaderLabels(['MATCH ID','RESULT','PLACE','MODE','KILLS','ELIMS','DAMAGE','DEATHS','ASSISTS','DATE / TIME','STATUS','ATTEMPTS']);self.history.setSelectionBehavior(QTableWidget.SelectRows);self.history.setEditTriggers(QTableWidget.NoEditTriggers);self.history.setAlternatingRowColors(True);self.history.verticalHeader().hide();self.history.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents);self.history.horizontalHeader().setSectionResizeMode(3,QHeaderView.Stretch);self.history.itemDoubleClicked.connect(lambda _:self.review_selected());l.addWidget(self.history,1)
        self.row(l,self.button('Refresh',self.refresh_history),self.button('Review in Poster Studio',self.review_selected,True),self.button('View source data',self.view_source),self.button('Retry / resolve',self.resolve_selected))

    def set_history_period(self,period):
        self.history_period=period;self.refresh_history()
    def _codmunity_cache_dir(self):
        cache=codmunity_camo.default_cache_dir();return str(cache) if cache else ''
    def _autotrack_feed_path(self):
        try:base=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'CODMunity'/'autotrack-feed.json'
        except Exception:return ''
        return str(base) if base.is_file() else ''
    def build_camo_tracker(self):
        l=self.page('Camo Tracker','Weapon camos, prestige and mastery from the cached catalogue, with progress from the tracker and the desktop feed mirror. Manual ticks stay local.')
        status_bar=QFrame();status_bar.setObjectName('camoStatusBar')
        status_layout=QHBoxLayout(status_bar);status_layout.setContentsMargins(14,10,14,10);status_layout.setSpacing(10)
        self.camo_status_dot=QLabel('●');self.camo_status_dot.setObjectName('camoStatusDot');status_layout.addWidget(self.camo_status_dot)
        self.camo_status_line=QLabel('Waiting for Call of Duty');self.camo_status_line.setObjectName('camoStatusLine');status_layout.addWidget(self.camo_status_line)
        self.camo_last_line=QLabel('');self.camo_last_line.setObjectName('camoStatusLast');status_layout.addWidget(self.camo_last_line,1)
        self.camo_open_tracking_button=self.button('Open Auto tracking',self.goto_tracking);status_layout.addWidget(self.camo_open_tracking_button)
        l.addWidget(status_bar)
        self.camo_games_row=QHBoxLayout();self.camo_games_row.setSpacing(4);l.addLayout(self.camo_games_row)
        self.camo_game_group=QButtonGroup(self);self.camo_game_group.setExclusive(True)
        self.camo_game_buttons={}
        self.camo_grind_row=QHBoxLayout();self.camo_grind_row.setSpacing(6);l.addLayout(self.camo_grind_row)
        self.camo_grind_group=QButtonGroup(self);self.camo_grind_group.setExclusive(True)
        self.camo_grind_buttons={}
        title_row=QHBoxLayout()
        title_box=QVBoxLayout();title_box.setSpacing(2)
        self.camo_title_label=QLabel('Weapon Camos');self.camo_title_label.setObjectName('camoTitle');title_box.addWidget(self.camo_title_label)
        self.camo_subtitle_label=QLabel('');self.camo_subtitle_label.setObjectName('camoSubtitle');title_box.addWidget(self.camo_subtitle_label)
        title_row.addLayout(title_box,1)
        mastery_box=QVBoxLayout();mastery_box.setSpacing(2)
        self.camo_mastery_label=QLabel('MASTERY PROGRESSION');self.camo_mastery_label.setObjectName('camoMasteryLabel');mastery_box.addWidget(self.camo_mastery_label,0,Qt.AlignRight)
        self.camo_mastery_value=QLabel('');self.camo_mastery_value.setObjectName('camoMasteryValue');mastery_box.addWidget(self.camo_mastery_value,0,Qt.AlignRight)
        self.camo_mastery_bar=QProgressBar();self.camo_mastery_bar.setObjectName('camoMasteryBar');self.camo_mastery_bar.setTextVisible(False);self.camo_mastery_bar.setFixedWidth(180)
        mastery_box.addWidget(self.camo_mastery_bar,0,Qt.AlignRight)
        title_row.addLayout(mastery_box)
        l.addLayout(title_row)
        self.camo_cards_row=QHBoxLayout();self.camo_cards_row.setSpacing(10);l.addLayout(self.camo_cards_row)
        self._camo_game='mw4';self._camo_grind='';self._camo_catalogue={};self._camo_rows=[];self._camo_images={};self._camo_expanded=set()
        self._camo_filter='all';self._camo_category='ALL';self._camo_search='';self._camo_sort='popular'
        filter_frame=QFrame();filter_frame.setObjectName('camoFilterBar')
        filter_layout=QVBoxLayout(filter_frame);filter_layout.setContentsMargins(14,12,14,12);filter_layout.setSpacing(10)
        pills_row=QHBoxLayout();pills_row.setSpacing(6)
        self.camo_filter_group=QButtonGroup(self);self.camo_filter_group.setExclusive(True)
        self.camo_filter_buttons={}
        for index,(key,label) in enumerate((('all','ALL WEAPONS'),('progress','IN PROGRESS'),('todo','NOT STARTED'),('mastered','MASTERY'))):
            button=QPushButton(label);button.setCheckable(True);button.setObjectName('camoFilterPill')
            if key=='all':button.setChecked(True)
            self.camo_filter_group.addButton(button,index);button.setProperty('filter',key)
            self.camo_filter_buttons[key]=button;pills_row.addWidget(button)
        self.camo_filter_group.idClicked.connect(lambda _i:self._camo_apply_filters())
        pills_row.addStretch()
        self.camo_sort_combo=QComboBox();self.camo_sort_combo.addItem('MOST POPULAR','popular');self.camo_sort_combo.addItem('NAME A–Z','name')
        self.camo_sort_combo.currentIndexChanged.connect(lambda _i:self._camo_apply_filters())
        pills_row.addWidget(self.camo_sort_combo)
        self.camo_view_group=QButtonGroup(self);self.camo_view_group.setExclusive(True)
        self.camo_view_buttons={}
        for index,(key,label) in enumerate((('list','☰'),('grid','⊞'))):
            button=QPushButton(label);button.setCheckable(True);button.setObjectName('camoViewPill')
            if key=='list':button.setChecked(True)
            self.camo_view_group.addButton(button,index);button.setProperty('view',key)
            self.camo_view_buttons[key]=button;pills_row.addWidget(button)
        self.camo_view_group.idClicked.connect(lambda _i:self._camo_apply_filters())
        self._camo_view='list'
        filter_layout.addLayout(pills_row)
        search_row=QHBoxLayout();search_row.setSpacing(8)
        self.camo_search_edit=QLineEdit();self.camo_search_edit.setPlaceholderText('Search a weapon...');self.camo_search_edit.setObjectName('camoSearch')
        self.camo_search_edit.textChanged.connect(lambda text:self._camo_apply_filters())
        search_row.addWidget(self.camo_search_edit,2)
        self.camo_category_row=QHBoxLayout();self.camo_category_row.setSpacing(6)
        self.camo_category_group=QButtonGroup(self);self.camo_category_group.setExclusive(True)
        self.camo_category_buttons={}
        search_row.addLayout(self.camo_category_row,5)
        filter_layout.addLayout(search_row)
        hint_row=QHBoxLayout()
        hint=QLabel('Your camo progress across all weapons.');hint.setObjectName('camoHint');hint_row.addWidget(hint,1)
        self.camo_showing_label=QLabel('');self.camo_showing_label.setObjectName('camoHint');hint_row.addWidget(self.camo_showing_label)
        filter_layout.addLayout(hint_row)
        l.addWidget(filter_frame)
        self.camo_weapon_list=QListWidget();self.camo_weapon_list.setObjectName('camoWeaponList');l.addWidget(self.camo_weapon_list,1)
        self.camo_status=QLabel('');self.camo_status.setWordWrap(True);self.camo_status.setObjectName('camoStatusLine');l.addWidget(self.camo_status)
        self.row(l,self.button('Refresh catalogue',lambda:self._maybe_refresh_camo(True)))
        self.refresh_camo_tracker()
    def goto_tracking(self):
        for row in range(self.nav.count()):
            if self.nav.item(row).data(Qt.UserRole)=='Scoreboard Capture':
                self.nav.setCurrentRow(row);return
        self.nav.setCurrentRow(0)
    def _select_camo_game(self,index):
        try:button=self.camo_game_group.button(index)
        except (AttributeError,RuntimeError):button=None
        if button is not None:
            code=button.property('game')
            if code:self._camo_game=str(code)
        self._maybe_refresh_camo(True)
    def _maybe_refresh_camo(self,force=False):
        try:
            if not hasattr(self,'camo_weapon_list'):return
            if not force and getattr(self,'_camo_rows',None) and not getattr(self,'_camo_failed',None):return
            self._camo_failed=set()
            self.refresh_camo_tracker()
        except (AttributeError,RuntimeError):pass
    def refresh_camo_tracker(self):
        if not hasattr(self,'camo_games_row'):return
        catalogue=codmunity_camo.load_camo_catalogue(self._codmunity_cache_dir() or None)
        tabs=codmunity_camo.tracked_game_tabs(catalogue)
        if not tabs:tabs=[('mw4','MW4')]
        wanted={code for code,_ in tabs}
        if getattr(self,'_camo_game','mw4') not in wanted:self._camo_game=tabs[0][0]
        while self.camo_games_row.count():
            child=self.camo_games_row.takeAt(0)
            if child.widget():child.widget().deleteLater()
        self.camo_game_group=QButtonGroup(self);self.camo_game_group.setExclusive(True)
        self.camo_game_buttons={}
        for index,(code,label) in enumerate(tabs):
            button=QPushButton(('◉  ' if code==self._camo_game else '◎  ')+label)
            button.setCheckable(True);button.setChecked(code==self._camo_game);button.setObjectName('boardGameTab')
            self.camo_game_group.addButton(button,index);button.setProperty('game',code)
            self.camo_game_buttons[code]=button;self.camo_games_row.addWidget(button)
        self.camo_game_group.idClicked.connect(lambda index:self._select_camo_game(index))
        self.camo_games_row.addStretch()
        game=next((key for key,button in self.camo_game_buttons.items() if button.isChecked()),tabs[0][0])
        self._camo_game=game
        for key,button in self.camo_game_buttons.items():
            label=dict(tabs).get(key,key)
            button.setText(('◉  ' if button.isChecked() else '◎  ')+label)
        grinds=codmunity_camo.grinds_for_game(catalogue,game)
        while self.camo_grind_row.count():
            child=self.camo_grind_row.takeAt(0)
            if child.widget():child.widget().deleteLater()
        grind_label=QLabel('● GRIND');grind_label.setObjectName('camoGrindLabel');self.camo_grind_row.addWidget(grind_label)
        self.camo_grind_group=QButtonGroup(self);self.camo_grind_group.setExclusive(True)
        self.camo_grind_buttons={}
        for index,grind in enumerate(grinds):
            button=QPushButton(str(grind.get('title') or grind.get('name') or '').upper());button.setCheckable(True);button.setObjectName('camoGrindPill')
            if index==0:button.setChecked(True)
            self.camo_grind_group.addButton(button,index);button.setProperty('grind',grind.get('name'))
            self.camo_grind_buttons[grind.get('name')]=button;self.camo_grind_row.addWidget(button)
        self.camo_grind_group.idClicked.connect(lambda _i:self.refresh_camo_weapons())
        self.camo_grind_row.addStretch()
        self._camo_catalogue=catalogue
        progress=codmunity_camo.progress_map(self.store,self._autotrack_feed_path() or None)
        self._camo_progress=progress
        last=codmunity_camo.last_detection(self.store,self._autotrack_feed_path() or None)
        if last and last.get('weapon'):
            ago=codmunity_camo.ago_text(last.get('at'))
            self.camo_status_line.setText('Tracking your camos')
            self.camo_last_line.setText(f"Last: {last.get('weapon')} · {last.get('camo')}" + (f' · {ago}' if ago else ''))
        else:
            self.camo_status_line.setText('Waiting for Call of Duty')
            self.camo_last_line.setText('')
        if grinds:self.refresh_camo_weapons()
        else:self.camo_status.setText('No camo catalogue cached yet. Progress still records locally while tracking.')
    def refresh_camo_weapons(self):
        if not hasattr(self,'camo_weapon_list'):return
        catalogue=getattr(self,'_camo_catalogue',{})
        game=getattr(self,'_camo_game','mw4')
        grind=next((key for key,button in self.camo_grind_buttons.items() if button.isChecked()),None)
        grinds=codmunity_camo.grinds_for_game(catalogue,game)
        if grind not in [g.get('name') for g in grinds]:grind=grinds[0].get('name') if grinds else ''
        self._camo_grind=grind or ''
        current_grind=next((g for g in grinds if g.get('name')==grind),{})
        self.camo_title_label.setText(str(current_grind.get('title') or grind or 'Weapon Camos'))
        progress=getattr(self,'_camo_progress',{})
        rows=codmunity_camo.weapon_rows(catalogue,progress,game,grind)
        self._camo_rows=rows
        self.camo_subtitle_label.setText(f'{self._camo_game_label()} · {len(rows)} weapons')
        done,total=codmunity_camo.mastery_progress(catalogue,progress,game)
        self.camo_mastery_value.setText(f'{done} / {total} camos' if total else '—')
        try:self.camo_mastery_bar.setMaximum(max(1,total));self.camo_mastery_bar.setValue(done)
        except (AttributeError,RuntimeError):pass
        self._render_camo_cards(current_grind)
        self._render_camo_categories(rows)
        self._camo_apply_filters()
        self._fetch_camo_images()
    def _camo_game_label(self):
        for code,label in codmunity_camo.game_tabs(getattr(self,'_camo_catalogue',{})):
            if code==getattr(self,'_camo_game','mw4'):return label
        return self._camo_game.upper()
    def _render_camo_cards(self,grind):
        while self.camo_cards_row.count():
            child=self.camo_cards_row.takeAt(0)
            if child.widget():child.widget().deleteLater()
        tiers=codmunity_camo.tier_order(getattr(self,'_camo_catalogue',{}),self._camo_grind)
        art=codmunity_camo.tier_images(getattr(self,'_camo_catalogue',{}),self._camo_grind)
        prev_done,prev_total=None,None
        for order,name in enumerate(tiers):
            done,total=codmunity_camo.class_completion(self._camo_rows,name)
            locked=order>0 and prev_total and prev_done<prev_total and done==0
            self.camo_cards_row.addWidget(self._camo_card_widget(order+1,name,f'{done} / {total}',locked,done,total,image=art.get(name,'')))
            prev_done,prev_total=done,total
        self.camo_cards_row.addStretch()
    def _camo_card_widget(self,order,name,progress_text,locked,done,total,sub='',image=''):
        card=QFrame();card.setObjectName('camoCard');card.setFixedSize(220,190)
        overlay=QVBoxLayout(card);overlay.setContentsMargins(12,10,12,10);overlay.setSpacing(4)
        art_label=self._camo_card_art(image)
        overlay.addWidget(art_label)
        top=QHBoxLayout();badge=QLabel(str(order));badge.setObjectName('camoCardBadge');top.addWidget(badge);top.addStretch()
        if locked:lock=QLabel('LOCKED');lock.setObjectName('camoCardLocked');top.addWidget(lock)
        else:count=QLabel(progress_text);count.setObjectName('camoCardCount');top.addWidget(count)
        overlay.addLayout(top)
        title=QLabel(name);title.setObjectName('camoCardTitle');title.setWordWrap(True);overlay.addWidget(title)
        if sub:sub_label=QLabel(sub);sub_label.setObjectName('camoCardSub');overlay.addWidget(sub_label)
        bar=QProgressBar();bar.setObjectName('camoCardBar');bar.setTextVisible(False);bar.setMaximum(max(1,total));bar.setValue(done)
        overlay.addWidget(bar)
        return card

    def _camo_card_art(self,url):
        label=QLabel();label.setFixedSize(196,56);label.setAlignment(Qt.AlignCenter);label.setObjectName('camoCardArt')
        pixmap=self._camo_images.get(url) if url else None
        if pixmap is not None and not pixmap.isNull():
            label.setPixmap(pixmap.scaled(196,56,Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation))
        else:label.setText('—')
        return label

    def _render_camo_categories(self,rows):
        while self.camo_category_row.count():
            child=self.camo_category_row.takeAt(0)
            if child.widget():child.widget().deleteLater()
        self.camo_category_group=QButtonGroup(self);self.camo_category_group.setExclusive(True)
        self.camo_category_buttons={}
        categories=sorted({row['category'] for row in rows if row.get('category')})
        for index,label in enumerate(['ALL']+categories):
            button=QPushButton(label);button.setCheckable(True);button.setObjectName('camoCategoryPill')
            if label=='ALL':button.setChecked(True)
            self.camo_category_group.addButton(button,index);button.setProperty('category',label)
            self.camo_category_buttons[label]=button;self.camo_category_row.addWidget(button)
        self.camo_category_group.idClicked.connect(lambda _i:self._camo_apply_filters())
        self._camo_category='ALL'
        counts={'all':len(rows),'progress':0,'todo':0,'mastered':0}
        for row in rows:
            if row['total'] and row['done']>=row['total']:counts['mastered']+=1
            elif row['done']>0:counts['progress']+=1
            else:counts['todo']+=1
        labels={'all':f"ALL WEAPONS {counts['all']}",'progress':f"IN PROGRESS {counts['progress']}",'todo':f"NOT STARTED {counts['todo']}",'mastered':f"MASTERY {counts['mastered']}"}
        for key,button in self.camo_filter_buttons.items():button.setText(labels.get(key,key.upper()))
    def _camo_apply_filters(self):
        if not hasattr(self,'camo_weapon_list'):return
        view=next((key for key,button in self.camo_view_buttons.items() if button.isChecked()),'list')
        self._camo_view=view
        filt=next((key for key,button in self.camo_filter_buttons.items() if button.isChecked()),'all')
        category=next((key for key,button in self.camo_category_buttons.items() if button.isChecked()),'ALL')
        query=self.camo_search_edit.text().strip().casefold() if hasattr(self,'camo_search_edit') else ''
        sort=str(self.camo_sort_combo.currentData() or 'popular') if hasattr(self,'camo_sort_combo') else 'popular'
        rows=list(getattr(self,'_camo_rows',[]))
        if category!='ALL':rows=[r for r in rows if r.get('category')==category]
        if query:rows=[r for r in rows if query in r.get('weapon','').casefold()]
        if filt=='progress':rows=[r for r in rows if 0<r['done']<r['total']]
        elif filt=='todo':rows=[r for r in rows if r['done']==0]
        elif filt=='mastered':rows=[r for r in rows if r['total']>0 and r['done']>=r['total']]
        if sort=='name':rows.sort(key=lambda r:r.get('weapon','').casefold())
        else:rows.sort(key=lambda r:(-(r.get('popularity') or 0),r.get('weapon','').casefold()))
        self.camo_weapon_list.clear();self._camo_expanded={w for w in self._camo_expanded if any(r['weapon']==w for r in rows)}
        if view=='grid':
            from PySide6.QtWidgets import QListView as _QListView
            self.camo_weapon_list.setViewMode(_QListView.IconMode);self.camo_weapon_list.setResizeMode(_QListView.Adjust);self.camo_weapon_list.setMovement(_QListView.Static);self.camo_weapon_list.setGridSize(QSize(190,190));self.camo_weapon_list.setSpacing(8)
            for row in rows:
                item=QListWidgetItem();item.setData(Qt.UserRole,row['weapon']);item.setSizeHint(QSize(180,180))
                self.camo_weapon_list.addItem(item);self.camo_weapon_list.setItemWidget(item,self._camo_grid_card(row))
        else:
            from PySide6.QtWidgets import QListView as _QListView
            self.camo_weapon_list.setViewMode(_QListView.ListMode)
            for row in rows:
                item=QListWidgetItem();item.setData(Qt.UserRole,row['weapon']);item.setSizeHint(QSize(0,118))
                card=self._camo_weapon_card(row)
                self.camo_weapon_list.addItem(item);self.camo_weapon_list.setItemWidget(item,card)
                try:item.setSizeHint(QSize(0,max(118,card.sizeHint().height()+4)))
                except (AttributeError,RuntimeError,TypeError):pass
        total=len(getattr(self,'_camo_rows',[]))
        self.camo_showing_label.setText(f'SHOWING {len(rows)} / {total}')
        self.camo_status.setText(f'{len(rows)} of {total} weapons shown.')
    def _camo_thumb(self,url,size=30):
        label=QLabel('◉');label.setFixedSize(size,size);label.setAlignment(Qt.AlignCenter);label.setObjectName('camoThumb')
        pixmap=self._camo_images.get(url) if url else None
        if pixmap is not None and not pixmap.isNull():
            label.setPixmap(pixmap.scaled(size,size,Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation))
            label.setText('')
        return label
    def _camo_lock_icon(self,size=16):
        cache=getattr(self,'_camo_lock_icons',None)
        if cache is None:cache=self._camo_lock_icons={}
        if size not in cache:
            pix=QPixmap(size,size);pix.fill(Qt.transparent)
            painter=QPainter(pix);painter.setRenderHint(QPainter.Antialiasing)
            pen=QPen(QColor('#c4ccd8'));pen.setWidth(max(2,size//6));pen.setCapStyle(Qt.RoundCap);painter.setPen(pen);painter.setBrush(Qt.NoBrush)
            painter.drawArc(QRectF(size*0.28,size*0.08,size*0.44,size*0.40),0,180*16)
            painter.setPen(Qt.NoPen);painter.setBrush(QColor('#c4ccd8'))
            painter.drawRoundedRect(QRectF(size*0.20,size*0.42,size*0.60,size*0.48),3,3)
            painter.end();cache[size]=pix
        return cache[size]
    def _camo_badge_thumb(self,url,size,done,locked):
        box=QWidget();box.setFixedSize(size,size)
        base=self._camo_thumb(url,size);base.setParent(box);base.move(0,0);base.show()
        if done:
            badge=QLabel('✓',box);badge.setFixedSize(14,14);badge.move(size-14,0)
            badge.setStyleSheet('background:#22c55e;color:#ffffff;border-radius:7px;font-size:10px;font-weight:900;')
            badge.setAlignment(Qt.AlignCenter);badge.setAttribute(Qt.WA_TransparentForMouseEvents);badge.show()
        elif locked:
            lock=QLabel(box);lock.setFixedSize(16,16);lock.move((size-16)//2,(size-16)//2)
            lock.setPixmap(self._camo_lock_icon(16));lock.setAttribute(Qt.WA_TransparentForMouseEvents);lock.show()
        return box
    def _fetch_camo_images(self):
        if os.environ.get('PYTEST_CURRENT_TEST') is not None:return
        urls=set()
        for row in getattr(self,'_camo_rows',[]):
            if row.get('icon'):urls.add(row['icon'])
            for camo in row.get('camos',[]):
                if camo.get('image'):urls.add(camo['image'])
        for _name,_url in codmunity_camo.tier_images(getattr(self,'_camo_catalogue',{}),getattr(self,'_camo_grind','')).items():
            if _url:urls.add(_url)
        failed_known=getattr(self,'_camo_failed',None)
        if failed_known is None:failed_known=self._camo_failed=set()
        urls=[u for u in urls if u and u not in self._camo_images and u not in failed_known]
        if not urls:return
        folder=E.DATA/'camo-images';folder.mkdir(parents=True,exist_ok=True)
        import hashlib as _hashlib
        cached={url:folder/(_hashlib.sha256(url.encode()).hexdigest()[:24]+'.img') for url in urls}
        for url,path in cached.items():
            if path.is_file():
                pixmap=QPixmap(str(path))
                if not pixmap.isNull():self._camo_images[url]=pixmap
        todo=[url for url in urls if url not in self._camo_images]
        if not todo:return
        def work():
            from urllib.request import Request as _Request,urlopen as _urlopen
            _h={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) VintendoCODTracker','Referer':'https://codmunity.gg/'}
            saved=[];failed=[]
            for url in todo[:12]:
                try:
                    with _urlopen(_Request(url,headers=_h),timeout=20) as response:body=response.read(2*1024*1024)
                    if not body:failed.append(url);continue
                    path=cached[url];path.write_bytes(body);saved.append(url)
                except Exception:failed.append(url);continue
            return saved,failed
        def done(result):
            saved,failed=result
            for url in saved:
                pixmap=QPixmap(str(cached[url]))
                if not pixmap.isNull():self._camo_images[url]=pixmap
            if failed:
                failed_known.update(u for u in failed if u not in self._camo_images)
                self.log(f'Camo artwork: {len(failed)} image(s) failed to download; showing placeholders.')
            if saved and hasattr(self,'camo_weapon_list'):
                self.refresh_camo_weapons()
            self._fetch_camo_images()
        self.async_task(work,done,lambda _message:None)

    def _camo_weapon_card(self,row):
        card=QFrame();card.setObjectName('camoWeaponCard')
        outer=QVBoxLayout(card);outer.setContentsMargins(10,10,10,10);outer.setSpacing(8)
        top=QHBoxLayout();top.setSpacing(10)
        expander=QPushButton('+');expander.setObjectName('camoExpander');expander.setFixedSize(28,28)
        expander.setToolTip('Open full camo details')
        expander.clicked.connect(lambda _on,weapon=row['weapon']:self._open_camo_detail(weapon))
        top.addWidget(expander)
        top.addWidget(self._camo_thumb(row.get('icon'),52))
        name_box=QVBoxLayout();name_box.setSpacing(0)
        name=QLabel(row['weapon']);name.setObjectName('camoWeaponName')
        name.setCursor(Qt.PointingHandCursor);name.setToolTip('Click to expand')
        name.mousePressEvent=lambda _event,weapon=row['weapon']:self._toggle_camo_expanded(weapon)
        name_box.addWidget(name)
        sub=QLabel((row.get('category') or '').upper());sub.setObjectName('camoWeaponClass');name_box.addWidget(sub)
        if row['weapon'] in self._camo_expanded:
            segrow=QHBoxLayout();segrow.setSpacing(2)
            for _i in range(max(0,int(row.get('total') or 0))):
                _seg=QLabel();_seg.setFixedSize(10,6)
                _seg.setStyleSheet('background:#22c55e;border-radius:2px;' if _i<int(row.get('done') or 0) else 'background:#262c36;border-radius:2px;')
                segrow.addWidget(_seg)
            segrow.addStretch(1);name_box.addLayout(segrow)
        top.addLayout(name_box)
        strip=QHBoxLayout();strip.setSpacing(3)
        show_badges=row['weapon'] in self._camo_expanded
        for camo in row.get('camos',[]):
            tip=f"{camo.get('name')}: {camo.get('challenge') or ('done' if camo.get('done') else 'locked' if camo.get('locked') else 'todo')}"
            if show_badges:
                thumb=self._camo_badge_thumb(camo.get('image'),30,bool(camo.get('done')),bool(camo.get('locked')))
            else:
                thumb=self._camo_thumb(camo.get('image'),30)
                if camo.get('done'):thumb.setProperty('camoState','done')
                elif camo.get('locked'):thumb.setProperty('camoState','locked')
                else:thumb.setProperty('camoState','todo')
                thumb.style().unpolish(thumb);thumb.style().polish(thumb)
            thumb.setToolTip(tip)
            strip.addWidget(thumb)
        strip.addWidget(QLabel(f"{row['done']}/{row['total']}"))
        top.addLayout(strip,1)
        next_box=QVBoxLayout();next_box.setSpacing(2)
        caption=QLabel('NEXT CAMO');caption.setObjectName('camoNextCaption');next_box.addWidget(caption)
        nxt=row.get('next')
        if nxt:
            next_name=QLabel(nxt['name']);next_name.setObjectName('camoNextName');next_box.addWidget(next_name)
            for tag,text,obj in (('MP',nxt.get('challenge'),'camoModeMP'),('DMZ',nxt.get('challenge_dm'),'camoModeDMZ'),('WZ',nxt.get('challenge_wz'),'camoModeWZ')):
                if not text:continue
                _r=QHBoxLayout();_r.setSpacing(6)
                _chip=QLabel(tag);_chip.setObjectName(obj);_r.addWidget(_chip)
                _ln=QLabel(str(text));_ln.setWordWrap(True);_ln.setObjectName('camoNextLine');_r.addWidget(_ln,1)
                next_box.addLayout(_r)
            if row['weapon'] in self._camo_expanded:
                _mark=self.button('Mark saved',lambda _on,weapon=row['weapon'],camo=nxt.get('name',''):self.mark_camo_challenge(weapon,camo))
                _mark_row=QHBoxLayout();_mark_row.addStretch(1);_mark_row.addWidget(_mark);next_box.addLayout(_mark_row)
        else:
            done_label=QLabel('MASTERED');done_label.setObjectName('camoNextName');next_box.addWidget(done_label)
        top.addLayout(next_box,2)
        ring=QLabel(f"{row['done']}/{row['total']}");ring.setObjectName('camoRing');ring.setAlignment(Qt.AlignCenter);ring.setFixedSize(52,52)
        top.addWidget(ring)
        outer.addLayout(top)
        return card
    def _camo_grid_card(self,row):
        card=QFrame();card.setObjectName('camoWeaponCard')
        layout=QVBoxLayout(card);layout.setContentsMargins(10,10,10,10);layout.setSpacing(6)
        layout.addWidget(self._camo_thumb(row.get('icon'),64),0,Qt.AlignHCenter)
        name=QLabel(row['weapon']);name.setObjectName('camoWeaponName');name.setAlignment(Qt.AlignCenter);name.setWordWrap(True);layout.addWidget(name)
        ring=QLabel(f"{row['done']}/{row['total']}");ring.setObjectName('camoRingSmall');ring.setAlignment(Qt.AlignCenter);layout.addWidget(ring,0,Qt.AlignCenter)
        return card
    def _toggle_camo_expanded(self,weapon):
        if weapon in self._camo_expanded:self._camo_expanded.discard(weapon)
        else:self._camo_expanded.add(weapon)
        self._camo_apply_filters()
    _CAMO_GROUP_ORDER=('Completionist','Special Camos','Military Camos','Mastery Camos','Mastery','Base Camos','Unique Camos','Weapon Prestige','Prestige 1','Prestige 2','Prestige Legend')
    def _camo_weapon_groups(self,weapon):
        catalogue=getattr(self,'_camo_catalogue',None) or {}
        game=getattr(self,'_camo_game','mw4') or 'mw4'
        try:progress=codmunity_camo.progress_map(self.store,self._autotrack_feed_path() or None)
        except Exception:progress=getattr(self,'_camo_progress',{}) or {}
        groups={}
        seen=set()
        for grind in codmunity_camo.grinds_for_game(catalogue,game):
            for row in codmunity_camo.weapon_rows(catalogue,progress,game,grind.get('name')):
                if str(row.get('weapon','')).casefold()!=str(weapon).casefold():continue
                for camo in row.get('camos',[]) or []:
                    key=str(camo.get('name') or '').casefold()
                    if not key or key in seen:continue
                    seen.add(key)
                    group=str(camo.get('subcategory') or 'Other').strip() or 'Other'
                    groups.setdefault(group,[]).append(camo)
        ordered=[g for g in self._CAMO_GROUP_ORDER if g in groups]
        ordered.extend(sorted(k for k in groups if k not in self._CAMO_GROUP_ORDER))
        return [(name,groups[name]) for name in ordered]
    def _camo_detail_tile(self,weapon,camo):
        tile=QFrame();tile.setObjectName('camoDetailTile')
        done=bool(camo.get('done'))
        tile.setToolTip('Click to unmark' if done else 'Click to mark as saved')
        tile.setCursor(Qt.PointingHandCursor)
        box=QVBoxLayout(tile);box.setContentsMargins(10,10,10,10);box.setSpacing(6)
        art_row=QHBoxLayout();art_row.addStretch(1)
        art_row.addWidget(self._camo_badge_thumb(camo.get('image'),64,done,bool(camo.get('locked'))))
        art_row.addStretch(1);box.addLayout(art_row)
        name=QLabel(str(camo.get('name') or ''));name.setObjectName('camoDetailName');name.setWordWrap(True);name.setAlignment(Qt.AlignCenter);box.addWidget(name)
        req=str(camo.get('challenge') or camo.get('challenge_dm') or camo.get('challenge_wz') or '')
        req_label=QLabel(req);req_label.setWordWrap(True);req_label.setAlignment(Qt.AlignCenter);req_label.setObjectName('camoDetailReq');box.addWidget(req_label)
        tile.mousePressEvent=lambda _event,w=weapon,c=str(camo.get('name','')),d=done:self._toggle_camo_mark(w,c,d)
        return tile
    def _toggle_camo_mark(self,weapon,camo,done):
        try:self.mark_camo_challenge(weapon,camo,'dismissed' if done else 'saved')
        except Exception as exc:self.notice(str(exc));return
        try:self._open_camo_detail(weapon)
        except Exception:pass
    def _open_camo_detail(self,weapon):
        try:
            old=getattr(self,'_camo_detail_dialog',None)
            if old is not None:old.close()
        except (AttributeError,RuntimeError):pass
        catalogue=getattr(self,'_camo_catalogue',None) or {}
        game=getattr(self,'_camo_game','mw4') or 'mw4'
        icon=next((r.get('icon','') for r in getattr(self,'_camo_rows',[]) if str(r.get('weapon','')).casefold()==str(weapon).casefold()),'')
        groups=self._camo_weapon_groups(weapon)
        done=sum(1 for _,camos in groups for c in camos if c.get('done'))
        total=sum(len(camos) for _,camos in groups)
        dialog=QDialog(self);dialog.setWindowTitle(str(weapon)+' — Camos');dialog.setModal(False)
        dialog.setMinimumSize(720,600)
        layout=QVBoxLayout(dialog);layout.setContentsMargins(18,14,18,14);layout.setSpacing(10)
        head=QHBoxLayout();head.setSpacing(12)
        head.addWidget(self._camo_thumb(icon,48))
        title_box=QVBoxLayout();title_box.setSpacing(0)
        title=QLabel(str(weapon));title.setObjectName('camoDetailTitle');title_box.addWidget(title)
        count=QLabel(f'{done} / {total}');count.setObjectName('camoDetailReq');title_box.addWidget(count)
        head.addLayout(title_box);head.addStretch(1)
        close_button=self.button('Close',dialog.close);close_button.setObjectName('camoDetailClose');head.addWidget(close_button)
        layout.addLayout(head)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        host=QWidget();host_layout=QVBoxLayout(host);host_layout.setSpacing(12)
        for group_name,camos in groups:
            ghead=QHBoxLayout()
            glabel=QLabel(group_name);glabel.setObjectName('camoGroupTitle');ghead.addWidget(glabel);ghead.addStretch(1)
            gcount=QLabel(str(len(camos)));gcount.setObjectName('camoGroupTitle');ghead.addWidget(gcount)
            host_layout.addLayout(ghead)
            grid=QGridLayout();grid.setSpacing(10)
            for index,camo in enumerate(camos):
                grid.addWidget(self._camo_detail_tile(weapon,camo),index//4,index%4)
            host_layout.addLayout(grid)
        scroll.setWidget(host);layout.addWidget(scroll,1)
        dialog.show();dialog.raise_();dialog.activateWindow()
        self._camo_detail_dialog=dialog
        return dialog
    def mark_camo_challenge(self,weapon,camo,state='saved'):
        grind=getattr(self,'_camo_grind','') or getattr(self,'_camo_game','mw4')
        det=codmunity_camo.mark_progress(self.store,weapon,camo,state,grind=grind)
        if det:
            self.log(f'Camo tick recorded: {weapon} / {camo} → {state}.')
            self._camo_progress=codmunity_camo.progress_map(self.store,self._autotrack_feed_path() or None)
            self.refresh_camo_weapons()
        else:self.notice('Could not record the tick.')
    def build_leaderboards(self):
        l=self.page('Leaderboards','The best CODMunity players, per game and mode. Only players who chose to appear are listed.')
        board_left=QFrame();board_left.setObjectName('dashboardPanel');board_layout=QVBoxLayout(board_left);board_layout.setContentsMargins(12,10,12,10);board_layout.setSpacing(8)
        games_row=QHBoxLayout()
        self.board_game_group=QButtonGroup(self);self.board_game_group.setExclusive(True)
        self.board_warzone_button=QPushButton('◎  WARZONE');self.board_warzone_button.setCheckable(True);self.board_warzone_button.setObjectName('boardGameTab')
        self.board_mw4_button=QPushButton('▤  MW4');self.board_mw4_button.setCheckable(True);self.board_mw4_button.setChecked(True);self.board_mw4_button.setObjectName('boardGameTab')
        self.board_game_group.addButton(self.board_warzone_button,0);self.board_game_group.addButton(self.board_mw4_button,1)
        self.board_game_group.idClicked.connect(lambda _i:self.refresh_leaderboards())
        games_row.addWidget(self.board_warzone_button);games_row.addWidget(self.board_mw4_button)
        self.board_mode_combo=QComboBox()
        for value,label in codmunity_camo.WARZONE_MODES:self.board_mode_combo.addItem(label,value)
        self.board_mode_combo.currentIndexChanged.connect(lambda _i:self.refresh_leaderboards())
        games_row.addWidget(self.board_mode_combo);games_row.addStretch();board_layout.addLayout(games_row)
        self._board_card=QFrame();self._board_card.setObjectName('boardCard')
        card_layout=QVBoxLayout(self._board_card);card_layout.setContentsMargins(18,14,18,14);card_layout.setSpacing(12)
        header_row=QHBoxLayout()
        self.board_title_label=QLabel('♛ MW4 · Multiplayer');self.board_title_label.setObjectName('boardTitle');header_row.addWidget(self.board_title_label);header_row.addStretch()
        self.board_metric_group=QButtonGroup(self);self.board_metric_group.setExclusive(True)
        self.board_metric_buttons={}
        for index,(key,label) in enumerate(codmunity_camo.BOARD_METRICS):
            button=QPushButton(label);button.setCheckable(True);button.setObjectName('boardMetricTab')
            self.board_metric_group.addButton(button,index);button.setProperty('metric',key)
            if key=='kd':button.setChecked(True)
            self.board_metric_buttons[key]=button;header_row.addWidget(button)
        self.board_metric_group.idClicked.connect(lambda _i:self.refresh_board_table())
        card_layout.addLayout(header_row)
        self.board_you_frame=QFrame();self.board_you_frame.setObjectName('boardYouRow')
        you_layout=QHBoxLayout(self.board_you_frame);you_layout.setContentsMargins(12,10,12,10);you_layout.setSpacing(12)
        self.board_you_avatar=QLabel('◉');self.board_you_avatar.setFixedSize(52,52);self.board_you_avatar.setAlignment(Qt.AlignCenter);self.board_you_avatar.setObjectName('boardAvatar');you_layout.addWidget(self.board_you_avatar)
        you_text=QVBoxLayout();you_text.setSpacing(2)
        you_name_row=QHBoxLayout()
        self.board_you_badge=QLabel('YOU');self.board_you_badge.setObjectName('boardYouBadge');you_name_row.addWidget(self.board_you_badge)
        self.board_you_name=QLabel('—');self.board_you_name.setObjectName('boardYouName');you_name_row.addWidget(self.board_you_name,1)
        you_text.addLayout(you_name_row)
        self.board_you_sub=QLabel('');self.board_you_sub.setObjectName('boardYouSub');self.board_you_sub.setWordWrap(True);you_text.addWidget(self.board_you_sub)
        you_layout.addLayout(you_text,1)
        for caption,attr in (('K/D RATIO','board_you_kd'),('WINS','board_you_wins'),('GAMES','board_you_games')):
            cell=QVBoxLayout();cell.setSpacing(2)
            title=QLabel(caption);title.setObjectName('boardYouCaption');title.setAlignment(Qt.AlignCenter);cell.addWidget(title)
            value=QLabel('—');value.setObjectName('boardYouValue');value.setAlignment(Qt.AlignCenter);cell.addWidget(value)
            setattr(self,attr,value);you_layout.addLayout(cell)
        visibility_box=QVBoxLayout();visibility_box.setSpacing(4)
        visibility_label=QLabel('Public');visibility_label.setObjectName('boardYouCaption');visibility_label.setAlignment(Qt.AlignCenter);visibility_box.addWidget(visibility_label)
        self.board_visibility_toggle=QCheckBox();self.board_visibility_toggle.setObjectName('boardSwitch');self.board_visibility_toggle.setChecked(True)
        self.board_visibility_toggle.toggled.connect(lambda _on:self.flip_board_visibility())
        visibility_box.addWidget(self.board_visibility_toggle,0,Qt.AlignCenter)
        you_layout.addLayout(visibility_box)
        card_layout.addWidget(self.board_you_frame)
        self.board_table=QTableWidget(0,5);self.board_table.setHorizontalHeaderLabels(['#','PLAYER','K/D RATIO','WINS','GAMES']);self.board_table.setSelectionBehavior(QTableWidget.SelectRows);self.board_table.setEditTriggers(QTableWidget.NoEditTriggers);self.board_table.setAlternatingRowColors(True);self.board_table.verticalHeader().setDefaultSectionSize(56);self.board_table.verticalHeader().hide();self.board_table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch);self.board_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Interactive);self.board_table.setColumnWidth(0,72);self.board_table.horizontalHeaderItem(0).setTextAlignment(Qt.AlignCenter)
        card_layout.addWidget(self.board_table,1)
        self.board_identity=QLabel('');self.board_identity.setWordWrap(True);self.board_identity.setObjectName('boardIdentity');card_layout.addWidget(self.board_identity)
        self.board_status=QLabel('');self.board_status.setWordWrap(True);self.board_status.setObjectName('boardStatus');card_layout.addWidget(self.board_status)
        board_layout.addWidget(self._board_card,1)
        self._board_game='mw4';self._board_mode='resurgence';self._board_metric='kd';self._board_entries=[];self._board_avatars={}
        self.row(board_layout,self.button('Refresh',self.refresh_leaderboards),self.button('Copy profile link',self.copy_profile_link),self.button('Open CODMunity site',self.open_codmunity_site))
        self.refresh_leaderboards()
        self.dash_bottom.insertWidget(0,board_left,70)
    def board_snapshot(self):
        try:return self.store.meta('codmunity_stats') or {}
        except Exception:return {}
    def refresh_leaderboards(self):
        if not hasattr(self,'board_table'):return
        game='warzone' if self.board_warzone_button.isChecked() else 'mw4'
        mode=str(self.board_mode_combo.currentData() or 'resurgence')
        self.board_mode_combo.setVisible(game=='warzone')
        snapshot=self.board_snapshot()
        _,label,entries=codmunity_camo.board_for(game,mode,snapshot)
        self._board_game=game;self._board_mode=mode;self._board_entries=list(entries)
        self.board_title_label.setText(f'♛ {label}')
        hits=codmunity_camo.identity_hits(snapshot)
        if hits:self.board_identity.setText(' · '.join(f"#{h.get('rank')} {h.get('leaderboard')}" for h in hits[:4]))
        else:self.board_identity.setText('')
        self.refresh_you_row(snapshot)
        self.refresh_board_table()
        self._fetch_board_avatars()
    def refresh_you_row(self,snapshot):
        stats=codmunity_camo.you_row_stats(snapshot,self._board_game,self._board_mode)
        name=str(self.settings.values.get('player','vintendo') or 'vintendo').strip() or 'vintendo'
        self.board_you_name.setText(name)
        if stats['kd'] is not None:self.board_you_kd.setText(f"{stats['kd']:.2f}")
        else:self.board_you_kd.setText('—')
        self.board_you_wins.setText(codmunity_camo.format_count(stats['wins']))
        self.board_you_games.setText(codmunity_camo.format_count(stats['games']))
        if stats['has_stats']:
            parts=[]
            if stats['kills'] is not None:parts.append(f"{codmunity_camo.format_count(stats['kills'])} kills")
            if stats['games'] is not None:parts.append(f"{codmunity_camo.format_count(stats['games'])} games")
            self.board_you_sub.setText('Local lifetime: '+', '.join(parts)+'.')
        else:self.board_you_sub.setText('No stats yet: play a match with the app open.')
        self.board_you_avatar.setText('◉')
        photo=self._you_headshot()
        if photo:
            pixmap=QPixmap(str(photo))
            if not pixmap.isNull():
                self.board_you_avatar.setPixmap(pixmap.scaled(52,52,Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation))
        listed=any(isinstance(h,dict) for h in codmunity_camo.identity_hits(snapshot))
        self.board_visibility_toggle.blockSignals(True);self.board_visibility_toggle.setChecked(bool(listed));self.board_visibility_toggle.blockSignals(False)
        self.board_visibility_toggle.setEnabled(self.codmunity_player_service.configured)
        self.board_visibility_toggle.setToolTip('Sign in to change visibility.' if not self.codmunity_player_service.configured else 'Toggle leaderboard visibility.')
    def _you_headshot(self):
        try:
            name=str(self.settings.values.get('player','vintendo') or '').casefold()
            for player in self.store.roster():
                aliases=[str(player.get('name',''))]+[str(a) for a in (player.get('aliases') or [])]
                if any(a.casefold()==name for a in aliases if a):
                    photo=player.get('photo') or ''
                    if photo and Path(photo).is_file():return photo
        except Exception:pass
        return None
    def flip_board_visibility(self):
        if self.board_visibility_toggle.signalsBlocked():return
        wanted=self.board_visibility_toggle.isChecked()
        if not self.codmunity_player_service.configured:
            self.board_visibility_toggle.blockSignals(True);self.board_visibility_toggle.setChecked(not wanted);self.board_visibility_toggle.blockSignals(False)
            self.board_status.setText('Sign in with your CODMunity account to change visibility.')
            return
        def work():
            import codmunity_player_stats as _cps
            auth=self.codmunity_auth
            if auth is None:raise RuntimeError('Not signed in')
            return _cps.set_visibility(auth,wanted)
        def done(_result):
            self.log('Leaderboard visibility updated.');self.board_status.setText('Visibility updated.')
        def failed(message):
            self.board_visibility_toggle.blockSignals(True);self.board_visibility_toggle.setChecked(not wanted);self.board_visibility_toggle.blockSignals(False)
            self.board_status.setText(str(message));self.log('Visibility update failed.')
        self.async_task(work,done,failed)
    def _rank_badge(self,position):
        label=QLabel(str(position));label.setAlignment(Qt.AlignCenter);label.setFixedSize(40,30)
        tier=codmunity_camo.medal(position)
        if tier=='gold':label.setText(f'♛ {position}');label.setObjectName('boardMedalGold')
        elif tier=='silver':label.setObjectName('boardMedalSilver')
        elif tier=='bronze':label.setObjectName('boardMedalBronze')
        else:label.setObjectName('boardMedalPlain')
        return label
    def _player_cell(self,entry):
        widget=QWidget();layout=QHBoxLayout(widget);layout.setContentsMargins(4,2,4,2);layout.setSpacing(10)
        avatar=QLabel('◉');avatar.setFixedSize(36,36);avatar.setAlignment(Qt.AlignCenter);avatar.setObjectName('boardAvatar')
        url=str(entry.get('avatar') or '')
        pixmap=self._board_avatars.get(url) if url else None
        if pixmap is not None and not pixmap.isNull():
            avatar.setPixmap(pixmap.scaled(36,36,Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation))
            avatar.setText('')
        layout.addWidget(avatar)
        text=QVBoxLayout();text.setSpacing(0)
        name=QLabel(str(entry.get('displayName') or entry.get('username') or '—'));name.setObjectName('boardPlayerName')
        handle=QLabel('@'+str(entry.get('username') or '—'));handle.setObjectName('boardPlayerHandle')
        text.addWidget(name);text.addWidget(handle);layout.addLayout(text,1)
        return widget
    def refresh_board_table(self):
        if not hasattr(self,'board_table'):return
        metric=next((key for key,button in self.board_metric_buttons.items() if button.isChecked()),'kd')
        self._board_metric=metric
        entries=codmunity_camo.sort_entries(self._board_entries,metric)[:100]
        self.board_table.setRowCount(0)
        bold=QFont();bold.setBold(True)
        for position,entry in enumerate(entries,1):
            row=self.board_table.rowCount();self.board_table.insertRow(row)
            rank_cell=QWidget();rank_layout=QHBoxLayout(rank_cell);rank_layout.setContentsMargins(0,0,0,0);rank_layout.addWidget(self._rank_badge(position),0,Qt.AlignCenter)
            self.board_table.setCellWidget(row,0,rank_cell)
            self.board_table.setCellWidget(row,1,self._player_cell(entry))
            kd=codmunity_camo.metric_value(entry,'kd');wins=codmunity_camo.metric_value(entry,'wins');games=entry.get('gamesPlayed')
            kd_item=QTableWidgetItem(f'{kd:.2f}' if kd is not None else '—');kd_item.setTextAlignment(Qt.AlignCenter);kd_item.setFont(bold);kd_item.setForeground(QBrush(QColor('#f0b429')))
            wins_item=QTableWidgetItem(codmunity_camo.format_count(wins));wins_item.setTextAlignment(Qt.AlignCenter)
            games_item=QTableWidgetItem(codmunity_camo.format_count(games));games_item.setTextAlignment(Qt.AlignCenter)
            self.board_table.setItem(row,2,kd_item);self.board_table.setItem(row,3,wins_item);self.board_table.setItem(row,4,games_item)
        if entries:self.board_status.setText(f'{len(entries)} cached entries shown, sorted by {dict(codmunity_camo.BOARD_METRICS).get(metric,metric)}.')
        else:self.board_status.setText('No cached entries for this board yet. Boards appear after the next stats import.')
    def _fetch_board_avatars(self):
        if os.environ.get('PYTEST_CURRENT_TEST') is not None:return
        urls=list({str(e.get('avatar') or '') for e in self._board_entries if e.get('avatar')})
        missing=[url for url in urls if url and url not in self._board_avatars]
        if not missing:return
        folder=E.DATA/'avatars';folder.mkdir(parents=True,exist_ok=True)
        import hashlib as _hashlib
        cached={url:folder/(_hashlib.sha256(url.encode()).hexdigest()[:24]+'.img') for url in missing}
        for url,path in cached.items():
            if path.is_file():
                pixmap=QPixmap(str(path))
                if not pixmap.isNull():self._board_avatars[url]=pixmap
        todo=[url for url in missing if url not in self._board_avatars]
        if not todo:return
        def work():
            from urllib.request import Request as _Request,urlopen as _urlopen
            _h={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) VintendoCODTracker','Referer':'https://codmunity.gg/'}
            saved=[]
            for url in todo[:25]:
                try:
                    with _urlopen(_Request(url,headers=_h),timeout=20) as response:body=response.read(2*1024*1024)
                    if not body:continue
                    path=cached[url];path.write_bytes(body);saved.append(url)
                except Exception:continue
            return saved
        def done(saved):
            for url in saved:
                pixmap=QPixmap(str(cached[url]))
                if not pixmap.isNull():self._board_avatars[url]=pixmap
            if saved and hasattr(self,'board_table'):self.refresh_board_table()
        self.async_task(work,done,lambda _message:None)
    def copy_profile_link(self):
        player=str(self.settings.values.get('player','vintendo') or 'vintendo').strip() or 'vintendo'
        QApplication.clipboard().setText(f'https://codmunity.gg/profile/{player}');self.log('Profile link copied to the clipboard.')
    def open_codmunity_site(self):
        QDesktopServices.openUrl(QUrl('https://codmunity.gg/'))
    def build_stats(self):
        l=self.page('Stats','Lifetime stats per game and mode from the cached snapshot, plus community benchmarks.')
        top=QHBoxLayout()
        top.addWidget(self.button('Refresh',self.refresh_stats))
        top.addWidget(self.button('Copy summary',self.copy_stats_summary,True))
        l.addLayout(top)
        grid=QGridLayout();grid.setSpacing(10)
        self.stats_cards={}
        for index,(key,caption) in enumerate((('kd','K / D RATIO'),('wins','WINS'),('games','GAMES'),('kills','KILLS'),('deaths','DEATHS'),('avg','AVG / GAME'),('best','BEST GAME'),('streak','BEST STREAK'),('spm','SCORE / MIN'),('time','TIME PLAYED'))):
            card=QFrame();card.setObjectName('dashboardPanel')
            box=QVBoxLayout(card);box.setContentsMargins(14,11,14,11);box.setSpacing(3)
            value=QLabel('—');value.setObjectName('historySummaryValue');box.addWidget(value)
            caption_label=QLabel(caption);caption_label.setObjectName('historySummaryCaption');box.addWidget(caption_label)
            grid.addWidget(card,index//5,index%5);self.stats_cards[key]=value
        l.addLayout(grid)
        self.stats_modes_label=QLabel('');self.stats_modes_label.setWordWrap(True);self.stats_modes_label.setObjectName('camoHint');l.addWidget(self.stats_modes_label)
        self.stats_benchmark_label=QLabel('');self.stats_benchmark_label.setWordWrap(True);self.stats_benchmark_label.setObjectName('camoHint');l.addWidget(self.stats_benchmark_label)
        self.stats_status=QLabel('');self.stats_status.setWordWrap(True);self.stats_status.setObjectName('camoStatusLine');l.addWidget(self.stats_status)
        l.addStretch(1)
        self.refresh_stats()
    def _select_lifetime_game(self,game,mode):
        self._lifetime_game=(game,mode)
        for group in (getattr(self,'stats_game_buttons',{}),getattr(self,'dash_profile_game_buttons',{})):
            for key,button in group.items():
                try:button.setChecked(tuple(key)==(game,mode))
                except (AttributeError,RuntimeError):pass
        self.refresh_stats()
    def _stats_selection(self):
        selection=getattr(self,'_lifetime_game',None)
        if isinstance(selection,tuple) and len(selection)==2:return selection
        return 'mw4','multiplayer'
    def _lifetime_stat_values(self,game=None,mode=None):
        if game is None or mode is None:game,mode=self._stats_selection()
        snapshot={}
        try:snapshot=self.store.meta('codmunity_stats') or {}
        except Exception:snapshot={}
        stats=codmunity_camo.lifetime_stats(snapshot,game,mode)
        kd=codmunity_camo.lifetime_kd(stats)
        games=stats.get('games_played',stats.get('games'))
        values={'kd':f'{kd:.2f}' if kd is not None else '\u2014',
                'wins':codmunity_camo.format_count(stats.get('wins')),
                'games':codmunity_camo.format_count(games),
                'kills':codmunity_camo.format_count(stats.get('kills')),
                'deaths':codmunity_camo.format_count(stats.get('deaths')),
                'avg':f"{stats['avg_kills_game']:.1f}" if isinstance(stats.get('avg_kills_game'),(int,float)) else (f"{stats.get('avg_elims')}" if stats.get('avg_elims') is not None else '\u2014'),
                'best':codmunity_camo.format_count(stats.get('highest_kills_game')),
                'streak':codmunity_camo.format_count(stats.get('highest_streak')),
                'spm':f"{stats['score_per_min']:.0f}" if isinstance(stats.get('score_per_min'),(int,float)) else '\u2014',
                'time':codmunity_camo.format_time(stats.get('time_played_s',stats.get('hours_played')*3600 if isinstance(stats.get('hours_played'),(int,float)) else None))}
        return game,mode,stats,values
    def refresh_stats(self):
        if not hasattr(self,'dash_profile_game_buttons'):return
        game,mode,stats,values=self._lifetime_stat_values()
        for key,label in self.stats_cards.items():label.setText(values.get(key,'\u2014'))
        wins_by_mode=stats.get('wins_by_mode') if isinstance(stats.get('wins_by_mode'),dict) else {}
        if wins_by_mode:self.stats_modes_label.setText('Wins by mode: '+', '.join(f'{k} {v}' for k,v in sorted(wins_by_mode.items())))
        else:self.stats_modes_label.setText('')
        try:snapshot=self.store.meta('codmunity_stats') or {}
        except Exception:snapshot={}
        benchmarks=(snapshot.get('benchmarks') or {}) if isinstance(snapshot,dict) else {}
        bench_key={'mw4':'mw4_multiplayer','warzone':'warzone_resurgence' if mode=='resurgence' else 'warzone_battle_royale'}.get(game,'mw4_multiplayer')
        bench=benchmarks.get(bench_key) or {}
        if isinstance(bench.get('kdMedian'),(int,float)) and isinstance(bench.get('kdP90'),(int,float)):
            self.stats_benchmark_label.setText(f"Community benchmark: median K/D {bench['kdMedian']:g} \u00b7 P90 {bench['kdP90']:g} (cached)")
        else:self.stats_benchmark_label.setText('')
        if stats:self.stats_status.setText(f"{len([v for v in values.values() if v != '\u2014'])} lifetime stats shown.")
        else:self.stats_status.setText('No lifetime stats cached for this game and mode yet.')
        self._refresh_dashboard_stats()
    def _refresh_dashboard_stats(self):
        if not hasattr(self,'dash_meters'):return
        try:game,mode,stats,values=self._lifetime_stat_values()
        except Exception:return
        self._refresh_dash_meters(game,mode,stats)
        self._refresh_dash_rank(game,mode,stats)

    @staticmethod
    def _set_meter(slot,text,sub,pct,med_text,ring_text=''):
        if not isinstance(slot,dict):return
        try:slot['value'].setText(text)
        except (AttributeError,RuntimeError):pass
        try:slot['sub'].setText(sub)
        except (AttributeError,RuntimeError):pass
        try:slot['bar'].set_value(pct or 0)
        except (AttributeError,RuntimeError):pass
        ring=slot.get('ring')
        if ring is not None:
            try:ring.set_value(pct or 0,ring_text)
            except (AttributeError,RuntimeError):pass
        if pct is None or med_text is None:caption='Refresh community data for comparison.'
        else:caption=f"Better than {pct:.0f}% of players \u00b7 Median {med_text}"
        try:slot['cap'].setText(caption)
        except (AttributeError,RuntimeError):pass
    def _dash_profile_page(self,step):
        try:pages=self.dash_profile_pages
        except (AttributeError,RuntimeError):return
        try:count=pages.count()
        except (AttributeError,RuntimeError):return
        if not count:return
        try:pages.setCurrentIndex((pages.currentIndex()+step)%count)
        except (AttributeError,RuntimeError):pass

    def _refresh_dash_meters(self,game,mode,stats):
        meters=getattr(self,'dash_meters',None)
        if not meters:return
        try:snapshot=self.store.meta('codmunity_public_distributions') or {}
        except Exception:snapshot={}
        dists=snapshot.get('|'.join((str(game),str(mode),'lifetime'))) if isinstance(snapshot,dict) else None
        dists=dists if isinstance(dists,dict) else {}
        kills=self._known_number(stats,'kills');deaths=self._known_number(stats,'deaths')
        wins=self._known_number(stats,'wins')
        raw_games=stats.get('games_played',stats.get('games'))
        games=float(raw_games) if isinstance(raw_games,(int,float)) and not isinstance(raw_games,bool) and raw_games>=0 else None
        kd=codmunity_camo.lifetime_kd(stats)
        losses=self._known_number(stats,'losses')
        if losses is None and wins is not None and games is not None:losses=games-wins
        wl=(wins/losses) if wins is not None and losses else None
        wr=(wins/games*100.0) if wins is not None and games else None
        def _pct(value,key):
            try:return codmunity_player_stats.percentile_of(value,dists.get(key))
            except Exception:return None
        def _med(key,scale=1.0,suffix=''):
            try:raw=codmunity_player_stats.median_of(dists.get(key))
            except Exception:return None
            return f"{raw*scale:g}{suffix}" if raw is not None else None
        kd_text=f"{kd:.2f}" if kd is not None else '\u2014'
        kd_sub=(f"{codmunity_camo.format_count(kills)} kills \u00b7 {codmunity_camo.format_count(deaths)} deaths" if kills is not None and deaths is not None else '\u2014')
        self._set_meter(meters.get('kd'),kd_text,kd_sub,_pct(kd,'kd') if kd is not None else None,_med('kd'))
        wl_text=f"{wl:.2f}" if wl is not None else '\u2014'
        wl_sub=(f"{codmunity_camo.format_count(wins)} wins \u00b7 {codmunity_camo.format_count(losses)} losses" if wins is not None and losses is not None else '\u2014')
        self._set_meter(meters.get('wl'),wl_text,wl_sub,_pct(wl,'wl') if wl is not None else None,_med('wl'))
        wr_text=f"{wr:.0f}%" if wr is not None else '\u2014'
        wr_sub=(f"{codmunity_camo.format_count(int(games))} games played" if games is not None else '\u2014')
        self._set_meter(meters.get('wr'),wr_text,wr_sub,_pct(wr/100.0 if wr is not None else None,'winRate') if wr is not None else None,_med('winRate',100.0,'%'),wr_text if wr is not None else '')

        tiles=getattr(self,'dash_profile_tiles',None)
        if tiles:
            best=self._known_number(stats,'highest_kills_game');streak=self._known_number(stats,'highest_streak')
            raw_spm=stats.get('score_per_min')
            spm=float(raw_spm) if isinstance(raw_spm,(int,float)) and not isinstance(raw_spm,bool) and raw_spm>=0 else None
            avg=None
            for akey in ('avg_kills_game','avg_elims'):
                cand=stats.get(akey)
                if isinstance(cand,(int,float)) and not isinstance(cand,bool) and cand>=0:avg=float(cand);break
            hours=None;hours_key=None
            raw_seconds=stats.get('time_played_s')
            if isinstance(raw_seconds,(int,float)) and not isinstance(raw_seconds,bool) and raw_seconds>=0:
                hours=float(raw_seconds)/3600.0;hours_key='timePlayedH' if 'timePlayedH' in dists else ('hoursPlayed' if 'hoursPlayed' in dists else None)
            else:
                raw_hours=stats.get('hours_played')
                if isinstance(raw_hours,(int,float)) and not isinstance(raw_hours,bool) and raw_hours>=0:
                    hours=float(raw_hours);hours_key='hoursPlayed' if 'hoursPlayed' in dists else ('timePlayedH' if 'timePlayedH' in dists else None)
            def _fmt_int(v):
                try:return codmunity_camo.format_count(int(v))
                except (TypeError,ValueError):return '\u2014'
            def _set_tile(key,text,pct):
                slot=tiles.get(key)
                if not isinstance(slot,dict):return
                try:slot['value'].setText(text)
                except (AttributeError,RuntimeError):pass
                try:
                    badge=slot.get('badge')
                    if badge is None:return
                    if pct is None:badge.setText('');badge.setVisible(False)
                    else:badge.setText(f"TOP {100.0-pct:.0f}%");badge.setVisible(True)
                except (AttributeError,RuntimeError):pass
            _set_tile('kills',_fmt_int(kills) if kills is not None else '\u2014',_pct(kills,'kills') if kills is not None else None)
            _set_tile('deaths',_fmt_int(deaths) if deaths is not None else '\u2014',None)
            _set_tile('wins',_fmt_int(wins) if wins is not None else '\u2014',None)
            _set_tile('losses',_fmt_int(losses) if losses is not None else '\u2014',None)
            _set_tile('games',_fmt_int(int(games)) if games is not None else '\u2014',None)
            if hours is None:time_text='\u2014'
            elif hours>=24:
                total_minutes=int(round(hours*60));time_text=f"{total_minutes//1440}d {(total_minutes%1440)//60}h"
            else:time_text=codmunity_camo.format_time(int(round(hours*3600)))
            _set_tile('time',time_text,_pct(hours,hours_key) if hours is not None and hours_key else None)
            _set_tile('streak',_fmt_int(streak) if streak is not None else '\u2014',None)
            _set_tile('best',_fmt_int(best) if best is not None else '\u2014',None)
            _set_tile('spm',f"{spm:.0f}" if spm is not None else '\u2014',_pct(spm,'spm') if spm is not None else None)
            avg_key=next((k for k in ('killsPerGame','avgElims') if k in dists),None)
            _set_tile('avg',f"{avg:.1f}" if avg is not None else '\u2014',_pct(avg,avg_key) if avg is not None and avg_key else None)

    def _maybe_fetch_distribution(self,game,mode):
        if os.environ.get('PYTEST_CURRENT_TEST') is not None:return
        try:player=self._codmunity_player_record()
        except Exception:player=None
        if player is None:return
        key='|'.join((str(game),str(mode),'lifetime'))
        fetching=getattr(self,'_dist_fetching',None)
        if fetching is None:fetching=self._dist_fetching=set()
        if key in fetching:return
        fetching.add(key)
        try:self.log(f'Loading community data ({game}/{mode}/lifetime).')
        except Exception:pass
        pid=str(player.get('id'))
        def done(distribution,pid=pid,key=key):
            try:
                if str(getattr(self,'current_player',None) or '')!=str(pid):return
                self.on_codmunity_distribution(pid,key,distribution)
            except Exception as exc:
                try:self.log('Community auto-refresh skipped: '+str(exc))
                except Exception:pass
            finally:
                try:self._refresh_dashboard_stats()
                except Exception:pass
        def failed(message,pid=pid,key=key):
            try:self.log('Community auto-refresh failed: '+str(message or 'unknown error'))
            except Exception:pass
        try:self.async_task(lambda:self.codmunity_client.community_distribution(game,mode,'lifetime'),done,failed)
        except Exception as exc:
            try:self.log('Community auto-refresh skipped: '+str(exc))
            except Exception:pass
    def _refresh_dash_rank(self,game,mode,stats):
        if not hasattr(self,'dash_rank_bar'):return
        try:kd=codmunity_camo.lifetime_kd(stats)
        except Exception:kd=None
        if kd is None:
            self.dash_rank_bar.set_rank(-1,0.0)
            try:self.dash_rank_current.setText('—')
            except (AttributeError,RuntimeError):pass
            try:self.dash_rank_next.setText('No lifetime K/D cached for this game yet.')
            except (AttributeError,RuntimeError):pass
            self._refresh_dash_profile_card(game,mode,stats,None)
            return
        try:snapshot=self.store.meta('codmunity_public_distributions') or {}
        except Exception:snapshot={}
        distribution=None
        if isinstance(snapshot,dict):
            saved=snapshot.get('|'.join((str(game),str(mode),'lifetime')))
            if isinstance(saved,dict) and isinstance(saved.get('kd'),list):
                try:distribution=codmunity_player_stats.CommunityDistribution.from_payload({'data':{'game':saved.get('game'),'mode':saved.get('mode'),'scope':saved.get('scope'),'sampled':saved.get('sampled'),'usersTotal':saved.get('users_total'),'dist':{'kd':saved.get('kd')}}})
                except (TypeError,ValueError):distribution=None
        if distribution is None or (str(distribution.game),str(distribution.mode))!=(str(game),str(mode)):
            self.dash_rank_bar.set_rank(-1,0.0)
            try:self.dash_rank_current.setText('—')
            except (AttributeError,RuntimeError):pass
            try:self.dash_rank_next.setText('Refresh community data to rank this K/D.')
            except (AttributeError,RuntimeError):pass
            self._refresh_dash_profile_card(game,mode,stats,None)
            self._maybe_fetch_distribution(game,mode)
            return
        try:rank=codmunity_player_stats.distribution_rank(kd,distribution)
        except Exception:rank=None
        if rank is None:
            self.dash_rank_bar.set_rank(-1,0.0)
            try:self.dash_rank_current.setText('—')
            except (AttributeError,RuntimeError):pass
            try:self.dash_rank_next.setText('Refresh community data to rank this K/D.')
            except (AttributeError,RuntimeError):pass
            self._refresh_dash_profile_card(game,mode,stats,None)
            self._maybe_fetch_distribution(game,mode)
            return
        tier_index=max(0,len(codmunity_player_stats.LADDER_TIERS)-1)
        for index,(_floor,tier_name) in enumerate(codmunity_player_stats.LADDER_TIERS):
            if tier_name==rank.tier:tier_index=index;break
        self.dash_rank_bar.set_rank(tier_index,(rank.progress_percent or 0)/100.0)
        try:self.dash_rank_current.setText(rank.tier)
        except (AttributeError,RuntimeError):pass
        try:
            if rank.next_tier is not None:self.dash_rank_next.setText(f"Next: {rank.next_tier} from {rank.next_tier_kd:.2f} K/D")
            else:self.dash_rank_next.setText('MAX RANK')
        except (AttributeError,RuntimeError):pass
        self._refresh_dash_profile_card(game,mode,stats,rank)

    def _refresh_dashboard_stats_view(self):
        if hasattr(self,'stats_game_buttons'):self.refresh_stats()
        else:self._refresh_dashboard_stats()
    def copy_stats_summary(self):
        game,mode=self._stats_selection()
        snapshot=self.board_snapshot() if hasattr(self,'board_snapshot') else {}
        try:snapshot=self.store.meta('codmunity_stats') or {}
        except Exception:snapshot={}
        line=codmunity_camo.summary_line(snapshot,game,mode)
        if not line:self.notice('No lifetime stats cached for this game and mode yet.');return
        QApplication.clipboard().setText(line);self.log('Stats summary copied to the clipboard.')
    def build_settings(self):
        l=self.page('Settings','CODMunity, Meta AI automation, and Discord settings in one place. Mutable files are stored under Windows AppData.')
        self.settings_tabs=QTabWidget();self.settings_tabs.addTab(self.solo_settings_panel,'CODMunity');l.addWidget(self.settings_tabs,1)
        self.sync_tab_page=QWidget();sync_tab_layout=QVBoxLayout(self.sync_tab_page);sync_tab_layout.setContentsMargins(20,16,20,16);sync_tab_layout.setSpacing(10)
        sync_header=QHBoxLayout();sync_title=QLabel('CODMunity Sync & Account');sync_title.setObjectName('title');sync_header.addWidget(sync_title);sync_header.addStretch()
        self.sync_header_status=QLabel('Native sync page — no external app.');self.sync_header_status.setStyleSheet('color:#99a8bc;font-weight:700;');sync_header.addWidget(self.sync_header_status);sync_tab_layout.addLayout(sync_header)
        if self.tab_prototype:
            sync_preview=QLabel('Preview uses a copy of your settings, match history, and roster photos. It will not change live tracker data. Tracking and Discord posting are disabled in this window.')
            sync_preview.setWordWrap(True);sync_preview.setStyleSheet('padding:9px 12px;background:#1b2330;border-left:3px solid #ed2938;color:#cbd3df;');sync_tab_layout.addWidget(sync_preview)
        self.build_codmunity_sync(sync_tab_layout)
        self.settings_tabs.addTab(self.sync_tab_page,'Sync & Account')
        self.settings_tabs.currentChanged.connect(self._settings_tab_changed)
        automation_tab=QWidget();automation_layout=QVBoxLayout(automation_tab);automation_layout.setContentsMargins(12,12,12,12);automation_layout.setSpacing(12);self.settings_tabs.addTab(automation_tab,'Automation & Chrome')
        self.poll=QSpinBox();self.poll.setRange(3,60);self.poll.setValue(int(self.settings.values.get('poll_seconds',5)))
        self.browser_timeout=QSpinBox();self.browser_timeout.setRange(10,180);self.browser_timeout.setSuffix(' sec');self.browser_timeout.setValue(int(self.settings.values.get('browser_navigation_timeout',60)))
        self.download_timeout=QSpinBox();self.download_timeout.setRange(5,120);self.download_timeout.setSuffix(' min');self.download_timeout.setValue(max(5,int(self.settings.values.get('browser_download_timeout',1800))//60))
        self.browser_retries=QSpinBox();self.browser_retries.setRange(0,3);self.browser_retries.setValue(int(self.settings.values.get('browser_retries',1)))
        f=QFormLayout();f.addRow('Polling interval (seconds)',self.poll);f.addRow('Meta page navigation timeout',self.browser_timeout);f.addRow('Wait for image download',self.download_timeout);f.addRow('Browser/navigation retries',self.browser_retries);f.addRow('Application data',QLabel(str(E.DATA)));automation_layout.addLayout(f)
        self.row(automation_layout,self.button('Open data folder',lambda:os.startfile(str(E.DATA))),self.button('Open logs folder',lambda:os.startfile(str(E.DATA))))
        self.cache_size_label=QLabel('');self.cache_size_label.setWordWrap(True);self.cache_size_label.setStyleSheet('color:#99a8bc')
        self.row(automation_layout,self.cache_size_label,self.button('Clear downloaded image caches',self.clear_image_caches))
        self.refresh_cache_size_label()
        automation_layout.addWidget(QLabel('Meta poster and video jobs run in a small in-app browser using the tracker profile. Sign in once with Log into Meta AI in the sidebar; no extension install or pairing token is needed.'))
        browser_mode_note=QLabel('Tracker Chrome profile: the dedicated profile keeps the Meta AI login isolated from your own Chrome; attaching reuses a Meta AI tab you already have open in your own Chrome.');browser_mode_note.setWordWrap(True);automation_layout.addWidget(browser_mode_note)
        self.browser_mode_combo=QComboBox();self.browser_mode_combo.addItems(['Dedicated tracker profile (recommended)','Use my already-open Chrome']);self.browser_mode_combo.setCurrentIndex(1 if self.tracker_browser_mode()=='attach' else 0);browser_mode_form=QFormLayout();browser_mode_form.addRow('Tracker Chrome profile',self.browser_mode_combo);automation_layout.addLayout(browser_mode_form)
        automation_layout.addStretch()
        poster_tab=QWidget();poster_layout=QVBoxLayout(poster_tab);poster_layout.setContentsMargins(12,12,12,12);poster_layout.setSpacing(12)
        poster_note=QLabel('Poster automation runs in the background. These switches apply to every finished match.');poster_note.setWordWrap(True);poster_note.setStyleSheet('color:#99a8bc');poster_layout.addWidget(poster_note)
        self.include_stats_checkbox=QCheckBox('Include player Ladder Rank + match stats in prompt');self.include_stats_checkbox.setChecked(bool(self.settings.values.get('include_stats',True)));self.include_stats_checkbox.setToolTip('Adds verified rank and match lines from the Player Stats Service. Unverified players are never included.');self.include_stats_checkbox.setStyleSheet('color:#c4ccd8;font-weight:700;');poster_layout.addWidget(self.include_stats_checkbox)
        self.auto_post=QCheckBox('Automatically post completed posters to Discord');self.auto_post.setChecked(bool(self.settings.values.get('auto_post',True)));self.auto_post.setStyleSheet('color:#c4ccd8;font-weight:700;');poster_layout.addWidget(self.auto_post)
        self.full_auto=QCheckBox('Full Auto: finished match → Meta AI picture → Discord, no clicks');self.full_auto.setChecked(bool(self.settings.values.get('full_auto',True)));self.full_auto.stateChanged.connect(lambda _state:self._toggle_full_auto());self.full_auto.setStyleSheet('color:#c4ccd8;font-weight:700;');poster_layout.addWidget(self.full_auto)
        poster_layout.addStretch(1)
        self.settings_tabs.addTab(poster_tab,'Poster Automation')
        self.settings_tabs.addTab(self.discord_settings_panel,'Discord')
        hotkeys_tab=QWidget();hotkeys_layout=QVBoxLayout(hotkeys_tab);hotkeys_layout.setContentsMargins(12,12,12,12);hotkeys_layout.setSpacing(12)
        hotkeys_note=QLabel('Global hotkeys work while the tracker is minimized. Click a box, then press the new keys (a modifier plus another key, or a bare key).');hotkeys_note.setWordWrap(True);hotkeys_layout.addWidget(hotkeys_note)
        score_vk,score_mod=self.hotkey_setting('scoreboard_hotkey_vk','scoreboard_hotkey_mod',DEFAULT_SCOREBOARD_HOTKEY)
        self.scoreboard_hotkey_edit=HotkeyEdit(score_vk,score_mod);self.screenshot_hotkey_edit=self.scoreboard_hotkey_edit
        hotkey_form=QFormLayout();hotkey_form.addRow('Capture screenshot + scoreboard hotkey',self.scoreboard_hotkey_edit);hotkeys_layout.addLayout(hotkey_form)
        self.row(hotkeys_layout,self.button('Reset hotkeys to defaults',self.reset_hotkeys))
        folders_note=QLabel('Media folders. Leave empty to keep everything inside the application data folder.');folders_note.setWordWrap(True);hotkeys_layout.addWidget(folders_note)
        self.screenshots_dir_edit=self.edit(str(self.settings.values.get('screenshots_dir') or ''));self.screenshots_dir_edit.setPlaceholderText(str(self.screenshots_dir()))
        self.videos_dir_edit=self.edit(str(self.settings.values.get('videos_dir') or ''));self.videos_dir_edit.setPlaceholderText(str(self.videos_dir()))
        folder_form=QFormLayout();folder_form.addRow('Screenshots folder',self.screenshots_dir_edit);folder_form.addRow('Videos folder',self.videos_dir_edit);hotkeys_layout.addLayout(folder_form)
        self.row(hotkeys_layout,self.button('Browse screenshots folder',lambda:self.pick_media_dir(self.screenshots_dir_edit)),self.button('Browse videos folder',lambda:self.pick_media_dir(self.videos_dir_edit)),self.button('Reset folders to defaults',self.reset_media_dirs))
        hotkeys_layout.addStretch()
        self.settings_tabs.addTab(hotkeys_tab,'Hotkeys & Folders')

        self.save_settings_button=self.button('Save all settings',self.save_settings,True)
        self.check_updates_button=self.button('Check for Updates',lambda:self.check_for_app_update(manual=True))
        settings_corner=QWidget();settings_corner_layout=QHBoxLayout(settings_corner);settings_corner_layout.setContentsMargins(0,0,4,0);settings_corner_layout.setSpacing(8)
        settings_corner_layout.addWidget(self.save_settings_button);settings_corner_layout.addWidget(self.check_updates_button)
        self.settings_tabs.setCornerWidget(settings_corner,Qt.TopRightCorner)
        self.update_version_label=QLabel(f'Installed version {APP_VERSION}. Updates come from GitHub Releases.');self.update_version_label.setStyleSheet('color:#99a8bc');l.addWidget(self.update_version_label)
        self.codmunity_stats_frame=QFrame();self.codmunity_stats_frame.setObjectName('dashboardPanel')
        stats_frame_layout=QVBoxLayout(self.codmunity_stats_frame);stats_frame_layout.setContentsMargins(0,0,0,0)
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView
            self.codmunity_stats_view=QWebEngineView(self.codmunity_stats_frame);self.codmunity_stats_view.setMinimumHeight(320)
            stats_frame_layout.addWidget(self.codmunity_stats_view)
        except Exception:
            self.codmunity_stats_view=None
            stats_frame_fallback=QLabel('Stats preview unavailable: Qt WebEngine is not installed.');stats_frame_fallback.setWordWrap(True)
            stats_frame_layout.addWidget(stats_frame_fallback)
        l.addWidget(self.codmunity_stats_frame,1)
        self._refresh_codmunity_stats_frame()

    @staticmethod
    def _codmunity_stats_url(username):
        from urllib.parse import quote
        name=str(username or '').strip().lstrip('@')
        if not name:return ''
        return 'https://codmunity.gg/profile/'+quote(name)+'/stats'
    STATS_FRAME_EMPTY_HTML='<html><body style="background:#0a0d13;color:#8b93a1;font-family:sans-serif;padding:40px;text-align:center;">Enter your CODMunity Name above to preview your public stats page here.</body></html>'
    def _refresh_codmunity_stats_frame(self):
        try:view=self.codmunity_stats_view
        except AttributeError:view=None
        if view is None:return
        url=self._codmunity_stats_url(self.player.text() if hasattr(self,'player') else '')
        if not url:
            try:view.setHtml(self.STATS_FRAME_EMPTY_HTML)
            except (AttributeError,RuntimeError):pass
            return
        try:
            if view.url().toString()!=url:view.setUrl(QUrl(url))
        except (AttributeError,RuntimeError,NameError):pass
    def refresh_cache_size_label(self):
        if not hasattr(self,'cache_size_label'):return
        try:sizes=codmunity_camo.cache_sizes(E.DATA)
        except Exception:sizes={}
        total=sum(sizes.values())
        if total>=1048576:text=f'{total/1048576:.1f} MB'
        elif total>=1024:text=f'{total/1024:.0f} KB'
        else:text=f'{total} B'
        detail=', '.join(f'{k} {v//1024} KB' for k,v in sorted(sizes.items()) if v) or 'empty'
        self.cache_size_label.setText(f'Downloaded images: {text} ({detail}). Boards, camos and avatars re-download on demand.')
    def clear_image_caches(self):
        try:removed=codmunity_camo.clear_caches(E.DATA)
        except Exception as exc:self.notice('Could not clear caches: '+str(exc));return
        try:self._board_avatars={}
        except (AttributeError,RuntimeError):pass
        try:self._camo_images={}
        except (AttributeError,RuntimeError):pass
        self.refresh_cache_size_label();self.log(f'Cleared {removed} cached image file(s).')
    def media_dir(self,key,default_name):
        custom=str(self.settings.values.get(key) or '').strip()
        folder=Path(custom).expanduser() if custom else E.DATA/default_name
        folder.mkdir(parents=True,exist_ok=True);return folder
    def screenshots_dir(self):return self.media_dir('screenshots_dir','Screenshots')
    def videos_dir(self):return self.media_dir('videos_dir','Cinematics')
    def hotkey_setting(self,vk_key,mod_key,default):
        try:vk=int(self.settings.values.get(vk_key,default[0]))
        except (TypeError,ValueError):vk=default[0]
        try:mod=int(self.settings.values.get(mod_key,default[1]))
        except (TypeError,ValueError):mod=default[1]
        return vk,mod&(MOD_CONTROL|MOD_SHIFT|MOD_ALT)
    def hotkey_text(self,kind):
        vk,mod=self.hotkey_setting('scoreboard_hotkey_vk','scoreboard_hotkey_mod',DEFAULT_SCOREBOARD_HOTKEY)
        return format_hotkey(vk,mod)
    def refresh_hotkey_labels(self):
        score=self.hotkey_text('scoreboard')
        if hasattr(self,'hotkey_hint'):self.hotkey_hint.setText(f'At match end, press {score} while the scoreboard is visible.')
        if hasattr(self,'capture_subtitle'):self.capture_subtitle.setText(self._scoreboard_capture_instruction())
    def open_hotkey_dialog(self,kind):
        is_scoreboard=kind=='scoreboard'
        vk_key,mod_key,default=('scoreboard_hotkey_vk','scoreboard_hotkey_mod',DEFAULT_SCOREBOARD_HOTKEY)
        vk,mod=self.hotkey_setting(vk_key,mod_key,default)
        dialog=QDialog(self);dialog.setWindowTitle('Capture hotkey');dialog.setModal(True)
        layout=QVBoxLayout(dialog);layout.setContentsMargins(18,18,18,18);layout.setSpacing(12)
        hint=QLabel('Click the box and press the new keys, then Save. Applies immediately; no need to visit Settings.');hint.setWordWrap(True);layout.addWidget(hint)
        edit=HotkeyEdit(vk,mod,dialog,confirm=False);layout.addWidget(edit)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        center=self.mapToGlobal(self.rect().center());dialog.adjustSize();dialog.move(center.x()-dialog.width()//2,center.y()-dialog.height()//2)
        if dialog.exec()!=QDialog.Accepted:return
        self.settings.values.update({vk_key:edit.vk,mod_key:edit.mod,screenshot_hotkey_vk:edit.vk,screenshot_hotkey_mod:edit.mod});self.settings.save()
        if hasattr(self,'scoreboard_hotkey_edit') and hasattr(self,'screenshot_hotkey_edit'):
            self.scoreboard_hotkey_edit.set_hotkey(edit.vk,edit.mod)
        self.refresh_hotkey_labels();self.unregister_global_hotkeys();self.register_global_hotkeys()
    def register_global_hotkeys(self):

        self.hotkey_filter=None;self.hotkey_registered=False;self.screenshot_hotkey_registered=False
        if os.name!='nt':
            self.hotkey_status.setText('Global hotkey unavailable on this platform; use Capture & OCR.')
            self.cod_ai_hotkey_status.setText('GLOBAL HOTKEY UNAVAILABLE · use Capture game screenshot now')
            return
        try:
            callbacks={}
            if not self.tab_prototype:callbacks[SCOREBOARD_HOTKEY_ID]=self.capture_scoreboard_hotkey
            self.hotkey_filter=ScoreboardHotkeyFilter(callbacks)
            QApplication.instance().installNativeEventFilter(self.hotkey_filter)
            score_vk,score_mod=self.hotkey_setting('scoreboard_hotkey_vk','scoreboard_hotkey_mod',DEFAULT_SCOREBOARD_HOTKEY)
            shot_vk,shot_mod=score_vk,score_mod
            score_text=format_hotkey(score_vk,score_mod);shot_text=score_text
            self.hotkey_registered=False;scoreboard_error=0
            if not self.tab_prototype:
                self.hotkey_registered=bool(ctypes.windll.user32.RegisterHotKey(None,SCOREBOARD_HOTKEY_ID,score_mod|MOD_NOREPEAT,score_vk))
                scoreboard_error=0 if self.hotkey_registered else ctypes.windll.kernel32.GetLastError()
            elif hasattr(self,'hotkey_status'):
                self.hotkey_status.setText('Scoreboard OCR hotkey disabled in CODMunity tab preview mode.')
                self.hotkey_status.setStyleSheet('color:#aab4c3')
            self.screenshot_hotkey_registered=self.hotkey_registered
            screenshot_error=scoreboard_error
            if self.hotkey_registered:
                self.hotkey_status.setText('Set Capture Hotkey  '+chr(0xB7)+'  '+score_text);self.hotkey_status.setObjectName('hotkeyPillShort');self.hotkey_status.setAlignment(Qt.AlignCenter);self.hotkey_status.setStyleSheet('color:#56e99a;font-weight:800;')
            elif self.tab_prototype:
                self.hotkey_status.setText('Scoreboard OCR hotkey disabled in CODMunity tab preview mode.');self.hotkey_status.setStyleSheet('color:#aab4c3')
            else:
                reason='key combination is already in use' if scoreboard_error==1409 else f'Windows error {scoreboard_error}'
                self.hotkey_status.setText('HOTKEY UNAVAILABLE  ·  '+reason+'  ·  use Capture & OCR');self.hotkey_status.setObjectName('');self.hotkey_status.setStyleSheet('color:#ffb15e;font-weight:800;')
            if self.screenshot_hotkey_registered:
                self.cod_ai_hotkey_status.setText('GLOBAL HOTKEY ACTIVE  '+chr(0xB7)+'  '+shot_text);self.cod_ai_hotkey_status.setStyleSheet('color:#56e99a;font-weight:800;')
            else:
                reason='another application already registered '+shot_text if screenshot_error==1409 else f'Windows error {screenshot_error}'
                self.cod_ai_hotkey_status.setText('HOTKEY UNAVAILABLE  ·  '+reason+'  ·  use Capture game screenshot now')
                self.cod_ai_hotkey_status.setStyleSheet('color:#ffb15e;font-weight:800;')
                self.cod_ai_hotkey_status.setToolTip('Windows RegisterHotKey error '+str(screenshot_error))
            if not self.hotkey_registered and not self.screenshot_hotkey_registered:
                QApplication.instance().removeNativeEventFilter(self.hotkey_filter);self.hotkey_filter=None
        except Exception as exc:
            if self.hotkey_filter:QApplication.instance().removeNativeEventFilter(self.hotkey_filter)
            self.hotkey_filter=None;self.hotkey_status.setObjectName('');self.hotkey_status.setText('HOTKEY UNAVAILABLE  ·  use Capture & OCR')
            self.hotkey_status.setToolTip(str(exc));self.hotkey_status.setStyleSheet('color:#ffb15e;font-weight:800;')
            self.cod_ai_hotkey_status.setText('HOTKEY UNAVAILABLE  ·  '+str(exc));self.cod_ai_hotkey_status.setStyleSheet('color:#ffb15e;font-weight:800;')

    def capture_scoreboard_hotkey(self):
        self.log(f'{self.hotkey_text("scoreboard")} pressed. Saving a screenshot and preparing scoreboard OCR…')
        self.capture_once(hotkey=True,save_screenshot=True)

    def _show_capture_review(self):
        self.nav.setCurrentRow(3);self.showNormal();self.raise_();self.activateWindow()

    def unregister_global_hotkeys(self):
        if getattr(self,'hotkey_registered',False):
            try:ctypes.windll.user32.UnregisterHotKey(None,SCOREBOARD_HOTKEY_ID)
            except Exception:pass
            self.hotkey_registered=False
        if getattr(self,'screenshot_hotkey_registered',False):
            try:ctypes.windll.user32.UnregisterHotKey(None,COD_AI_SCREENSHOT_HOTKEY_ID)
            except Exception:pass
            self.screenshot_hotkey_registered=False
        if getattr(self,'hotkey_filter',None):
            try:QApplication.instance().removeNativeEventFilter(self.hotkey_filter)
            except Exception:pass
            self.hotkey_filter=None
    def refresh_talent_friends(self):
        box=getattr(self,'talent_friends_box',None)
        if box is None:return
        while box.count()>0:
            child=box.takeAt(0);widget=child.widget()
            if widget is not None:widget.deleteLater()
        self.talent_friend_tiles={}
        eligible=[]
        for player in self.store.roster():
            if not player.get('enabled',True):continue
            if not player.get('consent_meta',False):continue
            photo=str(player.get('photo') or '')
            if not photo or not Path(photo).is_file():continue
            eligible.append(player)
        valid={str(player.get('id')) for player in eligible}
        self.talent_friend_ids=[pid for pid in (getattr(self,'talent_friend_ids',[]) or []) if pid in valid][:3]
        own=self._own_roster_id()
        if own and self._own_cameo_present():self.talent_friend_ids=[pid for pid in self.talent_friend_ids if pid!=own]
        chosen=set(self.talent_friend_ids)
        if not eligible and hasattr(self,'talent_friends_status'):self.talent_friends_status.setText('No eligible friends \u2014 roster players need a headshot and Meta AI consent.')
        for position,player in enumerate(eligible):
            pid=str(player.get('id'));name=str(player.get('display_name') or player.get('name',''))
            tile=FriendTile(pid,self.on_talent_friend_toggled)
            tile.name.setText(name);tile.setToolTip(name)
            try:self.show_image(tile.photo,str(player.get('photo')),64,64)
            except Exception:tile.photo.setText('No photo')
            tile.set_selected(pid in chosen)
            box.addWidget(tile,position//4,position%4)
            self.talent_friend_tiles[pid]=tile
        self.update_talent_friends_status()
    def _own_roster_id(self):
        try:me=str(self.settings.values.get('player','') or '').casefold()
        except Exception:return ''
        if not me:return ''
        try:roster=self.store.roster()
        except Exception:return ''
        for player in roster or []:
            names=[str(player.get('display_name') or ''),str(player.get('name') or '')]+[str(alias) for alias in (player.get('aliases') or [])]
            if any(name and name.casefold()==me for name in names):return str(player.get('id'))
        return ''
    def _own_cameo_present(self):
        try:return (self.videos_dir()/'talent.png').is_file()
        except Exception:return False
    def on_talent_friend_toggled(self,pid):
        if not pid:return
        own=self._own_roster_id()
        if own and str(pid)==own and self._own_cameo_present() and pid not in (getattr(self,'talent_friend_ids',[]) or []):
            if hasattr(self,'cinematic_status'):self.cinematic_status.setText('You are already in this video \u2014 the talent photo is you.')
            return
        chosen=[pid for pid in (getattr(self,'talent_friend_ids',[]) or [])]
        if pid in chosen:chosen.remove(pid)
        else:
            chosen.append(pid)
            if len(chosen)>3:
                chosen=chosen[:3]
                if hasattr(self,'cinematic_status'):self.cinematic_status.setText('Max of 3 friends per video (plus you).')
        self.talent_friend_ids=chosen
        for other_pid,tile in (getattr(self,'talent_friend_tiles',{}) or {}).items():
            try:tile.set_selected(other_pid in chosen)
            except RuntimeError:pass
        self.update_talent_friends_status()
    def update_talent_friends_status(self):
        if not hasattr(self,'talent_friends_status'):return
        count=len(getattr(self,'talent_friend_ids',[]) or [])
        self.talent_friends_status.setText((('No friends \u2014 just you' if not count else str(count)+'/3 friends selected')+' (Max Of 3 Can be used)'))
        if hasattr(self,'talent_cameo_label'):
            wanted=set(getattr(self,'talent_friend_ids',[]) or [])
            names=[str(player.get('display_name') or player.get('name','')) for player in self.store.roster() if str(player.get('id')) in wanted]
            self.talent_cameo_label.setText('Featuring: '+', '.join(names) if names else 'Featuring: just you')
        self.refresh_talent_cameo()
    def refresh_talent_cameo(self):
        strip=getattr(self,'talent_cameo_strip',None)
        if strip is None:return
        while strip.count()>0:
            child=strip.takeAt(0)
            widget=child.widget()
            if widget is not None:widget.deleteLater()
        for friend in self.selected_talent_friends():
            cell=QVBoxLayout();cell.setContentsMargins(0,0,0,0);cell.setSpacing(2)
            thumb=QLabel();thumb.setAlignment(Qt.AlignCenter);thumb.setFixedSize(56,56)
            thumb.setStyleSheet('background:#080a0e;border:1px solid #f97316;border-radius:6px;color:#77818f;')
            try:self.show_image(thumb,str(friend.get('photo')),56,56)
            except Exception:thumb.setText('No photo')
            caption=QLabel(str(friend.get('display_name') or friend.get('name','')))
            caption.setAlignment(Qt.AlignCenter);caption.setWordWrap(True)
            caption.setStyleSheet('font-size:10px;font-weight:700;color:#c4ccd8;')
            cell.addWidget(thumb,0,Qt.AlignHCenter);cell.addWidget(caption)
            holder=QWidget();holder.setLayout(cell);strip.addWidget(holder)
        strip.addStretch(1)
    def selected_talent_friends(self):
        wanted=set(getattr(self,'talent_friend_ids',[]) or [])
        friends=[]
        for player in self.store.roster():
            if str(player.get('id')) in wanted and player.get('enabled',True) and player.get('consent_meta',False):
                photo=str(player.get('photo') or '')
                if photo and Path(photo).is_file():friends.append(player)
        return friends[:3]

    def set_cinematic_talent(self):
        try:
            chosen,_=QFileDialog.getOpenFileName(self,'Choose talent reference photo',str(Path.home()),'Images (*.png *.jpg *.jpeg *.webp)')
            if not chosen:return
            from PIL import Image
            source=Path(chosen)
            if source.stat().st_size>20*1024*1024:self.cinematic_status.setText('Talent photo exceeds the 20 MB limit.');return
            with Image.open(source) as image:image.verify()
            folder=self.videos_dir();folder.mkdir(parents=True,exist_ok=True)
            target=folder/'talent.png'
            with Image.open(source) as image:image.convert('RGB').save(target,format='PNG')
            self.cinematic_status.setText('Talent photo saved. It will be attached to every future cinematic video as the featured-person reference.')
            self.refresh_talent_preview()
        except Exception as exc:self.cinematic_status.setText('Talent photo could not be saved: '+str(exc)[:200])
    def remove_cinematic_talent(self):
        target=self.videos_dir()/'talent.png'
        if not target.is_file():
            self.cinematic_status.setText('No talent photo is set.');return
        if not self.ask('Remove the talent photo? Future videos will generate without an appearance reference.'):return
        try:target.unlink()
        except OSError as exc:self.cinematic_status.setText('Could not remove the talent photo: '+str(exc)[:200]);return
        self.refresh_talent_preview()
        self.cinematic_status.setText('Talent photo removed. Future videos will generate without an appearance reference.')
    def confirm_cinematic_delete(self):
        box=QMessageBox(self);box.setWindowTitle('Delete cinematic video');box.setText('Delete this cinematic video?')
        box.setInformativeText('This removes the MP4 from disk. The queue entry stays for history.')
        delete_button=box.addButton('Delete',QMessageBox.DestructiveRole);box.addButton('Cancel',QMessageBox.RejectRole)
        box.setDefaultButton(box.buttons()[-1]);box.exec();return box.clickedButton() is delete_button
    def delete_cinematic_video(self,cid,path):
        """Delete exactly the video identified by cid/path (captured at click time)."""
        if not cid or not path:
            self.cinematic_status.setText('Select a finished cinematic video first, then delete it.');return False
        if not Path(path).is_file():
            self.store.update_cinematic(cid,video='')
            self._clear_cinematic_preview_if(cid)
            QTimer.singleShot(0,self.refresh_cinematic_results)
            self.cinematic_status.setText('The video file is already gone; the queue entry was cleared.');return True
        if not self.confirm_cinematic_delete():return False
        try:
            player=getattr(self,'cinematic_player',None)
            if player is not None and str(player.source().toString() or '')==QUrl.fromLocalFile(str(path)).toString():player.stop();player.setSource(QUrl())
            elif player is not None and not str(player.source().toString() or ''):player.stop()
        except Exception:pass
        try:Path(path).unlink()
        except OSError as exc:self.cinematic_status.setText('Could not delete the video: '+str(exc)[:200]);return False
        self.store.update_cinematic(cid,video='')
        self._clear_cinematic_preview_if(cid)
        QTimer.singleShot(0,self.refresh_cinematic_results)
        self.cinematic_status.setText('Deleted '+Path(path).name+'.');return True
    def _clear_cinematic_preview_if(self,cid):
        if not hasattr(self,'cinematic_results'):return
        current=self.cinematic_results.currentItem()
        if not current or str(current.data(Qt.UserRole))!=str(cid):return
        self.cinematic_results.blockSignals(True);self.cinematic_results.clearSelection();self.cinematic_results.setCurrentRow(-1);self.cinematic_results.blockSignals(False)
        self.preview_cinematic_video(None)
    def delete_selected_cinematic_video(self):
        item=self.cinematic_results.currentItem() if hasattr(self,'cinematic_results') else None
        cid=item.data(Qt.UserRole) if item else ''
        path=item.data(Qt.UserRole+1) if item else ''
        self.delete_cinematic_video(cid,path)
    def refresh_talent_preview(self):
        if not hasattr(self,'talent_preview'):return
        target=self.videos_dir()/'talent.png'
        if target.is_file():
            pix=QPixmap(str(target))
            if not pix.isNull():
                self.talent_preview.setPixmap(pix.scaled(60,60,Qt.KeepAspectRatio,Qt.SmoothTransformation))
                self.talent_info.setText('talent.png')
                if hasattr(self,'talent_dot'):self.talent_dot.show()
                return
        self.talent_preview.setPixmap(QPixmap());self.talent_preview.setText('No photo')
        self.talent_info.setText('No talent photo set')
        if hasattr(self,'talent_dot'):self.talent_dot.hide()
    def ensure_cinematic_thumbs(self):
        if not hasattr(self,'cinematic_results'):return
        pending=getattr(self,'_thumb_pending',None)
        if pending is None:pending=self._thumb_pending=set()
        try:rows=self.store.recent_cinematics(30)
        except Exception:return
        jobs=[]
        for row in rows or []:
            if str((row or {}).get('status') or '')!='completed':continue
            video=str((row or {}).get('video') or '')
            if not video or not Path(video).is_file():continue
            cid=str(row.get('id'))
            if (self.videos_dir()/(cid+'_thumb.jpg')).is_file() or cid in pending:continue
            shot=str((row or {}).get('image') or '')
            if shot and Path(shot).is_file():continue
            jobs.append((video,str(self.videos_dir()/(cid+'_thumb.jpg')),cid))
        if not jobs:return
        for _,_,cid in jobs:pending.add(cid)
        import shutil
        exe=shutil.which('ffmpeg')
        if not exe:
            for _,_,cid in jobs:pending.discard(cid)
            return
        def make():
            import subprocess
            made=[]
            for video,thumb,cid in jobs:
                try:
                    subprocess.run([exe,'-y','-v','error','-ss','1','-i',video,'-frames:v','1','-vf','scale=336:-2',thumb],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=120,creationflags=NO_CONSOLE_FLAG)
                    if Path(thumb).is_file():made.append(cid)
                except Exception:pass
                finally:pending.discard(cid)
            return made
        self.async_task(make,lambda made:self.refresh_cinematic_results() if made else None)
    def open_cinematics_folder(self):
        folder=self.videos_dir();folder.mkdir(parents=True,exist_ok=True);os.startfile(str(folder))
    def update_screenshot_grid(self):
        """Force exactly three screenshot columns at the current tab width."""
        if not hasattr(self,'cod_ai_screenshots'):return
        gallery=self.cod_ai_screenshots
        width=gallery.viewport().width()
        if width<240:width=max(gallery.width(),660)
        tile_w=max(110,(width-28)//3)
        tile_h=int((tile_w-12)*9/16)+34
        gallery.setGridSize(QSize(tile_w,tile_h));gallery.setIconSize(QSize(tile_w-16,int((tile_w-16)*9/16)))
        for index in range(gallery.count()):
            item=gallery.item(index)
            if item:item.setSizeHint(QSize(tile_w,tile_h))
    def update_media_count(self):
        shots=len(getattr(self,'_shot_paths',[]) or [])
        videos=len(getattr(self,'_video_rows',[]) or [])
        if hasattr(self,'media_tabs'):
            self.media_tabs.setTabText(0,f"Screenshots ({shots})")
            self.media_tabs.setTabText(1,f"Finished Videos ({videos})")
    def _sync_screenshot_tiles(self):
        if not hasattr(self,'cod_ai_screenshots'):return
        current=self.cod_ai_screenshots.currentItem()
        current_path=str(current.data(Qt.UserRole)) if current else ''
        for path,tile in getattr(self,'screenshot_tiles',{}).items():tile.set_selected(str(path)==current_path)
    def select_cod_ai_screenshot(self,path):
        if not hasattr(self,'cod_ai_screenshots'):return
        for index in range(self.cod_ai_screenshots.count()):
            item=self.cod_ai_screenshots.item(index)
            if item and str(item.data(Qt.UserRole))==str(path):self.cod_ai_screenshots.setCurrentItem(item);return
    def update_cinematic_grid(self):
        """Force exactly three gallery columns at the current gallery width."""
        if not hasattr(self,'cinematic_results'):return
        gallery=self.cinematic_results
        width=gallery.viewport().width()
        if width<240:width=max(gallery.width(),660)
        tile_w=max(110,(width-28)//3)
        tile_h=int((tile_w-12)*9/16)+34
        gallery.setGridSize(QSize(tile_w,tile_h));gallery.setIconSize(QSize(tile_w-16,int((tile_w-16)*9/16)))
        for index in range(gallery.count()):
            item=gallery.item(index)
            if item:item.setSizeHint(QSize(tile_w,tile_h))
    def _sync_cinematic_tiles(self):
        if not hasattr(self,'cinematic_results'):return
        current=self.cinematic_results.currentItem()
        current_id=str(current.data(Qt.UserRole)) if current else ''
        for cid,tile in getattr(self,'cinematic_tiles',{}).items():tile.set_selected(str(cid)==current_id)
    def select_cinematic_video(self,cid):
        if not hasattr(self,'cinematic_results'):return
        for index in range(self.cinematic_results.count()):
            item=self.cinematic_results.item(index)
            if item and str(item.data(Qt.UserRole))==str(cid):self.cinematic_results.setCurrentItem(item);return
    def cinematic_duration(self,path):
        cache=self.__dict__.setdefault('cinematic_duration_cache',{})
        try:key=(str(path),Path(path).stat().st_mtime,Path(path).stat().st_size)
        except OSError:return ''
        if key not in cache:
            seconds=probe_cinematic_duration(path);cache[key]=format_cinematic_duration(seconds) if seconds else ''
            if len(cache)>64:cache.clear()
        return cache.get(key,'')
    def exit_cinematic_fullscreen(self):
        widget=getattr(self,'cinematic_video_widget',None)
        if widget is not None and widget.isFullScreen():widget.setFullScreen(False)
    def toggle_cinematic_fullscreen(self):
        widget=getattr(self,'cinematic_video_widget',None)
        if widget is None:return
        entering=not widget.isFullScreen();widget.setFullScreen(entering)
        if entering:
            try:widget.setFocus(Qt.OtherFocusReason)
            except Exception:pass
    def refresh_cinematic_results(self):
        if not hasattr(self,'cinematic_results'):return
        current=self.cinematic_results.currentItem();current_id=current.data(Qt.UserRole) if current else None
        previous_video=current.data(Qt.UserRole+1) if current else ''
        rows=visible_cinematic_rows(self.store.recent_cinematics(30),current_id);self.update_cinematic_grid();self.cinematic_results.blockSignals(True);self.cinematic_results.clear()
        self.cinematic_tiles={}
        cache=self.__dict__.setdefault('cinematic_thumb_cache',{})
        for row in rows:
            video=str(row.get('video') or '')
            label_source=Path(video).name if video else str(row.get('filename') or row['id'])
            item=QListWidgetItem();item.setData(Qt.UserRole,row['id']);item.setData(Qt.UserRole+1,row.get('video',''));item.setToolTip(f"{row['id']} \u00b7 {row['status']}")
            thumb=cinematic_thumbnail_source(row,self.videos_dir()/(str(row.get('id'))+'_thumb.jpg'))
            tile_pixmap=None
            if thumb and Path(thumb).is_file():
                pix=QPixmap(thumb)
                if not pix.isNull():tile_pixmap=pix
            badge=''
            if video and Path(video).is_file():
                duration=self.cinematic_duration(video)
                badge=(chr(9654)+' '+duration) if duration else chr(9654)
            elif row.get('status'):badge=str(row.get('status'))
            tile=CinematicTile(row['id'],video,short_cinematic_name(label_source),badge,tile_pixmap,self.select_cinematic_video,self.delete_cinematic_video)
            self.cinematic_tiles[str(row['id'])]=tile
            item.setSizeHint(self.cinematic_results.gridSize());self.cinematic_results.addItem(item);self.cinematic_results.setItemWidget(item,tile)
            if row['id']==current_id:self.cinematic_results.setCurrentItem(item)
        self.cinematic_results.blockSignals(False)
        self.update_cinematic_grid();self._sync_cinematic_tiles()
        self._video_rows=list(rows);self.update_media_count();self.cinematic_results.setVisible(bool(rows));self.cinematic_empty.setVisible(not rows)
        self.ensure_cinematic_thumbs()
        selected=self.cinematic_results.currentItem();selected_video=selected.data(Qt.UserRole+1) if selected else ''
        pending=getattr(self,'cinematic_auto_post_pending',False)
        self.post_cinematic_button.setEnabled(bool(selected_video and Path(selected_video).is_file() and not pending))
        if hasattr(self,'cinematic_play_button'):self.cinematic_play_button.setEnabled(bool(selected_video and Path(selected_video).is_file()))
        if hasattr(self,'cinematic_fullscreen_button'):self.cinematic_fullscreen_button.setEnabled(bool(selected_video and Path(selected_video).is_file()))
        if selected_video and selected_video!=previous_video and Path(selected_video).is_file():
            self.cinematic_player.setSource(QUrl.fromLocalFile(selected_video));self.cinematic_player.play();self.cinematic_play_button.setText('Pause');self.preview_image.hide();self.cinematic_video_widget.show();self.video_controls.show()
            selected_id=selected.data(Qt.UserRole) if selected else ''
            if selected_id and str(self.cinematic_player.source().toLocalFile())==str(Path(selected_video)):
                self.ask_post_cinematic_video(selected_id,selected_video)
    def toggle_cinematic_playback(self):
        if self.cinematic_player.playbackState()==QMediaPlayer.PlayingState:self.cinematic_player.pause();self.cinematic_play_button.setText('Play')
        else:self.cinematic_player.play();self.cinematic_play_button.setText('Pause')
    def preview_cinematic_video(self,current,previous=None):
        path=current.data(Qt.UserRole+1) if current else ''
        self.cinematic_player.stop()
        if path and Path(path).is_file():self.cinematic_player.setSource(QUrl.fromLocalFile(path));self.cinematic_player.play();self.cinematic_play_button.setText('Pause')
        else:self.cinematic_player.setSource(QUrl());self.cinematic_play_button.setText('Play')
        has_video=bool(path and Path(path).is_file());self.preview_image.setVisible(not has_video);self.cinematic_video_widget.setVisible(has_video);self.video_controls.setVisible(has_video);self.preview_sub.setText(Path(path).name if has_video else 'Select a screenshot or finished video to preview it here.');self.preview_image.setText('Select a screenshot or finished video to preview it here.')
        self.post_cinematic_button.setEnabled(has_video)
        if hasattr(self,'cinematic_play_button'):self.cinematic_play_button.setEnabled(has_video)
        if hasattr(self,'cinematic_fullscreen_button'):self.cinematic_fullscreen_button.setEnabled(has_video)
        self._sync_cinematic_tiles()
    def test_cinematic_video(self):
        source,_=QFileDialog.getOpenFileName(self,'Choose a COD gameplay screenshot',str(self.screenshots_dir()),'Images (*.png *.jpg *.jpeg *.webp)')
        if source:self.queue_cinematic_source(source)
    def _sweep_stale_cinematics(self,max_age_s=2100):
        """Requeue video jobs whose worker died mid-run (restart/crash) instead of leaving them stuck."""
        try:rows=self.store.recent_cinematics(100)
        except Exception:return 0
        try:active=getattr(self,'_inapp_active',None)
        except Exception:active=None
        try:started=getattr(self,'_inapp_started',None)
        except Exception:started=None
        now=time.time();reset=0
        for row in rows or []:
            try:
                if str(row.get('status') or '') not in ('running','awaiting_generation','awaiting_download'):continue
                try:age=now-float(row.get('updated') or 0)
                except (TypeError,ValueError):age=max_age_s+1
                if age<max_age_s:continue
                try:attempts=int(row.get('attempts') or 0)
                except (TypeError,ValueError):attempts=0
                if active and row.get('id')==active:
                    try:flag_age=now-float(started) if started is not None else -1
                    except (TypeError,ValueError):flag_age=-1
                    if flag_age<max_age_s:continue
                    self._inapp_active=None
                    try:self._inapp_started=None
                    except Exception:pass
                if attempts>=3:
                    self.store.update_cinematic(row['id'],status='failed',attempts=attempts+1,error='Video worker timed out 3 times with no result; re-queue the screenshot by hand to try again.')
                else:
                    self.store.update_cinematic(row['id'],status='queued',attempts=attempts+1,error='Video worker ended with no result; requeued automatically (try '+str(attempts+1)+').')
            except Exception:continue
            reset+=1
            try:self.log('Reset orphaned video job '+str(row.get('id'))+'; it retries automatically.')
            except Exception:pass
        return reset
    def poll_inapp_cinematics(self):
        try:self._sweep_stale_cinematics()
        except Exception:pass
        if getattr(self,'_inapp_active',None):return
        try:pending=self.store.pending_cinematics(1)
        except Exception:return
        if not pending:return
        job_id=str(pending[0].get('id') or '')
        if not job_id:return
        try:claimed=self.store.claim_cinematic(job_id)
        except Exception:return
        if not claimed:return
        self._inapp_active=job_id
        try:self._inapp_started=time.time()
        except Exception:pass
        try:self.store.update_cinematic(job_id,status='awaiting_generation')
        except Exception:pass
        self._start_embed_poll()
        thread=threading.Thread(target=self._run_inapp_cinematic,args=(job_id,),daemon=True);thread.start()
    def _run_inapp_cinematic(self,job_id):
        try:row=self.store.get_cinematic(job_id)
        except Exception:row=None
        if not row or row.get('status') not in ('running','awaiting_generation'):
            self._inapp_active=None
            try:self._inapp_started=None
            except Exception:pass
            return
        prompt=str(row.get('prompt') or '');screenshot=str(row.get('image') or '')
        try:talent_ids=json.loads(row.get('talent_ids') or '[]')
        except Exception:talent_ids=[]
        faces=[]
        for pid in list(talent_ids or [])[:3]:
            staged=self.videos_dir()/('talent_'+str(pid)+'.png')
            if staged.is_file():faces.append(str(staged))
        talent_png=self.videos_dir()/'talent.png'
        if talent_png.is_file():faces.insert(0,str(talent_png))
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            self._fail_inapp_cinematic(job_id,'Playwright is not installed in the tracker environment.');return
        try:self.store.update_cinematic(job_id,status='awaiting_download')
        except Exception:pass
        try:
            with sync_playwright() as playwright:
                context=meta_automation.ensure_context(playwright,E.DATA/'TrackerChromeInApp',minimized=True)
                driver=meta_automation.MetaDriver(context,E.DATA/'meta-diagnostics')
                page=driver.new_job_page()
                try:
                    for other in list(getattr(context,'pages',None) or []):
                        if other is not page:
                            try:other.close()
                            except Exception:pass
                except Exception:pass
                try:video=driver.submit_image_to_video(page,screenshot,faces,prompt,str(self.videos_dir()),on_stage=self._driver_stage)
                finally:
                    try:page.close()
                    except Exception:pass
                    try:context.close()
                    except Exception:pass
        except Exception as exc:
            detail=str(getattr(exc,'detail',None) or exc)
            step=str(getattr(exc,'step','error'))
            if 'login' in step.lower():message='Meta AI is signed out. Click Log into Meta AI in the sidebar, verify your session, then requeue the video.'
            else:message=(f'Meta automation stopped at {step}: {detail}')[:300]
            self._fail_inapp_cinematic(job_id,message);return
        try:self.store.update_cinematic(job_id,status='completed',video=str(video),error='')
        except Exception:pass
        try:self.on_log('In-app cinematic completed: '+job_id)
        except Exception:pass
        QTimer.singleShot(0,lambda:self.set_automation_stage('collected',job_id))
        self._inapp_active=None
        try:self._inapp_started=None
        except Exception:pass
        QTimer.singleShot(0,self.refresh_cinematic_results)
    def _fail_inapp_cinematic(self,job_id,message):
        try:self.store.update_cinematic(job_id,status='failed',error=str(message)[:300])
        except Exception:pass
        try:self.on_log('In-app cinematic failed: '+job_id+' — '+str(message)[:160]);QTimer.singleShot(0,lambda:self._show_login_card(message) if ('login' in str(message).lower() or 'signed out' in str(message).lower()) else self.set_automation_stage('failed',message))
        except Exception:pass
        self._inapp_active=None
        try:self._inapp_started=None
        except Exception:pass
        QTimer.singleShot(0,self.refresh_cinematic_results)
    def queue_selected_cinematic(self):
        item=self.cod_ai_screenshots.currentItem()
        if not item:return
        source=item.data(Qt.UserRole)
        if source:self.queue_cinematic_source(source)
    def queue_cinematic_source(self,source):
        try:
            from PIL import Image
            source_path=Path(source);cid='cin-'+uuid.uuid4().hex
            folder=self.videos_dir()/'input';folder.mkdir(parents=True,exist_ok=True);target=folder/(cid+'.png')
            with Image.open(source_path) as image:
                image.convert('RGB').save(target,format='PNG',optimize=False)
            events=['A close air-support flyby kicks up a wave of loose battlefield debris without striking the operator.','A nearby armored vehicle skids to a controlled stop, splashing water and dust past the camera.','A sudden wall of rain sweeps across the scene and sends loose gear tumbling naturally.','A drone swarm briefly forms a strange moving pattern overhead before scattering.','A supply crate bursts open and releases a ridiculous cloud of colorful smoke and harmless gear.','A shockwave ripples through a puddle as a distant structure sheds debris in the background.','A flock of birds erupts from nearby cover and sweeps past the lens in slow motion.','A battlefield banner tears loose and spirals dramatically through the wind.']
            cameras=['a low tracking shot that rises into a smooth orbit','a steady handheld push-in with a brief slow-motion beat','a lateral tracking move that settles into a close hero angle','a short crane-like rise that reveals more of the surrounding environment']
            effects=['windblown dust and realistic debris','rain spray and shimmering reflections','subtle heat haze and drifting embers','water ripples and natural motion blur']
            def choose(key,items):
                old=self.store.meta('cinematic_recent_'+key,[]);choices=[x for x in items if x not in old[:4]] or list(items);value=__import__('random').choice(choices);self.store.set_meta('cinematic_recent_'+key,[value]+old[:3]);return value
            event=choose('event',events);camera=choose('camera',cameras);effect=choose('effect',effects)
            prompt=('Transform the provided Call of Duty screenshot into a breathtaking, hyper-realistic cinematic video. Use the screenshot as the opening frame. Preserve the operator, clothing, weapon, terrain, buildings, lighting, and recognizable scene. '
                    f'Invent this one surprising event, adapted naturally to the visible environment: {event} If this event does not fit the screenshot, replace it with one event that fits the visible scene. '
                    'Use a fresh creative direction; avoid defaulting to explosions or helicopters. Make it look like next-generation AAA gameplay with realistic physics, dynamic lighting, volumetric effects, natural character motion, and cinematic camera movement. '
                    f'Begin by matching the screenshot, animate the environment naturally, introduce the event using {effect}, then finish with a memorable cinematic shot. Use {camera}. No HUD, subtitles, watermarks, or added text.')
            talent=self.videos_dir()/'talent.png'
            if talent.is_file():
                prompt+=' A second attached image is a reference photo of the featured person: give the main operator that same face, headwear, and shirt while keeping the screenshot scene.'
            cast_folder=self.videos_dir()/cid;cast_folder.mkdir(parents=True,exist_ok=True)
            friends=self.selected_talent_friends();friend_ids=[];cast_names=[]
            for friend in friends:
                try:
                    friend_target=cast_folder/('talent-'+str(friend.get('id'))+'.png')
                    with Image.open(str(friend.get('photo'))) as face:face.convert('RGB').save(friend_target,format='PNG')
                    friend_ids.append(str(friend.get('id')))
                    poster=str(friend.get('display_name') or friend.get('name',''))
                    cast_names.append(poster)
                    if talent.is_file():prompt+=f' An attached image named {friend_target.name} is a reference photo of {poster}: give one of the other visible operators that same face, headwear, and shirt.'
                    else:prompt+=f' An attached image named {friend_target.name} is a reference photo of {poster}: give a visible operator that same face, headwear, and shirt.'
                except OSError:continue
            if not self.store.create_cinematic(cid,source_path.name,target,prompt,talent_ids=friend_ids):raise RuntimeError('Could not create a unique cinematic job.')
            self.cinematic_status.setText(f'Queued {cid}. The screenshot is stored locally; Meta AI will process it in the background. Discord posting is manual.'+(' Featuring '+', '.join(cast_names)+'.' if cast_names else ''))
            self.log('Cinematic test queued: '+cid);self.refresh_cinematic_results()
        except Exception as exc:
            self.cinematic_status.setText('Cinematic test could not be queued: '+str(exc)[:300]);self.log('Cinematic test failed: '+str(exc))
    def post_cinematic_video(self):
        item=self.cinematic_results.currentItem();path=item.data(Qt.UserRole+1) if item else ''
        if not path or not Path(path).is_file():return
        try:webhook=self.settings.cinematic_webhook().strip()
        except Exception:webhook=''
        if not webhook:
            self.cinematic_status.setText('Add the separate Cinematic videos webhook under Settings → Discord, save it, then post manually.');return
        if not re.fullmatch(r'https://discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9._-]+',webhook):self.cinematic_status.setText('The saved cinematic webhook is not a valid Discord webhook URL.');return
        def send():
            return post_file_to_discord_webhook(webhook,path)
        cid=item.data(Qt.UserRole) if item else ''
        self.async_task(send,lambda status:self.on_cinematic_discord_result(f'Discord accepted the video (HTTP {status}).',cid,path),lambda msg:self.on_cinematic_discord_result('Discord post failed: '+msg[:260]))
    def ask_post_cinematic_video(self,cinematic_id,path):
        if not path or not Path(path).is_file():return
        if str(getattr(self,'cinematic_approval_for','') or '')==str(cinematic_id):return
        try:
            webhook=self.settings.cinematic_webhook().strip()
        except Exception:webhook=''
        if not webhook or not re.fullmatch(r'https://discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9._-]+',webhook):return
        self.cinematic_approval_for=str(cinematic_id)
        rows=self.store.recent_cinematics(30)
        for index in range(self.cinematic_results.count()):
            item=self.cinematic_results.item(index)
            if item.data(Qt.UserRole)==cinematic_id:self.cinematic_results.setCurrentItem(item);break
        self.refresh_cinematic_results()
        self.cinematic_auto_post_pending=True;self.refresh_cinematic_results()
        if not self.ask('The cinematic video '+str(cinematic_id)+' is downloaded and is previewing. Post it to the Cinematic videos Discord webhook now?'):
            self.cinematic_approval_for='';self.cinematic_auto_post_pending=False;self.refresh_cinematic_results()
            self.cinematic_status.setText('Saved locally. Select the finished video above and choose Post selected video to Discord to share it.');return
        self.post_cinematic_video()
    def on_cinematic_discord_result(self,message,job_id='',video_path=''):
        message=re.sub(r'(https://discord(?:app)?\.com)?/api/webhooks/\d+/[\w.\-]+','[webhook]',str(message))
        self.cinematic_approval_for='';self.cinematic_auto_post_pending=False
        if message.startswith('Discord accepted') and job_id and video_path:
            try:
                target=Path(video_path)
                if target.resolve().parent==(self.videos_dir()).resolve() and target.suffix.lower()=='.mp4' and target.is_file():
                    target.unlink();self.store.update_cinematic(job_id,video='')
                    message+=' Local MP4 deleted to save space.'
                else:message+=' Local MP4 kept (unexpected location).'
            except OSError:message+=' Local MP4 could not be deleted.'
        self.cinematic_status.setText(message);self.refresh_cinematic_results()
    def open_screenshots_folder(self):
        folder=self.screenshots_dir();folder.mkdir(parents=True,exist_ok=True);os.startfile(str(folder))
    def refresh_cod_ai_screenshots(self):
        if not hasattr(self,'cod_ai_screenshots'):return
        current=self.cod_ai_screenshots.currentItem();current_path=current.data(Qt.UserRole) if current else None
        self.update_screenshot_grid();self.cod_ai_screenshots.blockSignals(True);self.cod_ai_screenshots.clear()
        self.screenshot_tiles={}
        folder=self.screenshots_dir()
        try:paths=sorted(folder.glob('COD_*.png'),key=lambda path:path.stat().st_mtime,reverse=True)[:40] if folder.exists() else []
        except OSError:paths=[]
        for path in paths:
            try:label=f'{path.name}   ·   {path.stat().st_size/1024/1024:.1f} MB'
            except OSError:label=path.name
            item=QListWidgetItem();item.setData(Qt.UserRole,str(path));item.setToolTip(label)
            pix=QPixmap(str(path))
            badge='' if pix.isNull() else f'{pix.width()}x{pix.height()}'
            tile=ScreenshotTile(str(path),short_cinematic_name(path.name),badge,None if pix.isNull() else pix,self.select_cod_ai_screenshot,self.delete_cod_ai_screenshot)
            self.screenshot_tiles[str(path)]=tile
            item.setSizeHint(self.cod_ai_screenshots.gridSize());self.cod_ai_screenshots.addItem(item);self.cod_ai_screenshots.setItemWidget(item,tile)
            if current_path is not None and str(path)==str(current_path):self.cod_ai_screenshots.setCurrentItem(item)
        self.cod_ai_screenshots.blockSignals(False)
        self.update_screenshot_grid();self._sync_screenshot_tiles()
        self._shot_paths=list(paths);self.update_media_count()

        self.cod_ai_screenshots.setVisible(bool(paths));self.cod_ai_empty.setVisible(not paths)
        if self.cod_ai_screenshots.count():
            if not self.cod_ai_screenshots.currentItem():self.cod_ai_screenshots.setCurrentRow(0)
        else:
            self.preview_image.show();self.cinematic_video_widget.hide();self.video_controls.hide();self.preview_image.setText('Select a screenshot or finished video to preview it here');self.preview_sub.setText('Select a screenshot or finished video to preview it here.')
        self.maybe_offer_screenshot_cleanup()
    def ask_screenshot_cleanup(self,used_count,total_count):
        box=QMessageBox(self);box.setWindowTitle('Many screenshots');box.setText('You seem to have a bunch of screenshots. Would you like to delete the ones you have already used in videos?');box.setInformativeText(f'{used_count} used of {total_count} screenshots would be deleted. Queue entries stay for history.');yes_button=box.addButton('Yes',QMessageBox.YesRole);box.addButton('No',QMessageBox.NoRole);box.setDefaultButton(yes_button);box.exec();return box.clickedButton() is yes_button
    def maybe_offer_screenshot_cleanup(self):
        if getattr(self,'_screenshot_cleanup_offered',False):return
        if not self.isVisible():return
        folder=self.screenshots_dir()
        try:files=sorted(folder.glob('COD_*.png')) if folder.is_dir() else []
        except OSError:return
        if len(files)<=10:return
        try:jobs=self.store.recent_cinematics(100)
        except Exception:return
        used=E.used_screenshots(folder,jobs)
        if not used:return
        self._screenshot_cleanup_offered=True
        if not self.ask_screenshot_cleanup(len(used),len(files)):return
        deleted=0
        try:keep_dir=folder.resolve()
        except OSError:return
        for path in used:
            try:
                if path.resolve().parent!=keep_dir or path.suffix.lower()!='.png' or not path.name.startswith('COD_'):continue
                path.unlink();deleted+=1
            except OSError:continue
        self.refresh_cod_ai_screenshots()
        self.cod_ai_capture_status.setText(f'Deleted {deleted} used screenshot'+('s' if deleted!=1 else '')+'.')
    def delete_cod_ai_screenshot(self,path=None):
        """Delete one screenshot. The tile X button passes its path, otherwise the selection is used."""
        if path is None:
            item=self.cod_ai_screenshots.currentItem() if hasattr(self,'cod_ai_screenshots') else None
            raw=item.data(Qt.UserRole) if item else ''
        else:raw=path
        if not raw or not Path(raw).is_file():
            self.cod_ai_capture_status.setText('Select a screenshot first.');return
        target=Path(raw)
        try:
            keep_dir=(self.screenshots_dir()).resolve();inside=target.resolve().parent==keep_dir
        except OSError:inside=False
        if not inside or target.suffix.lower()!='.png' or not target.name.startswith('COD_'):
            self.cod_ai_capture_status.setText('Only tracker COD screenshots can be deleted here.');return
        if not self.ask('Delete '+target.name+'? This cannot be undone.'):return
        try:target.unlink()
        except OSError as exc:
            self.cod_ai_capture_status.setText('Could not delete screenshot: '+str(exc)[:200]);return
        self.refresh_cod_ai_screenshots()
        self.cod_ai_capture_status.setText('Deleted '+target.name+'.')
    def preview_cod_ai_screenshot(self,item,previous=None):
        if not item:return
        path=item.data(Qt.UserRole)
        if not path:return
        pix=QPixmap(path)
        if pix.isNull():
            self.preview_image.setText('Could not read screenshot');self.preview_sub.setText(item.toolTip() or Path(str(path)).name);return
        self.preview_image.setPixmap(pix.scaled(self.preview_image.size(),Qt.KeepAspectRatio,Qt.SmoothTransformation))
        self.preview_sub.setText(f'{Path(path).name}  ·  {pix.width()}×{pix.height()}')
        self._sync_screenshot_tiles();self.cinematic_player.stop();self.preview_image.show();self.cinematic_video_widget.hide()
    def ensure_screenshot_capture(self):
        if self.capture is None:
            from capture_engine import CaptureEngine,StableDetector
            self.capture=CaptureEngine();self.detector=StableDetector(frames=2)
        return self.capture
    def capture_cod_ai_screenshot(self,checked=False):
        if self.screenshot_busy:return
        try:service=self.ensure_screenshot_capture()
        except Exception as exc:
            message='Screenshot capture could not start: '+str(exc)
            self.cod_ai_capture_status.setText(message);self.cod_ai_capture_status.setStyleSheet('color:#ffb15e');self.log(message);return
        self.screenshot_busy=True;self.cod_ai_capture_status.setText(self.hotkey_text('screenshot')+' received · finding the Call of Duty display…')
        self.cod_ai_capture_status.setStyleSheet('color:#f2bd62;font-weight:700;')
        def work():
            output,game_window=service.game_output();frame=service.grab_game_output(output)
            folder=self.screenshots_dir();folder.mkdir(parents=True,exist_ok=True)
            stamp=time.strftime('%Y-%m-%d_%H-%M-%S');target=folder/f'COD_{stamp}.png';sequence=2
            while target.exists():target=folder/f'COD_{stamp}_{sequence:02d}.png';sequence+=1
            from PIL import Image
            try:
                Image.fromarray(frame).save(target,format='PNG',compress_level=3)
                with Image.open(target) as saved:
                    if saved.format!='PNG' or saved.size!=(int(output['width']),int(output['height'])):
                        raise RuntimeError('Saved screenshot did not pass its PNG and native-resolution check.')
                    saved.verify()
            except Exception:
                target.unlink(missing_ok=True);raise
            return str(target),output,game_window
        def done(result):
            self.screenshot_busy=False;path,output,game_window=result;name=Path(path).name
            message=f'Saved {name} · {output["width"]}×{output["height"]} · {output.get("label",game_window.get("title") or "game display")}'
            self.cod_ai_capture_status.setText(message);self.cod_ai_capture_status.setStyleSheet('color:#56e99a;font-weight:800;');self.refresh_cod_ai_screenshots();self.log(message)
            if self.tray.isVisible():self.tray.showMessage('Screenshot saved',name,QSystemTrayIcon.Information,2500)
        def failed(message):
            self.screenshot_busy=False;detail='Screenshot not saved: '+str(message)
            self.cod_ai_capture_status.setText(detail);self.cod_ai_capture_status.setStyleSheet('color:#ffb15e;font-weight:800;');self.log(detail)
            if self.tray.isVisible():self.tray.showMessage('COD AI screenshot failed',str(message)[:240],QSystemTrayIcon.Warning,3500)
        self.async_task(work,done,failed)
    def build_how_it_works(self):
        l=self.page('How it works','The unified tracker loop, in order. Everything below runs locally except Discord posts and the pages you open in the browser.')
        steps=(('1 · Track','Start tracking on the Dashboard. The watcher reads your game screen with local OCR and files matches, camo ticks and lifetime totals into the tracker database.'),
               ('2 · Review','Scoreboards land in the review queue. Fix names and values, confirm, and save. Nothing posts automatically.'),
               ('3 · Progress','Camo Tracker, Loadouts, Leaderboards and Stats read the same local records. Manual ticks and builds stay in the tracker.'),
               ('4 · Share','Generate the Meta AI picture, approve it, and post to Discord. Profile links and share buttons only open the browser.'))
        for title,body in steps:
            card=QFrame();card.setObjectName('dashboardPanel');box=QVBoxLayout(card);box.setContentsMargins(14,12,14,12)
            heading=QLabel(title);heading.setObjectName('camoWeaponName');box.addWidget(heading)
            text=QLabel(body);text.setWordWrap(True);text.setObjectName('camoNextLine');box.addWidget(text)
            l.addWidget(card)
        l.addStretch(1)
    def build_logs(self):
        panel=QWidget();l=QVBoxLayout(panel);l.setContentsMargins(12,12,12,12);l.setSpacing(12)
        desc=QLabel('Operational events and recoverable errors. Webhook URLs are redacted.');desc.setWordWrap(True);l.addWidget(desc)
        self.logs=QPlainTextEdit();self.logs.setReadOnly(True);l.addWidget(self.logs,1)
        self.row(l,self.button('Refresh Solo worker log',self.worker_log),self.button('Open application log',lambda:os.startfile(str(E.DATA/'application.log'))))
        self.logs_tab_index=self.settings_tabs.addTab(panel,'Logs')

    def log(self,text):
        text=re.sub(r'(https://discord(?:app)?\.com)?/api/webhooks/\d+/[\w.\-]+','[webhook]',str(text));logging.info(text)
        if hasattr(self,'logs'):self.logs.appendPlainText(time.strftime('%H:%M:%S')+'  '+text)
        if hasattr(self,'job_status'):self.job_status.setText(text[:240])
    def mode_changed(self,mode,initial=False):
        if not hasattr(self,'logs'):return
        if self.tracking:self.stop()
        if mode=='Solo Ops':
            self.auto_detect.setChecked(False)
            if self.capture and not self.capture_busy and not self.screenshot_busy:self.capture.close();self.capture=None
            if self.browser:self.browser.close()
        self.settings.values['mode']=mode;self.settings.save();self.status.setText('●  STANDBY');self.refresh_dashboard_status()
        if not initial:self.log('Mode changed to '+mode+'. Press Start to begin.')
    def skipped_update_version(self):
        try:return (E.DATA/'skipped_update.txt').read_text(encoding='utf-8').strip()
        except OSError:return ''
    def check_for_app_update(self,manual=False):
        if not getattr(sys,'frozen',False) and not manual:return
        thread=threading.Thread(target=self._check_for_app_update_worker,args=(manual,),daemon=True);thread.start()
    def _check_for_app_update_worker(self,manual):
        try:status=updater.check_for_updates()
        except Exception:status=None
        if manual or (status and status.get('update_available') and status.get('latest')!=self.skipped_update_version()):
            QTimer.singleShot(0,lambda:self.prompt_app_update(status,manual))
        elif manual:QTimer.singleShot(0,lambda:self.notice('You are on the latest version.' if status else 'Could not reach the update server.'))
    def prompt_app_update(self,status,manual=False):
        if not status:
            if manual:self.notice('Could not reach the update server.')
            return
        if not status.get('update_available'):
            if manual:self.notice('You are on the latest version.')
            return
        box=QMessageBox(self);box.setWindowTitle('Tracker update');box.setText(f"Version {status.get('latest')} is available (you have {status.get('current')}).")
        notes=(status.get('notes') or '').strip()
        if notes:box.setInformativeText(notes[:800])
        update_button=box.addButton('Update Now',QMessageBox.AcceptRole);box.addButton('Later',QMessageBox.RejectRole);skip_button=box.addButton('Skip This Version',QMessageBox.DestructiveRole);box.setDefaultButton(update_button);box.exec()
        clicked=box.clickedButton()
        if clicked is skip_button:
            try:(E.DATA/'skipped_update.txt').write_text(str(status.get('latest')),encoding='utf-8')
            except OSError:pass
            return
        if clicked is not update_button:return
        if not getattr(sys,'frozen',False):self.notice('Updates install into the packaged app. Run the installed tracker to update.');return
        self.download_and_apply_update(status)
    def download_and_apply_update(self,status):
        from PySide6.QtWidgets import QProgressDialog
        dest=updater.pending_path()
        progress={'received':0,'total':status.get('size') or 0,'done':False,'ok':False}
        def worker():
            progress['ok']=bool(updater.download_update(status['download_url'],dest,progress=lambda r,t:(progress.update(received=r,total=t or progress['total']))))
            progress['done']=True
        threading.Thread(target=worker,daemon=True).start()
        dialog=QProgressDialog('Downloading tracker update…','Cancel',0,max(1,progress['total'] or 1),self);dialog.setWindowTitle('Tracker update');dialog.setMinimumDuration(500);dialog.show()
        while not progress['done']:
            QApplication.processEvents();time.sleep(0.05)
            if dialog.wasCanceled():break
            dialog.setMaximum(max(1,progress['total'] or 1));dialog.setValue(progress['received'])
        dialog.close()
        if not progress['done'] or not progress['ok'] or dialog.wasCanceled():
            try:os.remove(dest)
            except OSError:pass
            if not dialog.wasCanceled():self.notice('The update download did not complete.')
            return
        if self.ask('Update downloaded. Restart now to apply it?') and updater.apply_and_restart(dest):QApplication.quit()
    def save_settings(self):
        try:
            self.settings.values.update(player=self.player.text().strip() or 'vintendo',stats_dir=self.stats_dir.text().strip(),warzone_enabled=True,profile_url=self.profile.text().strip(),codmunity_email=self.codmunity_email_edit.text().strip(),poll_seconds=self.poll.value(),mode=self.mode.currentText(),auto_detect=self.auto_detect.isChecked(),auto_post=self.auto_post.isChecked(),full_auto=self.full_auto.isChecked(),include_stats=self.include_stats_checkbox.isChecked(),provider='Meta AI manual handoff',allow_both=self.allow_both.isChecked(),browser_navigation_timeout=self.browser_timeout.value(),browser_download_timeout=self.download_timeout.value()*60,browser_retries=self.browser_retries.value(),tracker_browser_mode=('attach' if self.browser_mode_combo.currentIndex()==1 else 'dedicated'))
            self.settings.values.update(scoreboard_hotkey_vk=self.scoreboard_hotkey_edit.vk,scoreboard_hotkey_mod=self.scoreboard_hotkey_edit.mod,screenshot_hotkey_vk=self.screenshot_hotkey_edit.vk,screenshot_hotkey_mod=self.screenshot_hotkey_edit.mod,screenshots_dir=self.screenshots_dir_edit.text().strip(),videos_dir=self.videos_dir_edit.text().strip(),webhook_channel=self.webhook_channel.text().strip(),cinematic_webhook_channel=self.cinematic_webhook_channel.text().strip());self.settings.set_webhook(self.webhook.text());self.settings.set_cinematic_webhook(self.cinematic_webhook.text());self.settings.save();self.log('Settings saved with DPAPI-protected webhook. Restart tracking to apply integration changes.');self.refresh_dashboard_status()
            self.unregister_global_hotkeys();self.register_global_hotkeys();self.refresh_hotkey_labels()
            self.flash_save_button(getattr(self,'save_settings_button',None),'Save all settings')
            if hasattr(self,'cod_ai_screenshots'):self.refresh_cod_ai_screenshots()
            if hasattr(self,'cod_ai_screenshots'):self.update_screenshot_grid()
            if hasattr(self,'cinematic_results'):self.update_cinematic_grid()

        except Exception as exc:self.notice('Settings could not be saved: '+str(exc))
    def import_previous_settings(self):
        old=E.read_json(Path(os.environ.get('LOCALAPPDATA',''))/'Vintendo Scoreboard Capture'/'settings.json',{}) or {}
        if 'game' in old:self.settings.values['game']=old['game']
        if old.get('protected_webhook') and not self.settings.values.get('protected_webhook'):
            # Same-account DPAPI blob can be imported without logging its plaintext.
            self.settings.values['protected_webhook']=old['protected_webhook']
            try:self.webhook.setText(self.settings.webhook())
            except Exception:pass
        self.settings.save()
    def import_config(self):
        path,_=QFileDialog.getOpenFileName(self,'Choose existing tracker config.json','','JSON (*.json)')
        if not path:return
        data=E.read_json(path,{})
        if data.get('webhook_url'):self.webhook.setText(data['webhook_url'])
        self.player.setText(data.get('player','vintendo'));self.stats_dir.setText(data.get('stats_dir',''));self.profile.setText(data.get('profile_url',''))
        parent=Path(path).parent
        for name in ('posted_matches.json','achievement_history.json','achievement_config.json'):
            source=parent/name;target=E.DATA/name
            if source.exists() and not target.exists():shutil.copy2(source,target)
        self.save_settings();self.log('Legacy configuration imported; credential stored only as DPAPI ciphertext.')
    def import_graphics(self):
        path,_=QFileDialog.getOpenFileName(self,'Choose graphics.json','','JSON (*.json)')
        if not path:return
        registry=E.read_json(path,{}) or {};out={};folder=E.DATA/'assets'/'custom';folder.mkdir(parents=True,exist_ok=True)
        for key,value in registry.items():
            source=Path(value);source=source if source.is_absolute() else Path(path).parent/source
            if source.is_file():target=folder/(uuid.uuid4().hex+source.suffix);shutil.copy2(source,target);out[key]=str(target)
        E.atomic_json(E.DATA/'graphics.json',out);self.log(f'Imported {len(out)} legacy artwork selections.')
    def reset_hotkeys(self):
        self.scoreboard_hotkey_edit.set_hotkey(*DEFAULT_SCOREBOARD_HOTKEY);self.screenshot_hotkey_edit.set_hotkey(*DEFAULT_SCREENSHOT_HOTKEY)
    def pick_media_dir(self,edit):
        path=QFileDialog.getExistingDirectory(self,'Choose folder',edit.text().strip() or str(E.DATA))
        if path:edit.setText(path)
    def reset_media_dirs(self):
        self.screenshots_dir_edit.setText('');self.videos_dir_edit.setText('')
    def run_first_boot_setup(self):
        if self.tab_prototype or is_smoke_run():return
        values=self.settings.values
        screenshot=str(values.get('screenshots_dir') or '').strip();video=str(values.get('videos_dir') or '').strip()
        folders_missing=not screenshot or not video or not Path(screenshot).is_dir() or not Path(video).is_dir()
        if folders_missing:self.show_media_folder_setup()
        if not values.get('capture_hotkey_prompt_seen'):
            answer=QMessageBox.question(self,'Capture hotkey','Would you like to change the capture hotkey?\n\nOne press saves a screenshot and prepares scoreboard OCR.',QMessageBox.Yes|QMessageBox.No,QMessageBox.Yes)
            values['capture_hotkey_prompt_seen']=True;self.settings.save()
            if answer==QMessageBox.Yes:self.open_hotkey_dialog('scoreboard')
    def show_media_folder_setup(self):
        dialog=QDialog(self);dialog.setWindowTitle('Choose screenshot and video folders');dialog.setModal(True)
        layout=QVBoxLayout(dialog);layout.setSpacing(12)
        note=QLabel('Choose where screenshots and generated videos should be saved. You can change these folders later in Settings.');note.setWordWrap(True);layout.addWidget(note)
        screenshots=QLineEdit(str(self.settings.values.get('screenshots_dir') or self.screenshots_dir()));videos=QLineEdit(str(self.settings.values.get('videos_dir') or self.videos_dir()))
        def folder_row(label,edit):
            row=QHBoxLayout();row.addWidget(edit,1);browse=QPushButton('Browse…');browse.clicked.connect(lambda: self._choose_folder_for_edit(edit));row.addWidget(browse);form.addRow(label,row)
        form=QFormLayout();folder_row('Screenshots folder',screenshots);folder_row('Videos folder',videos);layout.addLayout(form)
        buttons=QDialogButtonBox();save=buttons.addButton('Save folders',QDialogButtonBox.AcceptRole);defaults=buttons.addButton('Use app folders',QDialogButtonBox.DestructiveRole)
        save.clicked.connect(dialog.accept);defaults.clicked.connect(lambda:dialog.done(2));layout.addWidget(buttons)
        dialog.resize(650,190)
        result=dialog.exec()
        if result not in (QDialog.Accepted,2):return
        if result==2:screenshots.setText(str(E.DATA/'Screenshots'));videos.setText(str(E.DATA/'Cinematics'))
        shot_path=Path(screenshots.text().strip() or str(E.DATA/'Screenshots')).expanduser();video_path=Path(videos.text().strip() or str(E.DATA/'Cinematics')).expanduser()
        try:
            shot_path.mkdir(parents=True,exist_ok=True);video_path.mkdir(parents=True,exist_ok=True)
            self.settings.values.update(screenshots_dir=str(shot_path),videos_dir=str(video_path));self.settings.save()
            if hasattr(self,'screenshots_dir_edit'):self.screenshots_dir_edit.setText(str(shot_path));self.videos_dir_edit.setText(str(video_path))
        except OSError as exc:QMessageBox.warning(self,'Folder setup failed',f'Could not create the selected folders:\n\n{exc}')
    def _choose_folder_for_edit(self,edit):
        path=QFileDialog.getExistingDirectory(self,'Choose folder',edit.text().strip() or str(E.DATA))
        if path:edit.setText(path)
    def _effective_stats_dir(self):
        """Resolved CODMunity stats folder: a valid saved override, else the auto-discovered user folder. Returns '' when nothing is resolvable yet."""
        try:override=self.stats_dir.text().strip() if hasattr(self,'stats_dir') else ''
        except (AttributeError,RuntimeError):override=''
        if override:
            try:
                if (Path(override)/'matches.json').is_file():return override
            except (OSError,RuntimeError):pass
        try:return str(E.legacy().find_stats_dir({}))
        except Exception:return override
    def _autofill_stats_dir(self):
        """Fill an empty stats folder field with the auto-discovered one so the user is never asked for the path."""
        try:
            if hasattr(self,'stats_dir') and not self.stats_dir.text().strip():
                found=self._effective_stats_dir()
                if found and Path(found).is_dir():
                    self.stats_dir.setText(found);self.settings.values['stats_dir']=found;self.settings.save()
        except (AttributeError,OSError,RuntimeError):pass
    def pick_stats(self):

        start=self._effective_stats_dir() or str(E.DATA)
        path=QFileDialog.getExistingDirectory(self,'Choose folder containing matches.json',start)
        if path:self.stats_dir.setText(path)
    def _maybe_auto_upload(self):
        if os.environ.get('PYTEST_CURRENT_TEST') is not None:return
        if getattr(self,'tab_prototype',False):return
        if getattr(self,'_auto_upload_done',False):return
        try:configured=bool(self.codmunity_player_service.configured)
        except Exception:configured=False
        if not configured:return
        try:auth=self.codmunity_auth
        except AttributeError:auth=None
        if auth is None:return
        try:user_dir=self._sync_user_dir()
        except Exception:user_dir=''
        self._auto_upload_done=True
        self.log('Uploading pending matches and lifetime stats automatically.')
        def work():
            import codmunity_player_stats as _cps
            first=_cps.upload_pending_matches(auth,user_dir) if user_dir else {'uploaded':0,'skipped':0,'failed':[]}
            second=_cps.upload_tracked_matches(auth,self.store)
            third=_cps.upload_lifetime_snapshot(auth,user_dir) if user_dir else {'uploaded':0,'skipped':0,'failed':[]}
            return first,second,third
        def done(results):
            try:
                first,second,third=results
                self.log(f"Auto-upload complete: file matches {first.get('uploaded',0)} sent, tracked matches {second.get('uploaded',0)} sent, lifetime {third.get('uploaded',0)} sent.")
            except Exception as exc:self.log('Auto-upload finished with unexpected result: '+str(exc))
            try:self.refresh_codmunity_sync()
            except Exception:pass
        def failed(message):self.log('Auto-upload failed (will retry on next launch): '+str(message or 'unknown error'))
        try:self.async_task(work,done,failed)
        except Exception as exc:
            try:self.log('Auto-upload skipped: '+str(exc))
            except Exception:pass
    def _boot_autostart(self):
        if self.tab_prototype or self.tracking:return
        if os.environ.get('PYTEST_CURRENT_TEST') is not None:return
        quiet=self.notice
        self.notice=lambda text:self.log('Auto-start deferred: '+str(text))
        try:self.start()
        except Exception as exc:
            try:self.log('Auto-start skipped: '+str(exc))
            except Exception:pass
        finally:self.notice=quiet
    def start(self):
        if self.tab_prototype:return
        if self.tracking:return
        if self.solo and self.solo.poll() is None:self.notice('The Solo worker is finishing its current request. Wait a moment, then start again.');return
        if any(j['status']=='posting' for j in self.store.history()):self.notice('Wait for the current Discord request to finish before starting another tracking mode.');return
        self.save_settings()
        try:
            import psutil
            for proc in psutil.process_iter(['name']):
                if (proc.info.get('name') or '').lower()=='vintendo match tracker.exe':self.notice('The original Vintendo Match Tracker is running. Stop it before starting the unified tracker to prevent duplicate reports.');return
        except ImportError:pass
        if not self.webhook.text().strip():self.notice('Enter a Discord webhook in Discord Settings first.');return
        if not self.store.meta('cod_bootstrapped',False):
            try:
                _,records=E.load_matches(self.settings.values)
                for match in records:
                    if isinstance(match,dict):self.store.add(match,'baseline');E.reserve_legacy_match(match)
                self.store.set_meta('cod_bootstrapped',True);E.refresh_codmunity_stats(self.settings.values,self.store);self.refresh_history();self.log(f'First-run baseline: {len(records)} existing records retained without reposting.')
            except Exception as exc:self.notice('Could not baseline local data: '+str(exc));return
        self.tracking=True;self.paused=False
        if self.mode.currentText()=='Solo Ops':
            (E.DATA/'solo-stop.flag').unlink(missing_ok=True)
            command=[sys.executable,'--solo-worker'] if getattr(sys,'frozen',False) else [sys.executable,str(E.ROOT/'app.py'),'--solo-worker']
            environment=dict(os.environ);environment['VINTENDO_PARENT_PID']=str(os.getpid())
            self.solo=subprocess.Popen(command,env=environment,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.status.setText('●  LIVE TRACKING');self.start_button.setText('●   TRACKING');self.start_button.setEnabled(False);self.start_button.setToolTip('Automatic match tracking is active.');self.stop_button.setEnabled(True);self.log('Tracking started.');self.last_poll=0;self.refresh_dashboard_status()
        if self.mode.currentText()=='Squad Night':self._start_autotrack()
    def stop(self):
        self.tracking=False;self.paused=False;self._stop_autotrack()
        if self.solo:
            if self.solo.poll() is None:
                try:
                    import psutil
                    psutil.Process(self.solo.pid).resume()
                except Exception:pass
                (E.DATA/'solo-stop.flag').touch()
                process=self.solo
                def wait_worker():
                    try:process.wait(timeout=45)
                    except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
                self.async_task(wait_worker,lambda _:self.log('Solo worker stopped cleanly.'))
        self.status.setText('●  STOPPED');self.start_button.setText('▶   START TRACKING');self.start_button.setEnabled(True);self.start_button.setToolTip('');self.stop_button.setEnabled(False);self.log('Tracking stopped.');self.refresh_dashboard_status()
    def pause(self):
        if not self.tracking:return
        if self.paused:
            self._start_autotrack()
        elif getattr(self,'autotrack_watcher',None) is not None:
            try:self.autotrack_watcher.stop(timeout=5.0)
            except Exception:pass
            self.autotrack_watcher=None;self.autotrack_state='paused'
        else:
            pass
        if self.mode.currentText()=='Solo Ops':
            if self.solo:
                import psutil
                if self.paused:psutil.Process(self.solo.pid).resume()
                else:psutil.Process(self.solo.pid).suspend()
        self.paused=not self.paused;self.status.setText('●  PAUSED' if self.paused else '●  LIVE TRACKING');self.refresh_dashboard_status()
        if hasattr(self,'pause_button'):self.pause_button.setText('▶   RESUME TRACKING' if self.paused else 'Ⅱ   PAUSE TRACKING')
    def tick(self):
        if not self.tracking or self.paused:return
        if self.solo and self.solo.poll() is not None:self.log('Solo worker stopped. See Activity Logs for details.');self.stop();return
        now=time.monotonic()
        if now-self.last_poll>=self.poll.value() and not self.poll_busy:self.last_poll=now;self.poll_records()
        if self.mode.currentText()=='Squad Night' and self.auto_detect.isChecked() and now-self.last_auto>=AUTO_OCR_INTERVAL_SECONDS and not self.capture_busy:
            if self.autotrack_watcher is not None and self.autotrack_watcher.is_alive():
                self.last_auto=now;return
            self.last_auto=now;self.capture_once(automatic=True)
    def _autotrack_wanted(self):
        if getattr(self,'tab_prototype',False):return False
        if self.mode.currentText()!='Squad Night':return False
        return bool(self.settings.values.get('autotrack_enabled',True))
    def _on_autotrack_state(self,state):
        def apply():
            self.autotrack_state=str(state);self.log(f'Autotrack: {state}')
        QTimer.singleShot(0,apply)
    def _on_autotrack_log(self,text):
        QTimer.singleShot(0,lambda:None if getattr(self,'closing',False) else self.log(str(text)))
    def _on_autotrack_draft(self,draft):
        def apply():
            self._watcher_draft=dict(draft or {});self._watcher_match_id=draft.get('match_id') if isinstance(draft,dict) else None
            self.log(f"Autotrack draft: {draft.get('parser','?')}")
            try:self.refresh_history()
            except Exception:pass
        QTimer.singleShot(0,apply)
    def _sync_discord_sender(self,store,row):
        mid=str(row.get('match_id') or '')
        if not mid:return True
        job=store.get(mid)
        if not job:return True
        status=str(job.get('status') or '')
        if status in ('delivered','solo_delivered'):return True
        if status in ('posting','failed','uncertain','baseline'):return False
        try:players=json.loads(job.get('players') or '[]')
        except (ValueError,TypeError):players=[]
        if not any(isinstance(p,dict) and p.get('verified') for p in players or []):return False
        poster=str(job.get('poster') or job.get('image') or '')
        if not poster:return False
        url=self.webhook.text().strip()
        if not url:return False
        try:match=json.loads(job.get('payload') or '{}')
        except (ValueError,TypeError):return False
        if not store.reserve(mid,allow_both=False):return False
        content=E.recap(match,players,snapshot=store.meta('codmunity_stats'))
        try:
            message_id=E.send_discord(url,poster,content)
            store.update(mid,status='delivered',message_id=message_id or '',error='');E.reserve_legacy_match(match);self.log('Discord sync delivered: '+mid);return True
        except Exception as exc:
            store.update(mid,status='uncertain',error=str(exc)[:300]);raise
    def _start_autotrack(self):
        self._stop_autotrack()
        if not self._autotrack_wanted():return
        try:
            from autotrack import AutotrackWatcher,SyncWorker
            service=self.ensure_capture()
        except Exception as exc:
            self.log('Autotrack unavailable: '+str(exc));return
        profile=str(self.settings.values.get('scoreboard_profile','auto') or 'auto')
        watcher=AutotrackWatcher(service,self.store,dict(self.settings.values),profile=profile,on_state=self._on_autotrack_state,on_draft=self._on_autotrack_draft,on_log=self._on_autotrack_log,own_capture=False)
        worker=SyncWorker(self.store,sender=self._sync_discord_sender,on_log=self._on_autotrack_log)
        self.autotrack_watcher=watcher;self.sync_worker=worker
        watcher.start();worker.start();self.autotrack_state='starting'
        self.log('Autotrack started (single exe, local OCR).')
    def _stop_autotrack(self):
        for attr in ('autotrack_watcher','sync_worker'):
            worker=getattr(self,attr,None)
            if worker is not None:
                try:worker.stop(timeout=5.0)
                except Exception:pass
            setattr(self,attr,None)
        self.autotrack_state='idle'
    def poll_records(self,source=None):
        self.poll_busy=True;values=dict(self.settings.values);mode=self.mode.currentText()
        def work():
            directory,records=E.load_matches(values);E.refresh_codmunity_stats(values,self.store);boot=self.store.meta('cod_bootstrapped',False);new=[]
            state=E.read_json(E.DATA/'posted_matches.json',{}) or {};seen=set(state.get('posted_ids',[]));legacy=E.legacy()
            self._poll_touched=False
            snapshot=self.store.meta('codmunity_stats') or {}
            local_player=str(values.get('player','vintendo') or 'vintendo')
            aliases=[hit.get(key) for hit in (snapshot.get('identity') or {}).get('hits',[]) for key in ('username','displayName') if hit.get(key)]
            aliases+=[alias for player in self.store.roster() if str(player.get('name','')).casefold()==local_player.casefold() for alias in (player.get('aliases') or [])]
            for match in records:
                if not isinstance(match,dict):continue
                delivered=any(k in seen for k in legacy.match_keys(match))
                status='solo_delivered' if delivered else 'baseline' if not boot else 'observed' if mode=='Solo Ops' else 'needs_review'
                match={**match,'integration_source':'CODMunity'}
                mid,added=self.store.add(match,status)
                existing=self.store.get(mid)
                if existing:
                    enriched,_=E.enrich_codmunity_match(match,local_player,aliases)
                    updates=E.codmunity_row_updates(existing,enriched,local_player,aliases)
                    if updates:self.store.update(mid,**updates);self._poll_touched=True
                if added and boot and status not in ('solo_delivered','baseline'):
                    context=E.extract_match_context(match,'')
                    mode_known=bool(context.get('game_mode')) and not str(context['game_mode']).startswith('Not shown')
                    result_known=context.get('result') in ('Victory','Defeat')
                    if mode_known and result_known:
                        self.store.update(mid,gen_state='queued')
                        new.append(mid)
                    if status=='needs_review':E.reserve_legacy_match(match)
            self.store.set_meta('cod_bootstrapped',True);return new
        def done(new):
            self.poll_busy=False
            for mid in new:self.log('New match queued for Squad Night review: '+mid)
            if source=='player-intel':self.log(f"Match check complete: {len(new)} new; {len(self.store.history())} tracked in total.")
            if source in ('codmunity-tab','sync-tab') and hasattr(self,'sync_header_status'):self.sync_header_status.setText('CODMunity matches are in the tracker.')
            self.refresh_history()
            self._pump_full_auto()
            touched=getattr(self,'_poll_touched',False);self._poll_touched=False
            try:self.refresh_player_intel()
            except (AttributeError,RuntimeError):pass
            if new or touched:
                try:self.refresh_roster()
                except (AttributeError,RuntimeError):pass
        self.async_task(work,done,lambda _:setattr(self,'poll_busy',False))
    def ensure_capture(self):
        if self.mode.currentText()!='Squad Night':raise RuntimeError('Enable Squad Night to use scoreboard capture.')
        if self.capture is None:
            from capture_engine import CaptureEngine,StableDetector
            self.capture=CaptureEngine();self.detector=StableDetector(frames=2)
        return self.capture
    def refresh_monitors(self):
        try:
            self.outputs=self.ensure_capture().outputs();self.monitor.clear();self.monitor.addItems([x['label'] for x in self.outputs])
            saved=str(self.settings.values.get('scoreboard_monitor_device') or '')
            selected=next((i for i,item in enumerate(self.outputs) if saved and str(item.get('device_name') or '')==saved),None)
            if selected is None:
                if self.settings.values.get('scoreboard_display_setup'):
                    try:selected=max(0,min(int(self.settings.values.get('monitor',0)),len(self.outputs)-1))
                    except (TypeError,ValueError):selected=0
                else:selected=next((i for i,item in enumerate(self.outputs) if item.get('primary')),0)
            self.monitor.setCurrentIndex(selected);self.log('Display list refreshed.')
        except Exception as exc:self.log(str(exc))
    def _paint_monitor_button(self,output=None):
        button=getattr(self,'capture_setup_button',None)
        if button is None:return
        try:
            if isinstance(output,dict) and str(output.get('label') or ''):
                button.setText('Game display: '+str(output['label']))
                button.setObjectName('monitorReady')
            else:
                button.setText('Pick Game Monitor')
                button.setObjectName('')
        except (AttributeError,RuntimeError):return
        try:button.style().unpolish(button);button.style().polish(button);button.update()
        except (AttributeError,RuntimeError):pass
    def setup_scoreboard_display(self,force=False):
        already_setup=bool(self.settings.values.get('scoreboard_display_setup'))
        try:
            self.outputs=self.ensure_capture().outputs()
            if not self.outputs:raise RuntimeError('No screen outputs found.')
            self.monitor.clear();self.monitor.addItems([item['label'] for item in self.outputs])
            saved=str(self.settings.values.get('scoreboard_monitor_device') or '')
            primary_index=next((i for i,item in enumerate(self.outputs) if item.get('primary')),0)
            selected=next((i for i,item in enumerate(self.outputs) if saved and str(item.get('device_name') or '')==saved),primary_index)
            if already_setup and not saved:
                try:selected=max(0,min(int(self.settings.values.get('monitor',primary_index)),len(self.outputs)-1))
                except (TypeError,ValueError):selected=primary_index
            self.monitor.setCurrentIndex(selected)
            if already_setup and not force:
                self._paint_monitor_button(self.outputs[selected])
                return
            if len(self.outputs)>1:
                choice,ok=QInputDialog.getItem(self,'Choose Game Display','Which display do you play Call of Duty on? Resolution is detected automatically.',[item['label'] for item in self.outputs],selected,False)
                if not ok:return
                self.monitor.setCurrentIndex(max(0,[item['label'] for item in self.outputs].index(choice)))
            self._remember_scoreboard_monitor(self.outputs[self.monitor.currentIndex()])
            self.settings.values['scoreboard_display_setup']=True;self.settings.save()
            self._paint_monitor_button(self.outputs[self.monitor.currentIndex()])
            self.ocr_review_status.setText('Game display saved. The scoreboard crop will be selected automatically from its resolution.')
            self.ocr_review_status.setStyleSheet('font-size:11px;color:#56e99a;font-weight:700;padding:2px;')
        except Exception as exc:self.ocr_review_status.setText('Could not list displays: '+str(exc)[:180])
    def selected_output(self):
        if not self.outputs:self.refresh_monitors()
        if not self.outputs:raise RuntimeError('No screen outputs found.')
        return self.outputs[max(0,self.monitor.currentIndex())]
    def _remember_scoreboard_monitor(self,output):
        device_name=str(output.get('device_name') or '')
        if device_name:self.settings.values['scoreboard_monitor_device']=device_name
        self.settings.values['monitor']=max(0,self.monitor.currentIndex());self.settings.save()
    def scoreboard_profile_changed(self,_index=None):
        if not hasattr(self,'scoreboard_profile'):return
        profile=str(self.scoreboard_profile.currentData() or 'auto')
        self.settings.values['scoreboard_profile']=profile;self.settings.save()
        self.capture_subtitle.setText(self._scoreboard_capture_instruction())
    def _scoreboard_capture_instruction(self):
        return 'At The End of a match Capture a Screenshot using your hotkey and once loaded you can Generate a Ai picture to discord'
    def _scoreboard_region_for_profile(self,output,profile):
        width,height=int(output.get('width',0)),int(output.get('height',0))
        templates=MULTIPLAYER_REGION_TEMPLATES if str(profile)=='multiplayer' else SCOREBOARD_REGION_TEMPLATES
        bounds=templates.get((width,height))
        if not bounds:
            template_size=min(templates,key=lambda size:abs(size[1]-height))
            bounds=templates[template_size]
        x,y,crop_width,crop_height=bounds
        return (x/width,y/height,crop_width/width,crop_height/height)
    def _scoreboard_region(self,output):
        return self._scoreboard_region_for_profile(output,str(self.settings.values.get('scoreboard_profile','auto')))
    @staticmethod
    def _scoreboard_profile_score(text,profile):
        raw=str(text or '').casefold();tokens=set(re.findall(r'[a-z0-9]+',raw));joined=re.sub(r'[^a-z0-9]+','',raw)
        if profile=='multiplayer':
            return (4 if 'objtime' in joined or 'objectivetime' in joined else 0)+\
                   (3 if {'player','level'}<=tokens else 0)+\
                   (2 if 'hardpoint' in tokens else 0)+\
                   (1 if 'shadow' in tokens or 'company' in tokens else 0)
        return (3 if 'redeploy' in joined or 'redeploys' in joined else 0)+\
               (2 if 'damage' in tokens else 0)+\
               (2 if 'elimination' in tokens or 'eliminations' in tokens else 0)+\
               (1 if 'assist' in tokens or 'assists' in tokens else 0)+\
               (2 if 'name' in tokens and ('lvl' in tokens or 'level' in tokens) else 0)
    @staticmethod
    def _crop_scoreboard_region(frame,region):
        h,w=frame.shape[:2];rx,ry,rw,rh=(max(0.0,min(1.0,float(v))) for v in region)
        x0=max(0,min(w-1,round(rx*w)));y0=max(0,min(h-1,round(ry*h)))
        x1=max(x0+1,min(w,round((rx+rw)*w)));y1=max(y0+1,min(h,round((ry+rh)*h)))
        return frame[y0:y1,x0:x1].copy()
    def _finish_scoreboard_ocr(self,frame,text,lines,hotkey=False,automatic=False,detected_profile=None,ocr_tokens=None,ocr_region_width=None):
        if automatic:
            from capture_utils import looks_like_scoreboard
            if not self.detector.observe(text,looks_like_scoreboard(lines)):return
            self.log('Stable scoreboard detected; draft ready for review.')
        self.last_frame=frame;self.last_capture=None;self.last_detected_scoreboard_profile=detected_profile;self.last_ocr_capture_text=text;self.last_ocr_tokens=ocr_tokens or [];self.last_ocr_region_width=ocr_region_width;self.verified.setChecked(True);self.ocr_text.setPlainText(text);self.capture_preview.setPixmap(frame_pixmap(frame).scaled(650,290,Qt.KeepAspectRatio,Qt.SmoothTransformation));self.parse_ocr()
        if hotkey:self._show_capture_review();self.log('Scoreboard OCR draft is ready. Review the names and values, then save it.')
    def capture_once(self,checked=False,automatic=False,hotkey=False,save_screenshot=False):
        if self.capture_busy:
            if hotkey:self.log('Scoreboard capture is already running; wait for the current OCR draft.')
            return
        try:
            service=self.ensure_capture();output=self.selected_output()
            self._remember_scoreboard_monitor(output)
        except Exception as exc:
            if automatic:self.auto_detect.setChecked(False)
            self.log(str(exc))
            if hotkey:self.hotkey_status.setText(str(exc));self.hotkey_status.setObjectName('');self.hotkey_status.setStyleSheet('color:#56e99a;font-weight:800;');self._show_capture_review()
            return
        self.capture_busy=True
        def work():
            frame=service.grab(output,None)
            screenshot_path=None
            if save_screenshot:
                folder=self.screenshots_dir();folder.mkdir(parents=True,exist_ok=True)
                stamp=time.strftime('%Y-%m-%d_%H-%M-%S');target=folder/f'COD_{stamp}.png';sequence=2
                while target.exists():target=folder/f'COD_{stamp}_{sequence:02d}.png';sequence+=1
                from PIL import Image
                Image.fromarray(frame).save(target,format='PNG',compress_level=3)
                screenshot_path=str(target)
            profile=str(self.settings.values.get('scoreboard_profile','auto'))
            if profile=='auto':
                candidates=[]
                for candidate in ('warzone','multiplayer'):
                    region=self._scoreboard_region_for_profile(output,candidate)
                    source=self._crop_scoreboard_region(frame,region)
                    text,lines,tokens=service.recognize_with_tokens(source)
                    score=self._scoreboard_profile_score(text,candidate)
                    candidates.append((score,candidate,text,lines,tokens,source.shape[1]))
                score,profile,text,lines,tokens,region_width=max(candidates,key=lambda item:item[0])
                if score<2:
                    profile='warzone';text,lines,tokens,region_width=next((item[2],item[3],item[4],item[5]) for item in candidates if item[1]=='warzone')
                    self.log('Could not confidently identify scoreboard; using the Warzone crop. Review OCR before saving.')
                else:self.log(f"Auto-detected {profile.title()} scoreboard ({score} layout signals).")
            else:
                region=self._scoreboard_region_for_profile(output,profile)
                source=self._crop_scoreboard_region(frame,region)
                text,lines,tokens=service.recognize_with_tokens(source)
                region_width=source.shape[1]
            return frame,text,lines,profile,tokens,region_width,screenshot_path
        def done(result):
            self.capture_busy=False;frame,text,lines,profile,tokens,region_width,screenshot_path=result
            if screenshot_path:
                self.refresh_cod_ai_screenshots()
                if hasattr(self,'cod_ai_capture_status'):
                    self.cod_ai_capture_status.setText(f'Screenshot saved · {Path(screenshot_path).name} · scoreboard OCR ready')
                    self.cod_ai_capture_status.setStyleSheet('color:#56e99a;font-weight:800;')
            self._finish_scoreboard_ocr(frame,text,lines,hotkey,automatic,profile,tokens,region_width)
        def failed(message):
            self.capture_busy=False
            if hotkey:self.hotkey_status.setText('Capture failed; try Capture Screen Shot.');self.hotkey_status.setObjectName('');self.hotkey_status.setStyleSheet('color:#56e99a;font-weight:800;');self._show_capture_review()
        self.async_task(work,done,failed)
    def parse_ocr(self):
        self._parse_ocr_text(self.ocr_text.toPlainText())
    def _parse_ocr_text(self,text):
        tokens=getattr(self,'last_ocr_tokens',[]) if text==getattr(self,'last_ocr_capture_text',None) else []
        rows=E.parse_scoreboard(text,ocr_tokens=tokens,profile=getattr(self,'last_detected_scoreboard_profile',None),region_width=getattr(self,'last_ocr_region_width',None))
        roster=self.store.roster()
        self.ocr_roster_names=set()
        for row in rows:
            try:player,score=E.match_player(str(row.get('name','')),roster)
            except Exception:player=None;score=0
            if player is not None:
                row['roster_match']=True;row['roster_match_score']=score;row['roster_match_name']=player.get('name','');self.ocr_roster_names.add(str(row.get('name','')))
        rows.sort(key=lambda row:not row.get('roster_match',False))
        keys=list(dict.fromkeys(key for row in rows for key in row.get('stats',{})))
        self.set_ocr_columns(keys or ['kills','eliminations','deaths','assists','damage','score','placement','redeploys'])
        self.ocr_table.setRowCount(0)
        for row in rows:self.add_ocr_row(row=row)
        self.verified.setChecked(True)
        if rows:
            detected=getattr(self,'last_detected_scoreboard_profile',None)
            detected_label=(detected.title()+' detected · ') if detected in ('warzone','multiplayer') else ''
            mapping_label='Position-mapped · ' if rows[0].get('source')=='ocr_spatial' else ''
            self.ocr_review_status.setText(mapping_label+detected_label+f'{len(rows)} player row'+('s' if len(rows)!=1 else '')+' detected. Check names and scores against the screenshot before saving.')
            self.ocr_review_status.setStyleSheet('font-size:11px;color:#56e99a;font-weight:700;padding:2px;')
        else:
            self.ocr_review_status.setText('No player scoreboard detected. Keep the final match scoreboard visible and capture again; menu and Combat Record text will not be treated as players.')
            self.ocr_review_status.setStyleSheet('font-size:11px;color:#f2bb62;font-weight:700;padding:2px;')
        self.log(f'{len(rows)} scoreboard player rows detected. Review columns and correct values before saving.')
    def set_ocr_columns(self,keys):
        self.ocr_stat_keys=list(dict.fromkeys(str(key) for key in keys if key))
        labels={'kills':'Kills','eliminations':'Eliminations','deaths':'Deaths','assists':'Assists','damage':'Damage','score':'Score','placement':'Placement','redeploys':'Redeploys','revives':'Revives','headshots':'Headshots','kd':'K/D','downs':'Downs'}
        self.ocr_table.setColumnCount(1+len(self.ocr_stat_keys));self.ocr_table.setHorizontalHeaderLabels(['Player']+[labels.get(key,key.replace('_',' ').title()) for key in self.ocr_stat_keys])
    def add_ocr_row(self,checked=False,row=None):
        index=self.ocr_table.rowCount();self.ocr_table.insertRow(index);row=row or {'name':'','stats':{}}
        self.ocr_table.setItem(index,0,QTableWidgetItem(str(row.get('name',''))))
        name_item=self.ocr_table.item(index,0)
        if row.get('roster_match') and name_item is not None:
            name_item.setBackground(QBrush(QColor('#1d3a2a')))
            score=float(row.get('roster_match_score',0) or 0)
            name_item.setToolTip(f"Roster match: {row.get('roster_match_name','')} · {score:.0%} confidence")
        for col,key in enumerate(self.ocr_stat_keys,1):
            value=row.get('stats',{}).get(key,'')
            self.ocr_table.setItem(index,col,QTableWidgetItem('' if value is None else str(value)))
    def remove_ocr_row(self):
        if self.ocr_table.currentRow()>=0:self.ocr_table.removeRow(self.ocr_table.currentRow())
    def keep_matched_ocr_rows(self):
        roster=self.store.roster();kept=0
        for r in range(self.ocr_table.rowCount()-1,-1,-1):
            item=self.ocr_table.item(r,0);name=item.text().strip() if item else ''
            try:player,_=E.match_player(name,roster)
            except Exception:player=None
            if player is not None:kept+=1
            else:self.ocr_table.removeRow(r)
        if kept:self.log(f'Kept {kept} roster-matched row(s); removed the rest.')
        else:self.notice('No rows match the player roster.')
    def verified_players(self):
        rows=[]
        for r in range(self.ocr_table.rowCount()):
            name=self.ocr_table.item(r,0).text().strip() if self.ocr_table.item(r,0) else ''
            if not name:continue
            stats={}
            for c,key in enumerate(self.ocr_stat_keys,1):
                value=self.ocr_table.item(r,c).text().strip() if self.ocr_table.item(r,c) else ''
                if value:
                    if not re.fullmatch(r'\d{1,8}(?:\.\d{1,4})?',value):raise ValueError(f'Invalid {key} for {name}. Leave uncertain values blank.')
                    stats[key]=float(value) if '.' in value else int(value)
            rows.append(dict(name=name,stats=stats,verified=True,source='user_verified_ocr',confidence=1.0))
        return rows
    def _latest_codmunity_match(self):
        watched=getattr(self,'_watcher_match_id',None)
        if watched:
            job=self.store.get(watched)
            if job and str(job.get('status') or '') not in ('delivered','solo_delivered'):return watched
        for job in self.store.history():
            if job['status'] in ('delivered','solo_delivered'):continue
            try:payload=json.loads(job['payload'])
            except (ValueError,TypeError):continue
            if isinstance(payload,dict) and payload.get('integration_source')=='CODMunity':return job['id']
        return None
    def save_capture(self):
        if self.last_frame is None:self.notice('Capture a scoreboard first.');return False
        if not self.verified.isChecked():self.notice('Review the scoreboard and confirm the verification checkbox.');return False
        try:players=self.verified_players()
        except ValueError as exc:self.notice(str(exc));return False
        mid=self._latest_codmunity_match()
        if mid:
            existing=self.store.get(mid)
            if existing['status'] in ('delivered','solo_delivered') and not self.allow_both.isChecked():self.notice('This match was already delivered. Choose another record or explicitly enable an additional format in Discord Settings.');return False
            base_payload=json.loads(existing['payload'])
        else:
            digest=__import__('hashlib').sha256(self.last_frame.tobytes()).hexdigest()[:24]
            profile=str(getattr(self,'last_detected_scoreboard_profile',None) or self.settings.values.get('scoreboard_profile','auto'))
            mode_key='multiplayer' if profile=='multiplayer' else 'warzone' if profile=='warzone' else self.settings.values.get('game','Squad scoreboard')
            base_payload={'matchId':'capture-'+digest,'modeKey':mode_key,'playedAt':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),'result':'match','source':'screen_capture'}
        snapshot=self.store.meta('codmunity_stats') or {};aliases=[hit.get(key) for hit in (snapshot.get('identity') or {}).get('hits',[]) for key in ('username','displayName') if hit.get(key)]
        aliases+=[alias for player in self.store.roster() if str(player.get('name','')).casefold()==str(self.settings.values.get('player','vintendo')).casefold() for alias in (player.get('aliases') or [])]
        players,personal_added=E.merge_personal_row(players,base_payload,self.settings.values.get('player','vintendo'),aliases)
        if personal_added:self.log('Added your CODMunity end-game row to this scoreboard; teammates still come from reviewed OCR.')
        payload_dict=dict(base_payload)
        team_place=E.placement_from_players(players)
        if team_place is not None and not str(payload_dict.get('placement') or '').strip() and not str((payload_dict.get('stats') if isinstance(payload_dict.get('stats'),dict) else {}).get('placement') or '').strip():payload_dict['placement']=team_place
        payload=E.with_scoreboard_context(payload_dict,self.ocr_text.toPlainText())
        override='auto'
        if override in ('win','loss'):payload['result']=override;payload['poster_context']=E.extract_match_context(payload,self.ocr_text.toPlainText())
        context=E.extract_match_context(payload,self.ocr_text.toPlainText())
        if context.get('result') not in ('Victory','Defeat') and override=='auto':
            picked=self.ask_match_result()
            if picked in ('win','loss'):payload['result']=picked;payload['poster_context']=E.extract_match_context(payload,self.ocr_text.toPlainText());context=E.extract_match_context(payload,self.ocr_text.toPlainText())
            else:self.log('Save cancelled: match result is still unknown. Set the Result selector, or answer the win/loss question.');return False
        if not mid:mid,_=self.store.add(dict(base_payload))
        folder=E.DATA/'captures';folder.mkdir(parents=True,exist_ok=True);path=folder/(E.disk_id(mid)+'.png')
        from PIL import Image
        Image.fromarray(self.last_frame).save(path)
        auto_eligible=(context.get('result') in ('Victory','Defeat') and bool(context.get('game_mode')) and
                       not str(context['game_mode']).startswith('Not shown') and 1<=len(players)<=4)
        self.store.update(mid,payload=payload,capture=str(path),ocr=self.ocr_text.toPlainText(),players=json.dumps(players),status='needs_review',gen_state='queued' if auto_eligible else 'idle',prompt='',theme=None);self.active_id=mid;self.last_capture=str(path);self.refresh_history();self.load_studio(mid)
        self.flash_save_button(getattr(self,'save_capture_button',None),'Save and Generate AI Picture')
        self.log('Verified scoreboard saved. A Meta AI creative brief was prepared from the reviewed names and performance ranks.')
        self._pump_full_auto()
        return True
    def save_capture_and_generate(self):
        if not self.save_capture():return
        mid=getattr(self,'active_id',None)
        if mid and getattr(self,'_inapp_poster_active',None)==mid:return
        self.prepare_artwork()
    def ask_match_result(self):
        box=QMessageBox(self);box.setWindowTitle('Vintendo COD Tracker');box.setText('Did you win or lose?');box.setInformativeText('Neither the latest CODMunity match nor the scoreboard shows the result.');win_button=box.addButton('Win',QMessageBox.AcceptRole);lose_button=box.addButton('Lose',QMessageBox.AcceptRole);box.addButton('Cancel',QMessageBox.RejectRole);box.setDefaultButton(win_button);box.exec()
        if box.clickedButton() is win_button:return 'win'
        if box.clickedButton() is lose_button:return 'loss'
        return None
    def post_capture_only(self):
        self.last_capture=None
        self.save_capture()
        if not self.last_capture:return
        self.store.update(self.active_id,image=self.last_capture)
        self.post_active()
    # CODMunity Ladder Rank tier colors (matches the seven-tier scale).
    LADDER_TIER_COLORS={'Bot':'#aeb8c2','Casual':'#62cdff','Solid':'#9fdc3a','Sweat':'#ffffff','Demon':'#ff6f5c','Cracked':'#d394ff','Goat':'#ffc62a'}
    def refresh_roster_rank_card(self):
        if not hasattr(self,'roster_rank_tier'):return
        gray='color:#6b7280;'
        player=self._codmunity_player_record()
        if player is None:
            self.roster_rank_tier.setText('Select a player');self.roster_rank_tier.setStyleSheet('font-size:16px;font-weight:900;'+gray)
            self.roster_rank_kd.setText('');self.roster_rank_pct.setText('—')
            self.roster_rank_kdbar.setValue(0)
            for block,label in self.roster_rank_segs:
                block.setStyleSheet('background:#232833;border-radius:2px;');label.setStyleSheet('font-size:8px;color:#6b7280;')
            self.roster_rank_next.setText('');return
        game=self.codmunity_game.currentData() or 'warzone' if hasattr(self,'codmunity_game') else 'warzone'
        mode=self.codmunity_mode.currentData() or 'resurgence' if hasattr(self,'codmunity_mode') else 'resurgence'
        scope=self.codmunity_scope.currentData() or 'lifetime' if hasattr(self,'codmunity_scope') else 'lifetime'
        info=self._roster_rank_info(player,game,mode,scope)
        try:
            import player_stats_service as PSSnap
            PSSnap.record_snapshot(self.store,str(player.get('id')),game,mode,scope)
            for event in PSSnap.detect_stat_events(self.store,str(player.get('id')),game,mode,scope):
                who=player.get('display_name') or player.get('name')
                if event['type']=='rank_promotion':self.log(f"Achievement unlocked: {who} promoted to {event['to_tier']} ({game} {mode}).")
                elif event['type']=='pb_kd':self.log(f"Achievement unlocked: {who} new personal-best K/D {event['kd']:.2f} ({game} {mode}).")
        except Exception as exc:self.log('Stat snapshot skipped: '+str(exc))
        kd_value=info.get('kd');rank=info.get('rank')
        if kd_value is None:
            self.roster_rank_tier.setText('RANK UNAVAILABLE');self.roster_rank_tier.setStyleSheet('font-size:16px;font-weight:900;'+gray)
            self.roster_rank_kd.setText('K/D —');self.roster_rank_pct.setText('—')
            self.roster_rank_kdbar.setValue(0)
            for block,label in self.roster_rank_segs:
                block.setStyleSheet('background:#232833;border-radius:2px;');label.setStyleSheet('font-size:8px;color:#6b7280;')
            self.roster_rank_next.setText('Play verified matches to earn a rank.');return
        if rank is None:
            color='#e5e9ef'
            self.roster_rank_tier.setText('RANK UNAVAILABLE');self.roster_rank_tier.setStyleSheet('font-size:16px;font-weight:900;'+gray)
            self.roster_rank_kd.setText(f"{kd_value:.2f} K/D · {info.get('source')}" if info.get('source') else f'{kd_value:.2f} K/D');self.roster_rank_pct.setText('—')
            self.roster_rank_kdbar.setRange(0,1200);self.roster_rank_kdbar.setValue(min(1200,int(round(kd_value*100))))
            self.roster_rank_kdbar.setStyleSheet('QProgressBar{background:#1a1f2a;border:none;border-radius:3px;}QProgressBar::chunk{background:'+color+';border-radius:3px;}')
            for block,label in self.roster_rank_segs:
                block.setStyleSheet('background:#232833;border-radius:2px;');label.setStyleSheet('font-size:8px;color:#6b7280;')
            self.roster_rank_next.setText('Refresh community data to rank this K/D.');return
        color=self.LADDER_TIER_COLORS.get(rank['tier'],'#e5e9ef')
        self.roster_rank_tier.setText('●  '+rank['tier'].upper());self.roster_rank_tier.setStyleSheet('font-size:16px;font-weight:900;color:'+color+';')
        self.roster_rank_kd.setText(f"{kd_value:.2f} K/D · {info.get('source')}" if info.get('source') else f'{kd_value:.2f} K/D');self.roster_rank_pct.setText(f"{rank['percentile']:.1f}%")
        self.roster_rank_kdbar.setRange(0,1200);self.roster_rank_kdbar.setValue(min(1200,int(round(kd_value*100))))
        self.roster_rank_kdbar.setStyleSheet('QProgressBar{background:#1a1f2a;border:none;border-radius:3px;}QProgressBar::chunk{background:'+color+';border-radius:3px;}')
        tier_index=max(0,len(codmunity_player_stats.LADDER_TIERS)-1)
        for index,(_,tier_name) in enumerate(codmunity_player_stats.LADDER_TIERS):
            if tier_name==rank['tier']:tier_index=index;break
        for index,(block,label) in enumerate(self.roster_rank_segs):
            if index<=tier_index:
                seg_color=self.LADDER_TIER_COLORS.get(codmunity_player_stats.LADDER_TIERS[index][1],'#e5e9ef')
                block.setStyleSheet('background:'+seg_color+';border-radius:2px;')
                label.setStyleSheet('font-size:8px;font-weight:700;color:'+seg_color+';')
            else:
                block.setStyleSheet('background:#232833;border-radius:2px;');label.setStyleSheet('font-size:8px;color:#6b7280;')
        if rank['next_tier'] is not None:self.roster_rank_next.setText(f"Next: {rank['next_tier']} @ {rank['next_kd']:.2f} K/D")
        else:self.roster_rank_next.setText('MAX RANK')
    def refresh_player_intel(self):
        return
    def _codmunity_comparison_changed(self,*_):
        try:self.refresh_codmunity_panel()
        except (AttributeError,RuntimeError):pass
        try:self.refresh_roster()
        except (AttributeError,RuntimeError):pass
        try:self.refresh_roster_rank_card()
        except (AttributeError,RuntimeError):pass
        try:self.refresh_player_intel()
        except (AttributeError,RuntimeError):pass
    def _is_local_roster_player(self,player):
        wanted=str(self.settings.values.get('player','')).casefold()
        if not wanted or not isinstance(player,dict):return False
        names=[str(player.get('name') or '').casefold()]
        names+=[str(a).casefold() for a in (player.get('aliases') or []) if isinstance(a,str)]
        return wanted in names
    def _roster_rank_info(self,player,game,mode,scope):
        import player_stats_service as PSS
        snap=PSS.snapshot_for_player(self.store,player,game,mode,scope)
        totals=PSS.totals_for_player(self.store,player,game,mode)
        return {'kd':snap['kd'],'rank':snap['rank'],'source':snap['kd_source'],'record':{'matches':totals['matches'],'wins':totals['wins'],'best':totals['best_kills']}}
    def refresh_roster(self):
        self.roster_rows=self.store.roster()
        query=self.roster_search.text().strip().casefold() if hasattr(self,'roster_search') else ''
        def matches(player):
            if not query:return True
            haystack=' '.join([str(player.get('name','')),str(player.get('display_name') or ''),str(player.get('codmunity_username') or ''),' '.join(player.get('aliases',[]) or [])]).casefold()
            return query in haystack
        visible=[p for p in self.roster_rows if matches(p)]
        self.roster_visible_ids=[str(p.get('id')) for p in visible]
        self.roster_count.setText('PLAYERS (%d)'%len(self.roster_rows))
        game=self.codmunity_game.currentData() or 'warzone' if hasattr(self,'codmunity_game') else 'warzone'
        mode=self.codmunity_mode.currentData() or 'resurgence' if hasattr(self,'codmunity_mode') else 'resurgence'
        scope=self.codmunity_scope.currentData() or 'lifetime' if hasattr(self,'codmunity_scope') else 'lifetime'
        rank_by_id={}
        for player in visible:
            rank_by_id[str(player.get('id'))]=self._roster_rank_info(player,game,mode,scope)
        self.roster_list.blockSignals(True);self.roster_list.clear()
        for player in visible:
            item=QListWidgetItem();item.setData(Qt.UserRole,str(player.get('id')));item.setSizeHint(QSize(0,92))
            self.roster_list.addItem(item);self.roster_list.setItemWidget(item,self.roster_card_widget(player,rank_by_id.get(str(player.get('id')))))
        self.roster_list.blockSignals(False)
        if getattr(self,'current_player',None) in self.roster_visible_ids:
            for index in range(self.roster_list.count()):
                if str(self.roster_list.item(index).data(Qt.UserRole))==self.current_player:self.roster_list.setCurrentRow(index);break
        elif visible:self.roster_list.setCurrentRow(0)
        else:self.show_roster_empty()
    def on_roster_item_changed(self,current):
        if current is None:return
        pid=str(current.data(Qt.UserRole))
        if pid==self.current_player and not self.roster_dirty:return
        if self.roster_dirty and not self.maybe_discard_roster_edits():
            self.roster_list.blockSignals(True)
            for index in range(self.roster_list.count()):
                if str(self.roster_list.item(index).data(Qt.UserRole))==self.current_player:self.roster_list.setCurrentRow(index);break
            else:self.roster_list.setCurrentRow(-1)
            self.roster_list.blockSignals(False)
            return
        self.load_roster_player(pid)
    def maybe_discard_roster_edits(self):
        box=QMessageBox(self);box.setWindowTitle('Unsaved roster changes');box.setText('Save changes to this player before switching?')
        save_button=box.addButton('Save',QMessageBox.AcceptRole);discard_button=box.addButton('Discard',QMessageBox.DestructiveRole);box.addButton('Cancel',QMessageBox.RejectRole);box.setDefaultButton(save_button);box.exec()
        if box.clickedButton() is save_button:self.save_player();return not self.roster_dirty
        if box.clickedButton() is discard_button:return True
        return False
    def load_roster_player(self,pid):
        player=next((p for p in self.roster_rows if str(p.get('id'))==str(pid)),None)
        if player is None:self.show_roster_empty();return
        self._roster_loading=True
        try:
            self.current_player=str(player.get('id'))
            self.roster_name.setText(player.get('name',''))
            self.roster_display_name.setText(player.get('display_name') or player.get('name',''))
            self.codmunity_username.setText(player.get('codmunity_username','') or '')
            self.roster_profile_url.setText(player.get('profile_url','') or '')
            self.roster_enabled.setChecked(bool(player.get('enabled',True)))
            self.photo_consent.setChecked(bool(player.get('consent_meta',False)))
            self.photo_path.setText(player.get('photo','') or '');self.roster_photo_removed=False
            self.headshot_filename.setText(Path(player.get('photo') or '').name if player.get('photo') else 'No headshot selected')
            self.show_image(self.photo_preview,player.get('photo','') or '',420,220)
            display=str(player.get('display_name') or player.get('name',''))
            self.roster_big_name.setText(display)
            self.roster_big_sub.setText('COD: '+str(player.get('name','')))
            photo=str(player.get('photo') or '')
            self.roster_big_photo.setPixmap(self.rounded_thumb(photo,120) if photo and Path(photo).is_file() else self.default_avatar(120))
            enabled=bool(player.get('enabled',True))
            self.roster_enabled_pill.setText('✓  ENABLED' if enabled else 'DISABLED')
            self.roster_enabled_pill.setObjectName('rosterPillEnabled' if enabled else 'rosterPillDisabled')
            self.style().unpolish(self.roster_enabled_pill);self.style().polish(self.roster_enabled_pill)
            consent=bool(player.get('consent_meta',False))
            if consent:self.consent_status.setText('Consent granted — this headshot may be used in Meta AI artwork.');self.consent_status.setStyleSheet('font-size:11px;color:#4ade80;')
            else:self.consent_status.setText('No consent — this player will be shown generically (NO REFERENCE IMAGE).');self.consent_status.setStyleSheet('font-size:11px;color:#e5a13d;')
        finally:self._roster_loading=False
        self.refresh_profile_tab();self.set_roster_dirty(False);self.roster_status.setText('')
        self.refresh_codmunity_panel();self.refresh_roster_rank_card();self.refresh_player_intel()
        if hasattr(self,'roster_tabs') and hasattr(self,'_profile_tab_index') and self.roster_tabs.currentIndex()==self._profile_tab_index:self.load_roster_profile_page()
    def show_roster_empty(self):
        self._roster_loading=True
        try:
            self.current_player=None
            self.roster_big_photo.setPixmap(self.default_avatar(120));self.roster_big_name.setText('No player selected');self.roster_big_sub.setText('');self.roster_enabled_pill.setText('');self.consent_status.setText('');self.photo_path.clear();self.headshot_filename.setText('No headshot selected');self.photo_preview.clear();self.photo_preview.setText('No headshot');self.roster_profile_url.clear();self.codmunity_username.clear();self.roster_enabled.setChecked(True)
        finally:self._roster_loading=False
        self.refresh_codmunity_panel();self.refresh_roster_rank_card();self.refresh_player_intel()
    def mark_roster_dirty(self):
        if getattr(self,'_roster_loading',False):return
        self.set_roster_dirty(True)
    def set_roster_dirty(self,dirty):
        self.roster_dirty=bool(dirty)
        if hasattr(self,'roster_save_button'):self.roster_save_button.setText(('● ' if dirty else '')+'Save Player')
        if hasattr(self,'roster_status'):
            if dirty:self.roster_status.setText('Unsaved changes.')
            elif not self.roster_status.text().startswith('Saved') and not self.roster_status.text().startswith('Deleted'):self.roster_status.setText('')
    def roster_new_player(self):
        if self.roster_dirty and not self.maybe_discard_roster_edits():return
        self.new_player()
        self.roster_list.blockSignals(True);self.roster_list.clearSelection();self.roster_list.setCurrentRow(-1);self.roster_list.blockSignals(False)
        self.roster_tabs.setCurrentIndex(0);self.roster_name.setFocus()
    def new_player(self):
        self._roster_loading=True
        try:
            self.current_player=None;self.roster_name.clear();self.roster_display_name.clear();self.codmunity_username.clear();self.roster_profile_url.clear();self.photo_path.clear();self.headshot_filename.setText('No headshot selected');self.photo_preview.clear();self.photo_preview.setText('No headshot');self.photo_consent.setChecked(False);self.roster_enabled.setChecked(True);self.roster_photo_removed=False
            self.roster_big_photo.setPixmap(self.default_avatar(120));self.roster_big_name.setText('New player');self.roster_big_sub.setText('Fill in the details, then Save Player.');self.roster_enabled_pill.setText('');self.consent_status.setText('')
        finally:self._roster_loading=False
        self.set_roster_dirty(False);self.roster_status.setText('');self.refresh_codmunity_panel();self.refresh_roster_rank_card();self.refresh_player_intel()
    def pick_photo(self):
        path,_=QFileDialog.getOpenFileName(self,'Choose player headshot','','Images (*.png *.jpg *.jpeg *.webp)')
        if path:self.photo_path.setText(path);self.headshot_filename.setText(Path(path).name);self.show_image(self.photo_preview,path,420,220);self.roster_photo_removed=False;self.mark_roster_dirty()
    def remove_roster_photo(self):
        self.photo_path.clear();self.headshot_filename.setText('No headshot selected');self.photo_preview.clear();self.photo_preview.setText('No headshot');self.roster_photo_removed=True;self.mark_roster_dirty()
    def update_codmunity_profile_link(self,username):
        if getattr(self,'_roster_loading',False):return
        username=str(username or '').strip().lstrip('@').strip()
        url='https://codmunity.gg/profile/'+quote(username,safe='')+'/stats' if username else ''
        self.roster_profile_url.setText(url)
    def refresh_profile_tab(self):
        """Show the current player's profile link; live while typing."""
        if not hasattr(self,'profile_link_view'):return
        url=(self.roster_profile_url.text().strip() if hasattr(self,'roster_profile_url') else '')
        if url:safe=url.replace('"','%22');self.profile_link_view.setText('<a href="'+safe+'" style="color:#fdba74;">'+safe+'</a>')
        else:self.profile_link_view.setText('No profile link set — add one in Player Info.')
        if hasattr(self,'profile_open_button'):self.profile_open_button.setEnabled(bool(url))
    def _profile_page_url(self):
        url=(self.roster_profile_url.text().strip() if hasattr(self,'roster_profile_url') else '')
        if not url and getattr(self,'current_player',None):
            player=next((p for p in getattr(self,'roster_rows',[]) if str(p.get('id'))==str(self.current_player)),None)
            url=str((player or {}).get('profile_url') or '').strip()
        if url and not re.match(r'^https?://',url,re.I):url='https://'+url
        return url
    def load_roster_profile_page(self):
        """Load the player's CODMunity profile inside the PROFILE tab."""
        if getattr(self,'profile_view',None) is None:return
        url=self._profile_page_url()
        if url==getattr(self,'_profile_loaded_url',''):return
        self._profile_loaded_url=url
        if url:self.profile_view.setUrl(QUrl(url))
        else:self.profile_view.setHtml('<html><body style="background:#0a0d13;color:#8b93a1;font-family:sans-serif;padding:40px;text-align:center;">No profile link set — add one in Player Info.</body></html>')
    def _on_roster_tab_changed(self,index):
        if index==getattr(self,'_profile_tab_index',-1):self.load_roster_profile_page()
    def profile_page_back(self):
        if getattr(self,'profile_view',None) is not None:self.profile_view.back()
    def profile_page_reload(self):
        if getattr(self,'profile_view',None) is not None:self.profile_view.reload()
    def open_roster_profile(self):
        url=self._profile_page_url()
        if not url:self.roster_status.setText('No profile link set for this player.');return
        if QDesktopServices.openUrl(QUrl(url)):self.roster_status.setText('Opened '+url[:120]+'.')
        else:self.roster_status.setText('Could not open the profile link.')
    def save_player(self):
        name=self.roster_name.text().strip()
        if not name:self.roster_status.setText('Enter the exact COD username before saving.');self.roster_tabs.setCurrentIndex(0);self.roster_name.setFocus();return
        pid=self.current_player or uuid.uuid4().hex;photo=self.photo_path.text()
        stored_photo=next((p.get('photo','') for p in self.store.roster() if str(p.get('id'))==str(pid)),'')
        if not self.photo_consent.isChecked() and (photo or (stored_photo and not self.roster_photo_removed)):
            reminder=QMessageBox(QMessageBox.Warning,'Photo permission not confirmed','This player has not been marked as having agreed to Meta AI photo use. Their headshot will not be sent. If they have agreed, go back and check the permission box.',QMessageBox.NoButton,self)
            go_back=reminder.addButton('Go Back and Check',QMessageBox.RejectRole)
            save_without=reminder.addButton('Save Without Photo',QMessageBox.AcceptRole)
            reminder.setDefaultButton(go_back);reminder.exec()
            if reminder.clickedButton() is go_back:return
        if photo:
            from PIL import Image,ImageOps
            folder=E.DATA/'headshots';folder.mkdir(parents=True,exist_ok=True);target=folder/(pid+'.jpg')
            try:
                with Image.open(photo) as source:im=ImageOps.exif_transpose(source).convert('RGB');im.thumbnail((1600,1600));im.save(target,quality=94)
            except Exception as exc:self.notice('Could not read headshot: '+str(exc));return
            photo=str(target)
        elif not self.roster_photo_removed:photo=stored_photo
        display_name=self.roster_display_name.text().strip() or name
        codmunity_username=self.codmunity_username.text().strip()
        for other in self.store.roster():
            if str(other.get('id'))==str(pid):continue
            if codmunity_username and str(other.get('codmunity_username') or '').casefold()==codmunity_username.casefold():
                self.roster_status.setText('That CODMunity username is already linked to '+str(other.get('name') or 'another player')+'.');self.roster_tabs.setCurrentIndex(0);self.codmunity_username.setFocus();return
        saved_player=self._codmunity_player_record(pid) or {'id':pid}
        saved_aliases=list(saved_player.get('aliases') or [])
        previous_username=str(saved_player.get('codmunity_username') or '')
        if previous_username.casefold()!=codmunity_username.casefold():
            for key in ('codmunity_uid','codmunity_display_name','codmunity_identity_updated_at'):
                saved_player.pop(key,None)
        saved_player.update(dict(id=pid,name=name,display_name=display_name,aliases=saved_aliases,profile_url=self.roster_profile_url.text().strip(),photo=photo,enabled=self.roster_enabled.isChecked(),consent_meta=self.photo_consent.isChecked(),codmunity_username=codmunity_username))
        self.store.save_player(saved_player);self.current_player=pid;self.set_roster_dirty(False);self.refresh_roster();self.roster_status.setText('Saved '+name+'.');self.load_roster_player(pid);self.refresh_talent_friends();self.refresh_squad_intel();self.flash_save_button(getattr(self,'roster_save_button',None),'Save Player');self.log('Player saved: '+name+' (poster name: '+display_name+')')
    def delete_player(self):
        if not self.current_player:return
        player=next((p for p in self.roster_rows if str(p.get('id'))==str(self.current_player)),None)
        label=player.get('name','this player') if player else 'this player'
        if self.ask("Delete player '"+label+"'? This cannot be undone."):
            self.store.delete_player(self.current_player);self.new_player();self.refresh_roster();self.refresh_talent_friends();self.refresh_squad_intel();self.roster_status.setText('Deleted '+label+'.')
    def refresh_history(self):
        if not hasattr(self,'history'):return
        rows=self.store.history()
        active_id=getattr(self,'active_id',None)
        current=self.store.get(active_id) if active_id else None
        if current and current['status'] in ('delivered','solo_delivered'):
            active_id=None
            self.active_id=None
        if active_id and self.store.get(active_id):picked=active_id
        else:picked=next((r['id'] for r in rows if r['status'] not in ('baseline','solo_delivered','delivered')),None)
        if picked and picked!=getattr(self,'active_id',None):self.load_studio(picked)
        elif picked is None and getattr(self,'active_id',None) is None:
            try:self._clear_studio_for_next()
            except (AttributeError,RuntimeError):pass
        self.dashboard_metrics['matches'].setText(f'{len(rows):,}')
        self.dashboard_metrics['squad_posts'].setText(f"{sum(j['status']=='delivered' for j in rows):,}")
        review_statuses={'needs_review','art_ready','ready','failed','uncertain','awaiting_artwork'}
        self.dashboard_metrics['reviews'].setText(f"{sum(j['status'] in review_statuses for j in rows):,}")
        try:period_rows=self._history_period_rows(rows) if hasattr(self,'history_period') else rows
        except Exception:period_rows=rows
        try:summary=self._match_summary(period_rows)
        except Exception:summary={}
        self.dashboard_metrics['games'].setText(str(summary.get('games',len(rows))))
        self.dashboard_metrics['win_rate'].setText(str(summary.get('win_rate','—')))
        self.dashboard_metrics['kd'].setText(str(summary.get('kd','—')))
        self.dashboard_metrics['kills_per_game'].setText(str(summary.get('kills_per_game','—')))
        self.refresh_match_center(rows)
        self.refresh_dashboard_status();self.refresh_match_history_view(rows);self._refresh_dashboard_stats()

    def _match_epoch(self,match,created_fallback=None):
        value=match.get('playedAt') or match.get('date') or match.get('timestamp')
        if not value:return created_fallback
        try:
            from datetime import datetime
            parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
            return parsed.timestamp()
        except (TypeError,ValueError,OverflowError):return created_fallback

    def _history_period_rows(self,rows):
        ordered=sorted(rows,key=lambda job:self._match_epoch(json.loads(job['payload']),job.get('created',0)) or 0,reverse=True)
        if self.history_period=='recent':return ordered
        if self.history_period=='today':
            from datetime import date,datetime
            today=date.today();return [job for job in ordered if (epoch:=self._match_epoch(json.loads(job['payload']))) is not None and datetime.fromtimestamp(epoch).date()==today]
        if self.history_period=='session':
            return [job for job in ordered if (epoch:=self._match_epoch(json.loads(job['payload']))) is not None and epoch>=self.session_started]
        return ordered

    @staticmethod
    def _known_number(stats,key):
        value=stats.get(key) if isinstance(stats,dict) else None
        if isinstance(value,bool) or not isinstance(value,(int,float)) or value<0:return None
        return float(value)

    def _match_summary(self,rows):
        wins=losses=0;kills_total=kd_kills_total=deaths_total=0.0;kills_games=0;kd_games=0;best_kills=None;best_damage=None
        for job in rows:
            match=json.loads(job['payload']);outcome=str(match.get('result') or '').lower()
            if outcome in ('win','victory'):wins+=1
            elif outcome in ('loss','defeat'):losses+=1
            stats=match.get('stats') if isinstance(match.get('stats'),dict) else {}
            kills=self._known_number(stats,'kills');deaths=self._known_number(stats,'deaths')
            if kills is not None:kills_total+=kills;kills_games+=1
            if kills is not None and deaths is not None:kd_kills_total+=kills;deaths_total+=deaths;kd_games+=1
            elims=self._known_number(stats,'eliminations');best_source=kills if kills is not None else elims
            if best_source is not None:best_kills=max(best_kills or 0,best_source)
            damage=self._known_number(stats,'damage')
            if damage is not None:best_damage=max(best_damage or 0,damage)
        known=wins+losses
        ratio=f'{kd_kills_total/deaths_total:.2f}' if kd_games and deaths_total>0 else '—'
        return {'games':len(rows),'wins':wins,'losses':losses,'known':known,'win_rate':f'{wins/known*100:.0f}%' if known else '—','kd':ratio,'kd_games':kd_games,'kills_per_game':f'{kills_total/kills_games:.1f}' if kills_games else '—','kills_games':kills_games,'kills_total':kills_total,'best_kills':best_kills,'best_damage':best_damage}

    def refresh_match_history_view(self,all_rows=None):
        all_rows=all_rows if all_rows is not None else self.store.history();rows=self._history_period_rows(all_rows);self.history_rows=rows;self.history.setRowCount(len(rows))
        selected=self._match_summary(rows);lifetime=self._match_summary(all_rows);period_label={'recent':'Recent · all records','today':'Today','session':'This session'}.get(self.history_period,'All records')
        self.history_summary['games'][0].setText(str(selected['games']));self.history_summary['games'][1].setText(period_label)
        self.history_summary['win_rate'][0].setText(selected['win_rate']);self.history_summary['win_rate'][1].setText(f"{selected['wins']} wins · {selected['losses']} losses")
        self.history_summary['kd'][0].setText(selected['kd']);self.history_summary['kd'][1].setText(f"{selected['kd_games']} matches · all tracked {lifetime['kd']}")
        self.history_summary['kills_per_game'][0].setText(selected['kills_per_game']);self.history_summary['kills_per_game'][1].setText(f"From {selected['kills_games']} matches with kills recorded"+(f" · Best {selected['best_kills']:g} kills"+(f" / {selected['best_damage']:,.0f} dmg" if selected['best_damage'] is not None else '') if selected['best_kills'] is not None else ''))
        self.history.setToolTip('Summary values use recorded match-level statistics only. Missing results and statistics are excluded from the relevant calculations.')
        for row,job in enumerate(rows):
            match=json.loads(job['payload']);stats=match.get('stats') if isinstance(match.get('stats'),dict) else {}
            raw_result=str(match.get('result') or '').lower();result={'win':'WIN','victory':'WIN','loss':'LOSS','defeat':'LOSS'}.get(raw_result,'—')
            place=match.get('placement')
            if place is None:place=stats.get('placement')
            values=[job['id'][-12:],result,self._display_value(place),str(match.get('modeKey',match.get('mode','—')) or '—').replace('_',' ').title(),self._display_value(stats.get('kills')),self._display_value(stats.get('eliminations')),self._display_value(stats.get('damage')),self._display_value(stats.get('deaths')),self._display_value(stats.get('assists')),self._display_time(match.get('playedAt') or match.get('date') or match.get('timestamp')),str(job.get('status','—')).replace('_',' ').upper(),str(job.get('attempts',0))]
            for col,value in enumerate(values):
                item=QTableWidgetItem(value)
                if col==0:item.setToolTip(job['id'])
                if col==1:item.setForeground(QColor('#56e99a' if result=='WIN' else '#ff626d' if result=='LOSS' else '#bbc4d0'))
                self.history.setItem(row,col,item)

    @staticmethod
    def _display_value(value):
        if value is None or value=='':return '—'
        return f'{value:g}' if isinstance(value,float) else str(value)

    @staticmethod
    def _display_time(value):
        if not value:return '—'
        try:
            from datetime import datetime
            parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
            if parsed.tzinfo:parsed=parsed.astimezone()
            return parsed.strftime('%b %d, %Y  %I:%M %p')
        except (TypeError,ValueError):return str(value)

    def open_latest_match(self):
        if getattr(self,'latest_job_id',None):self.load_studio(self.latest_job_id);self.nav.setCurrentRow(5)
    def _latest_job(self,rows):
        if not rows:return None
        def _key(job):
            try:payload=json.loads(job['payload'])
            except (TypeError,ValueError):payload={}
            epoch=self._match_epoch(payload,job.get('created',0))
            return epoch if epoch is not None else 0
        return max(rows,key=_key)
    def refresh_match_center(self,rows):
        if not hasattr(self,'latest_card'):return
        total=len(rows)
        self.center_count.setText(f"{total} match{'es' if total!=1 else ''} tracked")
        job=self._latest_job(rows)
        if job is None:
            self.latest_job_id=None;self.latest_result.setText('-');self.latest_result.setStyleSheet('font-size:26px;font-weight:900;color:#f2f4f7;');self.latest_detail.setText('No matches tracked yet.');self.latest_stats.setText('');self.latest_status.setText('');self.latest_review.setEnabled(False);return
        match=json.loads(job['payload']);stats=match.get('stats') if isinstance(match.get('stats'),dict) else {}
        raw_result=str(match.get('result') or '').lower();result={'win':'WIN','victory':'WIN','loss':'LOSS','defeat':'LOSS'}.get(raw_result,'-')
        color='#56e99a' if result=='WIN' else '#ff626d' if result=='LOSS' else '#bbc4d0'
        place=match.get('placement')
        if place is None:place=stats.get('placement')
        mode=str(match.get('modeKey',match.get('mode','-')) or '-').replace('_',' ').title()
        played=self._display_time(match.get('playedAt') or match.get('date') or match.get('timestamp'))
        self.latest_job_id=job['id']
        self.latest_result.setText(result);self.latest_result.setStyleSheet('font-size:26px;font-weight:900;color:'+color+';')
        self.latest_detail.setText(f"{self._display_value(place)} · {mode} · {played}")
        self.latest_stats.setText(f"Kills {self._display_value(stats.get('kills'))} · Elims {self._display_value(stats.get('eliminations'))} · Damage {self._display_value(stats.get('damage'))} · Deaths {self._display_value(stats.get('deaths'))} · Assists {self._display_value(stats.get('assists'))}")
        status=str(job.get('status','-')).replace('_',' ').upper();attempts=job.get('attempts',0)
        hint=' · last post failed, review to retry' if job.get('status')=='failed' else ''
        self.latest_status.setText(f"Status: {status} · {attempts} attempt(s){hint}")
        actionable=job.get('status') not in ('baseline','uncertain')
        self.latest_review.setEnabled(actionable)
        self.latest_review.setToolTip('Starts Meta AI generation. A match already delivered to Discord will not be posted twice.' if actionable else 'This match cannot be started automatically.')
    def start_latest_auto_job(self):
        """Start the latest match in the no-click Meta AI poster flow."""
        mid=getattr(self,'latest_job_id',None)
        job=self.store.get(mid) if mid else None
        if not job:
            self.notice('There is no match ready for an automatic poster job yet.')
            return
        if job.get('status') in ('baseline','uncertain'):
            self.notice('This match cannot be started automatically. Resolve it in Match History or capture a new scoreboard.')
            return
        if self._latest_auto_job_id:
            self.statusBar().showMessage('An automatic poster job is already running.',5000)
            return
        no_discord=job.get('status') in ('delivered','solo_delivered')
        try:webhook= self.settings.webhook().strip()
        except Exception:webhook=self.webhook.text().strip()
        if not no_discord and not webhook:
            self.notice('Add a Discord webhook in Settings before starting the full automatic job.')
            return
        if not no_discord:
            self.auto_post.setChecked(True)
            self.settings.values['auto_post']=True
            try:self.settings.save()
            except Exception:pass
        self._latest_auto_job_id=mid;self._latest_auto_no_discord=no_discord
        self._set_latest_auto_busy(True,'STARTING AI JOB')
        QTimer.singleShot(0,lambda mid=mid:self._launch_latest_auto_job(mid))
    def _launch_latest_auto_job(self,mid):
        try:
            launched=self._prepare_and_launch(mid,manual=False)
        except Exception as exc:
            self._latest_auto_job_id=None
            self._set_latest_auto_busy(False,'JOB FAILED')
            self.notice('Could not start the automatic poster job: '+str(exc)[:220])
            self.log('Dashboard auto job failed for '+str(mid)+': '+str(exc)[:220])
            return
        if launched:
            self.nav.setCurrentRow(5)
            self.job_status.setText('Meta AI poster job started.' if self._latest_auto_no_discord else 'Full auto started: Meta AI will generate the poster, then the tracker will deliver it to Discord.')
            self._set_latest_auto_busy(True,'META AI WORKING')
        else:
            self._latest_auto_job_id=None
            self._set_latest_auto_busy(False,'START FULL AUTO JOB')
            self.notice('The Meta AI browser automation could not start. Verify the Meta AI login and try again.')
    def _pulse_latest_auto_button(self):
        button=getattr(self,'latest_review',None)
        if button is None:return
        button.setProperty('pulsePhase',not bool(button.property('pulsePhase')))
        button.style().unpolish(button);button.style().polish(button);button.update()
    def _set_latest_auto_busy(self,busy,label='START FULL AUTO JOB'):
        button=getattr(self,'latest_review',None)
        if button is None:return
        button.setText(label);button.setProperty('jobRunning',bool(busy))
        if busy:
            button.setProperty('pulsePhase',True);button.setEnabled(False)
            if not self.latest_auto_pulse_timer.isActive():self.latest_auto_pulse_timer.start()
        else:
            self.latest_auto_pulse_timer.stop();button.setProperty('pulsePhase',False)
            current=self.store.get(self.latest_job_id) if self.latest_job_id else None
            button.setEnabled(bool(current and current.get('status') not in ('baseline','uncertain')))
            if label in ('JOB COMPLETE','POSTER READY','JOB FAILED'):
                QTimer.singleShot(2600,lambda:self._set_latest_auto_busy(False,'START FULL AUTO JOB'))
        button.style().unpolish(button);button.style().polish(button);button.update()
    def _finish_latest_auto_job(self,mid,label):
        if getattr(self,'_latest_auto_job_id',None)!=mid:return
        self._latest_auto_job_id=None;self._latest_auto_no_discord=False
        self._set_latest_auto_busy(False,label)
    def _set_dashboard_health(self,key,value,good=None):
        if key not in self.dashboard_health:return
        indicator,label=self.dashboard_health[key]
        online=bool(good) if good is not None else str(value).casefold() in ('online','ready','true')
        label.setText('Online' if online else 'Offline')
        color='#56e99a' if online else '#ff626d'
        label.setStyleSheet('color:'+color+';font-size:11px;font-weight:800;')
        indicator.setStyleSheet('color:'+color+';')
    def refresh_dashboard_status(self):
        if not hasattr(self,'dashboard_health'):return
        resolved=self._effective_stats_dir() if hasattr(self,'stats_dir') else ''
        folder=Path(resolved) if resolved else None
        folder_ready=bool(folder and folder.is_dir())
        self._set_dashboard_health('codmunity','Online' if folder_ready else 'Offline',folder_ready)
        self._set_dashboard_health('ocr','Online',True)
        try:discord_ready=bool(self.settings.webhook().strip())
        except Exception:discord_ready=bool(hasattr(self,'webhook') and self.webhook.text().strip())
        self._set_dashboard_health('discord','Online' if discord_ready else 'Offline',discord_ready)
        if self.tracking and self.paused:tracking='PAUSED';color='#e3b95e'
        elif self.tracking:tracking='LIVE TRACKING';color='#56e99a'
        else:tracking='STANDBY';color='#a7b0bd'
        self.status.setText('●  '+tracking);self.status.setStyleSheet(f'background:#111720;border:1px solid {color};border-radius:14px;color:{color};font-size:12px;font-weight:900;padding:4px 14px;')
        if hasattr(self,'footer_tracking'):self.footer_tracking.setText('●  '+tracking);self.footer_tracking.setStyleSheet('font-weight:800;color:'+color)
        if hasattr(self,'blink_timer'):
            if self.tracking and not self.paused:
                if not self.blink_timer.isActive():self.blink_timer.start()
            else:self.blink_timer.stop()
        latest=json.loads(self.history_rows[0]['payload']) if getattr(self,'history_rows',None) else {}
        played=latest.get('playedAt') or latest.get('date') or latest.get('timestamp')
        if hasattr(self,'footer_match'):self.footer_match.setText('Last match: '+(self._display_time(played) if played else '—'))
    def _blink_live_indicator(self):
        if not (self.tracking and not self.paused):return
        try:pill=self.status
        except (AttributeError,RuntimeError):return
        self._blink_on=not getattr(self,'_blink_on',True)
        color='#56e99a' if self._blink_on else '#1f6b44'
        try:pill.setStyleSheet(f'background:#111720;border:1px solid {color};border-radius:18px;color:{color};font-size:13px;font-weight:900;padding:10px 16px;')
        except (AttributeError,RuntimeError):pass
    def selected_job(self):
        row=self.history.currentRow();return self.history_rows[row] if 0<=row<len(self.history_rows) else None
    def review_selected(self):
        job=self.selected_job()
        if job:self.load_studio(job['id']);self.nav.setCurrentRow(5)
    def open_latest(self):
        rows=self.store.history()
        if rows:self.load_studio(next((r['id'] for r in rows if r['status'] not in ('baseline','solo_delivered','delivered')),rows[0]['id']));self.nav.setCurrentRow(5)
    def load_studio(self,mid):
        job=self.store.get(mid)
        if not job:return
        self.active_id=mid;match=json.loads(job['payload']);context=E.extract_match_context(match,job.get('ocr',''));self.studio_match.setText(mid+'  /  '+job['status'].upper()+'  ·  '+context['game_mode']+'  ·  '+context['result']+('  ·  '+context['placement'] if not context['placement'].startswith('Not shown') else ''));self.show_image(self.art_preview,job['image'],800,300)
        try:self.opt_game_mode.setText(str(context.get('game_mode') or '—'))
        except (AttributeError,RuntimeError):pass
        try:self.opt_result.setText(str(context.get('result') or '—'))
        except (AttributeError,RuntimeError):pass
        try:placement=str(context.get('placement') or '—');self.opt_placement.setText('—' if placement.startswith('Not shown') else placement)
        except (AttributeError,RuntimeError):pass
        players=json.loads(job['players'] or '[]');self.ensure_match_prompt(job,players);self.preview_reference_photos(players)
        if job['capture']:
            self.show_image(self.capture_preview,job['capture'],650,290);self.ocr_text.setPlainText(job['ocr'])
            if self.mode.currentText()=='Squad Night':
                try:
                    import numpy as np
                    from PIL import Image
                    with Image.open(job['capture']) as image:self.last_frame=np.array(image.convert('RGB'))
                    saved_players=json.loads(job['players']);keys=list(dict.fromkeys(key for player in saved_players for key in (player.get('stats') or {})))
                    self.set_ocr_columns(keys or ['kills','eliminations','deaths','assists','damage','score','placement','redeploys']);self.ocr_table.setRowCount(0);self.verified.setChecked(True)
                    for player in saved_players:self.add_ocr_row(row=player)
                    self.verified.setChecked(True)
                except (OSError,ValueError):self.last_frame=None
        self.job_status.setText(job['error'] or 'Ready for review')
    def _clear_studio_for_next(self,message=''):
        """One-shot Studio: drop the posted match so the next screenshot starts fresh."""
        self.active_id=None
        try:self.studio_match.setText('Ready for the next match — capture a scoreboard.')
        except (AttributeError,RuntimeError):pass
        try:self.prompt.clear()
        except (AttributeError,RuntimeError):pass
        for attr in ('opt_game_mode','opt_result','opt_placement'):
            try:getattr(self,attr).setText('—')
            except (AttributeError,RuntimeError):pass
        try:self.art_preview.setText('Finished artwork preview')
        except (AttributeError,RuntimeError):pass
        try:self.photo_assignments.setText('Consented reference photos are matched automatically when available.')
        except (AttributeError,RuntimeError):pass
        try:self.generate_poster_button.setEnabled(False)
        except (AttributeError,RuntimeError):pass
        try:self.job_status.setText(message or 'Ready for the next match.')
        except (AttributeError,RuntimeError):pass
    def view_source(self):
        job=self.selected_job()
        if not job:return
        dialog=QDialog(self);dialog.setWindowTitle('Stored match data and provenance');dialog.resize(850,600);layout=QVBoxLayout(dialog);text=QPlainTextEdit(json.dumps({**job,'payload':json.loads(job['payload']),'players':json.loads(job['players'])},indent=2));text.setReadOnly(True);layout.addWidget(text);dialog.exec()
    def _ai_stat_block(self,job,players):
        """Verified-only stat lines for the AI prompt; '' when opted out or empty."""
        try:
            if not getattr(self,'include_stats_checkbox',None) or not self.include_stats_checkbox.isChecked():return ''
        except (AttributeError,RuntimeError):return ''
        try:
            import player_stats_service as PSS
        except ImportError:return ''
        match=json.loads(job.get('payload') or '{}') if isinstance(job.get('payload'),str) else (job.get('payload') or {})
        if not isinstance(match,dict):return ''
        game=str(match.get('game') or 'warzone');mode=str(match.get('modeKey') or match.get('mode') or 'resurgence')
        roster=self.store.roster()
        blocks=[]
        for row in players or []:
            if not isinstance(row,dict) or not row.get('verified'):continue
            stats=row.get('stats') if isinstance(row.get('stats'),dict) else {}
            detail=E.match_player_detail(str(row.get('name') or ''),roster)
            if detail.get('status')!='matched' or not detail.get('player'):continue
            snap=PSS.snapshot_for_player(self.store,detail['player'],game,mode,'lifetime')
            kills=stats.get('kills',stats.get('eliminations'));deaths=stats.get('deaths')
            block=PSS.prompt_stat_block(snap,{'kills':kills,'deaths':deaths,'result':match.get('result')})
            if block:blocks.append(block)
            if len(blocks)>=4:break
        if not blocks:return ''
        return '\n'.join(blocks)
    def ensure_match_prompt(self,job,players=None):
        players=json.loads(job['players'] or '[]') if players is None else players
        existing=str(job.get('prompt') or '').split('\nREFERENCE PHOTO MAPPING:')[0]
        if any(isinstance(p,dict) and p.get('verified') for p in players):
            legacy_fields=('Unique match ID:','Creative variation ID:','performance_rank','ranking_basis','CODMunity benchmark context')
            if E.MATCH_POSTER_PROMPT_MARKER not in existing or any(field in existing for field in legacy_fields):
                try:existing=E.match_poster_prompt(players,json.loads(job['payload']),job.get('ocr',''),roster=self.store.roster(),snapshot=self.store.meta('codmunity_stats'))
                except ValueError as exc:existing='';self.job_status.setText(str(exc))
                else:self.store.update(job['id'],prompt=existing,theme=None)
            marker='PLAYER STATISTICS:'
            if marker in existing:existing=existing.split(marker)[0].rstrip()
            block=self._ai_stat_block(job,players)
            if block:existing=(existing+'\n'+marker+'\n'+block).rstrip()
            self.prompt.setPlainText(existing)
            self.generate_poster_button.setEnabled(bool(existing))
            return existing
        self.prompt.clear();self.generate_poster_button.setEnabled(False)
        self.photo_assignments.setText('Save a reviewed OCR scoreboard with kills, damage, or score to prepare a ranked poster prompt.')
        return ''
    def preview_reference_photos(self,players):
        assignments=[]
        while self.studio_heads.count():
            widget=self.studio_heads.takeAt(0).widget()
            if widget:widget.deleteLater()
        mappings=E.match_reference_photos(players,self.store.roster())
        for position,mapping in enumerate(mappings):
            if mapping['status']=='ready':
                assignments.append(mapping['username']+' → poster name: '+mapping['posterName']+' (consented photo matched)')
                cell=QVBoxLayout();cell.setSpacing(4);thumbnail=QLabel();thumbnail.setToolTip(mapping['username']+' → '+mapping['posterName']);thumbnail.setAlignment(Qt.AlignCenter);self.show_image(thumbnail,mapping['photoPath'],128,128);caption=QLabel(mapping['posterName']);caption.setAlignment(Qt.AlignCenter);caption.setWordWrap(True);caption.setStyleSheet('font-size:12px;font-weight:700;color:#c4ccd8;');cell.addWidget(thumbnail);cell.addWidget(caption);holder=QWidget();holder.setLayout(cell);self.studio_heads.addWidget(holder,position//4,position%4)
            elif mapping['status']=='ambiguous':assignments.append(mapping['username']+' → AMBIGUOUS roster match ('+', '.join(mapping['candidates'])+'); no photo attached')
            elif mapping['status']=='not_consented':assignments.append(mapping['username']+' → '+mapping['rosterName']+' (not consented; photo omitted)')
            elif mapping['status']=='missing_photo':assignments.append(mapping['username']+' → '+mapping['rosterName']+' (photo missing)')
            elif mapping['status']=='invalid_photo':assignments.append(mapping['username']+' → '+mapping['rosterName']+' (photo invalid; omitted)')
            else:assignments.append(mapping['username']+' → no confident roster match; no photo attached')
        self.photo_assignments.setText('\n'.join(assignments) or 'No verified scoreboard players yet.')
    def active_job(self):
        job=self.store.get(self.active_id) if self.active_id else None
        if not job:self.notice('Select a match or save a verified scoreboard first.')
        return job
    def open_meta(self):
        self.prepare_artwork()
    def cancel_browser(self):
        if self.browser:self.browser.close()
        if self.active_id:
            self.store.cancel_handoff(self.active_id)
        self.log('Browser cancellation requested; match retained.');self.refresh_history()
    def prepare_artwork(self):
        job=self.store.get(self.active_id) if self.active_id else None
        if job and job['status'] in ('delivered','solo_delivered'):job=None
        if not job:
            rows=self.store.history()
            actionable=[row for row in rows if row['status'] not in ('baseline','solo_delivered','delivered')]
            if not actionable:self.notice('All tracked matches are already posted. Capture a new scoreboard for the next one.');return
            pool=sorted(actionable,key=lambda row:row.get('created') or 0,reverse=True)
            mid=pool[0]['id']
            self.active_id=mid;job=self.store.get(mid)
            self.log('One-click poster: newest actionable match selected automatically.')
        if not job:self.notice('Select a match or save a verified scoreboard first.');return
        prompt=self.ensure_match_prompt(job)
        prepared=None
        if not prompt:
            try:prepared=E.prepare_poster_job(self.store,E.DATA,job['id'],solo_player_name=self.settings.values.get('player',''),on_log=self.log)
            except Exception as exc:
                message='Could not prepare this match for Meta AI: '+str(exc)
                self.job_status.setText(message[:300]);self.notice(message);self.log('Poster preparation failed for '+job['id']+': '+str(exc));return
            job=self.store.get(job['id']);prompt=self.ensure_match_prompt(job)
            if not prompt:
                message='Could not prepare this match for Meta AI: no identified players'
                self.job_status.setText(message);self.notice(message);return
        try:
            launched=self._prepare_and_launch(job['id'],manual=True)
        except Exception as exc:
            message='Could not prepare this match for Meta AI: '+str(exc)
            self.job_status.setText(message[:300]);self.notice(message);self.log('Poster preparation failed for '+job['id']+': '+str(exc));return
        if launched:
            try:queued=str((self.store.get(job['id']) or {}).get('gen_state') or '')=='queued'
            except Exception:queued=False
            if queued:
                self.log(f"Match {job['id']} queued behind the running poster job.")
                self.job_status.setText('A poster is already running — this match is queued and starts automatically when it finishes.')
            else:
                self.log(f"Match {job['id']} auto-submitted to Meta AI.")
                self.job_status.setText('Meta AI is generating your picture in the background. You can keep using the tracker.')
            return
        try:
            folder=E.DATA/'handoff'/E.disk_id(job['id'])
            if folder.is_dir():os.startfile(str(folder))
        except OSError as exc:self.log('Photos staged; Explorer could not open the folder: '+str(exc))
        self.job_status.setText('Match request prepared for Meta AI. Prompt copied; attach the staged reference photos in the opened folder.')
        self.log(f"Match {job['id']} prepared for Meta AI: prompt copied and import folder opened.")
    def _prepare_and_launch(self,mid,manual=True):
        job=self.store.get(mid)
        if not job:raise ValueError('Match is no longer in Match History.')
        self.active_id=mid
        try:self.load_studio(mid)
        except (AttributeError,RuntimeError):pass
        players=json.loads(job.get('players') or '[]')
        if not self.ensure_match_prompt(job,players):raise ValueError('No identified players for this match.')
        prepared=E.prepare_poster_job(self.store,E.DATA,mid,solo_player_name=self.settings.values.get('player',''),on_log=self.log)
        if not prepared:raise ValueError('The match job could not be prepared.')
        final_prompt=(self.prompt.toPlainText() or '').strip() or prepared['prompt']
        QApplication.clipboard().setText(final_prompt)
        self._write_unified_prompt(prepared['folder'],final_prompt)
        try:self.refresh_history()
        except (AttributeError,RuntimeError):pass
        photo_names=[photo['filename'] for photo in prepared['photos'] if photo.get('filename')]
        self._poster_manual=manual
        launched=bool(self._start_poster_automation(mid,prepared['folder'],photo_names,final_prompt))
        if launched:
            try:stage=str((self.store.get(mid) or {}).get('gen_state') or 'submitting')
            except Exception:stage='submitting'
            if stage not in ('queued','submitting'):stage='submitting'
            self.set_automation_stage(stage,mid)
        return launched
    def _toggle_full_auto(self):
        try:on=bool(self.full_auto.isChecked())
        except (AttributeError,RuntimeError):return
        self.settings.values['full_auto']=on
        if on and hasattr(self,'auto_post'):
            try:
                if not self.auto_post.isChecked():self.auto_post.setChecked(True);self.log('Full Auto on: automatic Discord posting enabled with it.')
            except (AttributeError,RuntimeError):pass
            self.settings.values['auto_post']=True
        try:self.settings.save()
        except Exception:pass
        if on:self._pump_full_auto()
    def _pump_full_auto(self):
        if getattr(self,'tab_prototype',False):return
        if getattr(self,'closing',False):return
        try:self._sweep_stale_poster_jobs()
        except Exception:pass
        try:on=bool(self.full_auto.isChecked())
        except (AttributeError,RuntimeError):return
        if not on:return
        if getattr(self,'_inapp_poster_active',None):return
        try:rows=self.store.history()
        except Exception:return
        target=None
        for row in rows:
            if str(row.get('gen_state') or '')!='queued':continue
            if str(row.get('status') or '') in ('delivered','solo_delivered','failed','uncertain'):continue
            target=row['id'];break
        if target is None:return
        try:webhook_ok=bool(self.webhook.text().strip())
        except (AttributeError,RuntimeError):webhook_ok=False
        if not webhook_ok:self.log('Full Auto is holding '+target+': set a Discord webhook first.');return
        try:
            if not self.auto_post.isChecked():self.auto_post.setChecked(True);self.settings.values['auto_post']=True;self.log('Full Auto enabled automatic Discord posting.')
        except (AttributeError,RuntimeError):pass
        self.set_automation_stage('queued',target)
        try:launched=self._prepare_and_launch(target,manual=False)
        except Exception as exc:
            try:self.store.update(target,gen_state='idle',error=str(exc)[:300])
            except Exception:pass
            self.log('Full Auto skipped '+target+': '+str(exc)[:160]);return
        if launched:self.log('Full Auto submitted '+target+' to Meta AI.')
    def _sweep_stale_poster_jobs(self,max_age_s=2100):
        try:rows=self.store.history()
        except Exception:return 0
        try:active=getattr(self,'_inapp_poster_active',None)
        except Exception:active=None
        try:started=getattr(self,'_inapp_poster_started',None)
        except Exception:started=None
        now=time.time();reset=0
        for row in rows or []:
            try:
                if str(row.get('gen_state') or '')!='submitting':continue
                try:age=now-float(row.get('updated') or 0)
                except (TypeError,ValueError):age=max_age_s+1
                if age<max_age_s:continue
                try:attempts=int(row.get('gen_attempts') or 0)
                except (TypeError,ValueError):attempts=0
                if active and row.get('id')==active:
                    try:flag_age=now-float(started) if started is not None else -1
                    except (TypeError,ValueError):flag_age=-1
                    if flag_age<max_age_s:continue
                    self._inapp_poster_active=None
                    try:self._inapp_poster_started=None
                    except Exception:pass
                    if attempts>=3:
                        self.store.update(row['id'],gen_state='idle',gen_attempts=attempts+1,error='Poster job timed out 3 times with no worker result; press Generate AI End Game Picture to retry it by hand.')
                    else:
                        self.store.update(row['id'],gen_state='queued',gen_attempts=attempts+1,error='Poster worker died with no result; requeued automatically (try '+str(attempts+1)+').')
                else:
                    self.store.update(row['id'],gen_state='idle',gen_attempts=attempts+1,error='Poster job was stuck submitting with no worker; reset so it can run again.')
            except Exception:continue
            reset+=1
            try:self.log('Reset orphaned poster job '+str(row.get('id'))+'; submit it again to retry.')
            except Exception:pass
        return reset
    def _drain_poster_queue(self):
        try:self._pump_full_auto()
        except Exception:pass
        try:on=bool(self.full_auto.isChecked())
        except (AttributeError,RuntimeError):on=False
        if on:return
        self._sweep_stale_poster_jobs()
        if getattr(self,'_inapp_poster_active',None):return
        try:rows=self.store.history()
        except Exception:return
        target=None
        for row in reversed(rows or []):
            if str(row.get('gen_state') or '')!='queued':continue
            if str(row.get('status') or '') in ('delivered','solo_delivered','failed','uncertain'):continue
            target=row.get('id');break
        if target is None:return
        try:launched=self._prepare_and_launch(target)
        except Exception as exc:
            try:self.store.update(target,gen_state='idle',error=str(exc)[:300])
            except Exception:pass
            try:self.log('Queued poster skipped '+str(target)+': '+str(exc)[:160])
            except Exception:pass
            return
        if launched:
            try:self.log('Queued poster submitted '+str(target)+' to Meta AI.')
            except Exception:pass
    def _write_unified_prompt(self,folder,text):
        try:
            target=Path(folder)
            (target/'prompt.txt').write_text(text,encoding='utf-8')
            (target/'Meta-AI-Automation-Import.txt').write_text(' '.join(text.split()),encoding='utf-8')
        except OSError as exc:self.log('Could not refresh staged prompt text: '+str(exc))
    def _start_poster_automation(self,job_id,folder,filenames,prompt_text):
        if getattr(self,'_inapp_poster_active',None):
            try:self.store.update(job_id,gen_state='queued')
            except Exception:pass
            self.job_status.setText('A poster is already running — this match is queued and starts automatically when it finishes.')
            try:self.set_automation_stage('queued',job_id)
            except (AttributeError,RuntimeError):pass
            self.log('Match '+str(job_id)+' queued behind the running poster job.')
            return True
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            return False
        self._inapp_poster_active=job_id
        self._inapp_poster_started=time.time()
        try:self.store.update(job_id,gen_state='submitting')
        except Exception:pass
        try:attempt=int((self.store.get(job_id) or {}).get('gen_attempts') or 0)
        except Exception:attempt=0
        try:table=self._poster_attempts
        except AttributeError:table=self._poster_attempts={}
        try:table[job_id]=attempt
        except Exception:pass
        try:self.generate_poster_button.setEnabled(False)
        except (AttributeError,RuntimeError):pass
        self._start_embed_poll()
        self.async_task(lambda:self._run_poster_job(job_id,sync_playwright,folder,list(filenames or []),prompt_text),lambda result:self._deliver_poster_result(job_id,result))
        return True
    def _run_poster_job(self,job_id,playwright_factory,folder,filenames,prompt_text):
        try:
            with playwright_factory() as playwright:
                context=meta_automation.ensure_context(playwright,E.DATA/'TrackerChromeInApp',minimized=True)
                driver=meta_automation.MetaDriver(context,E.DATA/'meta-diagnostics')
                page=driver.new_job_page()
                try:
                    for other in list(getattr(context,'pages',None) or []):
                        if other is not page:
                            try:other.close()
                            except Exception:pass
                except Exception:pass
                try:
                    base=Path(folder)
                    photos=[str(base/name) for name in filenames]
                    saved, via=driver.submit_text_to_image(page,photos,prompt_text,str(E.DATA/'artwork'/'inbox'),on_stage=self._driver_stage)
                finally:
                    try:page.close()
                    except Exception:pass
                    try:context.close()
                    except Exception:pass
                return {'ok':True,'path':str(saved),'via':via}
        except Exception as exc:
            return {'ok':False,'step':str(getattr(exc,'step','error')),'detail':str(getattr(exc,'detail',None) or exc)}
    def _poster_attempt_current(self,job_id,attempt):
        if attempt is None:return True
        try:row=self.store.get(job_id)
        except Exception:return False
        if row is None:return False
        try:return int(row.get('gen_attempts') or 0)==int(attempt)
        except (TypeError,ValueError):return False
    def _deliver_poster_result(self,job_id,result):
        try:
            if isinstance(result,dict) and result.get('ok') is True and result.get('path'):
                self._finish_inapp_poster(job_id,str(result['path']),via=str(result.get('via') or 'download'));return
            if isinstance(result,dict) and result.get('ok') is False:
                self._fail_inapp_poster(job_id,str(result.get('step') or 'automation'),str(result.get('detail') or 'Poster job failed'));return
            self._fail_inapp_poster(job_id,'automation','Poster job returned an unreadable result')
        except Exception as exc:
            try:self.log('Poster result dispatch failed for '+str(job_id)+': '+str(exc)[:200])
            except Exception:pass
            try:self._fail_inapp_poster(job_id,'dispatch',str(exc)[:200])
            except Exception:pass
    def _finish_inapp_poster(self,job_id,saved,via='download'):
        self._inapp_poster_active=None
        try:self.generate_poster_button.setEnabled(True)
        except (AttributeError,RuntimeError):pass
        try:table=getattr(self,'_poster_attempts',None) or {}
        except Exception:table={}
        if job_id in table:
            launch_attempt=table.pop(job_id,None)
            if not self._poster_attempt_current(job_id,launch_attempt):
                try:self.log('Ignoring late poster result for '+str(job_id)+': the job was reset and retried after this worker started.')
                except Exception:pass
                self._drain_poster_queue();return
        try:
            from PIL import Image
            folder=E.DATA/'artwork';folder.mkdir(parents=True,exist_ok=True);target=folder/(uuid.uuid4().hex+'.png')
            with Image.open(saved) as image:
                if image.width<320 or image.height<180:raise ValueError('Image is too small')
                image.convert('RGB').save(target)
            try:Path(saved).unlink(missing_ok=True)
            except OSError:pass
        except Exception as exc:
            message='Meta AI returned an unusable image: '+str(exc)
            self.job_status.setText(message[:300]);self.log('In-app poster failed for '+job_id+': '+str(exc)[:160]);self._finish_latest_auto_job(job_id,'JOB FAILED');return
        self.active_id=job_id
        try:self.load_studio(job_id)
        except Exception:pass

        if via!='download':
            try:linked=self.store.note_artwork(job_id,str(target))
            except Exception:linked=False
            if linked:
                try:self.show_image(self.art_preview,str(target),800,300)
                except (AttributeError,RuntimeError):pass
                try:self.refresh_history()
                except (AttributeError,RuntimeError):pass
            self.log('Poster held for manual download: '+str(job_id))
            self.job_status.setText('Browser screenshot linked for review, not posted: only a real Meta download goes to Discord. Right-click save the clean picture in the Meta window, then press Import Artwork.')
            self.set_automation_stage('collected',job_id)
            self._finish_latest_auto_job(job_id,'IMAGE NEEDS REVIEW')
            self._drain_poster_queue();return
        self.received_art(job_id,str(target))
        self.job_status.setText('Poster submitted and finished image collected automatically.')
        self.log('In-app poster completed: '+job_id)
        self.set_automation_stage('collected',job_id)

        self._drain_poster_queue()
    def _fail_inapp_poster(self,job_id,step,detail):
        self._inapp_poster_active=None
        try:self.generate_poster_button.setEnabled(True)
        except (AttributeError,RuntimeError):pass
        try:table=getattr(self,'_poster_attempts',None) or {}
        except Exception:table={}
        if job_id in table:
            launch_attempt=table.pop(job_id,None)
            if not self._poster_attempt_current(job_id,launch_attempt):
                try:self.log('Ignoring late poster failure for '+str(job_id)+': the job was already reset and retried.')
                except Exception:pass
                self._drain_poster_queue();return
        try:self.store.update(job_id,gen_state='idle',error=(str(step)+': '+str(detail))[:300])
        except Exception:pass
        if 'login' in str(step).lower():message='Meta AI is signed out. Click Log into Meta AI in the sidebar, verify your session, then retry the picture.'
        else:message=(f'Automatic submission stopped at {step}: {detail}')[:300]
        self.job_status.setText(message);self.log('In-app poster failed for '+job_id+': '+str(step)+' — '+str(detail)[:160])
        if 'login' in str(step).lower():self._show_login_card(message)
        else:self.set_automation_stage('failed',message)
        self._finish_latest_auto_job(job_id,'JOB FAILED')
        if getattr(self,'_poster_manual',True):
            self.notice(message)
            try:
                folder=E.DATA/'handoff'/E.disk_id(job_id)
                if folder.is_dir():os.startfile(str(folder))
            except OSError:pass

        self._drain_poster_queue()
    def received_art(self,mid,path):
        current=self.store.get(mid)
        if not current:return
        if current.get('artwork')!=path and not self.store.note_artwork(mid,path):return
        self.log('Validated artwork received for '+mid);self.refresh_history()
        if self.active_id==mid:self.show_image(self.art_preview,path,800,300)
        job=self.store.get(mid)
        if job and json.loads(job['players']):
            self.active_id=mid
            skip_repeat_post=(mid==getattr(self,'_latest_auto_job_id',None) and getattr(self,'_latest_auto_no_discord',False))
            self.render_overlay(post_after=self.auto_post.isChecked() and not skip_repeat_post)
    def import_art(self):
        job=self.active_job()
        if not job:return
        path,_=QFileDialog.getOpenFileName(self,'Import a manually downloaded Meta AI image','','Images (*.png *.jpg *.jpeg *.webp)')
        if not path:return
        folder=E.DATA/'artwork';folder.mkdir(parents=True,exist_ok=True);target=folder/(uuid.uuid4().hex+'.png')
        try:
            from PIL import Image
            with Image.open(path) as im:
                if im.width<320 or im.height<180:raise ValueError('Image is too small')
                im.convert('RGB').save(target)
        except Exception as exc:self.notice('Invalid artwork: '+str(exc));return
        self.received_art(job['id'],str(target))
    def render_overlay(self,checked=False,post_after=False):
        job=self.active_job()
        background=(job and job.get('artwork')) or ''
        if not job or not background:self.notice('The downloaded Meta AI artwork is not linked to this match yet.');return
        target=E.DATA/'posters'/(E.disk_id(job['id'])+'.jpg');match=json.loads(job['payload']);players=json.loads(job['players'])
        def done(path):
            self.store.note_poster(job['id'],path);self.show_image(self.art_preview,path,800,300);self.refresh_history();self.log('Meta AI artwork prepared at 1600×900; no statistics were painted over it.')
            if post_after:self.deliver(job['id'])
            elif job['id']==getattr(self,'_latest_auto_job_id',None) and getattr(self,'_latest_auto_no_discord',False):
                self.job_status.setText('Poster generated. This match was already delivered to Discord, so it was not posted again.')
                self._finish_latest_auto_job(job['id'],'POSTER READY')
            else:self.offer_approve_post(job['id'])
        self.async_task(lambda:E.render_poster(background,target,match,players),done)
    def show_image(self,label,path,width,height):
        pix=QPixmap(path) if path else QPixmap()
        if not pix.isNull():label.setPixmap(pix.scaled(width,height,Qt.KeepAspectRatio,Qt.SmoothTransformation))
        else:label.setText('No image available')
    def generate_and_approve(self):
        """One-click End Game post: newest finished match, then an approve popup."""
        if getattr(self,'tab_prototype',False):self.notice('Discord posting is disabled in the CODMunity tab preview.');return
        try:rows=self.store.history()
        except Exception:rows=[]
        actionable=[row for row in rows or [] if row.get('status') not in ('baseline','solo_delivered','delivered')]
        if not actionable:self.notice('All tracked matches are already posted. Capture a new scoreboard for the next one.');return
        top=sorted(actionable,key=lambda row:row.get('created') or 0,reverse=True)[0]
        if top.get('poster') or top.get('image'):
            self.active_id=top['id']
            try:self.load_studio(top['id'])
            except (AttributeError,RuntimeError):pass
            self.offer_approve_post(top['id']);return
        self.prepare_artwork()
    def offer_approve_post(self,mid):
        """Ask for one-tap approval of the finished poster, then post it."""
        job=self.store.get(mid)
        if not job:return
        if job.get('status') in ('delivered','solo_delivered'):return
        image=job.get('poster') or job.get('image')
        if not image:return
        self.active_id=mid
        dialog=QDialog(self);dialog.setWindowTitle('Approve End Game Post');dialog.setModal(True)
        layout=QVBoxLayout(dialog);layout.setContentsMargins(18,18,18,18);layout.setSpacing(12)
        question=QLabel('Do you approve this post?');question.setStyleSheet('font-size:15px;font-weight:900;color:#f2f4f7;');question.setAlignment(Qt.AlignCenter);layout.addWidget(question)
        preview=QLabel();preview.setAlignment(Qt.AlignCenter);preview.setMinimumSize(640,360)
        pix=QPixmap(image)
        if not pix.isNull():preview.setPixmap(pix.scaled(640,360,Qt.KeepAspectRatio,Qt.SmoothTransformation))
        else:preview.setText('Preview unavailable')
        layout.addWidget(preview)
        buttons=QDialogButtonBox()
        approve=buttons.addButton('Approve & Post',QDialogButtonBox.AcceptRole)
        cancel=buttons.addButton('Cancel',QDialogButtonBox.RejectRole)
        approve.clicked.connect(dialog.accept);cancel.clicked.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec()!=QDialog.Accepted:return
        self.deliver(mid)
    def post_active(self):
        if self.tab_prototype:self.notice('Discord posting is disabled in the CODMunity tab preview.');return
        job=self.active_job()
        if not job:return
        if not job['image']:self.notice('Import artwork or render the poster first.');return
        if not self.ask('Post this reviewed image and verified recap to your Discord webhook?'):return
        if job['status']=='baseline':self.store.update(job['id'],status='needs_review')
        self.deliver(job['id'])
    def deliver(self,mid,force=False):
        if self.tab_prototype:self.notice('Discord posting is disabled in the CODMunity tab preview.');return
        if self.solo and self.solo.poll() is None:self.notice('Stop Solo tracking before manually posting a Squad Night report.');return
        job=self.store.get(mid)
        if not job:return
        if job['status']=='solo_delivered' and self.allow_both.isChecked():self.store.update(mid,status='ready')
        if not self.store.reserve(mid,force,allow_both=self.allow_both.isChecked()):self.notice('Duplicate delivery prevented, or this job needs manual resolution in Match History.');return
        url=self.webhook.text().strip();match=json.loads(job['payload']);players=json.loads(job['players']);content=E.recap(match,players,snapshot=self.store.meta('codmunity_stats'));poster=job.get('poster') or job['image']
        if job['capture'] and not players:content+='\nOCR (not verified):\n'+job['ocr'][:600]
        def done(message_id):
            self.store.update(mid,status='delivered',message_id=message_id,error='');E.reserve_legacy_match(match);self.refresh_history();self.log('Discord poster delivered: '+mid)
            try:self._clear_studio_for_next('Posted to Discord. Ready for the next match — capture a new scoreboard.')
            except (AttributeError,RuntimeError):pass
            self._finish_latest_auto_job(mid,'JOB COMPLETE')
            QTimer.singleShot(0,lambda:self.set_automation_stage('posted',mid))
        def failed(error):
            self.store.update(mid,status='uncertain' if 'uncertain' in error.lower() else 'failed',error=error);self.refresh_history();self._finish_latest_auto_job(mid,'JOB FAILED')
        self.async_task(lambda:E.send_discord(url,poster,content),done,failed);self.refresh_history()
    def test_discord(self):
        if self.tab_prototype:self.notice('Discord posting is disabled in the CODMunity tab preview.');return
        channel=self.webhook_channel.text().strip() or 'the configured Discord channel'
        if not self.ask('Send a single Vintendo COD Tracker test message to %s?' % channel):return
        self.async_task(lambda:E.send_discord(self.webhook.text().strip(),None,'Vintendo COD Tracker — webhook connection test.'),lambda _,channel=channel:self.log('Discord webhook test delivered to %s.' % channel))
    def resolve_selected(self):
        job=self.selected_job()
        if not job:return
        self.active_id=job['id'];self.retry_job()
    def retry_job(self):
        job=self.active_job()
        if not job:return
        if job['status']=='uncertain':
            if not self.ask('Check Discord first. If the message already exists, choose No. Choose Yes only to acknowledge the duplicate risk and retry this delivery.'):return
            self.deliver(job['id'],force=True)
        elif job['status']=='failed':self.store.update(job['id'],status='needs_review',error='');self.load_studio(job['id']);self.refresh_history()
        else:self.load_studio(job['id'])
    def worker_log(self):
        path=E.DATA/'solo-worker.log'
        if path.exists():self.logs.setPlainText(path.read_text(encoding='utf-8',errors='replace')[-50000:])
        for row in range(self.nav.count()):
            if self.nav.item(row).data(Qt.UserRole)=='Settings':self.nav.setCurrentRow(row);break
        if hasattr(self,'logs_tab_index'):self.settings_tabs.setCurrentIndex(self.logs_tab_index)
    def shutdown(self):
        app_instance=QApplication.instance()
        if app_instance is not None and getattr(self,'cinematic_fullscreen_filter',None) is not None:
            try:app_instance.removeEventFilter(self.cinematic_fullscreen_filter)
            except Exception:pass
        self.closing=True;self.stop();self.timer.stop();self.unregister_global_hotkeys()
        self.codmunity_poll.stop();self.codmunity_host.detach();self._close_codmunity_process()
        try:self.browser_host.detach()
        except Exception:pass
        if self.browser:self.browser.close()
        if self.capture and not self.capture_busy:self.capture.close()
        self.tray.hide();QApplication.quit()
    def closeEvent(self,event):
        if self.tracking and self.tray.isVisible() and not self.closing:self.hide();event.ignore();self.tray.showMessage('Vintendo COD Tracker','Tracking continues in the tray. Use Quit to stop.');return
        self.shutdown();event.accept()

def is_smoke_run(argv=None, env=None):
    """Smoke verification must not join a running instance; it owns its result files."""
    args=sys.argv if argv is None else argv
    environment=os.environ if env is None else env
    return '--smoke-test' in args or environment.get('VINTENDO_SMOKE_TEST')=='1'

def main():
    if '--solo-worker' in sys.argv:E.run_solo_worker();return
    if '--verify-build' in sys.argv or os.environ.get('VINTENDO_VERIFY_BUILD')=='1':
        import verification
        sys.exit(verification.run())
    E.DATA.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=logging.INFO,handlers=[RotatingFileHandler(E.DATA/'application.log',maxBytes=2_000_000,backupCount=3,encoding='utf-8')],format='%(asctime)s %(levelname)s %(message)s')
    if os.name=='nt':
        try:
            import ctypes as _ctypes
            _ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Vintendo.CODTracker')
        except Exception:pass
    app=QApplication(sys.argv);app.setApplicationName('Vintendo COD Tracker');app.setOrganizationName('Vintendo')
    for font in ('segoeui.ttf','segoeuib.ttf','arial.ttf'):
        path=Path('C:/Windows/Fonts')/font
        if path.exists():QFontDatabase.addApplicationFont(str(path))
    prototype=os.environ.get('VINTENDO_TAB_PROTOTYPE')=='1'
    if prototype:
        source=os.environ.get('VINTENDO_TAB_PREVIEW_SOURCE','').strip()
        try:
            if not source:raise ValueError('The preview launcher did not specify the live tracker data folder.')
            prepare_tab_preview_data(source,E.DATA)
        except Exception as exc:QMessageBox.critical(None,'CODMunity Tab Preview','Could not copy the tracker settings and roster into preview data:\n\n'+str(exc));return
    from PySide6.QtNetwork import QLocalServer,QLocalSocket
    smoke=is_smoke_run()
    server=None
    if not smoke and not prototype:
        name='VintendoCODTracker-'+__import__('hashlib').sha256(str(E.DATA).encode()).hexdigest()[:16]
        socket=QLocalSocket();socket.connectToServer(name)
        if socket.waitForConnected(300):return
        QLocalServer.removeServer(name);server=QLocalServer();server.listen(name)
    window=Window();window.show()
    if not smoke and not prototype:QTimer.singleShot(650,window.run_first_boot_setup)
    if not smoke and not prototype:QTimer.singleShot(8000,window.maybe_show_startup_requirements)
    if server is not None and not prototype:
        server.newConnection.connect(window.showNormal)
    if server is not None and os.environ.get('VINTENDO_NO_AUTO_START','0')!='1':
        QTimer.singleShot(1500,window.start)
        QTimer.singleShot(20000,window.check_for_app_update)
    if smoke:
        def finish():
            window.grab().save(str(E.DATA/'startup-smoke.png'));E.atomic_json(E.DATA/'smoke-result.json',{'startup':'passed','pages':window.nav.count(),'mode':window.mode.currentText(),'ocr_initialized':window.capture is not None});window.shutdown()
        QTimer.singleShot(2500,finish)
    sys.exit(app.exec())

if __name__=='__main__':
    try:main()
    except Exception:
        E.DATA.mkdir(parents=True,exist_ok=True)
        import traceback
        (E.DATA/'crash.log').write_text(re.sub(r'(https://discord(?:app)?\.com)?/api/webhooks/\d+/[\w.\-]+','[webhook]',traceback.format_exc()),encoding='utf-8')
        raise