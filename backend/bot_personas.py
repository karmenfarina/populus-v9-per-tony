"""
Populus — Bot personas generator.

Design goals
─────────────────────────────────────────────────────────────────────
Produce a deterministic list of exactly 100 fake user profiles that
look and BEHAVE differently:
  • Different name, nickname, age, gender, region, city, profession, bio
  • Different political leaning + top interests (categories)
  • Different tone of voice + verbosity
  • Different activity level (low / medium / high)

The list is ordered so that a caller who wants "the first N bots
active" already gets a BALANCED sample across every axis. This is
what lets the admin slider (0..100) reduce/increase the online bot
count without accidentally skewing the platform toward one topic or
political side.

Balanced-order algorithm
─────────────────────────────────────────────────────────────────────
We build the list as an interleaving of three axes:

  1. political_lean  — 7 buckets
  2. main_topic      — 9 buckets (one per Populus category)
  3. activity_level  — 3 buckets (low / medium / high)

We deterministically construct 100 tuples that CYCLE through those
buckets so any prefix of the list is close to a uniform sample. The
exact recipe is documented inside `build_personas()`.

Nothing here talks to the DB. `bot_engine.ensure_bots_seeded()` does
the persistence. Keeping the personas pure makes the tests reliable.
"""
from __future__ import annotations

import hashlib
import random
from typing import List, Dict, Any, Optional

# Populus categories — same IDs as in server.py.
CATEGORY_IDS = [
    "politica", "tv", "musica", "sport", "cinema",
    "social", "gossip", "cronaca", "tech",
]

POLITICAL_LEANS = [
    "sinistra_radicale", "sinistra", "centro_sinistra",
    "centro",
    "centro_destra", "destra", "destra_radicale",
]

ACTIVITY_LEVELS = ["low", "medium", "high"]

TONES = [
    "sarcastico", "empatico", "cinico", "entusiasta",
    "analitico", "giocoso", "serio", "polemico", "conciliante",
]

VERBOSITIES = ["micro", "breve", "medio", "lungo", "verboso"]

# Emotional micro-reactions bots occasionally pick when their default
# verbosity or per-call override picks the "micro" bucket. Kept as a
# curated list so Claude has concrete examples to imitate — DO NOT
# pass them to the model verbatim, just show them as inspiration in
# the system prompt.
MICRO_SAMPLES = [
    "assurdo",
    "bravissim*",
    "che palle",
    "che schifo",
    "quoto tutto",
    "d'accordissimo",
    "assolutamente no",
    "vergognoso",
    "esatto",
    "esagerato dai",
    "no vabbè",
    "punto",
    "meno male",
    "finalmente",
    "cringe",
    "epico",
    "no comment",
    "chapeau",
    "ridicolo",
    "questa non ce la potevamo perdere",
]

# 20 Italian regions — realistic distribution weighted later on.
REGIONS_CITIES = {
    "Lombardia": ["Milano", "Brescia", "Bergamo", "Monza", "Como"],
    "Lazio": ["Roma", "Latina", "Frosinone", "Viterbo"],
    "Campania": ["Napoli", "Salerno", "Caserta", "Avellino"],
    "Sicilia": ["Palermo", "Catania", "Messina", "Ragusa"],
    "Veneto": ["Venezia", "Verona", "Padova", "Vicenza"],
    "Emilia-Romagna": ["Bologna", "Modena", "Parma", "Rimini"],
    "Piemonte": ["Torino", "Alessandria", "Novara", "Asti"],
    "Puglia": ["Bari", "Lecce", "Taranto", "Foggia"],
    "Toscana": ["Firenze", "Pisa", "Siena", "Livorno"],
    "Calabria": ["Reggio Calabria", "Catanzaro", "Cosenza"],
    "Sardegna": ["Cagliari", "Sassari", "Olbia"],
    "Liguria": ["Genova", "La Spezia", "Savona"],
    "Marche": ["Ancona", "Pesaro", "Ascoli Piceno"],
    "Abruzzo": ["Pescara", "L'Aquila", "Chieti"],
    "Friuli-Venezia Giulia": ["Trieste", "Udine", "Pordenone"],
    "Trentino-Alto Adige": ["Trento", "Bolzano"],
    "Umbria": ["Perugia", "Terni"],
    "Basilicata": ["Potenza", "Matera"],
    "Molise": ["Campobasso"],
    "Valle d'Aosta": ["Aosta"],
}

FIRST_NAMES_M = [
    "Luca", "Marco", "Alessandro", "Andrea", "Matteo", "Giovanni", "Simone",
    "Francesco", "Giorgio", "Riccardo", "Federico", "Paolo", "Davide",
    "Lorenzo", "Emanuele", "Fabio", "Roberto", "Stefano", "Giulio",
    "Nicola", "Salvatore", "Enrico", "Gabriele", "Claudio", "Mauro",
]

FIRST_NAMES_F = [
    "Giulia", "Sofia", "Martina", "Chiara", "Alessia", "Elena", "Sara",
    "Francesca", "Alice", "Laura", "Silvia", "Beatrice", "Anna",
    "Federica", "Camilla", "Valentina", "Eleonora", "Roberta", "Ilaria",
    "Serena", "Marta", "Carla", "Paola", "Cristina", "Vanessa",
]

LAST_NAMES = [
    # Curated pool of 120+ Italian surnames so we can hand out 100 unique
    # ones AND still have spare in case names get filtered out (e.g. an
    # exact collision with a shuffled first-name pair). Ordered
    # roughly by real-world frequency but expanded for uniqueness.
    "Rossi", "Ferrari", "Russo", "Bianchi", "Romano", "Colombo", "Ricci",
    "Marino", "Greco", "Bruno", "Gallo", "Conti", "De Luca", "Costa",
    "Giordano", "Mancini", "Rizzo", "Lombardi", "Moretti", "Barbieri",
    "Fontana", "Santoro", "Mariani", "Rinaldi", "Caruso", "Ferrara",
    "Galli", "Martini", "Leone", "Longo", "Gentile", "Serra", "Vitale",
    "Marchetti", "Parisi", "Villa", "Guerra", "Battaglia", "Sartori",
    "Esposito", "Bianco", "Bruni", "Carbone", "Coppola", "De Santis",
    "De Rosa", "Farina", "Ferri", "Fiore", "Fumagalli", "Gatti",
    "Grassi", "Grimaldi", "La Rocca", "Longhi", "Marconi", "Marini",
    "Mazza", "Messina", "Montanari", "Monti", "Morelli", "Neri",
    "Palumbo", "Pellegrini", "Piras", "Poli", "Riva", "Ruggiero",
    "Sanna", "Sartor", "Serafini", "Silvestri", "Sorrentino", "Testa",
    "Valente", "Valentini", "Vinci", "Zanetti", "Basile", "Benedetti",
    "Bertolini", "Bianchini", "Bonetti", "Borghi", "Bosco", "Cattaneo",
    "Cavallaro", "Ciccone", "Cirillo", "Colella", "D'Angelo", "D'Amico",
    "De Angelis", "De Simone", "Donati", "Fabbri", "Ferretti", "Franco",
    "Guidi", "Iannone", "La Torre", "Lanza", "Lombardo", "Lupo",
    "Magni", "Marra", "Martino", "Meloni", "Milani", "Molinari",
    "Napolitano", "Orlando", "Pace", "Pagano", "Parisi", "Perri",
    "Pesce", "Piccoli", "Pini", "Pirozzi", "Pisano", "Pizzuto",
    "Poggi", "Proietti", "Puglisi",
]

PROFESSIONS = [
    "Studente", "Impiegato", "Insegnante", "Ingegnere", "Medico",
    "Avvocato", "Grafico", "Designer", "Programmatore", "Giornalista",
    "Fotografo", "Musicista", "Attore", "Chef", "Commesso",
    "Barista", "Barbiere", "Idraulico", "Elettricista", "Falegname",
    "Autista", "Commerciante", "Libero professionista", "Pensionato",
    "Operaio", "Artigiano", "Consulente", "Ricercatore", "Architetto",
    "Ristoratore", "PR", "Copywriter", "Social media manager", "Fisioterapista",
    "Infermiere", "Farmacista", "Traduttore", "Content creator", "Editor",
]

# Short bio templates — combined with topic tags. Kept intentionally
# generic-Italian to sound like real users, not marketing copy.
BIO_TEMPLATES = [
    "{topic1} è la mia droga. Anche {topic2}, ma di meno.",
    "Vivo di {topic1}, {topic2} e caffè.",
    "Divido il tempo tra {topic1} e {topic2}. Il resto è rumore.",
    "Innamorat{gender_e} di {topic1}. Odiat{gender_ore} da chi non mi capisce.",
    "Sopravvivo grazie a {topic1}. E ogni tanto un po' di {topic2}.",
    "{topic1} > tutto il resto.",
    "Se non mi trovi qui, sono da qualche parte a parlare di {topic1}.",
    "Semplicemente uno che segue {topic1} con troppa passione.",
    "Fanatic{gender_o} di {topic1}. Curios{gender_o} di {topic2}.",
    "Faccio scelte discutibili. Su {topic1} però ho ragione.",
    "Guardo troppa {topic1}. E scrivo commenti troppo lunghi.",
    "Cresciut{gender_a} a pane e {topic1}.",
    "Il mio hobby preferito è litigare di {topic1}.",
    "Mi interessa tutto: partiamo da {topic1}?",
    "In continua ricerca del prossimo dibattito su {topic1}.",
    "Da vent'anni a discutere di {topic1}. Non sono ancora stanco.",
    "Prometto di essere gentile. Poi arriva un post di {topic1}.",
    "Curatore non ufficiale del dramma di {topic1}.",
]

TOPIC_LABELS_IT = {
    "politica": "politica",
    "tv": "programmi TV",
    "musica": "musica",
    "sport": "sport",
    "cinema": "cinema",
    "social": "social",
    "gossip": "gossip",
    "cronaca": "cronaca",
    "tech": "tecnologia",
}


def _seeded_rng(seed_str: str) -> random.Random:
    """Return a stable RNG derived from a string. Deterministic across
    processes so re-seeding the bot pool doesn't reshuffle everything.
    """
    h = hashlib.sha256(seed_str.encode("utf-8")).hexdigest()
    return random.Random(int(h[:16], 16))


def _bio_for(rng: random.Random, gender: str, topics: List[str]) -> str:
    tpl = rng.choice(BIO_TEMPLATES)
    topic_labels = [TOPIC_LABELS_IT.get(t, t) for t in topics]
    if len(topic_labels) < 2:
        topic_labels = topic_labels + [rng.choice(list(TOPIC_LABELS_IT.values()))]
    return (
        tpl.replace("{topic1}", topic_labels[0])
        .replace("{topic2}", topic_labels[1])
        .replace("{gender_e}", "a" if gender == "F" else "o")
        .replace("{gender_ore}", "rice" if gender == "F" else "ore")
        .replace("{gender_o}", "a" if gender == "F" else "o")
        .replace("{gender_a}", "a" if gender == "F" else "o")
    )


def _unique_nick(rng: random.Random, first: str, last: str, used: set) -> str:
    """Instagram-ish nickname: lowercase letters/dots/underscores/digits.
    Guaranteed unique within a run.
    """
    base_first = first.lower()
    base_last = last.lower().replace(" ", "").replace("'", "")
    styles = [
        f"{base_first}.{base_last}",
        f"{base_first}_{base_last}",
        f"{base_first}{base_last}",
        f"{base_first}{rng.randint(70, 99)}",
        f"{base_first}.{base_last[:3]}{rng.randint(1, 99)}",
        f"il_{base_first}",
        f"la_{base_first}",
        f"{base_first}_official",
        f"{base_first}.{rng.randint(1985, 2005)}",
    ]
    for s in styles:
        if s not in used and 3 <= len(s) <= 20:
            used.add(s)
            return s
    # Fallback with a counter
    i = 2
    while True:
        candidate = f"{base_first}{i}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        i += 1


def _age_for(activity: str, rng: random.Random) -> int:
    # Younger users tend to be more active online — soft bias only.
    if activity == "high":
        return rng.randint(18, 38)
    if activity == "medium":
        return rng.randint(21, 55)
    return rng.randint(28, 72)


def _party_bias(political_lean: str) -> Dict[str, float]:
    """Return abstract weights used later by the bot engine when it
    picks a side on a political feud. Non-political feuds ignore this.
    A/B mapping isn't fixed — the engine reads the feud's parties and
    decides which one matches the bias heuristically.
    """
    intensity = {
        "sinistra_radicale": 0.95,
        "sinistra": 0.80,
        "centro_sinistra": 0.65,
        "centro": 0.5,
        "centro_destra": 0.35,
        "destra": 0.20,
        "destra_radicale": 0.05,
    }
    return {"left_side_probability": intensity.get(political_lean, 0.5)}


def build_personas() -> List[Dict[str, Any]]:
    """Return 100 personas in a balanced order.

    Balanced-order recipe (deterministic):
      * axis A: political_lean cycles through 7 leans (index % 7)
      * axis B: main topic cycles through 9 categories (index % 9)
      * axis C: activity level cycles through 3 levels (index % 3)
      * gender flips every other index

    Because 7, 9 and 3 are pairwise coprime with 100 in enough ways,
    any prefix of size ≥ ~15 already covers all buckets fairly.
    """
    used_nicks: set = set()
    used_emails: set = set()
    personas: List[Dict[str, Any]] = []

    regions_list = list(REGIONS_CITIES.keys())

    # Deterministic shuffled pools — ensures adjacent bots don't share
    # the same surname and that every one of the 100 bots gets a UNIQUE
    # surname (pool >= 100). Bug fix (v3): the earlier version split
    # the pool by gender, which limited effective uniqueness to ~50.
    pool_rng = random.Random("populus-name-pool-v4-unique")
    # Dedup while preserving order — the LAST_NAMES literal has one
    # accidental duplicate ("Parisi"), which would otherwise leak into
    # the final pool as a repeat surname.
    _seen: set = set()
    last_names_shuffled = []
    for n in LAST_NAMES:
        if n not in _seen:
            _seen.add(n)
            last_names_shuffled.append(n)
    pool_rng.shuffle(last_names_shuffled)
    if len(last_names_shuffled) < 100:
        raise RuntimeError(
            f"LAST_NAMES pool has {len(last_names_shuffled)} entries — need at least 100 for unique surnames."
        )
    first_pool_f_shuf = FIRST_NAMES_F.copy()
    first_pool_m_shuf = FIRST_NAMES_M.copy()
    pool_rng.shuffle(first_pool_f_shuf)
    pool_rng.shuffle(first_pool_m_shuf)
    # Track per-gender counter so first-name index advances independently
    # by gender; surname index advances globally so all 100 are unique.
    f_counter = 0
    m_counter = 0

    for i in range(100):
        rng = _seeded_rng(f"populus-bot-{i}-v2")

        political_lean = POLITICAL_LEANS[i % 7]
        main_topic = CATEGORY_IDS[i % 9]
        activity = ACTIVITY_LEVELS[i % 3]
        gender = "F" if (i % 2 == 0) else "M"

        # Secondary topic — pick something adjacent to main topic to
        # make each bot feel LIKE they have a coherent taste, not just
        # a random category grab.
        remaining_topics = [c for c in CATEGORY_IDS if c != main_topic]
        secondary_topic = remaining_topics[(i * 3) % len(remaining_topics)]
        third_topic = remaining_topics[(i * 7 + 4) % len(remaining_topics)]
        # 2-3 favorite categories — mimics the onboarding UX.
        favorite_categories = [main_topic, secondary_topic]
        if rng.random() < 0.5:
            favorite_categories.append(third_topic)

        region = regions_list[i % len(regions_list)]
        city = rng.choice(REGIONS_CITIES[region])
        profession = PROFESSIONS[(i * 11) % len(PROFESSIONS)]

        # Pick first name via per-gender rotating pool, surname via a
        # global rotating pool (unique across 100 bots).
        last_name = last_names_shuffled[i]
        if gender == "F":
            first_name = first_pool_f_shuf[f_counter % len(first_pool_f_shuf)]
            f_counter += 1
        else:
            first_name = first_pool_m_shuf[m_counter % len(first_pool_m_shuf)]
            m_counter += 1

        nickname = _unique_nick(rng, first_name, last_name, used_nicks)
        # Bot email lives on a reserved local domain so it cannot
        # collide with a real user AND cannot be reused for login.
        email = f"{nickname}@bot.populus.local"
        if email in used_emails:
            email = f"{nickname}{i}@bot.populus.local"
        used_emails.add(email)

        age = _age_for(activity, rng)
        tone = TONES[(i * 5) % len(TONES)]
        # gcd(17,5)=1 so verbosity now cycles all 5 buckets uniformly.
        verbosity = VERBOSITIES[(i * 17) % len(VERBOSITIES)]

        bio = _bio_for(rng, gender, [main_topic, secondary_topic])
        display_name = f"{first_name} {last_name}"

        # per-bot randomness weights for how often they act
        activity_probability = {
            "low": 0.18,
            "medium": 0.42,
            "high": 0.75,
        }[activity]

        comment_probability = {
            "low": 0.22,
            "medium": 0.40,
            "high": 0.60,
        }[activity]

        story_probability = {
            "low": 0.02,
            "medium": 0.05,
            "high": 0.10,
        }[activity]

        personas.append({
            "bot_index": i,
            "user_id": f"bot_{i:03d}",
            "nickname": nickname,
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "display_name": display_name,
            "age": age,
            "sex": gender,
            "region": region,
            "city": city,
            "profession": profession,
            "bio": bio,
            "favorite_categories": favorite_categories,
            "main_topic": main_topic,
            "secondary_topic": secondary_topic,
            "political_lean": political_lean,
            "party_bias": _party_bias(political_lean),
            "tone": tone,
            "verbosity": verbosity,
            "activity_level": activity,
            "activity_probability": activity_probability,
            "comment_probability": comment_probability,
            "story_probability": story_probability,
        })

    return personas


# Openers we do NOT want the bots to overuse. The LLM tends to fall
# back on the same 5-6 words ("Sinceramente", "Ecco", "Onestamente"…)
# regardless of prompt tweaks, so we blacklist them explicitly AND
# post-process the output. Add new ones here if you see repetition.
BANNED_OPENERS = [
    "sinceramente", "onestamente", "francamente", "personalmente",
    "ecco", "dai", "boh", "insomma", "allora",
    "in verità", "in tutta onestà", "a dire il vero", "a essere sinceri",
    "diciamocelo", "diciamola tutta", "secondo me",
    "ok", "va bene", "beh", "vabbè",
    "guarda", "senti", "ascolta",
    "il commento", "commento",
]

# Per-call opening style hints — one of these is injected on every LLM
# call so consecutive comments diverge naturally. The goal is not to
# force a specific opener, only to nudge Claude away from its "safe"
# starter word.
OPENING_STYLES = [
    "Inizia direttamente con un verbo all'indicativo (es. «penso che…», «trovo che…»).",
    "Inizia con un aggettivo che valuta la situazione (es. «ridicolo», «giusto», «assurdo»).",
    "Inizia con una domanda retorica.",
    "Inizia con un sostantivo o nome proprio, senza avverbio.",
    "Inizia con «ma…» o «però…» come contrasto.",
    "Inizia con un numero, un anno o una cifra.",
    "Inizia con una constatazione fattuale, senza premesse.",
    "Inizia con un'esclamazione secca (es. «follia», «bravo», «disastro»).",
    "Inizia con «se…» o «quando…» come ipotesi.",
    "Inizia con «io» seguito da un verbo, per portare un'esperienza personale.",
    "Inizia con «alla fine», «in fondo», o simili.",
    "Inizia con un verbo all'imperativo (es. «guardiamo», «pensiamo»).",
    "Inizia con una battuta o un'immagine visiva.",
    "Inizia con un dato o un paragone concreto.",
    "Inizia con un dubbio (es. «non sono sicur*», «non capisco perché»).",
    "Inizia con una negazione forte (es. «non è vero che», «non ci sto»).",
]

# Extra micro-quirks the LLM can pick from — chosen randomly per call.
STYLE_QUIRKS = [
    "Usa una frase spezzata con un punto in mezzo.",
    "Fai un paragone concreto con la vita quotidiana.",
    "Cita un dettaglio molto specifico del post.",
    "Chiudi con una domanda aperta.",
    "Chiudi con una constatazione tagliente.",
    "Usa un'espressione dialettale leggera solo se coerente con la regione.",
    "Evita ogni frase fatta.",
    "Non usare avverbi in -mente.",
]


def random_style_hint(
    rng: random.Random, base_verbosity: Optional[str] = None
) -> Dict[str, str]:
    """Return per-call style knobs: opening_style, style_quirk, and an
    OPTIONAL `verbosity_override`. The override is what gives real-user
    diversity — even a "medio" bot sometimes drops a 3-word reaction
    or a long rant.

    Sampling:
      * 65% keep the bot's own verbosity
      * 12% shift one bucket shorter
      * 12% shift one bucket longer
      * 6% jump to `micro` (very short reaction)
      * 5% jump to `lungo` / `verboso` (longer, articulated)
    """
    result = {
        "opening_style": rng.choice(OPENING_STYLES),
        "style_quirk": rng.choice(STYLE_QUIRKS),
    }
    if base_verbosity and base_verbosity in VERBOSITIES:
        idx = VERBOSITIES.index(base_verbosity)
        r = rng.random()
        if r < 0.65:
            override = None
        elif r < 0.77:
            override = VERBOSITIES[max(0, idx - 1)]
        elif r < 0.89:
            override = VERBOSITIES[min(len(VERBOSITIES) - 1, idx + 1)]
        elif r < 0.95:
            override = "micro"
        else:
            override = rng.choice(["lungo", "verboso"])
        if override and override != base_verbosity:
            result["verbosity_override"] = override
    return result


# ─── System prompt builder for the LLM ─────────────────────────────
def system_prompt_for(
    persona: Dict[str, Any], style_hint: Optional[Dict[str, str]] = None
) -> str:
    """Prompt engineered so Claude Haiku produces short, human-sounding
    Italian comments. The prompt is intentionally strict on style so
    the output doesn't read like a chatbot summary.
    """
    # style_hint may override verbosity for this specific comment so
    # even a "medio" bot can occasionally write a micro reaction or a
    # longer rant. See `random_style_hint()` for the sampling logic.
    effective_verbosity = (
        (style_hint or {}).get("verbosity_override") or persona.get("verbosity", "breve")
    )
    if effective_verbosity not in VERBOSITIES:
        effective_verbosity = "breve"

    verbosity_hint = {
        "micro": (
            "1-4 parole in tutto (max 25 caratteri). Una reazione secca, "
            f"nello stile di: {', '.join(f'«{s}»' for s in MICRO_SAMPLES[:6])}. "
            "Non spiegare, reagisci e basta."
        ),
        "breve": "1 sola frase molto breve (max 80 caratteri).",
        "medio": "1-2 frasi (max 160 caratteri totali).",
        "lungo": "2-3 frasi (max 260 caratteri totali).",
        "verboso": "3-4 frasi articolate (max 420 caratteri totali).",
    }[effective_verbosity]

    tone_hint = {
        "sarcastico": "tono sarcastico e ironico, con battute pungenti",
        "empatico": "tono comprensivo e caloroso",
        "cinico": "tono disincantato, un po' amareggiato",
        "entusiasta": "tono acceso, uso di punti esclamativi",
        "analitico": "tono lucido e ragionato, senza fronzoli",
        "giocoso": "tono leggero, con qualche battuta",
        "serio": "tono asciutto e diretto",
        "polemico": "tono provocatorio, cerca il confronto",
        "conciliante": "tono equilibrato, cerca il compromesso",
    }[persona["tone"]]

    lean_hint = {
        "sinistra_radicale": "convinzioni di sinistra radicale, anticapitalista",
        "sinistra": "convinzioni di sinistra, progressista",
        "centro_sinistra": "convinzioni di centro-sinistra, riformista",
        "centro": "convinzioni moderate, poco ideologiche",
        "centro_destra": "convinzioni di centro-destra, liberali",
        "destra": "convinzioni di destra, conservatrici",
        "destra_radicale": "convinzioni di destra radicale, sovraniste",
    }[persona["political_lean"]]

    opening_hint = (
        (style_hint or {}).get("opening_style")
        or "Inizia in modo naturale e vario, mai con avverbi di premessa."
    )
    quirk = (style_hint or {}).get("style_quirk") or "Rimani conciso e sincero."

    banned_list = ", ".join(f"«{w.capitalize()}»" for w in BANNED_OPENERS[:14])

    return (
        f"Sei {persona['display_name']}, {persona['age']} anni, {persona['profession'].lower()} "
        f"da {persona['city']} ({persona['region']}). "
        f"Su Populus (social italiano di scontri d'opinione) hai queste caratteristiche: "
        f"{tone_hint}; {lean_hint}. Ti appassiona soprattutto {TOPIC_LABELS_IT.get(persona['main_topic'], persona['main_topic'])}. "
        f"Devi scrivere UN commento sotto un post, come faresti su un social. Regole ferree: "
        f"1) Italiano informale, come parleresti in chat. "
        f"2) {verbosity_hint} "
        f"3) VIETATISSIMO iniziare il commento con una di queste parole/avverbi di premessa: "
        f"{banned_list}. Se pensi di dover partire da uno di questi, riformula. "
        f"4) {opening_hint} "
        f"5) {quirk} "
        f"6) NON riassumere il post, esprimi un'opinione personale. "
        f"7) NON usare hashtag né emoji. NON rivelare che sei un'IA. "
        f"8) Evita insulti personali, razzismo o incitamento all'odio. "
        f"9) Refusi minori e colloquialismi vanno bene, ma varia il vocabolario: ogni commento deve suonare diverso dagli altri. "
        f"Rispondi SOLO con il commento, senza virgolette e senza premesse tipo «Il mio commento:»."
    )


def story_prompt_for(persona: Dict[str, Any]) -> str:
    """Short caption for a story sharing a feud. Very short by nature.

    NB: gli input all'LLM ora includono il titolo/le fazioni del feud che
    il bot sta condividendo (vedi `_generate_story_caption`). Qui
    rinforziamo che la risposta e' UNA sola frase, NON una risposta a un
    interlocutore, per evitare output tipo "certo, ecco la frase:" o
    "non posso perche' non ho il post" (bug osservato).
    """
    return (
        f"Sei {persona['display_name']}. Stai condividendo su una story un post che ti ha colpito. "
        f"Scrivi UNA sola breve frase (max 60 caratteri) come commento personale che accompagna la story. "
        f"Tono: {persona['tone']}. In italiano informale, senza emoji ne' hashtag. "
        f"NON introdurre la risposta, NON fare domande, NON chiedere contesto: "
        f"emetti soltanto la frase finale che apparira' nella story."
    )
