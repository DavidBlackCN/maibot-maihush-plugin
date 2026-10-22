"""Half-open interval algebra. Charges only newly reserved union pieces."""
def union(intervals):
    result=[]
    for a,b in sorted((a,b) for a,b in intervals if b>a):
        if result and a <= result[-1][1]: result[-1]=(result[-1][0],max(b,result[-1][1]))
        else: result.append((a,b))
    return result

def subtract(interval, existing):
    a,b=interval; result=[]
    for x,y in union(existing):
        if y<=a: continue
        if x>=b: break
        if x>a: result.append((a,min(b,x)))
        a=max(a,y)
        if a>=b: break
    if a<b: result.append((a,b))
    return result

def window_peak(intervals, width=3600):
    pieces=union(intervals)
    # Occupancy is piecewise linear; extrema occur at an endpoint or an
    # endpoint shifted by the window width. Check every such breakpoint.
    ends={p for pair in pieces for p in pair}
    return max((sum(max(0,min(b,t+width)-max(a,t)) for a,b in pieces)
                for t in ends | {p-width for p in ends}),default=0)

def reserve_end(now, wanted, existing, owned, group_cap, actor_cap):
    def fits(end):
        added=subtract((now,end),existing)
        return window_peak(existing+added)<=group_cap+1e-7 and window_peak(owned+added)<=actor_cap+1e-7
    # Whole-second grants never round an insufficient budget upward.
    lo,hi=0,max(0,int(wanted-now))
    while lo<hi:
        mid=(lo+hi+1)//2
        if fits(now+mid): lo=mid
        else: hi=mid-1
    return now+lo
