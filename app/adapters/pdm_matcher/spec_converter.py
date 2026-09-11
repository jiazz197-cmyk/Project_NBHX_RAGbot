"""
Spec sheet OCR params → component query params converter.

Translates raw OCR-extracted spec sheet parameters into the normalized
(type, model, attr) format expected by the matching engine, following
the conversion rules from 转换规则.xlsx.

Supports two OCR formats:
- PSM 11 (sparse text): simple key-value layout
- PSM 4 (table/column): form-style layout with Other column and Remarks
"""

import re


def parse_spec_sheet(text: str) -> dict:
    """Parse OCR spec sheet text into a structured dict of raw parameters."""
    params = {}

    # Model — handle OCR variations: "IADW-A-", "AADW-A-", "ADW-A-"
    m = re.search(r'Model\s+[IA]?ADW-A-(\d{4}[A-Z]*)', text)
    if not m:
        # PSM 11 may split across lines: "Model\nAADW-A-0314S"
        m = re.search(r'Model\s*\n\s*[IA]?ADW-A-(\d{4}[A-Z]*)', text)
    if m:
        params["model_full"] = "ADW-A-" + m.group(1)
        params["model_num"] = m.group(1)

    # Surface
    m = re.search(r'Surface\s+(Flat|Emboss|Dimple|Water\s*[Dd]imple|Rice\s*[Dd]imple)', text)
    if m:
        val = m.group(1).strip()
        if val.lower().startswith("flat"):
            params["surface"] = "flat"
        elif val.lower().startswith("emboss"):
            params["surface"] = "dimple"

    # Common bed (机架材质)
    m = re.search(r'Common bed\s+(.+?)(?:\n|$)', text)
    if m:
        raw = m.group(1).strip()
        if "SS" in raw.upper() or "Painted" in raw:
            if "SUS" in raw.upper():
                params["material"] = "sus"
            else:
                params["material"] = "carbon_steel"

    # Degree
    m = re.search(r'Degree\s+(\d+)[-\s]*degree', text)
    if not m:
        m = re.search(r'Collating chute\s+(\d+)[-\s]*degree', text)
    if m:
        params["degree"] = m.group(1)

    # End-user country — handle OCR errors: "Hindi"→"India", split "In\ndia"
    # Enhanced to capture multi-word names (e.g. "South Korea", "Hong Kong")
    # and map to regions for country/region scoring in engine.py.
    m = re.search(r'End-user country\s+([^\n]+)', text)
    if m:
        # Take first word from the captured line (handles "India", "Hae Selup/...", etc.)
        raw_line = m.group(1).strip()
        country = raw_line.split()[0].strip().lower() if raw_line else ""
        # Fix common OCR misreads
        if country in ("hindi", "hindia", "hae"):
            country = "india"
        elif country in ("dia", "inda", "ind"):
            country = "india"

        # European countries → "europe" (has "欧洲"/"MID" keywords in CN)
        _EUROPE = {"france", "germany", "spain", "sweden", "belgium", "russia",
                    "turkey", "italy", "uk", "poland", "netherlands", "portugal",
                    "greece", "romania", "czech", "hungary", "austria", "switzerland",
                    "denmark", "norway", "finland", "ireland", "bulgaria", "croatia",
                    "slovakia", "slovenia", "lithuania", "latvia", "estonia",
                    "serbia", "ukraine", "united kingdom"}

        if country == "india":
            params["end_user_country"] = "india"
        elif country == "china":
            params["end_user_country"] = "domestic"
        elif country in ("korea", "south korea"):
            params["end_user_country"] = "korea"
        elif country in _EUROPE:
            params["end_user_country"] = "europe"
        else:
            params["end_user_country"] = "export"

    # Regulation
    m = re.search(r'Regulation\s+(.+?)(?:\n|$)', text)
    if m:
        raw = m.group(1).strip()
        if "W&M" in raw or "W&M" in raw:
            params["regulation"] = "india_wm"
        elif "TNA" in raw:
            params["regulation"] = "tna"
        elif "CE" in raw:
            params["regulation"] = "ce"
        elif "UL" in raw:
            params["regulation"] = "ul"

    # Cable length
    m = re.search(r'Cable length\s+(\d+)m', text)
    if m:
        params["cable_length"] = m.group(1) + "m"

    # Name plate
    m = re.search(r'Name plate\s+[S$]?B(\d+[A-Z]?\d*)', text)
    if m:
        params["name_plate"] = "SB" + m.group(1)

    # Linear feeder pan
    m = re.search(r'Linear feeder pan\s+(\w+)', text)
    if m:
        params["linear_feeder_pan"] = m.group(1).strip()

    # LFP lip — handle OCR errors: "LFP Sip", "LFP lip", "LFP Lip"
    m = re.search(r'LFP\s+[lS]ip\s+(.+?)(?:\n|$)', text)
    if m:
        raw = m.group(1).strip().lower()
        # Fix OCR: "Fiat" → "Flat"
        raw = raw.replace("fiat", "flat")
        if "flat" in raw:
            params["lfp_lip"] = "flat_lip"
        elif "bent" in raw:
            params["lfp_lip"] = "lips"

    # Feed bucket / FB gate / FB spring — multiline aware
    m = re.search(r'Feed bucket\s+(.+?)(?:\n|$)', text)
    if m:
        params["feed_bucket"] = m.group(1).strip()

    m = re.search(r'FB gate\s+(.+?)(?:\n|$)', text)
    if m:
        raw = m.group(1).strip().lower()
        if "single" in raw:
            params["fb_gate"] = "single"
        elif "double" in raw:
            params["fb_gate"] = "double"

    m = re.search(r'FB spring\s+(.+?)(?:\n|$)', text)
    if m:
        raw = m.group(1).strip().lower()
        if "yes" in raw:
            params["fb_spring"] = "yes"
        elif "no" in raw:
            params["fb_spring"] = "no"

    # Weigh bucket / WB gate / WB spring
    m = re.search(r'Weigh bucket\s+(.+?)(?:\n|$)', text)
    if m:
        params["weigh_bucket"] = m.group(1).strip()

    m = re.search(r'WB gate\s+(.+?)(?:\n|$)', text)
    if m:
        raw = m.group(1).strip().lower()
        if "single" in raw:
            params["wb_gate"] = "single"
        elif "double" in raw:
            params["wb_gate"] = "double"

    m = re.search(r'WB spring\s+(.+?)(?:\n|$)', text)
    if m:
        raw = m.group(1).strip().lower()
        if "yes" in raw:
            params["wb_spring"] = "yes"
        elif "no" in raw:
            params["wb_spring"] = "no"

    # Collection bucket (集合斗容量和方向) — multiline aware:
    # Captures content until next numbered item or section header
    m = re.search(r'Collection bucket\s+(.+?)(?=\n\d+\||\n(?:Degree|C-C|CF|Product|CB|Enclosure|Detergent|Common|Cable|Software|Regulation|Name|Optional|Display|Printer|Operation|Remarks)\b|\n\s*\n)', text, re.DOTALL)
    if m:
        raw = m.group(1).strip()
        cap_m = re.search(r'(\d+)L', raw)
        if cap_m:
            params["collection_bucket_capacity"] = cap_m.group(1) + "L"
        if "1-way" in raw:
            params["collection_direction"] = "single"
        elif "2-way" in raw:
            params["collection_direction"] = "double"

    # Collating chute — multiline aware
    m = re.search(r'Collating chute\s+(.+?)(?=\n\d+\||\n(?:Degree|C-C|CF|CC|Collating|Product|Collection|CB|Enclosure|Detergent|Common|Cable|Software|Regulation|Name|Optional|Display|Printer|Operation|Remarks)\b|\n\s*\n)', text, re.DOTALL)
    if m:
        raw = m.group(1).strip()
        if "degree" in raw:
            deg = re.search(r'(\d+)', raw)
            if deg:
                params["collating_degree"] = deg.group(1)
        if re.search(r'(?<!no\s)fork|分叉', raw, re.IGNORECASE) and "no fork" not in raw.lower():
            params["collating_chute"] = "fork"
        elif "collection" in raw.lower() or "集合" in raw:
            params["collating_chute"] = "collection"

    # CC baffles
    m = re.search(r'CC baffles\s+(.+?)(?:\n|$)', text)
    if m:
        raw = m.group(1).strip().lower()
        params["cc_baffles"] = "yes" if "yes" in raw else "no"

    # CF baffles
    m = re.search(r'CF baffles\s+(.+?)(?:\n|$)', text)
    if m:
        raw = m.group(1).strip().lower()
        params["cf_baffles"] = "yes" if "yes" in raw else "no"

    # Collating funnel
    m = re.search(r'Collating funnel\s+(.+?)(?:\n|$)', text)
    if m:
        params["collating_funnel"] = m.group(1).strip()

    # Detergent
    m = re.search(r'Detergent\s+(.+?)(?:\n|$)', text)
    if m:
        raw = m.group(1).strip().lower()
        params["detergent"] = "yes" if "yes" in raw else "no"

    # Leak-proof — explicit field (handles "No", "Yes", "A type", "B type")
    m = re.search(r'Leak[-\s]*proof\s+(Yes|No|A\s*type|B\s*type)', text, re.IGNORECASE)
    if m:
        val = m.group(1).strip()
        vl = val.lower()
        if "no" in vl:
            params["leak_proof"] = "no"
        elif "b" in vl:
            params["leak_proof"] = "B"
        elif "a" in vl:
            params["leak_proof"] = "A"
        elif "yes" in vl:
            params["leak_proof"] = "yes"

    # Center vibrator
    m = re.search(r'Center vibrator\s+(.+?)(?:\n|$)', text)
    if m:
        params["center_vibrator"] = m.group(1).strip()

    # Top cone
    m = re.search(r'Top cone\s+(.+?)(?:\n|$)', text)
    if m:
        params["top_cone"] = m.group(1).strip()

    # Infeed funnel
    m = re.search(r'Infeed funnel\s+(.+?)(?:\n|$)', text)
    if m:
        params["infeed_funnel"] = m.group(1).strip()

    # Product stopper
    m = re.search(r'Product stopper\s+(.+?)(?:\n|$)', text)
    if m:
        params["product_stopper"] = m.group(1).strip()

    # ---- Second pass: extract Other column and Remarks ----
    other_params = _parse_other_column(text)
    remarks_params = _parse_remarks(text)

    # Merge: only fill in params that are missing or "No" in main parse
    for k, v in {**other_params, **remarks_params}.items():
        if k not in params or params[k] is None:
            params[k] = v

    # ---- 2.5 pass: extract Surface change / Leak-proof / Other sub-columns ----
    # DotsOCR outputs tab-separated table rows where each parameter row may
    # have additional columns beyond the main value:
    #   [#] [ParamName] [Value] [SurfaceChange] [LeakProof] [Urethane] [Other]
    # These per-row supplements override/refine the global parameter values.
    row_supps = _parse_row_columns(text)
    params["_row_supplements"] = row_supps

    # ---- Third pass: extract supplementary params from noisy OCR fields ----
    supp = _extract_supplementary_params(params, text)
    for k, v in supp.items():
        if k not in params or params[k] is None:
            params[k] = v

    # ---- Fourth pass: detect application from full text ----
    # "sprout" / "bean sprout" / "もやし" indicates vegetable scale application.
    # The engine maps sprout→蔬菜 and boosts candidates with "蔬菜" in CHINANAME.
    if "application" not in params or not params["application"]:
        text_lower = text.lower()
        if re.search(r'\bsprout\b|bean\s*sprout|もやし|芽菜|豆芽', text_lower):
            params["application"] = "sprout"

    return params


def _parse_row_columns(text: str) -> dict:
    """Extract Surface-change / Leak-proof / Other sub-columns from DotsOCR output.

    DotsOCR renders the spec table with 4 right-side columns after the main value:
      Surface change | Leak-proof | Urethane insert | Other

    This function scans tab-separated rows for these trailing column values,
    keyed by the parameter name (e.g. 'Feed bucket', 'Top cone').

    Returns dict of param_name → {surface, leak_proof, other_keywords}
    where 'other_keywords' is a list of tokens like ['Silo', 'PIM'].
    """
    supplements = {}

    # Parameter names that can have sub-column data (numbered rows in the spec)
    # Maps the spec row name to the internal param key
    PARAM_ROW_NAMES = [
        ("Surface", "surface"),
        ("Infeed funnel", "infeed_funnel"),
        ("Infeed ring", "infeed_ring"),
        ("Top cone", "top_cone"),
        ("Center vibrator", "center_vibrator"),
        ("Linear feeder pan", "linear_feeder_pan"),
        ("LFP lip", "lfp_lip"),
        ("Feed bucket", "feed_bucket"),
        ("FB gate", "fb_gate"),
        ("FB spring", "fb_spring"),
        ("Weigh bucket", "weigh_bucket"),
        ("WB gate", "wb_gate"),
        ("WB spring", "wb_spring"),
        ("Collating chute", "collating_chute"),
        ("CC baffles", "cc_baffles"),
        ("Collating funnel", "collating_funnel"),
        ("CF baffles", "cf_baffles"),
        ("CF L-shaped bracket", "cf_l_bracket"),
        ("Product stopper", "product_stopper"),
        ("Collection bucket", "collection_bucket"),
        ("CB gate", "cb_gate"),
        ("Enclosure", "enclosure"),
        ("Detergent", "detergent"),
        ("Common bed", "common_bed"),
        ("Cable length", "cable_length"),
        ("Name plate", "name_plate"),
        ("Regulation", "regulation"),
    ]

    # Build a mapping from display name → internal key
    name_map = {display: internal for display, internal in PARAM_ROW_NAMES}

    # Scan text for tab-separated rows that have sub-columns
    for line in text.split('\n'):
        if '\t' not in line:
            continue
        cells = [c.strip() for c in line.split('\t') if c.strip()]
        if len(cells) < 3:
            continue

        # Try to match the first or second cell to a known param name
        param_key = None
        value_cell_idx = -1
        for i, cell in enumerate(cells):
            # Strip leading numbers like "2 ", "12 "
            cleaned = re.sub(r'^\d+\s*', '', cell).strip()
            if cleaned in name_map:
                param_key = name_map[cleaned]
                value_cell_idx = i + 1  # next cell is main value
                break
            # Also try the raw cell (DotsOCR may keep number+name together)
            raw_cleaned = re.sub(r'^\d+', '', cell).strip()
            if raw_cleaned in name_map:
                param_key = name_map[raw_cleaned]
                value_cell_idx = i
                break

        if param_key is None:
            continue

        # Extract remaining cells after the value as sub-columns
        # Pattern: [Name] [Value] [SurfaceChange?] [LeakProof?] [Urethane?] [Other?]
        sub_cells = cells[value_cell_idx + 1:] if value_cell_idx + 1 < len(cells) else []

        supp = {"_surface_change": "", "_leak_proof_col": "", "_other_tokens": []}

        for sc in sub_cells:
            sc_lower = sc.lower()
            # Surface change column
            if sc_lower in ("flat", "emboss", "dimple"):
                supp["_surface_change"] = sc_lower
            # Leak-proof column
            elif sc_lower in ("yes", "b type", "a type", "ingress-proof",
                             "ingress proof", "btype", "atype"):
                supp["_leak_proof_col"] = sc_lower
            # Urethane insert / Other columns — collect as keywords
            elif len(sc) > 2 and sc_lower not in ("no", "↑", "↔", "←"):
                # Split comma-separated tokens like "Silo type, PIM compliant"
                for part in re.split(r'[,;，；]\s*', sc):
                    part = part.strip()
                    if part and len(part) > 1:
                        supp["_other_tokens"].append(part)

        if supp["_surface_change"] or supp["_leak_proof_col"] or supp["_other_tokens"]:
            supplements[param_key] = supp

    return supplements


def _parse_other_column(text: str) -> dict:
    """Extract supplementary parameters from the 'Other' column in form-style specs.

    The Other column appears after 'Surface change ... Other' header line.
    Content from this column supplements parameters whose main value is 'No' or empty.
    """
    params = {}

    # Find the Other column section
    other_start = None
    for m in re.finditer(r'Other\s*\n', text):
        other_start = m.end()
        break

    if other_start is None:
        return params

    # Get text from Other header to Remarks/end
    other_text = text[other_start:]
    end_m = re.search(r'\n\s*(?:34\||Remarks)', other_text)
    if end_m:
        other_text = other_text[:end_m.start()]

    if not other_text.strip():
        return params

    # Try to parse numbered items from Other column
    # Format: number|name value or number/name value
    for m in re.finditer(r'(\d+)\s*[\\|/]\s*([A-Za-z][^(]*?)\s+((?:Flat|Emboss|Dimple|Single|Double|Yes|No|SN|[A-Z][a-z]+\s+lip|\d+[-\s]*degree|\d+L|\d+m|SB\d+[A-Z]?\d*|AC\d+V|\d+\s*Hz)[^|\n]*)', other_text):
        num = m.group(1)
        name = m.group(2).strip()
        value = m.group(3).strip()
        _apply_other_param(params, num, name, value)

    return params


def _apply_other_param(params: dict, num: str, name: str, value: str):
    """Map an Other-column parameter to the standard params dict."""
    name_lower = name.lower()
    value_lower = value.lower()

    if "power supply" in name_lower and "hz" in name_lower:
        m = re.search(r'(\d+)\s*Hz', value, re.IGNORECASE)
        if m:
            params["power_hz"] = m.group(1)
    elif "surface" in name_lower:
        if "flat" in value_lower:
            params["surface"] = "flat"
        elif "emboss" in value_lower or "dimple" in value_lower:
            params["surface"] = "dimple"
    elif "lfp lip" in name_lower:
        if "flat" in value_lower:
            params["lfp_lip"] = "flat_lip"
        elif "bent" in value_lower:
            params["lfp_lip"] = "lips"
    elif "fb gate" in name_lower:
        if "single" in value_lower:
            params["fb_gate"] = "single"
        elif "double" in value_lower:
            params["fb_gate"] = "double"
    elif "wb gate" in name_lower:
        if "single" in value_lower:
            params["wb_gate"] = "single"
        elif "double" in value_lower:
            params["wb_gate"] = "double"
    elif "fb spring" in name_lower:
        params["fb_spring"] = "yes" if "yes" in value_lower else "no"
    elif "wb spring" in name_lower:
        params["wb_spring"] = "yes" if "yes" in value_lower else "no"
    elif "cable length" in name_lower:
        m = re.search(r'(\d+)m', value)
        if m:
            params["cable_length"] = m.group(1) + "m"
    elif "collection bucket" in name_lower:
        cap_m = re.search(r'(\d+)L', value)
        if cap_m:
            params["collection_bucket_capacity"] = cap_m.group(1) + "L"
        if "1-way" in value:
            params["collection_direction"] = "single"
        elif "2-way" in value:
            params["collection_direction"] = "double"
    elif "degree" in name_lower:
        m = re.search(r'(\d+)', value)
        if m:
            params["degree"] = m.group(1)
    elif "name plate" in name_lower:
        m = re.search(r'[S$]?B(\d+[A-Z]?\d*)', value)
        if m:
            params["name_plate"] = "SB" + m.group(1)
    elif "regulation" in name_lower:
        if "w&m" in value_lower:
            params["regulation"] = "india_wm"
        elif "tna" in value_lower:
            params["regulation"] = "tna"
        elif "ce" in value_lower:
            params["regulation"] = "ce"
        elif "ul" in value_lower:
            params["regulation"] = "ul"
    elif "common bed" in name_lower:
        if "ss" in value_lower or "painted" in value_lower:
            params["material"] = "sus" if "sus" in value_lower else "carbon_steel"
    elif "detergent" in name_lower:
        params["detergent"] = "yes" if "yes" in value_lower else "no"
    elif "cc baffles" in name_lower:
        params["cc_baffles"] = "yes" if "yes" in value_lower else "no"
    elif "cf baffles" in name_lower:
        params["cf_baffles"] = "yes" if "yes" in value_lower else "no"


def _parse_remarks(text: str) -> dict:
    """Extract supplementary parameters from the Remarks section."""
    params = {}

    m = re.search(r'Remarks\s*:\s*\n(.+?)(?:\n\s*\n|\n\s*Ver\.|\Z)', text, re.DOTALL | re.IGNORECASE)
    if not m:
        return params

    remarks_text = m.group(1).strip()
    if not remarks_text or remarks_text.startswith("Ver."):
        return params

    # Parse key-value patterns from remarks
    for line in remarks_text.split('\n'):
        line = line.strip()
        if not line:
            continue
        # Try "key: value" or "key = value" or "key value" patterns
        for sep in [':', '=', '：']:
            if sep in line:
                key, val = line.split(sep, 1)
                key = key.strip().lower()
                val = val.strip()
                _apply_remark_param(params, key, val)
                break

    return params


def _apply_remark_param(params: dict, key: str, value: str):
    """Map a Remarks parameter to the standard params dict."""
    value_lower = value.lower()

    if "surface" in key:
        if "flat" in value_lower:
            params["surface"] = "flat"
        elif "dimple" in value_lower or "emboss" in value_lower:
            params["surface"] = "dimple"
    elif "degree" in key:
        m = re.search(r'(\d+)', value)
        if m:
            params["degree"] = m.group(1)
    elif "cable" in key and "length" in key:
        m = re.search(r'(\d+)m', value)
        if m:
            params["cable_length"] = m.group(1) + "m"
    elif "regulation" in key:
        if "w&m" in value_lower:
            params["regulation"] = "india_wm"
        elif "tna" in value_lower:
            params["regulation"] = "tna"
        elif "ce" in value_lower:
            params["regulation"] = "ce"
    elif "name plate" in key:
        m = re.search(r'SB(\d+[A-Z]?\d*)', value)
        if m:
            params["name_plate"] = "SB" + m.group(1)
    elif "common bed" in key or "bed" in key:
        if "sus" in value_lower:
            params["material"] = "sus"
        elif "ss" in value_lower or "painted" in value_lower:
            params["material"] = "carbon_steel"
    elif "collection" in key and "bucket" in key:
        cap_m = re.search(r'(\d+)L', value)
        if cap_m:
            params["collection_bucket_capacity"] = cap_m.group(1) + "L"
        if "1-way" in value:
            params["collection_direction"] = "single"
        elif "2-way" in value:
            params["collection_direction"] = "double"


def _extract_supplementary_params(params: dict, text: str = "") -> dict:
    """Scan raw OCR field values for PIM, leak-proof, duck mouth, etc.

    These supplementary keywords are often embedded in noisy OCR text
    for fields like feed_bucket, weigh_bucket, infeed_funnel, etc.
    They should not override explicitly parsed structured params.
    """
    supp = {}

    # Gather raw text from fields that may contain supplementary keywords
    source_keys = [
        "feed_bucket", "weigh_bucket", "infeed_funnel",
        "collection_bucket", "collating_funnel", "top_cone",
        "center_vibrator", "product_stopper",
    ]
    raw_texts = []
    for key in source_keys:
        val = params.get(key, "")
        if val and isinstance(val, str) and val.strip():
            raw_texts.append(val)
    # Also scan Other column params that weren't matched to known fields
    for key, val in params.items():
        if key not in source_keys and isinstance(val, str) and len(val) > 5:
            # Long unmapped string values might carry supplementary info
            pass  # skip for now — Other/Remarks already handled

    full_text = " ".join(raw_texts)
    text_lower = full_text.lower()

    # ---- PIM ----
    if re.search(r'pim', text_lower):
        supp["pim"] = "yes"

    # ---- Leak proof type ----
    # In feed/weigh bucket OCR context, "B type" / "Btype" indicates
    # B-type leak-proof. The explicit "leak-proof" text is often garbled
    # by OCR, but "B type" in these fields is a domain convention.
    fb_text = " ".join([
        str(params.get("feed_bucket", "")),
        str(params.get("weigh_bucket", "")),
    ]).lower()
    if re.search(r'\bb\s*type\b|btype|b\s*防|b防|b\s*型\s*防', fb_text):
        supp["leak_proof"] = "B"
    elif re.search(r'\ba\s*type\b|atype|a\s*防|a防|a\s*型\s*防', fb_text):
        supp["leak_proof"] = "A"
    # Fallback: explicit leak-proof anywhere in supplementary text
    if "leak_proof" not in supp:
        if re.search(r'leak[-\s]*proof|防漏|防\s*漏', text_lower):
            supp["leak_proof"] = "yes"
    # If spec sheet has Leak-proof field but no type detected, it's "no"
    if "leak_proof" not in supp and "leak_proof" not in params:
        if re.search(r'Leak[-\s]*proof', text, re.IGNORECASE):
            supp["leak_proof"] = "no"

    # ---- Duck mouth ----
    if re.search(r'duck\s*mouth|鸭嘴|鸭\s*嘴', text_lower):
        supp["duck_mouth"] = "yes"

    # ---- Capacity from raw text (backup) ----
    cap_m = re.search(r'(?<!\d)(\d+)\s*L(?!\d)', full_text)
    if cap_m:
        supp["capacity"] = cap_m.group(1) + "L"

    # ---- Collection direction from raw text (backup) ----
    if re.search(r'1[-\s]way|single\s*direction|single\s*horizontal|'
                 r'单横|单\s*方\s*向|one\s*way', text_lower):
        supp["collection_direction"] = "single"
    elif re.search(r'2[-\s]way|double\s*direction|double\s*horizontal|'
                   r'双横|双\s*方\s*向|two\s*way', text_lower):
        supp["collection_direction"] = "double"

    # ---- Side stroke / side drive ----
    if re.search(r'side\s*stroke|side\s*drive|横拉|侧\s*驱|横\s*拉', text_lower):
        supp["drive_type"] = "side"

    return supp


# ---- Conversion rules for each component type ----

def _model_full(params: dict) -> str:
    return params.get("model_full", "")


def _model_short(params: dict) -> str:
    """0314S (strip ADW-A- prefix)."""
    return params.get("model_num", "")


def _model_base(params: dict) -> str:
    """0314 (strip suffix letters, keep digits only)."""
    num = params.get("model_num", "")
    return re.sub(r'[A-Z]+$', '', num)


def _model_series(params: dict) -> str:
    """03系列 or 5系列."""
    num = params.get("model_num", "")
    if num.startswith("03"):
        return "03系列"
    if num.startswith("5"):
        return "5系列"
    return num[:2] + "系列"


def _apply_row_supplement(spec: dict, params: dict, row_keys: list):
    """Apply Surface Change / Leak-proof / Other column data from spec rows.

    When DotsOCR extracts the sub-columns (Surface change, Leak-proof, Other),
    override or supplement the component spec's attr dict.
    """
    row_supps = params.get("_row_supplements", {})
    if not row_supps:
        return

    for rk in row_keys:
        supp = row_supps.get(rk, {})
        if not supp:
            continue

        # Surface Change column overrides the global surface for this component
        sc = supp.get("_surface_change", "")
        if sc == "emboss" or sc == "dimple":
            spec["attr"]["surface"] = "dimple"
        elif sc == "flat":
            spec["attr"]["surface"] = "flat"

        # Leak-proof column
        lp = supp.get("_leak_proof_col", "")
        if lp == "yes":
            spec["attr"]["leak_proof"] = "yes"
        elif lp in ("b type", "btype"):
            spec["attr"]["leak_proof"] = "B"
        elif lp in ("a type", "atype"):
            spec["attr"]["leak_proof"] = "A"
        elif lp in ("ingress-proof", "ingress proof"):
            spec["attr"]["leak_proof"] = "yes"

        # Other column tokens → supplementary keywords for scoring
        tokens = supp.get("_other_tokens", [])
        for t in tokens:
            t_lower = t.lower()
            if "pim" in t_lower:
                spec["attr"]["pim"] = "yes"
            if "silo" in t_lower:
                spec["attr"]["silo"] = "yes"
            # Regulation sub-column values
            if "india" in t_lower and "w&m" in t_lower:
                spec["attr"]["regulation"] = "india_wm"
            elif "w&m" in t_lower:
                spec["attr"]["regulation"] = "india_wm"
            elif "european mid" in t_lower or "europe mid" in t_lower:
                spec["attr"]["regulation"] = "ce"
            elif "tna" in t_lower:
                spec["attr"]["regulation"] = "tna"
            elif "ul" in t_lower:
                spec["attr"]["regulation"] = "ul"


def convert_all(params: dict) -> list:
    """Convert parsed OCR params into a list of (type, model, attr) specs
    for all known component types. Returns list of dicts with type/model/attr."""
    specs = []

    # ---- 机架 ----
    spec = {"type": "机架", "model": _model_full(params), "attr": {}}
    if "material" in params:
        spec["attr"]["material"] = params["material"]
    if "degree" in params:
        spec["attr"]["degree"] = params["degree"]
    if "surface" in params:
        spec["attr"]["surface"] = params["surface"]
    if "end_user_country" in params:
        spec["attr"]["end_user_country"] = params["end_user_country"]
    if "detergent" in params:
        spec["attr"]["detergent"] = params["detergent"]
    specs.append(spec)

    # ---- 中心柱天板密封罩 ----
    spec = {"type": "中心柱天板密封罩", "model": _model_full(params), "attr": {}}
    if "detergent" in params:
        spec["attr"]["detergent"] = params["detergent"]
    specs.append(spec)

    # ---- 供料漏斗 ----
    spec = {"type": "供料漏斗", "model": _model_full(params), "attr": {}}
    if "surface" in params:
        spec["attr"]["surface"] = params["surface"]
    if "degree" in params:
        spec["attr"]["degree"] = params["degree"]
    specs.append(spec)

    # ---- 供料锥支架 ----
    spec = {"type": "供料锥支架", "model": _model_full(params), "attr": {}}
    specs.append(spec)

    # ---- 顶锥 ----
    spec = {"type": "顶锥", "model": _model_full(params), "attr": {}}
    if "surface" in params:
        spec["attr"]["surface"] = params["surface"]
    if "degree" in params:
        spec["attr"]["degree"] = params["degree"]
    if "linear_feeder_pan" in params:
        spec["attr"]["lfp_type"] = params["linear_feeder_pan"]
    specs.append(spec)

    # ---- 振动盘 ----
    spec = {"type": "振动盘", "model": _model_full(params), "attr": {}}
    if "surface" in params:
        spec["attr"]["surface"] = params["surface"]
    if "degree" in params:
        spec["attr"]["degree"] = params["degree"]
    if "lfp_lip" in params:
        spec["attr"]["lfp_lip"] = params["lfp_lip"]
    if "linear_feeder_pan" in params:
        spec["attr"]["lfp_type"] = params["linear_feeder_pan"]
    if "leak_proof" in params:
        spec["attr"]["leak_proof"] = params["leak_proof"]
    if "pim" in params:
        spec["attr"]["pim"] = params["pim"]
    if "detergent" in params:
        spec["attr"]["detergent"] = params["detergent"]
    specs.append(spec)

    # ---- 供料斗 ----
    spec = {"type": "供料斗", "model": _model_full(params), "attr": {}}
    if "surface" in params:
        spec["attr"]["surface"] = params["surface"]
    if "degree" in params:
        spec["attr"]["degree"] = params["degree"]
    if "fb_spring" in params:
        spec["attr"]["fb_spring"] = params["fb_spring"]
    if "fb_gate" in params:
        spec["attr"]["fb_gate"] = params["fb_gate"]
    if "capacity" in params:
        spec["attr"]["capacity"] = params["capacity"]
    if "pim" in params:
        spec["attr"]["pim"] = params["pim"]
    if "leak_proof" in params:
        spec["attr"]["leak_proof"] = params["leak_proof"]
    if "detergent" in params:
        spec["attr"]["detergent"] = params["detergent"]
    specs.append(spec)

    # ---- 计量斗 ----
    spec = {"type": "计量斗", "model": _model_full(params), "attr": {}}
    if "surface" in params:
        spec["attr"]["surface"] = params["surface"]
    if "degree" in params:
        spec["attr"]["degree"] = params["degree"]
    if "wb_spring" in params:
        spec["attr"]["wb_spring"] = params["wb_spring"]
    if "wb_gate" in params:
        spec["attr"]["wb_gate"] = params["wb_gate"]
    if "capacity" in params:
        spec["attr"]["capacity"] = params["capacity"]
    if "pim" in params:
        spec["attr"]["pim"] = params["pim"]
    if "leak_proof" in params:
        spec["attr"]["leak_proof"] = params["leak_proof"]
    if "detergent" in params:
        spec["attr"]["detergent"] = params["detergent"]
    specs.append(spec)

    # ---- 溜槽 ----
    spec = {"type": "溜槽", "model": _model_full(params), "attr": {}}
    if "surface" in params:
        spec["attr"]["surface"] = params["surface"]
    if "degree" in params:
        spec["attr"]["degree"] = params["degree"]
    if "collating_chute" in params:
        spec["attr"]["collating_chute"] = params["collating_chute"]
    if "cc_baffles" in params:
        spec["attr"]["baffle"] = params["cc_baffles"]
    if "leak_proof" in params:
        spec["attr"]["leak_proof"] = params["leak_proof"]
    if "detergent" in params:
        spec["attr"]["detergent"] = params["detergent"]
    specs.append(spec)

    # ---- 收集锥 ----
    spec = {"type": "收集锥", "model": _model_full(params), "attr": {}}
    if "surface" in params:
        spec["attr"]["surface"] = params["surface"]
    if "degree" in params:
        spec["attr"]["degree"] = params["degree"]
    if "cf_baffles" in params:
        # Map cf_baffles → baffle so engine's baffle keywords (带挡板/无挡板)
        # score for 收集锥 the same way cc_baffles→baffle works for 溜槽.
        spec["attr"]["baffle"] = params["cf_baffles"]
        spec["attr"]["cf_baffles"] = params["cf_baffles"]
    if "duck_mouth" in params:
        spec["attr"]["duck_mouth"] = params["duck_mouth"]
    if "detergent" in params:
        spec["attr"]["detergent"] = params["detergent"]
    specs.append(spec)

    # ---- 集合斗 ----
    spec = {"type": "集合斗", "model": _model_full(params), "attr": {}}
    if "surface" in params:
        spec["attr"]["surface"] = params["surface"]
    if "degree" in params:
        spec["attr"]["degree"] = params["degree"]
    if "collection_bucket_capacity" in params:
        spec["attr"]["capacity"] = params["collection_bucket_capacity"]
    if "collection_direction" in params:
        spec["attr"]["collection_direction"] = params["collection_direction"]
    if "pim" in params:
        spec["attr"]["pim"] = params["pim"]
    if "leak_proof" in params:
        spec["attr"]["leak_proof"] = params["leak_proof"]
    if "duck_mouth" in params:
        spec["attr"]["duck_mouth"] = params["duck_mouth"]
    if "detergent" in params:
        spec["attr"]["detergent"] = params["detergent"]
    specs.append(spec)

    # ---- 驱动单元 ----
    spec = {"type": "驱动单元", "model": _model_full(params), "attr": {}}
    if "regulation" in params:
        spec["attr"]["regulation"] = params["regulation"]
    if "end_user_country" in params:
        spec["attr"]["end_user_country"] = params["end_user_country"]
    specs.append(spec)

    # ---- 主振动器 ----
    spec = {"type": "主振动器", "model": _model_full(params), "attr": {}}
    specs.append(spec)

    # ---- 线性振动器 ----
    spec = {"type": "线性振动器", "model": _model_full(params), "attr": {}}
    if "regulation" in params:
        spec["attr"]["regulation"] = params["regulation"]
    if "end_user_country" in params:
        spec["attr"]["end_user_country"] = params["end_user_country"]
    specs.append(spec)

    # ---- 配线单元 ----
    spec = {"type": "配线单元", "model": _model_full(params), "attr": {}}
    if "cable_length" in params:
        spec["attr"]["cable_length"] = params["cable_length"]
    if "end_user_country" in params:
        spec["attr"]["end_user_country"] = params["end_user_country"]
    if "regulation" in params:
        spec["attr"]["regulation"] = params["regulation"]
    specs.append(spec)

    # ---- 铭牌 ----
    spec = {"type": "铭牌", "model": _model_full(params), "attr": {}}
    if "name_plate" in params:
        spec["attr"]["name_plate"] = params["name_plate"]
    if "end_user_country" in params:
        spec["attr"]["end_user_country"] = params["end_user_country"]
    if "detergent" in params:
        spec["attr"]["detergent"] = params["detergent"]
    specs.append(spec)

    # ---- 包装 ----
    spec = {"type": "包装", "model": _model_full(params), "attr": {}}
    if "end_user_country" in params:
        spec["attr"]["end_user_country"] = params["end_user_country"]
    if "detergent" in params:
        spec["attr"]["detergent"] = params["detergent"]
    specs.append(spec)

    # ---- 电气 ----
    spec = {"type": "电气", "model": _model_full(params), "attr": {}}
    if "end_user_country" in params:
        spec["attr"]["end_user_country"] = params["end_user_country"]
    if "detergent" in params:
        spec["attr"]["detergent"] = params["detergent"]
    specs.append(spec)

    # ---- 防碎 ----
    spec = {"type": "防碎", "model": _model_full(params), "attr": {}}
    specs.append(spec)

    # ---- 料层调整圈 ----
    spec = {"type": "料层调整圈", "model": _model_full(params), "attr": {}}
    if "pim" in params:
        spec["attr"]["pim"] = params["pim"]
    specs.append(spec)

    # ---- 记忆斗 ----
    spec = {"type": "记忆斗", "model": _model_full(params), "attr": {}}
    if "surface" in params:
        spec["attr"]["surface"] = params["surface"]
    specs.append(spec)

    # ---- 光电料位计 ----
    spec = {"type": "光电料位计", "model": _model_full(params), "attr": {}}
    if "sensor_type" in params:
        spec["attr"]["sensor_type"] = params["sensor_type"]
    if "photoelectric_model" in params:
        spec["attr"]["photoelectric_model"] = params["photoelectric_model"]
    if "detergent" in params:
        spec["attr"]["detergent"] = params["detergent"]
    specs.append(spec)

    # ---- Pass row-supplement (Surface change / Leak-proof / Other) data ----
    # Maps component type → spec row parameter names whose sub-columns
    # (Surface change, Leak-proof, Other) apply to this component.
    _TYPE_TO_ROW_KEYS = {
        "机架": ["surface", "common_bed"],
        "供料漏斗": ["infeed_funnel"],
        "供料锥支架": ["infeed_funnel"],  # Silo / PIM from infeed funnel
        "顶锥": ["top_cone"],
        "振动盘": ["linear_feeder_pan"],
        "供料斗": ["feed_bucket"],
        "计量斗": ["weigh_bucket"],
        "溜槽": ["collating_chute"],
        "收集锥": ["collating_funnel"],
        "集合斗": ["collection_bucket"],
        "主振动器": ["center_vibrator"],
        "防碎": ["collection_bucket"],
    }
    for s in specs:
        row_keys = _TYPE_TO_ROW_KEYS.get(s["type"], [])
        if row_keys:
            _apply_row_supplement(s, params, row_keys)

    # ---- Pass application to all component specs ----
    # "sprout" → 蔬菜 mapping enables engine scoring boost for candidates
    # with "蔬菜" in CHINANAME (see engine.py sprout/vegetable scale bonus).
    if "application" in params:
        for s in specs:
            if "attr" not in s:
                s["attr"] = {}
            s["attr"]["application"] = params["application"]

    return specs
