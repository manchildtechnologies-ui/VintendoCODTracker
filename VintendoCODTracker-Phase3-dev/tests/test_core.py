import io,json,os,tempfile,unittest,urllib.error,uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
import engine as E

MP={'matchId':'fixture-mp-001','modeKey':'kill_confirmed','playedAt':'2026-11-07T10:00:00Z','result':'win','scoreOwn':100,'scoreOpp':88,'stats':{'kills':32,'confirms':20,'score':4500,'deaths':10}}
WZ={'game':'warzone','mode':'resurgence','playedAt':'2026-11-07T10:30:00Z','result':'loss','stats':{'kills':12,'damage':3400},'placement':4}
@contextmanager
def fixture_dir():
    path=E.DATA/('fixture-'+uuid.uuid4().hex);path.mkdir(parents=True,exist_ok=True)
    yield path
class CoreTests(unittest.TestCase):
    def setUp(self):self.root=E.DATA/('fixture-'+uuid.uuid4().hex);self.root.mkdir(parents=True,exist_ok=True)
    def test_used_screenshots_matches_job_filenames_only(self):
        folder=self.root/'shots';folder.mkdir()
        used=folder/'COD_used_1.png';used.write_bytes(b'\x89PNG\r\n\x1a\n'+b'0'*64)
        fresh=folder/'COD_fresh_2.png';fresh.write_bytes(b'\x89PNG\r\n\x1a\n'+b'0'*64)
        (folder/'random.png').write_bytes(b'\x89PNG\r\n\x1a\n'+b'0'*64)
        jobs=[{'filename':'COD_used_1.png'},{'filename':'COD_gone_9.png'},{'filename':''}]
        self.assertEqual(E.used_screenshots(folder,jobs),[used])
        self.assertEqual(E.used_screenshots(folder,[]),[])
        self.assertEqual(E.used_screenshots(folder/'missing',jobs),[])
    def test_dpapi_settings_never_store_plaintext(self):
        s=E.Settings(self.root);s.set_webhook('test-secret-not-a-real-webhook');s.save()
        self.assertNotIn('test-secret-not-a-real-webhook',s.path.read_text());self.assertEqual(E.Settings(self.root).webhook(),'test-secret-not-a-real-webhook')
    def test_cinematic_webhook_persists_and_never_stores_plaintext(self):
        s=E.Settings(self.root);s.set_cinematic_webhook('test-cinematic-secret-not-a-real-webhook');s.save()
        self.assertNotIn('test-cinematic-secret-not-a-real-webhook',s.path.read_text())
        self.assertEqual(E.Settings(self.root).cinematic_webhook(),'test-cinematic-secret-not-a-real-webhook')
    def test_settings_persist(self):
        s=E.Settings(self.root);s.values['player']='fixture';s.save();self.assertEqual(E.Settings(self.root).values['player'],'fixture')
    def test_squad_auto_post_migration_preserves_future_opt_out(self):
        legacy=self.root/'old-settings';legacy.mkdir();(legacy/'settings.json').write_text(json.dumps({'auto_post':False,'player':'fixture'}))
        migrated=E.Settings(legacy);self.assertTrue(migrated.values['auto_post']);self.assertEqual(migrated.values['player'],'fixture')
        migrated.values['auto_post']=False;migrated.save();self.assertFalse(E.Settings(legacy).values['auto_post'])
    def test_record_dedup(self):
        s=E.Store(self.root/'db');mid,added=s.add(MP);self.assertTrue(added);self.assertFalse(s.add(MP)[1]);self.assertEqual(len(s.history()),1)
    def test_source_id_change_still_deduplicates(self):
        s=E.Store(self.root/'db');mid,_=s.add(MP);again,added=s.add({**MP,'matchId':'replacement-id'});self.assertEqual(again,mid);self.assertFalse(added)
    def test_concurrent_delivery_reservation(self):
        import concurrent.futures
        s=E.Store(self.root/'db');mid,_=s.add(MP)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(lambda _:s.reserve(mid),range(8)))
        self.assertEqual(sum(results),1)
    def test_fallback_identity_ignores_delivery_metadata(self):
        self.assertEqual(E.key_for(WZ),E.key_for({**WZ,'sent':True,'reason':'saved'}))
    def test_fallback_changes_for_distinct_match(self):
        self.assertNotEqual(E.key_for(WZ),E.key_for({**WZ,'playedAt':'2026-11-07T11:00:00Z'}))
    def test_delivery_reservation_is_single(self):
        s=E.Store(self.root/'db');mid,_=s.add(MP);self.assertTrue(s.reserve(mid));self.assertFalse(s.reserve(mid));s.update(mid,status='delivered');self.assertFalse(s.reserve(mid,True))
    def test_artwork_edits_cannot_reset_delivery_protection(self):
        s=E.Store(self.root/'db');mid,_=s.add(MP);s.update(mid,status='delivered');s.update(mid,status='art_ready');self.assertFalse(s.reserve(mid,True))
    def test_optional_second_format_requires_explicit_setting(self):
        s=E.Store(self.root/'db');mid,_=s.add(MP,'solo_delivered');s.update(mid,status='ready');self.assertFalse(s.reserve(mid));self.assertTrue(s.reserve(mid,allow_both=True));s.update(mid,status='delivered');s.update(mid,status='ready');self.assertFalse(s.reserve(mid,allow_both=True))
    def test_solo_cannot_repeat_squad_delivery(self):
        s=E.Store(self.root/'db');mid,_=s.add(MP);s.update(mid,status='delivered');s.update(mid,status='ready');self.assertFalse(s.reserve(mid,kind='solo'))
    def test_restart_preserves_uncertain_delivery(self):
        s=E.Store(self.root/'db');mid,_=s.add(MP);s.reserve(mid);s=E.Store(self.root/'db');self.assertEqual(s.get(mid)['status'],'uncertain');self.assertFalse(s.reserve(mid));self.assertTrue(s.reserve(mid,True))
    def test_generation_recovery_keeps_data(self):
        s=E.Store(self.root/'db');mid,_=s.add(WZ);s.update(mid,status='generating',prompt='16:9 prompt');s=E.Store(self.root/'db');self.assertEqual(s.get(mid)['status'],'needs_review');self.assertEqual(s.get(mid)['prompt'],'16:9 prompt')
    def test_roster_storage(self):
        s=E.Store(self.root/'db');s.save_player({'name':'Vintendo','aliases':['Vin'],'photo':'','enabled':True});self.assertEqual(s.roster()[0]['name'],'Vintendo');s.delete_player(s.roster()[0]['id']);self.assertFalse(s.roster())
    def test_clan_tags_case_and_punctuation(self):
        p={'name':'[POOP]VinTenDoPower','aliases':[],'enabled':True};self.assertEqual(E.match_player('vintendopower',[p])[0],p)
        self.assertEqual(E.normalize_name('[TAG](Squad)<Clan>Véndi!'), 'vendi')
        self.assertEqual(E.normalize_name('ABC|Player-One'),'playerone')
    def test_alias(self):
        p={'name':'flying concha','aliases':['concha'],'enabled':True};self.assertEqual(E.match_player('CONCHA',[p])[0],p)
    def test_ambiguous_players_not_assigned(self):
        roster=[{'name':'same','enabled':True},{'name':'same','enabled':True}];self.assertIsNone(E.match_player('same',roster)[0])
    def test_disabled_not_assigned(self):self.assertIsNone(E.match_player('same',[{'name':'same','enabled':False}])[0])
    def test_low_confidence_not_assigned(self):self.assertIsNone(E.match_player('unknown',[{'name':'VintendoPower','enabled':True}])[0])
    def test_ocr_table_requires_review(self):
        rows=E.parse_scoreboard('PLAYER  KILLS  DAMAGE  SCORE\nVintendo  12  3400  4500\nConcha  8  2000  3200');self.assertEqual(rows[0]['stats']['damage'],3400);self.assertFalse(rows[0]['verified'])
    def test_ocr_uncertain_columns_not_invented(self):
        rows=E.parse_scoreboard('Vintendo  12  3400  4500');self.assertEqual(rows[0]['stats'],{})
    def test_ocr_invalid_numbers_stay_unknown(self):
        rows=E.parse_scoreboard('PLAYER  KILLS  DAMAGE\nVintendo  I2  3400');self.assertIsNone(rows[0]['stats']['kills'])
    def test_mode_specific_scoreboard_columns_keep_eliminations_and_other_metrics(self):
        rows=E.parse_scoreboard('PLAYER  ELIMINATIONS  DEATHS  K/D  HEADSHOTS  SCORE\n[WZ] Alpha  8  2  4.0  3  1200\nBravo  5  4  1.25  1  900')
        self.assertEqual(rows[0]['stats'],{'eliminations':8,'deaths':2,'kd':4.0,'headshots':3,'score':1200})
        self.assertEqual(rows[1]['stats']['kd'],1.25)
    def test_codommunity_and_ocr_context_cover_warzone_resurgence_and_multiplayer(self):
        warzone=E.extract_match_context(WZ)
        self.assertEqual(warzone,{'game_mode':'Warzone · Resurgence','result':'Defeat','placement':'4th place'})
        resurgence=E.extract_match_context({'modeKey':'resurgence','result':'win','placement':2})
        self.assertEqual(resurgence['game_mode'],'Resurgence');self.assertEqual(resurgence['result'],'Victory');self.assertEqual(resurgence['placement'],'2nd place')
        mp=E.extract_match_context({'modeKey':'kill_confirmed','result':'win'})
        self.assertEqual(mp['game_mode'],'Multiplayer · Kill Confirmed')
        ocr=E.extract_match_context({},'CALL OF DUTY MULTIPLAYER\nHARDPOINT\nVICTORY\n3rd Place')
        self.assertEqual(ocr,{'game_mode':'Multiplayer · Hardpoint','result':'Victory','placement':'3rd place'})
    def test_missing_scoreboard_context_stays_explicitly_unavailable(self):
        context=E.extract_match_context({'modeKey':'resurgence','result':'match'})
        self.assertEqual(context['game_mode'],'Resurgence')
        self.assertTrue(context['result'].startswith('Not shown'))
        self.assertTrue(context['placement'].startswith('Not shown'))
    def test_warzone_placement_decides_win_loss_when_result_missing(self):
        first=E.extract_match_context({'modeKey':'resurgence'},'1st place')
        self.assertEqual(first['result'],'Victory');self.assertEqual(first['placement'],'1st place')
        eighth=E.extract_match_context({'modeKey':'resurgence'},'8th place')
        self.assertEqual(eighth['result'],'Defeat')
        codmunity_wins=E.extract_match_context({'modeKey':'resurgence','result':'win'},'8th place')
        self.assertEqual(codmunity_wins['result'],'Victory')
        multiplayer=E.extract_match_context({'modeKey':'kill_confirmed'},'1st place')
        self.assertTrue(multiplayer['result'].startswith('Not shown'))
    def test_placement_from_players_takes_most_common_row(self):
        rows=[dict(name='A',stats={'placement':4}),dict(name='B',stats={'placement':4}),dict(name='C',stats={'placement':2})]
        self.assertEqual(E.placement_from_players(rows),4)
        self.assertIsNone(E.placement_from_players([]))
        self.assertIsNone(E.placement_from_players([dict(name='A',stats={'kills':5})]))
    def test_ranked_scoreboard_uses_available_mode_metrics_and_leaves_missing_rows_unranked(self):
        players=[dict(name='A',verified=True,stats={'score':1200,'kills':4}),dict(name='B',verified=True,stats={'score':1400,'kills':3}),dict(name='C',verified=True,stats={'kills':99})]
        ranked=E.rank_verified_players(players,{'modeKey':'kill_confirmed'})
        self.assertEqual(ranked[0]['name'],'B');self.assertEqual(ranked[0]['ranking_basis'][0],'score')
        self.assertTrue(ranked[0]['mvp']);self.assertEqual(ranked[-1]['name'],'C');self.assertIsNone(ranked[-1]['rank'])
    def test_no_comparable_verified_stats_means_no_claimed_mvp(self):
        prompt=E.match_poster_prompt([dict(name='A',verified=True,stats={}),dict(name='B',verified=True,stats={})],WZ)
        self.assertIn('"verified_statistics":{}',prompt);self.assertNotIn('"mvp":true',prompt)
    def test_missing_tiebreak_stat_does_not_push_player_below_known_value(self):
        players=[dict(name='A',verified=True,stats={'kills':8,'damage':900}),dict(name='B',verified=True,stats={'kills':8}),dict(name='C',verified=True,stats={'kills':4,'damage':400})]
        ranked=E.rank_verified_players(players,{'game':'warzone','modeKey':'resurgence'})
        self.assertEqual(ranked[0]['rank'],1);self.assertEqual(ranked[1]['rank'],1)
        self.assertTrue(ranked[0]['mvp']);self.assertTrue(ranked[1]['mvp'])
        self.assertEqual(ranked[0]['ranking_basis'],['kills'])
    def test_match_poster_prompt_uses_verified_scoreboard_ranking_and_open_creativity(self):
        players=[dict(name='Alpha',stats={'kills':12,'damage':4000},verified=True),dict(name='Bravo',stats={'kills':8,'damage':5000},verified=True),dict(name='Unverified',stats={'kills':999999},verified=False)]
        prompt=E.match_poster_prompt(players,WZ)
        self.assertIn('16:9',prompt);self.assertIn('invent the setting',prompt.lower());self.assertIn('creative variation id',prompt.lower())
        self.assertIn('"player":"Alpha"',prompt);self.assertIn('"player":"Bravo"',prompt);self.assertIn('"performance_rank":1',prompt);self.assertIn('"mvp":true',prompt);self.assertNotIn('Unverified',prompt);self.assertNotIn('999999',prompt)
        self.assertNotIn('rubber duck',prompt.lower());self.assertNotIn('Theme:',prompt)
        self.assertIn('Game mode: Warzone · Resurgence',prompt);self.assertIn('Result: Defeat',prompt);self.assertIn('Placement: 4th place',prompt)
    def test_match_poster_requires_verified_ocr_but_allows_missing_stat_rows(self):
        with self.assertRaisesRegex(ValueError,'actual match scoreboard'):
            E.match_poster_prompt([dict(name='Alpha',stats={'kills':12},verified=False)],WZ)
        self.assertIn('verified_statistics',E.match_poster_prompt([dict(name='Alpha',stats={},verified=True)],WZ))
    def test_reference_photo_mapping_is_appended_without_rewriting_names(self):
        prompt=E.match_poster_prompt([dict(name='Alpha',stats={'kills':12},verified=True)],WZ)
        mapped=E.prompt_with_photo_mapping(prompt,[{'player':'Alpha','file':'reference-alpha.jpg'}])
        self.assertIn('Reference 1 (reference-alpha.jpg) is Alpha',mapped)
    def test_automatic_victory_and_defeat_templates_cover_one_to_four_players(self):
        for result,claim in (('Victory','They WON'),('Defeat','They LOST')):
            for count in range(1,5):
                players=[{'name':f'Player{i}','verified':True,'stats':{'kills':i}} for i in range(1,count+1)]
                assignments=[{'username':p['name'],'filename':f"{p['name']}.jpg",'status':'ready'} for p in players]
                prompt=E.automatic_match_prompt(players,'Resurgence',result,assignments)
                self.assertIn(f'featuring these {count} people',prompt);self.assertIn(claim,prompt)
                self.assertIn('This was a Resurgence match.',prompt);self.assertIn('different every time',prompt)
                for player in players:
                    self.assertIn(f"{player['name']}: kills=",prompt)
                    self.assertIn(f"COD username {player['name']} uses reference {player['name']}.jpg",prompt)
        with self.assertRaisesRegex(ValueError,'will not guess'):
            E.automatic_match_prompt([{'name':'Player1','verified':True,'stats':{}}],'Resurgence','Not shown',[])
    def test_photo_match_withholds_ambiguous_missing_and_unconsented_headshots(self):
        from PIL import Image
        photo=self.root/'ok.jpg';Image.new('RGB',(240,240),'red').save(photo)
        players=[dict(name='[TAG]Alpha',verified=True,stats={'kills':5}),dict(name='Bravo',verified=True,stats={'kills':2}),dict(name='NoPhoto',verified=True,stats={'kills':1})]
        roster=[dict(id='a',name='Alpha',aliases=[],photo=str(photo),consent_meta=True,enabled=True),
            dict(id='b1',name='Bravo',aliases=[],photo=str(photo),consent_meta=True,enabled=True),
            dict(id='b2',name='BravO',aliases=[],photo=str(photo),consent_meta=True,enabled=True),
            dict(id='n',name='NoPhoto',aliases=[],photo='',consent_meta=True,enabled=True)]
        maps=E.match_reference_photos(players,roster)
        self.assertEqual([m['status'] for m in maps],['ready','ambiguous','missing_photo'])
        self.assertEqual(maps[0]['rosterName'],'Alpha');self.assertEqual(maps[1]['photoPath'],'')
    def test_recap_only_verified_ocr(self):
        text=E.recap(WZ,[dict(name='bad',stats={'kills':900},verified=False)]);self.assertNotIn('bad',text);self.assertIn('CODMunity local player: kills: 12',text)
    def test_deterministic_overlay_16_9(self):
        from PIL import Image
        source=self.root/'source.png';target=self.root/'out.jpg';Image.new('RGB',(800,800),'navy').save(source)
        E.render_poster(source,target,WZ,[dict(name='Alpha',stats={'kills':12},verified=True)])
        with Image.open(target) as im:self.assertEqual(im.size,(1600,900))
    def test_invalid_art_rejected(self):
        from PIL import Image
        source=self.root/'tiny.png';Image.new('RGB',(10,10)).save(source)
        with self.assertRaises(ValueError):E.render_poster(source,self.root/'out.jpg',WZ,[])
    def test_path_ids_cannot_escape_directory(self):self.assertNotIn('/',E.disk_id('../../secret'))
    def test_discord_payload_and_mentions(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self):return b'{"id":"fixture-message"}'
        calls=[]
        def opener(req,**kwargs):calls.append(req);return Response()
        result=E.send_discord('https://discord.com/api/webhooks/123/fixture',None,'verified recap',opener)
        self.assertEqual(result,'fixture-message');self.assertIn(b'"parse": []',calls[0].data);self.assertIn('wait=true',calls[0].full_url)
    def test_discord_429_retry(self):
        count=[0];delays=[]
        class Response:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self):return b'{}'
        def opener(req,**kwargs):
            count[0]+=1
            if count[0]==1:raise urllib.error.HTTPError(req.full_url,429,'rate limited',{},io.BytesIO(b'{"retry_after":2}'))
            return Response()
        E.send_discord('https://discord.com/api/webhooks/123/fixture',None,'test',opener,delays.append);self.assertEqual(count[0],2);self.assertEqual(delays,[2])
    def test_network_timeout_not_blindly_retried(self):
        def opener(*a,**k):raise TimeoutError()
        with self.assertRaisesRegex(RuntimeError,'uncertain'):E.send_discord('https://discord.com/api/webhooks/123/fixture',None,'test',opener)
    def test_discord_rejects_lookalike_host(self):
        with self.assertRaises(ValueError):E.send_discord('https://discord.com.evil.invalid/api/webhooks/123/fixture',None,'test')

    def test_codmunity_game_for_mode_buckets(self):
        self.assertEqual(E.codmunity_game_for_mode('resurgence'),'warzone')
        self.assertEqual(E.codmunity_game_for_mode('battle_royale'),'warzone')
        self.assertEqual(E.codmunity_game_for_mode('plunder'),'warzone')
        self.assertEqual(E.codmunity_game_for_mode('kill_confirmed'),'mw4')
        self.assertEqual(E.codmunity_game_for_mode('multiplayer'),'mw4')
        self.assertEqual(E.codmunity_game_for_mode('mystery_mode'),'')
        self.assertEqual(E.codmunity_game_for_mode(''),'')
        self.assertEqual(E.codmunity_game_for_mode(None),'')
    def test_enrich_codmunity_match_stamps_game_and_personal_row(self):
        match={'matchId':'legacy-1','modeKey':'resurgence','playedAt':'2026-11-07T10:30:00Z','result':'loss','stats':{'kills':9,'deaths':3}}
        enriched,players=E.enrich_codmunity_match(match,'Vintendo',[])
        self.assertEqual(enriched['game'],'warzone')
        self.assertNotIn('game',match)
        self.assertEqual(players,[{'name':'Vintendo','stats':{'kills':9,'deaths':3},'verified':True,'source':'codmunity_local'}])
    def test_enrich_leaves_unknown_modes_unstamped(self):
        match={'matchId':'legacy-2','modeKey':'mystery_mode','stats':{'kills':9,'deaths':3}}
        enriched,players=E.enrich_codmunity_match(match,'Vintendo',[])
        self.assertNotIn('game',enriched)
        self.assertEqual(len(players),1)
    def test_codmunity_game_stamp_keeps_matchid_dedup(self):
        match={'matchId':'legacy-3','modeKey':'resurgence','stats':{'kills':9,'deaths':3}}
        enriched,_=E.enrich_codmunity_match(match,'Vintendo',[])
        self.assertEqual(E.key_for(enriched),E.key_for(match))
    def test_codmunity_row_updates_backfills_legacy_row(self):
        existing={'payload':json.dumps({'integration_source':'CODMunity','modeKey':'resurgence','stats':{'kills':9,'deaths':3}}),'players':'[]'}
        enriched,_=E.enrich_codmunity_match({'matchId':'legacy-4','modeKey':'resurgence','stats':{'kills':9,'deaths':3}},'Vintendo',[])
        updates=E.codmunity_row_updates(existing,enriched,'Vintendo',[])
        self.assertEqual(updates['payload']['game'],'warzone')
        rows=json.loads(updates['players'])
        self.assertEqual(len(rows),1);self.assertTrue(rows[0]['verified'])
    def test_codmunity_row_updates_never_rewrites_complete_rows(self):
        payload={'integration_source':'CODMunity','game':'warzone','modeKey':'resurgence','stats':{'kills':9,'deaths':3}}
        players=json.dumps([{'name':'Vintendo','stats':{'kills':9,'deaths':3},'verified':True,'source':'codmunity_local'}])
        enriched,_=E.enrich_codmunity_match({**payload,'matchId':'legacy-5'},'Vintendo',[])
        self.assertEqual(E.codmunity_row_updates({'payload':json.dumps(payload),'players':players},enriched,'Vintendo',[]),{})
class CaptureTests(unittest.TestCase):
    def test_game_monitor_selection_uses_game_window_monitor_handle(self):
        from capture_engine import select_game_output
        outputs=[{'device':0,'output':0,'monitor_handle':11,'width':2560,'height':1440},
                 {'device':0,'output':1,'monitor_handle':22,'width':2560,'height':1440},
                 {'device':0,'output':2,'monitor_handle':33,'width':1920,'height':1080}]
        windows=[{'monitor_handle':11,'process_name':'pythonw.exe','title':'Vintendo COD Tracker','width':1450,'height':930},
                 {'monitor_handle':33,'process_name':'cod.exe','title':'Call of Duty','width':1920,'height':1080}]
        output,window=select_game_output(outputs,windows)
        self.assertEqual((output['device'],output['output']),(0,2));self.assertEqual(window['process_name'],'cod.exe')
    def test_game_monitor_selection_fails_closed_without_cod_window(self):
        from capture_engine import select_game_output
        outputs=[{'device':0,'output':0,'monitor_handle':11,'width':2560,'height':1440}]
        windows=[{'monitor_handle':11,'process_name':'pythonw.exe','title':'Vintendo COD Tracker','width':1450,'height':930}]
        with self.assertRaisesRegex(RuntimeError,'Could not find a visible Call of Duty'):
            select_game_output(outputs,windows)
    def test_game_screenshot_reads_only_target_output_at_native_resolution(self):
        import threading
        import numpy as np
        from capture_engine import CaptureEngine
        class Camera:
            def __init__(self,frame):self.frame=frame;self.released=False
            def grab(self,**_):return self.frame
            def release(self):self.released=True
        class FakeDXcam:
            def __init__(self):self.created=[]
            def create(self,**kw):
                self.created.append((kw['device_idx'],kw['output_idx']))
                return Camera(np.full((1080,1920,3),17,dtype=np.uint8))
        capture=object.__new__(CaptureEngine);capture.dxcam=FakeDXcam();capture.lock=threading.RLock()
        capture.camera_id=(0,0);capture.camera=Camera(np.ones((1440,2560,3),dtype=np.uint8));capture.ocr=None
        frame=capture.grab_game_output({'device':0,'output':2,'width':1920,'height':1080})
        self.assertEqual(frame.shape,(1080,1920,3));self.assertEqual(capture.dxcam.created,[(0,2),(0,0)])
        self.assertEqual(capture.camera_id,(0,0))
    def test_detector_ignores_existing_board_and_rearms(self):
        from capture_engine import StableDetector
        d=StableDetector()
        self.assertFalse(any(d.observe('board',True) for _ in range(6)))
        for _ in range(3):d.observe('',False)
        self.assertFalse(d.observe('board',True));self.assertFalse(d.observe('board',True));self.assertTrue(d.observe('board',True));self.assertFalse(d.observe('board',True))
    def test_local_ocr_models_can_read_generated_fixture(self):
        from PIL import Image,ImageDraw,ImageFont
        import numpy as np
        from capture_engine import CaptureEngine
        image=Image.new('RGB',(1200,400),'white');draw=ImageDraw.Draw(image);font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',44)
        draw.text((30,30),'PLAYER     KILLS     DAMAGE',font=font,fill='black');draw.text((30,110),'Vintendo       12           3400',font=font,fill='black');draw.text((30,190),'Concha           8           2000',font=font,fill='black')
        service=CaptureEngine();text,lines,_=service.recognize(np.array(image));self.assertIn('3400',text);self.assertTrue('vintendo' in text.casefold() or 'concha' in text.casefold());service.close()

class LegacyRegressionTests(unittest.TestCase):
    def test_warzone_and_mp_routes(self):
        m=E.legacy();self.assertEqual(m.classify_watch_match(MP,False),'multiplayer');self.assertEqual(m.classify_watch_match(WZ,False),'warzone_disabled');self.assertEqual(m.classify_watch_match(WZ,True),'warzone')
    def test_solo_original_render_post_and_dedup_pipeline(self):
        m=E.legacy()
        with fixture_dir() as tmp:
            root=Path(tmp);stats=root/'stats';stats.mkdir();E.atomic_json(stats/'matches.json',[MP]);E.atomic_json(stats/'local.json',{'stats':{'wins':10,'kills':100}})
            config={'webhook_url':'https://discord.com/api/webhooks/123/fixture','stats_dir':str(stats),'player':'vintendo','profile_url':'','poll_seconds':3}
            calls=[]
            class Response:
                status=204
                def __enter__(self):return self
                def __exit__(self,*a):pass
            class Stop:
                stopped=False
                def is_set(self):return self.stopped
                def wait(self,seconds):self.stopped=True
            original=m.read_json
            def read(path,default):return config if Path(path)==m.CONFIG else original(path,default)
            state=root/'state.json';E.atomic_json(state,{'posted_ids':[],'feed_cutoff_ms':0,'matches_since_career':0})
            with patch.object(m,'STATE',state),patch.object(m,'OUT',root/'reports'),patch.object(m,'ACHIEVEMENT_HISTORY',root/'achievements.json'),patch.object(m,'read_json',read),patch.object(sys_module(),'argv',['tracker','--watch']),patch('urllib.request.urlopen',lambda req,**kwargs:(calls.append(req) or Response())):
                m.main(Stop());self.assertEqual(len(calls),1);self.assertIn(b'WIN',calls[0].data);self.assertIn(MP['matchId'],E.read_json(state)['posted_ids']);m.main(Stop());self.assertEqual(len(calls),1)
def sys_module():
    import sys
    return sys

if __name__=='__main__':unittest.main()
