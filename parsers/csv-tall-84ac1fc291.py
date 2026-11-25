import io, csv, re

def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    s = s.replace('$', '').replace(',', '').replace('%', '').strip()
    # remove internal spaces that are thousand separators, but keep failure otherwise
    s2 = s.replace(' ', '')
    try:
        return float(s2)
    except Exception:
        return None

def _s(v):
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None

def parse(fp):
    # Try to detect JSON by peeking first non-whitespace byte if possible
    # Binary file may support peek via buffered reader; fall back to CSV
    is_json = False
    try:
        pos = fp.tell()
        chunk = fp.read(2048)
        # we peeked, rewind
        try:
            fp.seek(pos)
        except Exception:
            pass
        stripped = chunk.lstrip(b'\xef\xbb\xbf \t\r\n') if isinstance(chunk, (bytes, bytearray)) else str(chunk).lstrip()
        if isinstance(stripped, (bytes, bytearray)):
            if stripped[:1] in (b'{', b'['):
                is_json = True
        else:
            if stripped[:1] in ('{', '['):
                is_json = True
    except Exception:
        is_json = False

    if is_json:
        import ijson
        yield from _parse_json(fp)
        return

    text = io.TextIOWrapper(fp, encoding='utf-8-sig', newline='', errors='replace')
    reader = csv.reader(text)
    header = None
    header_idx = None
    # Locate header row by content
    for row in reader:
        if not row:
            continue
        low = [ (c or '').strip().lower() for c in row ]
        # header contains description and billing_class or standard_charge
        if 'description' in low and (any('standard_charge' in c for c in low) or 'billing_class' in low):
            header = low
            header_idx = {name: i for i, name in enumerate(low)}
            break
        # also generic: contains billing_class and code
        if 'billing_class' in low and 'description' in low:
            header = low
            header_idx = {name: i for i, name in enumerate(low)}
            break
    if header is None:
        return

    def get(row, *names):
        for n in names:
            i = header_idx.get(n)
            if i is not None and i < len(row):
                return row[i]
        return None

    # Determine code slots
    # patterns: code|1 , code|1|type , code_1, code1 etc
    code_slots = []  # list of (code_idx, type_idx)
    # Build regex maps
    for name, i in header_idx.items():
        m = re.match(r'^code[|_\-]?(\d+)$', name)
        if m:
            num = m.group(1)
            type_name = None
            for cand in (f'code|{num}|type', f'code_{num}|type', f'code{num}|type', f'code|{num}_type', f'code_{num}_type', f'code{num}type', f'code_type_{num}', f'code|type|{num}'):
                if cand in header_idx:
                    type_name = cand
                    break
            if type_name is None:
                # search any name containing num and type
                for n2 in header_idx:
                    if 'type' in n2 and num in n2 and 'code' in n2:
                        type_name = n2
                        break
            code_slots.append((i, header_idx.get(type_name) if type_name else None, int(num)))
    code_slots.sort(key=lambda x: x[2])
    has_single_code = False
    if not code_slots:
        # single code / code_type columns
        ci = header_idx.get('code')
        ct = header_idx.get('code_type') or header_idx.get('code|type')
        if ci is not None:
            code_slots = [(ci, ct, 1)]
            has_single_code = True

    # Wide detection: standard_charge|<payer>|<plan>|<field>
    wide_cols = []  # (idx, payer, plan, field)
    for name, i in header_idx.items():
        parts = name.split('|')
        if len(parts) >= 4 and parts[0] == 'standard_charge' and parts[3] in ('negotiated_dollar','negotiated_percentage','negotiated_algorithm','methodology','min','max'):
            wide_cols.append((i, parts[1], parts[2], parts[3]))

    is_wide = bool(wide_cols)

    for row in reader:
        if not row or all(not (c or '').strip() for c in row):
            continue
        # pad row
        if len(row) < len(header):
            row = row + [''] * (len(header) - len(row))

        description = _s(get(row, 'description'))
        codes = []
        for ci, ti, _n in code_slots:
            code_val = _s(row[ci] if ci < len(row) else None)
            if not code_val:
                continue
            typ = _s(row[ti] if ti is not None and ti < len(row) else None)
            codes.append({'code': code_val, 'type': typ})
        setting = _s(get(row, 'setting'))
        billing_class = _s(get(row, 'billing_class'))
        modifiers = _s(get(row, 'modifiers'))
        gross = _num(get(row, 'standard_charge|gross', 'standard_charge_gross', 'gross'))
        cash = _num(get(row, 'standard_charge|discounted_cash', 'discounted_cash', 'standard_charge|discounted_cash_price'))
        mn = _num(get(row, 'standard_charge|min', 'standard_charge|minimum', 'min'))
        mx = _num(get(row, 'standard_charge|max', 'standard_charge|maximum', 'max'))
        methodology = _s(get(row, 'standard_charge|methodology', 'methodology'))
        est = get(row, 'estimated_amount', 'standard_charge|estimated_amount')
        if est is None or (isinstance(est, str) and not est.strip()):
            est = get(row, 'median_amount', 'standard_charge|median', 'standard_charge|median_amount')
        estimated = _num(est)

        if is_wide:
            # group by payer/plan
            groups = {}
            order = []
            for idx, payer, plan, field in wide_cols:
                key = (payer, plan)
                if key not in groups:
                    groups[key] = {}
                    order.append(key)
                groups[key][field] = row[idx] if idx < len(row) else None
            yielded = False
            for payer, plan in order:
                g = groups[(payer, plan)]
                if not any((v or '').strip() if isinstance(v, str) else v is not None for v in g.values()):
                    continue
                yielded = True
                yield {
                    'description': description,
                    'codes': codes,
                    'setting': setting,
                    'billing_class': billing_class,
                    'modifiers': modifiers,
                    'gross': gross,
                    'cash': cash,
                    'min': _num(g.get('min')) if 'min' in g else mn,
                    'max': _num(g.get('max')) if 'max' in g else mx,
                    'payer_name': _s(payer),
                    'plan_name': _s(plan),
                    'negotiated_dollar': _num(g.get('negotiated_dollar')),
                    'negotiated_percentage': _num(g.get('negotiated_percentage')),
                    'negotiated_algorithm': _s(g.get('negotiated_algorithm')),
                    'estimated_amount': estimated,
                    'methodology': _s(g.get('methodology')) or methodology,
                }
            if not yielded:
                yield {
                    'description': description,
                    'codes': codes,
                    'setting': setting,
                    'billing_class': billing_class,
                    'modifiers': modifiers,
                    'gross': gross,
                    'cash': cash,
                    'min': mn,
                    'max': mx,
                    'payer_name': None,
                    'plan_name': None,
                    'negotiated_dollar': None,
                    'negotiated_percentage': None,
                    'negotiated_algorithm': None,
                    'estimated_amount': estimated,
                    'methodology': methodology,
                }
            continue

        payer_name = _s(get(row, 'payer_name'))
        plan_name = _s(get(row, 'plan_name'))
        neg_d = _num(get(row, 'standard_charge|negotiated_dollar', 'negotiated_dollar'))
        neg_p = _num(get(row, 'standard_charge|negotiated_percentage', 'negotiated_percentage'))
        neg_a = _s(get(row, 'standard_charge|negotiated_algorithm', 'negotiated_algorithm'))

        yield {
            'description': description,
            'codes': codes,
            'setting': setting,
            'billing_class': billing_class,
            'modifiers': modifiers,
            'gross': gross,
            'cash': cash,
            'min': mn,
            'max': mx,
            'payer_name': payer_name,
            'plan_name': plan_name,
            'negotiated_dollar': neg_d,
            'negotiated_percentage': neg_p,
            'negotiated_algorithm': neg_a,
            'estimated_amount': estimated,
            'methodology': methodology,
        }

def _parse_json(fp):
    import ijson
    # Try standard CMS structure
    try:
        items = ijson.items(fp, 'standard_charge_information.item')
    except Exception:
        return
    for it in items:
        codes = []
        # code_information
        ci = it.get('code_information') if isinstance(it, dict) else None
        if isinstance(ci, list):
            for c in ci:
                if isinstance(c, dict):
                    code = c.get('code') or c.get('Code')
                    typ = c.get('type') or c.get('Type')
                    if code:
                        codes.append({'code': str(code), 'type': str(typ) if typ else None})
        elif isinstance(ci, dict):
            code = ci.get('code')
            if code:
                codes.append({'code': str(code), 'type': ci.get('type')})
        else:
            # flat code/type fields
            for k, v in (it.items() if isinstance(it, dict) else []):
                if v:
                    pass
        setting = None
        if isinstance(it, dict):
            setting = it.get('setting') or it.get('Setting')
        # payer offers
        offers = it.get('payer_offers') if isinstance(it, dict) else None
        base = {
            'description': _s(it.get('description')) if isinstance(it, dict) else None,
            'codes': codes,
            'setting': _s(setting),
            'billing_class': _s(it.get('billing_class')) if isinstance(it, dict) else None,
            'modifiers': _s(it.get('modifiers')) if isinstance(it, dict) else None,
            'gross': _num(it.get('standard_charge|gross') if isinstance(it, dict) else None),
            'cash': _num(it.get('standard_charge|discounted_cash') if isinstance(it, dict) else None),
            'min': _num(it.get('standard_charge|min') if isinstance(it, dict) else None),
            'max': _num(it.get('standard_charge|max') if isinstance(it, dict) else None),
            'methodology': _s(it.get('standard_charge|methodology')) if isinstance(it, dict) else None,
            'estimated_amount': _num(it.get('estimated_amount') or it.get('median_amount')) if isinstance(it, dict) else None,
        }
        if not offers:
            yield {**base, 'payer_name': None, 'plan_name': None, 'negotiated_dollar': None, 'negotiated_percentage': None, 'negotiated_algorithm': None}
        else:
            for off in offers:
                if not isinstance(off, dict):
                    continue
                pname = off.get('payer_name')
                plans = off.get('plan_offers') or [off]
                for pl in plans:
                    if not isinstance(pl, dict):
                        continue
                    yield {
                        **base,
                        'payer_name': _s(pname),
                        'plan_name': _s(pl.get('plan_name')),
                        'negotiated_dollar': _num(pl.get('standard_charge|negotiated_dollar') or pl.get('negotiated_dollar')),
                        'negotiated_percentage': _num(pl.get('standard_charge|negotiated_percentage') or pl.get('negotiated_percentage')),
                        'negotiated_algorithm': _s(pl.get('standard_charge|negotiated_algorithm') or pl.get('negotiated_algorithm')),
                        'estimated_amount': _num(pl.get('estimated_amount') or base['estimated_amount']),
                        'methodology': _s(pl.get('standard_charge|methodology')) or base['methodology'],
                    }
