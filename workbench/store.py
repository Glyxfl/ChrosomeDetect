import copy
import json
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timezone
from .config import DATA, STAGES

def now():
    return datetime.now(timezone.utc).isoformat()

class Conflict(ValueError):
    pass

class Store:
    def __init__(self, root=DATA):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, project_id TEXT, body TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS records(kind TEXT, id TEXT, body TEXT, PRIMARY KEY(kind,id))')

    def records(self, kind):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT body FROM records WHERE kind=? ORDER BY rowid DESC', (kind,))]

    def record(self, kind, value):
        with self.lock, self.connect() as db:
            db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)', (kind, value['id'], json.dumps(value, ensure_ascii=False)))

    def history(self, identifier):
        self.get(identifier)
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT body FROM events WHERE project_id=? ORDER BY id', (identifier,))]

    def connect(self):
        return sqlite3.connect(self.root / 'workbench.sqlite3', timeout=30)

    def list(self):
        with self.connect() as db:
            return [upgrade(json.loads(r[0])) for r in db.execute('SELECT body FROM projects ORDER BY rowid DESC')]

    def get(self, identifier):
        with self.connect() as db:
            row = db.execute('SELECT body FROM projects WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise KeyError('项目不存在')
        return upgrade(json.loads(row[0]))

    def create(self, project):
        with self.lock, self.connect() as db:
            db.execute('INSERT INTO projects VALUES (?,?)', (project['id'], json.dumps(project, ensure_ascii=False)))
        return project

    def change(self, identifier, revision, action, fn):
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT body FROM projects WHERE id=?', (identifier,)).fetchone()
            if not row:
                raise KeyError('项目不存在')
            project = upgrade(json.loads(row[0]))
            if revision != project['revision']:
                raise Conflict('结果已更新，请刷新后重试')
            before = copy.deepcopy(project)
            fn(project)
            project=upgrade(project)
            project['revision'] += 1
            project['updated_at'] = now()
            db.execute('UPDATE projects SET body=? WHERE id=?', (json.dumps(project, ensure_ascii=False), identifier))
            db.execute('INSERT INTO events(project_id,body) VALUES (?,?)', (identifier, json.dumps({
                'at': now(), 'action': action, 'from_revision': revision,
                'before': before, 'after': project,
            }, ensure_ascii=False)))
        return project

def invalidate(project, stage):
    index = STAGES.index(stage)
    project['confirmed'][stage] = False
    for downstream in STAGES[index + 1:]:
        # SDPC detection preceded crop quality grading and is independent of it.
        if stage=='quality' and downstream=='clusters' and project.get('source_slide'):continue
        project['confirmed'][downstream] = False
        if downstream != 'clusters':
            project['results'][downstream] = []
        project.get('truth_complete', {})[downstream] = False
        if downstream in project.get('predictions', {}):
            project['predictions'][downstream]['stale'] = True
    project.get('truth_complete', {})[stage] = False
    project['stage'] = stage

def upgrade(project):
    for stage in STAGES:
        project['confirmed'].setdefault(stage, False)
        project['results'].setdefault(stage, [])
    project.setdefault('predictions', {})
    project.setdefault('timings', [])
    project.setdefault('truth_complete', {})
    project.setdefault('schema_version', 2)
    # Legacy cluster-level outliers cannot be presented as chromosome-level findings.
    if any('instance_id' not in r for r in project['results']['outliers']):
        project.setdefault('legacy_cluster_outliers', project['results']['outliers'])
        project['results']['outliers'] = []
        project['confirmed']['outliers'] = False
    for stage in STAGES[:STAGES.index(project['stage'])]:
        if not project['confirmed'][stage]:
            project['stage']=stage
            break
    source=project.get('source_slide')
    if source:
        ox,oy=source['crop_box_l0'][:2]
        for group in [project['results'], *[{'prediction':s['rows']} for s in project.get('predictions',{}).values()]]:
            for rows in group.values():
                for row in rows:
                    if 'bbox' in row:
                        b=row['bbox'];row['bbox_l0']=[b[0]+ox,b[1]+oy,b[2]+ox,b[3]+oy];row['slide_id']=source['id']
                        if row.get('polygon'):row['polygon_l0']=[[x+ox,y+oy] for x,y in row['polygon']]
                        else:row.pop('polygon_l0',None)
    return project
