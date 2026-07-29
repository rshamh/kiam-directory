"""
Controlled vocabulary.

Four independent axes: profession (what they are), speciality (what they treat),
approach (how they work), client group (who they see). Plus flat lists.

EDITORIAL RULES BAKED IN:
  - No prescription-only medicine is named anywhere. The permitted terms are
    "Medication management", "Prescribing", "Titration and review", "Shared care".
    UK ASA/CAP prohibits advertising POMs to the public.
  - No efficacy or outcome language in any label.
  - Category and speciality `description` fields are clinical content and require
    Dr. Abbass sign-off before they appear publicly. Left empty here deliberately.

Loaded by `manage.py seed_taxonomy`, which is idempotent and safe to re-run.
"""

# (slug, name, restricted, required_bodies)
PROFESSIONS = [
    ("consultant-psychiatrist", "Consultant Psychiatrist", True, ["GMC"]),
    ("psychiatrist", "Psychiatrist", True, ["GMC"]),
    ("child-adolescent-psychiatrist", "Child & Adolescent Psychiatrist", True, ["GMC"]),
    ("clinical-psychologist", "Clinical Psychologist", True, ["HCPC"]),
    ("counselling-psychologist", "Counselling Psychologist", True, ["HCPC"]),
    ("educational-psychologist", "Educational Psychologist", True, ["HCPC"]),
    ("forensic-psychologist", "Forensic Psychologist", True, ["HCPC"]),
    ("health-psychologist", "Health Psychologist", True, ["HCPC"]),
    ("occupational-psychologist", "Occupational Psychologist", True, ["HCPC"]),
    ("neuropsychologist", "Clinical Neuropsychologist", True, ["HCPC"]),
    ("psychotherapist", "Psychotherapist", False, ["UKCP", "BACP", "BPC"]),
    ("child-psychotherapist", "Child & Adolescent Psychotherapist", False, ["UKCP", "ACP"]),
    ("counsellor", "Counsellor", False, ["BACP", "NCPS"]),
    ("cbt-therapist", "CBT Therapist", False, ["BABCP"]),
    ("emdr-therapist", "EMDR Therapist", False, ["EMDR UK"]),
    ("family-systemic-psychotherapist", "Family & Systemic Psychotherapist", False, ["AFT", "UKCP"]),
    ("mental-health-nurse", "Mental Health Nurse", True, ["NMC"]),
    ("non-medical-prescriber", "Non-Medical Prescriber", True, ["NMC", "GPhC", "HCPC"]),
    ("occupational-therapist", "Occupational Therapist", True, ["HCPC"]),
    ("art-therapist", "Art Therapist", True, ["HCPC"]),
    ("music-therapist", "Music Therapist", True, ["HCPC"]),
    ("dramatherapist", "Dramatherapist", True, ["HCPC"]),
    ("play-therapist", "Play Therapist", False, ["BAPT", "PTUK"]),
    ("dietitian", "Dietitian", True, ["HCPC"]),
    ("speech-language-therapist", "Speech & Language Therapist", True, ["HCPC"]),
    ("psychological-wellbeing-practitioner", "Psychological Wellbeing Practitioner", False, []),
    ("adhd-coach", "ADHD Coach", False, []),
    ("hypnotherapist", "Hypnotherapist", False, ["GHR", "NCH"]),
    ("social-worker", "Social Worker", True, ["Social Work England"]),
]

# category_slug, category_name, [(slug, name, synonyms, implies_minors)]
SPECIALITY_CATEGORIES = [
    (
        "neurodevelopmental",
        "Neurodevelopmental",
        [
            ("adult-adhd-assessment", "Adult ADHD assessment", ["adhd diagnosis", "add"], False),
            ("child-adhd-assessment", "Child & adolescent ADHD assessment", [], True),
            ("adhd-treatment-review", "ADHD treatment & review", [], False),
            ("adhd-medication-management", "ADHD medication management", [], False),
            ("adhd-shared-care", "ADHD shared care", [], False),
            ("adhd-in-women", "ADHD in women", [], False),
            ("adhd-coaching", "ADHD coaching", [], False),
            ("autism-assessment-adults", "Autism assessment (adults)", ["asd", "asperger"], False),
            ("autism-assessment-children", "Autism assessment (children)", [], True),
            ("autism-support", "Autism support & post-diagnostic work", [], False),
            ("tic-disorders", "Tics & Tourette's", [], False),
            ("specific-learning-difficulties", "Specific learning difficulties", ["spld"], False),
            ("dyslexia", "Dyslexia", [], False),
            ("dyspraxia", "Dyspraxia / DCD", [], False),
            ("learning-disability", "Learning disability", [], False),
            ("sensory-processing", "Sensory processing differences", [], False),
        ],
    ),
    (
        "mood",
        "Mood",
        [
            ("depression", "Depression", ["low mood"], False),
            ("treatment-resistant-depression", "Treatment-resistant depression", [], False),
            ("bipolar-disorder", "Bipolar disorder", ["manic depression"], False),
            ("persistent-low-mood", "Persistent low mood", ["dysthymia"], False),
            ("seasonal-affective-disorder", "Seasonal affective disorder", ["sad"], False),
            ("postnatal-depression", "Postnatal depression", ["pnd"], False),
        ],
    ),
    (
        "anxiety",
        "Anxiety",
        [
            ("generalised-anxiety", "Generalised anxiety", ["gad", "worry"], False),
            ("panic-disorder", "Panic disorder & panic attacks", [], False),
            ("social-anxiety", "Social anxiety", ["social phobia"], False),
            ("health-anxiety", "Health anxiety", ["hypochondria"], False),
            ("phobias", "Phobias", [], False),
            ("agoraphobia", "Agoraphobia", [], False),
            ("separation-anxiety", "Separation anxiety", [], True),
            ("performance-anxiety", "Performance anxiety", [], False),
        ],
    ),
    (
        "obsessive-compulsive",
        "Obsessive-compulsive & related",
        [
            ("ocd", "Obsessive-compulsive disorder (OCD)", ["obsessive compulsive"], False),
            ("body-dysmorphic-disorder", "Body dysmorphic disorder", ["bdd"], False),
            ("hoarding", "Hoarding", [], False),
            (
                "trichotillomania",
                "Hair pulling & skin picking",
                ["trichotillomania", "dermatillomania"],
                False,
            ),
            ("intrusive-thoughts", "Intrusive thoughts", [], False),
        ],
    ),
    (
        "trauma",
        "Trauma & loss",
        [
            ("ptsd", "Post-traumatic stress (PTSD)", ["ptsd"], False),
            ("complex-ptsd", "Complex PTSD", ["cptsd"], False),
            ("childhood-trauma", "Childhood trauma", ["ace"], False),
            ("domestic-abuse", "Domestic abuse", ["domestic violence"], False),
            ("sexual-abuse-assault", "Sexual abuse & assault", [], False),
            ("bereavement", "Bereavement & grief", ["grief", "loss"], False),
            ("medical-trauma", "Medical & birth trauma", [], False),
            ("accident-trauma", "Accident & injury trauma", [], False),
            ("refugee-asylum-trauma", "Refugee & asylum-related trauma", [], False),
        ],
    ),
    (
        "psychosis",
        "Psychosis & severe mental illness",
        [
            ("psychosis", "Psychosis", [], False),
            ("schizophrenia", "Schizophrenia", [], False),
            ("schizoaffective-disorder", "Schizoaffective disorder", [], False),
            ("first-episode-psychosis", "First-episode psychosis", [], False),
            ("relapse-prevention", "Relapse prevention", [], False),
        ],
    ),
    (
        "eating-and-body",
        "Eating & body image",
        [
            ("eating-disorders", "Eating disorders", [], False),
            ("anorexia-nervosa", "Anorexia nervosa", [], False),
            ("bulimia-nervosa", "Bulimia nervosa", [], False),
            ("binge-eating", "Binge eating", ["bed"], False),
            ("arfid", "ARFID", ["avoidant restrictive food intake"], False),
            ("body-image", "Body image", [], False),
        ],
    ),
    (
        "personality-and-relational",
        "Personality & emotion regulation",
        [
            (
                "emotionally-unstable-personality",
                "Emotionally unstable personality disorder",
                ["eupd", "bpd"],
                False,
            ),
            ("emotion-dysregulation", "Emotion dysregulation", [], False),
            ("attachment-difficulties", "Attachment difficulties", [], False),
            ("dissociation", "Dissociation", [], False),
            ("identity-difficulties", "Identity difficulties", [], False),
        ],
    ),
    (
        "addiction",
        "Addiction & compulsive behaviour",
        [
            ("alcohol", "Alcohol use", ["drinking"], False),
            ("drug-use", "Drug use", ["substance misuse"], False),
            ("gambling", "Gambling", [], False),
            ("gaming-internet", "Gaming & internet use", [], False),
            ("sex-pornography", "Sex & pornography", [], False),
            ("smoking-vaping", "Smoking & vaping", [], False),
            ("shopping-spending", "Compulsive spending", [], False),
        ],
    ),
    (
        "sleep",
        "Sleep",
        [
            ("insomnia", "Insomnia", [], False),
            ("cbt-i", "CBT for insomnia", ["cbti"], False),
            ("nightmares", "Nightmares", [], False),
            ("circadian-rhythm", "Circadian rhythm difficulties", [], False),
            ("sleep-in-neurodivergence", "Sleep & neurodivergence", [], False),
        ],
    ),
    (
        "relationships-family",
        "Relationships & family",
        [
            ("couples-therapy", "Couples therapy", ["marriage counselling"], False),
            ("relationship-difficulties", "Relationship difficulties", [], False),
            ("separation-divorce", "Separation & divorce", [], False),
            ("infidelity", "Infidelity", ["affairs"], False),
            ("family-conflict", "Family conflict", [], False),
            ("parenting-support", "Parenting support", [], False),
            ("co-parenting", "Co-parenting", [], False),
            ("sexual-difficulties", "Sexual difficulties", ["psychosexual"], False),
            ("loneliness", "Loneliness & isolation", [], False),
        ],
    ),
    (
        "life-stage",
        "Life stage & reproductive health",
        [
            ("perinatal-mental-health", "Perinatal mental health", ["maternal"], False),
            ("fertility-pregnancy-loss", "Fertility & pregnancy loss", ["miscarriage"], False),
            ("menopause-mental-health", "Menopause & mental health", ["perimenopause"], False),
            ("premenstrual-difficulties", "Premenstrual difficulties", ["pmdd"], False),
            ("mens-mental-health", "Men's mental health", [], False),
            ("womens-mental-health", "Women's mental health", [], False),
            ("older-adults", "Older adults' mental health", [], False),
            ("dementia-support", "Dementia & cognitive change", [], False),
            ("carers-support", "Carer support", [], False),
            ("adolescent-mental-health", "Adolescent mental health", ["teen", "camhs"], True),
            ("student-mental-health", "Student mental health", ["university"], False),
        ],
    ),
    (
        "work-performance",
        "Work & performance",
        [
            ("burnout", "Burnout", [], False),
            ("workplace-stress", "Workplace stress", [], False),
            ("occupational-health", "Occupational health", [], False),
            ("career-difficulties", "Career difficulties", [], False),
            ("leadership-executive", "Leadership & executive support", [], False),
            ("sport-performance", "Sport & performance psychology", [], False),
            ("workplace-adjustments", "Workplace adjustments & Access to Work", [], False),
        ],
    ),
    (
        "identity-culture",
        "Identity & culture",
        [
            ("lgbtq-affirmative", "LGBTQ+ affirmative practice", ["lgbt", "queer"], False),
            ("gender-identity", "Gender identity", ["trans"], False),
            ("race-cultural-identity", "Race & cultural identity", [], False),
            ("faith-and-spirituality", "Faith & spirituality", ["religion"], False),
            ("neurodiversity-affirming", "Neurodiversity-affirming practice", [], False),
            ("migration-acculturation", "Migration & acculturation", [], False),
            ("discrimination", "Discrimination & racial trauma", [], False),
        ],
    ),
    (
        "physical-health",
        "Physical health & long-term conditions",
        [
            ("chronic-pain", "Chronic pain", [], False),
            ("chronic-fatigue", "Chronic fatigue / ME", ["cfs", "me"], False),
            ("long-covid", "Long COVID", [], False),
            ("ibs-gut-health", "IBS & gut-brain difficulties", ["irritable bowel"], False),
            ("cancer-oncology", "Cancer & oncology support", [], False),
            ("diabetes-distress", "Diabetes distress", [], False),
            ("tinnitus", "Tinnitus", [], False),
            ("functional-neurological-disorder", "Functional neurological disorder", ["fnd"], False),
            ("adjustment-to-illness", "Adjustment to illness & disability", [], False),
            ("acquired-brain-injury", "Acquired brain injury", ["abi", "tbi"], False),
        ],
    ),
    (
        "self-and-behaviour",
        "Self & behaviour",
        [
            ("anger", "Anger", [], False),
            ("self-esteem", "Self-esteem", ["confidence"], False),
            ("perfectionism", "Perfectionism", [], False),
            ("procrastination", "Procrastination", [], False),
            ("assertiveness", "Assertiveness & boundaries", [], False),
            ("self-harm", "Self-harm", [], False),
            ("suicidal-thoughts", "Suicidal thoughts", [], False),
            ("stress-management", "Stress management", [], False),
            ("life-transitions", "Life transitions", [], False),
        ],
    ),
    (
        "assessments-reports",
        "Assessments & reports",
        [
            ("psychiatric-assessment", "Psychiatric assessment", [], False),
            ("second-opinion", "Second opinion", [], False),
            ("cognitive-assessment", "Cognitive & neuropsychological assessment", [], False),
            ("nhs-right-to-choose", "NHS Right to Choose", ["rtc"], False),
            ("fitness-to-work", "Fitness to work & study reports", [], False),
            ("educational-reports", "Educational & DSA reports", ["dsa", "ehcp"], False),
            ("capacity-assessment", "Mental capacity assessment", ["mca"], False),
        ],
    ),
    (
        "medico-legal",
        "Medico-legal & forensic",
        [
            ("expert-witness", "Expert witness", [], False),
            ("court-reports", "Court reports", [], False),
            ("personal-injury-reports", "Personal injury reports", [], False),
            ("immigration-asylum-reports", "Immigration & asylum reports", [], False),
            ("family-court-work", "Family court work", [], False),
            ("forensic-risk-assessment", "Forensic risk assessment", [], False),
        ],
    ),
]

# (slug, name, abbreviation)
APPROACHES = [
    ("cbt", "Cognitive Behavioural Therapy", "CBT"),
    ("dbt", "Dialectical Behaviour Therapy", "DBT"),
    ("act", "Acceptance & Commitment Therapy", "ACT"),
    ("cft", "Compassion Focused Therapy", "CFT"),
    ("cat", "Cognitive Analytic Therapy", "CAT"),
    ("emdr", "EMDR", "EMDR"),
    ("schema-therapy", "Schema Therapy", ""),
    ("ipt", "Interpersonal Therapy", "IPT"),
    ("psychodynamic", "Psychodynamic Therapy", ""),
    ("psychoanalytic", "Psychoanalytic Psychotherapy", ""),
    ("person-centred", "Person-Centred Therapy", ""),
    ("humanistic", "Humanistic Therapy", ""),
    ("integrative", "Integrative Therapy", ""),
    ("existential", "Existential Therapy", ""),
    ("gestalt", "Gestalt Therapy", ""),
    ("transactional-analysis", "Transactional Analysis", "TA"),
    ("systemic-family", "Systemic & Family Therapy", ""),
    ("solution-focused", "Solution-Focused Brief Therapy", "SFBT"),
    ("narrative-therapy", "Narrative Therapy", ""),
    ("ifs", "Internal Family Systems", "IFS"),
    ("mbct", "Mindfulness-Based Cognitive Therapy", "MBCT"),
    ("mbsr", "Mindfulness-Based Stress Reduction", "MBSR"),
    ("behavioural-activation", "Behavioural Activation", ""),
    ("erp", "Exposure & Response Prevention", "ERP"),
    ("motivational-interviewing", "Motivational Interviewing", "MI"),
    ("somatic", "Somatic & Body-Based Therapy", ""),
    ("sensorimotor", "Sensorimotor Psychotherapy", ""),
    ("eft-couples", "Emotionally Focused Therapy (Couples)", "EFT"),
    ("gottman", "Gottman Method", ""),
    ("art-therapy", "Art Therapy", ""),
    ("music-therapy", "Music Therapy", ""),
    ("dramatherapy", "Dramatherapy", ""),
    ("play-therapy", "Play Therapy", ""),
    ("group-therapy", "Group Therapy", ""),
    ("clinical-hypnotherapy", "Clinical Hypnotherapy", ""),
    # Psychiatric care — deliberately generic. Never name a prescription-only medicine.
    ("medication-management", "Medication management", ""),
    ("titration-and-review", "Titration & review", ""),
    ("clinical-supervision", "Clinical supervision", ""),
]

# (slug, name, min_age, max_age, is_minors)
CLIENT_GROUPS = [
    ("children", "Children (0–11)", 0, 11, True),
    ("adolescents", "Adolescents (12–17)", 12, 17, True),
    ("young-adults", "Young adults (18–25)", 18, 25, False),
    ("adults", "Adults (18+)", 18, None, False),
    ("older-adults", "Older adults (65+)", 65, None, False),
    ("couples", "Couples", None, None, False),
    ("families", "Families", None, None, False),
    ("groups", "Groups", None, None, False),
    ("organisations", "Organisations & workplaces", None, None, False),
]

SESSION_FORMATS = [
    ("individual", "Individual"),
    ("couples", "Couples"),
    ("family", "Family"),
    ("group", "Group"),
    ("workshop", "Workshop / training"),
    ("supervision", "Supervision"),
]

# (slug, name, group)
FUNDING_OPTIONS = [
    ("self-funding", "Self-funding", "self_pay"),
    ("sliding-scale", "Sliding scale / concessions", "self_pay"),
    ("free-initial-call", "Free initial call", "self_pay"),
    ("bupa", "Bupa", "insurer"),
    ("axa-health", "AXA Health", "insurer"),
    ("aviva", "Aviva", "insurer"),
    ("vitality", "Vitality", "insurer"),
    ("cigna", "Cigna", "insurer"),
    ("wpa", "WPA", "insurer"),
    ("healix", "Healix", "insurer"),
    ("freedom-health", "Freedom Health", "insurer"),
    ("nhs-right-to-choose", "NHS Right to Choose", "nhs"),
    ("nhs-shared-care", "NHS shared care", "nhs"),
    ("eap", "Employee Assistance Programme", "employer"),
    ("employer-funded", "Employer funded", "employer"),
]

LANGUAGES = [
    ("en", "English"),
    ("bsl", "British Sign Language"),
    ("fa", "Persian (Farsi)"),
    ("ar", "Arabic"),
    ("ur", "Urdu"),
    ("pa", "Punjabi"),
    ("hi", "Hindi"),
    ("bn", "Bengali"),
    ("gu", "Gujarati"),
    ("ta", "Tamil"),
    ("pl", "Polish"),
    ("ro", "Romanian"),
    ("ru", "Russian"),
    ("uk", "Ukrainian"),
    ("tr", "Turkish"),
    ("ku", "Kurdish"),
    ("fr", "French"),
    ("de", "German"),
    ("es", "Spanish"),
    ("pt", "Portuguese"),
    ("it", "Italian"),
    ("nl", "Dutch"),
    ("el", "Greek"),
    ("zh", "Mandarin"),
    ("yue", "Cantonese"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
    ("th", "Thai"),
    ("vi", "Vietnamese"),
    ("tl", "Tagalog"),
    ("sw", "Swahili"),
    ("so", "Somali"),
    ("am", "Amharic"),
    ("he", "Hebrew"),
    ("cy", "Welsh"),
]

# ---------------------------------------------------------------------------
# Submission lint
# ---------------------------------------------------------------------------

EFFICACY_CLAIM_FLAGS = [
    "cure",
    "cures",
    "guaranteed",
    "guarantee",
    "100%",
    "proven to",
    "permanently eliminates",
    "miracle",
    "risk-free",
    "no side effects",
    "success rate",
    "best in",
    "world-leading",
    "instantly",
]

RESTRICTED_TITLE_FLAGS = [
    "psychologist",
    "psychiatrist",
    "clinical psychologist",
    "counselling psychologist",
    "educational psychologist",
    "occupational therapist",
    "dietitian",
    "arts therapist",
    "social worker",
]

# Flagged in intro/services for any practitioner not CLEARED for under-18 work. Catches
# the case where client-group tags are gated but the bio still advertises child work.
CHILD_WORK_FLAGS = [
    "child",
    "children",
    "teenager",
    "teenagers",
    "adolescent",
    "adolescents",
    "young people",
    "under 18",
    "under-18",
    "school-age",
    "camhs",
    "paediatric",
]

# POM detection uses a maintained dictionary of BNF chapter 4 substance and brand names,
# held server-side (settings.POM_DICTIONARY_PATH) so it updates without a deploy. Any
# match blocks submission and routes to admin review.
POM_DICTIONARY_SETTING = "POM_DICTIONARY_PATH"
