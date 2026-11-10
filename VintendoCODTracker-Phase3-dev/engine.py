"""Persistence, verified records, player matching, jobs and deterministic rendering."""
from __future__ import annotations
import hashlib, json, os, re, sqlite3, sys, time, shutil, uuid, unicodedata
from pathlib import Path
from difflib import SequenceMatcher
from datetime import datetime, timezone
from contextlib import contextmanager
from security import protect_text, unprotect_text

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
DATA = Path(os.environ.get('VINTENDO_DATA_DIR', str(Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Vintendo COD Tracker')))
MATCH_POSTER_PROMPT_MARKER = 'VERIFIED MATCH POSTER REQUEST v3'
DEFAULTS = dict(mode='Solo Ops', player='vintendo', stats_dir='', poll_seconds=5, warzone_enabled=False,
                protected_webhook='', provider='Meta AI manual handoff',
                auto_detect=False, auto_post=True, photos_consent=False, allow_both=False, roi=None,
                monitor=0, profile_url='', career_summary_every_matches=10,
                browser_navigation_timeout=60, browser_download_timeout=1800, browser_retries=1,
                scoreboard_hotkey_vk=0x77, scoreboard_hotkey_mod=0x0002|0x0004,
                screenshot_hotkey_vk=0x77, screenshot_hotkey_mod=0x0002,
                screenshots_dir='', videos_dir='',
                webhook_channel='', cinematic_webhook_channel='')

def read_json(path, default=None):
    try: return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError): return default

def atomic_json(path, value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp'); tmp.write_text(json.dumps(value,indent=2),encoding='utf-8'); os.replace(tmp,path)
def used_screenshots(folder,jobs):
    """Screenshots in folder already used as a cinematic source, matched by job filename."""
    try:known={str(job.get('filename') or '') for job in jobs or []}
    except Exception:return []
    used=[]
    try:files=sorted(Path(folder).glob('COD_*.png'))
    except OSError:return []
    for path in files:
        try:
            if path.name in known and path.is_file():used.append(path)
        except OSError:continue
    return used

class Settings:
    def __init__(self, directory=DATA):
        self.directory=Path(directory); self.directory.mkdir(parents=True,exist_ok=True)
        self.path=self.directory/'settings.json'; stored=read_json(self.path,{}) or {}; self.values={**DEFAULTS,**stored}
        if int(stored.get('squad_auto_post_version',0))<1:
            # The requested Squad Night flow posts automatically after the user
            # downloads the generated image. Preserve an explicit future opt-out.
            self.values['auto_post']=True;self.values['squad_auto_post_version']=1;self.save()
    def save(self): atomic_json(self.path,self.values)
    def webhook(self): return unprotect_text(self.values.get('protected_webhook',''))
    def set_webhook(self,value): self.values['protected_webhook']=protect_text(value.strip())
    def cinematic_webhook(self): return unprotect_text(self.values.get('protected_cinematic_webhook',''))
    def set_cinematic_webhook(self,value): self.values['protected_cinematic_webhook']=protect_text(value.strip())
    def extension_token(self):
        value=self.values.get('extension_token_protected','')
        if not value:
            import secrets
            value=secrets.token_urlsafe(36);self.values['extension_token_protected']=protect_text(value);self.save()
        else:value=unprotect_text(value)
        return value
    def rotate_extension_token(self):
        import secrets
        value=secrets.token_urlsafe(36);self.values['extension_token_protected']=protect_text(value);self.save();return value

def legacy():
    directory=ROOT/'core'/'legacy'
    if str(directory) not in sys.path: sys.path.insert(0,str(directory))
    import vintendo_tracker as mod
    mod.ROOT=directory; mod.CONFIG=DATA/'legacy-runtime-config.json'; mod.STATE=DATA/'posted_matches.json'
    mod.OUT=DATA/'reports'; mod.ACHIEVEMENT_HISTORY=DATA/'achievement_history.json'
    mod.ACHIEVEMENT_CONFIG=DATA/'achievement_config.json'
    mod.ACHIEVEMENT_IMAGES=directory/'achievements'/'images'
    mod.WARZONE_VICTORY_TEMPLATE=directory/'templates'/'warzone_victory.jpg'
    mod.WARZONE_DEFEAT_TEMPLATE=directory/'templates'/'warzone_defeat.jpg'
    import graphics
    graphics.ROOT=directory; graphics.REGISTRY=DATA/'graphics.json'
    if not mod.ACHIEVEMENT_CONFIG.exists():
        DATA.mkdir(parents=True,exist_ok=True); shutil.copy2(directory/'achievement_config.json',mod.ACHIEVEMENT_CONFIG)
    return mod

def run_solo_worker():
    """Run the preserved tracker loop in a managed worker, without plaintext config."""
    import logging
    import ctypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_wchar_p];kernel.CreateMutexW.restype=ctypes.c_void_p
    mutex=kernel.CreateMutexW(None,True,'Local\\VintendoSolo-'+disk_id(str(DATA)))
    if not mutex or ctypes.get_last_error()==183:return
    DATA.mkdir(parents=True,exist_ok=True)
    stream=open(DATA/'solo-worker.log','a',encoding='utf-8',buffering=1)
    class Redactor:
        def write(self,value):
            value=re.sub(r'https://(?:discord(?:app)?\.com)/api/webhooks/[^\s\"\']+','[webhook]',value)
            stream.write(value)
        def flush(self): stream.flush()
    sys.stdout=sys.stderr=Redactor()
    settings=Settings(); m=legacy(); config=dict(settings.values)
    config['webhook_url']=settings.webhook(); config['share_username']=config['player']
    original=m.read_json
    def read(path,default): return config if Path(path)==m.CONFIG else original(path,default)
    m.read_json=read
    store=Store();original_post=m.post_discord
    def coordinated_post(url,image,match):
        if not match.get('playedAt'):return original_post(url,image,match)
        mid,_=store.add(match,'observed');job=store.get(mid)
        if job['status'] in ('delivered','solo_delivered'):return
        if not store.reserve(mid,kind='solo'):raise RuntimeError('Match delivery is awaiting manual review; duplicate suppressed')
        store.update(mid,image=str(image))
        try:
            original_post(url,image,match)
            store.update(mid,status='solo_delivered',error='')
        except Exception as exc:
            import urllib.error
            safe_failure=isinstance(exc,urllib.error.HTTPError) and 400<=exc.code<500
            store.update(mid,status='failed' if safe_failure else 'uncertain',error='Solo delivery failed' if safe_failure else 'Solo delivery uncertain; check Discord before retry')
            raise RuntimeError('Solo delivery failed; see Match History') from None
    m.post_discord=coordinated_post
    achievements=read_json(m.ACHIEVEMENT_CONFIG,{})
    achievements['warzone_enabled']=bool(config.get('warzone_enabled')); atomic_json(m.ACHIEVEMENT_CONFIG,achievements)
    sys.argv=[sys.argv[0],'--watch']
    class FileStop:
        def is_set(self):
            if (DATA/'solo-stop.flag').exists():return True
            if '--parent-pid' in sys.argv:
                import psutil
                try:return not psutil.pid_exists(int(sys.argv[sys.argv.index('--parent-pid')+1]))
                except (ValueError,IndexError):return True
            return False
        def wait(self,seconds):
            end=time.monotonic()+seconds
            while time.monotonic()<end and not self.is_set():time.sleep(min(.2,max(0,end-time.monotonic())))
    # main() parses its own preserved CLI; capture the parent before replacing argv.
    parent_pid=os.environ.get('VINTENDO_PARENT_PID')
    original_is_set=FileStop.is_set
    def stopped(self):
        if original_is_set(self):return True
        if parent_pid:
            import psutil
            return not psutil.pid_exists(int(parent_pid))
        return False
    FileStop.is_set=stopped
    try: m.main(FileStop())
    except Exception as exc: print('Tracker stopped:',type(exc).__name__)

def key_for(match):
    identity={k:match.get(k) for k in ('game','mode','modeKey','playedAt','result','stats','placement') if k in match}
    digest=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    return str(match.get('matchId') or 'record-'+digest[:24])

def disk_id(mid): return hashlib.sha256(str(mid).encode()).hexdigest()[:32]

def identity_aliases(match):
    aliases=[]
    if match.get('playedAt') or not match.get('matchId'):aliases.append('content:'+key_for({k:v for k,v in match.items() if k!='matchId'}))
    if match.get('matchId'):aliases.append('source:'+str(match['matchId']))
    mode=str(match.get('mode') or match.get('modeKey') or '').lower().replace(' ','_')
    game=str(match.get('game') or ('warzone' if mode in ('resurgence','battle_royale') or mode.startswith('warzone_') else 'mw4')).lower()
    mode=mode.removeprefix('warzone_');played=match.get('playedAt');result=match.get('result')
    if played and mode and result:
        try:played=datetime.fromisoformat(str(played).replace('Z','+00:00')).timestamp()
        except ValueError:pass
        aliases.append('natural:'+disk_id(json.dumps([game,mode,played,str(result).lower()])))
    return aliases

def normalize_name(value):
    value=unicodedata.normalize('NFKD',str(value).casefold())
    value=''.join(c for c in value if not unicodedata.combining(c)).strip()
    for _ in range(3):
        updated=re.sub(r'^\s*[\[({<][^\])}>]{1,18}[\])}>]\s*','',value)
        if updated==value:break
        value=updated
    value=re.sub(r'^\s*[a-z0-9]{1,10}[|:]\s*','',value)
    return re.sub(r'[^a-z0-9]','',value)

def match_player_detail(name, roster):
    """Conservative exact/fuzzy roster match with explicit ambiguity reporting."""
    query=normalize_name(name)
    if not query:return {'player':None,'score':0.0,'status':'unmatched','candidates':[]}
    candidates=[]
    for player in roster or []:
        if not player.get('enabled',True):continue
        aliases=[player.get('name','')]+list(player.get('aliases',[]))
        normalized=[normalize_name(a) for a in aliases]
        score=max((SequenceMatcher(None,query,a).ratio() for a in normalized if a),default=0.0)
        candidates.append((score,player,query in normalized))
    candidates.sort(key=lambda item:(item[0],item[2]),reverse=True)
    exact=[item for item in candidates if item[2]]
    if len(exact)==1:return {'player':exact[0][1],'score':1.0,'status':'matched','candidates':[x[1].get('name','') for x in exact]}
    if len(exact)>1:return {'player':None,'score':1.0,'status':'ambiguous','candidates':[x[1].get('name','') for x in exact]}
    if not candidates:return {'player':None,'score':0.0,'status':'unmatched','candidates':[]}
    top=candidates[0]
    tied=[x for x in candidates if x[0]>=0.88 and top[0]-x[0]<0.12]
    if len(tied)>1:return {'player':None,'score':top[0],'status':'ambiguous','candidates':[x[1].get('name','') for x in tied]}
    if top[0]<0.88:return {'player':None,'score':top[0],'status':'unmatched','candidates':[top[1].get('name','')]}
    return {'player':top[1],'score':top[0],'status':'matched','candidates':[top[1].get('name','')]}

def match_player(name, roster):
    detail=match_player_detail(name,roster)
    return detail['player'],detail['score']

STAT_ALIASES={
    'kill':'kills','kills':'kills','elimination':'eliminations','eliminations':'eliminations','elim':'eliminations','elims':'eliminations',
    'death':'deaths','deaths':'deaths','damage':'damage','assist':'assists','assists':'assists','score':'score',
    'placement':'placement','place':'placement','position':'placement','redeploy':'redeploys','redeploys':'redeploys',
    'revive':'revives','revives':'revives','headshot':'headshots','headshots':'headshots','objective':'objective','objectives':'objectives',
    'captures':'captures','capture':'captures','downs':'downs','down':'downs','wins':'wins','win':'wins','streak':'streak',
    'kd':'kd','kdratio':'kd','objective':'objective','objectives':'objectives'
}

def _header_stat(token):
    cleaned=re.sub(r'[^a-z0-9]+','',str(token).casefold())
    if cleaned in ('player','players','username','name','operator','rank','team','squad'):return None
    if cleaned in STAT_ALIASES:return STAT_ALIASES[cleaned]
    if not re.search(r'[a-z]',cleaned):return None
    return re.sub(r'[^a-z0-9]+','_',str(token).casefold()).strip('_')

def parse_scoreboard(text):
    """Candidate rows only. User review is mandatory before they are verified."""
    lines=[line.strip() for line in str(text or '').splitlines() if line.strip()]
    headings=[]; rows=[]
    for line in lines:
        cells=re.split(r'\s{2,}|\t|\|',line)
        header_stats=[_header_stat(token) for token in cells]
        if len(cells)>=2 and header_stats[0] is None and sum(field is not None for field in header_stats[1:])>=1:
            headings=[field or re.sub(r'[^a-z0-9]+','_',cells[index].casefold()).strip('_') for index,field in enumerate(header_stats[1:],1)]
            continue
        if len(cells)<2: continue
        name=cells[0].strip()
        if not re.search(r'[A-Za-z]',name): continue
        nums=[]
        for cell in cells[1:]:
            cleaned=cell.strip().replace(',','')
            if re.fullmatch(r'\d+',cleaned):nums.append(int(cleaned))
            elif re.fullmatch(r'\d+\.\d+',cleaned):nums.append(float(cleaned))
            else:nums.append(None)
        if any(n is not None for n in nums):
            stats=dict(zip(headings,nums)) if headings and len(headings)==len(nums) else {}
            rows.append(dict(name=name,stats=stats,verified=False,source='ocr',confidence='review_required'))
    return rows[:16]

def extract_match_context(match=None, ocr_text=''):
    """Use explicit CODMunity fields first, then only facts read from scoreboard OCR."""
    match=match if isinstance(match,dict) else {}
    stored=match.get('poster_context') if isinstance(match.get('poster_context'),dict) else {}
    def first_value(source,keys):
        for key in keys:
            value=source.get(key)
            if value is not None and str(value).strip():return str(value).strip()
        return ''
    game=first_value(match,('game','gameName','gameTitle','title')) or first_value(stored,('game',))
    mode=first_value(match,('modeKey','mode','modeName','mode_name','modeLabel','modeTitle','gameMode','playlist','playlistName')) or first_value(stored,('mode',))
    if mode.casefold() in ('match','unknown','squad scoreboard','scoreboard capture','capture','n/a','none'):mode=''
    if game.casefold() in ('match','unknown','squad scoreboard','scoreboard capture','capture','n/a','none'):game=''
    mode_key=re.sub(r'[^a-z0-9]+','_',mode.casefold()).strip('_')
    game_key=re.sub(r'[^a-z0-9]+','_',game.casefold()).strip('_')
    labels={'resurgence':'Resurgence','battle_royale':'Battle Royale','br':'Battle Royale','plunder':'Plunder',
        'kill_confirmed':'Kill Confirmed','killconfirmed':'Kill Confirmed','team_deathmatch':'Team Deathmatch','tdm':'Team Deathmatch',
        'free_for_all':'Free-for-All','hardpoint':'Hardpoint','domination':'Domination','search_destroy':'Search and Destroy',
        'control':'Control','ranked_play':'Ranked Play','multiplayer':'Multiplayer','warzone':'Warzone'}
    mode_label=labels.get(mode_key,mode)
    game_label=labels.get(game_key,game)
    if not game_label and mode_key in ('kill_confirmed','killconfirmed','team_deathmatch','tdm','free_for_all','hardpoint','domination','search_destroy','ranked_play'):
        game_label='Multiplayer'
    patterns=[('resurgence','Resurgence'),('battle royale','Battle Royale'),('plunder','Plunder'),
        ('kill confirmed','Kill Confirmed'),('team deathmatch','Team Deathmatch'),('free for all','Free-for-All'),
        ('hardpoint','Hardpoint'),('domination','Domination'),('search and destroy','Search and Destroy'),('ranked play','Ranked Play')]
    ocr_mode=next((label for needle,label in patterns if re.search(r'\b'+re.escape(needle)+r'\b',str(ocr_text),re.I)), '')
    if not mode_label:mode_label=ocr_mode
    if not game_label and re.search(r'\bmultiplayer\b',str(ocr_text),re.I):game_label='Multiplayer'
    if not game_label and mode_label in ('Kill Confirmed','Team Deathmatch','Free-for-All','Hardpoint','Domination','Search and Destroy','Ranked Play'):game_label='Multiplayer'
    if not game_label and re.search(r'\bwarzone\b',str(ocr_text),re.I):game_label='Warzone'
    if game_label and mode_label and normalize_name(game_label)!=normalize_name(mode_label):
        mode_label=f'{game_label} · {mode_label}'
    elif not mode_label:mode_label=game_label

    placeholder={'','match','unknown','none','null','n/a','not available'}
    raw_result=first_value(match,('result','matchResult','resultName','outcome'))
    if raw_result.casefold() in placeholder:raw_result=''
    if not raw_result:raw_result=first_value(stored,('result',))
    if not raw_result:
        if re.search(r'\b(victory|victorious|you win|winner)\b',str(ocr_text),re.I):raw_result='Victory'
        elif re.search(r'\b(defeat|defeated|you lose|you lost)\b',str(ocr_text),re.I):raw_result='Defeat'
    result_labels={'win':'Victory','won':'Victory','victory':'Victory','loss':'Defeat','lost':'Defeat','defeat':'Defeat'}
    result=result_labels.get(raw_result.casefold(),raw_result)
    placement=first_value(match,('placement','place','position')) or first_value(stored,('placement',))
    if not placement:
        stats=match.get('stats') if isinstance(match.get('stats'),dict) else {}
        if stats.get('placement') is not None:placement=str(stats['placement'])
    if not placement:
        found=re.search(r'\b(?:placement|place|position)\s*[:#-]?\s*(\d{1,2})(?:st|nd|rd|th)?\b|\b(\d{1,2})(?:st|nd|rd|th)\s+(?:place|position)\b',str(ocr_text),re.I)
        if found:placement=next((part for part in found.groups() if part),'')
    placement=str(placement).strip()
    numeric_place=re.fullmatch(r'(\d{1,3})(?:st|nd|rd|th)?(?:\s+place)?',placement,re.I)
    number=None
    if numeric_place:
        number=int(numeric_place.group(1));suffix='th' if 10<=number%100<=20 else {1:'st',2:'nd',3:'rd'}.get(number%10,'th')
        placement=f'{number}{suffix} place'
    if not raw_result and number is not None:
        warzone_mode=mode_key in ('resurgence','battle_royale','br','plunder','warzone') or game_key=='warzone' or any(needle in mode_label.casefold() for needle in ('resurgence','battle royale','warzone','plunder'))
        if warzone_mode:raw_result='Victory' if number==1 else 'Defeat'
    result=result_labels.get(raw_result.casefold(),raw_result)
    unavailable='Not shown in CODMunity data or the captured scoreboard.'
    return {'game_mode':mode_label or unavailable,'result':result or unavailable,'placement':placement or unavailable}

def placement_from_players(players):
    """Most common verified-row placement, or None.

    A Warzone squad shares one team placement, so the OCR table's
    placement column can backfill a match payload that has none. Ties
    break toward the best (smallest) placement.
    """
    from collections import Counter
    votes = Counter()
    for row in players or []:
        if not isinstance(row, dict):
            continue
        stats = row.get('stats') if isinstance(row.get('stats'), dict) else {}
        value = stats.get('placement')
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and value >= 1:
            votes[int(value)] += 1
    if not votes:
        return None
    return max(sorted(votes.items()), key=lambda item: (item[1], -item[0]))[0]


def with_scoreboard_context(match, ocr_text=''):
    enriched=dict(match or {})
    enriched['poster_context']=extract_match_context(enriched,ocr_text)
    context=enriched['poster_context']
    current=str(enriched.get('modeKey',enriched.get('mode','')) or '').strip()
    if not current or current.casefold() in ('match','unknown','squad scoreboard','scoreboard capture','capture'):
        if context['game_mode']!='Not shown in CODMunity data or the captured scoreboard.':enriched['modeKey']=context['game_mode']
    current_result=str(enriched.get('result','') or '').strip()
    if not current_result or current_result.casefold() in ('match','unknown','none','n/a'):
        if context['result']!='Not shown in CODMunity data or the captured scoreboard.':enriched['result']=context['result']
    if context['placement']!='Not shown in CODMunity data or the captured scoreboard.':enriched['placement']=context['placement']
    return enriched

RANK_FIELDS=('kills','eliminations','score','damage','assists','headshots','downs','revives','captures','objective','deaths')
def rank_verified_players(players, match=None):
    """Rank only comparable verified values; leave rows with no comparable value unranked."""
    verified=[]
    for order,player in enumerate(players or []):
        if not isinstance(player,dict) or not player.get('verified'):continue
        name=str(player.get('name','')).strip()
        if not name:continue
        raw=player.get('stats') if isinstance(player.get('stats'),dict) else {}
        stats={str(key):value for key,value in raw.items() if isinstance(key,str) and isinstance(value,(int,float)) and not isinstance(value,bool) and value>=0}
        verified.append({'name':name,'stats':stats,'order':order,'rank':None,'mvp':False,'ranking_basis':[]})
    context=extract_match_context(match or {})
    mode=context['game_mode'].casefold()
    preferred=('score','kills','eliminations','assists','damage','headshots','downs','revives','captures','objective','deaths') if 'multiplayer' in mode or any(x in mode for x in ('hardpoint','domination','search and destroy','kill confirmed')) else ('kills','eliminations','damage','score','assists','headshots','downs','revives','captures','objective','deaths')
    coverage={key:sum(key in row['stats'] for row in verified) for key in RANK_FIELDS}
    candidates=[key for key in preferred if coverage.get(key,0)>=2 or len(verified)==1 and coverage.get(key,0)==1]
    if not candidates:return verified
    primary=candidates[0]
    comparable=[row for row in verified if primary in row['stats']]
    # Missing secondary values do not count as zero or as a worse result.
    basis=[primary]+[key for key in preferred if key!=primary and len(comparable)>1 and all(key in row['stats'] for row in comparable)]
    def value(row,key):
        number=row['stats'].get(key)
        if number is None:return (1,0)
        return (0,-number if key not in ('deaths',) else number)
    comparable.sort(key=lambda row:tuple(value(row,key) for key in basis)+(row['order'],))
    previous=None;rank=0
    for index,row in enumerate(comparable,1):
        score=tuple(value(row,key) for key in basis)
        if score!=previous:rank=index
        row['rank']=rank;row['ranking_basis']=basis[:];previous=score
    if comparable:
        top_score=tuple(value(comparable[0],key) for key in basis)
        leaders=[row for row in comparable if tuple(value(row,key) for key in basis)==top_score]
        for row in leaders:row['mvp']=True
    unranked=[row for row in verified if primary not in row['stats']]
    return comparable+unranked

def match_reference_photos(players, roster):
    """Return a row-aligned photo decision for every verified player, never guessing ambiguity."""
    results=[]
    verified_names=[normalize_name(p.get('name','')) for p in (players or []) if isinstance(p,dict) and p.get('verified')]
    for index,player in enumerate(players or []):
        if not isinstance(player,dict) or not player.get('verified'):continue
        username=str(player.get('name','')).strip()
        if verified_names.count(normalize_name(username))>1:
            results.append({'row':index,'username':username,'status':'ambiguous','candidates':[username],
                'rosterName':'','posterName':username,'consented':False,'photoPath':'','reason':'This username appears more than once on the verified scoreboard.'})
            continue
        detail=match_player_detail(username,roster)
        entry={'row':index,'username':username,'status':detail['status'],'candidates':detail['candidates'],
               'rosterName':'','posterName':username,'consented':False,'photoPath':''}
        matched=detail['player']
        if detail['status']=='ambiguous':entry['reason']='Multiple roster entries are similarly plausible.'
        elif not matched:entry['reason']='No confident roster username match.'
        else:
            entry['rosterName']=str(matched.get('name',''));entry['posterName']=str(matched.get('display_name') or matched.get('name') or username).strip();entry['consented']=bool(matched.get('consent_meta'))
            photo=Path(str(matched.get('photo') or ''))
            if not photo.is_file():entry.update(status='missing_photo',reason='Matched roster player has no available photo.')
            elif not entry['consented']:entry.update(status='not_consented',reason='Matched player has not consented to Meta AI photo use.')
            else:
                try:
                    from PIL import Image
                    with Image.open(photo) as image:
                        image.verify()
                    with Image.open(photo) as image:
                        if image.width<80 or image.height<80 or image.width>8192 or image.height>8192:
                            raise ValueError('photo dimensions are unsuitable')
                except Exception:
                    entry.update(status='invalid_photo',reason='Matched consented reference photo could not be validated.')
                else:entry.update(status='ready',reason='Consented photo matched to this username.',photoPath=str(photo))
        results.append(entry)
    return results

QUEUE_LIMIT = 10

class Store:
    def __init__(self,path=DATA/'history.sqlite3'):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS matches(id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL, created REAL, updated REAL, capture TEXT DEFAULT '', ocr TEXT DEFAULT '', players TEXT DEFAULT '[]', theme TEXT DEFAULT '', prompt TEXT DEFAULT '', image TEXT DEFAULT '', attempts INTEGER DEFAULT 0, error TEXT DEFAULT '', message_id TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS roster(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY,status TEXT,created REAL,message_id TEXT,error TEXT);''')
            c.execute('CREATE TABLE IF NOT EXISTS match_aliases(alias TEXT PRIMARY KEY,match_id TEXT NOT NULL)')
            c.execute("CREATE TABLE IF NOT EXISTS cinematic_jobs(id TEXT PRIMARY KEY,filename TEXT NOT NULL,image TEXT NOT NULL,prompt TEXT NOT NULL,status TEXT NOT NULL,video TEXT DEFAULT '',error TEXT DEFAULT '',created REAL,updated REAL)")
            columns={row[1] for row in c.execute('PRAGMA table_info(matches)')}
            for name,ddl in (('artwork',"TEXT DEFAULT ''"),('poster',"TEXT DEFAULT ''"),
                             ('gen_state',"TEXT DEFAULT 'idle'"),('gen_attempts','INTEGER DEFAULT 0')):
                if name not in columns:c.execute(f'ALTER TABLE matches ADD COLUMN {name} {ddl}')
            cine_columns={row[1] for row in c.execute('PRAGMA table_info(cinematic_jobs)')}
            if 'talent_ids' not in cine_columns:c.execute("ALTER TABLE cinematic_jobs ADD COLUMN talent_ids TEXT DEFAULT ''")
            c.execute("INSERT OR IGNORE INTO deliveries SELECT id||'|squad','delivered',updated,message_id,'' FROM matches WHERE status='delivered'")
            c.execute("INSERT OR IGNORE INTO deliveries SELECT id||'|solo','delivered',updated,message_id,'' FROM matches WHERE status='solo_delivered'")
            c.execute("UPDATE matches SET status='needs_review',error='Interrupted job; review before retry' WHERE status='generating'")
            c.execute("UPDATE matches SET status='uncertain',error='Delivery interrupted; check Discord before retry' WHERE status='posting'")
    @contextmanager
    def connect(self):
        c=sqlite3.connect(self.path,timeout=10);c.row_factory=sqlite3.Row
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback();raise
        finally:c.close()
    def create_cinematic(self,job_id,filename,image_path,prompt,talent_ids=None):
        with self.connect() as c:
            cur=c.execute("INSERT OR IGNORE INTO cinematic_jobs(id,filename,image,prompt,talent_ids,status,created,updated) VALUES(?,?,?,?,?,?,?,?)",(job_id,filename,str(image_path),prompt,json.dumps(list(talent_ids or [])),'queued',time.time(),time.time()))
            return cur.rowcount==1
    def get_cinematic(self,job_id):
        with self.connect() as c:
            row=c.execute('SELECT * FROM cinematic_jobs WHERE id=?',(job_id,)).fetchone()
            return dict(row) if row else None
    def pending_cinematics(self,limit=5):
        with self.connect() as c:return [dict(r) for r in c.execute("SELECT * FROM cinematic_jobs WHERE status='queued' ORDER BY created LIMIT ?",(max(1,min(int(limit),20)),))]
    def update_cinematic(self,job_id,**values):
        allowed={'status','video','error','talent_ids'}
        if not values or not set(values)<=allowed:raise ValueError('Invalid cinematic job update')
        values['updated']=time.time()
        with self.connect() as c:
            c.execute('UPDATE cinematic_jobs SET '+','.join(k+'=?' for k in values)+' WHERE id=?',list(values.values())+[job_id])
            return c.execute('SELECT changes()').fetchone()[0]==1
    def recent_cinematics(self,limit=30):
        with self.connect() as c:return [dict(r) for r in c.execute('SELECT * FROM cinematic_jobs ORDER BY created DESC LIMIT ?',(max(1,min(int(limit),100)),))]
    def add(self,match,status='needs_review'):
        mid=key_for(match)
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            aliases=identity_aliases(match)
            existing=c.execute('SELECT match_id FROM match_aliases WHERE alias IN ('+','.join('?' for _ in aliases)+') LIMIT 1',aliases).fetchone()
            if existing:mid=existing[0]
            result=c.execute('INSERT OR IGNORE INTO matches(id,payload,status,created,updated) VALUES(?,?,?,?,?)',(mid,json.dumps(match),status,time.time(),time.time()))
            if result.rowcount and status in ('delivered','solo_delivered'):
                c.execute('INSERT OR IGNORE INTO deliveries VALUES(?,?,?,?,?)',(mid+('|solo' if status=='solo_delivered' else '|squad'),'delivered',time.time(),'',''))
            for alias in aliases:c.execute('INSERT OR IGNORE INTO match_aliases VALUES(?,?)',(alias,mid))
            return mid,result.rowcount==1
    def update(self,mid,**values):
        allowed={'status','capture','ocr','players','theme','prompt','image','attempts','error','message_id','payload',
                 'artwork','poster','gen_state','gen_attempts'}
        if not values or not set(values)<=allowed: raise ValueError('Invalid update')
        values['updated']=time.time()
        with self.connect() as c:
            if values.get('status') in ('delivered','solo_delivered'):
                kind='solo' if values['status']=='solo_delivered' else 'squad'
                c.execute('INSERT OR REPLACE INTO deliveries VALUES(?,?,?,?,?)',(mid+'|'+kind,'delivered',time.time(),values.get('message_id',''),''))
            values={k:(json.dumps(v,ensure_ascii=False) if k=='payload' and not isinstance(v,str) else v) for k,v in values.items()}
            c.execute('UPDATE matches SET '+','.join(k+'=?' for k in values)+' WHERE id=?',list(values.values())+[mid])
    def get(self,mid):
        with self.connect() as c:
            row=c.execute('SELECT * FROM matches WHERE id=?',(mid,)).fetchone()
            return dict(row) if row else None
    def history(self):
        with self.connect() as c:return [dict(r) for r in c.execute('SELECT * FROM matches ORDER BY created DESC LIMIT 1000')]
    def record_handoff(self,mid,prompt,photo_count):
        """Stage one generation job: prompt, photo count and browser-side state.

        Manual website steps still follow; this only records what was staged
        so downloads, overlays and deliveries stay bound to this job id."""
        with self.connect() as c:
            row=c.execute('SELECT gen_attempts FROM matches WHERE id=?',(mid,)).fetchone()
            if not row:return False
            attempts=int(row[0] or 0)+1
            c.execute("UPDATE matches SET prompt=?,gen_state='staged',gen_attempts=?,updated=? WHERE id=?",
                      (str(prompt),attempts,time.time(),mid))
            return True
    def note_artwork(self,mid,artwork_path):
        """Bind one validated download to its job; image stays synced for older readers."""
        with self.connect() as c:
            if not c.execute('SELECT 1 FROM matches WHERE id=?',(mid,)).fetchone():return False
            c.execute("UPDATE matches SET artwork=?,image=?,status='art_ready',gen_state='done',error='',updated=? WHERE id=?",
                      (str(artwork_path),str(artwork_path),time.time(),mid))
            return True
    def accept_bridge_artwork(self,mid,artwork_path):
        """Atomically accept one extension upload only while its job awaits download."""
        with self.connect() as c:
            result=c.execute("UPDATE matches SET artwork=?,image=?,status='art_ready',gen_state='done',error='',updated=? WHERE id=? AND status='awaiting_artwork' AND gen_state='awaiting_download'",
                             (str(artwork_path),str(artwork_path),time.time(),mid))
            return result.rowcount==1
    def note_poster(self,mid,poster_path):
        with self.connect() as c:
            if not c.execute('SELECT 1 FROM matches WHERE id=?',(mid,)).fetchone():return False
            c.execute("UPDATE matches SET poster=?,image=?,status='ready',error='',updated=? WHERE id=?",
                      (str(poster_path),str(poster_path),time.time(),mid))
            return True
    def cancel_handoff(self,mid):
        with self.connect() as c:
            row=c.execute('SELECT status FROM matches WHERE id=?',(mid,)).fetchone()
            if not row or row[0] in ('delivered','solo_delivered','baseline'):return False
            c.execute("UPDATE matches SET status='needs_review',gen_state='cancelled',error='Browser handoff cancelled; match data preserved.',updated=? WHERE id=?",
                      (time.time(),mid))
            return True
    def queue_snapshot(self,limit=QUEUE_LIMIT):
        limit=max(1,min(int(limit),50))
        with self.connect() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM matches WHERE status NOT IN ('delivered','solo_delivered','baseline') ORDER BY created ASC LIMIT ?",
                (limit,))]
    def extension_snapshot(self,limit=QUEUE_LIMIT):
        """Return explicit automatic jobs plus recent completed jobs.

        Historical review rows are intentionally excluded: they predate the
        automatic handoff and must never be mistaken for fresh Chrome jobs.
        """
        limit=max(1,min(int(limit),50))
        with self.connect() as c:
            active=[dict(r) for r in c.execute(
                "SELECT * FROM matches WHERE gen_state IN ('queued','prepared','staged','awaiting_generation','awaiting_download') AND status NOT IN ('delivered','solo_delivered','baseline','failed','uncertain') ORDER BY CASE WHEN gen_state='queued' THEN 1 ELSE 0 END, created ASC LIMIT ?",(limit,))]
            remaining=max(0,limit)
            completed=[dict(r) for r in c.execute(
                "SELECT * FROM matches WHERE status IN ('delivered','solo_delivered') ORDER BY updated DESC LIMIT ?",(remaining,))]
            return active+completed
    def stale_handoffs(self,timeout_secs=1800):
        cutoff=time.time()-max(60,int(timeout_secs))
        with self.connect() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM matches WHERE gen_state='staged' AND updated<? ORDER BY created ASC LIMIT 50",
                (cutoff,))]
    def reserve(self,mid,force=False,kind='squad',allow_both=False):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            keys=[mid+'|'+kind] if allow_both else [mid+'|solo',mid+'|squad']
            delivered=c.execute("SELECT 1 FROM deliveries WHERE id IN ("+','.join('?' for _ in keys)+") AND status='delivered' LIMIT 1",keys).fetchone()
            if delivered:return False
            excluded=['posting','delivered','baseline']+([] if allow_both and kind=='squad' else ['solo_delivered'])+([] if force else ['uncertain'])
            result=c.execute("UPDATE matches SET status='posting',attempts=attempts+1,updated=? WHERE id=? AND status NOT IN ("+','.join('?' for _ in excluded)+')',[time.time(),mid]+excluded)
            return result.rowcount==1
    def roster(self):
        with self.connect() as c:return [json.loads(r[0]) for r in c.execute('SELECT payload FROM roster')]
    def save_player(self,player):
        player.setdefault('id',uuid.uuid4().hex)
        with self.connect() as c:c.execute('INSERT OR REPLACE INTO roster VALUES(?,?)',(player['id'],json.dumps(player)))
    def delete_player(self,pid):
        with self.connect() as c:c.execute('DELETE FROM roster WHERE id=?',(pid,))
    def meta(self,key,default=None):
        with self.connect() as c:
            row=c.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone(); return json.loads(row[0]) if row else default
    def set_meta(self,key,value):
        with self.connect() as c:c.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,json.dumps(value)))

def load_matches(settings):
    m=legacy(); directory=m.find_stats_dir(settings)
    records=read_json(directory/'matches.json',[])
    if not isinstance(records,list): raise ValueError('matches.json must contain an array')
    return directory,m.watch_matches(directory,records,0)

def reserve_legacy_match(match):
    m=legacy(); state=read_json(m.STATE,{'posted_ids':[],'matches_since_career':0})
    state['posted_ids']=sorted(set(state.get('posted_ids',[]))|set(m.match_keys(match)))
    state.setdefault('feed_cutoff_ms',int(time.time()*1000)); atomic_json(m.STATE,state)

def _codmunity_context(match, snapshot):
    """CODMunity benchmark context lines, or [] without a stored snapshot."""
    if not snapshot:
        return []
    try:
        import codmunity_stats
        return codmunity_stats.benchmark_lines(match, snapshot)
    except Exception:
        return []


PERSONAL_ROW_STATS = ('kills', 'eliminations', 'damage', 'assists', 'deaths', 'score')

def codmunity_personal_row(match, player_name):
    """The local player's CODMunity end-game row for one match, or None.

    CODMunity records only the local player's end-game statistics, so
    this row is labelled with its source and never stands in for a
    squad scoreboard: teammates still require reviewed OCR rows.
    """
    name = str(player_name or '').strip()
    stats = match.get('stats') if isinstance(match, dict) and isinstance(match.get('stats'), dict) else {}
    values = {key: stats[key] for key in PERSONAL_ROW_STATS
              if isinstance(stats.get(key), (int, float)) and not isinstance(stats.get(key), bool)}
    if not name or not any(key in values for key in ('kills', 'eliminations', 'damage')):
        return None
    return {'name': name, 'stats': values, 'verified': True, 'source': 'codmunity_local'}

def merge_personal_row(players, match, player_name, aliases=()):
    """Append the CODMunity personal row unless OCR already covers it."""
    rows = list(players or [])
    if any(isinstance(row, dict) and str(row.get('source', '')).startswith('codmunity') for row in rows):
        return rows, False
    known = {normalize_name(name) for name in [player_name, *(aliases or [])] if str(name).strip()}
    if any(normalize_name(row.get('name', '')) in known for row in rows if isinstance(row, dict)):
        return rows, False
    personal = codmunity_personal_row(match, player_name)
    if personal is None:
        return rows, False
    return rows + [personal], True


def refresh_codmunity_stats(settings, store):
    """Best-effort CODMunity cache import; never breaks match tracking."""
    try:
        import codmunity_stats
        stats_dir = (settings or {}).get('stats_dir') or ''
        if not stats_dir:
            directory = legacy().find_stats_dir(settings or {})
            stats_dir = str(directory)
        if not codmunity_stats.needs_refresh(store, stats_dir):
            return None
        aliases = tuple(dict.fromkeys(
            [str((settings or {}).get('player') or 'vintendo')] + list(codmunity_stats.DEFAULT_ALIASES)))
        return codmunity_stats.import_snapshot(store, stats_dir, aliases=aliases,
                                               roster_player=str((settings or {}).get('player') or 'vintendo'))
    except Exception:
        return None


def match_poster_prompt(players, match=None, ocr_text='', variation_id=None, roster=None, snapshot=None):
    """Build a complete Meta request from match metadata and reviewed scoreboard rows."""
    ranked=rank_verified_players(players,match)
    if not ranked:raise ValueError('Review and save the actual match scoreboard before generating a poster.')
    context=extract_match_context(match or {},ocr_text)
    scoreboard=[]
    for player in ranked:
        detail=match_player_detail(player['name'],roster or []) if roster else {'player':None}
        matched=detail.get('player')
        poster_name=str((matched or {}).get('display_name') or player['name']).strip()
        row={'player':poster_name,'cod_username_for_identity':player['name'],'verified_statistics':player['stats']}
        if player['rank'] is not None:row['performance_rank']=player['rank']
        if player['ranking_basis']:row['ranking_basis']=player['ranking_basis']
        if player['mvp']:row['mvp']=True
        scoreboard.append(row)
    structured=json.dumps(scoreboard,ensure_ascii=False,separators=(',',':'))
    benchmark_lines=_codmunity_context(match or {},snapshot)
    benchmark_block=('\nCODMunity benchmark context (background only; do not render these benchmark numbers as match statistics; render only the verified match statistics above): '+'; '.join(benchmark_lines)+'\n' if benchmark_lines else '')
    return (
        'Create a wildly creative, hilarious, completely unpredictable 16:9 cinematic Call of Duty end-match image that celebrates or roasts this actual result. '
        'Invent the setting, story, costumes, title, action and visual comedy yourself. Do not choose from presets; make the artwork different every time.\n'
        f'{MATCH_POSTER_PROMPT_MARKER}\n'
        f'Unique match ID: {str((match or {}).get("matchId") or "unavailable")}\n'
        f'Creative variation ID: {str(variation_id or uuid.uuid4().hex[:12])}\n'
        f'Game mode: {context["game_mode"]}\nResult: {context["result"]}\nPlacement: {context["placement"]}\n'
        'Actual players and verified scoreboard values (JSON; omitted fields were unavailable):\n'+structured+'\n'+benchmark_block+
        'Use the poster display name as the visible name in the artwork. COD usernames are identity keys for matching people to their attached photos; do not print a different COD username when a poster display name is supplied. Use attached headshots only for the corresponding identities in the reference-photo mapping. You may feature the verified MVP prominently or the entire squad, depending on the match. '
        'Compose one full-bleed cinematic poster with the match results physically built into the scene on a dramatic in-world scoreboard, monitor, banner, sign, or match-complete display. Keep the players large and clear. '
        'Render the supplied result, game mode, poster display names, and exact verified statistics as visible text within the generated scene itself. Copy every supplied display name and number exactly; do not invent a lobby size, rank, score, or unavailable statistic. '
        'Do not leave the results for a later edit or add a separate graphic panel on top of the finished image.'
    )


def prompt_with_photo_mapping(prompt, assignments):
    prompt=str(prompt or '').split('\nREFERENCE PHOTO MAPPING:')[0]
    rows=[]
    for index,item in enumerate(assignments or [],1):
        if not isinstance(item,dict):continue
        name=str(item.get('player',item.get('username',''))).strip()
        filename=str(item.get('file',item.get('filename',''))).strip()
        if name and filename:rows.append(f'Reference {index} ({filename}) is {name}')
    if rows:prompt+='\nREFERENCE PHOTO MAPPING: '+'; '.join(rows)+'. Use each image only to portray its named player.'
    return prompt

def automatic_match_prompt(players, game_mode, outcome, assignments, snapshot=None):
    """Build the requested victory/defeat template from identified players only."""
    if outcome not in ('Victory','Defeat'):
        raise ValueError('Match outcome is unknown; automation will not guess win or loss.')
    identified=[p for p in players or [] if isinstance(p,dict) and p.get('verified') and str(p.get('name','')).strip()]
    if not 1<=len(identified)<=4:
        raise ValueError('Automatic poster jobs require one to four identified players.')
    if not game_mode or str(game_mode).startswith('Not shown'):
        raise ValueError('Game mode is unknown; automation will not invent a mode.')
    by_name={str(item.get('username','')):item for item in assignments or [] if isinstance(item,dict)}
    stats=[]
    for player in identified:
        identity=str(player['name']);item=by_name.get(identity,{})
        poster_name=str(item.get('posterName') or item.get('matchedRosterName') or identity).strip()
        values=player.get('stats') if isinstance(player.get('stats'),dict) else {}
        actual=', '.join(f'{key}={value}' for key,value in values.items()) or 'no verified statistics available'
        stats.append(f"{poster_name}: {actual}")
    mapping=[]
    for player in identified:
        name=str(player['name']);item=by_name.get(name,{})
        poster_name=str(item.get('posterName') or item.get('matchedRosterName') or name).strip()
        if item.get('status')=='ready' and item.get('filename'):
            mapping.append(f"COD username {name} uses reference {item['filename']}; show this person as {poster_name}")
        else:
            reason=str(item.get('reason') or item.get('status') or 'headshot not available')
            mapping.append(f'COD username {name} is shown as {poster_name}; NO REFERENCE IMAGE ({reason})')
    verb='WON' if outcome=='Victory' else 'LOST'
    celebration='celebrating their victory' if outcome=='Victory' else 'showing their hilarious defeat'
    return (f'Create a completely random, funny, cinematic 16:9 end-of-round Call of Duty-style picture featuring these {len(identified)} people using their attached reference photos. '
        f'They {verb} the match! Make the scene wildly creative, unpredictable, and different every time, {celebration}. Preserve their recognizable facial appearances. '
        'Compose it as one full-bleed cinematic 16:9 poster. Integrate an end-of-match results display naturally into the scene itself (for example, a battered arena screen, wall display, scoreboard, or banner), with the people clearly visible. '
        f'This was a {game_mode} match. The players and verified match statistics are: {"; ".join(stats)}. '
        f'Reference image assignments: {"; ".join(mapping)}. Do not substitute another person\'s image or claim a missing headshot is attached. '
        'The in-scene display must visibly include the exact supplied result, game mode, poster display names, and verified statistics. Copy every listed display name and value exactly. Treat COD usernames only as photo identity keys; do not print them as the poster name when a separate display name is given. Do not add a lobby size, rank, score, or other number unless it appears in the supplied statistics. '
        'Keep the results as part of the generated environment; do not add a separate footer, caption, post-processing overlay, or clean floating stats box.'
        +(' CODMunity benchmark context (background only; do not render these benchmark numbers as match statistics): '+'; '.join(_codmunity_context(str(game_mode),snapshot))+'.' if _codmunity_context(str(game_mode),snapshot) else ''))

def render_poster(background,target,match,players):
    from PIL import Image,ImageOps
    with Image.open(background) as source:
        if source.width<320 or source.height<180: raise ValueError('Artwork is too small')
        image=ImageOps.fit(source.convert('RGB'),(1600,900))
    # Keep Meta AI's generated image intact. Match statistics are requested as
    # lettering inside the generated scene, not painted on afterward.
    Path(target).parent.mkdir(parents=True,exist_ok=True); image.save(target,'JPEG',quality=94); return str(target)

def recap(match,players,snapshot=None):
    context=extract_match_context(match)
    lines=[f"🎮 **Vintendo Squad Night · {context['result'].upper()}**",context['game_mode']]
    if not context['placement'].startswith('Not shown'):lines.append('Placement: '+context['placement'])
    for p in players:
        if p.get('verified'):
            stats=' · '.join(f'{k}: {v}' for k,v in p.get('stats',{}).items() if isinstance(v,(int,float)) and not isinstance(v,bool))
            lines.append(p['name']+(' — '+stats if stats else ''))
    if match.get('playedAt'):lines.append('Played: '+str(match['playedAt']))
    stats=match.get('stats') if isinstance(match.get('stats'),dict) else {}
    local=' · '.join(f'{k}: {v}' for k,v in stats.items() if k in ('kills','eliminations','damage','assists','deaths','score','confirms') and isinstance(v,(int,float)) and not isinstance(v,bool))
    if local:lines.append('CODMunity local player: '+local)
    lines.extend(_codmunity_context(match,snapshot))
    return '\n'.join(lines)[:1900]

def send_discord(url,image_path,content,opener=None,sleep=time.sleep):
    import urllib.request,urllib.error
    from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname not in ('discord.com','discordapp.com') or not re.fullmatch(r'/api/webhooks/\d+/[A-Za-z0-9._-]+',parsed.path):raise ValueError('Invalid Discord webhook URL')
    query=dict(parse_qsl(parsed.query)); query['wait']='true'; url=urlunsplit(parsed._replace(query=urlencode(query)))
    boundary='----Vintendo'+uuid.uuid4().hex
    payload=json.dumps({'content':content[:1950],'allowed_mentions':{'parse':[]}}).encode()
    body=(f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\nContent-Type: application/json\r\n\r\n').encode()+payload+b'\r\n'
    if image_path:
        data=Path(image_path).read_bytes()
        if len(data)>10*1024*1024:raise ValueError('Image exceeds 10 MB; resize it first')
        mime='image/jpeg' if data[:3]==b'\xff\xd8\xff' else 'image/png'
        ext='jpg' if mime=='image/jpeg' else 'png'
        body+=(f'--{boundary}\r\nContent-Disposition: form-data; name="files[0]"; filename="match.{ext}"\r\nContent-Type: {mime}\r\n\r\n').encode()+data+b'\r\n'
    body+=(f'--{boundary}--\r\n').encode()
    request=urllib.request.Request(url,body,{'Content-Type':f'multipart/form-data; boundary={boundary}','User-Agent':'VintendoCODTracker/1.0'})
    opener=opener or urllib.request.urlopen
    for attempt in range(4):
        try:
            with opener(request,timeout=35) as response:
                raw=response.read(); return json.loads(raw).get('id','') if raw else ''
        except urllib.error.HTTPError as exc:
            if exc.code==429 and attempt<3:
                try: retry=float(json.loads(exc.read()).get('retry_after',1))
                except (ValueError,TypeError): retry=1
                sleep(max(1,min(60,retry)));continue
            raise RuntimeError(f'Discord returned HTTP {exc.code}') from None
        except (TimeoutError,OSError):
            raise RuntimeError('Delivery uncertain after a network failure. Check Discord before retrying.') from None

