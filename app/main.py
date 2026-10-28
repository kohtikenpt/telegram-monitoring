import os, asyncio, secrets, contextlib
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, FileResponse, PlainTextResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from . import core as c
from . import account_groups
from .account_monitor import runtime as account_monitor

security=HTTPBasic()
def auth(credentials:HTTPBasicCredentials=Depends(security)):
    usr=os.getenv('DASHBOARD_USERNAME','admin');pwd=os.getenv('DASHBOARD_PASSWORD','')
    if not pwd or not (secrets.compare_digest(credentials.username,usr) and secrets.compare_digest(credentials.password,pwd)):
        raise HTTPException(401,'Invalid credentials / configure DASHBOARD_PASSWORD',headers={'WWW-Authenticate':'Basic'})
    return True

@asynccontextmanager
async def lifespan(app):
    c.init_db()
    jobs=[]
    if c.TOKEN and c.TOKEN!='PASTE_TOKEN_HERE':jobs.append(asyncio.create_task(c.polling_loop()))
    if account_monitor.enabled:jobs.append(asyncio.create_task(account_monitor.run()))
    jobs.append(asyncio.create_task(c.scheduler_loop()))
    yield
    for job in jobs:job.cancel()
    for job in jobs:
        with contextlib.suppress(asyncio.CancelledError):await job
    await c.client.aclose()

app=FastAPI(title='Telegram Monitoring MVP',lifespan=lifespan)
app.mount('/static',StaticFiles(directory=Path(__file__).parent/'static'),name='static')

@app.get('/health')
def health():return {'status':'ok','bot_configured':bool(c.TOKEN and c.TOKEN!='PASTE_TOKEN_HERE')}

@app.get('/',response_class=HTMLResponse)
def dashboard(_=Depends(auth)):
    return (Path(__file__).parent/'dashboard.html').read_text(encoding='utf-8')

@app.get('/api/stats')
def stats(_=Depends(auth)):
    count=lambda tbl:c.query('SELECT COUNT(*) n FROM '+tbl)[0]['n']
    return {'groups':count('groups'),'messages':c.query('SELECT COUNT(*) n FROM messages WHERE monitor_visible(body,filename,security_review)')[0]['n'],'alerts':c.query('SELECT COUNT(*) n FROM alerts a JOIN messages m ON m.id=a.message_ref WHERE monitor_visible(m.body,m.filename,m.security_review)')[0]['n'],'open_tasks':c.query("SELECT COUNT(*) n FROM tasks WHERE status='open'")[0]['n'],'ai_enabled':c.AI_ENABLED,'private_reviews':c.query('SELECT COUNT(*) n FROM messages WHERE security_review=1')[0]['n'],'private_forwards_enabled':c.PRIVATE_FORWARDS and bool(c.PRIVATE_REVIEWERS),'private_inbox_enabled':c.PRIVATE_INBOX_ENABLED,'private_inbox_connected':c.ACCOUNT_PRIVATE_ACTIVE}

@app.get('/api/groups')
def groups(_=Depends(auth)):return c.query('SELECT * FROM groups ORDER BY last_seen DESC')

@app.get('/api/groups/dashboard')
def group_dashboard(_=Depends(auth)):
    return {**account_groups.dashboard_data(), 'account_monitor':account_monitor.status()}

@app.get('/api/account/monitor')
def account_monitor_status(_=Depends(auth)):
    return account_monitor.status()

@app.post('/api/account/groups/sync')
async def sync_account_group_list(_=Depends(auth)):
    try:
        return await account_groups.sync_account_groups()
    except account_groups.AccountSyncError as error:
        raise HTTPException(409, str(error)) from None

@app.get('/api/messages')
def messages(q:str='',group:int|None=None,kind:str='',limit:int=Query(100,ge=1,le=500),offset:int=Query(0,ge=0),_=Depends(auth)):
    clauses=['monitor_visible(m.body,m.filename,m.security_review)'];args=[]
    if q:clauses.append('(m.body LIKE ? OR m.user_name LIKE ? OR m.filename LIKE ? OR m.ai_note LIKE ? OR m.sender_name LIKE ? OR CAST(m.user_id AS TEXT)=?)');args.extend(['%'+q+'%']*5+[q])
    if group is not None:clauses.append('m.chat_id=?');args.append(group)
    if kind:clauses.append('m.kind=?');args.append(kind)
    where=('WHERE '+' AND '.join(clauses)) if clauses else ''
    return c.query(f'SELECT m.*,COALESCE(g.title,CASE WHEN m.chat_type="private" AND m.ingest_source="account" THEN "Private inbox" WHEN m.chat_type="private" THEN "Forwarded inbox" ELSE "Group" END) group_title FROM messages m LEFT JOIN groups g ON m.chat_id=g.chat_id {where} ORDER BY m.id DESC LIMIT ? OFFSET ?',(*args,limit,offset))

@app.get('/api/security')
def security_reviews(_=Depends(auth)):
    return c.query('SELECT * FROM messages WHERE chat_type="private" AND security_review=1 ORDER BY id DESC LIMIT 200')

@app.get('/api/alerts')
def alerts(_=Depends(auth)):return c.query('SELECT a.* FROM alerts a JOIN messages m ON m.id=a.message_ref WHERE monitor_visible(m.body,m.filename,m.security_review) ORDER BY a.id DESC LIMIT 200')

@app.get('/api/tasks')
def tasks(status:str='',_=Depends(auth)):
    if status:return c.query('SELECT * FROM tasks WHERE status=? ORDER BY id DESC',(status,))
    return c.query('SELECT * FROM tasks ORDER BY id DESC LIMIT 300')

class TaskIn(BaseModel):
    chat_id:int|None=None
    title:str=Field(min_length=1,max_length=500)
    assignee:str=Field(default='',max_length=80)
    due:str=Field(default='',max_length=10)

@app.post('/api/tasks')
def add_task(task:TaskIn,_=Depends(auth)):
    if task.due:
        try:datetime.strptime(task.due,'%Y-%m-%d')
        except ValueError:raise HTTPException(400,'due must be YYYY-MM-DD')
    tid=c.execute('INSERT INTO tasks(chat_id,title,assignee,due,created,updated) VALUES(?,?,?,?,?,?)',(task.chat_id,task.title,task.assignee,task.due,c.now(),c.now()))
    return {'id':tid}

@app.patch('/api/tasks/{task_id}')
def set_task(task_id:int,status:str=Query(pattern='^(open|done)$'),_=Depends(auth)):
    if not c.query('SELECT id FROM tasks WHERE id=?',(task_id,)):raise HTTPException(404,'Unknown task')
    c.execute('UPDATE tasks SET status=?,updated=? WHERE id=?',(status,c.now(),task_id))
    return {'ok':True}

@app.get('/api/media/{message_id}')
def media(message_id:int,_=Depends(auth)):
    rows=c.query('SELECT media_path,filename FROM messages WHERE id=? AND chat_type!="private" AND ingest_source="bot" AND monitor_visible(body,filename,security_review)',(message_id,))
    if not rows or not rows[0]['media_path']:raise HTTPException(404,'Media not available; may exceed file size limit')
    base=c.MEDIA.resolve();p=(base/rows[0]['media_path']).resolve()
    if p.parent!=base or not p.is_file():raise HTTPException(404,'Missing')
    return FileResponse(p,filename=rows[0]['filename'] or p.name,media_type='application/octet-stream')

@app.get('/api/report')
async def report(days:int=Query(1,ge=1,le=7),_=Depends(auth)):
    return {'report':await c.summary(days)}

@app.post('/api/analyze/{message_id}')
async def analyze_one(message_id:int,_=Depends(auth)):
    if not c.AI_ENABLED:raise HTTPException(400,'Enable AI and set OPENAI_API_KEY first')
    rows=c.query('SELECT body FROM messages WHERE id=? AND chat_type!="private" AND monitor_visible(body,filename,security_review)',(message_id,))
    if not rows:raise HTTPException(404,'Not found')
    result=await c.analyze(rows[0]['body'] or '')
    if not result:raise HTTPException(422,'No text / analysis unavailable')
    c.execute('UPDATE messages SET ai_label=?,ai_note=? WHERE id=?',(result['label'],result['note'],message_id))
    return result
