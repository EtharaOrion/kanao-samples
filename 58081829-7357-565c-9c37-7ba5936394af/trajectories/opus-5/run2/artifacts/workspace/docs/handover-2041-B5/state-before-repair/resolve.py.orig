"""Resolve the current signed authorization snapshot."""
import json
import sys


def resolve(request):
    if 'transport' in request:
        transport=request['transport']
        newest=max(c['batch'] for c in transport['commits'])
        request=dict(request,events=[event for page in transport['pages'] if page['batch']==newest for event in page['events']])
    selected = {}
    for event in request['events']:
        if event['operation'] == 'withdraw' or not (event['start'] <= request['now'] <= event['end']):
            continue
        subject = event['subject']
        old = selected.get(subject)
        if old is None or str(event['revision']) > str(old['revision']):
            selected[subject] = event
    return {subject: selected[subject]['parameters'] if subject in selected else None for subject in request['subjects']}


if __name__ == '__main__':
    print(json.dumps(resolve(json.load(sys.stdin)),sort_keys=True))
