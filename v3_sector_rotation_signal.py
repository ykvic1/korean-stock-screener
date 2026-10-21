"""Research-only sector-relative strength change."""


def early_rotation_signal(market, sector, dates):
    if not market or not sector or len(dates)!=61:
        return None
    if any(d not in market or d not in sector for d in dates):
        return None
    recent=100*((sector[dates[-1]]/sector[dates[-21]])/(market[dates[-1]]/market[dates[-21]])-1)
    prior=50*((sector[dates[-21]]/sector[dates[0]])/(market[dates[-21]]/market[dates[0]])-1)
    total=100*((sector[dates[-1]]/sector[dates[0]])/(market[dates[-1]]/market[dates[0]])-1)
    speed=recent-prior
    value=min(1,speed/5)*max(0,1-recent/15) if speed>0 and 0<recent<15 and total<25 else 0
    return {'strength':round(max(0,value),6),'recent20':round(recent,6)}
