"""Read-only playback of committed production boundaries, indexed once per view."""
from collections import Counter
from .graph import decode_record

TYPES={'Clock','Workpiece','AssetState','Execution','Inspection','OperationPlan','Equipment','WorkOrder','Batch'}


class ReplayIndex:
    def __init__(self,graph,head):
        self.head=head
        self.frames=[];self.deltas=[];self.snapshots={};state={}
        at=0.;pending={};activity=[];sequence=0;event_time=None

        def publish():
            if not pending:return
            index=len(self.frames)
            self.frames.append(dict(index=index,sequence=sequence,time_s=event_time,
                                    activities=list(dict.fromkeys(activity)),changed=len(pending)))
            self.deltas.append(dict(pending))
            state.update(pending)
            if index%64==0:self.snapshots[index]=dict(state)
            pending.clear();activity.clear()

        for entry in graph.records(through=head):
            p=entry['payload']
            selected=[c['after'] for c in p['changes'] if c['after']['type'] in TYPES]
            if not selected:continue
            clock=next((r for r in selected if r['type']=='Clock'),None)
            if clock:at=clock['time_s']
            if event_time is not None and abs(event_time-at)>1e-6:publish()
            event_time=at;sequence=p['sequence']
            for r in selected:pending[r['id']]=decode_record(r)
            activity.append(p['kind'])
        publish()

    def frame(self,index):
        if not 0<=index<len(self.frames):raise ValueError('回放时刻不存在')
        base=index//64*64
        rows=dict(self.snapshots[base])
        for delta in self.deltas[base+1:index+1]:rows.update(delta)
        jobs=sorted((r for r in rows.values() if r['type']=='Workpiece'),key=lambda j:j['id'])
        route={(r['product'],r['index']):r['name'] for r in rows.values() if r['type']=='OperationPlan'}
        jobs=[{**j,'operation_name':route.get((j['product'],j['op_index']),'—')} for j in jobs]
        equipment=[{**r,'name':rows.get(r['resource'],{}).get('name',r['resource']),
                    'queue':sum(j['status']=='queued' and j['assigned']==r['resource'] for j in jobs)}
                   for r in rows.values() if r['type']=='AssetState']
        prior=dict(self.snapshots[(index-1)//64*64]) if index else {}
        if index:
            for delta in self.deltas[(index-1)//64*64+1:index]:prior.update(delta)
        changes=[{**r,'previous_status':prior.get(r['id'],{}).get('status'),
                  'previous_location':prior.get(r['id'],{}).get('location'),
                  'previous_busy':prior.get(r['id'],{}).get('busy')}
                 for r in self.deltas[index].values() if r['type'] in ('AssetState','Execution','Inspection','Workpiece') and prior.get(r['id'])!=r]
        return {**self.frames[index],'mode':'historical_state','total':len(self.frames),'jobs':jobs,
                'equipment':sorted(equipment,key=lambda r:r['resource']),'counts':dict(Counter(j['status'] for j in jobs)),
                'changes':changes}
