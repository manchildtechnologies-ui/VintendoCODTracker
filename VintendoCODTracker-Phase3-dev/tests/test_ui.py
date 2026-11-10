import os,sys,unittest,time,json,uuid
from pathlib import Path
from contextlib import contextmanager
import shutil
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
import app

@contextmanager
def browser_fixture_dir():
    root=Path(app.E.DATA)/('browser-fixture-'+uuid.uuid4().hex)
    root.mkdir(parents=True)
    try:yield root
    finally:shutil.rmtree(root,ignore_errors=True)

class UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.qt=QApplication.instance() or QApplication([])
    def setUp(self):self.window=app.Window()
    def tearDown(self):self.window.shutdown();self.window.deleteLater();self.qt.processEvents()
    def test_all_navigation_sections_exist(self):
        self.assertEqual(self.window.nav.count(),11)
        self.assertIn('ScreenShots and Videos',[self.window.nav.item(i).data(Qt.UserRole) for i in range(self.window.nav.count())])
    def test_top_level_tabs_include_tracker_and_codommunity(self):
        self.assertEqual(self.window.app_tabs.count(),2)
        self.assertEqual([self.window.app_tabs.tabText(i) for i in range(2)],['Vintendo COD Tracker','CODMunity'])
        self.assertIs(self.window.app_tabs.widget(1),self.window.codmunity_page)
    def test_tick_never_triggers_automatic_capture(self):
        self.assertGreater(app.AUTO_OCR_INTERVAL_SECONDS,0)
        self.assertTrue(self.window.auto_detect.isHidden())
        self.assertFalse(self.window.auto_detect.isChecked())
        self.window.mode.setCurrentText('Squad Night')
        self.window.last_poll=time.monotonic()
        self.window.last_auto=0
        self.window.capture_busy=False
        self.window.tracking=True
        calls=[];real=self.window.capture_once;self.window.capture_once=lambda *args,**kwargs:calls.append((args,kwargs))
        try:self.window.tick()
        finally:self.window.tracking=False;self.window.capture_once=real
        self.assertEqual(calls,[])
    def test_delete_selected_screenshot_removes_file_and_row(self):
        from PIL import Image
        folder=Path(app.E.DATA)/'Screenshots';folder.mkdir(parents=True,exist_ok=True)
        target=folder/('COD_delete_'+uuid.uuid4().hex+'.png');Image.new('RGB',(320,180),'blue').save(target)
        self.window.refresh_cod_ai_screenshots()
        found=[self.window.cod_ai_screenshots.item(i) for i in range(self.window.cod_ai_screenshots.count())
              if self.window.cod_ai_screenshots.item(i).data(Qt.UserRole)==str(target)]
        self.assertTrue(found);self.window.cod_ai_screenshots.setCurrentItem(found[0])
        self.window.ask=lambda *args,**kwargs:True
        self.window.delete_cod_ai_screenshot()
        self.assertFalse(target.exists())
        self.assertIn('Deleted',self.window.cod_ai_capture_status.text())
    def test_posted_cinematic_video_is_auto_deleted_but_failed_post_keeps_file(self):
        folder=Path(app.E.DATA)/'Cinematics';folder.mkdir(parents=True,exist_ok=True)
        cid='cin-del-'+uuid.uuid4().hex
        shot=folder/('in_'+cid+'.png');shot.write_bytes(b'\x89PNG\r\n\x1a\n'+b'0'*64)
        self.assertTrue(self.window.store.create_cinematic(cid,'shot.png',shot,'prompt'))
        good=folder/(cid+'.mp4');good.write_bytes(b'\x00\x00\x00\x18ftyp'+b'0'*4096)
        self.window.store.update_cinematic(cid,status='completed',video=str(good))
        self.window.on_cinematic_discord_result('Discord accepted the video (HTTP 200).',cid,str(good))
        self.assertFalse(good.exists())
        self.assertEqual(self.window.store.get_cinematic(cid)['video'],'')
        self.assertIn('deleted',self.window.cinematic_status.text().lower())
        bad=folder/(cid+'-keep.mp4');bad.write_bytes(b'\x00\x00\x00\x18ftyp'+b'0'*4096)
        self.window.store.update_cinematic(cid,video=str(bad))
        self.window.on_cinematic_discord_result('Discord post failed: boom',cid,str(bad))
        self.assertTrue(bad.exists())
    def _button_by_text(self,text):
        from PySide6.QtWidgets import QPushButton
        for button in self.window.findChildren(QPushButton):
            if button.text()==text:return button
        return None
    def test_send_to_meta_queues_without_bridge(self):
        from PIL import Image
        folder=Path(app.E.DATA)/'Screenshots';folder.mkdir(parents=True,exist_ok=True)
        target=folder/('COD_send_'+uuid.uuid4().hex+'.png');Image.new('RGB',(320,180),'red').save(target)
        self.window.refresh_cod_ai_screenshots()
        send=self._button_by_text('Send Selected Material to Meta Ai to Make A Video')
        self.assertIsNotNone(send)
        self.assertIsNone(getattr(self.window,'extension_bridge',None))
        found=[self.window.cod_ai_screenshots.item(i) for i in range(self.window.cod_ai_screenshots.count())
              if self.window.cod_ai_screenshots.item(i).data(Qt.UserRole)==str(target)]
        self.assertTrue(found);self.window.cod_ai_screenshots.setCurrentItem(found[0])
        send.click()
        self.assertIn('Queued',self.window.cinematic_status.text())
        cid=self.window.cinematic_status.text().split('Queued ')[1].split('.')[0].strip()
        try:self.assertTrue(self.window.store.get_cinematic(cid))
        finally:
            try:self.window.store.update_cinematic(cid,status='failed')
            except Exception:pass
            shutil.rmtree(Path(app.E.DATA)/'Cinematics'/cid,ignore_errors=True)
            try:(Path(app.E.DATA)/'Cinematics'/'input'/(cid+'.png')).unlink(missing_ok=True)
            except OSError:pass
    def test_sidebar_fits_without_horizontal_scroll(self):
        self.assertGreaterEqual(self.window.sidebar.width(),275)
        self.assertEqual(self.window.nav.horizontalScrollBarPolicy(),Qt.ScrollBarAlwaysOff)
    def test_clicking_a_screenshot_loads_an_in_app_preview(self):
        from PIL import Image
        path=Path(app.E.DATA)/'Screenshots'/('COD_preview_'+uuid.uuid4().hex+'.png');path.parent.mkdir(parents=True,exist_ok=True);Image.new('RGB',(640,360),'red').save(path)
        item=app.QListWidgetItem(path.name);item.setData(Qt.UserRole,str(path))
        try:
            self.window.preview_cod_ai_screenshot(item)
            self.assertIsNotNone(self.window.preview_image.pixmap())
            self.assertIn('640×360',self.window.preview_sub.text())
        finally:path.unlink(missing_ok=True)
    def test_codommunity_preview_snapshots_settings_roster_and_headshots(self):
        import hashlib
        source=Path(app.E.DATA)/('preview-source-'+uuid.uuid4().hex);target=Path(app.E.DATA)/('preview-copy-'+uuid.uuid4().hex)
        (source/'headshots').mkdir(parents=True);(source/'headshots'/'player.jpg').write_bytes(b'fixture-photo')
        (source/'Screenshots').mkdir();(source/'Screenshots'/'COD_fixture.png').write_bytes(b'fixture-screenshot')
        settings=b'{"player":"fixture-user","poll_seconds":9}';(source/'settings.json').write_bytes(settings)
        store=app.E.Store(source/'history.sqlite3');store.save_player({'id':'fixture-id','name':'Fixture','photo':str(source/'headshots'/'player.jpg'),'enabled':True,'consent_meta':True})
        digest=hashlib.sha256((source/'history.sqlite3').read_bytes()).hexdigest()
        try:
            app.prepare_tab_preview_data(source,target)
            preview_store=app.E.Store(target/'history.sqlite3');player=preview_store.roster()[0]
            self.assertEqual((target/'settings.json').read_bytes(),settings)
            self.assertTrue(Path(player['photo']).is_file());self.assertEqual(Path(player['photo']).read_bytes(),b'fixture-photo')
            self.assertNotEqual(Path(player['photo']).resolve(),(source/'headshots'/'player.jpg').resolve())
            self.assertEqual((target/'Screenshots'/'COD_fixture.png').read_bytes(),b'fixture-screenshot')
            self.assertEqual(hashlib.sha256((source/'history.sqlite3').read_bytes()).hexdigest(),digest)
        finally:
            shutil.rmtree(source,ignore_errors=True);shutil.rmtree(target,ignore_errors=True)
    def test_cod_ai_screenshot_controls_are_available(self):
        self.assertTrue(hasattr(self.window,'cod_ai_hotkey_status'))
        self.assertTrue(hasattr(self.window,'cod_ai_screenshots'))
    def test_preview_video_widget_stays_hidden_without_a_video(self):
        w=self.window
        self.assertTrue(w.cinematic_video_widget.isHidden())
        w.preview_cinematic_video(None)
        self.assertTrue(w.cinematic_video_widget.isHidden())
        self.assertFalse(w.preview_image.isHidden())
        self.assertEqual(w.preview_sub.text(),'Watch the selected video.')
        self.assertTrue(w.video_controls.isHidden())
    def test_poster_studio_has_one_scoreboard_driven_generate_action(self):
        self.assertEqual(self.window.generate_poster_button.text(),'Generate AI End Game Picture')
        self.assertFalse(hasattr(self.window,'provider'));self.assertFalse(hasattr(self.window,'theme'))
    def test_solo_startup_does_not_initialize_ocr(self):self.assertIsNone(self.window.capture);self.assertIsNone(self.window.browser)
    def test_activate_squad_night_button_acknowledges_every_click(self):
        w=self.window
        w.mode.setCurrentText('Solo Ops');w.nav.setCurrentRow(2)
        button=self._button_by_text('Activate Squad Night')
        self.assertIsNotNone(button);self.assertTrue(button.isEnabled())
        button.click()
        self.assertEqual(w.mode.currentText(),'Squad Night');self.assertEqual(w.nav.currentRow(),0)
        button.click()
        self.assertEqual(w.mode.currentText(),'Squad Night');self.assertEqual(w.nav.currentRow(),0)
    def test_tracker_browser_search_survives_capture_engine_scan(self):
        import ctypes
        from capture_engine import CaptureEngine
        before=ctypes.windll.user32.EnumWindows.argtypes
        CaptureEngine().game_windows()
        self.assertEqual(ctypes.windll.user32.EnumWindows.argtypes,before)
        self.assertIsInstance(self.window.find_tracker_chrome_windows(),list)
    def test_hotkey_capture_shows_waiting_then_confirms(self):
        from unittest.mock import patch
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QFocusEvent,QKeyEvent
        from PySide6.QtWidgets import QMessageBox
        edit=self.window.scoreboard_hotkey_edit
        edit.focusInEvent(QFocusEvent(QEvent.FocusIn))
        self.assertIn('Press the new keys',edit.text())
        event=QKeyEvent(QEvent.KeyPress,Qt.Key_F9,Qt.ControlModifier|Qt.ShiftModifier,'',False,1)
        with patch.object(QMessageBox,'information',return_value=QMessageBox.Ok) as confirm:
            edit.keyPressEvent(event)
        self.assertEqual((edit.vk,edit.mod),(0x78,app.MOD_CONTROL|app.MOD_SHIFT))
        confirm.assert_called_once()
        self.assertEqual(confirm.call_args[0][1],'Hotkeys Set')
        self.assertNotIn('Press the new keys',edit.text())
    def test_codmunity_tab_pulls_matches_into_tracker(self):
        from unittest.mock import patch
        w=self.window
        with patch.object(app.E,'load_matches',wraps=app.E.load_matches) as loader:
            w.app_tabs.setCurrentIndex(1)
            self.wait_until(lambda:not w.poll_busy)
            self.assertTrue(loader.called)
            self.assertEqual(w.codmunity_status.text(),'CODMunity matches are in the tracker.')
    def test_studio_match_picker_loads_selected_match(self):
        w=self.window
        payload={'matchId':'studio-pick','game':'warzone','mode':'battle_royale','modeKey':'resurgence','playedAt':'2026-10-04T08:00:00Z','result':'win','stats':{'kills':7}}
        mid,_=w.store.add(payload,'needs_review')
        w.store.update(mid,players=json.dumps([{'name':'Vintendo','stats':{'kills':7},'verified':True}]),prompt=app.E.MATCH_POSTER_PROMPT_MARKER+'\nfixture')
        w.refresh_history()
        ix=w.studio_match_picker.findData(mid)
        self.assertGreaterEqual(ix,0)
        w.studio_match_picker.setCurrentIndex(ix)
        self.assertEqual(w.active_id,mid)
        self.assertIn(mid,w.studio_match.text())
        self.assertTrue(w.generate_poster_button.isEnabled())
    def test_mission_core_animates_real_stages(self):
        w=self.window
        w.set_automation_stage('generating','test job overdrive')
        self.assertEqual(w.auto_core.stage,'generating')
        self.assertTrue(w.auto_core._timer.isActive())
        self.assertIn('test job overdrive',w.browser_status.text())
        w._auto_stage_tick()
        self.assertTrue(w.auto_core.elapsed_text)
        w.set_automation_stage('failed','boom')
        self.assertEqual(w.auto_core.stage,'failed')
        self.assertFalse(w.auto_core._timer.isActive())
        self.assertIn('boom',w.browser_status.text())
        w.set_automation_stage('ready')
        self.assertEqual(w.auto_core.stage,'ready')
        self.assertIn('idle in the background',w.browser_status.text())
    def test_mission_core_paints_every_stage(self):
        from PySide6.QtGui import QPixmap
        core=app.MissionCore()
        core.resize(220,220)
        for stage in ('ready','queued','submitting','generating','collecting','collected','posted','failed'):
            core.set_stage(stage);core.set_elapsed('01:23')
            pix=QPixmap(220,220);core.render(pix)
        self.assertEqual(core.stage,'failed')
        self.assertEqual(core.elapsed_text,'01:23')
    def test_login_card_appears_on_login_failure(self):
        w=self.window
        mid,_=w.store.add({'matchId':'login-fail-'+uuid.uuid4().hex,'modeKey':'resurgence','result':'win'})
        pumped=[]
        w._pump_full_auto=lambda:pumped.append(True)
        w._poster_manual=False
        w.notice=lambda *args:(_ for _ in ()).throw(AssertionError('no modal in background flow'))
        w._fail_inapp_poster(mid,'login','Meta AI is signed out')
        self.assertFalse(w.login_card.isHidden())
        self.assertEqual(pumped,[True])
        row=w.store.get(mid);self.assertEqual(row['gen_state'],'idle')
    def test_toggle_browser_without_window_reports_clearly(self):
        w=self.window;w.auto_browser_hwnd=None
        w.toggle_auto_browser()
        self.assertIn('not open',w.browser_status.text())
    def test_open_login_window_refuses_during_active_job(self):
        w=self.window;w._inapp_poster_active='job-123'
        try:w.open_login_window()
        finally:w._inapp_poster_active=None
        self.assertIn('wait for it',w.browser_status.text())
    def test_verify_login_without_context_reports(self):
        w=self.window;w._login_context=None
        w.verify_login()
        self.assertIn('Press Log into Meta AI first',w.browser_status.text())
    def test_open_tracker_chrome_login_notice_then_meta_home(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        w=self.window
        w.browser_host.hwnd=None
        with patch.object(QMessageBox,'information',return_value=QMessageBox.Ok) as confirm, \
                patch.object(w,'chrome_executable',return_value='C:/fake/chrome.exe'), \
                patch.object(w,'find_tracker_chrome_windows',return_value=[]), \
                patch('subprocess.Popen') as popen:
            w.open_tracker_chrome()
            confirm.assert_called_once()
            self.assertEqual(confirm.call_args[0][1],'Meta.ai Login Required')
            self.assertIn('login to Meta.ai',confirm.call_args[0][2])
        popen.assert_called_once()
        launched_args=popen.call_args[0][0]
        self.assertIn('--window-size=1440,900',launched_args)
        self.assertTrue(str(launched_args[-1]).startswith('https://www.meta.ai'))
    def test_embedded_browser_focus_is_safe_without_window(self):
        w=self.window
        self.assertFalse(w.browser_host.focus_embedded())
        self.assertFalse(w.codmunity_host.focus_embedded())
    def test_tracker_browser_nav_focuses_embedded_window(self):
        from unittest.mock import patch as mock_patch
        w=self.window
        with mock_patch.object(w.browser_host,'focus_embedded',return_value=True) as focus:
            for row in range(w.nav.count()):
                if w.nav.item(row).data(Qt.UserRole)=='Tracker Browser':
                    w.nav.setCurrentRow(row);break
            focus.assert_called_once_with()
    def test_all_buttons_keep_stylesheet_hover_feedback(self):
        from PySide6.QtWidgets import QPushButton
        offenders=[b.text() or b.objectName() for b in self.window.findChildren(QPushButton) if 'background' in b.styleSheet()]
        self.assertEqual(offenders,[])
    def test_friend_tile_hover_highlights_border(self):
        tile=app.FriendTile('f1',lambda pid:None)
        tile.set_selected(False)
        self.assertIn('#292f39',tile.styleSheet())
        tile.enterEvent(None);self.assertIn('#8a93a3',tile.styleSheet())
        tile.leaveEvent(None);self.assertIn('#292f39',tile.styleSheet())
        tile.set_selected(True);self.assertIn('#f97316',tile.styleSheet())
    def test_mode_switch_roundtrip(self):
        self.window.mode.setCurrentText('Squad Night');self.window.mode.setCurrentText('Solo Ops');self.assertIsNone(self.window.capture);self.assertFalse(self.window.tracking)
    def test_roi_verification_defaults_checked_and_hidden(self):self.assertTrue(self.window.verified.isChecked());self.assertTrue(self.window.verified.isHidden())
    def test_browser_failure_is_reported_without_losing_match(self):
        from browser_service import BrowserSession
        statuses=[];b=BrowserSession(app.E.DATA,lambda *a:None,statuses.append)
        from unittest.mock import patch
        with patch('playwright.sync_api.sync_playwright',side_effect=RuntimeError('fixture browser unavailable')):b._run('fixture')
        self.assertIn('browser unavailable',statuses[0].lower())
    def test_browser_download_is_validated_and_bound_to_immutable_match_id(self):
        import shutil
        from PIL import Image
        from browser_service import save_download
        with browser_fixture_dir() as root:
            source=Path(root)/'meta-output.jpg';Image.new('RGB',(640,360),'orange').save(source)
            class Download:
                def save_as(self,target):shutil.copyfile(source,target)
            images=[];statuses=[]
            target=save_download(root,'match-fixed-id',Download(),lambda *args:images.append(args),statuses.append)
            self.assertTrue(Path(target).is_file());self.assertEqual(images[0][0],'match-fixed-id')
            self.assertEqual(Path(target).suffix,'.png');self.assertIn('validated',statuses[0].lower())
            self.assertFalse(Path(target).with_suffix('.download').exists())
    def test_browser_download_rejects_small_image(self):
        import shutil
        from PIL import Image
        from browser_service import save_download
        with browser_fixture_dir() as root:
            source=Path(root)/'too-small.png';Image.new('RGB',(100,100),'black').save(source)
            class Download:
                def save_as(self,target):shutil.copyfile(source,target)
            images=[];statuses=[]
            self.assertIsNone(save_download(root,'small',Download(),lambda *args:images.append(args),statuses.append))
            self.assertFalse(images);self.assertIn('valueerror',statuses[0].lower())
    def test_browser_refuses_to_relink_open_window_to_another_match(self):
        from browser_service import BrowserSession
        statuses=[];b=BrowserSession(app.E.DATA,lambda *a:None,statuses.append)
        class LiveThread:
            def is_alive(self):return True
        b.thread=LiveThread();b.job_id='match-one'
        self.assertFalse(b.open('match-two'));self.assertEqual(b.job_id,'match-one')
        self.assertIn('another match',statuses[0].lower())
    def test_browser_uses_small_isolated_profile_and_retries_browser_launch(self):
        from pathlib import Path
        from types import SimpleNamespace
        from browser_service import BrowserSession
        statuses=[];b=BrowserSession(app.E.DATA,lambda *a:None,statuses.append,retries=1)
        b.stopped.wait=lambda _:False
        calls=[]
        def launch(profile,**kwargs):
            kwargs['profile']=profile;calls.append(kwargs)
            if len(calls)<3:raise RuntimeError('fixture')
            return object()
        with browser_fixture_dir() as root:
            result=b._launch(SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch)),Path(root)/'browser-profile')
            self.assertIsNotNone(result);self.assertEqual(len(calls),3)
            self.assertEqual(calls[0]['channel'],'msedge');self.assertEqual(calls[1]['channel'],'chrome')
            self.assertEqual(calls[2]['channel'],'msedge');self.assertFalse(calls[2]['headless'])
            self.assertTrue(calls[2]['accept_downloads']);self.assertEqual(calls[2]['viewport'],{'width':900,'height':700})
            self.assertEqual(calls[2]['args'],['--window-size=940,780'])
    def test_browser_download_timeout_reports_without_closing_manual_session_early(self):
        from types import SimpleNamespace
        from browser_service import BrowserSession
        from unittest.mock import patch
        statuses=[];b=BrowserSession(app.E.DATA,lambda *a:None,statuses.append,download_timeout=300,retries=0)
        class Page:
            def __init__(self):self.handlers={};self.navigations=[]
            def on(self,name,callback):self.handlers[name]=callback
            def goto(self,url,**kwargs):self.navigations.append((url,kwargs))
            def wait_for_timeout(self,ms):self.wait_ms=ms;b.stopped.set()
        class Context:
            def __init__(self):self.pages=[Page()];self.handlers={};self.closed=False
            def new_page(self):return self.pages[0]
            def on(self,name,callback):self.handlers[name]=callback
            def close(self):self.closed=True
        context=Context();manager=SimpleNamespace(__enter__=lambda s:SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=lambda *a,**k:context)),__exit__=lambda *a:None)
        class Manager:
            def __enter__(self):return manager.__enter__(self)
            def __exit__(self,*args):return manager.__exit__(self,*args)
        with browser_fixture_dir() as root,patch('playwright.sync_api.sync_playwright',return_value=Manager()),patch('browser_service.time.monotonic',side_effect=[10,311]):
            b.directory=Path(root);b._run('timeout-match')
        self.assertEqual(context.pages[0].navigations[0][0],'https://www.meta.ai/')
        self.assertTrue(context.closed);self.assertIn('wait timeout', '\n'.join(statuses).lower())
    def wait_until(self,fn):
        end=time.monotonic()+10
        while time.monotonic()<end and not fn():self.qt.processEvents();time.sleep(.01)
        self.assertTrue(fn())
    def test_squad_capture_prompt_overlay_post_end_to_end(self):
        from PIL import Image
        import numpy as np
        from unittest.mock import patch
        w=self.window;w.mode.setCurrentText('Squad Night');w.auto_post.setChecked(False);mid,_=w.store.add({'matchId':'squad-test-'+uuid.uuid4().hex,'modeKey':'resurgence','result':'win','stats':{'kills':12}})
        w.refresh_history();w.link_match.setCurrentIndex(w.link_match.findData(mid));w.last_frame=np.array(Image.new('RGB',(1200,500),'navy'))
        w.ocr_text.setPlainText('PLAYER  KILLS  DAMAGE  SCORE\nVintendo  12  3400  4500\nConcha  8  2000  3200');w.parse_ocr();w.verified.setChecked(True);w.save_capture()
        job=w.store.get(mid);self.assertTrue(job['capture']);self.assertEqual(len(json.loads(job['players'])),2);self.assertIn('16:9',job['prompt'])
        background=app.E.DATA/('fixture-art-'+uuid.uuid4().hex+'.png');Image.new('RGB',(1600,900),'navy').save(background);w.received_art(mid,str(background));w.render_overlay();self.wait_until(lambda:w.store.get(mid)['status']=='ready')
        calls=[];w.webhook.setText('https://discord.com/api/webhooks/123/fixture');w.notice=lambda _:None;w.ask=lambda _:True
        with patch('engine.send_discord',side_effect=lambda *args:(calls.append(args) or 'fixture-message')):
            w.deliver(mid);self.wait_until(lambda:w.store.get(mid)['status']=='delivered');w.deliver(mid)
        self.assertEqual(len(calls),1);self.assertIn('kills: 12',calls[0][2]);self.assertEqual(w.store.get(mid)['message_id'],'fixture-message')
    def test_history_and_dashboard_show_codmunity_end_game_fields(self):
        w=self.window
        mid,_=w.store.add({'matchId':'endgame-'+uuid.uuid4().hex,'game':'warzone','mode':'battle_royale','playedAt':'2026-10-01T08:00:00Z','result':'win','stats':{'kills':17,'eliminations':19,'damage':6878,'deaths':2,'assists':3,'placement':1}})
        w.refresh_history()
        self.assertEqual(w.history.columnCount(),12)
        headers=[w.history.horizontalHeaderItem(i).text() for i in range(w.history.columnCount())]
        self.assertIn('DAMAGE',headers);self.assertIn('ELIMS',headers)
        row=next(i for i in range(w.history.rowCount()) if w.history.item(i,0).toolTip()==mid)
        self.assertEqual(w.history.item(row,headers.index('DAMAGE')).text(),'6878')
        self.assertEqual(w.history.item(row,headers.index('ELIMS')).text(),'19')
        self.assertEqual(w.center_tables['records'].columnCount(),12)
        rec_headers=[w.center_tables['records'].horizontalHeaderItem(i).text() for i in range(w.center_tables['records'].columnCount())]
        rec_row=next(i for i in range(w.center_tables['records'].rowCount()) if w.center_tables['records'].item(i,0).toolTip()==mid)
        self.assertEqual(w.center_tables['records'].item(rec_row,rec_headers.index('DAMAGE')).text(),'6878')
        self.assertEqual(w.center_tables['records'].item(rec_row,rec_headers.index('ELIMS')).text(),'19')
        self.assertEqual(w.dashboard_metrics['games'].text(),str(len(w.center_rows['records'])))
        self.assertRegex(w.history_summary['kills_per_game'][1].text(),r'Best \d+ kills')
    def test_match_center_tabs_share_tables_and_count(self):
        w=self.window
        tag=uuid.uuid4().hex
        mid_post,_=w.store.add({'matchId':'post-'+tag,'game':'warzone','mode':'battle_royale','playedAt':'2026-10-02T08:00:00Z','result':'win','stats':{'kills':5,'eliminations':5,'damage':2500,'deaths':1,'assists':0}},'delivered')
        mid_rev,_=w.store.add({'matchId':'rev-'+tag,'game':'warzone','mode':'battle_royale','playedAt':'2026-10-03T08:00:00Z','result':'loss','stats':{'kills':2,'eliminations':2,'damage':900,'deaths':4,'assists':1}},'needs_review')
        w.refresh_history()
        posts=w.center_rows['posts'];review=w.center_rows['review']
        self.assertTrue(any(j['id']==mid_post for j in posts))
        self.assertTrue(any(j['id']==mid_rev for j in review))
        self.assertEqual(w.center_tables['posts'].rowCount(),len(posts))
        self.assertEqual(w.center_tables['review'].rowCount(),len(review))
        w.center_tabs.setCurrentIndex(1)
        self.assertEqual(w.center_count.text(),f"{len(posts)} of {len(w.center_rows['records'])} records")
        w.center_tabs.setCurrentIndex(2)
        self.assertEqual(w.center_count.text(),f"{len(review)} of {len(w.center_rows['records'])} records")
        w.center_tabs.setCurrentIndex(0)
        self.assertEqual(w.center_count.text(),f"{len(w.center_rows['records'])} of {len(w.center_rows['records'])} records")
    def test_dashboard_games_cards_mirror_history_summary(self):
        w=self.window
        w.center_tabs.setCurrentIndex(0)
        rows=w.center_rows['records']
        summary=w._match_summary(rows)
        self.assertEqual(w.dashboard_metrics['games'].text(),str(summary['games']))
        self.assertEqual(w.dashboard_metrics['win_rate'].text(),str(summary['win_rate']))
        self.assertEqual(w.dashboard_metrics['kd'].text(),str(summary['kd']))
        self.assertEqual(w.dashboard_metrics['kills_per_game'].text(),str(summary['kills_per_game']))
    def test_match_center_double_click_opens_studio(self):
        w=self.window
        mid,_=w.store.add({'matchId':'open-'+uuid.uuid4().hex,'game':'warzone','mode':'battle_royale','playedAt':'2026-10-04T08:00:00Z','result':'win','stats':{'kills':9,'eliminations':9,'damage':4100,'deaths':0,'assists':2}})
        w.refresh_history()
        row=next(i for i,r in enumerate(w.center_rows['records']) if r['id']==mid)
        calls=[];real=w.load_studio;w.load_studio=lambda job_id:calls.append(job_id)
        try:
            w.open_center_match('records',row)
            self.assertEqual(calls,[mid]);self.assertEqual(w.nav.currentRow(),5)
        finally:
            w.load_studio=real;w.nav.setCurrentRow(0)
    def test_section_banners_centered(self):
        w=self.window
        for name in app.PAGE_BANNERS:
            _page,layout=w.pages[name]
            title=layout.itemAt(0).widget()
            self.assertEqual(title.objectName(),'pageBanner',name)
            self.assertTrue(bool(layout.itemAt(0).alignment()&Qt.AlignHCenter),name)
            self.assertFalse(title.pixmap().isNull(),name)
    def test_settings_nav_and_logs_tab(self):
        w=self.window
        names=[w.nav.item(r).data(Qt.UserRole) for r in range(w.nav.count()) if not w.nav.item(r).isHidden()]
        self.assertIn('Settings',names)
        self.assertNotIn('Application Settings',names);self.assertNotIn('Activity Logs',names)
        tabs=[w.settings_tabs.tabText(i) for i in range(w.settings_tabs.count())]
        self.assertIn('Hotkeys & Folders',tabs);self.assertIn('Logs',tabs)
        self.assertEqual(w.settings_tabs.tabText(w.settings_tabs.indexOf(w.logs.parentWidget())),'Logs')
    def test_worker_log_opens_settings_logs_tab(self):
        w=self.window
        w.worker_log()
        settings_row=next(r for r in range(w.nav.count()) if w.nav.item(r).data(Qt.UserRole)=='Settings')
        self.assertEqual(w.nav.currentRow(),settings_row)
        self.assertEqual(w.settings_tabs.tabText(w.settings_tabs.currentIndex()),'Logs')
    def test_hotkey_and_folder_overrides_persist(self):
        import tempfile
        w=self.window
        keys=('scoreboard_hotkey_vk','scoreboard_hotkey_mod','screenshot_hotkey_vk','screenshot_hotkey_mod','screenshots_dir','videos_dir')
        original={k:w.settings.values.get(k) for k in keys}
        try:
            self.assertEqual((w.scoreboard_hotkey_edit.vk,w.scoreboard_hotkey_edit.mod),w.hotkey_setting('scoreboard_hotkey_vk','scoreboard_hotkey_mod',app.DEFAULT_SCOREBOARD_HOTKEY))
            with tempfile.TemporaryDirectory() as tmp:
                shots=str(Path(tmp)/'Shots');vids=str(Path(tmp)/'Vids')
                w.scoreboard_hotkey_edit.set_hotkey(0x79,0x0002)
                w.screenshot_hotkey_edit.set_hotkey(0x7A,0x0006)
                w.screenshots_dir_edit.setText(shots);w.videos_dir_edit.setText(vids)
                w.save_settings()
                self.assertEqual(w.settings.values['scoreboard_hotkey_vk'],0x79)
                self.assertEqual(w.settings.values['screenshot_hotkey_mod'],0x0006)
                self.assertEqual(w.screenshots_dir(),Path(shots));self.assertEqual(w.videos_dir(),Path(vids))
                self.assertTrue(Path(shots).is_dir() and Path(vids).is_dir())
                self.assertIn('F10',w.hotkey_hint.text())
        finally:
            for k,v in original.items():
                if v is None:w.settings.values.pop(k,None)
                else:w.settings.values[k]=v
            w.settings.save();w.unregister_global_hotkeys();w.register_global_hotkeys();w.refresh_hotkey_labels()
            w.screenshots_dir_edit.setText(original['screenshots_dir'] or '');w.videos_dir_edit.setText(original['videos_dir'] or '')
    def test_discord_channel_labels_persist(self):
        w=self.window
        original={k:w.settings.values.get(k) for k in ('webhook_channel','cinematic_webhook_channel')}
        try:
            w.webhook_channel.setText('#match-clips');w.cinematic_webhook_channel.setText('#cod-cinema')
            w.save_settings()
            self.assertEqual(w.settings.values['webhook_channel'],'#match-clips')
            self.assertEqual(w.settings.values['cinematic_webhook_channel'],'#cod-cinema')
        finally:
            for k,v in original.items():
                if v is None:w.settings.values.pop(k,None)
                else:w.settings.values[k]=v
            w.settings.save()
            w.webhook_channel.setText(original['webhook_channel'] or '');w.cinematic_webhook_channel.setText(original['cinematic_webhook_channel'] or '')
    def test_exit_button_confirms_then_shuts_down(self):
        w=self.window
        self.assertEqual(w.app_tabs.cornerWidget(Qt.TopRightCorner),w.exit_button)
        w.ask=lambda *args,**kwargs:False;w.request_exit();self.assertFalse(w.closing)
        w.ask=lambda *args,**kwargs:True;w.request_exit();self.assertTrue(w.closing)
    def test_match_history_button_hidden_but_page_kept(self):
        w=self.window
        found=[w.nav.item(r) for r in range(w.nav.count()) if w.nav.item(r).data(Qt.UserRole)=='Match History']
        self.assertEqual(len(found),1);self.assertTrue(found[0].isHidden())
        self.assertTrue(hasattr(w,'history'));self.assertTrue(hasattr(w,'refresh_match_history_view'))
    def test_save_capture_combines_ocr_placement_and_manual_result(self):
        import numpy as np
        from PIL import Image
        w=self.window;w.mode.setCurrentText('Squad Night')
        w.last_frame=np.array(Image.new('RGB',(1200,500),'navy'))
        w.ocr_text.setPlainText('PLAYER  KILLS  PLACEMENT\nVintendo  12  1\nConcha  8  1');w.parse_ocr();w.verified.setChecked(True)
        w.link_match.setCurrentIndex(0);self.assertIsNone(w.link_match.currentData())
        w.save_capture()
        job=w.store.get(w.active_id);context=app.E.extract_match_context(json.loads(job['payload']),job.get('ocr',''))
        self.assertEqual(context['result'],'Victory');self.assertEqual(context['placement'],'1st place')
        w.last_frame=np.array(Image.new('RGB',(1200,500),'maroon'))
        w.ocr_text.setPlainText('PLAYER  KILLS\nVintendo  12');w.parse_ocr();w.verified.setChecked(True)
        w.link_match.setCurrentIndex(0)
        w.result_override.setCurrentIndex(w.result_override.findData('loss'))
        w.save_capture()
        forced=w.store.get(w.active_id);forced_context=app.E.extract_match_context(json.loads(forced['payload']),forced.get('ocr',''))
        self.assertEqual(forced_context['result'],'Defeat')
    def test_save_capture_asks_win_or_lose_when_result_unknown(self):
        import numpy as np
        from PIL import Image
        w=self.window;w.mode.setCurrentText('Squad Night')
        w.last_frame=np.array(Image.new('RGB',(1200,500),'teal'))
        w.ocr_text.setPlainText('PLAYER  KILLS\nVintendo  12');w.parse_ocr();w.verified.setChecked(True)
        w.link_match.setCurrentIndex(0)
        w.ask_match_result=lambda:'win'
        w.save_capture()
        job=w.store.get(w.active_id)
        self.assertEqual(app.E.extract_match_context(json.loads(job['payload']),job.get('ocr',''))['result'],'Victory')
        w.last_frame=np.array(Image.new('RGB',(1200,500),'olive'))
        w.ocr_text.setPlainText('PLAYER  KILLS\nVintendo  5');w.parse_ocr();w.verified.setChecked(True)
        w.link_match.setCurrentIndex(0);before=w.active_id
        w.ask_match_result=lambda:None;rows_before=len(w.store.history())
        w.save_capture()
        self.assertEqual(w.active_id,before)
        self.assertEqual(len(w.store.history()),rows_before)
        self.assertEqual(w.store.get(w.active_id)['capture'],job['capture'])
    def test_download_auto_overlays_and_posts_once_when_enabled(self):
        import shutil
        from PIL import Image
        from unittest.mock import patch
        from browser_service import save_download
        w=self.window;match={'matchId':'auto-download-'+uuid.uuid4().hex,'modeKey':'resurgence','result':'win'}
        mid,_=w.store.add(match);w.store.update(mid,players=json.dumps([dict(name='Alpha',verified=True,stats={'kills':9,'damage':2100})]))
        w.auto_post.setChecked(True);w.webhook.setText('https://discord.com/api/webhooks/123/fixture');w.notice=lambda _:None
        with browser_fixture_dir() as root:
            source=Path(root)/'meta-result.png';Image.new('RGB',(640,360),'navy').save(source)
            class Download:
                def save_as(self,target):shutil.copyfile(source,target)
            calls=[]
            with patch('engine.send_discord',side_effect=lambda *args:(calls.append(args) or 'auto-message')):
                save_download(root,mid,Download(),w.received_art,w.log)
                self.wait_until(lambda:w.store.get(mid)['status']=='delivered')
            self.assertEqual(len(calls),1);self.assertIn('kills: 9',calls[0][2]);self.assertTrue(Path(calls[0][1]).is_file())
    def test_headshots_only_for_identified_consenting_players(self):
        from PIL import Image
        from unittest.mock import patch
        w=self.window;mid,_=w.store.add({'matchId':'photo-test-'+uuid.uuid4().hex,'modeKey':'resurgence','result':'win'});w.active_id=mid
        w.store.update(mid,players=json.dumps([dict(name='Alpha',verified=True,stats={'kills':8}),dict(name='Beta',verified=True,stats={'kills':3}),dict(name='NotIdentified',verified=False,stats={'kills':900})]))
        photo=app.E.DATA/('fixture-head-'+uuid.uuid4().hex+'.jpg');Image.new('RGB',(400,400),'gray').save(photo)
        roster=[dict(id='alpha',name='Alpha',photo=str(photo),consent_meta=True,enabled=True),dict(id='beta',name='Beta',photo=str(photo),consent_meta=False,enabled=True),dict(id='other',name='NotIdentified',photo=str(photo),consent_meta=True,enabled=True)]
        w.notice=lambda _:None
        with patch.object(w.store,'roster',return_value=roster),patch('os.startfile'):
            w.load_studio(mid);self.assertTrue(w.prompt.toPlainText());w._start_poster_automation=lambda *args:False;w.prepare_artwork()
        self.assertIn('prepared',w.job_status.text().lower())
        handoff=app.E.DATA/'handoff'/app.E.disk_id(mid)
        self.addCleanup(shutil.rmtree,handoff,True)
        assignments=app.E.read_json(handoff/'photo-assignments.json')
        by_user={item['username']:item for item in assignments}
        self.assertEqual(by_user['Alpha']['status'],'ready');self.assertTrue(by_user['Alpha']['filename'])
        self.assertEqual(by_user['Beta']['status'],'not_consented');self.assertIn('not consented',by_user['Beta']['reason'])
    def test_prepare_artwork_prefers_automation_over_folder(self):
        from PIL import Image
        from unittest.mock import patch
        w=self.window;mid,_=w.store.add({'matchId':'auto-poster-'+uuid.uuid4().hex,'modeKey':'resurgence','result':'win'});w.active_id=mid
        w.store.update(mid,players=json.dumps([dict(name='Alpha',verified=True,stats={'kills':8})]))
        photo=app.E.DATA/('fixture-head-'+uuid.uuid4().hex+'.jpg');Image.new('RGB',(400,400),'gray').save(photo)
        roster=[dict(id='alpha',name='Alpha',photo=str(photo),consent_meta=True,enabled=True)]
        w.notice=lambda _:None
        calls=[]
        with patch.object(w.store,'roster',return_value=roster),patch('os.startfile') as opened:
            w.load_studio(mid)
            w._start_poster_automation=lambda *args:(calls.append(args) or True)
            w.prepare_artwork()
        self.assertFalse(opened.called)
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][0],mid)
        self.assertTrue(calls[0][2])
        for name in calls[0][2]:
            self.assertTrue(Path(calls[0][1],name).is_file())
        self.assertIn('automatically',w.job_status.text().lower())
        self.addCleanup(w.store.update,mid,gen_state='idle',error='')
        self.addCleanup(shutil.rmtree,Path(calls[0][1]),True)
    def test_prepare_artwork_opens_folder_when_automation_unavailable(self):
        from PIL import Image
        from unittest.mock import patch
        w=self.window;mid,_=w.store.add({'matchId':'manual-poster-'+uuid.uuid4().hex,'modeKey':'resurgence','result':'win'});w.active_id=mid
        w.store.update(mid,players=json.dumps([dict(name='Alpha',verified=True,stats={'kills':8})]))
        photo=app.E.DATA/('fixture-head-'+uuid.uuid4().hex+'.jpg');Image.new('RGB',(400,400),'gray').save(photo)
        roster=[dict(id='alpha',name='Alpha',photo=str(photo),consent_meta=True,enabled=True)]
        w.notice=lambda _:None
        with patch.object(w.store,'roster',return_value=roster),patch('os.startfile') as opened:
            w.load_studio(mid)
            w._start_poster_automation=lambda *args:False
            w.prepare_artwork()
        self.assertTrue(opened.called)
        folder=opened.call_args[0][0]
        self.assertTrue(Path(folder,'prompt.txt').is_file())
        self.assertIn('prepared',w.job_status.text().lower())
        self.addCleanup(shutil.rmtree,Path(folder),True)
    def test_full_auto_pump_launches_queued_job(self):
        from PIL import Image
        from unittest.mock import patch
        w=self.window;mid,_=w.store.add({'matchId':'fullauto-'+uuid.uuid4().hex,'modeKey':'resurgence','result':'win'})
        w.store.update(mid,players=json.dumps([dict(name='Alpha',verified=True,stats={'kills':8})]),gen_state='queued')
        photo=app.E.DATA/('fixture-head-'+uuid.uuid4().hex+'.jpg');Image.new('RGB',(400,400),'gray').save(photo)
        roster=[dict(id='alpha',name='Alpha',photo=str(photo),consent_meta=True,enabled=True)]
        w.notice=lambda _:None
        calls=[]
        with patch.object(w.store,'roster',return_value=roster):
            w._start_poster_automation=lambda *args:(calls.append(args) or True)
            w.webhook.setText('https://discord.com/api/webhooks/123/fixture');w.full_auto.blockSignals(True);w.full_auto.setChecked(True);w.full_auto.blockSignals(False)
            calls.clear()
            w._pump_full_auto()
        self.assertEqual(len(calls),1);self.assertEqual(calls[0][0],mid)
        self.addCleanup(shutil.rmtree,Path(calls[0][1]),True)
    def test_full_auto_holds_without_webhook(self):
        w=self.window;mid,_=w.store.add({'matchId':'held-'+uuid.uuid4().hex,'modeKey':'resurgence','result':'win'})
        w.store.update(mid,players=json.dumps([dict(name='Alpha',verified=True,stats={'kills':8})]),gen_state='queued')
        w.notice=lambda _:None
        calls=[]
        w._start_poster_automation=lambda *args:(calls.append(args) or True)
        w.webhook.setText('');w.full_auto.blockSignals(True);w.full_auto.setChecked(True);w.full_auto.blockSignals(False)
        calls.clear()
        w._pump_full_auto()
        self.assertEqual(calls,[])
        self.assertEqual(w.store.get(mid)['gen_state'],'queued')
        self.addCleanup(w.store.update,mid,gen_state='idle',error='')
    def test_full_auto_parks_unpreparable_job(self):
        w=self.window;mid,_=w.store.add({'matchId':'unprepped-'+uuid.uuid4().hex,'modeKey':'resurgence','result':'win'})
        w.store.update(mid,players=json.dumps([dict(name='Nobody',verified=False,stats={'kills':1})]),gen_state='queued')
        w.notice=lambda _:None
        w._start_poster_automation=lambda *args:True
        w.webhook.setText('https://discord.com/api/webhooks/123/fixture');w.full_auto.blockSignals(True);w.full_auto.setChecked(True);w.full_auto.blockSignals(False)
        w._pump_full_auto()
        row=w.store.get(mid);self.assertEqual(row['gen_state'],'idle');self.assertTrue(row['error'])
    def test_finish_chains_pump_and_fail_parks(self):
        from PIL import Image
        from unittest.mock import patch
        w=self.window;mid,_=w.store.add({'matchId':'chained-'+uuid.uuid4().hex,'modeKey':'resurgence','result':'win'})
        w.store.update(mid,players=json.dumps([]))
        saved=app.E.DATA/('chained-'+uuid.uuid4().hex+'.png');Image.new('RGB',(640,360),'navy').save(saved)
        w.notice=lambda _:None
        pumped=[]
        w._pump_full_auto=lambda:pumped.append(True)
        w._finish_inapp_poster(mid,str(saved))
        self.assertEqual(pumped,[True])
        pumped.clear()
        with patch('os.startfile') as opened:
            w._poster_manual=False
            w._fail_inapp_poster(mid,'generation','boom')
        self.assertEqual(pumped,[True]);self.assertFalse(opened.called)
        row=w.store.get(mid);self.assertEqual(row['gen_state'],'idle')
    def test_one_click_prepare_picks_newest_codmunity_match_without_scoreboard(self):
        from PIL import Image
        from unittest.mock import patch
        import tempfile
        w=self.window;w.active_id=None
        tmp=tempfile.mkdtemp(prefix='oneclick-store-')
        w.store=app.E.Store(tmp+'/history.sqlite3')
        self.addCleanup(shutil.rmtree,tmp,True)
        record={'matchId':'oneclick-'+uuid.uuid4().hex,'integration_source':'CODMunity','modeKey':'resurgence','playedAt':'2026-11-08T20:00:00Z','result':'win','stats':{'kills':19,'damage':6878}}
        mid,_=w.store.add(record)
        photo=app.E.DATA/('fixture-head-'+uuid.uuid4().hex+'.jpg');Image.new('RGB',(400,400),'gray').save(photo)
        w.store.save_player({'id':'vintendo','name':'Vintendo','photo':str(photo),'consent_meta':True,'enabled':True,'aliases':[]})
        w.notice=lambda _:None;w.settings.values['player']='Vintendo'
        w._start_poster_automation=lambda *args:False
        with patch('os.startfile'):w.prepare_artwork()
        self.addCleanup(shutil.rmtree,app.E.DATA/'handoff'/app.E.disk_id(mid),True)
        self.assertEqual(w.active_id,mid)
        row=w.store.get(mid);self.assertEqual(row['gen_state'],'prepared');self.assertTrue(row['prompt'])
        self.assertTrue(w.prompt.toPlainText())
    def test_final_poster_keeps_all_verified_rows_and_exact_mode_stats(self):
        from PIL import Image,ImageChops
        players=[dict(name=f'Squad{i}',verified=True,stats={'kills':10-i,'damage':2500+i,'assists':i,'eliminations':10-i,'deaths':i}) for i in range(6)]
        source=Path(app.E.DATA)/('full-squad-source-'+uuid.uuid4().hex+'.png');Image.new('RGB',(1600,900),'navy').save(source)
        target=Path(app.E.DATA)/('full-squad-poster-'+uuid.uuid4().hex+'.jpg')
        match={'game':'warzone','modeKey':'resurgence','result':'win','placement':2}
        app.E.render_poster(source,target,match,players)
        with Image.open(target) as poster:
            self.assertEqual(poster.size,(1600,900))
            self.assertIsNotNone(ImageChops.difference(poster.crop((0,790,760,830)),Image.new('RGB',(760,40),'navy')).getbbox())
        recap=app.E.recap(match,players)
        self.assertIn('VICTORY',recap);self.assertIn('Warzone',recap);self.assertIn('Resurgence',recap);self.assertIn('eliminations: 10',recap)
        self.assertIn('Squad5',recap);self.assertIn('deaths: 5',recap)
    def test_first_start_baselines_before_solo_worker(self):
        from unittest.mock import patch,MagicMock
        w=self.window;w.mode.setCurrentText('Solo Ops');w.webhook.setText('https://discord.com/api/webhooks/123/fixture')
        record={'matchId':'baseline-'+uuid.uuid4().hex,'modeKey':'kill_confirmed','playedAt':uuid.uuid4().hex,'result':'win'}
        process=MagicMock();process.poll.return_value=0
        def spawn(*args,**kwargs):
            self.assertEqual(w.store.get(app.E.key_for(record))['status'],'baseline')
            return process
        with patch.object(w.store,'meta',return_value=False),patch('engine.load_matches',return_value=(app.E.DATA,[record])),patch('psutil.process_iter',return_value=[]),patch('subprocess.Popen',side_effect=spawn):w.start()
        self.assertTrue(w.tracking);self.assertEqual(w.store.get(app.E.key_for(record))['status'],'baseline')
    def test_smoke_run_bypasses_single_instance_gate(self):
        self.assertTrue(app.is_smoke_run(['app.py', '--smoke-test'], {}))
        self.assertTrue(app.is_smoke_run(['app.py'], {'VINTENDO_SMOKE_TEST': '1'}))
        self.assertFalse(app.is_smoke_run(['app.py'], {}))
    def test_browser_close_joins_worker_thread(self):
        import threading
        from browser_service import BrowserSession
        release = threading.Event()
        class SlowThread:
            def __init__(self):self.joined = False
            def is_alive(self):return not self.joined
            def join(self, timeout=None):self.joined = True;release.set()
        statuses = []
        session = BrowserSession(app.E.DATA, lambda *a: None, statuses.append)
        slow = SlowThread()
        session.thread = slow
        session.close(timeout=2)
        self.assertTrue(session.stopped.is_set())
        self.assertTrue(slow.joined)

    def test_visible_cinematic_rows_hides_finished_jobs(self):
        rows=[{'id':'a','status':'queued','video':''},{'id':'b','status':'running','video':''},
              {'id':'c','status':'completed','video':''},{'id':'d','status':'failed','video':'','error':'boom'}]
        self.assertEqual([r['id'] for r in app.visible_cinematic_rows(rows)],['a','b'])
        self.assertEqual([r['id'] for r in app.visible_cinematic_rows(rows,selected_id='d')],['a','b','d'])
    def test_visible_cinematic_rows_keeps_postable_completed_video(self):
        folder=Path(app.E.DATA)/'Cinematics';folder.mkdir(parents=True,exist_ok=True)
        cid='cin-vis-'+uuid.uuid4().hex
        good=folder/(cid+'.mp4');good.write_bytes(b'\x00\x00\x00\x18ftyp'+b'0'*1024)
        try:
            rows=[{'id':cid,'status':'completed','video':str(good)},{'id':'old','status':'completed','video':''}]
            self.assertEqual([r['id'] for r in app.visible_cinematic_rows(rows)],[cid])
        finally:good.unlink(missing_ok=True)
    def test_refresh_results_button_exists(self):
        from PySide6.QtWidgets import QPushButton
        found=[b for b in self.window.findChildren(QPushButton) if b.text()=='Refresh']
        self.assertTrue(found)
    def test_cinematic_discord_post_retries_transient_tls_error(self):
        import ssl,requests
        calls=[];urls=[]
        class FakeResponse:status_code=200
        def fake_post(url,*args,**kwargs):
            calls.append(1);urls.append(url)
            if len(calls)==1:raise ssl.SSLError('SSL: SSL3_ALERT_BAD_RECORD_MAC')
            return FakeResponse()
        real_post=requests.post;requests.post=fake_post
        try:
            target=Path(app.E.DATA)/('cin-post-'+uuid.uuid4().hex+'.mp4');target.write_bytes(b'0'*16)
            try:status=app.post_file_to_discord_webhook('https://discord.com/api/webhooks/1/abc',str(target))
            finally:target.unlink(missing_ok=True)
        finally:requests.post=real_post
        self.assertEqual(status,200);self.assertEqual(len(calls),2)
        self.assertIn('wait=true',urls[-1])
    def test_cinematic_discord_post_gives_up_after_retries(self):
        import requests
        calls=[]
        def fake_post(*args,**kwargs):calls.append(1);raise ConnectionError('boom')
        real_post=requests.post;requests.post=fake_post
        try:
            target=Path(app.E.DATA)/('cin-post-'+uuid.uuid4().hex+'.mp4');target.write_bytes(b'0'*16)
            try:
                with self.assertRaises(ConnectionError):app.post_file_to_discord_webhook('https://discord.com/api/webhooks/1/abc',str(target))
            finally:target.unlink(missing_ok=True)
        finally:requests.post=real_post
        self.assertEqual(len(calls),3)
    def test_delete_selected_cinematic_video_removes_file_and_clears_row(self):
        folder=Path(app.E.DATA)/'Cinematics';folder.mkdir(parents=True,exist_ok=True)
        cid='cin-del-'+uuid.uuid4().hex
        shot=folder/('in_'+cid+'.png');shot.write_bytes(b'\x89PNG\r\n\x1a\n'+b'0'*64)
        self.assertTrue(self.window.store.create_cinematic(cid,'shot.png',shot,'prompt'))
        good=folder/(cid+'.mp4');good.write_bytes(b'\x00\x00\x00\x18ftyp'+b'0'*1024)
        self.window.store.update_cinematic(cid,status='completed',video=str(good))
        self.window.refresh_cinematic_results()
        found=[self.window.cinematic_results.item(i) for i in range(self.window.cinematic_results.count())
              if self.window.cinematic_results.item(i).data(Qt.UserRole)==cid]
        self.assertTrue(found);self.window.cinematic_results.setCurrentItem(found[0])
        self.window.ask=lambda *args,**kwargs:True;self.window.confirm_cinematic_delete=lambda:True
        self.window.delete_selected_cinematic_video()
        self.assertFalse(good.exists())
        self.assertEqual(self.window.store.get_cinematic(cid)['video'],'')
        self.assertIn('Deleted',self.window.cinematic_status.text())
        shot.unlink(missing_ok=True)
    def test_delete_releases_preview_player_before_removing_file(self):
        folder=Path(app.E.DATA)/'Cinematics';folder.mkdir(parents=True,exist_ok=True)
        cid='cin-lock-'+uuid.uuid4().hex
        shot=folder/('in_'+cid+'.png');shot.write_bytes(b'\x89PNG\r\n\x1a\n'+b'0'*64)
        self.assertTrue(self.window.store.create_cinematic(cid,'shot.png',shot,'prompt'))
        good=folder/(cid+'.mp4');good.write_bytes(b'\x00\x00\x00\x18ftyp'+b'0'*1024)
        self.window.store.update_cinematic(cid,status='completed',video=str(good))
        self.window.refresh_cinematic_results()
        found=[self.window.cinematic_results.item(i) for i in range(self.window.cinematic_results.count())
              if self.window.cinematic_results.item(i).data(Qt.UserRole)==cid]
        self.assertTrue(found);self.window.cinematic_results.setCurrentItem(found[0])
        self.window.cinematic_player.setSource(app.QUrl.fromLocalFile(str(good)))
        self.assertFalse(self.window.cinematic_player.source().isEmpty())
        real_unlink=Path.unlink
        def guard_unlink(target,missing_ok=False):
            if target==good and not self.window.cinematic_player.source().isEmpty():raise PermissionError(13,'file in use',str(target))
            return real_unlink(target,missing_ok=missing_ok)
        from unittest.mock import patch
        self.window.confirm_cinematic_delete=lambda:True
        with patch.object(Path,'unlink',guard_unlink):
            self.window.delete_selected_cinematic_video()
        self.assertFalse(good.exists())
        self.assertTrue(self.window.cinematic_player.source().isEmpty())
        shot.unlink(missing_ok=True)
    def test_open_tracker_chrome_auto_embeds_new_window(self):
        from unittest.mock import patch
        w=self.window;attached=[]
        w.attach_browser_window=lambda hwnd:attached.append(hwnd)
        with patch.object(w,'find_tracker_chrome_windows',return_value=[]),patch('subprocess.Popen') as popen,patch('app.QMessageBox.information'):
            w.open_tracker_chrome()
            self.assertTrue(w.browser_poll.isActive());popen.assert_called_once()
        with patch.object(w,'find_tracker_chrome_windows',return_value=[12345]):
            w.try_attach_tracker_chrome()
        self.assertEqual(attached,[12345]);self.assertFalse(w.browser_poll.isActive())
    def test_open_tracker_chrome_times_out_to_manual_embed(self):
        from unittest.mock import patch
        w=self.window;attached=[]
        w.attach_browser_window=lambda hwnd:attached.append(hwnd)
        with patch.object(w,'find_tracker_chrome_windows',return_value=[]),patch('subprocess.Popen'),patch('app.QMessageBox.information'):
            w.open_tracker_chrome()
            for _ in range(30):w.try_attach_tracker_chrome()
        self.assertEqual(attached,[]);self.assertFalse(w.browser_poll.isActive())
        self.assertIn('Embed',w.browser_status.text())
        with patch.object(w,'find_tracker_chrome_windows',return_value=[7]),patch('subprocess.Popen'),patch('app.QMessageBox.information'):
            w.open_tracker_chrome()
            for _ in range(30):w.try_attach_tracker_chrome()
        self.assertEqual(attached,[7])
    def test_section_headlines_render_banner_graphics(self):
        from PySide6.QtWidgets import QLabel
        w=self.window
        for name,filename in app.PAGE_BANNERS.items():
            self.assertIn(name,w.pages)
            labels=[c for c in w.pages[name][0].findChildren(QLabel) if c.objectName()=='pageBanner']
            self.assertEqual(len(labels),1,f'{name} should show exactly one banner')
            self.assertEqual(labels[0].property('bannerFile'),filename)
            pixmap=labels[0].pixmap()
            self.assertIsNotNone(pixmap);self.assertFalse(pixmap.isNull())
            self.assertLessEqual(pixmap.width(),520);self.assertLessEqual(pixmap.height(),170)
            self.assertGreaterEqual(pixmap.height(),100)
        for name in ('Solo Ops','Squad Night','Discord Settings'):
            titles=[c for c in w.pages[name][0].findChildren(QLabel) if c.objectName()=='title']
            self.assertEqual(len(titles),1);self.assertEqual(titles[0].text(),name)
    def test_nav_buttons_use_text_without_operations_branding(self):
        from PySide6.QtWidgets import QLabel
        w=self.window
        for row in range(w.nav.count()):
            item=w.nav.item(row);name=item.data(Qt.UserRole)
            self.assertIn(name,item.text())
            self.assertIsNone(w.nav.itemWidget(item))
        self.assertEqual(w.findChildren(QLabel,'eyebrow'),[])
        self.assertEqual(w.findChildren(QLabel,'sidebarFooter'),[])
    def test_roster_redesign_save_search_match_and_delete(self):
        import tempfile
        tmpdir=Path(tempfile.mkdtemp());self.addCleanup(shutil.rmtree,tmpdir,True)
        self.window.store=app.E.Store(tmpdir/'history.sqlite3')
        self.window.refresh_roster()
        self.assertEqual(self.window.roster_count.text(),'PLAYERS (0)')
        self.window.notice=lambda *a,**k:(_ for _ in ()).throw(AssertionError('unexpected modal notice: %r' % (a,)))
        self.window.roster_name.setText('TestFella');self.window.roster_display_name.setText('Fella')
        self.window.roster_aliases.setText('tf; fella2')
        self.assertTrue(self.window.roster_dirty)
        self.assertIn('●',self.window.roster_save_button.text())
        self.window.save_player()
        self.assertFalse(self.window.roster_dirty)
        self.assertEqual(self.window.roster_count.text(),'PLAYERS (1)')
        self.assertEqual(self.window.roster_list.count(),1)
        self.assertEqual(self.window.roster_big_name.text(),'Fella')
        self.assertIn('ENABLED',self.window.roster_enabled_pill.text())
        pid=self.window.current_player
        self.window.roster_search.setText('zzz-no-match');self.assertEqual(self.window.roster_list.count(),0)
        self.window.roster_search.setText('FELLA2');self.assertEqual(self.window.roster_list.count(),1)
        self.window.roster_search.clear();self.assertEqual(self.window.roster_list.count(),1)
        self.window.name_test.setText('testfella');self.window.test_name()
        self.assertIn('TestFella',self.window.name_match_result.text())
        self.window.name_test.setText('zzzz');self.window.test_name()
        self.assertIn('No unambiguous',self.window.name_match_result.text())
        self.window.roster_display_name.setText('Changed')
        self.assertTrue(self.window.roster_dirty)
        self.window.maybe_discard_roster_edits=lambda:False
        self.window.roster_new_player()
        self.assertEqual(self.window.current_player,pid)
        self.window.maybe_discard_roster_edits=lambda:True
        self.window.roster_new_player()
        self.assertIsNone(self.window.current_player)
        self.window.ask=lambda *args,**kwargs:False
        self.window.current_player=pid;self.window.delete_player()
        self.assertEqual(len(self.window.store.roster()),1)
        self.window.ask=lambda *args,**kwargs:True
        self.window.delete_player()
        self.assertEqual(len(self.window.store.roster()),0)
        self.assertEqual(self.window.roster_count.text(),'PLAYERS (0)')
    def test_roster_headshot_save_and_remove(self):
        import tempfile
        from PIL import Image
        tmpdir=Path(tempfile.mkdtemp());self.addCleanup(shutil.rmtree,tmpdir,True)
        self.window.store=app.E.Store(tmpdir/'history.sqlite3')
        self.window.refresh_roster()
        img=tmpdir/'face.png';Image.new('RGB',(64,64),'red').save(img)
        self.window.notice=lambda *a,**k:(_ for _ in ()).throw(AssertionError('unexpected modal notice: %r' % (a,)))
        self.window.roster_name.setText('PhotoGal')
        self.window.photo_path.setText(str(img));self.window.headshot_filename.setText('face.png')
        self.window.show_image(self.window.photo_preview,str(img),420,220)
        self.window.mark_roster_dirty();self.window.save_player()
        pid=self.window.current_player
        target=Path(app.E.DATA)/'headshots'/(pid+'.jpg')
        self.addCleanup(lambda:target.unlink(missing_ok=True))
        self.assertTrue(target.is_file())
        stored=next(p for p in self.window.store.roster() if p['id']==pid)
        self.assertEqual(stored['photo'],str(target))
        self.window.remove_roster_photo()
        self.assertTrue(self.window.roster_dirty)
        self.window.save_player()
        cleared=next(p for p in self.window.store.roster() if p['id']==pid)
        self.assertEqual(cleared['photo'],'')
    def test_ai_prompt_stat_block_is_opt_in(self):
        w=self.window
        w.store.save_player({'id':'rank-a','name':'Vintendo','display_name':'Vintendo local','aliases':['VintendoPower'],'photo':'','enabled':True,'consent_meta':True,'codmunity_username':'vintendopower','codmunity_uid':'uid-primary','codmunity_display_name':'VintendoPower','profile_url':'https://codmunity.gg/profile/vintendopower'})
        w.store.set_meta('codmunity_public_distributions',{'warzone|resurgence|lifetime':{'game':'warzone','mode':'resurgence','scope':'lifetime','kd':[0.8,1.0,1.5,2.0,2.75,3.47,4.2,4.94,5.86,7.43,8.84,12.03],'sampled':492,'users_total':515,'updated_at':'2026-11-10T12:00:00Z','source':'test'}})
        mid,_=w.store.add({'matchId':'ai-'+uuid.uuid4().hex,'game':'warzone','modeKey':'resurgence','playedAt':'2026-11-10T12:00:00Z','result':'win','ocr':'VICTORY'})
        w.store.update(mid,players=json.dumps([{'name':'VintendoPower','verified':True,'stats':{'kills':19,'deaths':8}}]))
        job=w.store.get(mid)
        w.include_stats_checkbox.setChecked(True)
        with_text=w.ensure_match_prompt(job)
        self.assertIn('PLAYER STATISTICS:',with_text)
        self.assertIn('Casual',with_text)
        self.assertIn('2.38',with_text)
        w.include_stats_checkbox.setChecked(False)
        without_text=w.ensure_match_prompt(w.store.get(mid))
        self.assertNotIn('PLAYER STATISTICS:',without_text)
        w.include_stats_checkbox.setChecked(True)
        self.assertIn('PLAYER STATISTICS:',w.ensure_match_prompt(w.store.get(mid)))
    def test_roster_cards_show_ladder_rank_or_placeholder(self):
        from PySide6.QtWidgets import QLabel
        w=self.window
        w.store.save_player({'id':'rank-a','name':'Vintendo','display_name':'Vintendo local','aliases':['VintendoPower'],'photo':'','enabled':True,'consent_meta':True,'codmunity_username':'vintendopower','codmunity_uid':'uid-primary','codmunity_display_name':'VintendoPower','profile_url':'https://codmunity.gg/profile/vintendopower'})
        w.store.save_player({'id':'rank-b','name':'Goomy11','display_name':'Goomy local','aliases':[],'photo':'','enabled':True,'consent_meta':False})
        mid,_=w.store.add({'matchId':'rank-'+uuid.uuid4().hex,'game':'warzone','modeKey':'resurgence','playedAt':'2026-11-10T12:00:00Z','result':'win'})
        w.store.update(mid,players=json.dumps([{'name':'VintendoPower','verified':True,'stats':{'kills':341,'deaths':100}}]))
        w.store.set_meta('codmunity_public_distributions',{'warzone|resurgence|lifetime':{'game':'warzone','mode':'resurgence','scope':'lifetime','kd':[0.8,1.0,1.5,2.0,2.75,3.47,4.2,4.94,5.86,7.43,8.84,12.03],'sampled':492,'users_total':515,'updated_at':'2026-11-10T12:00:00Z','source':'test'}})
        w.codmunity_game.setCurrentIndex(max(0,w.codmunity_game.findData('warzone')));w.codmunity_mode.setCurrentIndex(max(0,w.codmunity_mode.findData('resurgence')));w.codmunity_scope.setCurrentIndex(max(0,w.codmunity_scope.findData('lifetime')))
        w.refresh_roster()
        texts={}
        for index in range(w.roster_list.count()):
            item=w.roster_list.item(index);card=w.roster_list.itemWidget(item)
            texts[str(item.data(Qt.UserRole))]=' | '.join(label.text() for label in card.findChildren(QLabel))
        self.assertIn('rank-a',texts);self.assertIn('rank-b',texts)
        self.assertIn('Solid',texts['rank-a']);self.assertIn('3.41',texts['rank-a'])
        self.assertNotIn('VintendoPower',texts['rank-b'])
        self.assertIn('RANK UNAVAILABLE',texts['rank-b'])
        w.load_roster_player('rank-a')
        self.assertIn('SOLID',w.roster_rank_tier.text())
        self.assertIn('3.41',w.roster_rank_kd.text())
        self.assertIn('1 tracked match',w.roster_rank_kd.text())
        self.assertIn('%',w.roster_rank_pct.text())
        self.assertIn('Sweat',w.roster_rank_next.text())
        w.load_roster_player('rank-b')
        self.assertIn('RANK UNAVAILABLE',w.roster_rank_tier.text())
    def test_codommunity_roster_identity_cache_rank_and_player_isolation(self):
        import tempfile,json
        tmpdir=Path(tempfile.mkdtemp());self.addCleanup(shutil.rmtree,tmpdir,True)
        w=self.window;w.store=app.E.Store(tmpdir/'history.sqlite3')
        players=[
            {'id':'codm-a','name':'Vintendo','display_name':'Vintendo local','aliases':['VintendoPower'],'photo':'','enabled':True,'consent_meta':True,
             'codmunity_username':'vintendopower','codmunity_uid':'uid-primary','codmunity_display_name':'VintendoPower','profile_url':'https://codmunity.gg/profile/vintendopower'},
            {'id':'codm-b','name':'Goomy11','display_name':'Goomy local','aliases':[],'photo':'','enabled':True,'consent_meta':False,
             'codmunity_username':'Goomy11','codmunity_uid':'uid-secondary','codmunity_display_name':'Goomy','profile_url':'https://codmunity.gg/profile/Goomy11'},
        ]
        for player in players:w.store.save_player(player)
        for suffix,name,kills,deaths in (('a','VintendoPower',341,100),('b','Goomy11',10,5)):
            mid,_=w.store.add({'matchId':'codm-'+suffix,'game':'warzone','modeKey':'resurgence','playedAt':'2026-11-10T12:00:'+('01' if suffix=='a' else '02')+'Z','result':'win'})
            w.store.update(mid,players=json.dumps([{'name':name,'verified':True,'stats':{'kills':kills,'deaths':deaths}}]))
        w.store.set_meta('codmunity_public_distributions',{
            'warzone|resurgence|lifetime':{'game':'warzone','mode':'resurgence','scope':'lifetime',
                'kd':[0.8,1.0,1.5,2.0,2.75,3.47,4.2,4.94,5.86,7.43,8.84,12.03],
                'sampled':492,'users_total':515,'updated_at':'2026-11-10T12:00:00+00:00','source':'public_global_endpoint'}})
        w.refresh_roster();w.load_roster_player('codm-a')
        self.assertIn('vintendopower',w.codmunity_identity_label.text())
        self.assertIn('uid-primary',w.codmunity_identity_label.text())
        self.assertIn('https://codmunity.gg/profile/vintendopower',w.codmunity_profile_label.text())
        self.assertEqual(w.codmunity_status.text(),'Player statistics not connected')
        self.assertIn('3.41',w.codmunity_kd_value.text())
        self.assertIn('not lifetime',w.codmunity_kd_value.text())
        self.assertIn('Solid',w.codmunity_rank_value.text())
        self.assertAlmostEqual(float(w.codmunity_percentile_value.text().rstrip('%')),49.2,places=1)
        self.assertEqual(w.codmunity_next_kd_value.text(),'4.94')
        self.assertIn('Saved community cache',w.codmunity_distribution_label.text())
        w.codmunity_stats_button.click()
        self.assertEqual(w.codmunity_status.text(),'Player statistics not connected')
        w.load_roster_player('codm-b')
        self.assertIn('uid-secondary',w.codmunity_identity_label.text())
        self.assertIn('2.00',w.codmunity_kd_value.text())
        self.assertIn('local tracked matches',w.codmunity_kd_value.text())
        self.assertNotIn('3.41',w.codmunity_kd_value.text())
        self.assertIn('Saved community cache',w.codmunity_distribution_label.text())
        w.roster_display_name.setText('Goomy display updated');w.save_player()
        saved=next(player for player in w.store.roster() if player['id']=='codm-b')
        self.assertEqual(saved['codmunity_uid'],'uid-secondary')
        self.assertEqual(saved['codmunity_username'],'Goomy11')
        self.assertEqual(saved['display_name'],'Goomy display updated')
        self.assertFalse(saved['consent_meta'])
    def test_cinematic_results_list_shows_several_rows(self):
        self.assertGreaterEqual(self.window.cinematic_results.minimumHeight(),140)
    def test_cod_ai_dashboard_has_workflow_and_no_standalone_delete_button(self):
        self.assertIsNone(self._button_by_text('Delete selected video'))
        self.assertIsNotNone(self._button_by_text('Refresh'))
        self.assertIsNone(self._button_by_text('Test Video'))
        self.assertIsNotNone(self._button_by_text('Post to Discord'))
        self.assertIsNotNone(self._button_by_text('Change Photo'))
        self.assertIsNotNone(self._button_by_text('Remove Photo'))
        self.assertIsNotNone(self._button_by_text('Capture Game Screenshot'))
        self.assertIn('Ctrl + F8',self.window.cod_ai_hotkey_status.text())
    def test_cinematic_display_helpers_format_duration_and_shorten_names(self):
        self.assertEqual(app.format_cinematic_duration(18),'0:18')
        self.assertEqual(app.format_cinematic_duration(75),'1:15')
        self.assertEqual(app.format_cinematic_duration(None),'')
        short=app.short_cinematic_name('cin-c193ae51b3a43b84f0e51caa123456789.mp4')
        self.assertLessEqual(len(short),24);self.assertIn(chr(8230),short);self.assertTrue(short.endswith('.mp4'))
    def test_gallery_grid_uses_three_columns(self):
        self.window.cinematic_results.resize(660,420)
        self.window.update_cinematic_grid()
        viewport=self.window.cinematic_results.viewport().width()
        columns=viewport//self.window.cinematic_results.gridSize().width()
        self.assertEqual(columns,3)
    def test_per_thumbnail_delete_requires_confirmation_and_only_deletes_that_video(self):
        folder=Path(app.E.DATA)/'Cinematics';folder.mkdir(parents=True,exist_ok=True)
        made=[]
        try:
            for tag in ('keep','drop'):
                cid='cin-tile-'+tag+'-'+uuid.uuid4().hex
                shot=folder/('in_'+cid+'.png');shot.write_bytes(b'\x89PNG\r\n\x1a\n'+b'0'*64)
                self.assertTrue(self.window.store.create_cinematic(cid,'shot.png',shot,'prompt'))
                video=folder/(cid+'.mp4');video.write_bytes(b'\x00\x00\x00\x18ftyp'+b'0'*1024)
                self.window.store.update_cinematic(cid,status='completed',video=str(video))
                made.append((cid,video,shot))
            self.window.refresh_cinematic_results()
            drop_cid,drop_video,_=made[1];keep_cid,keep_video,_=made[0]
            tiles=getattr(self.window,'cinematic_tiles',{})
            self.assertIn(drop_cid,tiles);self.assertIn(keep_cid,tiles)
            self.window.confirm_cinematic_delete=lambda:False
            tiles[drop_cid].delete_button.click()
            self.assertTrue(drop_video.exists())
            self.window.confirm_cinematic_delete=lambda:True
            tiles[drop_cid].delete_button.click()
            self.assertFalse(drop_video.exists());self.assertTrue(keep_video.exists())
            self.assertEqual(self.window.store.get_cinematic(drop_cid)['video'],'')
            self.assertEqual(self.window.store.get_cinematic(keep_cid)['video'],str(keep_video))
        finally:
            for _,video,shot in made:video.unlink(missing_ok=True);shot.unlink(missing_ok=True)
    def test_fullscreen_escape_and_double_click_exit_via_filter(self):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent,QMouseEvent
        from PySide6.QtCore import QPointF
        calls=[]
        filt=app.FullscreenExitFilter(lambda:True,lambda:calls.append('escape'),lambda:calls.append('toggle'))
        escape=QKeyEvent(QEvent.KeyPress,Qt.Key_Escape,Qt.NoModifier)
        self.assertTrue(filt.eventFilter(None,escape));self.assertEqual(calls,['escape'])
        other=QKeyEvent(QEvent.KeyPress,Qt.Key_A,Qt.NoModifier)
        self.assertFalse(filt.eventFilter(None,other));self.assertEqual(calls,['escape'])
        class FakeEvent:
            def __init__(self,kind):self._kind=kind
            def type(self):return self._kind
        self.assertTrue(filt.eventFilter(None,FakeEvent(QEvent.MouseButtonDblClick)));self.assertEqual(calls,['escape','toggle'])
        windowed=app.FullscreenExitFilter(lambda:False,lambda:calls.append('escape'),lambda:calls.append('toggle'))
        self.assertFalse(windowed.eventFilter(None,escape));self.assertEqual(calls,['escape','toggle'])
    def test_cinematic_fullscreen_exit_forces_windowed(self):
        calls=[]
        class FakeVideo:
            def __init__(self):self.full=True
            def isFullScreen(self):return self.full
            def setFullScreen(self,value):self.full=value;calls.append(value)
        fake=FakeVideo();self.window.cinematic_video_widget=fake
        self.window.exit_cinematic_fullscreen();self.assertFalse(fake.full)
        self.window.toggle_cinematic_fullscreen();self.assertTrue(fake.full)
        self.window.toggle_cinematic_fullscreen();self.assertFalse(fake.full)
        self.assertIsNotNone(getattr(self.window,'cinematic_fullscreen_filter',None))
    def test_preview_actions_disabled_without_selection(self):
        self.window.preview_cinematic_video(None)
        self.assertFalse(self.window.post_cinematic_button.isEnabled())
        self.assertFalse(self.window.cinematic_play_button.isEnabled())
    def test_cinematic_thumbnail_source_prefers_screenshot_then_thumb(self):
        folder=Path(app.E.DATA)/'Cinematics';folder.mkdir(parents=True,exist_ok=True)
        shot=folder/('thumb-in-'+uuid.uuid4().hex+'.png');shot.write_bytes(b'\x89PNG\r\n\x1a\n'+b'0'*64)
        thumb=folder/('thumb-'+uuid.uuid4().hex+'.jpg');thumb.write_bytes(b'\xff\xd8'+b'0'*64)
        try:
            self.assertEqual(app.cinematic_thumbnail_source({'image':str(shot)},str(thumb)),str(shot))
            shot.unlink()
            self.assertEqual(app.cinematic_thumbnail_source({'image':str(shot)},str(thumb)),str(thumb))
            self.assertEqual(app.cinematic_thumbnail_source({'image':str(shot)},str(folder/'nope.jpg')),'')
        finally:shot.unlink(missing_ok=True);thumb.unlink(missing_ok=True)
    def test_screenshot_cleanup_offers_and_deletes_used(self):
        folder=Path(app.E.DATA)/'Screenshots';folder.mkdir(parents=True,exist_ok=True)
        names=[f'COD_test_{uuid.uuid4().hex}.png' for _ in range(11)]
        created=[]
        for name in names:
            target=folder/name;target.write_bytes(b'\x89PNG\r\n\x1a\n'+b'0'*64);created.append(target)
        self.addCleanup(lambda:[target.unlink(missing_ok=True) for target in created])
        used_name=names[0];w=self.window
        real_recent=w.store.recent_cinematics
        w.store.recent_cinematics=lambda limit=30:[{'filename':used_name}]
        calls=[]
        w.ask_screenshot_cleanup=lambda u,t:(calls.append((u,t)),True)[1]
        try:
            w.show();self.qt.processEvents()
            w._screenshot_cleanup_offered=False
            w.refresh_cod_ai_screenshots()
            self.assertEqual(len(calls),1);self.assertEqual(calls[0][0],1);self.assertGreaterEqual(calls[0][1],11)
            self.assertFalse((folder/used_name).exists())
            self.assertTrue(all((folder/name).exists() for name in names[1:]))
            self.assertIn('Deleted 1 used screenshot.',w.cod_ai_capture_status.text())
            w.refresh_cod_ai_screenshots()
            self.assertEqual(len(calls),1)
        finally:
            w.store.recent_cinematics=real_recent
            w.hide()
            w.refresh_cod_ai_screenshots()
    def test_screenshot_cleanup_no_and_few_files_stay_quiet(self):
        folder=Path(app.E.DATA)/'Screenshots';folder.mkdir(parents=True,exist_ok=True)
        names=[f'COD_quiet_{uuid.uuid4().hex}.png' for _ in range(11)]
        created=[]
        for name in names:
            target=folder/name;target.write_bytes(b'\x89PNG\r\n\x1a\n'+b'0'*64);created.append(target)
        self.addCleanup(lambda:[target.unlink(missing_ok=True) for target in created])
        w=self.window
        real_recent=w.store.recent_cinematics
        w.store.recent_cinematics=lambda limit=30:[{'filename':'COD_never_queued.png'}]
        w.ask_screenshot_cleanup=lambda u,t:(_ for _ in ()).throw(AssertionError('should not ask without used files'))
        try:
            w.show();self.qt.processEvents()
            w._screenshot_cleanup_offered=False
            w.refresh_cod_ai_screenshots()
            self.assertTrue(all((folder/name).exists() for name in names))
            for target in list(created)[:8]:target.unlink()
            created[:]=[target for target in created if target.exists()]
            w.refresh_cod_ai_screenshots()
            self.assertTrue(all(target.exists() for target in created))
        finally:
            w.store.recent_cinematics=real_recent
            w.hide()
            w.refresh_cod_ai_screenshots()
    def test_talent_friends_picker_lists_eligible_and_caps_four(self):
        import tempfile
        from PySide6.QtTest import QTest
        from PIL import Image
        tmpdir=Path(tempfile.mkdtemp());self.addCleanup(shutil.rmtree,tmpdir,True)
        self.window.store=app.E.Store(tmpdir/'history.sqlite3')
        def photo(color):
            target=tmpdir/(f'{color}.png');Image.new('RGB',(48,48),color).save(target);return str(target)
        players=[
            dict(id='f1',name='Friend1',display_name='F1',aliases=[],photo=photo('red'),enabled=True,consent_meta=True),
            dict(id='f2',name='Friend2',display_name='F2',aliases=[],photo=photo('blue'),enabled=True,consent_meta=True),
            dict(id='f3',name='Friend3',display_name='F3',aliases=[],photo=photo('green'),enabled=True,consent_meta=True),
            dict(id='f4',name='Friend4',display_name='F4',aliases=[],photo=photo('orange'),enabled=True,consent_meta=True),
            dict(id='f5',name='Friend5',display_name='F5',aliases=[],photo=photo('pink'),enabled=True,consent_meta=True),
            dict(id='noconsent',name='NoConsent',display_name='NC',aliases=[],photo=photo('yellow'),enabled=True,consent_meta=False),
            dict(id='nophoto',name='NoPhoto',display_name='NP',aliases=[],photo=str(tmpdir/'ghost.png'),enabled=True,consent_meta=True),
            dict(id='off',name='Off',display_name='Off',aliases=[],photo=photo('purple'),enabled=False,consent_meta=True),
        ]
        for player in players:self.window.store.save_player(player)
        self.window.refresh_talent_friends()
        tiles=self.window.talent_friend_tiles
        self.assertEqual(set(tiles),{'f1','f2','f3','f4','f5'})
        self.assertIn('(Max Of 3 Can be used)',self.window.talent_friends_status.text())
        QTest.mouseClick(tiles['f1'],Qt.LeftButton);QTest.mouseClick(tiles['f2'],Qt.LeftButton)
        self.assertEqual(self.window.talent_friend_ids,['f1','f2'])
        self.assertIn('Featuring: F1, F2',self.window.talent_cameo_label.text())
        strip_widgets=[self.window.talent_cameo_strip.itemAt(i).widget() for i in range(self.window.talent_cameo_strip.count())]
        self.assertEqual(len([wide for wide in strip_widgets if wide is not None]),2)
        self.assertFalse(tiles['f1'].remove_button.isHidden())
        self.assertTrue(tiles['f3'].remove_button.isHidden())
        tiles['f1'].remove_button.click()
        self.assertEqual(self.window.talent_friend_ids,['f2'])
        self.assertIn('Featuring: F2',self.window.talent_cameo_label.text())
        self.assertTrue(tiles['f1'].remove_button.isHidden())
        self.window.on_talent_friend_toggled('f3');self.window.on_talent_friend_toggled('f4')
        self.assertEqual(self.window.talent_friend_ids,['f2','f3','f4'])
        self.assertIn('Featuring: F2, F3, F4',self.window.talent_cameo_label.text())
        self.window.on_talent_friend_toggled('f5')
        self.assertEqual(len(self.window.talent_friend_ids),3)
        self.assertNotIn('f5',self.window.talent_friend_ids)
        self.assertIn('Max of 3 friends',self.window.cinematic_status.text())
    def test_friend_grid_rejects_own_cameo_with_message(self):
        from PIL import Image
        w=self.window
        def photo(color):
            target=app.E.DATA/('cameo-'+color+'-'+uuid.uuid4().hex+'.jpg');Image.new('RGB',(48,48),color).save(target);return str(target)
        w.store.save_player(dict(id='me',name='Me',display_name='Me',aliases=[],photo=photo('red'),enabled=True,consent_meta=True))
        w.store.save_player(dict(id='pal',name='Pal',display_name='Pal',aliases=[],photo=photo('blue'),enabled=True,consent_meta=True))
        w.settings.values['player']='Me'
        talent=w.videos_dir()/'talent.png'
        created=not talent.is_file()
        if created:Image.new('RGB',(48,48),'gray').save(talent)
        try:
            w.refresh_talent_friends()
            w.on_talent_friend_toggled('me')
            self.assertEqual(w.talent_friend_ids,[])
            self.assertIn('already in this video',w.cinematic_status.text())
            w.on_talent_friend_toggled('pal')
            self.assertEqual(w.talent_friend_ids,['pal'])
        finally:
            w.talent_friend_ids=[]
            w.settings.values.pop('player',None)
            if created:
                try:talent.unlink()
                except OSError:pass
    def test_queue_cinematic_with_friends_stages_cast_and_prompt(self):
        import shutil as _shutil
        import tempfile
        from PIL import Image
        tmpdir=Path(tempfile.mkdtemp());self.addCleanup(_shutil.rmtree,tmpdir,True)
        self.window.store=app.E.Store(tmpdir/'history.sqlite3')
        w=self.window
        def photo(color):
            target=tmpdir/(f'{color}.png');Image.new('RGB',(48,48),color).save(target);return str(target)
        w.store.save_player(dict(id='f1',name='Friend1',display_name='F1',aliases=[],photo=photo('red'),enabled=True,consent_meta=True))
        w.store.save_player(dict(id='f2',name='Friend2',display_name='F2',aliases=[],photo=photo('blue'),enabled=True,consent_meta=True))
        w.talent_friend_ids=['f1','f2']
        src=tmpdir/'shot.png';Image.new('RGB',(64,36),'green').save(src)
        created=[]
        real_create=w.store.create_cinematic
        def spy(cid,filename,image,prompt,talent_ids=None):
            created.append((cid,filename,prompt,talent_ids));return real_create(cid,filename,image,prompt,talent_ids=talent_ids)
        w.store.create_cinematic=spy
        cids=[]
        try:
            w.queue_cinematic_source(str(src))
            self.assertEqual(len(created),1)
            cid,filename,prompt,tids=created[0];cids.append(cid)
            self.assertEqual(tids,['f1','f2'])
            self.assertIn('talent-f1.png is a reference photo of F1',prompt)
            self.assertIn('talent-f2.png is a reference photo of F2',prompt)
            self.assertTrue((Path(app.E.DATA)/'Cinematics'/cid/'talent-f1.png').is_file())
            self.assertTrue((Path(app.E.DATA)/'Cinematics'/cid/'talent-f2.png').is_file())
            self.assertEqual(json.loads(w.store.get_cinematic(cid)['talent_ids']),['f1','f2'])
            self.assertIn('Featuring F1, F2.',w.cinematic_status.text())
            w.talent_friend_ids=[]
            w.queue_cinematic_source(str(src))
            self.assertEqual(len(created),2);cids.append(created[1][0])
            self.assertEqual(created[1][3],[])
            self.assertNotIn('talent-f1.png',created[1][2])
        finally:
            w.store.create_cinematic=real_create
            for job_id in cids:
                try:w.store.update_cinematic(job_id,status='failed')
                except Exception:pass
                _shutil.rmtree(Path(app.E.DATA)/'Cinematics'/job_id,ignore_errors=True)
                try:(Path(app.E.DATA)/'Cinematics'/'input'/(job_id+'.png')).unlink(missing_ok=True)
                except OSError:pass
    def test_remove_talent_photo_deletes_file_and_clears_preview(self):
        from PIL import Image
        target=Path(app.E.DATA)/'Cinematics'/'talent.png'
        backup=target.read_bytes() if target.is_file() else None
        try:
            Image.new('RGB',(64,64),'blue').save(target)
            self.window.ask=lambda *args,**kwargs:True
            self.window.remove_cinematic_talent()
            self.assertFalse(target.exists())
            self.assertIn('No talent photo',self.window.talent_info.text())
        finally:
            if backup is None:target.unlink(missing_ok=True)
            else:target.write_bytes(backup)
            self.window.refresh_talent_preview()
    def test_remove_talent_photo_without_file_reports_clearly(self):
        target=Path(app.E.DATA)/'Cinematics'/'talent.png'
        backup=target.read_bytes() if target.is_file() else None
        try:
            target.unlink(missing_ok=True)
            self.window.remove_cinematic_talent()
            self.assertIn('No talent photo',self.window.cinematic_status.text())
        finally:
            if backup is None:target.unlink(missing_ok=True)
            else:target.write_bytes(backup)
            self.window.refresh_talent_preview()
    def test_talent_preview_shows_current_photo(self):
        from PIL import Image
        target=Path(app.E.DATA)/'Cinematics'/'talent.png'
        backup=target.read_bytes() if target.is_file() else None
        try:
            Image.new('RGB',(64,64),'green').save(target)
            self.window.refresh_talent_preview()
            self.assertFalse(self.window.talent_preview.pixmap().isNull())
            self.assertIn('talent.png',self.window.talent_info.text())
        finally:
            if backup is None:target.unlink(missing_ok=True)
            else:target.write_bytes(backup)
            self.window.refresh_talent_preview()
    def test_cinematic_grid_shows_screenshot_thumbnails(self):
        from PIL import Image
        folder=Path(app.E.DATA)/'Cinematics';folder.mkdir(parents=True,exist_ok=True)
        cid='cin-thumb-'+uuid.uuid4().hex
        shot=folder/('in_'+cid+'.png');Image.new('RGB',(320,180),'red').save(shot)
        self.assertTrue(self.window.store.create_cinematic(cid,'shot.png',shot,'prompt'))
        try:
            self.window.refresh_cinematic_results()
            found=[self.window.cinematic_results.item(i) for i in range(self.window.cinematic_results.count())
                  if self.window.cinematic_results.item(i).data(Qt.UserRole)==cid]
            self.assertTrue(found)
            tile=self.window.cinematic_tiles[cid];self.assertFalse(tile.image.pixmap().isNull())
        finally:shot.unlink(missing_ok=True)
    def test_embed_without_chrome_window_reports_clearly(self):
        self.window.find_tracker_chrome_windows=lambda: []
        self.window.embed_tracker_chrome()
        self.assertIn('No Chrome windows',self.window.browser_status.text())
    def test_discord_failure_message_redacts_webhook_url(self):
        self.window.on_cinematic_discord_result('Discord post failed: https://discord.com/api/webhooks/123/secret-token-here')
        text=self.window.cinematic_status.text()
        self.assertIn('[webhook]',text);self.assertNotIn('secret-token-here',text)
    def test_discord_failure_message_redacts_split_webhook_url(self):
        self.window.on_cinematic_discord_result("Max retries exceeded with url: /api/webhooks/123/secret-token-here (Caused by SSLError('boom'))")
        text=self.window.cinematic_status.text()
        self.assertIn('[webhook]',text);self.assertNotIn('secret-token-here',text)
    def test_tracker_browser_page_exists(self):
        from PySide6.QtWidgets import QPushButton
        names=[self.window.nav.item(i).data(Qt.UserRole) for i in range(self.window.nav.count())]
        self.assertIn('Tracker Browser',names)
        page,layout=self.window.pages['Tracker Browser']
        labels=[b.text() for b in page.findChildren(QPushButton)]
        self.assertIn('Log into Meta AI',labels)
        self.assertTrue(all('Open Tracker Chrome' not in text and 'Embed' not in text for text in labels))
        self.assertIsInstance(self.window.auto_core,app.MissionCore)
        found=True
        self.assertTrue(found)

    def test_browser_mode_defaults_to_dedicated(self):
        w=self.window
        self.assertEqual(w.tracker_browser_mode(),'dedicated')
        self.assertEqual(w.browser_mode_combo.currentIndex(),0)
    def test_browser_mode_setting_round_trip(self):
        w=self.window
        original=w.settings.values.get('tracker_browser_mode')
        try:
            w.browser_mode_combo.setCurrentIndex(1);w.save_settings()
            self.assertEqual(w.settings.values['tracker_browser_mode'],'attach')
            self.assertEqual(w.tracker_browser_mode(),'attach')
            w.browser_mode_combo.setCurrentIndex(0);w.save_settings()
            self.assertEqual(w.settings.values['tracker_browser_mode'],'dedicated')
        finally:
            if original is None:w.settings.values.pop('tracker_browser_mode',None)
            else:w.settings.values['tracker_browser_mode']=original
            w.settings.save()
            w.browser_mode_combo.setCurrentIndex(1 if w.tracker_browser_mode()=='attach' else 0)
    def test_open_tracker_chrome_attach_without_windows_reports_clearly(self):
        w=self.window
        original=w.settings.values.get('tracker_browser_mode')
        w.settings.values['tracker_browser_mode']='attach'
        try:
            from unittest.mock import patch
            w.find_tracker_chrome_windows=lambda: []
            with patch('app.QMessageBox.information'):
                w.open_tracker_chrome()
            self.assertIn('No Chrome windows found',w.browser_status.text())
        finally:
            if original is None:w.settings.values.pop('tracker_browser_mode',None)
            else:w.settings.values['tracker_browser_mode']=original
    def test_open_tracker_chrome_attach_single_window_embeds_it(self):
        w=self.window
        original=w.settings.values.get('tracker_browser_mode')
        attached=[]
        w.settings.values['tracker_browser_mode']='attach'
        try:
            w.find_tracker_chrome_windows=lambda: [4242]
            from unittest.mock import patch
            w.attach_browser_window=lambda hwnd: attached.append(hwnd)
            with patch('app.QMessageBox.information'):
                w.open_tracker_chrome()
            self.assertEqual(attached,[4242])
        finally:
            if original is None:w.settings.values.pop('tracker_browser_mode',None)
            else:w.settings.values['tracker_browser_mode']=original
    def test_open_tracker_chrome_dedicated_uses_isolated_data_dir(self):
        import subprocess as subprocess_mod
        w=self.window
        real_popen=subprocess_mod.Popen;seen=[]
        try:
            w.chrome_executable=lambda: Path('chrome.exe')
            w.find_tracker_chrome_windows=lambda: []
            w.refresh_tracker_browser=lambda: None
            from unittest.mock import patch
            subprocess_mod.Popen=lambda *args,**kwargs: seen.append(args[0])
            with patch('app.QMessageBox.information'):
                w.open_tracker_chrome()
            self.assertEqual(len(seen),1)
            flags=[str(part) for part in seen[0][1:]]
            self.assertTrue(any(part.startswith('--user-data-dir=') for part in flags))
            self.assertFalse(any(part.startswith('--profile-directory') for part in flags))
            self.assertIn(str(app.E.DATA),seen[0][1])
        finally:
            subprocess_mod.Popen=real_popen
if __name__=='__main__':unittest.main()
