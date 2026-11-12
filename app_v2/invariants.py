"""Cross-object invariants on a disposable RDF transaction view."""
def validate_boundary(graph,changes=()):
    kinds=('Workpiece','Equipment','AssetState','FixturePool','FixtureReservation','MaterialBalance','MaterialLot','WorkOrder','Execution')
    before={r['id']:r for kind in kinds for r in graph.entities(kind)}
    rows={**before,**{r['id']:r for r in changes}}
    typed=lambda kind:[r for r in rows.values() if r['type']==kind]
    errors=[];jobs=typed('Workpiece');machines={r['id']:r for r in typed('Equipment')}
    states={r['resource']:r for r in typed('AssetState')}
    for job in jobs:
        if job['status'] not in ('pending','queued','setup','processing','transfer','done','scrapped','material_wait'):errors.append(job['id']+': 未知状态')
        old=before.get(job['id'])
        if old and any(old[k]!=job[k] for k in ('order','batch','product','material_lot')):errors.append(job['id']+': 批次或产品身份不可隐式改变')
        if job['status'] in ('queued','setup','processing'):
            if job['assigned'] not in machines or job['location']!=job['assigned']:errors.append(job['id']+': 位置与设备不一致')
        if job['status'] in ('setup','processing'):
            execution=rows.get(job['execution'])
            if not execution or execution['status']!='active' or execution['resource']!=job['assigned']:errors.append(job['id']+': 活动与设备不一致')
            if not states.get(job['assigned']) or states[job['assigned']]['busy']!=job['id']:errors.append(job['id']+': 设备占用不一致')
    for rid,asset in states.items():
        active=[j for j in jobs if j['assigned']==rid and j['status'] in ('setup','processing')]
        if len(active)>1:errors.append(rid+': 单容量设备重复占用')
        if asset['status']=='maintenance' and active:errors.append(rid+': 维护与加工重叠')
        if asset['busy'] and (asset['busy'] not in rows or not active):errors.append(rid+': 占用工件不存在')
    for pool in typed('FixturePool'):
        reserved=[x for x in typed('FixtureReservation') if x['fixture']==pool['id'] and x['status']=='reserved']
        if len(reserved)>pool['quantity']:errors.append(pool['id']+': 夹具预留超量')
    for balance in typed('MaterialBalance'):
        lot=rows[balance['material_lot']]
        if balance['available']<0 or balance['consumed']<0 or balance['available']+balance['consumed']!=lot['quantity']:errors.append(lot['id']+': 物料不守恒')
        if balance['consumed']!=sum(j['material_lot']==lot['id'] and j['released_at']>=0 for j in jobs):errors.append(lot['id']+': 领用数量与已投产工件不一致')
    for order in typed('WorkOrder'):
        if sum(j['order']==order['id'] for j in jobs)!=order['quantity']:errors.append(order['id']+': 工件数量不守恒')
    if errors:raise ValueError('运行状态校验失败：'+'；'.join(errors[:15]))
    return {'conforms':True,'workpieces':len(jobs),'equipment':len(machines),'checks':['identity','location','occupancy','maintenance','fixture','material','quantity']}
