import json
import os
import urllib.error
import urllib.request
import uuid
import unittest
from pathlib import Path
from PIL import Image
import engine as E
from local_bridge import EXTENSION_ID, EXTENSION_ORIGIN, LocalBridge, MAX_IMAGE


def bridge_dir():
    path=E.DATA/('bridge-fixture-'+uuid.uuid4().hex);path.mkdir(parents=True,exist_ok=True);return path


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.root=bridge_dir();self.data=self.root/'data';self.store=E.Store(self.root/'history.sqlite3')
        self.match={'matchId':'bridge-'+uuid.uuid4().hex,'modeKey':'resurgence','playedAt':'2026-11-07T12:00:00Z','result':'win','stats':{'kills':10}}
        self.mid,_=self.store.add(self.match);players=[{'name':'Alpha','verified':True,'stats':{'kills':7,'damage':2100}},{'name':'Bravo','verified':True,'stats':{'kills':3}}]
        self.store.update(self.mid,players=json.dumps(players),prompt='Create a 16:9 horizontal comedy poster.',gen_state='queued')
        for name,color in (('Alpha','orange'),('Bravo','blue')):
            photo=self.root/(name.lower()+'-default.jpg');Image.new('RGB',(320,240),color).save(photo)
            self.store.save_player({'id':name.lower(),'name':name,'aliases':[],'photo':str(photo),'consent_meta':True,'enabled':True})
        self.events=[];self.prepared=[]
        self.bridge=LocalBridge(self.store,self.data,'test-token-'+uuid.uuid4().hex,on_artwork=lambda *a:self.events.append(a),on_prepare=lambda *a:self.prepared.append(a),on_log=lambda *_:None,port=0)
        port=self.bridge.start();self.url=f'http://127.0.0.1:{port}/v1';self.addCleanup(self.bridge.close)
    def request(self,path,method='GET',body=None,*,origin=EXTENSION_ORIGIN,token=None,extension_id=EXTENSION_ID,request_headers='authorization, x-vintendo-extension-id',content_type='application/json',host=None):
        headers={'Origin':origin,'Authorization':'Bearer '+(token or self.bridge.token),'X-Vintendo-Extension-ID':extension_id}
        if method=='OPTIONS':headers['Access-Control-Request-Headers']=request_headers
        if content_type:headers['Content-Type']=content_type
        if host:headers['Host']=host
        if body is not None:headers['Content-Length']=str(len(body))
        req=urllib.request.Request(self.url+path,data=body,headers=headers,method=method)
        try:
            with urllib.request.urlopen(req,timeout=3) as response:return response.status,response.read()
        except urllib.error.HTTPError as exc:return exc.code,exc.read()
    def test_loopback_and_authenticated_snapshot(self):
        self.assertEqual(self.bridge.server.server_address[0],'127.0.0.1')
        status,body=self.request('/status');self.assertEqual(status,200)
        data=json.loads(body);job=data['jobs'][0];self.assertEqual(job['jobId'],self.mid);self.assertEqual(job['aspectRatio'],'16:9')
        self.assertEqual([x['username'] for x in job['squad']],['Alpha','Bravo']);self.assertEqual(job['squad'][0]['statistics']['kills'],7)
        self.assertNotIn('webhook',json.dumps(data).lower())

    def test_old_review_rows_are_not_automatic_jobs(self):
        from local_bridge import public_job
        old={'matchId':'historic-'+uuid.uuid4().hex,'modeKey':'resurgence','playedAt':'2026-11-06T12:00:00Z','result':'win'}
        old_mid,_=self.store.add(old,'needs_review')
        legacy=self.store.get(old_mid)
        self.assertEqual(public_job(self.store,legacy,self.data)['state'],'not_queued')
        status,body=self.request('/status')
        self.assertEqual(status,200)
        snapshot=json.loads(body)
        self.assertEqual(snapshot['pendingCount'],1)
        self.assertNotIn(old_mid,{job['jobId'] for job in snapshot['jobs']})

    def test_explicitly_queued_review_row_is_pending(self):
        from local_bridge import public_job
        row=self.store.get(self.mid)
        self.assertEqual(public_job(self.store,row,self.data)['state'],'pending')

    def test_manual_retry_requeues_only_the_selected_match(self):
        self.store.update(self.mid,status='failed',gen_state='error',error='Previous attempt failed.')
        status,body=self.request(f'/jobs/{self.mid}/retry','POST',b'{}')
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['job']['state'],'pending')
    def test_bad_bearer_rejected(self):self.assertEqual(self.request('/status',token='wrong')[0],401)
    def test_wrong_origin_rejected(self):self.assertEqual(self.request('/status',origin='https://example.com')[0],403)
    def test_meta_page_origin_is_limited_to_authenticated_artwork_uploads(self):
        from PIL import Image
        self.store.update(self.mid,status='awaiting_artwork',gen_state='awaiting_download')
        source=self.root/'meta-transfer.png';Image.new('RGB',(640,360),'navy').save(source)
        status,body=self.request(f'/jobs/{self.mid}/artwork','POST',source.read_bytes(),origin='https://www.meta.ai',content_type='image/png')
        self.assertEqual(status,201);self.assertIn(b'artwork',body)
        self.assertEqual(self.request('/status',origin='https://www.meta.ai')[0],403)
        self.assertEqual(self.request(f'/jobs/{self.mid}/artwork',origin='https://example.com')[0],403)
    def test_originless_unpackaged_chrome_request_uses_token_authentication(self):
        self.assertEqual(self.request('/status',origin='')[0],200)
        self.assertEqual(self.request('/status',origin='',extension_id='a'*32)[0],200)
        self.assertEqual(self.request('/status',origin='',token='wrong')[0],401)
        self.assertEqual(self.request('/status',origin='',extension_id='wrong')[0],403)
        self.assertEqual(self.request('/status',origin='https://example.com')[0],403)
    def test_wrong_host_rejected(self):self.assertEqual(self.request('/status',host='127.0.0.1:9')[0],403)
    def test_valid_preflight_and_wrong_origin_preflight(self):
        self.assertEqual(self.request('/status','OPTIONS',origin=EXTENSION_ORIGIN)[0],204)
        self.assertEqual(self.request('/status','OPTIONS',origin='https://bad.test')[0],403)
    def test_meta_artwork_preflight_is_route_and_header_scoped(self):
        self.assertEqual(self.request(f'/jobs/{self.mid}/artwork','OPTIONS',origin='https://www.meta.ai',request_headers='authorization, content-type, x-vintendo-extension-id')[0],204)
        self.assertEqual(self.request('/status','OPTIONS',origin='https://www.meta.ai',request_headers='authorization, content-type, x-vintendo-extension-id')[0],403)
        self.assertEqual(self.request(f'/jobs/{self.mid}/artwork','OPTIONS',origin='https://meta.ai',request_headers='authorization')[0],403)
    def test_originless_preflight_requires_extension_identity_and_auth_headers(self):
        self.assertEqual(self.request('/status','OPTIONS',origin='')[0],204)
        self.assertEqual(self.request('/status','OPTIONS',origin='',request_headers='authorization')[0],403)
    def test_prepare_stages_only_consenting_headshots_and_prompt(self):
        self.store.update(self.mid,players=json.dumps([{'name':'Alpha','verified':True,'stats':{'kills':7}}]))
        photo=self.root/'alpha.jpg';Image.new('RGB',(640,480),'orange').save(photo)
        self.store.save_player({'id':'alpha','name':'Alpha','aliases':[],'photo':str(photo),'consent_meta':True,'enabled':True})
        other=self.root/'bravo.jpg';Image.new('RGB',(640,480),'blue').save(other)
        self.store.save_player({'id':'bravo','name':'Bravo','aliases':[],'photo':str(other),'consent_meta':False,'enabled':True})
        status,body=self.request(f'/jobs/{self.mid}/prepare','POST',b'{}');self.assertEqual(status,200)
        data=json.loads(body);self.assertEqual(len(data['referencePhotos']),1);self.assertEqual(data['referencePhotos'][0]['username'],'Alpha')
        self.assertIn('16:9',data['job']['prompt']);self.assertEqual(data['job']['state'],'prepared')
        self.assertEqual(data['job']['mode'],'Resurgence');self.assertEqual(data['job']['result'],'Victory')
        mappings={x['username']:x for x in data['job']['referencePhotoMappings']}
        self.assertEqual(mappings['Alpha']['status'],'ready');self.assertNotIn('Bravo',mappings)
        self.assertEqual(mappings['Alpha']['filename'],data['referencePhotos'][0]['filename'])
        self.assertEqual(len(list(Path(data['referenceFolder']).glob('reference-*.jpg'))),1)
        import_file=Path(data['referenceFolder'])/'Meta-AI-Automation-Import.txt'
        self.assertTrue(import_file.is_file());self.assertEqual(len(import_file.read_text(encoding='utf-8').splitlines()),1)
        self.assertIn('Reference image assignments:',import_file.read_text(encoding='utf-8'))
        mapping_csv=Path(data['referenceFolder'])/'reference-photo-mapping.csv';self.assertTrue(mapping_csv.is_file())
        instructions=Path(data['referenceFolder'])/'Vintendo-Squad-Import-Instructions.txt'
        self.assertIn(self.mid,instructions.read_text(encoding='utf-8'))
        image_status,image=self.request(f"/jobs/{self.mid}/photos/{data['referencePhotos'][0]['filename']}")
        self.assertEqual(image_status,200);self.assertEqual(image,Path(data['referenceFolder'],data['referencePhotos'][0]['filename']).read_bytes())

    def test_four_player_job_stages_four_identity_mapped_headshots(self):
        players=[{'name':name,'verified':True,'stats':{'kills':4-index}}
                 for index,name in enumerate(('Alpha','Bravo','Charlie','Delta'))]
        self.store.update(self.mid,players=json.dumps(players))
        for name,color in zip(('Alpha','Bravo','Charlie','Delta'),('red','blue','green','yellow')):
            photo=self.root/(name.lower()+'.jpg');Image.new('RGB',(320,240),color).save(photo)
            self.store.save_player({'id':name.lower(),'name':name,'aliases':[],'photo':str(photo),
                                    'consent_meta':True,'enabled':True})
        status,body=self.request(f'/jobs/{self.mid}/prepare','POST',b'{}');self.assertEqual(status,200)
        data=json.loads(body)
        self.assertEqual([x['username'] for x in data['referencePhotos']],['Alpha','Bravo','Charlie','Delta'])
        mappings={x['username']:x for x in data['job']['referencePhotoMappings']}
        self.assertEqual(set(mappings),{'Alpha','Bravo','Charlie','Delta'})
        self.assertEqual({x['matchedRosterName'] for x in mappings.values()},set(mappings))
        self.assertEqual({x['filename'] for x in mappings.values()},
                         {x['filename'] for x in data['referencePhotos']})
        self.assertEqual(len(list(Path(data['referenceFolder']).glob('reference-*.jpg'))),4)
        self.assertEqual(self.prepared[0][0],self.mid)

    def test_missing_headshot_does_not_block_job_and_keeps_player_specific_reason(self):
        self.store.update(self.mid,players=json.dumps([{'name':'Alpha','verified':True,'stats':{'kills':4}},
                                                        {'name':'NoPhoto','verified':True,'stats':{'kills':2}}]))
        photo=self.root/'alpha.jpg';Image.new('RGB',(320,240),'orange').save(photo)
        self.store.save_player({'id':'alpha','name':'Alpha','aliases':[],'photo':str(photo),'consent_meta':True,'enabled':True})
        self.store.save_player({'id':'no-photo','name':'NoPhoto','aliases':[],'photo':'','consent_meta':True,'enabled':True})
        status,body=self.request(f'/jobs/{self.mid}/prepare','POST',b'{"automatic":true}')
        self.assertEqual(status,200)
        data=json.loads(body)
        self.assertEqual([item['username'] for item in data['referencePhotos']],['Alpha'])
        mappings={item['username']:item for item in data['job']['referencePhotoMappings']}
        self.assertEqual(mappings['NoPhoto']['status'],'missing_photo')
        self.assertIn('no available photo',mappings['NoPhoto']['reason'])

    def test_partial_headshots_proceed_with_ready_subset_and_recorded_reason(self):
        self.store.update(self.mid,players=json.dumps([{'name':'Alpha','verified':True,'stats':{'kills':4}},
                                                        {'name':'NoPhoto','verified':True,'stats':{'kills':2}}]))
        photo=self.root/'alpha.jpg';Image.new('RGB',(320,240),'orange').save(photo)
        self.store.save_player({'id':'alpha','name':'Alpha','aliases':[],'photo':str(photo),'consent_meta':True,'enabled':True})
        self.store.save_player({'id':'no-photo','name':'NoPhoto','aliases':[],'photo':str(self.root/'absent.jpg'),'consent_meta':True,'enabled':True})
        prepared=self.bridge.prepare(self.mid,open_folder=False)
        self.assertEqual([item['username'] for item in prepared['photos']],['Alpha'])
        row=self.store.get(self.mid)
        self.assertEqual(row['gen_state'],'prepared')
        self.assertIn('NO REFERENCE IMAGE',prepared['prompt'])
        self.assertIn('NoPhoto',prepared['prompt'])
        from local_bridge import public_job
        mappings={item['username']:item for item in public_job(self.store,row,self.data)['referencePhotoMappings']}
        self.assertEqual(mappings['Alpha']['status'],'ready')
        self.assertEqual(mappings['NoPhoto']['status'],'missing_photo')
        self.assertTrue(mappings['NoPhoto']['reason'])

    def test_codommunity_solo_victory_and_defeat_use_configured_player(self):
        from PIL import Image
        photo=self.root/'vintendo.jpg';Image.new('RGB',(320,240),'orange').save(photo)
        self.store.save_player({'id':'vintendo','name':'Vintendo','aliases':[],'photo':str(photo),'consent_meta':True,'enabled':True})
        self.bridge.solo_player_name='Vintendo'
        for result,phrase in (('win','They WON'),('loss','They LOST')):
            record={'matchId':'solo-'+result+'-'+uuid.uuid4().hex,'integration_source':'CODMunity',
                    'modeKey':'resurgence','playedAt':f"2026-11-07T12:0{1 if result=='win' else 2}:00Z",'result':result,
                    'stats':{'kills':17,'damage':3210}}
            mid,_=self.store.add(record)
            prepared=self.bridge.prepare(mid,open_folder=False)
            row=self.store.get(mid);players=json.loads(row['players'])
            self.assertEqual([player['name'] for player in players],['Vintendo'])
            self.assertIn(phrase,prepared['prompt']);self.assertIn('Vintendo: kills=17, damage=3210',prepared['prompt'])
            self.assertEqual(len(prepared['photos']),1)

    def test_bridge_prepares_all_player_counts_for_both_outcomes(self):
        from PIL import Image
        from local_bridge import public_job
        for outcome, result, verb in (('Victory', 'win', 'They WON'), ('Defeat', 'loss', 'They LOST')):
            for count in range(1, 5):
                names=[f'{outcome}{count}Player{index+1}' for index in range(count)]
                record={'matchId':f'matrix-{outcome.lower()}-{count}-{uuid.uuid4().hex}',
                        'integration_source':'squad_ocr','game':'Warzone','modeKey':'resurgence',
                        'playedAt':f'2026-11-07T12:{count:02d}:00Z','result':result}
                mid,_=self.store.add(record)
                rows=[]
                for index,name in enumerate(names):
                    rows.append({'name':name,'verified':True,'stats':{'kills':index+2,'damage':1200+index}})
                    photo=self.root/(name+'.jpg');Image.new('RGB',(320,240),(index*40,50,200-index*30)).save(photo)
                    self.store.save_player({'id':name.lower(),'name':name,'aliases':[],'photo':str(photo),
                                            'consent_meta':True,'enabled':True})
                self.store.update(mid,players=json.dumps(rows))
                prepared=self.bridge.prepare(mid,open_folder=False)
                job=public_job(self.store,self.store.get(mid),self.data)
                self.assertEqual(job['playerCount'],count)
                self.assertEqual(len(prepared['photos']),count)
                self.assertEqual({photo['username'] for photo in prepared['photos']},set(names))
                self.assertIn(verb,prepared['prompt'])
                self.assertIn('Warzone · Resurgence',prepared['prompt'])
                for row in rows:
                    self.assertIn(f"{row['name']}: kills=",prepared['prompt'])
                    mapping=next(item for item in job['referencePhotoMappings'] if item['username']==row['name'])
                    photo=next(item for item in prepared['photos'] if item['username']==row['name'])
                    self.assertEqual(mapping['status'],'ready')
                    self.assertEqual(mapping['filename'],photo['filename'])

    def test_unknown_outcome_is_not_converted_to_victory_or_defeat(self):
        unknown={'matchId':'unknown-'+uuid.uuid4().hex,'integration_source':'CODMunity',
                 'modeKey':'resurgence','playedAt':'2026-11-07T12:00:00Z','result':'match',
                 'stats':{'kills':4}}
        mid,_=self.store.add(unknown);self.bridge.solo_player_name='Vintendo'
        self.assertEqual(self.request(f'/jobs/{mid}/prepare','POST',b'{"automatic":true}')[0],422)

    def test_dynamic_unpackaged_extension_id_is_authenticated_by_token(self):
        extension_id='a'*32;origin='chrome-extension://'+extension_id
        status,body=self.request('/status',origin=origin,extension_id=extension_id)
        self.assertEqual(status,200);self.assertIn(b'connected',body)

    def test_poster_job_rejects_more_than_four_identified_participants(self):
        names=('Alpha','Bravo','Charlie','Delta','Echo')
        self.store.update(self.mid,players=json.dumps([{'name':name,'verified':True,'stats':{'kills':5-index}}
                                                        for index,name in enumerate(names)]))
        for name in names:
            photo=self.root/(name.lower()+'.jpg');Image.new('RGB',(320,240),'purple').save(photo)
            self.store.save_player({'id':name.lower(),'name':name,'aliases':[],'photo':str(photo),
                                    'consent_meta':True,'enabled':True})
        status,body=self.request(f'/jobs/{self.mid}/prepare','POST',b'{}');self.assertEqual(status,422)
        self.assertIn('one to four',json.loads(body)['error'])
    def test_close_matches_get_separate_metadata_photo_packs_and_exact_job_ids(self):
        from local_bridge import public_job
        from PIL import Image
        photo_a=self.root/'alpha.jpg';photo_b=self.root/'charlie.jpg'
        Image.new('RGB',(320,320),'red').save(photo_a);Image.new('RGB',(320,320),'blue').save(photo_b)
        self.store.save_player({'id':'alpha','name':'Alpha','photo':str(photo_a),'consent_meta':True,'enabled':True,'aliases':[]})
        self.store.save_player({'id':'bravo','name':'Bravo','photo':str(photo_b),'consent_meta':True,'enabled':True,'aliases':[]})
        self.store.save_player({'id':'charlie','name':'Charlie','photo':str(photo_b),'consent_meta':True,'enabled':True,'aliases':[]})
        self.store.save_player({'id':'kilo1','name':'Kilo','photo':str(photo_b),'consent_meta':True,'enabled':True,'aliases':[]})
        self.store.save_player({'id':'kilo2','name':'Kilo','photo':str(photo_a),'consent_meta':True,'enabled':True,'aliases':[]})
        b_match={'matchId':'close-b-'+uuid.uuid4().hex,'game':'warzone','modeKey':'resurgence','result':'loss','placement':3,'playedAt':'2026-11-07T12:01:00Z'}
        b_mid,_=self.store.add(b_match)
        self.store.update(b_mid,ocr='3rd Place',players=json.dumps([{'name':'[CL]Charlie','verified':True,'stats':{'kills':11,'damage':2900}}]))
        a=self.bridge.prepare(self.mid);b=self.bridge.prepare(b_mid)
        self.assertNotEqual(a['folder'],b['folder'])
        self.assertIn('Warzone · Resurgence',b['prompt']);self.assertIn('They LOST',b['prompt'])
        self.assertIn('Alpha',a['prompt']);self.assertNotIn('Charlie',a['prompt'])
        self.assertIn('[CL]Charlie',b['prompt']);self.assertNotIn('Alpha',b['prompt'])
        row_a=self.store.get(self.mid);row_b=self.store.get(b_mid)
        meta_a=public_job(self.store,row_a,self.data);meta_b=public_job(self.store,row_b,self.data)
        self.assertEqual(meta_a['matchId'],self.mid);self.assertEqual(meta_b['matchId'],b_mid)
        self.assertEqual(meta_a['referencePhotoMappings'][0]['username'],'Alpha')
        self.assertEqual(meta_b['referencePhotoMappings'][0]['username'],'[CL]Charlie')
        self.assertEqual(meta_b['referencePhotoMappings'][0]['matchedRosterName'],'Charlie')
        self.assertEqual(len(meta_b['referencePhotoMappings']),1)
        self.assertNotEqual(meta_a['referencePhotoMappings'][0]['filename'],meta_b['referencePhotoMappings'][0]['filename'])
        self.assertEqual(len(a['photos']),2);self.assertEqual(len(b['photos']),1)
        # Matches can queue and prepare close together, but each finished image
        # is accepted only by the immutable job ID its extension sends.
        self.store.update(self.mid,status='awaiting_artwork',gen_state='awaiting_download')
        self.store.update(b_mid,status='awaiting_artwork',gen_state='awaiting_download')
        art_a=self.root/'art-a.png';art_b=self.root/'art-b.png'
        Image.new('RGB',(640,360),'red').save(art_a);Image.new('RGB',(640,360),'blue').save(art_b)
        for mid,path in ((b_mid,art_b),(self.mid,art_a)):
            status,_=self.request(f'/jobs/{mid}/artwork','POST',path.read_bytes(),content_type='image/png')
            self.assertEqual(status,201)
        self.assertEqual([event[0] for event in self.events[-2:]],[b_mid,self.mid])
        self.assertTrue(Path(self.store.get(self.mid)['artwork']).is_file())
        self.assertTrue(Path(self.store.get(b_mid)['artwork']).is_file())
        self.assertNotEqual(self.store.get(self.mid)['artwork'],self.store.get(b_mid)['artwork'])
    def test_state_transitions_require_prepare_then_submission(self):
        body=b'{"state":"awaiting_download"}'
        self.assertEqual(self.request(f'/jobs/{self.mid}/state','POST',body)[0],409)
        self.assertEqual(self.request(f'/jobs/{self.mid}/prepare','POST',b'{}')[0],200)
        self.assertEqual(self.request(f'/jobs/{self.mid}/state','POST',b'{"state":"awaiting_generation"}')[0],200)
        self.assertEqual(self.request(f'/jobs/{self.mid}/state','POST',body)[0],200)
        self.assertEqual(self.request(f'/jobs/{self.mid}/state','POST',b'{"state":"prepared"}')[0],409)
    def test_wrong_job_id_is_not_found(self):self.assertEqual(self.request('/jobs/not-a-match')[0],404)
    def test_invalid_content_type_rejected(self):
        self.store.update(self.mid,status='awaiting_artwork',gen_state='awaiting_download')
        self.assertEqual(self.request(f'/jobs/{self.mid}/artwork','POST',b'abc',content_type='text/plain')[0],415)
    def test_artwork_is_rejected_before_download_state(self):
        self.store.update(self.mid,status='awaiting_artwork',gen_state='prepared')
        source=self.root/'poster.png';Image.new('RGB',(640,360),'navy').save(source)
        self.assertEqual(self.request(f'/jobs/{self.mid}/artwork','POST',source.read_bytes(),content_type='image/png')[0],409)
    def test_invalid_image_rejected(self):
        self.store.update(self.mid,status='awaiting_artwork',gen_state='awaiting_download')
        self.assertEqual(self.request(f'/jobs/{self.mid}/artwork','POST',b'bad image',content_type='image/png')[0],422)
    def test_valid_download_is_sanitized_and_bound_to_selected_job(self):
        self.store.update(self.mid,status='awaiting_artwork',gen_state='awaiting_download')
        source=self.root/'poster.webp';Image.new('RGB',(640,360),'navy').save(source,format='WEBP');body=source.read_bytes()
        status,payload=self.request(f'/jobs/{self.mid}/artwork','POST',body,content_type='image/webp')
        self.assertEqual(status,201);data=json.loads(payload);self.assertEqual(data['image']['width'],640);self.assertEqual(data['image']['height'],360)
        self.assertEqual(len(self.events),1);self.assertEqual(self.events[0][0],self.mid)
        saved=Path(self.store.get(self.mid)['artwork']);self.assertTrue(saved.is_file());self.assertEqual(saved.suffix,'.png')
        with Image.open(saved) as image:self.assertEqual(image.format,'PNG');self.assertEqual(image.size,(640,360))
    def test_completed_job_rejects_replacement_artwork(self):
        self.store.update(self.mid,status='delivered',gen_state='done')
        source=self.root/'poster.png';Image.new('RGB',(640,360),'navy').save(source)
        self.assertEqual(self.request(f'/jobs/{self.mid}/artwork','POST',source.read_bytes(),content_type='image/png')[0],409)
    def test_duplicate_upload_is_atomically_rejected(self):
        self.store.update(self.mid,status='awaiting_artwork',gen_state='awaiting_download')
        source=self.root/'poster.png';Image.new('RGB',(640,360),'navy').save(source);body=source.read_bytes()
        self.assertEqual(self.request(f'/jobs/{self.mid}/artwork','POST',body,content_type='image/png')[0],201)
        self.assertEqual(self.request(f'/jobs/{self.mid}/artwork','POST',body,content_type='image/png')[0],409)


    def test_cinematic_queue_is_separate_and_claimed_once(self):
        cid='cin-'+uuid.uuid4().hex;image=self.root/'game.png';Image.new('RGB',(64,36),'black').save(image)
        self.assertTrue(self.store.create_cinematic(cid,'game.png',image,'animate this'))
        self.assertFalse(self.store.create_cinematic(cid,'game.png',image,'animate this'))
        status,body=self.request('/cinematics/pending');self.assertEqual(status,200);self.assertEqual(json.loads(body)['jobs'][0]['id'],cid)
        status,body=self.request(f'/cinematics/{cid}/claim','POST',b'{}');self.assertEqual(status,200);self.assertEqual(json.loads(body)['job']['status'],'running')
        self.assertEqual(self.request(f'/cinematics/{cid}/claim','POST',b'{}')[0],409)
        status,body=self.request(f'/cinematics/{cid}/image');self.assertEqual(status,200);self.assertTrue(body.startswith(b'\x89PNG'))
        self.assertEqual(self.store.history()[0]['id'],self.mid)
    def test_cinematic_mp4_upload_requires_complete_container_and_rejects_duplicates(self):
        cid='cin-'+uuid.uuid4().hex;image=self.root/'game.png';Image.new('RGB',(64,36),'black').save(image)
        self.store.create_cinematic(cid,'game.png',image,'animate this');self.request(f'/cinematics/{cid}/claim','POST',b'{}');self.request(f'/cinematics/{cid}/state','POST',b'{"state":"awaiting_download"}')
        self.assertEqual(self.request(f'/cinematics/{cid}/video','POST',b'not an mp4'+b'x'*1400,content_type='video/mp4')[0],415)
        fixture=b'\0\0\0\x18ftypisom'+b'\0'*12+b'moov'+b'\0'*16+b'mdat'+b'x'*1400
        status,body=self.request(f'/cinematics/{cid}/video','POST',fixture,content_type='video/mp4');self.assertEqual(status,201);self.assertEqual(json.loads(body)['job']['status'],'completed')
        self.assertEqual(self.request(f'/cinematics/{cid}/video','POST',fixture,content_type='video/mp4')[0],409)
    def test_handler_failure_returns_readable_500_instead_of_dropped_connection(self):
        def boom(*args,**kwargs):raise RuntimeError('simulated store failure')
        self.store.pending_cinematics=boom
        status,body=self.request('/cinematics/pending')
        self.assertEqual(status,500);self.assertIn('bridge failed',body.decode())
    def test_cinematic_talent_reference_is_served_only_when_stored(self):
        self.assertEqual(self.request('/cinematics/talent')[0],404)
        folder=self.data/'Cinematics';folder.mkdir(parents=True,exist_ok=True)
        Image.new('RGB',(64,36),'black').save(folder/'talent.png')
        status,body=self.request('/cinematics/talent');self.assertEqual(status,200);self.assertTrue(body.startswith(b'\x89PNG'))
    def test_per_job_talent_list_and_files(self):
        cid='cin-'+uuid.uuid4().hex;image=self.root/'game.png';Image.new('RGB',(64,36),'black').save(image)
        self.assertTrue(self.store.create_cinematic(cid,'game.png',image,'animate this',talent_ids=['ppp','qqq']))
        staged=self.data/'Cinematics'/cid;staged.mkdir(parents=True,exist_ok=True)
        Image.new('RGB',(32,32),'red').save(staged/'talent-ppp.png')
        status,body=self.request(f'/cinematics/{cid}/talent');self.assertEqual(status,200)
        entries=json.loads(body)['talent'];self.assertEqual(len(entries),1);self.assertEqual(entries[0]['player_id'],'ppp')
        self.assertTrue(entries[0]['url'].endswith(f'/cinematics/{cid}/talent/ppp.png'))
        status,body=self.request(f'/cinematics/{cid}/talent/ppp.png');self.assertEqual(status,200);self.assertTrue(body.startswith(b'\x89PNG'))
        self.assertEqual(self.request(f'/cinematics/{cid}/talent/qqq.png')[0],404)
        self.assertEqual(self.request('/cinematics/cin-missing/talent')[0],404)
    def test_per_job_talent_list_empty_without_friends(self):
        cid='cin-'+uuid.uuid4().hex;image=self.root/'game2.png';Image.new('RGB',(64,36),'black').save(image)
        self.store.create_cinematic(cid,'game2.png',image,'animate this')
        status,body=self.request(f'/cinematics/{cid}/talent');self.assertEqual(status,200);self.assertEqual(json.loads(body)['talent'],[])
    def test_meta_origin_preflight_is_limited_to_cinematic_video_upload(self):
        cid='cin-'+uuid.uuid4().hex;image=self.root/'game.png';Image.new('RGB',(64,36),'black').save(image);self.store.create_cinematic(cid,'game.png',image,'animate this')
        headers='authorization, content-type, x-vintendo-extension-id'
        self.assertEqual(self.request(f'/cinematics/{cid}/video','OPTIONS',origin='https://www.meta.ai',request_headers=headers)[0],204)
        self.assertEqual(self.request('/cinematics/pending','OPTIONS',origin='https://www.meta.ai',request_headers=headers)[0],403)


if __name__=='__main__':unittest.main()
