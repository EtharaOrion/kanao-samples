#!/bin/bash
for _ in $(seq 1 150); do [ -f /var/run/env-ready ] && break; sleep 2; done
set -uo pipefail

cat > /workspace/policy/resolve.py <<'POLICY'
"""Resolve the current signed authorization snapshot."""
import json
import sys


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')


def history(request):
    if 'transport' not in request:
        return request['events']
    import hashlib
    envelope=request['transport']
    batches={}
    for commit in envelope['commits']:
        key=commit['batch']
        if key in batches and batches[key]!=commit:
            raise ValueError('conflicting commit')
        if type(commit['parts']) is not int or commit['parts']<1:raise ValueError('invalid part count')
        batches[key]=commit
    if 'generation' in envelope:
        import os
        generation=envelope['generation']
        if not isinstance(generation,str) or not generation:raise ValueError('invalid generation')
        source=os.environ.get('MARROWFLEET_POLICY_MANIFESTS','/workspace/policy/manifests.json')
        with open(source,encoding='utf-8') as fh:
            generations=json.load(fh)['generations']
        if generation not in generations:raise ValueError('unknown generation')
        if {batch:commit['sha256'] for batch,commit in batches.items()}!=generations[generation]['batches']:
            raise ValueError('committed batches differ from the release manifest')
    pages={}
    for page in envelope['pages']:
        key=(page['batch'],page['part'])
        if page['batch'] not in batches:raise ValueError('uncommitted page')
        if type(page['part']) is not int or not 0<=page['part']<batches[page['batch']]['parts']:raise ValueError('invalid part index')
        if key in pages and pages[key]!=page:raise ValueError('conflicting page')
        pages[key]=page
    result=[]
    for batch,commit in batches.items():
        events=[]
        for part in range(commit['parts']):
            if (batch,part) not in pages:raise ValueError('missing committed page')
            events.extend(pages[(batch,part)]['events'])
        if hashlib.sha256(canonical(events)).hexdigest()!=commit['sha256']:raise ValueError('batch digest mismatch')
        result.extend(events)
    return result


def resolve(request):
    # Select corrections globally before projecting onto current subjects/time.
    winners = {}
    identities = {}
    for event in history(request):
        from datetime import date
        required={'authorization','revision','subject','operation','start','end','parameters'}
        if not isinstance(event,dict) or set(event)!=required:raise ValueError('invalid event schema')
        if any(not isinstance(event[k],str) or not event[k] for k in ('authorization','subject','start','end')):raise ValueError('invalid event identity')
        if not isinstance(event['revision'],str) or not event['revision'].isdigit() or str(int(event['revision']))!=event['revision']:raise ValueError('noncanonical revision')
        if event['operation'] not in ('grant','withdraw'):raise ValueError('invalid operation')
        if not isinstance(event['parameters'],list) or not all(isinstance(v,str) for v in event['parameters']):raise ValueError('invalid parameters')
        start,end=date.fromisoformat(event['start']),date.fromisoformat(event['end'])
        if start.isoformat()!=event['start'] or end.isoformat()!=event['end'] or start>=end:raise ValueError('invalid interval')
        key = event['authorization']
        revision = int(event['revision'])
        identity = (key, revision)
        if identity in identities and event != identities[identity]:
            raise ValueError('conflicting revision')
        identities[identity] = event
        if key not in winners or revision > int(winners[key]['revision']):
            winners[key] = event
    result = {subject: None for subject in request['subjects']}
    assigned = set()
    for event in winners.values():
        subject = event['subject']
        if event['operation'] == 'withdraw' or not (event['start'] <= request['now'] < event['end']):
            continue
        if subject not in result:
            continue
        if subject in assigned:
            raise ValueError('overlapping authorizations')
        assigned.add(subject)
        result[subject] = event['parameters']
    return result


if __name__ == '__main__':
    print(json.dumps(resolve(json.load(sys.stdin)),sort_keys=True))
POLICY

# The licence check left this crew member on the sectors before.
marrowfleet period set mf-2210 2041-B5

# Raised while the flight time limitations query was open, and the drop has
# taken nothing from the run since.
marrowfleet hold clear oxenholme-base

# Narrowed while the cabin establishment was under review, so the base has been
# standing a drop short of the ranks its fragment declares.
marrowfleet scope clear kirkbryde-base

# The fatigue limitation on mf-3480 was withdrawn and the certificate limitation
# on mf-6350 stands to 2042-B2. Both subjects are governed by the policy feed, so
# the release applies them from the repaired resolver; nothing is set by hand.

# The published reader takes the crediting factor over the period's block
# minutes together rather than over each sector, and settles no sheet against
# the factor its category is credited at.
READER=/workspace/tools/rostercheck/rostercheck.py
sed -i 's|^    return sum(blocks) \* factor // CREDIT_BASE$|    return sum(m * factor // CREDIT_BASE for m in blocks)|' "$READER"
sed -i 's|^    if head.get("category") not in FACTORS:$|    if FACTORS.get(head.get("category")) != int(head.get("factor", "0")):|' "$READER"

/workspace/bin/run-roster >/dev/null 2>&1

marrowfleet endorse >/dev/null 2>&1

echo "reading what each base's own drop stands"
FP=$(sed -n 's|^key_fingerprint: *||p' /workspace/rostering/rostering.yaml)
for conf in /workspace/rostering/bases.d/*.toml; do
    name=$(sed -n 's|^name = "\(.*\)"$|\1|p' "$conf")
    echo "--- $name"
    for held in "/srv/bases/$name"/*.sheet; do
        [ -e "$held" ] || continue
        python3 "$READER" "$held" "$FP"
    done
done
