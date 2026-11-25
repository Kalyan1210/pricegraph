"""Shoppable services kept from each file (based on the CMS shoppable-services list plus
common imaging and inpatient DRGs). code -> (plain-English label, category)."""

SHOPPABLE: dict[str, tuple[str, str]] = {
    # Office and preventive visits
    "99203": ("New patient office visit, 30 min", "visit"),
    "99204": ("New patient office visit, 45 min", "visit"),
    "99205": ("New patient office visit, 60 min", "visit"),
    "99283": ("Emergency room visit, moderate", "visit"),
    "99284": ("Emergency room visit, high", "visit"),
    "99285": ("Emergency room visit, highest", "visit"),
    "99385": ("Preventive visit, new patient 18-39", "visit"),
    "99386": ("Preventive visit, new patient 40-64", "visit"),
    # Lab
    "80048": ("Basic metabolic panel", "lab"),
    "80053": ("Comprehensive metabolic panel", "lab"),
    "80055": ("Obstetric blood panel", "lab"),
    "80061": ("Lipid (cholesterol) panel", "lab"),
    "80069": ("Renal function panel", "lab"),
    "80076": ("Liver (hepatic) function panel", "lab"),
    "81001": ("Urinalysis with microscopy", "lab"),
    "84153": ("PSA (prostate specific antigen)", "lab"),
    "84443": ("TSH thyroid test", "lab"),
    "85025": ("Complete blood count (CBC) with differential", "lab"),
    "85027": ("Complete blood count (CBC)", "lab"),
    "85610": ("Prothrombin time (PT/INR)", "lab"),
    "85730": ("Partial thromboplastin time (PTT)", "lab"),
    # Imaging
    "70450": ("CT head/brain without contrast", "imaging"),
    "70551": ("MRI brain without contrast", "imaging"),
    "70553": ("MRI brain without and with contrast", "imaging"),
    "71046": ("Chest X-ray, 2 views", "imaging"),
    "72110": ("X-ray lower spine, 4+ views", "imaging"),
    "72148": ("MRI lower back (lumbar spine) without contrast", "imaging"),
    "72193": ("CT pelvis with contrast", "imaging"),
    "73721": ("MRI knee/leg joint without contrast", "imaging"),
    "74150": ("CT abdomen without contrast", "imaging"),
    "74160": ("CT abdomen with contrast", "imaging"),
    "74176": ("CT abdomen and pelvis without contrast", "imaging"),
    "74177": ("CT abdomen and pelvis with contrast", "imaging"),
    "76700": ("Ultrasound abdomen, complete", "imaging"),
    "76805": ("Pregnancy ultrasound, after first trimester", "imaging"),
    "76830": ("Transvaginal ultrasound", "imaging"),
    "77065": ("Diagnostic mammogram, one breast", "imaging"),
    "77066": ("Diagnostic mammogram, both breasts", "imaging"),
    "77067": ("Screening mammogram, both breasts", "imaging"),
    # Medicine and therapy
    "90834": ("Psychotherapy, 45 min", "therapy"),
    "90837": ("Psychotherapy, 60 min", "therapy"),
    "93000": ("Electrocardiogram (EKG)", "medicine"),
    "93452": ("Left heart catheterization", "medicine"),
    "95810": ("Sleep study, in-lab", "medicine"),
    "97110": ("Physical therapy, therapeutic exercise", "therapy"),
    "97161": ("Physical therapy evaluation, low complexity", "therapy"),
    # Outpatient surgery and procedures
    "19120": ("Removal of breast lump", "surgery"),
    "29826": ("Shoulder arthroscopy with decompression", "surgery"),
    "29880": ("Knee arthroscopy, meniscus repair (both)", "surgery"),
    "29881": ("Knee arthroscopy, meniscus repair (one side)", "surgery"),
    "42820": ("Tonsil and adenoid removal, under 12", "surgery"),
    "43235": ("Upper GI endoscopy, diagnostic", "surgery"),
    "43239": ("Upper GI endoscopy with biopsy", "surgery"),
    "45378": ("Colonoscopy, diagnostic", "surgery"),
    "45380": ("Colonoscopy with biopsy", "surgery"),
    "45385": ("Colonoscopy with polyp removal", "surgery"),
    "G0121": ("Screening colonoscopy, not high risk", "surgery"),
    "47562": ("Laparoscopic gallbladder removal", "surgery"),
    "49505": ("Inguinal hernia repair", "surgery"),
    "55700": ("Prostate biopsy", "surgery"),
    "59400": ("Vaginal delivery with prenatal and postpartum care", "maternity"),
    "59510": ("C-section with prenatal and postpartum care", "maternity"),
    "59610": ("VBAC delivery with prenatal and postpartum care", "maternity"),
    "62323": ("Lumbar epidural steroid injection with imaging", "surgery"),
    "64483": ("Transforaminal epidural injection, lumbar", "surgery"),
    "66821": ("Laser surgery after cataract (YAG)", "surgery"),
    "66984": ("Cataract removal with lens implant", "surgery"),
    # Inpatient stays (MS-DRG)
    "470": ("Hip or knee replacement (MS-DRG 470)", "inpatient"),
    "460": ("Spinal fusion (MS-DRG 460)", "inpatient"),
    "473": ("Neck spinal fusion (MS-DRG 473)", "inpatient"),
    "216": ("Heart valve surgery with cath (MS-DRG 216)", "inpatient"),
    "291": ("Heart failure with complications (MS-DRG 291)", "inpatient"),
    "871": ("Sepsis with complications (MS-DRG 871)", "inpatient"),
    "194": ("Pneumonia with complications (MS-DRG 194)", "inpatient"),
    "392": ("Digestive disorders (MS-DRG 392)", "inpatient"),
    "743": ("Uterine procedures, non-cancer (MS-DRG 743)", "inpatient"),
    "788": ("C-section without complications (MS-DRG 788)", "inpatient"),
    "807": ("Vaginal delivery without complications (MS-DRG 807)", "inpatient"),
}

DRG_CODES = {c for c in SHOPPABLE if c.isdigit() and len(c) == 3}


def match_keys() -> dict[str, str]:
    """code as it may appear in a file -> required code family. DRGs show up as 470 and 0470,
    and 0470 is also a revenue code (audiology), so the type must match too."""
    keys: dict[str, str] = {}
    for c in SHOPPABLE:
        if c in DRG_CODES:
            keys[c] = keys["0" + c] = "DRG"
        else:
            keys[c] = "CPT"
    return keys


def canonical(code: str) -> str:
    code = code.strip().upper()
    if code.startswith("0") and code[1:] in DRG_CODES:
        return code[1:]
    return code
