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
from typing import List, Dict, Any

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

VERBOSITIES = ["breve", "medio", "verboso"]

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
    "Rossi", "Ferrari", "Russo", "Bianchi", "Romano", "Colombo", "Ricci",
    "Marino", "Greco", "Bruno", "Gallo", "Conti", "De Luca", "Costa",
    "Giordano", "Mancini", "Rizzo", "Lombardi", "Moretti", "Barbieri",
    "Fontana", "Santoro", "Mariani", "Rinaldi", "Caruso", "Ferrara",
    "Galli", "Martini", "Leone", "Longo", "Gentile", "Serra", "Vitale",
    "Marchetti", "Parisi", "Villa", "Guerra", "Battaglia", "Sartori",
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

        first_pool = FIRST_NAMES_F if gender == "F" else FIRST_NAMES_M
        first_name = first_pool[i % len(first_pool)]
        last_name = LAST_NAMES[(i * 13) % len(LAST_NAMES)]

        nickname = _unique_nick(rng, first_name, last_name, used_nicks)
        # Bot email lives on a reserved local domain so it cannot
        # collide with a real user AND cannot be reused for login.
        email = f"{nickname}@bot.populus.local"
        if email in used_emails:
            email = f"{nickname}{i}@bot.populus.local"
        used_emails.add(email)

        age = _age_for(activity, rng)
        tone = TONES[(i * 5) % len(TONES)]
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


# ─── System prompt builder for the LLM ─────────────────────────────
def system_prompt_for(persona: Dict[str, Any]) -> str:
    """Prompt engineered so Claude Haiku produces short, human-sounding
    Italian comments. The prompt is intentionally strict on style so
    the output doesn't read like a chatbot summary.
    """
    verbosity_hint = {
        "breve": "1 sola frase molto breve (max 80 caratteri).",
        "medio": "1-2 frasi (max 160 caratteri totali).",
        "verboso": "2-3 frasi (max 260 caratteri totali).",
    }[persona["verbosity"]]

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

    return (
        f"Sei {persona['display_name']}, {persona['age']} anni, {persona['profession'].lower()} "
        f"da {persona['city']} ({persona['region']}). "
        f"Su Populus (social italiano di scontri d'opinione) hai queste caratteristiche: "
        f"{tone_hint}; {lean_hint}. Ti appassiona soprattutto {TOPIC_LABELS_IT.get(persona['main_topic'], persona['main_topic'])}. "
        f"Devi scrivere UN commento sotto un post, come faresti su un social. Regole ferree: "
        f"1) Scrivi in italiano informale, come parleresti in chat. "
        f"2) {verbosity_hint} "
        f"3) NON iniziare con la parola 'Ecco' o simili. "
        f"4) NON riassumere il post, esprimi un'opinione personale. "
        f"5) NON usare hashtag né emoji. "
        f"6) NON rivelare che sei un'IA. "
        f"7) Evita insulti personali, razzismo o incitamento all'odio. "
        f"8) Puoi usare colloquialismi (tipo 'sinceramente', 'boh', 'dai'), refusi minori sono ok. "
        f"Rispondi SOLO con il commento, senza virgolette."
    )


def story_prompt_for(persona: Dict[str, Any]) -> str:
    """Short caption for a story sharing a feud. Very short by nature."""
    return (
        f"Sei {persona['display_name']}. Stai condividendo su una story un post che ti ha colpito. "
        f"Scrivi UNA breve frase (max 60 caratteri) come commento alla story. "
        f"Tono: {persona['tone']}. In italiano informale, senza emoji né hashtag. "
        f"Rispondi solo con la frase."
    )
