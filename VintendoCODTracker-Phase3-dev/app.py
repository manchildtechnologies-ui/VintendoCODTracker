from __future__ import annotations
import os,sys,json,time,threading,subprocess,shutil,logging,re,uuid,ctypes,sqlite3
from ctypes import wintypes
from logging.handlers import RotatingFileHandler
from pathlib import Path
from PySide6.QtCore import Qt,QUrl,QTimer,QObject,QEvent,Signal,QRunnable,QThreadPool,QRect,QAbstractNativeEventFilter,QSize,QPoint,QPropertyAnimation,QEasingCurve
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtGui import QIcon,QPixmap,QImage,QPainter,QPainterPath,QPen,QColor,QBrush,QPalette,QAction,QFontDatabase,QLinearGradient,QDesktopServices
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,QPushButton,QListWidget,QListWidgetItem,QStackedWidget,QLineEdit,QComboBox,QCheckBox,QPlainTextEdit,QTableWidget,QTableWidgetItem,QHeaderView,QFileDialog,QMessageBox,QFormLayout,QSpinBox,QDialog,QDialogButtonBox,QSystemTrayIcon,QMenu,QSplitter,QGroupBox,QScrollArea,QTabWidget,QFrame,QButtonGroup,QInputDialog,QSlider,QDoubleSpinBox)
import engine as E
import updater
import meta_automation
from version import APP_VERSION

SCOREBOARD_HOTKEY_ID=0x5638
COD_AI_SCREENSHOT_HOTKEY_ID=0x5639
AUTO_OCR_INTERVAL_SECONDS=30

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
        out=subprocess.run([exe,'-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(path)],capture_output=True,text=True,timeout=3)
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
        user32.SetWindowPos.argtypes=(hwnd,hwnd,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.UINT);user32.SetWindowPos.restype=wintypes.BOOL
        user32.MoveWindow.argtypes=(hwnd,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.BOOL);user32.MoveWindow.restype=wintypes.BOOL
        user32.GetWindowRect.argtypes=(hwnd,ctypes.POINTER(wintypes.RECT));user32.GetWindowRect.restype=wintypes.BOOL
        return user32,get_style,set_style
    def attach(self,hwnd,parent_hwnd):
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

class Bridge(QObject):
    image=Signal(str,str);status=Signal(str)

PAGE_BANNERS={'Dashboard':'dashboard.png','Scoreboard Capture':'scoreboard.png','Match History':'match-history.png','Player Roster':'roster.png','AI Poster Studio':'ai-studio.png','COD AI':'screenshots.png','Tracker Browser':'browser.png','Settings':'settings.png','COD Tweaker':'tweaker.png'}
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

class RegionDialog(QDialog):
    def __init__(self,frame,parent=None):
        super().__init__(parent);self.setWindowTitle('Select the complete scoreboard');self.frame=frame
        self.original=(frame.shape[1],frame.shape[0]);self.start=None;self.end=None;self.roi=None
        pix=frame_pixmap(frame).scaled(1100,660,Qt.KeepAspectRatio,Qt.SmoothTransformation)
        self.label=QLabel();self.label.setPixmap(pix);self.label.setFixedSize(pix.size());self.label.mousePressEvent=self.press;self.label.mouseMoveEvent=self.move;self.label.mouseReleaseEvent=self.release
        layout=QVBoxLayout(self);layout.addWidget(QLabel('Drag around every player row and the column headings.'));layout.addWidget(self.label)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel);buttons.accepted.connect(self.accept_region);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.resize(pix.width()+30,pix.height()+95)
    def press(self,event):self.start=event.position().toPoint();self.end=self.start
    def move(self,event):
        if self.start:
            self.end=event.position().toPoint();pix=frame_pixmap(self.frame).scaled(self.label.size(),Qt.KeepAspectRatio,Qt.SmoothTransformation)
            painter=QPainter(pix);painter.setPen(QPen(QColor('#ff9638'),3));painter.drawRect(QRect(self.start,self.end).normalized());painter.end();self.label.setPixmap(pix)
    def release(self,event):self.move(event)
    def accept_region(self):
        if not self.start or not self.end:return
        rect=QRect(self.start,self.end).normalized().intersected(self.label.rect())
        sx=self.original[0]/self.label.width();sy=self.original[1]/self.label.height()
        self.roi=[int(rect.left()*sx),int(rect.top()*sy),min(self.original[0],int((rect.right()+1)*sx)),min(self.original[1],int((rect.bottom()+1)*sy))]
        if self.roi[2]-self.roi[0]<80 or self.roi[3]-self.roi[1]<60:return
        self.accept()

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
QLabel#livePill {background:#10251d;border:1px solid #1c754b;border-radius:18px;color:#57f19b;font-size:13px;font-weight:900;padding:10px 19px;}
QFrame#metricRed {border:1px solid #8e2631;border-radius:9px;background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #37161d,stop:1 #171218);}
QFrame#metricBlue {border:1px solid #245b95;border-radius:9px;background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #102a43,stop:1 #111721);}
QFrame#metricPurple {border:1px solid #65409b;border-radius:9px;background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #27193c,stop:1 #15131d);}
QLabel#metricGlyph {font-size:29px;font-weight:900;color:#fa3544;}
QLabel#metricValue {font-size:33px;font-weight:900;color:#ffffff;}
QLabel#metricLabel {font-size:12px;font-weight:800;color:#bdc6d5;letter-spacing:1px;}
QFrame#dashboardPanel {border:1px solid #303642;border-radius:9px;background:#0e1118;}
QFrame#healthRow {border:1px solid #20252d;border-radius:6px;background:#141922;}
QLabel#healthDot {font-size:15px;color:#77818f;}
QLabel#healthValue {font-size:11px;font-weight:800;color:#c4ccd8;}
QFrame#dashboardFooter {border-top:1px solid #3a2026;background:#0d1016;}
QFrame#historySummaryCard {border:1px solid #49313a;border-radius:8px;background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #1a151b,stop:1 #10131a);}
QLabel#historySummaryValue {font-size:25px;font-weight:900;color:#ffffff;}
QLabel#historySummaryCaption {font-size:10px;font-weight:900;color:#ff5964;letter-spacing:1px;}
QLabel#historySummaryDetail {font-size:10px;color:#9da8b7;}
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
QPushButton#exitButton {background:#3a1116;color:#ff8b93;border:1px solid #ff6570;font-weight:800;padding:4px 14px;}
QPushButton#exitButton:hover {background:#ff3947;color:#ffffff;}
QPushButton#accent:hover {background:#ff4854;border-color:#ffa0a5;}
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
QTabBar::tab {background:#151922;color:#aab3c1;border:1px solid #303642;padding:10px 20px;margin-right:4px;border-top-left-radius:5px;border-top-right-radius:5px;}
QTabBar::tab:selected {background:#37151b;color:#ffffff;border-color:#d72837;border-bottom-color:#37151b;}
QFrame#codAiSteps {border:1px solid #303642;border-radius:9px;background:#0e1118;padding:6px;}
QLabel#stepNum {background:#3d0d15;border:2px solid #ed2938;border-radius:16px;color:#ffffff;font-size:15px;font-weight:900;min-width:32px;min-height:32px;max-width:32px;max-height:32px;}
QLabel#stepTitle {font-size:13px;font-weight:900;color:#ffffff;letter-spacing:1px;}
QLabel#stepDesc {font-size:11px;color:#9da8b7;}
QLabel#stepArrow {font-size:18px;font-weight:900;color:#ed2938;padding:0 4px;}
QLabel#hotkeyPill {background:#0d2318;border:1px solid #2ea36b;border-radius:14px;color:#7bf0a8;font-size:12px;font-weight:800;padding:7px 13px;}
QFrame#cinematicTile {border:1px solid #2a2f3a;border-radius:8px;background:#0c0f15;}
QFrame#cinematicTile[selected="true"] {border:2px solid #ed2938;background:#180d12;}
QLabel#cinematicThumb {background:#050609;color:#77818f;border-radius:5px;}
QLabel#cinematicName {font-size:11px;color:#c4ccd8;padding:0 2px;}
QLabel#cinematicBadge {background:rgba(0,0,0,170);color:#ffffff;font-size:11px;font-weight:800;border-radius:4px;padding:2px 6px;margin:5px;}
QPushButton#cinematicDelete {background:#c8102e;color:#ffffff;border:1px solid #ff5b6a;border-radius:12px;font-size:12px;font-weight:900;padding:0;margin:4px;}
QPushButton#cinematicDelete:hover {background:#ed2938;}
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
QFrame#cinematicTile[selected="true"] {border:2px solid #f97316;background:#1c1008;}
QPushButton#friendChip {background:#14171e;border:1px solid #343945;padding:8px 10px;border-radius:7px;font-weight:700;color:#e8ebf0;text-align:left;}
QPushButton#friendChip:hover {border-color:#f97316;color:#ffffff;}
QPushButton#friendChip:checked {background:#2a1503;border:1px solid #f97316;color:#fdba74;}
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

class StatCarousel(QFrame):
    SLIDE_MS=140
    def __init__(self,parent=None):
        super().__init__(parent);self.setObjectName('historySummaryCard')
        outer=QVBoxLayout(self);outer.setContentsMargins(10,6,10,8);outer.setSpacing(4)
        self.host=QWidget();self.host.setFixedHeight(66);outer.addWidget(self.host)
        self.card=QFrame(self.host)
        content=QVBoxLayout(self.card);content.setContentsMargins(0,0,0,0);content.setSpacing(1)
        self.value=QLabel('\\u2014');self.value.setObjectName('historySummaryValue');self.value.setAlignment(Qt.AlignCenter)
        self.caption=QLabel('');self.caption.setObjectName('historySummaryCaption');self.caption.setAlignment(Qt.AlignCenter)
        content.addWidget(self.value);content.addWidget(self.caption)
        self.dots=QHBoxLayout();self.dots.setSpacing(8)
        dots_host=QWidget();dots_host.setLayout(self.dots);outer.addWidget(dots_host)
        self._cards=[];self._index=0;self._animating=False;self._dot_buttons=[];self._out=None;self._back=None
        self._fit_card()
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if not self._animating:self._fit_card()
    def _fit_card(self):
        self.card.setGeometry(0,0,max(self.host.width(),1),self.host.height())
    def set_cards(self,cards):
        for running in (self._out,self._back):
            try:
                if running is not None:running.stop()
            except RuntimeError:pass
        self._cards=list(cards);self._index=0;self._animating=False
        while self.dots.count()>0:
            taken=self.dots.takeAt(0);widget=taken.widget()
            if widget is not None:widget.deleteLater()
        self._dot_buttons=[];self.dots.addStretch()
        for i,(title,_text) in enumerate(self._cards):
            dot=QPushButton();dot.setFixedSize(12,12);dot.setCursor(Qt.PointingHandCursor);dot.setAccessibleName(title);dot.setToolTip(title)
            dot.clicked.connect(lambda _checked=False,i=i:self.show_index(i))
            self.dots.addWidget(dot);self._dot_buttons.append(dot)
        self.dots.addStretch()
        self._paint_dots();self._show_current();self._fit_card()
    def show_index(self,i):
        if not self._cards or self._animating:return
        i=max(0,min(i,len(self._cards)-1))
        if i==self._index or self.host.width()<=1:
            self._index=i;self._paint_dots();self._show_current();return
        forward=i>self._index;self._index=i;self._paint_dots()
        distance=self.host.width()
        self._animating=True
        out=QPropertyAnimation(self.card,b'pos',self);out.setDuration(self.SLIDE_MS);out.setEasingCurve(QEasingCurve.OutCubic)
        out.setStartValue(self.card.pos());out.setEndValue(QPoint(-distance if forward else distance,0))
        def _swapped():
            self._show_current();self.card.move(distance if forward else -distance,0)
            back=QPropertyAnimation(self.card,b'pos',self);back.setDuration(self.SLIDE_MS);back.setEasingCurve(QEasingCurve.OutCubic)
            back.setStartValue(self.card.pos());back.setEndValue(QPoint(0,0))
            back.finished.connect(self._finish_slide);back.start();self._back=back
        out.finished.connect(_swapped);out.start();self._out=out
    def _finish_slide(self):
        self._animating=False;self._fit_card()
    def _show_current(self):
        if self._cards and 0<=self._index<len(self._cards):
            title,text=self._cards[self._index];self.value.setText(text);self.caption.setText(title)
        else:self.value.setText('\\u2014');self.caption.setText('')
    def _paint_dots(self):
        for i,dot in enumerate(self._dot_buttons):
            active=i==self._index;dot.setFixedWidth(26 if active else 12)
            dot.setStyleSheet('QPushButton{background:%s;border:none;border-radius:6px;}QPushButton:hover{background:%s;}'%('#e72a3b' if active else '#3a4150','#ff5966' if active else '#59616f'))
    def card_titles(self):
        return [title for title,_text in self._cards]
    def dot_count(self):
        return len(self._dot_buttons)
    def current_index(self):
        return self._index
    def value_text(self):
        return self.value.text()
    def title_text(self):
        return self.caption.text()


MOD_ALT=0x0001;MOD_CONTROL=0x0002;MOD_SHIFT=0x0004;MOD_NOREPEAT=0x4000
DEFAULT_SCOREBOARD_HOTKEY=(0x77,MOD_CONTROL|MOD_SHIFT)
DEFAULT_SCREENSHOT_HOTKEY=(0x77,MOD_CONTROL)
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
    def __init__(self,vk,mod,parent=None):
        super().__init__(parent);self.setReadOnly(True);self.set_hotkey(vk,mod);self.setPlaceholderText('Click here, then press the new keys')
        self.setToolTip('Click this box and press the key combination to use. Hold Ctrl, Shift or Alt plus another key.')
    def set_hotkey(self,vk,mod):
        self.vk=int(vk or 0);self.mod=int(mod or 0)&(MOD_CONTROL|MOD_SHIFT|MOD_ALT);self.setText(format_hotkey(self.vk,self.mod))
    def keyPressEvent(self,event):
        key=event.key()
        if key in (Qt.Key_Control,Qt.Key_Shift,Qt.Key_Alt,Qt.Key_Meta):return
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
        if vk and mod:self.set_hotkey(vk,mod)
class Window(QMainWindow):

    def __init__(self):
        super().__init__();self.settings=E.Settings();self.store=E.Store();self.pool=QThreadPool.globalInstance();self.tasks=set();self.session_started=time.time()
        self.tracking=False;self.paused=False;self.solo=None;self.capture=None;self.capture_busy=False;self.screenshot_busy=False;self.poll_busy=False;self.active_id=None;self.last_capture=None;self.last_frame=None;self.outputs=[];self.browser=None;self.closing=False
        self.tab_prototype=os.environ.get('VINTENDO_TAB_PROTOTYPE')=='1'
        self.track_both_startup=os.environ.get('VINTENDO_TRACK_BOTH')=='1'
        self.bridge=Bridge();self.bridge.image.connect(self.received_art);self.bridge.status.connect(self.log)
        self.setWindowTitle('Vintendo COD Tracker — CODMunity Tab Preview' if self.tab_prototype else 'Vintendo COD Tracker');self.resize(1450,930);self.setMinimumSize(1100,730);self.setWindowIcon(QIcon(str(E.ROOT/'tracker.ico')))
        self.setStyleSheet(STYLE);central=QWidget();central.setObjectName('centralWindow');self.setCentralWidget(central);root=QVBoxLayout(central);root.setContentsMargins(0,0,0,0);root.setSpacing(0)
        self.app_tabs=QTabWidget();self.app_tabs.setDocumentMode(True);root.addWidget(self.app_tabs)
        tracker_shell=QWidget();tracker_shell_layout=QHBoxLayout(tracker_shell);tracker_shell_layout.setContentsMargins(0,0,0,0);tracker_shell_layout.setSpacing(0);self.app_tabs.addTab(tracker_shell,'Vintendo COD Tracker')
        self.sidebar=QWidget();self.sidebar.setObjectName('sidebar');self.sidebar.setFixedWidth(275);side=QVBoxLayout(self.sidebar);side.setContentsMargins(10,16,10,12);side.setSpacing(8)
        logo_path=E.ROOT/'assets'/'vintendo-cod-tracker-logo.png'
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

        self.stack=QStackedWidget();tracker_shell_layout.addWidget(self.sidebar);tracker_shell_layout.addWidget(self.stack,1)
        self.codmunity_host=WindowsWindowHost();self.codmunity_process=None;self.codmunity_hwnd=None
        self.browser_host=WindowsWindowHost();self.browser_hwnd=None
        self.codmunity_page=QWidget();codmunity_layout=QVBoxLayout(self.codmunity_page);codmunity_layout.setContentsMargins(20,16,20,16);codmunity_layout.setSpacing(10)
        codmunity_header=QHBoxLayout();codmunity_title=QLabel('CODMunity Desktop');codmunity_title.setObjectName('title');codmunity_header.addWidget(codmunity_title);codmunity_header.addStretch()
        self.codmunity_status=QLabel('Select this tab to open CODMunity Desktop');self.codmunity_status.setStyleSheet('color:#99a8bc;font-weight:700;');codmunity_header.addWidget(self.codmunity_status);codmunity_layout.addLayout(codmunity_header)
        if self.tab_prototype:
            preview_note=QLabel('Preview uses a copy of your settings, match history, and roster photos. It will not change live tracker data. Tracking and Discord posting are disabled in this window.')
            preview_note.setWordWrap(True);preview_note.setStyleSheet('padding:9px 12px;background:#1b2330;border-left:3px solid #ed2938;color:#cbd3df;');codmunity_layout.addWidget(preview_note)
        self.codmunity_surface=QFrame();self.codmunity_surface.setObjectName('dashboardPanel');self.codmunity_surface.setMinimumSize(800,550);codmunity_layout.addWidget(self.codmunity_surface,1);self.app_tabs.addTab(self.codmunity_page,'CODMunity')
        self.codmunity_poll=QTimer(self);self.codmunity_poll.setInterval(500);self.codmunity_poll.timeout.connect(self.try_attach_codmunity)
        self.browser_poll=QTimer(self);self.browser_poll.setInterval(500);self.browser_poll.timeout.connect(self.try_attach_tracker_chrome)
        self.app_tabs.currentChanged.connect(self.app_tab_changed)
        self.exit_button=QPushButton('EXIT');self.exit_button.setObjectName('exitButton');self.exit_button.setToolTip('Close Vintendo COD Tracker completely');self.exit_button.clicked.connect(self.request_exit);self.app_tabs.setCornerWidget(self.exit_button,Qt.TopRightCorner)
        self.pages={};self.build_dashboard();self.build_solo();self.build_squad();self.build_capture();self.build_roster();self.build_studio();self.build_cod_ai();self.build_tracker_browser();self.build_discord();self.build_history();self.build_settings();self.build_cod_tweaker();self.build_logs()
        if self.tab_prototype:
            for control in (self.start_button,self.pause_button,self.stop_button,self.auto_post,self.allow_both,self.webhook,self.send_test_button,self.approve_post_button,self.generate_poster_button):control.setEnabled(False)
            self.auto_post.setChecked(False)
        if self.track_both_startup:
            # Keep the existing internal mode for OCR, but hide its redundant
            # manual mode page when the launcher already starts both sources.
            self.mode.setCurrentText('Squad Night');self.mode.setEnabled(False)
            if int(self.settings.values.get('scoreboard_hotkey_migration',0))<1:
                self.settings.values['auto_detect']=False;self.settings.values['scoreboard_hotkey_migration']=1;self.settings.save()
            self.auto_detect.setChecked(bool(self.settings.values.get('auto_detect',False)))
            for row in range(self.nav.count()):
                if self.nav.item(row).data(Qt.UserRole)=='Squad Night':self.nav.item(row).setHidden(True)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex);self.nav.setCurrentRow(0)
        for hidden_page in ('Solo Ops','Discord Settings','Match History'):
            for row in range(self.nav.count()):
                if self.nav.item(row).data(Qt.UserRole)==hidden_page:self.nav.item(row).setHidden(True)
        self.tray=QSystemTrayIcon(self.windowIcon(),self);menu=QMenu();menu.addAction('Open Vintendo COD Tracker',self.showNormal);menu.addAction('Pause tracking',self.pause);menu.addAction('Quit',self.shutdown);self.tray.setContextMenu(menu);self.tray.activated.connect(lambda reason:self.showNormal() if reason==QSystemTrayIcon.DoubleClick else None)
        if QSystemTrayIcon.isSystemTrayAvailable():self.tray.show()
        self.timer=QTimer(self);self.timer.timeout.connect(self.tick);self.timer.start(1000);self.last_poll=0;self.last_auto=0;self.cinematic_refresh_timer=QTimer(self);self.cinematic_refresh_timer.timeout.connect(self.refresh_cinematic_results);self.cinematic_refresh_timer.start(3000)
        self.inapp_timer=QTimer(self);self.inapp_timer.timeout.connect(self.poll_inapp_cinematics);self.inapp_timer.start(10000)
        self._inapp_active=None
        self.refresh_history();self.refresh_roster();self.mode_changed(self.mode.currentText(),initial=True);self.refresh_hotkey_labels()
        self.log('Ready. Combined CODMunity + scoreboard tracking will start automatically.' if self.track_both_startup else 'Ready. Solo Ops is the default. Start tracking when the old tracker is stopped.')
        if not self.tab_prototype:self.import_previous_settings()
        self.refresh_cod_ai_screenshots()
        self.register_global_hotkeys()
    def app_tab_changed(self,index):
        if self.app_tabs.widget(index) is self.codmunity_page:self.start_or_attach_codmunity()
    def codmunity_executable(self):
        override=os.environ.get('CODMUNITY_DESKTOP_EXE','').strip()
        candidates=[Path(override)] if override else []
        candidates.append(Path.home()/'AppData'/'Local'/'CODMunity Desktop'/'codmunity-desktop.exe')
        return next((path for path in candidates if path.is_file()),None)
    def start_or_attach_codmunity(self):
        if self.codmunity_host.hwnd and ctypes.windll.user32.IsWindow(self.codmunity_host.hwnd):
            self.codmunity_host.resize(self.codmunity_surface.width(),self.codmunity_surface.height());return
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
        user32=ctypes.windll.user32;handles=[]
        @ctypes.WINFUNCTYPE(ctypes.c_bool,wintypes.HWND,wintypes.LPARAM)
        def enum_callback(hwnd,lparam):
            if not user32.IsWindowVisible(hwnd):return True
            pid=wintypes.DWORD();user32.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
            if self.codmunity_process and pid.value==self.codmunity_process.pid:handles.append(hwnd)
            else:
                try:
                    import psutil
                    if psutil.Process(pid.value).name().lower()=='codmunity-desktop.exe':handles.append(hwnd)
                except Exception:pass
            return True
        user32.EnumWindows(enum_callback,0)
        return handles[0] if handles else None
    def try_attach_codmunity(self):
        hwnd=self.find_codmunity_window()
        if hwnd:self.codmunity_poll.stop();self.attach_codmunity(hwnd)
        elif self.codmunity_process and self.codmunity_process.poll() is not None:
            self.codmunity_poll.stop();self.codmunity_status.setText(f'CODMunity Desktop exited with code {self.codmunity_process.returncode}.')
    def attach_codmunity(self,hwnd):
        try:
            self.codmunity_surface.winId();self.codmunity_host.attach(hwnd,int(self.codmunity_surface.winId()));self.codmunity_hwnd=hwnd
            self.codmunity_host.resize(self.codmunity_surface.width(),self.codmunity_surface.height());self.codmunity_status.setText('CODMunity Desktop is running in this tab.')
        except Exception as exc:self.codmunity_status.setText('Window embedding failed: '+str(exc));self.log('CODMunity window embedding failed: '+str(exc))
    def resizeEvent(self,event):
        super().resizeEvent(event)
        browser_page=self.pages.get('Tracker Browser',(None,None))[0] if hasattr(self,'pages') else None
        if (hasattr(self,'browser_host') and self.browser_host.hwnd and browser_page is not None
                and self.stack.currentWidget() is browser_page and hasattr(self,'browser_surface')):
            self.browser_host.resize(self.browser_surface.width(),self.browser_surface.height())
        if hasattr(self,'codmunity_host') and self.codmunity_host.hwnd and self.app_tabs.currentWidget() is self.codmunity_page:
            self.codmunity_host.resize(self.codmunity_surface.width(),self.codmunity_surface.height())
        if hasattr(self,'cinematic_results'):self.update_cinematic_grid()
        if hasattr(self,'cod_ai_screenshots'):self.update_screenshot_grid()
        if hasattr(self,'cod_ai_screenshots') and self.cod_ai_screenshots.currentItem():self.preview_cod_ai_screenshot(self.cod_ai_screenshots.currentItem())
    def page(self,name,subtitle):
        page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(30,25,30,25);layout.setSpacing(14)

        banner_file=PAGE_BANNERS.get(name);banner_path=(E.ROOT/'assets'/'banners'/banner_file) if banner_file else None
        banner_pix=QPixmap(str(banner_path)) if banner_path and banner_path.is_file() else QPixmap()
        title=QLabel()
        if not banner_pix.isNull():title.setObjectName('pageBanner');title.setPixmap(banner_pix.scaled(QSize(520,170),Qt.KeepAspectRatio,Qt.SmoothTransformation));title.setProperty('bannerFile',banner_file)
        else:title.setObjectName('title');title.setText(name)
        layout.addWidget(title,0,Qt.AlignHCenter if title.objectName()=='pageBanner' else Qt.Alignment());desc=QLabel(subtitle);desc.setWordWrap(True);desc.setStyleSheet('color:#99a8bc');layout.addWidget(desc)
        glyph={'Dashboard':'⌂','Solo Ops':'◎','Squad Night':'◈','Scoreboard Capture':'⊙','Player Roster':'♟','AI Poster Studio':'▧','COD AI':'>','Discord Settings':'⚙','Match History':'☷','Settings':'⚙','Tracker Browser':'▣','COD Tweaker':'T'}.get(name,'›')
        item=QListWidgetItem(glyph+'   '+name);item.setData(Qt.UserRole,name);self.nav.addItem(item);self.stack.addWidget(page);self.pages[name]=(page,layout);return layout
    def button(self,text,fn,accent=False):
        b=QPushButton(text);b.clicked.connect(fn)
        if accent:b.setObjectName('accent')
        return b
    def row(self,layout,*widgets):
        row=QHBoxLayout()
        for w in widgets:row.addWidget(w)
        layout.addLayout(row);return row
    def active_mode_label(self):
        return 'BOTH SOURCES' if self.track_both_startup else self.mode.currentText().upper()
    def edit(self,value=''):return QLineEdit(str(value))
    def notice(self,text):QMessageBox.information(self,'Vintendo COD Tracker',text)
    def ask(self,text):return QMessageBox.question(self,'Vintendo COD Tracker',text)==QMessageBox.Yes
    def request_exit(self):
        if self.ask('Exit Vintendo COD Tracker?\n\nTracking stops, the Chrome bridge disconnects, and all windows close.'):self.shutdown()
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
        l=self.page('Dashboard','CODMunity match data and scoreboard OCR are monitored together from one tracking session.' if self.track_both_startup else 'Your match activity, tracker health, and poster tools in one place.')
        hero=TacticalBackdrop();hero_layout=QHBoxLayout(hero);hero_layout.setContentsMargins(22,12,20,12)
        hero_copy=QVBoxLayout()
        hero_title=QLabel('MATCH OPERATIONS');hero_title.setStyleSheet('font-size:19px;font-weight:900;color:#ffffff;letter-spacing:1px;');hero_copy.addWidget(hero_title)
        hero_layout.addLayout(hero_copy,1);self.status=QLabel('STANDBY');self.status.setObjectName('livePill');self.status.setAlignment(Qt.AlignCenter);hero_layout.addWidget(self.status,0,Qt.AlignRight|Qt.AlignVCenter);l.addWidget(hero)

        top=QGroupBox('TRACKING SOURCES');f=QHBoxLayout(top);f.setSpacing(10)
        self.mode=QComboBox();self.mode.addItems(['Solo Ops','Squad Night']);self.mode.setCurrentText(self.settings.values.get('mode','Solo Ops'));self.mode.currentTextChanged.connect(self.mode_changed)
        if self.track_both_startup:self.mode.setVisible(False)
        source_label='CODMunity + scoreboard OCR' if self.track_both_startup else 'Active mode'
        source=QLabel('◉   '+source_label);source.setMinimumWidth(200);source.setStyleSheet('font-weight:700;color:#e5e9ef;background:#151922;padding:10px;border-radius:5px;')
        f.addWidget(source,1)
        if not self.track_both_startup:f.addWidget(self.mode,1)
        self.start_button=self.button('▶   START TRACKING',self.start,True);self.start_button.setMinimumHeight(42)
        self.pause_button=self.button('Ⅱ   PAUSE TRACKING',self.pause);self.pause_button.setMinimumHeight(42);self.pause_button.setEnabled(False)
        self.stop_button=self.button('■   STOP',self.stop);self.stop_button.setMinimumHeight(42);self.stop_button.setEnabled(False)
        f.addWidget(self.start_button,1);f.addWidget(self.pause_button,1);f.addWidget(self.stop_button,1);l.addWidget(top)

        cards=QHBoxLayout();cards.setSpacing(12);self.dashboard_metrics={}
        for key,title,glyph,color in (('matches','MATCH RECORDS','◎','metricRed'),('squad_posts','SQUAD POSTS','♟','metricBlue'),('reviews','REVIEW JOBS','▧','metricPurple')):
            card=QFrame();card.setObjectName(color);card_layout=QHBoxLayout(card);card_layout.setContentsMargins(18,13,18,13);card_layout.setSpacing(14)
            mark=QLabel(glyph);mark.setObjectName('metricGlyph');card_layout.addWidget(mark)
            text_layout=QVBoxLayout();value=QLabel('0');value.setObjectName('metricValue');label=QLabel(title);label.setObjectName('metricLabel');text_layout.addWidget(value);text_layout.addWidget(label);card_layout.addLayout(text_layout,1);cards.addWidget(card,1);self.dashboard_metrics[key]=value
        l.addLayout(cards)

        body=QGridLayout();body.setHorizontalSpacing(14);body.setVerticalSpacing(12)
        center=QFrame();center.setObjectName('dashboardPanel');center_layout=QVBoxLayout(center);center_layout.setContentsMargins(13,12,13,13);center_layout.setSpacing(9)
        center_header=QHBoxLayout();center_title=QLabel('\u25b0  MATCH CENTER');center_title.setStyleSheet('font-size:16px;font-weight:900;color:#f2f4f7;');center_header.addWidget(center_title);center_header.addStretch();self.center_count=QLabel('0 records');self.center_count.setStyleSheet('color:#aab4c3');center_header.addWidget(self.center_count);center_layout.addLayout(center_header)
        self.center_carousel=StatCarousel();center_layout.addWidget(self.center_carousel)
        self.center_tabs=QTabWidget()
        self.center_tables={};self.center_rows={'records':[],'posts':[],'review':[]}
        for view,headers in (('records',['MATCH ID','RESULT','PLACE','MODE','KILLS','ELIMS','DAMAGE','DEATHS','ASSISTS','DATE / TIME','STATUS','ATTEMPTS']),('posts',['MATCH ID','RESULT','MODE','KILLS','DAMAGE','DATE / TIME']),('review',['MATCH ID','RESULT','MODE','STATUS','ATTEMPTS','DATE / TIME'])):
            table=QTableWidget(0,len(headers));table.setHorizontalHeaderLabels(headers);table.setEditTriggers(QTableWidget.NoEditTriggers);table.setSelectionBehavior(QTableWidget.SelectRows);table.setSelectionMode(QTableWidget.SingleSelection);table.setAlternatingRowColors(True);table.verticalHeader().hide();table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents);table.setMinimumHeight(260)
            table.cellDoubleClicked.connect(lambda _row,_col,view=view:self.open_center_match(view,_row,_col))
            self.center_tabs.addTab(table,view.upper());self.center_tables[view]=table
        self.center_tabs.currentChanged.connect(lambda _index:self.refresh_center_count())
        center_layout.addWidget(self.center_tabs,1);body.addWidget(center,0,0,2,2)

        health=QFrame();health.setObjectName('dashboardPanel');health_layout=QVBoxLayout(health);health_layout.setContentsMargins(13,12,13,13);health_layout.setSpacing(10)
        health_title=QLabel('▮  SYSTEM STATUS');health_title.setStyleSheet('font-size:15px;font-weight:900;color:#f2f4f7;');health_layout.addWidget(health_title)
        self.dashboard_health={}
        for key,label in (('codmunity','CODMunity data'),('ocr','Scoreboard OCR'),('bridge','Chrome bridge'),('discord','Discord webhook')):
            line=QFrame();line.setObjectName('healthRow');row=QHBoxLayout(line);row.setContentsMargins(9,7,9,7);indicator=QLabel('●');indicator.setObjectName('healthDot');name=QLabel(label);value=QLabel('Checking');value.setObjectName('healthValue');row.addWidget(indicator);row.addWidget(name,1);row.addWidget(value);health_layout.addWidget(line);self.dashboard_health[key]=(indicator,value)
        body.addWidget(health,0,2)

        tools=QFrame();tools.setObjectName('dashboardPanel');tools_layout=QVBoxLayout(tools);tools_layout.setContentsMargins(13,12,13,13);tools_layout.setSpacing(8)
        tools_title=QLabel('⚡  QUICK TOOLS');tools_title.setStyleSheet('font-size:15px;font-weight:900;color:#f2f4f7;');tools_layout.addWidget(tools_title)
        review=self.button('🏆   REVIEW LATEST MATCH',self.open_latest,True);capture=self.button('◉   OPEN SCOREBOARD CAPTURE',lambda:self.nav.setCurrentRow(3));poster=self.button('▧   OPEN AI POSTER STUDIO',lambda:self.nav.setCurrentRow(5))
        tools_layout.addWidget(review);tools_layout.addWidget(capture);tools_layout.addWidget(poster);body.addWidget(tools,1,2)
        self.hotkey_hint=QLabel('At match end, press Ctrl + Shift + F8 while the scoreboard is visible.');self.hotkey_hint.setWordWrap(True);self.hotkey_hint.setStyleSheet('font-size:11px;color:#aab4c3;padding:5px 2px;');tools_layout.addWidget(self.hotkey_hint)
        body.setColumnStretch(0,1);body.setColumnStretch(1,1);body.setColumnStretch(2,0);body.setRowStretch(0,3);body.setRowStretch(1,2);l.addLayout(body,1)

        footer=QFrame();footer.setObjectName('dashboardFooter');footer_layout=QHBoxLayout(footer);footer_layout.setContentsMargins(13,8,13,8)
        self.footer_tracking=QLabel('●  STANDBY');self.footer_tracking.setStyleSheet('font-weight:800;color:#9da7b5');self.footer_match=QLabel('Last match: —');self.footer_mode=QLabel('Mode: '+self.active_mode_label());footer_layout.addWidget(self.footer_tracking);footer_layout.addSpacing(14);footer_layout.addWidget(self.footer_match);footer_layout.addStretch();footer_layout.addWidget(self.footer_mode);l.addWidget(footer)
    def build_solo(self):
        self.page('Solo Ops','CODMunity integration settings are available under Settings.')
        self.solo_settings_panel=QWidget();l=QVBoxLayout(self.solo_settings_panel);l.setContentsMargins(12,12,12,12);l.setSpacing(12)
        form=QFormLayout();self.player=self.edit(self.settings.values['player']);self.stats_dir=self.edit(self.settings.values['stats_dir']);self.warzone=QCheckBox('Enable confirmed Warzone reports in Solo Ops');self.warzone.setChecked(self.settings.values.get('warzone_enabled',False))
        form.addRow('Your CODMunity / COD username',self.player);form.addRow('CODMunity stats folder',self.stats_dir);form.addRow('',self.button('Browse stats folder',self.pick_stats));form.addRow('',self.warzone)
        self.profile=self.edit(self.settings.values.get('profile_url',''));form.addRow('Optional CODMunity profile URL',self.profile);l.addLayout(form)
        self.row(l,self.button('Test local data',self.test_data))
        self.data_status=QPlainTextEdit();self.data_status.setReadOnly(True);l.addWidget(self.data_status,1)
    def build_squad(self):
        l=self.page('Squad Night','CODMunity match context plus the captured scoreboard become one verified, match-bound poster job.')
        for text in ['01  New CODMunity matches enter the queue with their recorded mode, result and placement.', '02  Capture the end-match scoreboard. OCR reads its visible columns; review names and values before marking them verified.', '03  Poster Studio combines actual match context, verified player rows and conservative roster/photo matches. Ambiguous or unconsented photos are withheld.', '04  GENERATE MATCH POSTER stages the complete job for Vintendo Squad AI and writes a single-prompt import file plus a numbered photo map.', '05  Use the supported import controls in Meta AI Automation or the regular Meta AI page. Prompt submission, reference upload and download remain visible user actions; the generated scene is prompted to include match results and verified statistics in-world.']:
            label=QLabel(text);label.setWordWrap(True);label.setStyleSheet('padding:15px;background:#1b2330;border-left:3px solid #ff9638');l.addWidget(label)
        self.auto_post=QCheckBox('Automatically post completed posters to Discord');self.auto_post.setChecked(self.settings.values.get('auto_post',False));l.addWidget(self.auto_post)
        self.row(l,self.button('Activate Squad Night',lambda:self.mode.setCurrentText('Squad Night'),True));l.addStretch()
    def build_capture(self):
        l=self.page('Scoreboard Capture','Set the scoreboard region once. At match end, press Ctrl + Shift + F8 while the final scoreboard is visible; OCR prepares a draft for review.');self.capture_subtitle=l.itemAt(1).widget()
        self.monitor=QComboBox();self.roi_label=QLabel('Region: '+str(self.settings.values.get('roi') or 'not selected'))
        self.row(l,self.monitor,self.button('Refresh monitors',self.refresh_monitors),self.button('Select scoreboard region',self.pick_region),self.button('Capture & OCR',self.capture_once,True))
        l.addWidget(self.roi_label);self.hotkey_status=QLabel('Global hotkey: starting…');self.hotkey_status.setStyleSheet('color:#99a8bc;font-weight:700;');l.addWidget(self.hotkey_status)
        self.auto_detect=QCheckBox('Optional continuous OCR (uses more CPU)');self.auto_detect.setChecked(self.settings.values.get('auto_detect',False));self.auto_detect.setToolTip('Off by default. Use Ctrl + Shift + F8 to capture only when the final scoreboard is visible. Enable continuous scanning only if you prefer automatic detection.');l.addWidget(self.auto_detect)
        self.link_match=QComboBox();l.addWidget(self.link_match)
        self.result_override=QComboBox();self.result_override.addItem('Result: auto (CODMunity link, then scoreboard)','auto');self.result_override.addItem('Victory — I won this match','win');self.result_override.addItem('Defeat — I lost this match','loss');self.result_override.setToolTip('Win/loss comes from the linked CODMunity match first, then the scoreboard placement. Use this only when both are missing or wrong.');l.addWidget(self.result_override)
        split=QSplitter();self.capture_preview=QLabel('No capture yet');self.capture_preview.setAlignment(Qt.AlignCenter);self.capture_preview.setMinimumSize(400,180);self.ocr_text=QPlainTextEdit();self.ocr_text.setPlaceholderText('Local OCR lines. Correct column headings and spacing, then parse again.');split.addWidget(self.capture_preview);split.addWidget(self.ocr_text);l.addWidget(split,1)
        self.row(l,self.button('Parse corrected OCR',self.parse_ocr),self.button('Add player row',self.add_ocr_row),self.button('Remove selected row',self.remove_ocr_row))
        self.ocr_stat_keys=['kills','damage','score','assists','deaths'];self.ocr_table=QTableWidget(0,1+len(self.ocr_stat_keys));self.ocr_table.setHorizontalHeaderLabels(['Player','Kills','Damage','Score','Assists','Deaths']);self.ocr_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch);l.addWidget(self.ocr_table,1)
        self.verified=QCheckBox('I checked these names and numbers against the captured scoreboard');l.addWidget(self.verified)
        self.row(l,self.button('Save verified capture to match',self.save_capture,True),self.button('Post screenshot + OCR only',self.post_capture_only))
    def build_roster(self):
        l=self.page('Player Roster','Manage your COD player roster, aliases, and headshots for Meta AI artwork.')
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
        left.setMinimumWidth(280);split.addWidget(left)
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
        right_layout.addWidget(header)
        self.roster_tabs=QTabWidget()
        info_tab=QWidget();info_form=QFormLayout(info_tab);info_form.setSpacing(10)
        self.roster_name=self.edit();self.roster_display_name=self.edit();self.roster_aliases=self.edit()
        self.roster_enabled=self.roster_switch('Enabled — include this player in the roster and match processing.')
        self.roster_profile_url=self.edit();self.roster_profile_url.setPlaceholderText('https://codmunity.gg/profile/username')
        info_form.addRow('Exact COD username (identity)',self.roster_name)
        info_form.addRow('Poster name (shown in artwork)',self.roster_display_name)
        info_form.addRow('Aliases (separate with ;)',self.roster_aliases)
        info_form.addRow('CODMunity profile link',self.roster_profile_url)
        info_form.addRow('',self.roster_enabled)
        self.roster_tabs.addTab(info_tab,'PLAYER INFO')
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
        perm_tab=QWidget();perm_layout=QVBoxLayout(perm_tab);perm_layout.setSpacing(10)
        self.photo_consent=self.roster_switch('This player agreed to their photo being used with Meta AI')
        perm_layout.addWidget(self.photo_consent)
        consent_hint=QLabel('Allows this headshot in AI-generated artwork (victory/defeat, recaps, etc.). Players without consent are named in prompts with NO REFERENCE IMAGE and shown generically.')
        consent_hint.setWordWrap(True);consent_hint.setStyleSheet('font-size:11px;color:#9da8b7;')
        perm_layout.addWidget(consent_hint)
        self.consent_status=QLabel('');self.consent_status.setWordWrap(True)
        perm_layout.addWidget(self.consent_status);perm_layout.addStretch(1)
        self.roster_tabs.addTab(perm_tab,'PERMISSIONS')
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
        split.setStretchFactor(0,3);split.setStretchFactor(1,7)
        l.addWidget(split,1)
        tester=QFrame();tester.setObjectName('dashboardPanel')
        tester_layout=QHBoxLayout(tester);tester_layout.setContentsMargins(14,10,14,10);tester_layout.setSpacing(10)
        tester_label=QLabel('OCR username to test');tester_label.setStyleSheet('font-size:12px;font-weight:800;color:#c4ccd8;')
        tester_layout.addWidget(tester_label)
        self.name_test=self.edit();self.name_test.setPlaceholderText('Enter a COD username from a screenshot...')
        tester_layout.addWidget(self.name_test,1)
        tester_layout.addWidget(self.button('Test Name Match',self.test_name))
        l.addWidget(tester)
        self.name_match_result=QLabel('');self.name_match_result.setStyleSheet('font-size:11px;color:#9da8b7;');self.name_match_result.setWordWrap(True)
        l.addWidget(self.name_match_result)
        self.photo_path=self.edit();self.photo_path.setVisible(False)
        self.current_player=None;self.roster_rows=[];self.roster_visible_ids=[];self.roster_dirty=False;self._roster_loading=False;self.roster_photo_removed=False
        for widget in (self.roster_name,self.roster_display_name,self.roster_aliases):widget.textChanged.connect(lambda _:self.mark_roster_dirty())
        self.roster_profile_url.textChanged.connect(lambda _: (self.refresh_profile_tab(),self.mark_roster_dirty()))
        self.roster_enabled.toggled.connect(lambda _:self.mark_roster_dirty())
        self.photo_consent.toggled.connect(lambda _:self.mark_roster_dirty())
    def roster_switch(self,text):
        box=QCheckBox(text);box.setObjectName('rosterSwitch');return box
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
    def roster_card_widget(self,player):
        card=QWidget()
        layout=QHBoxLayout(card);layout.setContentsMargins(10,8,10,8);layout.setSpacing(10)
        thumb=QLabel();thumb.setFixedSize(48,48)
        photo=str(player.get('photo') or '')
        thumb.setPixmap(self.rounded_thumb(photo,48) if photo and Path(photo).is_file() else self.default_avatar(48))
        layout.addWidget(thumb)
        text=QVBoxLayout();text.setSpacing(2)
        display=str(player.get('display_name') or player.get('name',''))
        name_label=QLabel(display);name_label.setObjectName('rosterCardName')
        sub=QLabel('COD: '+str(player.get('name','')));sub.setObjectName('rosterCardSub')
        text.addWidget(name_label);text.addWidget(sub)
        layout.addLayout(text,1)
        enabled=bool(player.get('enabled',True))
        status=QLabel(('● Enabled' if enabled else '○ Disabled'));status.setStyleSheet('font-size:11px;font-weight:700;color:#4ade80;background:transparent;' if enabled else 'font-size:11px;font-weight:700;color:#6b7280;background:transparent;')
        layout.addWidget(status,0,Qt.AlignVCenter)
        return card
    def build_studio(self):
        l=self.page('AI Poster Studio','CODMunity + verified scoreboard → complete extension job → Meta AI artwork with in-scene match results → Discord.')
        self.studio_match=QLabel('Select a match or save a reviewed OCR scoreboard.');l.addWidget(self.studio_match)
        self.prompt=QPlainTextEdit();self.prompt.setReadOnly(True);self.prompt.setPlaceholderText('A creative 16:9 prompt is prepared automatically from the verified scoreboard.');l.addWidget(self.prompt,1)
        self.generate_poster_button=self.button('GENERATE MATCH POSTER',self.prepare_artwork,True);self.generate_poster_button.setEnabled(False);l.addWidget(self.generate_poster_button)
        self.photo_assignments=QLabel('Consented reference photos are matched automatically when available.');self.photo_assignments.setWordWrap(True);l.addWidget(self.photo_assignments)
        self.studio_heads=QHBoxLayout();l.addLayout(self.studio_heads)
        self.art_preview=QLabel('Finished artwork preview');self.art_preview.setAlignment(Qt.AlignCenter);self.art_preview.setMinimumHeight(180);l.addWidget(self.art_preview,1)
        self.approve_post_button=self.button('APPROVE & POST',self.post_active,True);self.row(l,self.approve_post_button)
        self.row(l,self.button('Cancel browser job',self.cancel_browser),self.button('Retry / review selected job',self.retry_job));self.job_status=QLabel('No active job');l.addWidget(self.job_status)
    def build_cod_ai(self):
        l=self.page('COD AI','Capture a Call of Duty moment, test cinematic clips, and send them to Meta AI for cinematic videos.');l.setSpacing(9);l.setContentsMargins(24,14,24,14)
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
        l.addWidget(steps)
        actions=QHBoxLayout();actions.setSpacing(10)
        actions.addWidget(self.button('Open Screenshots Folder',self.open_screenshots_folder))
        actions.addWidget(self.button('Capture Game Screenshot',self.capture_cod_ai_screenshot,True),1)
        actions.addWidget(self.button('Send selected screenshot to Meta AI \u2014 Make Video',self.queue_selected_cinematic),1)
        self.cod_ai_hotkey_status=QLabel('Hotkey: Ctrl + F8');self.cod_ai_hotkey_status.setObjectName('hotkeyPill');self.cod_ai_hotkey_status.setAlignment(Qt.AlignCenter);actions.addWidget(self.cod_ai_hotkey_status)
        l.addLayout(actions)
        self.cod_ai_capture_status=QLabel('Screenshots will be saved as lossless PNG files in the tracker data folder. Press Ctrl + F8 in game to capture.')
        self.cod_ai_capture_status.setWordWrap(True);self.cod_ai_capture_status.setStyleSheet('color:#99a8bc');l.addWidget(self.cod_ai_capture_status)
        media_panel=QFrame();media_panel.setObjectName('dashboardPanel');media_layout=QVBoxLayout(media_panel);media_layout.setContentsMargins(12,12,12,12);media_layout.setSpacing(8)
        media_head=QHBoxLayout();media_title=QLabel('Cinematics & Videos');media_title.setStyleSheet('font-size:16px;font-weight:900;color:#f2f4f7;');media_head.addWidget(media_title);media_head.addStretch(1)
        self.media_count=QLabel('0 screenshots • 0 videos');self.media_count.setStyleSheet('color:#99a8bc;font-weight:700;');media_head.addWidget(self.media_count)
        media_head.addWidget(self.button('Open Cinematics Folder',self.open_cinematics_folder));media_layout.addLayout(media_head)
        self.media_tabs=QTabWidget();self.media_tabs.setObjectName('mediaTabs');self.media_tabs.currentChanged.connect(lambda _index:(self.update_screenshot_grid(),self.update_cinematic_grid()))
        shots_tab=QWidget();shots_layout=QVBoxLayout(shots_tab);shots_layout.setContentsMargins(6,6,6,6);shots_layout.setSpacing(8)
        self.screenshot_tiles={}
        self.cod_ai_screenshots=QListWidget();self.cod_ai_screenshots.setObjectName('cinematicGallery');self.cod_ai_screenshots.setViewMode(QListWidget.IconMode);self.cod_ai_screenshots.setFlow(QListWidget.LeftToRight);self.cod_ai_screenshots.setMovement(QListWidget.Static);self.cod_ai_screenshots.setResizeMode(QListWidget.Adjust);self.cod_ai_screenshots.setWrapping(True);self.cod_ai_screenshots.setSpacing(6);self.cod_ai_screenshots.setMinimumHeight(220);self.cod_ai_screenshots.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);self.cod_ai_screenshots.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded);self.cod_ai_screenshots.currentItemChanged.connect(self.preview_cod_ai_screenshot);shots_layout.addWidget(self.cod_ai_screenshots,1)
        self.cod_ai_empty=QLabel('No screenshots yet\nCapture a screenshot or open your folder.');self.cod_ai_empty.setObjectName('codEmpty');self.cod_ai_empty.setAlignment(Qt.AlignCenter);self.cod_ai_empty.setWordWrap(True);self.cod_ai_empty.hide();shots_layout.addWidget(self.cod_ai_empty,1)
        self.cod_ai_preview_info=QLabel('Select a screenshot to preview');self.cod_ai_preview_info.setStyleSheet('color:#99a8bc;font-weight:700;');shots_layout.addWidget(self.cod_ai_preview_info)
        self.cod_ai_preview=QLabel('Screenshot preview');self.cod_ai_preview.setAlignment(Qt.AlignCenter);self.cod_ai_preview.setMinimumSize(200,84);self.cod_ai_preview.setMaximumHeight(84);self.cod_ai_preview.setStyleSheet('background:#080a0e;border:1px solid #292f39;border-radius:6px;color:#77818f;');shots_layout.addWidget(self.cod_ai_preview)
        self.media_tabs.addTab(shots_tab,'Screenshots')
        videos_tab=QWidget();videos_layout=QVBoxLayout(videos_tab);videos_layout.setContentsMargins(6,6,6,6);videos_layout.setSpacing(8)
        self.cinematic_tiles={};self.cinematic_duration_cache={}
        self.cinematic_results=QListWidget();self.cinematic_results.setObjectName('cinematicGallery');self.cinematic_results.setViewMode(QListWidget.IconMode);self.cinematic_results.setFlow(QListWidget.LeftToRight);self.cinematic_results.setMovement(QListWidget.Static);self.cinematic_results.setResizeMode(QListWidget.Adjust);self.cinematic_results.setWrapping(True);self.cinematic_results.setSpacing(6);self.cinematic_results.setMinimumHeight(300);self.cinematic_results.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);self.cinematic_results.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded);self.cinematic_results.currentItemChanged.connect(self.preview_cinematic_video);videos_layout.addWidget(self.cinematic_results,1)
        self.cinematic_empty=QLabel('No videos yet\nSend a screenshot to Meta AI to make a cinematic.');self.cinematic_empty.setObjectName('codEmpty');self.cinematic_empty.setAlignment(Qt.AlignCenter);self.cinematic_empty.setWordWrap(True);self.cinematic_empty.hide();videos_layout.addWidget(self.cinematic_empty,1)
        self.media_tabs.addTab(videos_tab,'Videos')
        media_layout.addWidget(self.media_tabs,1)
        self.cinematic_status=QLabel('Choose an existing gameplay screenshot to test Meta AI Frame to Video. Nothing posts automatically.')
        self.cinematic_status.setWordWrap(True);self.cinematic_status.setStyleSheet('color:#99a8bc')
        main_split=QSplitter(Qt.Horizontal);main_split.setHandleWidth(10);main_split.setChildrenCollapsible(False)
        main_split.addWidget(media_panel)
        preview_panel=QFrame();preview_panel.setObjectName('dashboardPanel');right_layout=QVBoxLayout(preview_panel);right_layout.setContentsMargins(12,12,12,12);right_layout.setSpacing(8)
        preview_title=QLabel('Preview');preview_title.setStyleSheet('font-size:16px;font-weight:900;color:#f2f4f7;');right_layout.addWidget(preview_title)
        preview_sub=QLabel('Watch the selected video.');preview_sub.setStyleSheet('color:#99a8bc');right_layout.addWidget(preview_sub)
        self.cinematic_video_widget=QVideoWidget();self.cinematic_fullscreen_filter=FullscreenExitFilter(lambda:bool(getattr(self,'cinematic_video_widget',None) and self.cinematic_video_widget.isFullScreen()),self.exit_cinematic_fullscreen,self.toggle_cinematic_fullscreen)
        app_instance=QApplication.instance()
        if app_instance is not None:app_instance.installEventFilter(self.cinematic_fullscreen_filter)
        self.cinematic_video_widget.setMinimumHeight(220);self.cinematic_video_widget.setStyleSheet('background:#050609;border:1px solid #343a46;border-radius:6px;');right_layout.addWidget(self.cinematic_video_widget,1)
        self.cinematic_player=QMediaPlayer(self);self.cinematic_audio=QAudioOutput(self);self.cinematic_audio.setVolume(.8);self.cinematic_player.setAudioOutput(self.cinematic_audio);self.cinematic_player.setVideoOutput(self.cinematic_video_widget);self.cinematic_player.errorOccurred.connect(lambda error,message:self.cinematic_status.setText('Video preview error: '+str(message)[:200]) if error else None)
        self.cinematic_seek=QSlider(Qt.Horizontal);self.cinematic_seek.setRange(0,0)
        self.cinematic_time=QLabel('0:00 / 0:00');self.cinematic_time.setStyleSheet('color:#c4ccd8;font-weight:700;')
        self.cinematic_player.durationChanged.connect(lambda duration:(self.cinematic_seek.setRange(0,duration),self.cinematic_time.setText(format_cinematic_duration(self.cinematic_player.position()/1000)+' / '+format_cinematic_duration(duration/1000))))
        self.cinematic_player.positionChanged.connect(lambda position:(self.cinematic_seek.setValue(position),self.cinematic_time.setText(format_cinematic_duration(position/1000)+' / '+format_cinematic_duration(self.cinematic_player.duration()/1000))))
        self.cinematic_seek.sliderMoved.connect(self.cinematic_player.setPosition)
        playback=QHBoxLayout();self.cinematic_play_button=self.button('Play',self.toggle_cinematic_playback);self.cinematic_play_button.setObjectName('codPlay');self.cinematic_play_button.setEnabled(False);playback.addWidget(self.cinematic_play_button);playback.addWidget(self.button('Stop preview',self.cinematic_player.stop));playback.addWidget(self.cinematic_seek,1);playback.addWidget(self.cinematic_time);right_layout.addLayout(playback)
        volume_row=QHBoxLayout();volume_label=QLabel('Volume');volume_label.setStyleSheet('color:#99a8bc;font-weight:700;');volume_row.addWidget(volume_label)
        self.cinematic_volume=QSlider(Qt.Horizontal);self.cinematic_volume.setRange(0,100);self.cinematic_volume.setValue(80);self.cinematic_volume.setMaximumWidth(120);self.cinematic_volume.valueChanged.connect(lambda value:self.cinematic_audio.setVolume(value/100));volume_row.addWidget(self.cinematic_volume);volume_row.addStretch(1)
        right_layout.addLayout(volume_row)
        self.cinematic_fullscreen_button=self.button('Fullscreen',self.toggle_cinematic_fullscreen);self.cinematic_fullscreen_button.setToolTip('Fullscreen - press Esc or double-click the video to exit');self.cinematic_fullscreen_button.setEnabled(False)
        self.refresh_cinematic_button=self.button('Refresh',self.refresh_cinematic_results)
        preview_actions=QHBoxLayout();preview_actions.addStretch(1);preview_actions.addWidget(self.cinematic_fullscreen_button);preview_actions.addWidget(self.refresh_cinematic_button);right_layout.addLayout(preview_actions)
        main_split.addWidget(preview_panel);main_split.setStretchFactor(0,5);main_split.setStretchFactor(1,6);main_split.setSizes([740,860]);l.addWidget(main_split,1)
        action_bar=QHBoxLayout();action_bar.setSpacing(10);action_bar.addWidget(self.cinematic_status,1)
        self.test_cinematic_button=self.button('Test Video',self.test_cinematic_video);self.test_cinematic_button.setObjectName('codPrimary');action_bar.addWidget(self.test_cinematic_button)
        self.post_cinematic_button=self.button('Post to Discord',self.post_cinematic_video);self.post_cinematic_button.setObjectName('codSecondary');self.post_cinematic_button.setEnabled(False);action_bar.addWidget(self.post_cinematic_button)
        l.addLayout(action_bar)
        talent_friends_row=QHBoxLayout();talent_friends_row.setSpacing(10)
        talent_panel=QFrame();talent_panel.setObjectName('dashboardPanel');talent_layout=QVBoxLayout(talent_panel);talent_layout.setContentsMargins(12,10,12,10);talent_layout.setSpacing(8)
        talent_head=QHBoxLayout();talent_head.setSpacing(6);talent_icon=QLabel('◉');talent_icon.setStyleSheet('color:#f97316;font-size:14px;font-weight:900;');talent_head.addWidget(talent_icon)
        talent_title=QLabel('TALENT PHOTO');talent_title.setObjectName('codPanelTitle');talent_head.addWidget(talent_title);talent_head.addStretch(1);talent_layout.addLayout(talent_head)
        talent_body=QHBoxLayout();talent_body.setSpacing(10)
        self.talent_preview=QLabel();self.talent_preview.setFixedSize(60,60);self.talent_preview.setAlignment(Qt.AlignCenter);self.talent_preview.setStyleSheet('background:#080a0e;border:1px solid #292f39;border-radius:6px;color:#77818f;');talent_body.addWidget(self.talent_preview)
        talent_meta=QVBoxLayout();talent_meta.setSpacing(6)
        talent_name_row=QHBoxLayout();talent_name_row.setSpacing(6)
        self.talent_dot=QLabel();self.talent_dot.setObjectName('talentDot');self.talent_dot.hide();talent_name_row.addWidget(self.talent_dot)
        self.talent_info=QLabel('No photo set');self.talent_info.setStyleSheet('color:#c4ccd8;font-weight:700;');talent_name_row.addWidget(self.talent_info,1);talent_meta.addLayout(talent_name_row)
        talent_btns=QHBoxLayout();talent_btns.setSpacing(6)
        talent_btns.addWidget(self.button('Change Photo',self.set_cinematic_talent));talent_btns.addWidget(self.button('Remove Photo',self.remove_cinematic_talent));talent_btns.addStretch(1);talent_meta.addLayout(talent_btns)
        talent_body.addLayout(talent_meta,1);talent_layout.addLayout(talent_body)
        talent_friends_row.addWidget(talent_panel,2)
        friends_panel=QFrame();friends_panel.setObjectName('dashboardPanel');friends_layout=QVBoxLayout(friends_panel);friends_layout.setContentsMargins(12,10,12,10);friends_layout.setSpacing(8)
        friends_head=QHBoxLayout();friends_head.setSpacing(6)
        friends_title=QLabel('FRIENDS IN THIS VIDEO');friends_title.setObjectName('codPanelTitle');friends_head.addWidget(friends_title)
        friends_opt=QLabel('(Optional)');friends_opt.setStyleSheet('font-size:11px;color:#9da8b7;');friends_head.addWidget(friends_opt);friends_head.addStretch(1)
        self.talent_friends_status=QLabel('');self.talent_friends_status.setStyleSheet('font-size:11px;font-weight:700;color:#9da8b7;');friends_head.addWidget(self.talent_friends_status);friends_layout.addLayout(friends_head)
        self.talent_friends_box=QGridLayout();self.talent_friends_box.setSpacing(6);friends_layout.addLayout(self.talent_friends_box,1)
        talent_friends_row.addWidget(friends_panel,3);l.addLayout(talent_friends_row)
        self.refresh_talent_friends()
        self.refresh_talent_preview()
        QTimer.singleShot(0,self.update_screenshot_grid);QTimer.singleShot(0,self.update_cinematic_grid)
        self.refresh_cinematic_results()
    def build_tracker_browser(self):
        l=self.page('Tracker Browser','The automation browser runs here so both Chrome windows do not need juggling. Leave the embedded Meta AI tab untouched while a video generates.')
        self.browser_status=QLabel('Tracker browser: checking…');self.browser_status.setWordWrap(True);l.addWidget(self.browser_status)
        self.browser_queue=QLabel('');self.browser_queue.setWordWrap(True);self.browser_queue.setStyleSheet('color:#99a8bc');l.addWidget(self.browser_queue)
        self.browser_surface=QFrame();self.browser_surface.setObjectName('dashboardPanel');self.browser_surface.setMinimumSize(800,450);l.addWidget(self.browser_surface,1)
        self.row(l,self.button('Open Tracker Chrome',self.open_tracker_chrome,True),self.button('Embed front Chrome window',self.embed_tracker_chrome,True),self.button('Release window',self.release_tracker_chrome),self.button('Refresh browser status',self.refresh_tracker_browser))
        self.refresh_tracker_browser()
    def chrome_executable(self):
        candidates=[Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'Google'/'Chrome'/'Application'/'chrome.exe',
                    Path(os.environ.get('ProgramFiles(x86)',r'C:\Program Files (x86)'))/'Google'/'Chrome'/'Application'/'chrome.exe']
        return next((path for path in candidates if path.is_file()),None)
    def tracker_browser_mode(self):
        try:mode=str(self.settings.values.get('tracker_browser_mode') or 'dedicated').strip().lower()
        except Exception:mode='dedicated'
        return mode if mode in ('dedicated','attach') else 'dedicated'
    def meta_submission(self):
        try:mode=str(self.settings.values.get('meta_submission') or 'inapp').strip().lower()
        except Exception:mode='inapp'
        return mode if mode in ('inapp','extension') else 'inapp'
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
        try:subprocess.Popen([str(executable),'--user-data-dir='+str(self.tracker_chrome_data_dir())])
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
        self.browser_status.setText('Bridge: checking 127.0.0.1:8765…')
        def check():
            import socket
            try:socket.create_connection(('127.0.0.1',8765),timeout=2).close();return True
            except OSError:return False
        def show_bridge(ok):
            if getattr(self,'browser_hold_message',False):return
            try:
                self.browser_status.setText('Bridge: CONNECTED on 127.0.0.1:8765. The extension polls automatically.' if ok else 'Bridge: OFFLINE. Start the tracker with Launch-Phase3-Tracker.cmd, then press Open Tracker Chrome.')
            except (RuntimeError, AttributeError):pass
        self.async_task(check,show_bridge)

    def find_tracker_chrome_windows(self):
        user32=ctypes.windll.user32;found=[]
        @ctypes.WINFUNCTYPE(ctypes.c_bool,wintypes.HWND,wintypes.LPARAM)
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
    def build_settings(self):
        l=self.page('Settings','CODMunity, Meta AI automation, and Discord settings in one place. Mutable files are stored under Windows AppData.')
        self.settings_tabs=QTabWidget();self.settings_tabs.addTab(self.solo_settings_panel,'CODMunity');l.addWidget(self.settings_tabs,1)
        automation_tab=QWidget();automation_layout=QVBoxLayout(automation_tab);automation_layout.setContentsMargins(12,12,12,12);automation_layout.setSpacing(12);self.settings_tabs.addTab(automation_tab,'Automation & Chrome')
        self.poll=QSpinBox();self.poll.setRange(3,60);self.poll.setValue(int(self.settings.values.get('poll_seconds',5)))
        self.browser_timeout=QSpinBox();self.browser_timeout.setRange(10,180);self.browser_timeout.setSuffix(' sec');self.browser_timeout.setValue(int(self.settings.values.get('browser_navigation_timeout',60)))
        self.download_timeout=QSpinBox();self.download_timeout.setRange(5,120);self.download_timeout.setSuffix(' min');self.download_timeout.setValue(max(5,int(self.settings.values.get('browser_download_timeout',1800))//60))
        self.browser_retries=QSpinBox();self.browser_retries.setRange(0,3);self.browser_retries.setValue(int(self.settings.values.get('browser_retries',1)))
        f=QFormLayout();f.addRow('Polling interval (seconds)',self.poll);f.addRow('Meta page navigation timeout',self.browser_timeout);f.addRow('Wait for image download',self.download_timeout);f.addRow('Browser/navigation retries',self.browser_retries);f.addRow('Application data',QLabel(str(E.DATA)));automation_layout.addLayout(f)
        self.row(automation_layout,self.button('Open data folder',lambda:os.startfile(str(E.DATA))),self.button('Open logs folder',lambda:os.startfile(str(E.DATA))))
        automation_layout.addWidget(QLabel('Chrome extension bridge: loopback-only at 127.0.0.1:8765. The pairing token is protected with Windows DPAPI.'))
        self.extension_bridge_status=QLabel('Starting when the application runs normally.');self.extension_bridge_status.setStyleSheet('color:#99a8bc')
        automation_layout.addWidget(self.extension_bridge_status)
        self.row(automation_layout,self.button('Copy Chrome pairing token',self.copy_extension_token),self.button('Rotate pairing token',self.rotate_extension_token),self.button('Reconnect bridge',self.reconnect_extension_bridge))
        browser_mode_note=QLabel('Tracker Chrome profile: the dedicated profile keeps the Meta AI login isolated from your own Chrome; attaching reuses a Meta AI tab you already have open in your own Chrome.');browser_mode_note.setWordWrap(True);automation_layout.addWidget(browser_mode_note)
        self.browser_mode_combo=QComboBox();self.browser_mode_combo.addItems(['Dedicated tracker profile (recommended)','Use my already-open Chrome']);self.browser_mode_combo.setCurrentIndex(1 if self.tracker_browser_mode()=='attach' else 0);browser_mode_form=QFormLayout();browser_mode_form.addRow('Tracker Chrome profile',self.browser_mode_combo);automation_layout.addLayout(browser_mode_form)
        self.meta_submission_combo=QComboBox();self.meta_submission_combo.addItems(['In-app Tracker Browser (recommended)','Legacy Chrome extension']);self.meta_submission_combo.setCurrentIndex(0 if self.meta_submission()=='inapp' else 1);submission_form=QFormLayout();submission_form.addRow('Meta video submission',self.meta_submission_combo);automation_layout.addLayout(submission_form)
        help_text=QLabel('Vintendo COD Automation pairs through this local bridge. After pairing, identified matches are queued automatically; the extension submits the prompt and matched reference images, then returns the generated artwork. Verified match results are requested as part of the Meta AI scene; the tracker does not paint a stats panel over it.');help_text.setWordWrap(True);automation_layout.addWidget(help_text);automation_layout.addStretch()
        self.settings_tabs.addTab(self.discord_settings_panel,'Discord')
        hotkeys_tab=QWidget();hotkeys_layout=QVBoxLayout(hotkeys_tab);hotkeys_layout.setContentsMargins(12,12,12,12);hotkeys_layout.setSpacing(12)
        hotkeys_note=QLabel('Global hotkeys work while the tracker is minimized. Click a box, then press the new keys (hold Ctrl, Shift or Alt plus another key).');hotkeys_note.setWordWrap(True);hotkeys_layout.addWidget(hotkeys_note)
        score_vk,score_mod=self.hotkey_setting('scoreboard_hotkey_vk','scoreboard_hotkey_mod',DEFAULT_SCOREBOARD_HOTKEY)
        shot_vk,shot_mod=self.hotkey_setting('screenshot_hotkey_vk','screenshot_hotkey_mod',DEFAULT_SCREENSHOT_HOTKEY)
        self.scoreboard_hotkey_edit=HotkeyEdit(score_vk,score_mod);self.screenshot_hotkey_edit=HotkeyEdit(shot_vk,shot_mod)
        hotkey_form=QFormLayout();hotkey_form.addRow('Scoreboard capture hotkey',self.scoreboard_hotkey_edit);hotkey_form.addRow('Screenshot hotkey',self.screenshot_hotkey_edit);hotkeys_layout.addLayout(hotkey_form)
        self.row(hotkeys_layout,self.button('Reset hotkeys to defaults',self.reset_hotkeys))
        folders_note=QLabel('Media folders. Leave empty to keep everything inside the application data folder.');folders_note.setWordWrap(True);hotkeys_layout.addWidget(folders_note)
        self.screenshots_dir_edit=self.edit(str(self.settings.values.get('screenshots_dir') or ''));self.screenshots_dir_edit.setPlaceholderText(str(self.screenshots_dir()))
        self.videos_dir_edit=self.edit(str(self.settings.values.get('videos_dir') or ''));self.videos_dir_edit.setPlaceholderText(str(self.videos_dir()))
        folder_form=QFormLayout();folder_form.addRow('Screenshots folder',self.screenshots_dir_edit);folder_form.addRow('Videos folder',self.videos_dir_edit);hotkeys_layout.addLayout(folder_form)
        self.row(hotkeys_layout,self.button('Browse screenshots folder',lambda:self.pick_media_dir(self.screenshots_dir_edit)),self.button('Browse videos folder',lambda:self.pick_media_dir(self.videos_dir_edit)),self.button('Reset folders to defaults',self.reset_media_dirs))
        hotkeys_layout.addStretch()
        self.settings_tabs.addTab(hotkeys_tab,'Hotkeys & Folders')

        self.row(l,self.button('Save all settings',self.save_settings,True))
        self.update_version_label=QLabel(f'Installed version {APP_VERSION}. Updates come from GitHub Releases.');self.update_version_label.setStyleSheet('color:#99a8bc')
        self.row(l,self.update_version_label,self.button('Check for Updates',lambda:self.check_for_app_update(manual=True)))
    # ---- COD Tweaker: review-first Call of Duty config editor ----
    def build_cod_tweaker(self):
        import cod_tweaker as CT
        self._twmod = CT
        self.tw_docs = {}
        self.tw_notes = CT.NotesRegistry(E.ROOT / 'cod_tweaker_notes.json', E.DATA / 'cod-tweaker-notes.json')
        self.tw_backups = CT.BackupStore(E.DATA / 'cod-tweaker-backups')
        self.tw_pins = CT.PinStore(E.DATA / 'cod-tweaker-pins.json')
        self.tw_files = []
        self.tw_current = None
        self.tw_unlocked = set()
        self.tw_preset_pairs = []
        self.tw_preset_index = 0
        self.tw_diff = None
        self.tw_restart_needed = False
        self.tw_edit_ref = None
        self.tw_edit_kind = 'line'
        l = self.page('COD Tweaker', 'Review-first editor for Call of Duty config files. Nothing is written until you confirm, and every write takes a dated backup first. Restart the game after applying changes.')
        self.tw_game_banner = QLabel('Call of Duty looks running: the game reads these files only at start and overwrites them at exit unless the file is locked.')
        self.tw_game_banner.setWordWrap(True)
        self.tw_game_banner.setStyleSheet('padding:9px 12px;background:#3a1418;border-left:3px solid #ed2938;color:#f3c1c5;')
        self.tw_game_banner.setVisible(False)
        l.addWidget(self.tw_game_banner)
        self.tw_stale_row = QWidget()
        stale_layout = QHBoxLayout(self.tw_stale_row)
        stale_layout.setContentsMargins(0, 0, 0, 0)
        self.tw_stale_label = QLabel('This file changed on disk since it was loaded.')
        self.tw_stale_label.setWordWrap(True)
        self.tw_stale_label.setStyleSheet('color:#ff9638;font-weight:700;')
        stale_layout.addWidget(self.tw_stale_label, 1)
        stale_layout.addWidget(self.button('Reload (keep my staged edits)', self.tweaker_reload_keep))
        stale_layout.addWidget(self.button('Show what changed', self.tweaker_show_disk_diff))
        self.tw_stale_row.setVisible(False)
        l.addWidget(self.tw_stale_row)
        file_row = QHBoxLayout()
        file_row.addWidget(QLabel('Config file'))
        self.tw_file_combo = QComboBox()
        self.tw_file_combo.setMinimumWidth(320)
        self.tw_file_combo.currentIndexChanged.connect(self.on_tweaker_file_changed)
        file_row.addWidget(self.tw_file_combo, 1)
        file_row.addWidget(self.button('Refresh files', self.refresh_tweaker_files))
        self.tw_unlock = QCheckBox('Unlock editing for this file')
        self.tw_unlock.setToolTip('Read-only until explicitly unlocked. Unlocking never writes anything by itself.')
        self.tw_unlock.toggled.connect(self.on_tweaker_unlock_toggled)
        file_row.addWidget(self.tw_unlock)
        self.tw_save_btn = self.button('Save staged edits (backs up first)', self.save_tweaker_edits, True)
        file_row.addWidget(self.tw_save_btn)
        l.addLayout(file_row)
        self.tw_restart_label = QLabel('Saved. Restart Call of Duty to apply the new values.')
        self.tw_restart_label.setStyleSheet('color:#ff9638;font-weight:700;')
        self.tw_restart_label.setVisible(False)
        l.addWidget(self.tw_restart_label)
        self.tw_tabs = QTabWidget()
        self.tw_tabs_state = {}
        for category in ('Graphics', 'Performance', 'Hidden Settings'):
            self.tw_tabs.addTab(self._tw_make_settings_tab(category), category)
        self.tw_tabs.addTab(self._tw_make_presets_tab(), 'Presets')
        self.tw_tabs.addTab(self._tw_make_compare_tab(), 'Compare')
        self.tw_tabs.addTab(self._tw_make_backup_tab(), 'Backup & Restore')
        self.tw_tabs.addTab(self._tw_make_about_tab(), 'About')
        self.tw_tabs.currentChanged.connect(lambda _i: self.on_tweaker_row_selected(None))
        l.addWidget(self.tw_tabs, 1)
        l.addWidget(self._tw_make_editor_panel())
        self.tw_error = QLabel('')
        self.tw_error.setStyleSheet('color:#ff6b74;')
        self.tw_error.setWordWrap(True)
        l.addWidget(self.tw_error)
        self.refresh_tweaker_files()
        self.tw_poll = QTimer(self)
        self.tw_poll.setInterval(3000)
        self.tw_poll.timeout.connect(self.poll_tweaker_guards)
        self.tw_poll.start()

    def _tw_make_about_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(6, 6, 18, 6)
        body_layout.setSpacing(4)
        for title, paras in (
            ('Pins and the Guard', (
                "Select a setting and click Pin to pin that value; pinned rows carry a [pinned] tag. Tick Pinned only to list just your pins, and use the Compare tab's Restore only pinned rows button to write pinned values back into the file - a safety backup is taken first.",
                "The Guard in this build keeps watch but never writes on its own: while the section is open it checks every few seconds and shows a banner when Call of Duty looks running (the game reads these files only at start and overwrites them at exit unless the file is locked), plus a 'changed on disk' banner when the file moves under you. After a session, open Compare against On disk, Take what you want, and Save.",
            )),
            ('Locked files and the in-game menu', (
                "While a settings file is locked, the game can't save to it. On some builds that makes the graphics menu misbehave, up to kicking you out of the match when you open it. If you want to use the in-game settings menu, make the file writable first (Lock file button or the per-file Unlock editing checkbox), make your changes in the game, then lock again. Or leave the file unlocked, use pins for the values you care about, and Take them back from the Compare tab afterwards.",
            )),
            ('Compare, presets and scripts', (
                "Open the Compare tab to diff the open file against any of its backups, another file, or a preset; every difference gets a Take button, and taken values become pending changes you then Save. The same view opens from the 'changed on disk' banner, so you can see exactly what the game altered after a session.",
            )),
        ):
            head = QLabel(title)
            head.setStyleSheet('font-size:15px;font-weight:900;color:#f2f4f7;')
            body_layout.addWidget(head)
            for para in paras:
                text = QLabel(para)
                text.setWordWrap(True)
                text.setStyleSheet('color:#c4ccd8;')
                text.setTextInteractionFlags(Qt.TextSelectableByMouse)
                body_layout.addWidget(text)
            body_layout.addSpacing(10)
        body_layout.addStretch()
        scroll.setWidget(body)
        layout.addWidget(scroll)
        return tab
    def _tw_make_settings_tab(self, category):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        search = QLineEdit()
        search.setPlaceholderText('Search key, name, or description...')
        search.setClearButtonEnabled(True)
        layout.addWidget(search)
        checks = QHBoxLayout()
        chk_mod = QCheckBox('Modified only')
        chk_pin = QCheckBox('Pinned only')
        checks.addWidget(chk_mod)
        checks.addWidget(chk_pin)
        chk_hidden = None
        if category != 'Hidden Settings':
            chk_hidden = QCheckBox('Show hidden settings')
            checks.addWidget(chk_hidden)
        checks.addStretch()
        layout.addLayout(checks)
        table = QTableWidget(0, 6)
        table.setHorizontalHeaderLabels(['Setting', 'Value', 'Description', 'Range', 'Warning', 'Pin'])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        table.setColumnWidth(0, 200)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        table.setColumnWidth(1, 300)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Interactive)
        table.setColumnWidth(3, 150)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Interactive)
        table.setColumnWidth(4, 160)
        table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Fixed)
        table.setColumnWidth(5, 80)
        table.verticalHeader().setDefaultSectionSize(36)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().hide()
        layout.addWidget(table, 1)
        state = {'category': category, 'search': search, 'chk_mod': chk_mod,
                 'chk_pin': chk_pin, 'chk_hidden': chk_hidden, 'table': table}
        self.tw_tabs_state[category] = state
        search.textChanged.connect(lambda _t: self.refresh_tweaker_tables())
        chk_mod.toggled.connect(lambda _t: self.refresh_tweaker_tables())
        chk_pin.toggled.connect(lambda _t: self.refresh_tweaker_tables())
        if chk_hidden is not None:
            chk_hidden.toggled.connect(lambda _t: self.refresh_tweaker_tables())
        table.itemSelectionChanged.connect(lambda _t=table: self.on_tweaker_row_selected(_t))
        return tab

    def _tw_make_presets_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        layout.addWidget(QLabel('Presets never apply on load or select. Review the preview, then confirm. Protected settings are always skipped.'))
        top = QHBoxLayout()
        self.tw_preset_combo = QComboBox()
        self.tw_preset_combo.setMinimumWidth(220)
        self.tw_preset_combo.currentIndexChanged.connect(self.preview_tweaker_preset)
        top.addWidget(self.tw_preset_combo, 1)
        top.addWidget(self.button('Dry-run (show what would change)', self.preview_tweaker_preset))
        self.tw_preset_apply_btn = self.button('Review & apply preset...', self.apply_tweaker_preset, True)
        top.addWidget(self.tw_preset_apply_btn)
        layout.addLayout(top)
        self.tw_preset_summary = QLabel('')
        self.tw_preset_summary.setWordWrap(True)
        layout.addWidget(self.tw_preset_summary)
        self.tw_preset_table = QTableWidget(0, 3)
        self.tw_preset_table.setHorizontalHeaderLabels(['Setting', 'Change', 'Detail'])
        self.tw_preset_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tw_preset_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tw_preset_table.setAlternatingRowColors(True)
        self.tw_preset_table.verticalHeader().hide()
        layout.addWidget(self.tw_preset_table, 1)
        self.refresh_tweaker_presets()
        return tab

    def _tw_make_compare_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        top = QHBoxLayout()
        top.addWidget(QLabel('Compare against'))
        self.tw_cmp_source = QComboBox()
        self.tw_cmp_source.setMinimumWidth(260)
        top.addWidget(self.tw_cmp_source, 1)
        top.addWidget(self.button('Refresh diff', self.refresh_tweaker_diff))
        self.tw_cmp_take_btn = self.button('Take selected (stage theirs)', self.take_tweaker_diff_row)
        top.addWidget(self.tw_cmp_take_btn)
        layout.addLayout(top)
        self.tw_cmp_summary = QLabel('')
        layout.addWidget(self.tw_cmp_summary)
        self.tw_cmp_table = QTableWidget(0, 4)
        self.tw_cmp_table.setHorizontalHeaderLabels(['Setting', 'Mine', 'Theirs', 'Status'])
        self.tw_cmp_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tw_cmp_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.tw_cmp_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tw_cmp_table.setAlternatingRowColors(True)
        self.tw_cmp_table.verticalHeader().hide()
        layout.addWidget(self.tw_cmp_table, 1)
        pin_row = QHBoxLayout()
        self.tw_cmp_pinned_only = QCheckBox('Pinned rows only')
        self.tw_cmp_pinned_only.toggled.connect(lambda _t: self.refresh_tweaker_diff())
        pin_row.addWidget(self.tw_cmp_pinned_only)
        self.tw_cmp_relock = QCheckBox('Relock file after selective restore')
        self.tw_cmp_relock.setChecked(True)
        pin_row.addWidget(self.tw_cmp_relock)
        pin_row.addWidget(self.button('Restore only pinned rows...', self.restore_tweaker_pinned))
        self.tw_lock_btn = self.button('Lock file (read-only)', self.toggle_tweaker_lock)
        self.tw_lock_btn.setToolTip('While locked, the in-game menu cannot save over this file.')
        pin_row.addWidget(self.tw_lock_btn)
        pin_row.addStretch()
        layout.addLayout(pin_row)
        return tab

    def _tw_make_backup_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        layout.addWidget(QLabel('Every save and every preset apply writes a dated backup here first. Restores take another safety backup before copying anything back.'))
        self.tw_backup_table = QTableWidget(0, 3)
        self.tw_backup_table.setHorizontalHeaderLabels(['When', 'File', 'Size'])
        self.tw_backup_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tw_backup_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.tw_backup_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tw_backup_table.setAlternatingRowColors(True)
        self.tw_backup_table.verticalHeader().hide()
        layout.addWidget(self.tw_backup_table, 1)
        row = QHBoxLayout()
        row.addWidget(self.button('Refresh list', self.refresh_tweaker_backups))
        row.addWidget(self.button('Restore selected...', self.restore_tweaker_backup, True))
        row.addWidget(self.button('Compare selected', self.compare_tweaker_backup))
        row.addStretch()
        layout.addLayout(row)
        return tab

    def _tw_make_editor_panel(self):
        panel = QFrame()
        panel.setObjectName('dashboardPanel')
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        self.tw_edit_title = QLabel('Select a setting above to review it.')
        self.tw_edit_title.setStyleSheet('font-weight:800;')
        self.tw_edit_title.setWordWrap(True)
        layout.addWidget(self.tw_edit_title)
        self.tw_edit_stack = QStackedWidget()
        self.tw_edit_line = QLineEdit()
        self.tw_edit_combo = QComboBox()
        self.tw_edit_combo.setMinimumWidth(200)
        self.tw_edit_int = QSpinBox()
        self.tw_edit_int.setRange(-1000000000, 1000000000)
        self.tw_edit_double = QDoubleSpinBox()
        self.tw_edit_double.setRange(-1000000000.0, 1000000000.0)
        self.tw_edit_double.setDecimals(3)
        slider_wrap = QWidget()
        slider_layout = QHBoxLayout(slider_wrap)
        slider_layout.setContentsMargins(0, 0, 0, 0)
        self.tw_edit_slider = QSlider(Qt.Horizontal)
        self.tw_edit_slider_value = QLabel('')
        self.tw_edit_slider_value.setMinimumWidth(90)
        self.tw_edit_slider.valueChanged.connect(
            lambda value: self.tw_edit_slider_value.setText(str(value / self._tw_slider_factor())))
        slider_layout.addWidget(self.tw_edit_slider, 1)
        slider_layout.addWidget(self.tw_edit_slider_value)
        for widget in (self.tw_edit_line, self.tw_edit_combo, self.tw_edit_int,
                       self.tw_edit_double, slider_wrap):
            self.tw_edit_stack.addWidget(widget)
        layout.addWidget(self.tw_edit_stack)
        buttons = QHBoxLayout()
        buttons.addWidget(self.button('Stage change', self.stage_tweaker_edit, True))
        self.tw_pin_btn = self.button('Pin', self.pin_tweaker_edit)
        buttons.addWidget(self.tw_pin_btn)
        buttons.addWidget(self.button('Revert to original', self.revert_tweaker_original))
        buttons.addWidget(self.button('Revert to on-disk value', self.revert_tweaker_disk))
        buttons.addStretch()
        layout.addLayout(buttons)
        self.tw_edit_hint = QLabel('Edits stay staged until you press Save. Invalid values are rejected with an inline error.')
        self.tw_edit_hint.setWordWrap(True)
        self.tw_edit_hint.setStyleSheet('color:#99a8bc;')
        layout.addWidget(self.tw_edit_hint)
        return panel

    def _tw_slider_factor(self):
        return float(getattr(self, 'tw_slider_factor_value', 1.0) or 1.0)

    def _tw_current_doc(self):
        return (self.tw_docs or {}).get(self.tw_current or '')

    def _tw_row_visible(self, setting, category, state):
        CT = self._twmod
        note = self.tw_notes.note_for(setting)
        hidden = bool(note.get('hidden', False))
        home = CT.category_for(setting.section, hidden)
        if home != category:
            show_hidden = state.get('chk_hidden') is not None and state['chk_hidden'].isChecked()
            if not (show_hidden and hidden and category != 'Hidden Settings'):
                return False
        needle = state['search'].text().strip().lower()
        if needle and needle not in (setting.key + ' ' + setting.name + ' ' + (setting.description or '')).lower():
            return False
        if state['chk_mod'].isChecked() and not setting.modified:
            return False
        if state['chk_pin'].isChecked() and not self.tw_pins.is_pinned(setting.key):
            return False
        return True

    def refresh_tweaker_files(self):
        CT = self._twmod
        try:
            self.tw_files = CT.CODTweakerLocator().find()
        except Exception as exc:
            self.log('COD Tweaker file scan failed: ' + str(exc))
            self.tw_files = []
        combo = self.tw_file_combo
        combo.blockSignals(True)
        combo.clear()
        if not self.tw_files:
            combo.addItem('No COD config files found (see tooltip)', None)
            combo.setToolTip('Looked under %LOCALAPPDATA%\\Activision and Documents\\Call of Duty. Place a config file there, then press Refresh files.')
        for item in self.tw_files:
            combo.addItem('%s — %s' % (((item.get('game') + ' \u2014 ') if item.get('game') else '') + (item.get('group') or item['kind']), item['path'].name), str(item['path']))
        combo.blockSignals(False)
        if self.tw_files:
            first = str(self.tw_files[0]['path'])
            combo.setCurrentIndex(0)
            if self.tw_current not in (str(item['path']) for item in self.tw_files):
                self.tw_current = first
        else:
            self.tw_current = None
        self.tw_unlock.blockSignals(True)
        self.tw_unlock.setChecked(bool(self.tw_current and self.tw_current in self.tw_unlocked))
        self.tw_unlock.blockSignals(False)
        self.on_tweaker_file_changed(combo.currentIndex())

    def on_tweaker_file_changed(self, _index):
        CT = self._twmod
        data = self.tw_file_combo.currentData() if hasattr(self, 'tw_file_combo') else None
        self.tw_current = str(data) if data else (str(self.tw_files[0]['path']) if self.tw_files else None)
        if self.tw_current and self.tw_current not in (self.tw_docs or {}):
            try:
                self.tw_docs[self.tw_current] = CT.CODDocument.load(self.tw_current)
            except (OSError, ValueError) as exc:
                self.log('COD Tweaker could not parse %s: %s' % (self.tw_current, exc))
                self.tw_docs[self.tw_current] = None
        self.tw_unlock.blockSignals(True)
        self.tw_unlock.setChecked(bool(self.tw_current and self.tw_current in self.tw_unlocked))
        self.tw_unlock.blockSignals(False)
        self.tw_edit_ref = None
        self.refresh_tweaker_tables()
        self.refresh_tweaker_backups()
        self.refresh_tweaker_compare_sources()
        self.refresh_tweaker_diff()
        self.preview_tweaker_preset()
        self.poll_tweaker_guards()
        self.update_tweaker_lock_button()

    def on_tweaker_unlock_toggled(self, checked):
        if not self.tw_current:
            return
        if checked:
            self.tw_unlocked.add(self.tw_current)
        else:
            self.tw_unlocked.discard(self.tw_current)
        self.on_tweaker_row_selected(None)

    def refresh_tweaker_tables(self):
        if not hasattr(self, 'tw_tabs_state'):
            return
        from PySide6.QtGui import QColor
        doc = self._tw_current_doc()
        settings = doc.settings if doc is not None else []
        unlocked_file = bool(self.tw_current and self.tw_current in self.tw_unlocked)
        for category, state in self.tw_tabs_state.items():
            table = state['table']
            table.blockSignals(True)
            table.setRowCount(0)
            for setting in settings:
                if not self._tw_row_visible(setting, category, state):
                    continue
                note = self.tw_notes.note_for(setting)
                row = table.rowCount()
                table.insertRow(row)
                name_item = QTableWidgetItem(setting.name + (' *' if setting.modified else ''))
                name_item.setData(Qt.UserRole, setting.key)
                name_item.setToolTip(setting.key + ((' @' + setting.variant) if setting.variant else ''))
                editable = unlocked_file and not setting.protected
                desc_text = note.get('note', '') or setting.description
                desc_item = QTableWidgetItem(desc_text)
                desc_item.setToolTip(desc_text)
                range_item = QTableWidgetItem(setting.range_text())
                warnings = []
                if setting.protected:
                    warnings.append('DO NOT MODIFY')
                if note.get('hidden', False):
                    warnings.append('Not in the in-game menu')
                suggested = str(note.get('suggested', '') or '')
                if suggested and suggested != setting.effective and not setting.protected:
                    warnings.append('Differs from suggested (%s)' % suggested)
                warn_item = QTableWidgetItem(' · '.join(warnings))
                if setting.protected:
                    warn_item.setForeground(QColor('#ff6b74'))
                for column, item in enumerate((name_item, desc_item, range_item, warn_item)):
                    table.setItem(row, column if column < 1 else column + 1, item)
                table.setCellWidget(row, 1, self._tw_row_value_widget(setting, editable))
                pin_btn = QPushButton('Unpin' if self.tw_pins.is_pinned(setting.key) else 'Pin')
                pin_btn.setToolTip('Pin this value')
                pin_btn.clicked.connect(lambda _c=False, k=setting.key: self.toggle_tweaker_pin(k))
                table.setCellWidget(row, 5, pin_btn)
            table.blockSignals(False)
        staged = len(doc.staged_changes()) if doc is not None else 0
        self.tw_save_btn.setText('Save %d staged edit(s) (backs up first)' % staged if staged else 'Save staged edits (backs up first)')
        self.on_tweaker_row_selected(None)

    def _tw_selected_setting(self, table):
        doc = self._tw_current_doc()
        if doc is None or table is None or table.currentRow() < 0:
            return None, None
        key_item = table.item(table.currentRow(), 0)
        if key_item is None:
            return None, None
        ref = key_item.data(Qt.UserRole)
        return doc.match(key=ref), ref

    def on_tweaker_row_selected(self, table):
        if table is None:
            table = (self.tw_tabs_state.get(self.tw_tabs.tabText(self.tw_tabs.currentIndex()), {}) or {}).get('table')
            if table is not None and table.currentRow() < 0:
                table = None
                for state in self.tw_tabs_state.values():
                    if state['table'].currentRow() >= 0:
                        table = state['table']
                        break
        setting, ref = self._tw_selected_setting(table)
        unlocked = bool(self.tw_current and self.tw_current in self.tw_unlocked)
        for widget in (self.tw_edit_line, self.tw_edit_combo, self.tw_edit_int,
                       self.tw_edit_double, self.tw_edit_slider):
            widget.setEnabled(False)
        if setting is None:
            self.tw_edit_ref = None
            self.tw_edit_title.setText('Select a setting above to review it.')
            self.tw_pin_btn.setText('Pin')
            return
        self.tw_edit_ref = ref
        note = self.tw_notes.note_for(setting)
        pinned = self.tw_pins.is_pinned(setting.key)
        title = '%s  —  %s' % (setting.name, setting.effective)
        if setting.modified:
            title += '  (staged: %s)' % setting.pending
        if pinned:
            title += '  [pinned]'
        if setting.protected:
            title += '  [DO NOT MODIFY — locked]'
        self.tw_edit_title.setText(title)
        self.tw_edit_title.setToolTip('%s%s\n%s' % (setting.key, (' @' + setting.variant) if setting.variant else '', note.get('note', '')))
        self.tw_pin_btn.setText('Unpin' if pinned else 'Pin')
        editable = unlocked and not setting.protected
        if setting.kind == 'bool':
            self.tw_edit_stack.setCurrentWidget(self.tw_edit_combo)
            self.tw_edit_combo.blockSignals(True)
            self.tw_edit_combo.clear()
            self.tw_edit_combo.addItems(['true', 'false'])
            self.tw_edit_combo.setCurrentText(setting.effective)
            self.tw_edit_combo.blockSignals(False)
            self.tw_edit_combo.setEnabled(editable)
            self.tw_edit_kind = 'combo'
        elif setting.kind == 'choice':
            self.tw_edit_stack.setCurrentWidget(self.tw_edit_combo)
            self.tw_edit_combo.blockSignals(True)
            self.tw_edit_combo.clear()
            self.tw_edit_combo.addItems(setting.options)
            self.tw_edit_combo.setCurrentText(setting.effective)
            self.tw_edit_combo.blockSignals(False)
            self.tw_edit_combo.setEnabled(editable)
            self.tw_edit_kind = 'combo'
        elif setting.kind == 'number' and setting.min is not None and setting.max is not None and (setting.max - setting.min) <= 10000:
            factor = 10.0 ** (setting.decimals or 0)
            self.tw_slider_factor_value = factor
            self.tw_edit_stack.setCurrentWidget(self.tw_edit_slider.parentWidget())
            self.tw_edit_slider.blockSignals(True)
            self.tw_edit_slider.setRange(int(round(setting.min * factor)), int(round(setting.max * factor)))
            try:
                self.tw_edit_slider.setValue(int(round(float(setting.effective) * factor)))
            except (TypeError, ValueError):
                self.tw_edit_slider.setValue(int(round(setting.min * factor)))
            self.tw_edit_slider.blockSignals(False)
            self.tw_edit_slider_value.setText(setting.effective)
            self.tw_edit_slider.setEnabled(editable)
            self.tw_edit_kind = 'slider'
        elif setting.kind == 'number':
            try:
                current = float(setting.effective)
            except (TypeError, ValueError):
                current = 0.0
            if setting.decimals in (None, 0) and setting.min is not None and float(setting.min).is_integer() and setting.max is not None and float(setting.max).is_integer():
                self.tw_edit_stack.setCurrentWidget(self.tw_edit_int)
                self.tw_edit_int.setRange(int(setting.min), int(setting.max))
                self.tw_edit_int.setValue(int(round(current)))
                self.tw_edit_int.setEnabled(editable)
                self.tw_edit_kind = 'int'
            else:
                self.tw_edit_stack.setCurrentWidget(self.tw_edit_double)
                decimals = setting.decimals if setting.decimals is not None else 3
                self.tw_edit_double.setDecimals(decimals)
                self.tw_edit_double.setRange(float(setting.min) if setting.min is not None else -1000000000.0,
                                             float(setting.max) if setting.max is not None else 1000000000.0)
                self.tw_edit_double.setValue(current)
                self.tw_edit_double.setEnabled(editable)
                self.tw_edit_kind = 'double'
        else:
            self.tw_edit_stack.setCurrentWidget(self.tw_edit_line)
            self.tw_edit_line.setText(setting.effective)
            self.tw_edit_line.setEnabled(editable)
            self.tw_edit_kind = 'line'
        if not unlocked:
            self.tw_edit_hint.setText('Read-only: tick "Unlock editing for this file" to stage changes.')
        elif setting.protected:
            self.tw_edit_hint.setText('Protected (DO NOT MODIFY): this setting cannot be staged or preset-applied.')
        else:
            self.tw_edit_hint.setText('Edits stay staged until you press Save. Invalid values are rejected with an inline error.')

    def _tw_editor_raw_value(self):
        doc = self._tw_current_doc()
        setting = doc.match(key=self.tw_edit_ref) if doc is not None else None
        if self.tw_edit_kind == 'combo':
            return self.tw_edit_combo.currentText()
        if self.tw_edit_kind == 'int':
            return str(self.tw_edit_int.value())
        if self.tw_edit_kind == 'double':
            decimals = setting.decimals if setting is not None and setting.decimals is not None else 3
            return '%.*f' % (decimals, self.tw_edit_double.value())
        if self.tw_edit_kind == 'slider':
            factor = self._tw_slider_factor()
            decimals = setting.decimals if setting is not None and setting.decimals is not None else 0
            return '%.*f' % (decimals, self.tw_edit_slider.value() / factor)
        return self.tw_edit_line.text()

    def _tw_row_value_widget(self, setting, editable):
        if setting.kind == 'bool' or (setting.kind == 'choice' and setting.options):
            combo = QComboBox()
            combo.addItems(['true', 'false'] if setting.kind == 'bool' else list(setting.options))
            combo.setCurrentText(setting.effective)
            combo.setEnabled(editable)
            combo.activated.connect(lambda _i, k=setting.key, c=combo: self.stage_tweaker_row(k, c.currentText()))
            return combo
        if setting.kind == 'number' and setting.min is not None and setting.max is not None and (setting.max - setting.min) <= 10000:
            factor = 10.0 ** (setting.decimals or 0)
            decimals = setting.decimals if setting.decimals is not None else 0
            wrap = QWidget()
            box = QHBoxLayout(wrap)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(6)
            slider = QSlider(Qt.Horizontal)
            slider.setRange(int(round(setting.min * factor)), int(round(setting.max * factor)))
            try:
                start = int(round(float(setting.effective) * factor))
            except (TypeError, ValueError):
                start = int(round(setting.min * factor))
            slider.setValue(start)
            slider.setEnabled(editable)
            edit = QLineEdit()
            edit.setMinimumWidth(80)
            edit.setText('%.*f' % (decimals, slider.value() / factor))
            edit.setEnabled(editable)
            slider.valueChanged.connect(lambda v, e=edit, f=factor, d=decimals: e.setText('%.*f' % (d, v / f)))
            slider.sliderReleased.connect(lambda k=setting.key, e=edit: self.stage_tweaker_row(k, e.text()))
            edit.editingFinished.connect(lambda k=setting.key, e=edit: self.stage_tweaker_row(k, e.text()))
            box.addWidget(slider, 1)
            box.addWidget(edit)
            return wrap
        edit = QLineEdit()
        edit.setText(setting.effective)
        edit.setEnabled(editable)
        edit.editingFinished.connect(lambda k=setting.key, e=edit: self.stage_tweaker_row(k, e.text()))
        return edit
    def stage_tweaker_row(self, key, raw_value):
        doc = self._tw_current_doc()
        self.tw_error.setText('')
        if doc is None:
            return
        if self.tw_current not in self.tw_unlocked:
            self.tw_error.setText('Unlock editing for this file first.')
            self.refresh_tweaker_tables()
            return
        ok, normalized, error = doc.stage(key, raw_value)
        if not ok:
            self.tw_error.setText('Invalid value: ' + error)
        self.refresh_tweaker_tables()
        self.refresh_tweaker_diff()
    def toggle_tweaker_pin(self, key):
        if not key:
            return
        self.tw_pins.toggle(key)
        self.refresh_tweaker_tables()
    def stage_tweaker_edit(self):
        doc = self._tw_current_doc()
        self.tw_error.setText('')
        if doc is None or self.tw_edit_ref is None:
            self.tw_error.setText('Select a setting first.')
            return
        if self.tw_current not in self.tw_unlocked:
            self.tw_error.setText('Unlock editing for this file first.')
            return
        ok, normalized, error = doc.stage(self.tw_edit_ref, self._tw_editor_raw_value())
        if not ok:
            self.tw_error.setText('Invalid value: ' + error)
            return
        self.refresh_tweaker_tables()
        self.refresh_tweaker_diff()

    def pin_tweaker_edit(self):
        if not self.tw_edit_ref:
            return
        self.tw_pin_btn.setText('Unpin' if self.tw_pins.toggle(self.tw_edit_ref) else 'Pin')
        self.refresh_tweaker_tables()

    def revert_tweaker_original(self):
        doc = self._tw_current_doc()
        if doc is not None and self.tw_edit_ref and doc.revert_to_original(self.tw_edit_ref):
            self.tw_error.setText('')
            self.refresh_tweaker_tables()
            self.refresh_tweaker_diff()

    def revert_tweaker_disk(self):
        doc = self._tw_current_doc()
        if doc is None or not self.tw_edit_ref:
            return
        ok, message = doc.revert_to_disk(self.tw_edit_ref)
        if not ok:
            self.tw_error.setText('Could not revert to disk: ' + str(message))
            return
        self.tw_error.setText('')
        self.refresh_tweaker_tables()
        self.refresh_tweaker_diff()

    def save_tweaker_edits(self):
        CT = self._twmod
        doc = self._tw_current_doc()
        if doc is None:
            self.notice('No config file is loaded.')
            return
        if not doc.staged_changes():
            self.notice('There are no staged edits to save.')
            return
        if self.tw_current not in self.tw_unlocked:
            self.notice('Unlock editing for this file first.')
            return
        if CT.is_game_running() and not self.ask('Call of Duty looks running. The game may overwrite this file at exit. Save anyway?'):
            return
        if doc.is_stale_on_disk():
            self.notice('The file changed on disk. Use Reload (keep my staged edits) or Show what changed first.')
            return
        try:
            backup = self.tw_backups.backup_file(doc.path)
        except OSError as exc:
            self.notice('Backup before write failed, nothing was saved: ' + str(exc))
            return
        try:
            doc.save()
        except OSError as exc:
            self.notice('Saving failed after backup (%s): %s' % (backup, exc))
            return
        self.tw_restart_needed = True
        self.tw_restart_label.setVisible(True)
        self.refresh_tweaker_tables()
        self.refresh_tweaker_backups()
        self.refresh_tweaker_compare_sources()
        self.refresh_tweaker_diff()
        self.log('COD Tweaker saved %s (backup %s). Restart the game to apply.' % (doc.path, backup))

    def tweaker_reload_keep(self):
        doc = self._tw_current_doc()
        if doc is None:
            return
        try:
            doc.refresh_keep_pending()
        except (OSError, ValueError) as exc:
            self.notice('Reload failed: ' + str(exc))
            return
        self.refresh_tweaker_tables()
        self.refresh_tweaker_diff()
        self.poll_tweaker_guards()
        self.log('COD Tweaker reloaded %s from disk; staged edits were kept.' % doc.path)

    def tweaker_show_disk_diff(self):
        self.tw_tabs.setCurrentIndex(4)
        self.refresh_tweaker_compare_sources()
        for index in range(self.tw_cmp_source.count()):
            if str(self.tw_cmp_source.itemData(index) or '').startswith('disk:'):
                self.tw_cmp_source.setCurrentIndex(index)
                break
        self.refresh_tweaker_diff()

    def poll_tweaker_guards(self):
        if not hasattr(self, 'tw_game_banner'):
            return
        try:
            match = self._twmod.matching_game_process()
            running = bool(match)
        except Exception:
            match = ''
            running = False
        try:
            self.tw_game_banner.setVisible(bool(running))
            self.tw_game_banner.setToolTip('Detected process: ' + match if match else 'No game process detected')
            doc = self._tw_current_doc()
            stale = bool(doc is not None and doc.is_stale_on_disk())
            self.tw_stale_row.setVisible(stale)
        except RuntimeError:
            pass

    # ---- presets ----
    def _tw_preset_payloads(self):
        try:
            data = json.loads((E.ROOT / 'cod_tweaker_presets.json').read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            return []
        presets = data.get('Presets', []) if isinstance(data, dict) else []
        return [item for item in presets if isinstance(item, dict) and isinstance(item.get('Values'), list)]

    def refresh_tweaker_presets(self):
        combo = self.tw_preset_combo
        combo.blockSignals(True)
        combo.clear()
        for payload in self._tw_preset_payloads():
            combo.addItem(str(payload.get('Title') or payload.get('Kind') or 'Preset'))
        combo.blockSignals(False)
        self.preview_tweaker_preset()

    def _tw_active_preset(self):
        payloads = self._tw_preset_payloads()
        if not payloads or self.tw_preset_combo.currentIndex() < 0:
            return None
        return self._twmod.Preset(payloads[self.tw_preset_combo.currentIndex() % len(payloads)])

    def preview_tweaker_preset(self):
        if not hasattr(self, 'tw_preset_table'):
            return
        doc = self._tw_current_doc()
        preset = self._tw_active_preset()
        self.tw_preset_table.setRowCount(0)
        if doc is None or preset is None:
            self.tw_preset_summary.setText('Load a config file to preview a preset against it.')
            self.tw_preset_pairs = []
            return
        self.tw_preset_pairs = preset.match_against(doc)
        plan = preset.dry_run(doc)
        stage_count = sum(1 for item in plan if item['action'] == 'stage')
        skip_count = sum(1 for item in plan if item['action'] == 'skip')
        self.tw_preset_summary.setText('%s: %d change(s) would be staged, %d skipped (protected, invalid, or already set). Nothing is staged until you confirm.' % (
            preset.title, stage_count, skip_count))
        for item in plan:
            row = self.tw_preset_table.rowCount()
            self.tw_preset_table.insertRow(row)
            if item['action'] == 'stage':
                detail = '%s -> %s' % (item.get('from', ''), item.get('to', ''))
            else:
                detail = item.get('reason', '')
            self.tw_preset_table.setItem(row, 0, QTableWidgetItem(item.get('key', '')))
            self.tw_preset_table.setItem(row, 1, QTableWidgetItem(item['action']))
            self.tw_preset_table.setItem(row, 2, QTableWidgetItem(detail))

    def apply_tweaker_preset(self):
        CT = self._twmod
        doc = self._tw_current_doc()
        preset = self._tw_active_preset()
        if doc is None or preset is None:
            self.notice('Load a config file and select a preset first.')
            return
        if self.tw_current not in self.tw_unlocked:
            self.notice('Unlock editing for this file first.')
            return
        plan = preset.dry_run(doc)
        staged_plan = [item for item in plan if item['action'] == 'stage']
        if not staged_plan:
            self.notice('The preset would change nothing (protected, invalid, or already set).')
            return
        if CT.is_game_running() and not self.ask('Call of Duty looks running. Apply the preset anyway?'):
            return
        if doc.is_stale_on_disk():
            self.notice('The file changed on disk. Use Reload (keep my staged edits) or Show what changed first.')
            return
        preview = '\n'.join('%s: %s -> %s' % (item['key'], item.get('from', ''), item.get('to', '')) for item in staged_plan[:12])
        if len(staged_plan) > 12:
            preview += '\n... and %d more' % (len(staged_plan) - 12)
        if not self.ask('Apply preset "%s" (%d change(s))?\n\n%s\n\nA dated backup is taken first.' % (preset.title, len(staged_plan), preview)):
            return
        try:
            backup = self.tw_backups.backup_file(doc.path)
        except OSError as exc:
            self.notice('Backup before write failed, nothing was applied: ' + str(exc))
            return
        staged, skipped = preset.confirm(doc, self.tw_preset_pairs)
        try:
            doc.save()
        except OSError as exc:
            self.notice('Saving failed after backup (%s): %s' % (backup, exc))
            return
        self.tw_restart_needed = True
        self.tw_restart_label.setVisible(True)
        self.refresh_tweaker_tables()
        self.refresh_tweaker_backups()
        self.refresh_tweaker_compare_sources()
        self.refresh_tweaker_diff()
        self.log('COD Tweaker applied preset "%s": %d staged, %d skipped (backup %s).' % (preset.title, len(staged), len(skipped), backup))

    # ---- compare / backup & restore ----
    def refresh_tweaker_compare_sources(self):
        if not hasattr(self, 'tw_cmp_source'):
            return
        combo = self.tw_cmp_source
        combo.blockSignals(True)
        combo.clear()
        combo.addItem('On-disk file (reread)', 'disk:')
        for payload in self._tw_preset_payloads():
            combo.addItem('Preset: %s' % (payload.get('Title') or payload.get('Kind')), 'preset:%s' % (payload.get('Title') or payload.get('Kind')))
        doc = self._tw_current_doc()
        if doc is not None:
            for backup in self.tw_backups.list_backups(basename=doc.path.name):
                combo.addItem('Backup: %s' % backup.name, 'backup:%s' % backup.name)
        combo.blockSignals(False)

    def _tw_theirs_values(self):
        CT = self._twmod
        doc = self._tw_current_doc()
        key = str(self.tw_cmp_source.currentData() or 'disk:')
        if doc is None:
            return {}
        if key.startswith('preset:'):
            title = key[len('preset:'):]
            payload = next((item for item in self._tw_preset_payloads() if str(item.get('Title') or item.get('Kind')) == title), None)
            if payload is None:
                return {}
            theirs = {}
            for entry in payload.get('Values', []):
                setting = doc.match(key=str(entry.get('Key') or '') or None,
                                    name=str(entry.get('Name') or '') or None,
                                    variant=str(entry.get('Variant') or ''))
                ref = setting.key if setting is not None else str(entry.get('Key') or entry.get('Name') or '')
                if ref:
                    theirs[ref] = str(entry.get('Value', ''))
            return theirs
        if key.startswith('backup:'):
            name = key[len('backup:'):]
            backups = [item for item in self.tw_backups.list_backups(basename=doc.path.name) if item.name == name]
            if not backups:
                return {}
            try:
                raw = backups[0].read_bytes()
            except OSError:
                return {}
            if len(raw) > CT.MAX_FILE_BYTES:
                return {}
            return {item.key: item.original for item in CT.parse_text(raw.decode('utf-8-sig', errors='replace'))}
        try:
            fresh = CT.CODDocument.load(doc.path)
        except (OSError, ValueError):
            return {}
        return {item.key: item.original for item in fresh.settings}

    def refresh_tweaker_diff(self):
        if not hasattr(self, 'tw_cmp_table'):
            return
        CT = self._twmod
        doc = self._tw_current_doc()
        self.tw_cmp_table.setRowCount(0)
        if doc is None:
            self.tw_cmp_summary.setText('Load a config file to compare it.')
            self.tw_diff = None
            return
        mine = {item.key: item.effective for item in doc.settings}
        theirs = self._tw_theirs_values()
        self.tw_diff = CT.Diff.build(mine, theirs)
        pinned_only = self.tw_cmp_pinned_only.isChecked()
        for row_data in self.tw_diff.rows:
            if pinned_only and not self.tw_pins.is_pinned(row_data['ref']):
                continue
            row = self.tw_cmp_table.rowCount()
            self.tw_cmp_table.insertRow(row)
            ref_item = QTableWidgetItem(row_data['ref'])
            ref_item.setData(Qt.UserRole, row_data['ref'])
            self.tw_cmp_table.setItem(row, 0, ref_item)
            self.tw_cmp_table.setItem(row, 1, QTableWidgetItem(row_data['mine']))
            self.tw_cmp_table.setItem(row, 2, QTableWidgetItem(row_data['theirs']))
            self.tw_cmp_table.setItem(row, 3, QTableWidgetItem(row_data['status']))
        summary = self.tw_diff.summary()
        self.tw_cmp_summary.setText('%d matching, %d different, %d only in backup/preset/disk, %d only in mine.' % (
            summary.get('matching', 0), summary.get('different', 0),
            summary.get('only-in-other', 0), summary.get('only-in-mine', 0)))

    def take_tweaker_diff_row(self):
        doc = self._tw_current_doc()
        if doc is None or self.tw_diff is None or self.tw_cmp_table.currentRow() < 0:
            self.tw_error.setText('Select a diff row to take.')
            return
        ref_item = self.tw_cmp_table.item(self.tw_cmp_table.currentRow(), 0)
        if ref_item is None:
            return
        if self.tw_current not in self.tw_unlocked:
            self.tw_error.setText('Unlock editing for this file first.')
            return
        ok, message = self.tw_diff.take(doc, ref_item.data(Qt.UserRole))
        if not ok:
            self.tw_error.setText('Could not take value: ' + str(message))
            return
        self.tw_error.setText('')
        self.refresh_tweaker_tables()
        self.refresh_tweaker_diff()

    def restore_tweaker_pinned(self):
        CT = self._twmod
        doc = self._tw_current_doc()
        if doc is None or self.tw_diff is None:
            self.notice('Load a config file and build a diff first.')
            return
        if self.tw_current not in self.tw_unlocked:
            self.notice('Unlock editing for this file first.')
            return
        pinned_rows = [row for row in self.tw_diff.rows if self.tw_pins.is_pinned(row['ref']) and row['theirs'] != '' and row['mine'] != row['theirs']]
        if not pinned_rows:
            self.notice('No pinned rows differ from this compare source.')
            return
        if not self.ask('Restore %d pinned setting(s) from this compare source? A safety backup is taken first.' % len(pinned_rows)):
            return
        if doc.is_stale_on_disk():
            self.notice('The file changed on disk. Use Reload (keep my staged edits) or Show what changed first.')
            return
        try:
            backup = self.tw_backups.backup_file(doc.path)
        except OSError as exc:
            self.notice('Backup before write failed, nothing was restored: ' + str(exc))
            return
        failures = []
        for row in pinned_rows:
            ok, message = self.tw_diff.take(doc, row['ref'])
            if not ok:
                failures.append('%s: %s' % (row['ref'], message))
        try:
            doc.save()
        except OSError as exc:
            self.notice('Saving failed after backup (%s): %s' % (backup, exc))
            return
        if self.tw_cmp_relock.isChecked():
            try:
                CT.set_locked(doc.path, True)
            except OSError as exc:
                self.log('COD Tweaker relock failed: ' + str(exc))
        self.tw_restart_needed = True
        self.tw_restart_label.setVisible(True)
        self.update_tweaker_lock_button()
        self.refresh_tweaker_tables()
        self.refresh_tweaker_backups()
        self.refresh_tweaker_diff()
        if failures:
            self.notice('Restored with %d skipped row(s):\n%s' % (len(failures), '\n'.join(failures[:8])))
        self.log('COD Tweaker restored %d pinned row(s) (backup %s).' % (len(pinned_rows) - len(failures), backup))

    def update_tweaker_lock_button(self):
        if not hasattr(self, 'tw_lock_btn'):
            return
        doc = self._tw_current_doc()
        if doc is None:
            self.tw_lock_btn.setText('Lock file (read-only)')
            return
        self.tw_lock_btn.setText('Unlock file (writable)' if self._twmod.is_locked(doc.path) else 'Lock file (read-only)')

    def toggle_tweaker_lock(self):
        CT = self._twmod
        doc = self._tw_current_doc()
        if doc is None:
            self.notice('Load a config file first.')
            return
        try:
            CT.set_locked(doc.path, not CT.is_locked(doc.path))
        except OSError as exc:
            self.notice('Could not change the file lock: ' + str(exc))
            return
        self.update_tweaker_lock_button()
        self.log('COD Tweaker file lock: %s is now %s.' % (doc.path.name, 'locked' if CT.is_locked(doc.path) else 'writable'))

    def refresh_tweaker_backups(self):
        if not hasattr(self, 'tw_backup_table'):
            return
        import time as _time
        self.tw_backup_table.setRowCount(0)
        doc = self._tw_current_doc()
        for backup in self.tw_backups.list_backups(basename=doc.path.name if doc is not None else None):
            try:
                stamp = _time.strftime('%Y-%m-%d %H:%M:%S', _time.localtime(backup.stat().st_mtime))
                size = str(backup.stat().st_size)
            except OSError:
                continue
            row = self.tw_backup_table.rowCount()
            self.tw_backup_table.insertRow(row)
            when_item = QTableWidgetItem(stamp)
            when_item.setData(Qt.UserRole, str(backup))
            self.tw_backup_table.setItem(row, 0, when_item)
            self.tw_backup_table.setItem(row, 1, QTableWidgetItem(backup.name))
            self.tw_backup_table.setItem(row, 2, QTableWidgetItem(size))

    def _tw_selected_backup(self):
        if self.tw_backup_table.currentRow() < 0:
            return None
        item = self.tw_backup_table.item(self.tw_backup_table.currentRow(), 0)
        return Path(item.data(Qt.UserRole)) if item is not None else None

    def restore_tweaker_backup(self):
        doc = self._tw_current_doc()
        backup = self._tw_selected_backup()
        if doc is None or backup is None:
            self.notice('Select a backup to restore.')
            return
        if not self.ask('Restore %s over %s? A fresh safety backup is taken first, and the file lock is preserved.' % (backup.name, doc.path.name)):
            return
        try:
            safety = self.tw_backups.restore(backup, doc.path)
        except (OSError, FileNotFoundError) as exc:
            self.notice('Restore failed, the live file was not touched: ' + str(exc))
            return
        try:
            self.tw_docs[self.tw_current] = self._twmod.CODDocument.load(doc.path)
        except (OSError, ValueError) as exc:
            self.notice('Restored, but the result could not be reparsed: ' + str(exc))
            return
        self.tw_restart_needed = True
        self.tw_restart_label.setVisible(True)
        self.refresh_tweaker_tables()
        self.refresh_tweaker_backups()
        self.refresh_tweaker_compare_sources()
        self.refresh_tweaker_diff()
        self.poll_tweaker_guards()
        self.log('COD Tweaker restored %s over %s (safety backup %s).' % (backup.name, doc.path.name, safety))

    def compare_tweaker_backup(self):
        backup = self._tw_selected_backup()
        if backup is None:
            self.notice('Select a backup to compare.')
            return
        self.tw_tabs.setCurrentIndex(4)
        self.refresh_tweaker_compare_sources()
        for index in range(self.tw_cmp_source.count()):
            if self.tw_cmp_source.itemData(index) == 'backup:%s' % backup.name:
                self.tw_cmp_source.setCurrentIndex(index)
                break
        self.refresh_tweaker_diff()

    def reconnect_extension_bridge(self):
        old=getattr(self,'extension_bridge',None)
        if old:
            try:old.close()
            except Exception:pass
            self.extension_bridge=None
        self.extension_bridge_status.setText('Reconnecting…');self.refresh_dashboard_status()
        if self.start_extension_bridge():
            self.cinematic_status.setText('Bridge reconnected. Queued screenshots can now be sent to Meta AI.')
    def start_extension_bridge(self):
        try:
            from local_bridge import LocalBridge
            self.extension_bridge=LocalBridge(self.store,E.DATA,self.settings.extension_token(),videos_dir=self.videos_dir(),
                solo_player_name=self.settings.values.get('player',''),
                on_artwork=self.bridge.image.emit,on_prepare=self.open_prepared_photos,
                on_log=self.bridge.status.emit,
                port=0 if os.environ.get('VINTENDO_BRIDGE_SMOKE_TEST')=='1' else 8765)
            port=self.extension_bridge.start();self.extension_bridge_status.setText(f'CONNECTED LOCALLY  /  127.0.0.1:{port}');self.refresh_dashboard_status()
            return True
        except Exception as exc:
            self.extension_bridge=None;self.extension_bridge_status.setText('DISCONNECTED  /  '+str(exc)[:180]);self.refresh_dashboard_status();self.log('Chrome extension bridge failed to start: '+str(exc));return False

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
        if kind=='scoreboard':vk,mod=self.hotkey_setting('scoreboard_hotkey_vk','scoreboard_hotkey_mod',DEFAULT_SCOREBOARD_HOTKEY)
        else:vk,mod=self.hotkey_setting('screenshot_hotkey_vk','screenshot_hotkey_mod',DEFAULT_SCREENSHOT_HOTKEY)
        return format_hotkey(vk,mod)
    def refresh_hotkey_labels(self):
        score=self.hotkey_text('scoreboard');shot=self.hotkey_text('screenshot')
        if hasattr(self,'hotkey_hint'):self.hotkey_hint.setText(f'At match end, press {score} while the scoreboard is visible.')
        if hasattr(self,'capture_subtitle'):self.capture_subtitle.setText(f'Set the scoreboard region once. At match end, press {score} while the final scoreboard is visible; OCR prepares a draft for review.')
        if hasattr(self,'cod_ai_capture_status') and not getattr(self,'screenshot_busy',False):self.cod_ai_capture_status.setText(f'Screenshots will be saved as lossless PNG files in your screenshots folder. Press {shot} in game to capture.')
    def register_global_hotkeys(self):

        self.hotkey_filter=None;self.hotkey_registered=False;self.screenshot_hotkey_registered=False
        if os.name!='nt':
            self.hotkey_status.setText('Global hotkey unavailable on this platform; use Capture & OCR.')
            self.cod_ai_hotkey_status.setText('GLOBAL HOTKEY UNAVAILABLE · use Capture game screenshot now')
            return
        try:
            callbacks={COD_AI_SCREENSHOT_HOTKEY_ID:self.capture_cod_ai_screenshot}
            if not self.tab_prototype:callbacks[SCOREBOARD_HOTKEY_ID]=self.capture_scoreboard_hotkey
            self.hotkey_filter=ScoreboardHotkeyFilter(callbacks)
            QApplication.instance().installNativeEventFilter(self.hotkey_filter)
            score_vk,score_mod=self.hotkey_setting('scoreboard_hotkey_vk','scoreboard_hotkey_mod',DEFAULT_SCOREBOARD_HOTKEY)
            shot_vk,shot_mod=self.hotkey_setting('screenshot_hotkey_vk','screenshot_hotkey_mod',DEFAULT_SCREENSHOT_HOTKEY)
            score_text=format_hotkey(score_vk,score_mod);shot_text=format_hotkey(shot_vk,shot_mod)
            self.hotkey_registered=False;scoreboard_error=0
            if not self.tab_prototype:self.hotkey_registered=bool(ctypes.windll.user32.RegisterHotKey(None,SCOREBOARD_HOTKEY_ID,score_mod|MOD_NOREPEAT,score_vk))
            elif hasattr(self,'hotkey_status'):
                self.hotkey_status.setText('Scoreboard OCR hotkey disabled in CODMunity tab preview mode.')
                self.hotkey_status.setStyleSheet('color:#aab4c3')
            self.screenshot_hotkey_registered=bool(ctypes.windll.user32.RegisterHotKey(None,COD_AI_SCREENSHOT_HOTKEY_ID,shot_mod|MOD_NOREPEAT,shot_vk))
            screenshot_error=0 if self.screenshot_hotkey_registered else ctypes.windll.kernel32.GetLastError()
            if self.hotkey_registered:
                self.hotkey_status.setText('GLOBAL HOTKEY ACTIVE  '+chr(0xB7)+'  '+score_text);self.hotkey_status.setStyleSheet('color:#56e99a;font-weight:800;')
            elif self.tab_prototype:
                self.hotkey_status.setText('Scoreboard OCR hotkey disabled in CODMunity tab preview mode.');self.hotkey_status.setStyleSheet('color:#aab4c3')
            else:
                reason='key combination is already in use' if scoreboard_error==1409 else f'Windows error {scoreboard_error}'
                self.hotkey_status.setText('HOTKEY UNAVAILABLE  ·  '+reason+'  ·  use Capture & OCR');self.hotkey_status.setStyleSheet('color:#ffb15e;font-weight:800;')
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
            self.hotkey_filter=None;self.hotkey_status.setText('HOTKEY UNAVAILABLE  ·  use Capture & OCR')
            self.hotkey_status.setToolTip(str(exc));self.hotkey_status.setStyleSheet('color:#ffb15e;font-weight:800;')
            self.cod_ai_hotkey_status.setText('HOTKEY UNAVAILABLE  ·  '+str(exc));self.cod_ai_hotkey_status.setStyleSheet('color:#ffb15e;font-weight:800;')

    def capture_scoreboard_hotkey(self):
        if self.mode.currentText()!='Squad Night':
            message='Switch to Squad Night or combined tracking before using scoreboard capture.'
            self.hotkey_status.setText(message);self.log(message)
            if self.tray.isVisible():self.tray.showMessage('Scoreboard capture',message)
            return
        self.log('Ctrl + Shift + F8 pressed. Capturing the visible scoreboard…')
        self.capture_once(hotkey=True)

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
        while box.count():
            child=box.takeAt(0);widget=child.widget()
            if widget is not None:widget.deleteLater()
        eligible=[]
        for player in self.store.roster():
            if not player.get('enabled',True):continue
            if not player.get('consent_meta',False):continue
            photo=str(player.get('photo') or '')
            if not photo or not Path(photo).is_file():continue
            eligible.append(player)
        valid={str(player.get('id')) for player in eligible}
        self.talent_friend_ids=[pid for pid in (getattr(self,'talent_friend_ids',[]) or []) if pid in valid][:2]
        chosen=set(self.talent_friend_ids)
        if not eligible and hasattr(self,'talent_friends_status'):self.talent_friends_status.setText('No eligible friends — roster players need a headshot and Meta AI consent.')
        for position,player in enumerate(eligible):
            pid=str(player.get('id'));name=str(player.get('display_name') or player.get('name',''))
            chip=QPushButton(name);chip.setCheckable(True);chip.setChecked(pid in chosen);chip.setProperty('talentPid',pid);chip.setObjectName('friendChip')
            chip.toggled.connect(lambda _checked=False,pid=pid:self.on_talent_friend_toggled(pid))
            box.addWidget(chip,position//3,position%3)
        self.update_talent_friends_status()
    def on_talent_friend_toggled(self,pid):
        box=getattr(self,'talent_friends_box',None);states={}
        if box is not None:
            for index in range(box.count()):
                widget=box.itemAt(index).widget()
                if widget is not None and widget.property('talentPid'):states[str(widget.property('talentPid'))]=widget.isChecked()
        chosen=[pid for pid in (getattr(self,'talent_friend_ids',[]) or [])]
        if states.get(pid) and pid not in chosen:chosen.append(pid)
        if not states.get(pid) and pid in chosen:chosen.remove(pid)
        if len(chosen)>2:
            chosen=chosen[:2]
            if box is not None:
                for index in range(box.count()):
                    widget=box.itemAt(index).widget()
                    if widget is not None and str(widget.property('talentPid') or '') not in chosen:widget.blockSignals(True);widget.setChecked(False);widget.blockSignals(False)
            if hasattr(self,'cinematic_status'):self.cinematic_status.setText('Max of 3 people per video (you + up to 2 friends).')
        self.talent_friend_ids=chosen;self.update_talent_friends_status()
    def update_talent_friends_status(self):
        if not hasattr(self,'talent_friends_status'):return
        count=len(getattr(self,'talent_friend_ids',[]) or [])
        self.talent_friends_status.setText((('No friends — just you' if not count else str(count)+'/2 friends selected')+' (Max Of 3 Can be used)'))
    def selected_talent_friends(self):
        wanted=set(getattr(self,'talent_friend_ids',[]) or [])
        friends=[]
        for player in self.store.roster():
            if str(player.get('id')) in wanted and player.get('enabled',True) and player.get('consent_meta',False):
                photo=str(player.get('photo') or '')
                if photo and Path(photo).is_file():friends.append(player)
        return friends[:2]
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
                    subprocess.run([exe,'-y','-v','error','-ss','1','-i',video,'-frames:v','1','-vf','scale=336:-2',thumb],timeout=120)
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
        shots=len(getattr(self,'_shot_paths',[]) or []);videos=len(getattr(self,'_video_rows',[]) or [])
        if hasattr(self,'media_count'):self.media_count.setText(f"{shots} screenshot{'s' if shots!=1 else ''} • {videos} video{'s' if videos!=1 else ''}")
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
        if hasattr(self,'media_tabs'):self.media_tabs.setTabText(1,f'Videos ({len(rows)})')
        self.ensure_cinematic_thumbs()
        selected=self.cinematic_results.currentItem();selected_video=selected.data(Qt.UserRole+1) if selected else ''
        pending=getattr(self,'cinematic_auto_post_pending',False)
        self.post_cinematic_button.setEnabled(bool(selected_video and Path(selected_video).is_file() and not pending))
        if hasattr(self,'cinematic_play_button'):self.cinematic_play_button.setEnabled(bool(selected_video and Path(selected_video).is_file()))
        if hasattr(self,'cinematic_fullscreen_button'):self.cinematic_fullscreen_button.setEnabled(bool(selected_video and Path(selected_video).is_file()))
        if selected_video and selected_video!=previous_video and Path(selected_video).is_file():
            self.cinematic_player.setSource(QUrl.fromLocalFile(selected_video));self.cinematic_player.play();self.cinematic_play_button.setText('Pause')
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
        has_video=bool(path and Path(path).is_file())
        self.post_cinematic_button.setEnabled(has_video)
        if hasattr(self,'cinematic_play_button'):self.cinematic_play_button.setEnabled(has_video)
        if hasattr(self,'cinematic_fullscreen_button'):self.cinematic_fullscreen_button.setEnabled(has_video)
        self._sync_cinematic_tiles()
    def test_cinematic_video(self):
        source,_=QFileDialog.getOpenFileName(self,'Choose a COD gameplay screenshot',str(self.screenshots_dir()),'Images (*.png *.jpg *.jpeg *.webp)')
        if source:self.queue_cinematic_source(source)
    def poll_inapp_cinematics(self):
        if self.meta_submission()!='inapp':return
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
        try:self.store.update_cinematic(job_id,status='awaiting_generation')
        except Exception:pass
        thread=threading.Thread(target=self._run_inapp_cinematic,args=(job_id,),daemon=True);thread.start()
    def _run_inapp_cinematic(self,job_id):
        try:row=self.store.get_cinematic(job_id)
        except Exception:row=None
        if not row or row.get('status') not in ('running','awaiting_generation'):
            self._inapp_active=None;return
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
                context=meta_automation.ensure_context(playwright,E.DATA/'TrackerChromeInApp')
                driver=meta_automation.MetaDriver(context,E.DATA/'meta-diagnostics')
                page=driver.new_job_page()
                try:video=driver.submit_image_to_video(page,screenshot,faces,prompt,str(self.videos_dir()))
                finally:
                    try:page.close()
                    except Exception:pass
                    try:context.close()
                    except Exception:pass
        except Exception as exc:
            detail=str(getattr(exc,'detail',None) or exc)
            step=str(getattr(exc,'step','error'))
            if 'login' in step.lower():message='The automation browser opened signed out. Log into Meta AI in that window, then requeue the video.'
            else:message=(f'Meta automation stopped at {step}: {detail}')[:300]
            self._fail_inapp_cinematic(job_id,message);return
        try:self.store.update_cinematic(job_id,status='completed',video=str(video),error='')
        except Exception:pass
        try:self.on_log('In-app cinematic completed: '+job_id)
        except Exception:pass
        self._inapp_active=None
        QTimer.singleShot(0,self.refresh_cinematic_results)
    def _fail_inapp_cinematic(self,job_id,message):
        try:self.store.update_cinematic(job_id,status='failed',error=str(message)[:300])
        except Exception:pass
        try:self.on_log('In-app cinematic failed: '+job_id+' — '+str(message)[:160])
        except Exception:pass
        self._inapp_active=None
        QTimer.singleShot(0,self.refresh_cinematic_results)
    def queue_selected_cinematic(self):
        item=self.cod_ai_screenshots.currentItem()
        if not item:return
        source=item.data(Qt.UserRole)
        if source:self.queue_cinematic_source(source)
    def queue_cinematic_source(self,source):
        if not getattr(self,'extension_bridge',None) or not self.extension_bridge.running:
            self.cinematic_status.setText('Chrome bridge is disconnected. Open Settings and press Reconnect bridge, then pair Vintendo COD Automation.');return
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
            self.cinematic_status.setText(f'Queued {cid}. The screenshot is stored locally; the extension will open Frame to Video and submit it. Discord posting is manual.'+(' Featuring '+', '.join(cast_names)+'.' if cast_names else ''))
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
        if hasattr(self,'media_tabs'):self.media_tabs.setTabText(0,f'Screenshots ({len(paths)})')
        self.cod_ai_screenshots.setVisible(bool(paths));self.cod_ai_empty.setVisible(not paths)
        if self.cod_ai_screenshots.count():
            if not self.cod_ai_screenshots.currentItem():self.cod_ai_screenshots.setCurrentRow(0)
        else:
            self.cod_ai_preview.clear();self.cod_ai_preview.setText('No screenshots yet');self.cod_ai_preview_info.setText('Select a screenshot to preview')
        self.maybe_offer_screenshot_cleanup()
    def ask_screenshot_cleanup(self,used_count,total_count):
        box=QMessageBox(self);box.setWindowTitle('Many screenshots');box.setText('You seem to have a bunch of screenshots. Would you like to delete the ones you have already used in COD AI videos?');box.setInformativeText(f'{used_count} used of {total_count} screenshots would be deleted. Queue entries stay for history.');yes_button=box.addButton('Yes',QMessageBox.YesRole);box.addButton('No',QMessageBox.NoRole);box.setDefaultButton(yes_button);box.exec();return box.clickedButton() is yes_button
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
            self.cod_ai_preview.clear();self.cod_ai_preview.setText('Could not read screenshot');self.cod_ai_preview_info.setText(item.toolTip() or Path(str(path)).name);return
        self.cod_ai_preview.setPixmap(pix.scaled(self.cod_ai_preview.size(),Qt.KeepAspectRatio,Qt.SmoothTransformation))
        self.cod_ai_preview_info.setText(f'{Path(path).name}  ·  {pix.width()}×{pix.height()}')
        self._sync_screenshot_tiles()
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
            if self.tray.isVisible():self.tray.showMessage('COD AI screenshot saved',name,QSystemTrayIcon.Information,2500)
        def failed(message):
            self.screenshot_busy=False;detail='Screenshot not saved: '+str(message)
            self.cod_ai_capture_status.setText(detail);self.cod_ai_capture_status.setStyleSheet('color:#ffb15e;font-weight:800;');self.log(detail)
            if self.tray.isVisible():self.tray.showMessage('COD AI screenshot failed',str(message)[:240],QSystemTrayIcon.Warning,3500)
        self.async_task(work,done,failed)
    def open_prepared_photos(self,mid,folder):
        try:os.startfile(str(folder));self.bridge.status.emit('Consent-filtered reference photos staged for '+mid+'.')
        except Exception as exc:self.bridge.status.emit('Photos staged; Explorer could not open the folder: '+str(exc))
    def copy_extension_token(self):
        try:
            if not getattr(self,'extension_bridge',None):
                if not self.start_extension_bridge():return
            QApplication.clipboard().setText(self.settings.extension_token())
            self.notice('Chrome pairing token copied. Paste it in Vintendo COD Automation Options, enable automatic handoff, and save.')
        except Exception as exc:self.notice('Could not create or copy the pairing token: '+str(exc))
    def rotate_extension_token(self):
        if not self.ask('Rotate the local Chrome pairing token? The extension will disconnect until paired again.'):return
        try:
            if getattr(self,'extension_bridge',None):self.extension_bridge.close()
            self.settings.rotate_extension_token();self.extension_bridge=None
            if self.start_extension_bridge():self.copy_extension_token()
        except Exception as exc:self.notice('Could not rotate the pairing token: '+str(exc))
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
        self.settings.values['mode']=mode;self.settings.save();self.status.setText('STANDBY  /  '+self.active_mode_label());self.refresh_dashboard_status()
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
        if not status:return
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
            self.settings.values.update(player=self.player.text().strip() or 'vintendo',stats_dir=self.stats_dir.text().strip(),warzone_enabled=self.warzone.isChecked(),profile_url=self.profile.text().strip(),poll_seconds=self.poll.value(),mode=self.mode.currentText(),auto_detect=self.auto_detect.isChecked(),auto_post=self.auto_post.isChecked(),provider='Meta AI manual handoff',allow_both=self.allow_both.isChecked(),browser_navigation_timeout=self.browser_timeout.value(),browser_download_timeout=self.download_timeout.value()*60,browser_retries=self.browser_retries.value(),tracker_browser_mode=('attach' if self.browser_mode_combo.currentIndex()==1 else 'dedicated'),meta_submission=('extension' if self.meta_submission_combo.currentIndex()==1 else 'inapp'))
            self.settings.values.update(scoreboard_hotkey_vk=self.scoreboard_hotkey_edit.vk,scoreboard_hotkey_mod=self.scoreboard_hotkey_edit.mod,screenshot_hotkey_vk=self.screenshot_hotkey_edit.vk,screenshot_hotkey_mod=self.screenshot_hotkey_edit.mod,screenshots_dir=self.screenshots_dir_edit.text().strip(),videos_dir=self.videos_dir_edit.text().strip(),webhook_channel=self.webhook_channel.text().strip(),cinematic_webhook_channel=self.cinematic_webhook_channel.text().strip());self.settings.set_webhook(self.webhook.text());self.settings.set_cinematic_webhook(self.cinematic_webhook.text());self.settings.save();self.log('Settings saved with DPAPI-protected webhook. Restart tracking to apply integration changes.');self.refresh_dashboard_status()
            self.unregister_global_hotkeys();self.register_global_hotkeys();self.refresh_hotkey_labels()
            if getattr(self,'extension_bridge',None) is not None:self.extension_bridge.videos_dir=self.videos_dir()
            if hasattr(self,'cod_ai_screenshots'):self.refresh_cod_ai_screenshots()
            if hasattr(self,'cod_ai_screenshots'):self.update_screenshot_grid()
            if hasattr(self,'cinematic_results'):self.update_cinematic_grid()

        except Exception as exc:self.notice('Settings could not be saved: '+str(exc))
    def import_previous_settings(self):
        if self.settings.values.get('roi'):return
        old=E.read_json(Path(os.environ.get('LOCALAPPDATA',''))/'Vintendo Scoreboard Capture'/'settings.json',{}) or {}
        for key in ('roi','game'):
            if key in old:self.settings.values[key]=old[key]
        if old.get('protected_webhook') and not self.settings.values.get('protected_webhook'):
            # Same-account DPAPI blob can be imported without logging its plaintext.
            self.settings.values['protected_webhook']=old['protected_webhook']
            try:self.webhook.setText(self.settings.webhook())
            except Exception:pass
        self.settings.save();self.roi_label.setText('Region: '+str(self.settings.values.get('roi') or 'not selected'))
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
    def pick_stats(self):

        path=QFileDialog.getExistingDirectory(self,'Choose folder containing matches.json')
        if path:self.stats_dir.setText(path)
    def test_data(self):
        values={**self.settings.values,'stats_dir':self.stats_dir.text().strip()}
        def done(result):
            directory,records=result;self.data_status.setPlainText(f'{directory}\n\n{len(records)} merged match records\n\n'+json.dumps([{k:v for k,v in m.items() if k in ('mode','modeKey','game','playedAt','result','stats')} for m in records[-5:]],indent=2));self.log('Local CODMunity data read successfully.')
        self.async_task(lambda:E.load_matches(values),done)
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
        self.status.setText('TRACKING  /  '+self.active_mode_label());self.start_button.setText('●   TRACKING');self.start_button.setEnabled(False);self.start_button.setToolTip('Automatic match tracking is active.');self.pause_button.setText('Ⅱ   PAUSE TRACKING');self.pause_button.setEnabled(True);self.stop_button.setEnabled(True);self.log('Tracking started.');self.last_poll=0;self.refresh_dashboard_status()
    def stop(self):
        self.tracking=False;self.paused=False
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
        self.status.setText('STOPPED');self.start_button.setText('▶   START TRACKING');self.start_button.setEnabled(True);self.start_button.setToolTip('');self.pause_button.setText('Ⅱ   PAUSE TRACKING');self.pause_button.setEnabled(False);self.stop_button.setEnabled(False);self.log('Tracking stopped.');self.refresh_dashboard_status()
    def pause(self):
        if not self.tracking:return
        if self.mode.currentText()=='Solo Ops':
            if self.solo:
                import psutil
                if self.paused:psutil.Process(self.solo.pid).resume()
                else:psutil.Process(self.solo.pid).suspend()
        self.paused=not self.paused;self.status.setText('PAUSED' if self.paused else 'TRACKING  /  '+self.active_mode_label());self.pause_button.setText('▶   RESUME TRACKING' if self.paused else 'Ⅱ   PAUSE TRACKING');self.refresh_dashboard_status()
    def tick(self):
        if not self.tracking or self.paused:return
        if self.solo and self.solo.poll() is not None:self.log('Solo worker stopped. See Activity Logs for details.');self.stop();return
        now=time.monotonic()
        if now-self.last_poll>=self.poll.value() and not self.poll_busy:self.last_poll=now;self.poll_records()
        if self.mode.currentText()=='Squad Night' and self.auto_detect.isChecked() and now-self.last_auto>=AUTO_OCR_INTERVAL_SECONDS and not self.capture_busy:
            self.last_auto=now;self.capture_once(automatic=True)
    def poll_records(self):
        self.poll_busy=True;values=dict(self.settings.values);mode=self.mode.currentText()
        def work():
            directory,records=E.load_matches(values);E.refresh_codmunity_stats(values,self.store);boot=self.store.meta('cod_bootstrapped',False);new=[]
            state=E.read_json(E.DATA/'posted_matches.json',{}) or {};seen=set(state.get('posted_ids',[]));legacy=E.legacy()
            for match in records:
                if not isinstance(match,dict):continue
                delivered=any(k in seen for k in legacy.match_keys(match))
                status='solo_delivered' if delivered else 'baseline' if not boot else 'observed' if mode=='Solo Ops' else 'needs_review'
                match={**match,'integration_source':'CODMunity'}
                mid,added=self.store.add(match,status)
                if not added:
                    existing=self.store.get(mid)
                    if existing:
                        payload=json.loads(existing['payload'])
                        if payload.get('integration_source')!='CODMunity':
                            payload['integration_source']='CODMunity';self.store.update(mid,payload=payload)
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
            self.refresh_history()
        self.async_task(work,done,lambda _:setattr(self,'poll_busy',False))
    def ensure_capture(self):
        if self.mode.currentText()!='Squad Night':raise RuntimeError('Enable Squad Night to use scoreboard capture.')
        if self.capture is None:
            from capture_engine import CaptureEngine,StableDetector
            self.capture=CaptureEngine();self.detector=StableDetector(frames=2)
        return self.capture
    def refresh_monitors(self):
        try:
            self.outputs=self.ensure_capture().outputs();self.monitor.clear();self.monitor.addItems([x['label'] for x in self.outputs]);self.monitor.setCurrentIndex(min(self.settings.values.get('monitor',0),max(0,len(self.outputs)-1)));self.log('Display list refreshed.')
        except Exception as exc:self.log(str(exc))
    def selected_output(self):
        if not self.outputs:self.refresh_monitors()
        if not self.outputs:raise RuntimeError('No screen outputs found.')
        return self.outputs[max(0,self.monitor.currentIndex())]
    def pick_region(self):
        if self.capture_busy:return
        try:frame=self.ensure_capture().grab(self.selected_output())
        except Exception as exc:self.notice('Capture unavailable: '+str(exc));return
        dialog=RegionDialog(frame,self)
        if dialog.exec()==QDialog.Accepted:
            self.settings.values.update(roi=dialog.roi,monitor=self.monitor.currentIndex());self.settings.save();self.roi_label.setText('Region: '+str(dialog.roi))
    def capture_once(self,checked=False,automatic=False,hotkey=False):
        if self.capture_busy:
            if hotkey:self.log('Scoreboard capture is already running; wait for the current OCR draft.')
            return
        try:
            service=self.ensure_capture();output=self.selected_output();roi=self.settings.values.get('roi')
            if not roi:raise RuntimeError('Select the scoreboard region first.')
        except Exception as exc:
            if automatic:self.auto_detect.setChecked(False)
            self.log(str(exc))
            if hotkey:self.hotkey_status.setText(str(exc));self._show_capture_review()
            return
        self.capture_busy=True
        def work():
            frame=service.grab(output,roi);text,lines,boxes=service.recognize(frame);return frame,text,lines
        def done(result):
            self.capture_busy=False;frame,text,lines=result
            if automatic:
                from capture_utils import looks_like_scoreboard
                if not self.detector.observe(text,looks_like_scoreboard(lines)):return
                self.log('Stable scoreboard detected; draft ready for review.')
            self.last_frame=frame;self.last_capture=None;self.verified.setChecked(False);self.ocr_text.setPlainText(text);self.capture_preview.setPixmap(frame_pixmap(frame).scaled(650,290,Qt.KeepAspectRatio,Qt.SmoothTransformation));self.parse_ocr()
            if hotkey:self._show_capture_review();self.log('Scoreboard OCR draft is ready. Review the names and values, then save it.')
        def failed(message):
            self.capture_busy=False
            if hotkey:self.hotkey_status.setText('Capture failed; try Capture & OCR.');self._show_capture_review()
        self.async_task(work,done,failed)
    def parse_ocr(self):
        rows=E.parse_scoreboard(self.ocr_text.toPlainText());keys=list(dict.fromkeys(key for row in rows for key in row.get('stats',{})))
        self.set_ocr_columns(keys or ['kills','eliminations','deaths','assists','damage','score','placement','redeploys'])
        self.ocr_table.setRowCount(0)
        for row in rows:self.add_ocr_row(row=row)
        self.verified.setChecked(False);self.log(f'{len(rows)} candidate player rows. Review columns and correct values before verifying.')
    def set_ocr_columns(self,keys):
        self.ocr_stat_keys=list(dict.fromkeys(str(key) for key in keys if key))
        labels={'kills':'Kills','eliminations':'Eliminations','deaths':'Deaths','assists':'Assists','damage':'Damage','score':'Score','placement':'Placement','redeploys':'Redeploys','revives':'Revives','headshots':'Headshots','kd':'K/D','downs':'Downs'}
        self.ocr_table.setColumnCount(1+len(self.ocr_stat_keys));self.ocr_table.setHorizontalHeaderLabels(['Player']+[labels.get(key,key.replace('_',' ').title()) for key in self.ocr_stat_keys])
    def add_ocr_row(self,checked=False,row=None):
        index=self.ocr_table.rowCount();self.ocr_table.insertRow(index);row=row or {'name':'','stats':{}}
        self.ocr_table.setItem(index,0,QTableWidgetItem(str(row.get('name',''))))
        for col,key in enumerate(self.ocr_stat_keys,1):
            value=row.get('stats',{}).get(key,'')
            self.ocr_table.setItem(index,col,QTableWidgetItem('' if value is None else str(value)))
    def remove_ocr_row(self):
        if self.ocr_table.currentRow()>=0:self.ocr_table.removeRow(self.ocr_table.currentRow())
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
    def save_capture(self):
        if self.last_frame is None:self.notice('Capture a scoreboard first.');return
        if not self.verified.isChecked():self.notice('Review the scoreboard and confirm the verification checkbox.');return
        try:players=self.verified_players()
        except ValueError as exc:self.notice(str(exc));return
        mid=self.link_match.currentData()
        if mid:
            existing=self.store.get(mid)
            if existing['status'] in ('delivered','solo_delivered') and not self.allow_both.isChecked():self.notice('This match was already delivered. Choose another record or explicitly enable an additional format in Discord Settings.');return
            base_payload=json.loads(existing['payload'])
        else:
            digest=__import__('hashlib').sha256(self.last_frame.tobytes()).hexdigest()[:24]
            base_payload={'matchId':'capture-'+digest,'modeKey':self.settings.values.get('game','Squad scoreboard'),'playedAt':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),'result':'match','source':'screen_capture'}
        snapshot=self.store.meta('codmunity_stats') or {};aliases=[hit.get(key) for hit in (snapshot.get('identity') or {}).get('hits',[]) for key in ('username','displayName') if hit.get(key)]
        aliases+=[alias for player in self.store.roster() if str(player.get('name','')).casefold()==str(self.settings.values.get('player','vintendo')).casefold() for alias in (player.get('aliases') or [])]
        players,personal_added=E.merge_personal_row(players,base_payload,self.settings.values.get('player','vintendo'),aliases)
        if personal_added:self.log('Added your CODMunity end-game row to this scoreboard; teammates still come from reviewed OCR.')
        payload_dict=dict(base_payload)
        team_place=E.placement_from_players(players)
        if team_place is not None and not str(payload_dict.get('placement') or '').strip() and not str((payload_dict.get('stats') if isinstance(payload_dict.get('stats'),dict) else {}).get('placement') or '').strip():payload_dict['placement']=team_place
        payload=E.with_scoreboard_context(payload_dict,self.ocr_text.toPlainText())
        override=self.result_override.currentData() if hasattr(self,'result_override') else 'auto'
        if override in ('win','loss'):payload['result']=override;payload['poster_context']=E.extract_match_context(payload,self.ocr_text.toPlainText())
        context=E.extract_match_context(payload,self.ocr_text.toPlainText())
        if context.get('result') not in ('Victory','Defeat') and override=='auto':
            picked=self.ask_match_result()
            if picked in ('win','loss'):payload['result']=picked;payload['poster_context']=E.extract_match_context(payload,self.ocr_text.toPlainText());context=E.extract_match_context(payload,self.ocr_text.toPlainText())
            else:self.log('Save cancelled: match result is still unknown. Link the CODMunity match, set the Result selector, or answer the win/loss question.');return
        if not mid:mid,_=self.store.add(dict(base_payload))
        folder=E.DATA/'captures';folder.mkdir(parents=True,exist_ok=True);path=folder/(E.disk_id(mid)+'.png')
        from PIL import Image
        Image.fromarray(self.last_frame).save(path)
        auto_eligible=(context.get('result') in ('Victory','Defeat') and bool(context.get('game_mode')) and
                       not str(context['game_mode']).startswith('Not shown') and 1<=len(players)<=4)
        self.store.update(mid,payload=payload,capture=str(path),ocr=self.ocr_text.toPlainText(),players=json.dumps(players),status='needs_review',gen_state='queued' if auto_eligible else 'idle',prompt='',theme=None);self.active_id=mid;self.last_capture=str(path);self.refresh_history();self.load_studio(mid)
        self.log('Verified scoreboard saved. A Meta AI creative brief was prepared from the reviewed names and performance ranks.')
    def ask_match_result(self):
        box=QMessageBox(self);box.setWindowTitle('Vintendo COD Tracker');box.setText('Did you win or lose?');box.setInformativeText('Neither the linked CODMunity match nor the scoreboard shows the result.');win_button=box.addButton('Win',QMessageBox.AcceptRole);lose_button=box.addButton('Lose',QMessageBox.AcceptRole);box.addButton('Cancel',QMessageBox.RejectRole);box.setDefaultButton(win_button);box.exec()
        if box.clickedButton() is win_button:return 'win'
        if box.clickedButton() is lose_button:return 'loss'
        return None
    def post_capture_only(self):
        self.last_capture=None
        self.save_capture()
        if not self.last_capture:return
        self.store.update(self.active_id,image=self.last_capture)
        self.post_active()
    def refresh_roster(self):
        self.roster_rows=self.store.roster()
        query=self.roster_search.text().strip().casefold() if hasattr(self,'roster_search') else ''
        def matches(player):
            if not query:return True
            haystack=' '.join([str(player.get('name','')),str(player.get('display_name') or ''),' '.join(player.get('aliases',[]) or [])]).casefold()
            return query in haystack
        visible=[p for p in self.roster_rows if matches(p)]
        self.roster_visible_ids=[str(p.get('id')) for p in visible]
        self.roster_count.setText('PLAYERS (%d)'%len(self.roster_rows))
        self.roster_list.blockSignals(True);self.roster_list.clear()
        for player in visible:
            item=QListWidgetItem();item.setData(Qt.UserRole,str(player.get('id')));item.setSizeHint(QSize(0,66))
            self.roster_list.addItem(item);self.roster_list.setItemWidget(item,self.roster_card_widget(player))
        self.roster_list.blockSignals(False)
        if self.current_player in self.roster_visible_ids:
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
            self.roster_aliases.setText('; '.join(player.get('aliases',[]) or []))
            self.roster_profile_url.setText(player.get('profile_url','') or '')
            self.roster_enabled.setChecked(bool(player.get('enabled',True)))
            self.photo_consent.setChecked(bool(player.get('consent_meta',False)))
            self.photo_path.setText(player.get('photo','') or '');self.roster_photo_removed=False
            self.headshot_filename.setText(Path(player.get('photo') or '').name if player.get('photo') else 'No headshot selected')
            self.show_image(self.photo_preview,player.get('photo','') or '',420,220)
            display=str(player.get('display_name') or player.get('name',''))
            self.roster_big_name.setText(display)
            alias_text='; '.join(player.get('aliases',[]) or [])
            self.roster_big_sub.setText('COD: '+str(player.get('name',''))+(('   |   Alias: '+alias_text) if alias_text else ''))
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
        if hasattr(self,'roster_tabs') and hasattr(self,'_profile_tab_index') and self.roster_tabs.currentIndex()==self._profile_tab_index:self.load_roster_profile_page()
    def show_roster_empty(self):
        self._roster_loading=True
        try:
            self.roster_big_photo.setPixmap(self.default_avatar(120));self.roster_big_name.setText('No player selected');self.roster_big_sub.setText('');self.roster_enabled_pill.setText('');self.consent_status.setText('');self.photo_path.clear();self.headshot_filename.setText('No headshot selected');self.photo_preview.clear();self.photo_preview.setText('No headshot');self.roster_profile_url.clear();self.roster_enabled.setChecked(True)
        finally:self._roster_loading=False
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
            self.current_player=None;self.roster_name.clear();self.roster_display_name.clear();self.roster_aliases.clear();self.roster_profile_url.clear();self.photo_path.clear();self.headshot_filename.setText('No headshot selected');self.photo_preview.clear();self.photo_preview.setText('No headshot');self.photo_consent.setChecked(False);self.roster_enabled.setChecked(True);self.roster_photo_removed=False
            self.roster_big_photo.setPixmap(self.default_avatar(120));self.roster_big_name.setText('New player');self.roster_big_sub.setText('Fill in the details, then Save Player.');self.roster_enabled_pill.setText('');self.consent_status.setText('');self.name_match_result.setText('')
        finally:self._roster_loading=False
        self.set_roster_dirty(False);self.roster_status.setText('')
    def pick_photo(self):
        path,_=QFileDialog.getOpenFileName(self,'Choose player headshot','','Images (*.png *.jpg *.jpeg *.webp)')
        if path:self.photo_path.setText(path);self.headshot_filename.setText(Path(path).name);self.show_image(self.photo_preview,path,420,220);self.roster_photo_removed=False;self.mark_roster_dirty()
    def remove_roster_photo(self):
        self.photo_path.clear();self.headshot_filename.setText('No headshot selected');self.photo_preview.clear();self.photo_preview.setText('No headshot');self.roster_photo_removed=True;self.mark_roster_dirty()
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
        if photo:
            from PIL import Image,ImageOps
            folder=E.DATA/'headshots';folder.mkdir(parents=True,exist_ok=True);target=folder/(pid+'.jpg')
            try:
                with Image.open(photo) as source:im=ImageOps.exif_transpose(source).convert('RGB');im.thumbnail((1600,1600));im.save(target,quality=94)
            except Exception as exc:self.notice('Could not read headshot: '+str(exc));return
            photo=str(target)
        elif not self.roster_photo_removed:photo=stored_photo
        display_name=self.roster_display_name.text().strip() or name
        self.store.save_player(dict(id=pid,name=name,display_name=display_name,aliases=[v.strip() for v in self.roster_aliases.text().split(';') if v.strip()],profile_url=self.roster_profile_url.text().strip(),photo=photo,enabled=self.roster_enabled.isChecked(),consent_meta=self.photo_consent.isChecked()));self.current_player=pid;self.set_roster_dirty(False);self.refresh_roster();self.roster_status.setText('Saved '+name+'.');self.load_roster_player(pid);self.refresh_talent_friends();self.log('Player saved: '+name+' (poster name: '+display_name+')')
    def delete_player(self):
        if not self.current_player:return
        player=next((p for p in self.roster_rows if str(p.get('id'))==str(self.current_player)),None)
        label=player.get('name','this player') if player else 'this player'
        if self.ask("Delete player '"+label+"'? This cannot be undone."):
            self.store.delete_player(self.current_player);self.new_player();self.refresh_roster();self.refresh_talent_friends();self.roster_status.setText('Deleted '+label+'.')
    def test_name(self):
        player,score=E.match_player(self.name_test.text(),self.store.roster())
        if player:self.name_match_result.setText('Match: '+player['name']+'  ·  confidence '+f'{score:.0%}');self.name_match_result.setStyleSheet('font-size:11px;color:#4ade80;')
        else:self.name_match_result.setText('No unambiguous match found.');self.name_match_result.setStyleSheet('font-size:11px;color:#e5a13d;')
    def refresh_history(self):
        if not hasattr(self,'history'):return
        rows=self.store.history();active_link=self.link_match.currentData();self.link_match.clear();self.link_match.addItem('Unlinked scoreboard — create capture record',None)
        for job in rows:
            match=json.loads(job['payload'])
            if job['status'] not in ('delivered','solo_delivered'):
                self.link_match.addItem(str(match.get('modeKey',match.get('mode','match')))+' · '+str(match.get('playedAt',''))+' · '+job['id'][-12:],job['id'])
        ix=self.link_match.findData(active_link)
        if ix>=0:self.link_match.setCurrentIndex(ix)
        self.dashboard_metrics['matches'].setText(f'{len(rows):,}')
        self.dashboard_metrics['squad_posts'].setText(f"{sum(j['status']=='delivered' for j in rows):,}")
        review_statuses={'needs_review','art_ready','ready','failed','uncertain','awaiting_artwork'}
        self.dashboard_metrics['reviews'].setText(f"{sum(j['status'] in review_statuses for j in rows):,}")
        self.center_count.setText(f'{len(rows)} records')
        self.refresh_match_center(rows)
        self.refresh_dashboard_status();self.refresh_match_history_view(rows)

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

    def open_center_match(self,view,row,column=0):
        rows=self.center_rows.get(view,[])
        if 0<=row<len(rows):self.load_studio(rows[row]['id']);self.nav.setCurrentRow(5)
    def _set_dashboard_health(self,key,value,good=None):
        indicator,label=self.dashboard_health[key];label.setText(value)
        color='#56e99a' if good else '#ff626d' if good is False else '#bbc4d0'
        label.setStyleSheet('color:'+color+';font-size:11px;font-weight:800;')
        indicator.setStyleSheet('color:'+color+';')

    CENTER_REVIEW_STATUSES={'needs_review','art_ready','ready','failed','uncertain','awaiting_artwork'}
    CENTER_CARD_SPECS={'records':[('games','GAMES'),('win_rate','WIN RATE')],'posts':[('kd','K/D RATIO'),('kills_per_game','KILLS / GAME')],'review':[('needs_review','NEEDS REVIEW'),('ready','READY'),('attention','NEEDS ATTENTION')]}
    REVIEW_STATUS_GROUPS={'needs_review':{'needs_review'},'ready':{'ready','art_ready'},'attention':{'failed','uncertain'}}

    def refresh_match_center(self,rows):
        if not hasattr(self,'center_tables'):return
        self.center_rows={'records':list(rows),'posts':[job for job in rows if job['status']=='delivered'],'review':[job for job in rows if job['status'] in self.CENTER_REVIEW_STATUSES]}
        for view,table in self.center_tables.items():
            view_rows=self.center_rows[view];table.setRowCount(len(view_rows))
            for row,job in enumerate(view_rows):
                match=json.loads(job['payload']);stats=match.get('stats') if isinstance(match.get('stats'),dict) else {}
                raw_result=str(match.get('result') or '').lower();result={'win':'WIN','victory':'WIN','loss':'LOSS','defeat':'LOSS'}.get(raw_result,'\u2014')
                color='#56e99a' if result=='WIN' else '#ff626d' if result=='LOSS' else '#bbc4d0'
                played=self._display_time(match.get('playedAt') or match.get('date') or match.get('timestamp'))
                mode=str(match.get('modeKey',match.get('mode','\u2014')) or '\u2014').replace('_',' ').title()
                if view=='records':
                    place=match.get('placement')
                    if place is None:place=stats.get('placement')
                    values=[job['id'][-12:],result,self._display_value(place),mode,self._display_value(stats.get('kills')),self._display_value(stats.get('eliminations')),self._display_value(stats.get('damage')),self._display_value(stats.get('deaths')),self._display_value(stats.get('assists')),played,str(job.get('status','\u2014')).replace('_',' ').upper(),str(job.get('attempts',0))]
                elif view=='posts':
                    values=[job['id'][-12:],result,mode,self._display_value(stats.get('kills')),self._display_value(stats.get('damage')),played]
                else:
                    values=[job['id'][-12:],result,mode,str(job.get('status','\u2014')).replace('_',' ').upper(),str(job.get('attempts',0)),played]
                for col,value in enumerate(values):
                    item=QTableWidgetItem(value)
                    if col==0:item.setToolTip(job['id'])
                    if col==1:item.setForeground(QColor(color))
                    table.setItem(row,col,item)
        self.refresh_center_stats()
    def refresh_center_stats(self):
        if not hasattr(self,'center_carousel'):return
        rows_by_view=getattr(self,'center_rows',{}) or {}
        cards=[]
        for view in ('records','posts','review'):
            rows=rows_by_view.get(view,[]) if isinstance(rows_by_view,dict) else []
            summary=self._match_summary(rows)
            for key,title in self.CENTER_CARD_SPECS[view]:
                if key in summary:value=str(summary[key])
                else:value=str(sum(1 for job in rows if job['status'] in self.REVIEW_STATUS_GROUPS.get(key,())))
                cards.append((title,value))
        self.center_carousel.set_cards(cards)
        self.refresh_center_count()
    def refresh_center_count(self):
        if not hasattr(self,'center_tabs') or not hasattr(self,'center_count'):return
        views=('records','posts','review')
        try:index=self.center_tabs.currentIndex()
        except (AttributeError,RuntimeError):index=0
        view=views[index] if 0<=index<3 else 'records'
        rows=self.center_rows.get(view,[]) if hasattr(self,'center_rows') else []
        total=len(self.center_rows.get('records',[])) if hasattr(self,'center_rows') else 0
        self.center_count.setText(f"{len(rows)} of {total} records")

    def refresh_dashboard_status(self):
        if not hasattr(self,'dashboard_health'):return
        folder=Path(self.stats_dir.text().strip()) if hasattr(self,'stats_dir') and self.stats_dir.text().strip() else None
        folder_ready=bool(folder and folder.is_dir())
        self._set_dashboard_health('codmunity','FOLDER READY' if folder_ready else 'FOLDER NOT FOUND',folder_ready)
        roi=self.settings.values.get('roi')
        roi_ready=bool(isinstance(roi,(list,tuple)) and len(roi)==4 and roi[2]>roi[0] and roi[3]>roi[1])
        ocr_state='AUTO SCAN' if roi_ready and self.tracking and self.mode.currentText()=='Squad Night' and self.auto_detect.isChecked() else 'REGION READY' if roi_ready else 'SET UP REGION'
        self._set_dashboard_health('ocr',ocr_state,roi_ready)
        bridge=getattr(self,'extension_bridge',None);bridge_ready=bool(bridge and getattr(bridge,'running',False))
        self._set_dashboard_health('bridge','LISTENING' if bridge_ready else 'OFFLINE',bridge_ready)
        discord_ready=bool(hasattr(self,'webhook') and self.webhook.text().strip())
        self._set_dashboard_health('discord','WEBHOOK SAVED' if discord_ready else 'NOT CONFIGURED',discord_ready)
        if self.tracking and self.paused:tracking='PAUSED';color='#e3b95e'
        elif self.tracking:tracking='LIVE TRACKING';color='#56e99a'
        else:tracking='STANDBY';color='#a7b0bd'
        self.status.setText('●  '+tracking);self.status.setStyleSheet(f'background:#111720;border:1px solid {color};border-radius:18px;color:{color};font-size:13px;font-weight:900;padding:10px 16px;')
        self.footer_tracking.setText('●  '+tracking);self.footer_tracking.setStyleSheet('font-weight:800;color:'+color)
        self.footer_mode.setText('Mode: '+self.active_mode_label())
        latest=json.loads(self.history_rows[0]['payload']) if getattr(self,'history_rows',None) else {}
        played=latest.get('playedAt') or latest.get('date') or latest.get('timestamp')
        self.footer_match.setText('Last match: '+(self._display_time(played) if played else '—'))
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
        players=json.loads(job['players'] or '[]');self.ensure_match_prompt(job,players);self.preview_reference_photos(players)
        if job['capture']:
            self.show_image(self.capture_preview,job['capture'],650,290);self.ocr_text.setPlainText(job['ocr'])
            if self.mode.currentText()=='Squad Night':
                try:
                    import numpy as np
                    from PIL import Image
                    with Image.open(job['capture']) as image:self.last_frame=np.array(image.convert('RGB'))
                    saved_players=json.loads(job['players']);keys=list(dict.fromkeys(key for player in saved_players for key in (player.get('stats') or {})))
                    self.set_ocr_columns(keys or ['kills','eliminations','deaths','assists','damage','score','placement','redeploys']);self.ocr_table.setRowCount(0)
                    for player in saved_players:self.add_ocr_row(row=player)
                    self.verified.setChecked(False)
                except (OSError,ValueError):self.last_frame=None
        ix=self.link_match.findData(mid)
        if ix>=0:self.link_match.setCurrentIndex(ix)
        self.job_status.setText(job['error'] or 'Ready for review')
    def view_source(self):
        job=self.selected_job()
        if not job:return
        dialog=QDialog(self);dialog.setWindowTitle('Stored match data and provenance');dialog.resize(850,600);layout=QVBoxLayout(dialog);text=QPlainTextEdit(json.dumps({**job,'payload':json.loads(job['payload']),'players':json.loads(job['players'])},indent=2));text.setReadOnly(True);layout.addWidget(text);dialog.exec()
    def ensure_match_prompt(self,job,players=None):
        players=json.loads(job['players'] or '[]') if players is None else players
        existing=str(job.get('prompt') or '').split('\nREFERENCE PHOTO MAPPING:')[0]
        if any(isinstance(p,dict) and p.get('verified') for p in players):
            if E.MATCH_POSTER_PROMPT_MARKER not in existing:
                try:existing=E.match_poster_prompt(players,json.loads(job['payload']),job.get('ocr',''),roster=self.store.roster(),snapshot=self.store.meta('codmunity_stats'))
                except ValueError as exc:existing='';self.job_status.setText(str(exc))
                else:self.store.update(job['id'],prompt=existing,theme=None)
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
        for mapping in mappings:
            if mapping['status']=='ready':
                assignments.append(mapping['username']+' → poster name: '+mapping['posterName']+' (consented photo matched)')
                thumbnail=QLabel();thumbnail.setToolTip(mapping['username']+' → '+mapping['posterName']);self.show_image(thumbnail,mapping['photoPath'],96,96);self.studio_heads.addWidget(thumbnail)
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
        if not job:
            rows=self.store.history()
            if not rows:self.notice('Select a match or save a verified scoreboard first.');return
            actionable=[row for row in rows if row['status'] not in ('baseline','solo_delivered','delivered')]
            pool=sorted(actionable or rows,key=lambda row:row.get('created') or 0,reverse=True)
            mid=pool[0]['id']
            self.active_id=mid;job=self.store.get(mid)
            self.log('One-click poster: newest actionable match selected automatically.')
        if not job:self.notice('Select a match or save a verified scoreboard first.');return
        bridge=getattr(self,'extension_bridge',None)
        if not bridge or not bridge.running:
            self.notice('The Vintendo Squad AI local bridge is unavailable. Restart the tracker, then retry.');return
        prompt=self.ensure_match_prompt(job)
        prepared=None
        if not prompt:
            try:prepared=bridge.prepare(job['id'])
            except Exception as exc:
                message='Could not prepare this match for Vintendo COD Automation: '+str(exc)
                self.job_status.setText(message[:300]);self.notice(message);self.log('Poster preparation failed for '+job['id']+': '+str(exc));return
            job=self.store.get(job['id']);prompt=self.ensure_match_prompt(job)
            if not prompt:
                message='Could not prepare this match for Vintendo COD Automation: no identified players'
                self.job_status.setText(message);self.notice(message);return
        try:
            if prepared is None:
                prepared=bridge.prepare(job['id'])
            if not prepared:raise ValueError('The match job could not be prepared.')
            QApplication.clipboard().setText(prepared['prompt'])
            bridge.on_prepare(job['id'],prepared['folder'])
            self.active_id=job['id'];self.load_studio(job['id']);self.refresh_history()
            self.job_status.setText('Complete match request prepared for Vintendo Squad AI. Prompt file and mapped photos are ready; use the extension and its supported import controls.')
            self.log(f"Match {job['id']} prepared for Vintendo Squad AI: {len(prepared['photos'])} consented reference photo(s); prompt copied and import folder opened.")
        except Exception as exc:
            message='Could not prepare this match for Vintendo COD Automation: '+str(exc)
            self.job_status.setText(message[:300]);self.notice(message);self.log('Poster preparation failed for '+job['id']+': '+str(exc))
    def received_art(self,mid,path):
        current=self.store.get(mid)
        if not current:return
        if current.get('artwork')!=path and not self.store.note_artwork(mid,path):return
        self.log('Validated artwork received for '+mid);self.refresh_history()
        if self.active_id==mid:self.show_image(self.art_preview,path,800,300)
        job=self.store.get(mid)
        if job and json.loads(job['players']):
            self.active_id=mid;self.render_overlay(post_after=self.auto_post.isChecked())
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
        self.async_task(lambda:E.render_poster(background,target,match,players),done)
    def show_image(self,label,path,width,height):
        pix=QPixmap(path) if path else QPixmap()
        if not pix.isNull():label.setPixmap(pix.scaled(width,height,Qt.KeepAspectRatio,Qt.SmoothTransformation))
        else:label.setText('No image available')
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
        def failed(error):
            self.store.update(mid,status='uncertain' if 'uncertain' in error.lower() else 'failed',error=error);self.refresh_history()
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
        self.codmunity_poll.stop();self.codmunity_host.detach()
        try:self.browser_host.detach()
        except Exception:pass
        if getattr(self,'extension_bridge',None):self.extension_bridge.close();self.extension_bridge=None
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
    if '--bridge-smoke-test' in sys.argv and not os.environ.get('VINTENDO_DATA_DIR'):
        raise SystemExit('--bridge-smoke-test requires VINTENDO_DATA_DIR pointing to a disposable test folder.')
    E.DATA.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=logging.INFO,handlers=[RotatingFileHandler(E.DATA/'application.log',maxBytes=2_000_000,backupCount=3,encoding='utf-8')],format='%(asctime)s %(levelname)s %(message)s')
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
    bridge_smoke='--bridge-smoke-test' in sys.argv or os.environ.get('VINTENDO_BRIDGE_SMOKE_TEST')=='1'
    smoke=is_smoke_run() or bridge_smoke
    server=None
    if not smoke and not prototype:
        name='VintendoCODTracker-'+__import__('hashlib').sha256(str(E.DATA).encode()).hexdigest()[:16]
        socket=QLocalSocket();socket.connectToServer(name)
        if socket.waitForConnected(300):return
        QLocalServer.removeServer(name);server=QLocalServer();server.listen(name)
    window=Window();window.show()
    if (server is not None or bridge_smoke) and not prototype:
        if server is not None:server.newConnection.connect(window.showNormal)
        if bridge_smoke:
            window.auto_post.setChecked(False)
            window.open_prepared_photos=lambda *_: None
        window.start_extension_bridge()
    if server is not None and os.environ.get('VINTENDO_NO_AUTO_START','0')!='1':
        QTimer.singleShot(1500,window.start)
        QTimer.singleShot(20000,window.check_for_app_update)
    if smoke and not bridge_smoke:
        def finish():
            window.grab().save(str(E.DATA/'startup-smoke.png'));E.atomic_json(E.DATA/'smoke-result.json',{'startup':'passed','pages':window.nav.count(),'mode':window.mode.currentText(),'ocr_initialized':window.capture is not None});window.shutdown()
        QTimer.singleShot(2500,finish)
    if bridge_smoke:
        def finish_bridge():
            result={'startup':'failed'};mid=None
            try:
                import urllib.request
                from PIL import Image
                import io
                bridge_id=__import__('local_bridge').EXTENSION_ID
                headers={'Origin':'chrome-extension://'+bridge_id,'Authorization':'Bearer '+window.settings.extension_token(),'X-Vintendo-Extension-ID':bridge_id}
                mid,_=window.store.add({'matchId':'bridge-smoke-'+uuid.uuid4().hex,'modeKey':'resurgence','result':'win','playedAt':'2026-11-07T00:00:00Z'})
                window.store.update(mid,players=json.dumps([{'name':'Mock Alpha','verified':True,'stats':{'kills':4}}]),prompt='Create a 16:9 mock image.',gen_state='queued')
                reference=E.DATA/'bridge-smoke-reference.png'
                Image.new('RGB',(320,240),'orange').save(reference)
                window.store.save_player({'id':'bridge-smoke-alpha','name':'Mock Alpha','aliases':[],
                    'photo':str(reference),'consent_meta':True,'enabled':True})
                def request(path,method='GET',body=None,content_type='application/json'):
                    outgoing=dict(headers)
                    if body is not None:outgoing['Content-Type']=content_type
                    req=urllib.request.Request(f'http://127.0.0.1:{window.extension_bridge.port}/v1'+path,data=body,headers=outgoing,method=method)
                    with urllib.request.urlopen(req,timeout=3) as response:return response.status,json.loads(response.read())
                status,payload=request('/status')
                if status!=200 or payload.get('connected') is not True:raise RuntimeError('Bridge status request failed.')
                status,prepared=request('/jobs/'+urllib.parse.quote(mid,safe='')+'/prepare','POST',b'{}')
                if status!=200:raise RuntimeError('Extension job prepare request failed.')
                status,_=request('/jobs/'+urllib.parse.quote(mid,safe='')+'/state','POST',b'{"state":"awaiting_download"}')
                if status!=200:raise RuntimeError('Generation state update failed.')
                image=Image.new('RGB',(640,360),'navy');buffer=io.BytesIO();image.save(buffer,format='PNG')
                status,received=request('/jobs/'+urllib.parse.quote(mid,safe='')+'/artwork','POST',buffer.getvalue(),'image/png')
                if status!=201:raise RuntimeError('Extension artwork upload failed: '+str(received))
                result={'startup':'passed','bridge_status':200,'loopback':True,'queue_count':payload.get('pendingCount'),'pages':window.nav.count(),'upload_status':status,'job_id':mid,'artwork_bound':received.get('job',{}).get('matchId')==mid}
            except Exception as exc:result={'startup':'failed','error':str(exc)[:240]}
            def finish_bridge_signal():
                if mid:
                    row=window.store.get(mid);result['app_received_artwork']=bool(row and row.get('artwork') and Path(row['artwork']).is_file())
                    if not result.get('app_received_artwork'):result['startup']='failed'
                E.atomic_json(E.DATA/'bridge-smoke-result.json',result);window.shutdown();app.quit()
            QTimer.singleShot(400,finish_bridge_signal)
        import urllib.parse
        QTimer.singleShot(700,finish_bridge)
    sys.exit(app.exec())

if __name__=='__main__':
    try:main()
    except Exception:
        E.DATA.mkdir(parents=True,exist_ok=True)
        import traceback
        (E.DATA/'crash.log').write_text(re.sub(r'(https://discord(?:app)?\.com)?/api/webhooks/\d+/[\w.\-]+','[webhook]',traceback.format_exc()),encoding='utf-8')
        raise
