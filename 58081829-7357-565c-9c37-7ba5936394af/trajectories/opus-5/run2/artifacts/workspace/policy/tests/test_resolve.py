import json,hashlib,subprocess,sys
sys.path.insert(0,'/workspace/policy')
from resolve import resolve, InvalidHistory

fails=[]
def ok(name,cond):
    print(('PASS ' if cond else 'FAIL ')+name)
    if not cond: fails.append(name)

def digest(evs):
    return hashlib.sha256(json.dumps(evs,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def ev(a,r,s,op='grant',start='2026-01-01',end='2100-01-01',params=None):
    return {'authorization':a,'revision':r,'subject':s,'operation':op,'start':start,'end':end,'parameters':params if params is not None else ['P']}

def raises(req):
    try:
        resolve(req); return False
    except InvalidHistory: return True

# real feed
feed=json.load(open('/workspace/policy/authorizations.json'))
c={x['id']:x['request'] for x in feed['candidates']}
ok('close-18 rejected', raises(c['close-18']))
ok('close-17 accepted', resolve(c['close-17'])=={'mf-3480':None,'mf-6350':['2042-B2','430']})

base=dict(now='2026-09-05',subjects=['s1','s2'])

# correction moves subject: old revision must not survive on former subject
r=dict(base,events=[ev('A','1','s1',params=['old']),ev('A','2','s2',params=['new'])])
ok('correction moves subject', resolve(r)=={'s1':None,'s2':['new']})

# winning withdrawal contributes nothing
r=dict(base,events=[ev('A','1','s1'),ev('A','2','s1',op='withdraw')])
ok('winning withdrawal', resolve(r)=={'s1':None,'s2':None})

# superseded withdrawal loses to later grant
r=dict(base,events=[ev('A','2','s1',op='withdraw'),ev('A','3','s1',params=['live'])])
ok('withdrawal superseded', resolve(r)=={'s1':['live'],'s2':None})

# numeric not lexicographic revision ordering
r=dict(base,events=[ev('A','2','s1',params=['two']),ev('A','10','s1',params=['ten'])])
ok('numeric revision order', resolve(r)=={'s1':['ten'],'s2':None})

# out of window: end is exclusive
r=dict(base,events=[ev('A','1','s1',start='2026-01-01',end='2026-09-05')])
ok('end exclusive', resolve(r)=={'s1':None,'s2':None})
r=dict(base,events=[ev('A','1','s1',start='2026-09-05',end='2026-09-06')])
ok('start inclusive', resolve(r)=={'s1':['P'],'s2':None})

# correction moves out of window
r=dict(base,events=[ev('A','1','s1'),ev('A','2','s1',start='2030-01-01',end='2031-01-01')])
ok('correction out of window', resolve(r)=={'s1':None,'s2':None})

# identical duplicate revision is idempotent
r=dict(base,events=[ev('A','1','s1'),ev('A','1','s1')])
ok('identical revision idempotent', resolve(r)=={'s1':['P'],'s2':None})

# conflicting equal revision invalid even when superseded
r=dict(base,events=[ev('A','1','s1',params=['x']),ev('A','1','s1',params=['y']),ev('A','9','s1',params=['z'])])
ok('conflicting equal revision (superseded)', raises(r))

# multiple active authorizations for one subject
r=dict(base,events=[ev('A','1','s1',params=['a']),ev('B','1','s1',params=['b'])])
ok('multiple active for subject', raises(r))

# two authorizations, different subjects, both active: fine
r=dict(base,events=[ev('A','1','s1',params=['a']),ev('B','1','s2',params=['b'])])
ok('distinct subjects both active', resolve(r)=={'s1':['a'],'s2':['b']})

# unknown subjects excluded from projection
r=dict(base,events=[ev('A','1','zz',params=['q'])])
ok('unknown subject excluded', resolve(r)=={'s1':None,'s2':None})

# malformed events
ok('bad key set', raises(dict(base,events=[{'authorization':'A','revision':'1','subject':'s1','operation':'grant','start':'2026-01-01','end':'2100-01-01'}])))
ok('leading zero revision', raises(dict(base,events=[ev('A','01','s1')])))
ok('negative revision', raises(dict(base,events=[ev('A','-1','s1')])))
ok('int revision', raises(dict(base,events=[ev('A',1,'s1')])))
ok('start==end', raises(dict(base,events=[ev('A','1','s1',start='2026-01-01',end='2026-01-01')])))
ok('start>end', raises(dict(base,events=[ev('A','1','s1',start='2027-01-01',end='2026-01-01')])))
ok('bad operation', raises(dict(base,events=[ev('A','1','s1',op='revoke')])))
ok('bad date form', raises(dict(base,events=[ev('A','1','s1',start='01-01-2026')])))
ok('params not strings', raises(dict(base,events=[ev('A','1','s1',params=[1])])))
ok('empty subject', raises(dict(base,events=[ev('A','1','')])))

# request shape
ok('missing now', raises({'subjects':['s1'],'events':[]}))
ok('missing subjects', raises({'now':'2026-09-05','events':[]}))
ok('both events and transport', raises(dict(base,events=[],transport={'commits':[],'pages':[]})))
ok('neither', raises(dict(base)))

# ---- transport ----
A=[ev('A','1','s1',params=['t'])]
B=[ev('B','1','s2',params=['u'])]
def tr(commits,pages): return dict(base,transport={'commits':commits,'pages':pages})

good=tr([{'batch':'b1','parts':1,'sha256':digest(A)},{'batch':'b2','parts':1,'sha256':digest(B)}],
        [{'batch':'b1','part':0,'events':A},{'batch':'b2','part':0,'events':B}])
ok('transport two batches', resolve(good)=={'s1':['t'],'s2':['u']})

# deltas across parts concatenate in numeric order
split=[ev('A','1','s1',params=['p0']),ev('B','1','s2',params=['p1'])]
two=tr([{'batch':'b','parts':2,'sha256':digest(split)}],
       [{'batch':'b','part':1,'events':[split[1]]},{'batch':'b','part':0,'events':[split[0]]}])
ok('parts concatenate numerically', resolve(two)=={'s1':['p0'],'s2':['p1']})

ok('missing part', raises(tr([{'batch':'b','parts':2,'sha256':digest(split)}],[{'batch':'b','part':0,'events':[split[0]]}])))
ok('extra part', raises(tr([{'batch':'b','parts':1,'sha256':digest(A)}],[{'batch':'b','part':0,'events':A},{'batch':'b','part':1,'events':B}])))
ok('uncommitted page', raises(tr([{'batch':'b1','parts':1,'sha256':digest(A)}],[{'batch':'b1','part':0,'events':A},{'batch':'zz','part':0,'events':B}])))
ok('digest mismatch', raises(tr([{'batch':'b','parts':1,'sha256':digest(B)}],[{'batch':'b','part':0,'events':A}])))
ok('identical page replay', resolve(tr([{'batch':'b','parts':1,'sha256':digest(A)}],[{'batch':'b','part':0,'events':A},{'batch':'b','part':0,'events':A}]))=={'s1':['t'],'s2':None})
ok('conflicting page replay', raises(tr([{'batch':'b','parts':1,'sha256':digest(A)}],[{'batch':'b','part':0,'events':A},{'batch':'b','part':0,'events':B}])))
ok('conflicting commit replay', raises(tr([{'batch':'b','parts':1,'sha256':digest(A)},{'batch':'b','parts':2,'sha256':digest(A)}],[{'batch':'b','part':0,'events':A}])))
ok('invalid event inside superseded batch', raises(tr([{'batch':'b','parts':1,'sha256':digest([ev('A','01','s1')])}],[{'batch':'b','part':0,'events':[ev('A','01','s1')]}])))

# batch order must not matter for history membership
rev=tr([{'batch':'b2','parts':1,'sha256':digest(B)},{'batch':'b1','parts':1,'sha256':digest(A)}],
       [{'batch':'b2','part':0,'events':B},{'batch':'b1','part':0,'events':A}])
ok('commit order irrelevant', resolve(rev)==resolve(good))

# ---- CLI exit codes ----
def cli(req):
    p=subprocess.run(['python3','/workspace/policy/resolve.py'],input=json.dumps(req),text=True,capture_output=True)
    return p.returncode,p.stdout.strip()
rc,out=cli(c['close-17'])
ok('cli accepts close-17', rc==0 and json.loads(out)=={'mf-3480':None,'mf-6350':['2042-B2','430']})
rc,out=cli(c['close-18'])
ok('cli rejects close-18 nonzero', rc!=0 and out=='')

print()
print('FAILURES:',fails if fails else 'none')
sys.exit(1 if fails else 0)
