"""Authenticated loopback bridge for the Vintendo Squad AI Chrome extension."""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

import engine as E

BRIDGE_HOST = '127.0.0.1'
BRIDGE_PORT = 8765
MAX_JSON = 128 * 1024
MAX_IMAGE = 20 * 1024 * 1024
MAX_VIDEO = 250 * 1024 * 1024
EXTENSION_ID = 'dmkkcacnncdkdndjbjhcheijfbneklef'
EXTENSION_ORIGIN = 'chrome-extension://' + EXTENSION_ID
JOB_ID_RE = re.compile(r'^[A-Za-z0-9_.:-]{1,160}$')

def valid_mp4(data):
    return len(data)>=1024 and data[4:8]==b'ftyp' and b'moov' in data and b'mdat' in data

GEN_STATES = {'prepared', 'awaiting_generation', 'awaiting_download', 'cancelled'}


def _json(value, default):
    try: return json.loads(value) if isinstance(value, str) else default
    except (ValueError, TypeError): return default


def public_job(store, row, data_dir):
    """Return only fields needed by the extension; never serialize webhook/config."""
    match = _json(row.get('payload'), {})
    players = _json(row.get('players'), [])
    verified = [p for p in players if isinstance(p, dict) and p.get('verified')]
    generation = row.get('gen_state') or 'idle'
    status = row.get('status') or ''
    if generation == 'cancelled': state = 'cancelled'
    elif status in ('delivered', 'solo_delivered'): state = 'completed'
    elif status in ('failed', 'uncertain'): state = 'failed'
    elif status in ('art_ready', 'rendering', 'ready', 'posting'): state = 'processing'
    elif generation == 'awaiting_download': state = 'awaiting_download'
    elif generation == 'awaiting_generation': state = 'awaiting_generation'
    elif generation in ('prepared', 'staged'): state = 'prepared'
    elif generation == 'queued': state = 'pending'
    elif status in ('needs_review', 'observed'): state = 'not_queued'
    else: state = 'idle'
    mappings=E.match_reference_photos(verified,store.roster())
    handoff=Path(data_dir)/'handoff'/E.disk_id(row['id'])/'photo-assignments.json'
    staged=E.read_json(handoff,None)
    if isinstance(staged,list):
        by_user={str(item.get('username','')):item for item in staged if isinstance(item,dict)}
        for item in mappings:
            saved=by_user.get(item['username'])
            if saved:item.update({key:saved[key] for key in ('status','reason','matchedRosterName','filename','consented') if key in saved})
    else:
        for item in mappings:
            if item['status']=='ready':item['status']='ready_to_stage'
    photo_count=sum(1 for p in mappings if p.get('status')=='ready' and p.get('filename'))
    prompt = str(row.get('prompt') or '')
    context=E.extract_match_context(match,row.get('ocr',''))
    ranked=E.rank_verified_players(verified,match)
    score_by_name={p['name']:p for p in ranked}
    return {
        'jobId': row['id'], 'matchId': row['id'], 'state': state,
        'playerCount': len(verified),
        'mode': context['game_mode'],'result':context['result'],'placement':context['placement'],
        'playedAt': str(match.get('playedAt', '')),
        'squad': [{'username': str(p.get('name', '')),
                   'statistics': p.get('stats', {}) if isinstance(p.get('stats'), dict) else {},
                   'verified': True,
                   'performanceRank':score_by_name.get(str(p.get('name','')),{}).get('rank'),
                   'rankingBasis':score_by_name.get(str(p.get('name','')),{}).get('ranking_basis',[]),
                   'mvp':bool(score_by_name.get(str(p.get('name','')),{}).get('mvp'))} for p in verified],
        'referencePhotoMappings':[{'username':item['username'],'status':item.get('status','unmatched'),
            'matchedRosterName':item.get('matchedRosterName',item.get('rosterName','')),
            'posterName':item.get('posterName',item.get('matchedRosterName',item.get('username',''))),
            'reason':item.get('reason',''),'candidates':item.get('candidates',[]),
            'filename':item.get('filename',''),'consented':bool(item.get('consented'))} for item in mappings],
        'referencePhotoCount': photo_count,
        'prompt': prompt,
        'aspectRatio': '16:9',
        'expectedOutputFolder': str(Path(data_dir) / 'artwork'),
        'generationAttempts': int(row.get('gen_attempts') or 0),
        'postAttempts': int(row.get('attempts') or 0),
        'artworkReady': bool(row.get('artwork')),
        'error': str(row.get('error') or '')[:300],
        'lastUpdated': float(row.get('updated') or 0),
    }


class LocalBridge:
    def __init__(self, store, data_dir, token, *, host=BRIDGE_HOST, port=BRIDGE_PORT,
                 origin=EXTENSION_ORIGIN, solo_player_name='', on_artwork=None, on_prepare=None, on_log=None, videos_dir=None):
        self.store, self.data_dir, self.token = store, Path(data_dir), str(token)
        self.host, self.port, self.origin = host, int(port), str(origin)
        self.solo_player_name=str(solo_player_name or '').strip()
        self.on_artwork = on_artwork or (lambda *_: None)
        self.on_prepare = on_prepare or (lambda *_: None)
        self.on_log = on_log or (lambda *_: None)
        self.videos_dir = Path(videos_dir) if videos_dir else None
        self.server = None
        self.thread = None
        self._prepare_lock = threading.Lock()

    @property
    def running(self):
        return bool(self.server and self.thread and self.thread.is_alive())

    def videos(self):
        folder=self.videos_dir if getattr(self,'videos_dir',None) else self.data_dir/'Cinematics'
        folder.mkdir(parents=True,exist_ok=True);return folder

    def start(self):
        if self.running: return self.server.server_address[1]
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = 'VintendoLocal/1'
            sys_version = ''
            def log_message(self, *_): pass
            def _is_artwork_route(self):
                return (re.fullmatch(r'/v1/jobs/[A-Za-z0-9_.:-]{1,160}/artwork', urlsplit(self.path).path) is not None or
                        re.fullmatch(r'/v1/cinematics/[A-Za-z0-9_.:-]{1,160}/video', urlsplit(self.path).path) is not None)
            def _allowed_page_origin(self, origin):
                return self._is_artwork_route() and origin in ('https://meta.ai', 'https://www.meta.ai')
            def _reply(self, code, value=None, *, content_type='application/json', extra=None):
                body = b'' if value is None else (value if isinstance(value, bytes) else json.dumps(value, separators=(',', ':')).encode())
                self.send_response(code)
                self.send_header('Content-Type', content_type)
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                incoming_origin=self.headers.get('Origin','')
                if re.fullmatch(r'chrome-extension://[a-p]{32}', incoming_origin) or self._allowed_page_origin(incoming_origin):
                    self.send_header('Access-Control-Allow-Origin', incoming_origin)
                    self.send_header('Vary', 'Origin')
                for key, val in (extra or {}).items(): self.send_header(key, val)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                if body: self.wfile.write(body)
            def _allowed(self):
                host = self.headers.get('Host', '')
                if host != f'{owner.host}:{owner.port}':
                    self._reply(403, {'error': 'invalid local host'}); return False
                if self.client_address[0] not in ('127.0.0.1', '::1'):
                    self._reply(403, {'error': 'loopback only'}); return False
                incoming_origin=self.headers.get('Origin','')
                extension_id=self.headers.get('X-Vintendo-Extension-ID','')
                if not re.fullmatch(r'[a-p]{32}', extension_id):
                    self._reply(403, {'error': 'extension identity rejected'}); return False
                extension_origin = re.fullmatch(r'chrome-extension://[a-p]{32}', incoming_origin) and incoming_origin.rsplit('/',1)[-1] == extension_id
                if incoming_origin and not (extension_origin or self._allowed_page_origin(incoming_origin)):
                    self._reply(403, {'error': 'extension origin rejected'}); return False
                # Chrome MV3 may omit Origin on loopback extension requests.
                # The unpacked extension's runtime ID still must be well formed
                # above, and the bearer token below remains the authentication.
                authorization = self.headers.get('Authorization', '')
                supplied = authorization[7:] if authorization.startswith('Bearer ') else ''
                if not hmac.compare_digest(supplied.encode(), owner.token.encode()):
                    self._reply(401, {'error': 'pairing token rejected'}); return False
                return True
            def do_OPTIONS(self):
                if self.headers.get('Host') != f'{owner.host}:{owner.port}':
                    return self._reply(403, {'error': 'invalid local host'})
                if self.client_address[0] not in ('127.0.0.1', '::1'):
                    return self._reply(403, {'error': 'loopback only'})
                incoming_origin=self.headers.get('Origin','')
                requested_headers={h.strip().lower() for h in self.headers.get('Access-Control-Request-Headers','').split(',')}
                extension_id=self.headers.get('X-Vintendo-Extension-ID','')
                extension_origin = re.fullmatch(r'chrome-extension://[a-p]{32}', incoming_origin) and incoming_origin.rsplit('/',1)[-1] == extension_id
                meta_artwork_origin = self._allowed_page_origin(incoming_origin)
                if incoming_origin and not (extension_origin or meta_artwork_origin):
                    return self._reply(403, {'error': 'extension origin rejected'})
                required_headers={'authorization','x-vintendo-extension-id'}
                if meta_artwork_origin: required_headers.add('content-type')
                if not incoming_origin and not required_headers.issubset(requested_headers):
                    return self._reply(403, {'error': 'extension identity headers required'})
                if meta_artwork_origin and not required_headers.issubset(requested_headers):
                    return self._reply(403, {'error': 'artwork transfer headers required'})
                self._reply(204, extra={'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                    'Access-Control-Allow-Headers': 'Authorization, Content-Type, X-Filename, X-Vintendo-Extension-ID',
                    'Access-Control-Max-Age': '300'})
            def _route(self):
                parts = [unquote(p) for p in urlsplit(self.path).path.split('/') if p]
                if parts[:2] != ['v1', 'jobs']: return None, parts
                if len(parts) >= 3 and not JOB_ID_RE.fullmatch(parts[2]): return None, parts
                return (parts[2] if len(parts) >= 3 else None), parts
            def _guarded(self, fn):
                try:return fn()
                except (BrokenPipeError,ConnectionResetError):return None
                except Exception as exc:
                    try:self._reply(500,{'error':'bridge failed: '+str(exc)[:160]})
                    except Exception:pass
                    try:owner.on_log('Bridge request failed: '+str(exc)[:200])
                    except Exception:pass
                    return None
            def do_GET(self):
                return self._guarded(self._do_GET)
            def _do_GET(self):
                if not self._allowed(): return
                parts = [unquote(p) for p in urlsplit(self.path).path.split('/') if p]
                if parts == ['v1','cinematics','pending']:
                    jobs=[]
                    for row in owner.store.pending_cinematics():
                        jobs.append({k:row.get(k) for k in ('id','filename','prompt','status','created')})
                    return self._reply(200,{'jobs':jobs})
                if parts==['v1','cinematics','talent']:
                    talent=owner.videos()/'talent.png'
                    if not talent.is_file():return self._reply(404,{'error':'no cinematic talent reference is stored'})
                    if talent.stat().st_size>MAX_IMAGE:return self._reply(413,{'error':'cinematic talent reference exceeds transfer limit'})
                    return self._reply(200,talent.read_bytes(),content_type='image/png',extra={'X-Vintendo-Talent':'1'})
                if len(parts)==4 and parts[:2]==['v1','cinematics'] and parts[3]=='talent' and JOB_ID_RE.fullmatch(parts[2]):
                    row=owner.store.get_cinematic(parts[2])
                    if not row:return self._reply(404,{'error':'cinematic job not found'})
                    try:talent_ids=json.loads(row.get('talent_ids') or '[]')
                    except Exception:talent_ids=[]
                    entries=[]
                    for pid in talent_ids or []:
                        staged=owner.videos()/str(parts[2])/('talent-'+str(pid)+'.png')
                        if staged.is_file():entries.append({'player_id':str(pid),'filename':staged.name,'url':'/cinematics/'+str(parts[2])+'/talent/'+str(pid)+'.png'})
                    return self._reply(200,{'talent':entries})
                if len(parts)==5 and parts[:2]==['v1','cinematics'] and parts[3]=='talent' and JOB_ID_RE.fullmatch(parts[2]):
                    row=owner.store.get_cinematic(parts[2])
                    try:talent_ids=json.loads((row or {}).get('talent_ids') or '[]')
                    except Exception:talent_ids=[]
                    pid=parts[4][:-4] if parts[4].endswith('.png') else ''
                    if not row or str(pid) not in [str(x) for x in talent_ids or []]:
                        return self._reply(404,{'error':'cinematic talent reference not found'})
                    staged=owner.videos()/str(parts[2])/('talent-'+pid+'.png')
                    if not staged.is_file():return self._reply(404,{'error':'cinematic talent reference not found'})
                    if staged.stat().st_size>MAX_IMAGE:return self._reply(413,{'error':'cinematic talent reference exceeds transfer limit'})
                    return self._reply(200,staged.read_bytes(),content_type='image/png',extra={'X-Vintendo-Talent':pid})
                if len(parts)==3 and parts[:2]==['v1','cinematics'] and JOB_ID_RE.fullmatch(parts[2]):
                    row=owner.store.get_cinematic(parts[2])
                    if not row:return self._reply(404,{'error':'cinematic job not found'})
                    return self._reply(200,{'job':{k:row.get(k) for k in ('id','filename','prompt','status','video','error','created','updated')}})
                if len(parts)==4 and parts[:2]==['v1','cinematics'] and parts[3]=='image' and JOB_ID_RE.fullmatch(parts[2]):
                    row=owner.store.get_cinematic(parts[2]);path=Path(row['image']) if row else None
                    if not path or not path.is_file():return self._reply(404,{'error':'cinematic screenshot not found'})
                    if path.stat().st_size>MAX_IMAGE:return self._reply(413,{'error':'cinematic screenshot exceeds transfer limit'})
                    return self._reply(200,path.read_bytes(),content_type='image/png')
                mid, parts = self._route()
                if parts == ['v1', 'status']:
                    jobs = owner.store.extension_snapshot(E.QUEUE_LIMIT)
                    public_jobs = [public_job(owner.store, r, owner.data_dir) for r in jobs]
                    pending = sum(1 for job in public_jobs if job.get('state') == 'pending')
                    return self._reply(200, {'connected': True, 'app': 'Vintendo COD Tracker',
                        'version': 1, 'pendingCount': pending, 'queueLimit': E.QUEUE_LIMIT,
                        'jobs': public_jobs})
                if parts == ['v1', 'jobs']:
                    rows = owner.store.extension_snapshot(E.QUEUE_LIMIT)
                    return self._reply(200, {'jobs': [public_job(owner.store, r, owner.data_dir) for r in rows]})
                if mid and len(parts) == 3:
                    row = owner.store.get(mid)
                    if not row: return self._reply(404, {'error': 'job not found'})
                    return self._reply(200, {'job': public_job(owner.store, row, owner.data_dir)})
                if mid and len(parts)==5 and parts[3]=='photos':
                    folder=owner.data_dir/'handoff'/E.disk_id(mid)
                    filename=parts[4]
                    if not re.fullmatch(r'reference-\d{2}-[A-Za-z0-9_-]+\.jpg',filename):
                        return self._reply(400,{'error':'invalid reference image name'})
                    manifest=E.read_json(folder/'photo-assignments.json',[])
                    if not any(isinstance(item,dict) and item.get('filename')==filename and item.get('status')=='ready' for item in manifest):
                        return self._reply(404,{'error':'reference image is not assigned to this match'})
                    path=folder/filename
                    if not path.is_file():return self._reply(404,{'error':'reference image is unavailable'})
                    return self._reply(200,path.read_bytes(),content_type='image/jpeg')
                return self._reply(404, {'error': 'route not found'})
            def _read_body(self, limit):
                raw_length = self.headers.get('Content-Length')
                if not raw_length or not raw_length.isdigit(): return None, 'content length required'
                length = int(raw_length)
                if length < 1 or length > limit: return None, 'request body too large or empty'
                return self.rfile.read(length), None
            def do_POST(self):
                return self._guarded(self._do_POST)
            def _do_POST(self):
                if not self._allowed(): return
                parts = [unquote(p) for p in urlsplit(self.path).path.split('/') if p]
                if len(parts)==4 and parts[:2]==['v1','cinematics'] and JOB_ID_RE.fullmatch(parts[2]):
                    cid,action=parts[2],parts[3];row=owner.store.get_cinematic(cid)
                    if not row:return self._reply(404,{'error':'cinematic job not found'})
                    if action=='claim':
                        if row['status']!='queued':return self._reply(409,{'error':'cinematic job is not queued'})
                        owner.store.update_cinematic(cid,status='running',error='')
                        return self._reply(200,{'job':owner.store.get_cinematic(cid)})
                    if action=='state':
                        body,error=self._read_body(MAX_JSON)
                        if error:return self._reply(400,{'error':error})
                        try:data=json.loads(body)
                        except (ValueError,UnicodeError):return self._reply(400,{'error':'invalid JSON'})
                        state=data.get('state') if isinstance(data,dict) else None
                        if state not in ('awaiting_generation','awaiting_download','failed'):return self._reply(400,{'error':'unsupported cinematic state'})
                        owner.store.update_cinematic(cid,status=state,error=str(data.get('error') or '')[:300])
                        return self._reply(200,{'job':owner.store.get_cinematic(cid)})
                    if action=='video':
                        body,error=self._read_body(MAX_VIDEO)
                        if error:return self._reply(400,{'error':error})
                        if not valid_mp4(body):return self._reply(415,{'error':'download is not a complete MP4 container (ftyp/moov/mdat required)'})
                        if row['status'] not in ('awaiting_download','running'):return self._reply(409,{'error':'cinematic job is not awaiting a video'})
                        folder=owner.videos()
                        target=folder/(cid+'.mp4')
                        tmp=target.with_suffix('.mp4.part');tmp.write_bytes(body);tmp.replace(target)
                        owner.store.update_cinematic(cid,status='completed',video=str(target),error='')
                        owner.on_log('Cinematic video completed: '+cid)
                        return self._reply(201,{'job':{k:owner.store.get_cinematic(cid).get(k) for k in ('id','status','video')}})
                    return self._reply(404,{'error':'route not found'})
                mid, parts = self._route()
                if not mid or len(parts) != 4: return self._reply(404, {'error': 'route not found'})
                row = owner.store.get(mid)
                if not row: return self._reply(404, {'error': 'job not found'})
                action = parts[3]
                if action in ('prepare', 'retry', 'cancel'):
                    body, error = self._read_body(MAX_JSON)
                    if error: return self._reply(400, {'error': error})
                    try: data = json.loads(body or b'{}')
                    except (ValueError, UnicodeError): return self._reply(400, {'error': 'invalid JSON'})
                    if action == 'prepare':
                        if row['status'] in ('delivered', 'solo_delivered', 'baseline', 'posting'):
                            return self._reply(409, {'error': 'match is not available for generation'})
                        try: prepared = owner.prepare(mid,open_folder=not bool(data.get('automatic')))
                        except Exception as exc: return self._reply(422, {'error': str(exc)[:240]})
                        if not prepared: return self._reply(409, {'error': 'job could not be prepared'})
                        if not data.get('automatic'):owner.on_prepare(mid, prepared['folder'])
                        return self._reply(200, {'job': public_job(owner.store, owner.store.get(mid), owner.data_dir),
                            'referencePhotos': prepared['photos'], 'referenceFolder': prepared['folder']})
                    if action == 'cancel':
                        if not owner.store.cancel_handoff(mid): return self._reply(409, {'error': 'job cannot be cancelled'})
                        return self._reply(200, {'job': public_job(owner.store, owner.store.get(mid), owner.data_dir)})
                    # Retry only resets recoverable artwork handoffs; Discord uncertain state stays guarded.
                    if row['status'] not in ('needs_review', 'failed', 'art_ready', 'ready'):
                        return self._reply(409, {'error': 'job is not retryable in its current state'})
                    owner.store.update(mid, status='needs_review', error='', gen_state='queued')
                    return self._reply(200, {'job': public_job(owner.store, owner.store.get(mid), owner.data_dir)})
                if action == 'state':
                    body, error = self._read_body(MAX_JSON)
                    if error: return self._reply(400, {'error': error})
                    try: data = json.loads(body)
                    except (ValueError, UnicodeError): return self._reply(400, {'error': 'invalid JSON'})
                    state = data.get('state') if isinstance(data, dict) else None
                    if state=='failed':
                        error=str(data.get('error') or 'Automatic Meta AI handoff failed.')[:300]
                        owner.store.update(mid,status='failed',gen_state='error',error=error)
                        return self._reply(200,{'job':public_job(owner.store,owner.store.get(mid),owner.data_dir)})
                    if state not in GEN_STATES - {'cancelled'}:
                        return self._reply(400, {'error': 'unsupported generation state'})
                    if row['status'] not in ('awaiting_artwork', 'needs_review'):
                        return self._reply(409, {'error': 'job is not awaiting generation'})
                    prior = row.get('gen_state') or 'idle'
                    if state == 'prepared' or \
                       (state == 'awaiting_generation' and prior != 'prepared') or \
                       (state == 'awaiting_download' and prior not in ('prepared', 'awaiting_generation')):
                        return self._reply(409, {'error': 'invalid generation state transition'})
                    owner.store.update(mid, gen_state=state)
                    return self._reply(200, {'job': public_job(owner.store, owner.store.get(mid), owner.data_dir)})
                if action == 'artwork':
                    if row['status'] != 'awaiting_artwork' or row.get('gen_state') != 'awaiting_download':
                        return self._reply(409, {'error': 'job is not awaiting artwork'})
                    body, error = self._read_body(MAX_IMAGE)
                    if error: return self._reply(413 if 'large' in error else 400, {'error': error})
                    kind = self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
                    if kind not in ('image/png', 'image/jpeg', 'image/webp'):
                        return self._reply(415, {'error': 'only PNG, JPEG or WebP artwork is accepted'})
                    try: target, width, height = owner.validate_and_save(mid, body, kind)
                    except ValueError as exc: return self._reply(422, {'error': str(exc)[:240]})
                    if not owner.store.accept_bridge_artwork(mid, str(target)):
                        target.unlink(missing_ok=True); return self._reply(409, {'error': 'job state changed; the artwork upload was not accepted'})
                    owner.on_artwork(mid, str(target))
                    return self._reply(201, {'job': public_job(owner.store, owner.store.get(mid), owner.data_dir),
                        'image': {'format': 'PNG', 'width': width, 'height': height, 'bytes': target.stat().st_size}})
                return self._reply(404, {'error': 'route not found'})

        self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = self.server.server_address[1]
        self.server.daemon_threads = True
        self.server.allow_reuse_address = False
        self.thread = threading.Thread(target=self.server.serve_forever, name='VintendoLocalBridge', daemon=True)
        self.thread.start()
        self.on_log(f'Chrome extension bridge listening on {self.host}:{self.server.server_address[1]}.')
        return self.server.server_address[1]

    def prepare(self, mid, *, open_folder=True):
        row = self.store.get(mid)
        if not row: return None
        match = _json(row.get('payload'), {})
        players = _json(row.get('players'), [])
        if not any(isinstance(p,dict) and p.get('verified') for p in players):
            solo_name=(match.get('playerName') or match.get('player') or match.get('username') or self.solo_player_name)
            if match.get('integration_source')=='CODMunity' and solo_name:
                personal=E.codmunity_personal_row(match,solo_name)
                if personal is not None:players=[personal]
                else:
                    stats=match.get('stats') if isinstance(match.get('stats'),dict) else {}
                    stats={key:value for key,value in stats.items() if isinstance(value,(int,float)) and not isinstance(value,bool)}
                    players=[{'name':str(solo_name).strip(),'stats':stats,'verified':True,'source':'CODMunity'}]
            else:raise ValueError('No identified match participants with verified statistics are available.')
        players=[p for p in players if isinstance(p,dict) and p.get('verified') and str(p.get('name','')).strip()]
        if not 1<=len(players)<=4:raise ValueError('Automatic poster jobs require one to four identified players.')
        match=E.with_scoreboard_context(match,row.get('ocr',''))
        context=E.extract_match_context(match,row.get('ocr',''))
        if context['result'] not in ('Victory','Defeat'):
            raise ValueError('Match outcome is unknown; automation will not guess win or loss.')
        self.store.update(mid,payload=match,players=json.dumps(players,ensure_ascii=False))
        folder = self.data_dir / 'handoff' / E.disk_id(mid)
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob('reference-*.jpg'): old.unlink(missing_ok=True)
        mappings=E.match_reference_photos(players,self.store.roster())
        photos=[]
        # The poster/Meta handoff supports at most four reference images.
        # Keep the full assignment list below so any additional eligible
        # players are explicitly reported instead of silently disappearing.
        max_headshots=4
        from PIL import Image
        ready_count=0
        for order,item in enumerate(mappings,1):
            if item['status']!='ready':continue
            if ready_count>=max_headshots:
                item.update(status='limit_reached',reason='Only four player headshots can be staged for one poster job.')
                continue
            source=Path(item['photoPath'])
            if source.stat().st_size>20*1024*1024:
                item.update(status='invalid_photo',reason='Reference photo exceeds the safe 20 MB staging limit.');continue
            target=folder/('reference-'+str(order).zfill(2)+'-'+E.disk_id(item['rosterName'])+'.jpg')
            with Image.open(source) as image:
                converted=image.convert('RGB');converted.thumbnail((1400,1400));converted.save(target,'JPEG',quality=88,optimize=True)
                while target.stat().st_size>10*1024*1024:
                    converted.thumbnail((max(80,int(converted.width*.75)),max(80,int(converted.height*.75))))
                    converted.save(target,'JPEG',quality=82,optimize=True)
            item['filename']=target.name
            photos.append({'username':item['username'],'filename':target.name,'bytes':target.stat().st_size,'consented':True})
            ready_count+=1
        assignments=[]
        for item in mappings:
            assignments.append({'row':item['row'],'username':item['username'],'status':item['status'],
                'matchedRosterName':item.get('rosterName',''),'posterName':item.get('posterName',item.get('rosterName') or item.get('username','')),'reason':item.get('reason',''),
                'candidates':item.get('candidates',[]),'filename':item.get('filename',''),
                'consented':bool(item.get('consented'))})
        unready=[item for item in assignments if item['status']!='ready' or not item.get('filename')]
        if unready:
            details='; '.join(f"{item['username']}: {item['reason'] or item['status']}" for item in unready)
            try:self.on_log('Poster proceeding without headshot(s): '+details[:240])
            except Exception:pass
        prompt=E.automatic_match_prompt(players,context['game_mode'],context['result'],assignments,snapshot=self.store.meta('codmunity_stats'))
        E.atomic_json(folder / 'photo-assignments.json', assignments)
        (folder / 'prompt.txt').write_text(prompt, encoding='utf-8')
        import re
        import_prompt=re.sub(r'\s+',' ',prompt).strip()
        (folder/'Meta-AI-Automation-Import.txt').write_text(import_prompt,encoding='utf-8')
        mapping_rows=['scoreboard_row,username,photo_file,status,roster_match']
        for item in assignments:
            fields=[str(item.get('row',0)+1),item['username'],item.get('filename',''),item['status'],item.get('matchedRosterName','')]
            mapping_rows.append(','.join('"'+value.replace('"','""')+'"' for value in fields))
        (folder/'reference-photo-mapping.csv').write_text('\n'.join(mapping_rows)+'\n',encoding='utf-8')
        instructions=['VINTENDO COD TRACKER — MATCH-BOUND META AI INPUTS',f'Match/job ID: {mid}',
            'When the paired Vintendo COD Automation extension is connected and automatic handoff is enabled, it submits the prompt and reference images for this match.',
            'Meta-AI-Automation-Import.txt remains available as a manual recovery copy of the prompt.',
            'Only the numbered reference JPEGs listed in reference-photo-mapping.csv are staged for this request.',
            'Each filename is mapped to one verified scoreboard username. Do not relabel or substitute photos.',
            f'Consented reference images staged: {len(photos)} (maximum four per poster job).',
            'The downloaded artwork is returned to this exact match ID by the extension and is validated before the tracker accepts it.']
        if unready:
            instructions.append('Players without a staged headshot are named in the prompt with NO REFERENCE IMAGE; portray them generically without guessing likeness.')
        (folder/'Vintendo-Squad-Import-Instructions.txt').write_text('\n'.join(instructions)+'\n',encoding='utf-8')
        self.store.update(mid, prompt=prompt, status='awaiting_artwork', error='')
        self.store.record_handoff(mid, prompt, len(photos))
        self.store.update(mid, gen_state='prepared')
        if open_folder:self.on_prepare(mid,str(folder))
        return {'folder': str(folder), 'photos': photos, 'prompt': prompt}

    def validate_and_save(self, mid, body, content_type):
        from PIL import Image, UnidentifiedImageError
        import io
        try:
            with Image.open(io.BytesIO(body)) as image:
                image.verify()
            with Image.open(io.BytesIO(body)) as image:
                if image.format not in ('PNG', 'JPEG', 'WEBP'):
                    raise ValueError('image content does not match a supported format')
                width, height = image.size
                if width < 320 or height < 180 or width > 8192 or height > 8192 or width * height > 50_000_000:
                    raise ValueError('image dimensions are outside the accepted range')
                expected = {'image/png': 'PNG', 'image/jpeg': 'JPEG', 'image/webp': 'WEBP'}[content_type]
                if image.format != expected: raise ValueError('image bytes do not match the declared format')
                clean = image.convert('RGB')
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValueError('download is not a valid supported image') from exc
        folder = self.data_dir / 'artwork'
        folder.mkdir(parents=True, exist_ok=True)
        name = f'{E.disk_id(mid)}-{secrets.token_hex(6)}.png'
        target = folder / name
        temp = target.with_suffix('.tmp')
        clean.save(temp, format='PNG', optimize=True)
        os.replace(temp, target)
        return target, width, height

    def close(self):
        if self.server:
            self.server.shutdown(); self.server.server_close(); self.server = None
        if self.thread and self.thread.is_alive(): self.thread.join(timeout=2)
        self.thread = None
