"""Windowed entry point that also records errors occurring during imports."""
import os,sys,re,traceback
from pathlib import Path
try:
    from app import main
    main()
except SystemExit:raise
except BaseException:
    folder=Path(os.environ.get('VINTENDO_DATA_DIR',str(Path(os.environ.get('LOCALAPPDATA',Path.home()))/'Vintendo COD Tracker')))
    folder.mkdir(parents=True,exist_ok=True)
    text=re.sub(r'https://discord(?:app)?\.com/api/webhooks/\S+','[webhook]',traceback.format_exc())
    (folder/'startup-error.log').write_text(text,encoding='utf-8')
    if sys.stderr:sys.stderr.write(text)
    sys.exit(1)
