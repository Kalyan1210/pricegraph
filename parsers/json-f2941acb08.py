import ijson, numbers
from decimal import Decimal

def _num(v):
    if v is None: return None
    if isinstance(v, numbers.Number): return float(v)
    if isinstance(v, str):
        s=v.strip().replace("$","").replace(",","").replace("%","")
        if not s: return None
        try: return float(s)
        except Exception: return None
    return None

def _str(v):
    if v is None: return None
    if isinstance(v,str):
        s=v.strip(); return s if s else None
    if isinstance(v,(list,tuple)):
        parts=[_str(x) for x in v]; parts=[p for p in parts if p]
        return parts[0] if len(parts)==1 else ("/".join(parts) if parts else None)
    return str(v)

def _get(d,*keys):
    if not isinstance(d,dict): return None
    lower={str(k).lower():v for k,v in d.items()}
    for key in keys:
        if key.lower() in lower: return lower[key.lower()]
    return None

def _codes(item):
    codes=[]
    ci=_get(item,"code_information","codes","billing_codes")
    if isinstance(ci,list):
        for e in ci:
            if isinstance(e,dict):
                code=_get(e,"code","billing_code"); typ=_get(e,"type","code_type")
                if code is not None and str(code).strip(): codes.append({"code":str(code).strip(),"type":_str(typ) or ""})
            elif e is not None and str(e).strip(): codes.append({"code":str(e).strip(),"type":""})
    elif isinstance(ci,dict):
        code=_get(ci,"code"); typ=_get(ci,"type")
        if code: codes.append({"code":str(code).strip(),"type":_str(typ) or ""})
    if not codes:
        code=_get(item,"code","billing_code"); typ=_get(item,"type","code_type")
        if code is not None and str(code).strip(): codes.append({"code":str(code).strip(),"type":_str(typ) or ""})
    return codes

def parse(fp):
    try:
        items=ijson.items(fp,"standard_charge_information.item")
        for item in items:
            try:
                if not isinstance(item,dict): continue
                description=_str(_get(item,"description"))
                setting=_str(_get(item,"setting")); setting=setting.lower() if setting else None
                modifiers_raw=_get(item,"modifiers","modifier")
                if isinstance(modifiers_raw,list):
                    parts=[]
                    for m in modifiers_raw:
                        parts.append(_str(_get(m,"modifier","code","description")) if isinstance(m,dict) else _str(m))
                    modifiers=",".join([p for p in parts if p]) or None
                else:
                    modifiers=_str(modifiers_raw)
                codes=_codes(item)
                charges=_get(item,"standard_charges","standard_charge","charges")
                if not isinstance(charges,list) or not charges: charges=[item]
                for sc in charges:
                    if not isinstance(sc,dict): continue
                    use_codes=codes or _codes(sc)
                    gross=_num(_get(sc,"gross_charge","gross"))
                    cash=_num(_get(sc,"discounted_cash","discounted_cash_price"))
                    mn=_num(_get(sc,"minimum","minimum_charge","min"))
                    mx=_num(_get(sc,"maximum","maximum_charge","max"))
                    methodology=_str(_get(sc,"methodology"))
                    billing_class=_str(_get(sc,"billing_class")) or _str(_get(item,"billing_class"))
                    est_sc=_num(_get(sc,"estimated_amount","median_amount"))
                    setting_sc=_str(_get(sc,"setting")) or setting
                    if setting_sc: setting_sc=setting_sc.lower()
                    desc_sc=_str(_get(sc,"description")) or description
                    payers=_get(sc,"payers_information","payer_information","payers")
                    if isinstance(payers,dict): payers=[payers]
                    if not isinstance(payers,list) or not payers:
                        yield {"description":desc_sc,"codes":list(use_codes),"setting":setting_sc,"billing_class":billing_class,"modifiers":modifiers,"gross":gross,"cash":cash,"min":mn,"max":mx,"payer_name":None,"plan_name":None,"negotiated_dollar":None,"negotiated_percentage":None,"negotiated_algorithm":None,"estimated_amount":est_sc,"methodology":methodology}
                    else:
                        for p in payers:
                            if not isinstance(p,dict): continue
                            payer_name=_str(_get(p,"payer_name","payer"))
                            plan_name=_str(_get(p,"plan_name","plan"))
                            neg_dollar=_num(_get(p,"standard_charge_dollar","negotiated_dollar","negotiated_dollar_amount"))
                            neg_pct=_num(_get(p,"standard_charge_percentage","negotiated_percentage"))
                            neg_algo=_str(_get(p,"standard_charge_algorithm","negotiated_algorithm","algorithm"))
                            est=_num(_get(p,"estimated_amount","median_amount"))
                            if est is None: est=est_sc
                            g=gross if gross is not None else _num(_get(p,"gross_charge","gross"))
                            csh=cash if cash is not None else _num(_get(p,"discounted_cash"))
                            mi=mn if mn is not None else _num(_get(p,"minimum"))
                            ma=mx if mx is not None else _num(_get(p,"maximum"))
                            meth=methodology or _str(_get(p,"methodology"))
                            yield {"description":desc_sc,"codes":list(use_codes),"setting":setting_sc,"billing_class":billing_class,"modifiers":modifiers,"gross":g,"cash":csh,"min":mi,"max":ma,"payer_name":payer_name,"plan_name":plan_name,"negotiated_dollar":neg_dollar,"negotiated_percentage":neg_pct,"negotiated_algorithm":neg_algo,"estimated_amount":est,"methodology":meth}
            except Exception:
                continue
    except Exception:
        return
