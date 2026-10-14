"""Deterministic reply rules. They run BEFORE and INDEPENDENTLY of the LLM. Safety outcomes found here
(unsubscribe, objection to contact, data-protection request) are always acted on; the LLM can add safety outcomes
but can never remove one. Keyword lists cover en/fr/de/nl/es/it/pt and Nordic (nb/da/fi/sv) for the safety classes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

DpType = Literal["access", "erasure", "objection", "rectification", "restriction", "portability", "unclear"]
Flag = Literal["anger", "legal", "pricing", "contract", "complaint"]


def _rx(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(patterns), re.IGNORECASE)


UNSUBSCRIBE = _rx(
    r"\bunsubscribe\b",
    r"\bremove me\b",
    r"\btake me off\b",
    r"\bopt(?:-| )?out\b",
    r"\bno more e-?mails?\b",
    r"\bremove (?:my|this) (?:e-?mail|address|details)\b",
    r"\bdélist",
    r"désinscri",
    r"désabonn",
    r"ne (?:plus|pas) (?:me )?(?:recevoir|envoyer)",
    r"retirez[- ]moi",
    r"ne (?:souhaite|veux|désire|voudrais) plus (?:rien )?recevoir",
    r"ne plus m'(?:envoyer|écrire|adresser)",
    r"(?:me|nous) (?:retirer|supprimer|enlever) de (?:vos|votre|la|cette) (?:listes?|fichiers?|base)",
    r"\babmelden\b",
    r"\baustragen\b",
    r"keine (?:weiteren )?e-?mails",
    r"\buitschrijven\b",
    r"\bafmelden\b",
    r"darme de baja",
    r"\bdarse de baja\b",
    r"\bcancellami\b",
    r"disiscriv",
    r"\bstop\s*$",
    r"entfernen sie mich",
    r"aus (?:ihrem|dem|ihrer) (?:verteiler|liste|mailingliste)",
    r"\babbestellen\b",
    r"verwijder (?:mij|me)\b",
    r"\bquit(?:ar|en)me de (?:la|su) lista",
    r"elim[ií]n(?:enme|arme)",
    r"rimuovete?mi|rimuovermi",
    # Nordic: Norwegian (bokmål), Finnish, Swedish (Finland is bilingual)
    r"\bmeld (?:meg|oss) av\b",
    r"\bavmeld",
    r"(?:ønsker|vil) ikke (?:å )?(?:motta|få|ha) (?:flere|mer)",
    r"\bfjern (?:meg|oss) fra\b",
    r"slutt å sende",
    r"^\W*avslutt\W*$",
    r"poista(?:kaa|a)? (?:minut|meidät)",
    r"en halua enää (?:saada|vastaanottaa)",
    r"peru(?:utan|uta|ttakaa|n) (?:tilaus|tilauksen|postitus)|tilauksen peru",
    r"lopetta(?:kaa|a) (?:viestien|sähköpostien|postitus|lähettäminen)",
    r"älkää (?:enää )?lähettäkö",
    r"^\W*lopeta\W*$",
    r"avregistrera",
    r"vill inte (?:få|ha) (?:fler|mer)",
    r"ta bort (?:mig|oss) (?:från|ur)",
    r"sluta skicka",
    r"avsluta (?:min )?prenumeration",
    r"\bafmeld",
    r"\bframeld",
    r"ønsker ikke (?:at )?(?:modtage|få) (?:flere|mere)",
    r"\bfjern (?:mig|os) fra\b",
    r"stop med at sende",
    # Spanish, Italian, Portuguese, Dutch (Stage 4), measured on tests/evals/datasets/reply_eval_stage4.jsonl
    r"no (?:deseo|quiero) (?:recibir|seguir recibiendo) más",
    r"dejen? de (?:enviarme|mandarme|escribirme)",
    r"elimínenme|eliminarme de (?:su|sus|la|vuestra)",
    r"bórr(?:enme|arme) de",
    r"cancellatemi|cancellarmi (?:dalla|dalle|dai|da)",
    r"non (?:voglio|desidero) più ricevere",
    r"smettete di (?:inviarmi|mandarmi|scrivermi)",
    r"retirem-me|retirar-me d",
    r"não (?:quero|desejo) (?:receber|continuar a receber) mais",
    r"cancel(?:em|ar) a (?:minha )?subscrição",
    r"deixem de me (?:enviar|mandar)",
    r"remov(?:am|er)-me d",
    r"geen e-?mails? meer",
    r"haal (?:mij|me|ons) (?:van|uit)",
    r"stop met (?:mij|me|ons) te (?:mailen|e-mailen|sturen)",
)
OBJECTION = _rx(
    r"\b(?:do not|don't|dont|never) (?:e-?mail|contact|message|write to) (?:me|us)\b",
    r"\bstop (?:e-?mailing|contacting|messaging|sending)\b",
    r"\bnot contact (?:me|us) again\b",
    r"\bleave (?:me|us) alone\b",
    r"ne (?:me|nous) contactez plus",
    r"ne plus (?:me|nous) (?:contacter|solliciter|relancer|écrire)",
    r"(?:arrêtez|cessez) de (?:me|nous) (?:contacter|solliciter|relancer|écrire)",
    r"nicht (?:mehr )?kontaktieren",
    r"neem geen contact",
    r"no (?:me|nos) contacte",
    r"non contattarmi",
    r"ikke kontakt (?:meg|oss)",
    r"(?:ikke|aldri) (?:å )?kontakte (?:meg|oss)",
    r"slutt å kontakte",
    r"älkää (?:enää )?ottako (?:minuun|meihin)(?: enää)? yhteyttä",
    r"ettette (?:enää )?ota (?:minuun|meihin) yhteyttä",
    r"lopettakaa yhteydenot",
    r"kontakta (?:mig|oss) inte",
    r"kontakta inte (?:mig|oss)",
    r"sluta kontakta",
    r"kontakt (?:mig|os) ikke",
    r"ikke (?:at )?kontakte (?:mig|os)",
    r"stop med at kontakte",
    r"no vuelvan a contactar(?:me|nos)",
    r"dejen de contactar(?:me|nos)",
    r"non contattatemi",
    r"di non contattar(?:mi|ci)",
    r"smettete di contattar",
    r"não (?:me|nos) contactem",
    r"não voltem a contactar",
    r"deixem de (?:me|nos) contactar",
    r"niet meer (?:te )?contacteren",
    r"geen contact meer",
)
DP_REQUEST = _rx(
    r"\bgdpr\b",
    r"\bdata protection\b",
    r"\bsubject access\b",
    r"\bsar\b",
    r"\bdsar\b",
    r"what (?:personal )?(?:data|information|details) (?:do )?you (?:hold|have|keep|store)",
    r"(?:where|how) did you (?:get|obtain|find) (?:my|our) (?:details|data|information|e-?mail|address)",
    r"\b(?:delete|erase|remove) (?:all )?(?:of )?(?:my|our) (?:personal )?(?:data|details|information)\b",
    r"\b(?:delete|erase|wipe|destroy)\b[^.?!\n]{0,40}\b(?:data|details|information|records)\b[^.?!\n]{0,40}"
    r"\b(?:me|my|us|our)\b",
    r"\b(?:data|information|details) you (?:hold|have|keep|store) (?:on|about) (?:me|us)\b",
    r"right to (?:be forgotten|erasure|object|access|rectification)",
    r"\barticle (?:15|16|17|18|20|21)\b",
    r"\bi object to\b",
    r"\bobject to (?:the )?processing\b",
    r"\brgpd\b",
    r"droit d'acc[eè]s",
    r"supprimer? (?:mes|nos) donn[ée]es",
    r"\bdsgvo\b",
    r"\bauskunft\b",
    r"\blöschung\b",
    r"datenschutz",
    r"\bavg\b",
    r"\brectif",
    r"\bcorrect (?:my|our) (?:data|details)\b",
    r"\bportability\b",
    r"\brestrict (?:the )?processing\b",
    r"personopplysning",
    r"\binnsyn\b",
    r"personvern",
    r"hvor har dere fått",
    r"\bslett(?:e)? (?:alle|mine|våre)\b",
    r"henkilötie",
    r"tietosuoj",
    r"tarkastusoikeu",
    r"mistä (?:olette )?saa(?:neet|tte|itte)",
    r"tietojeni poist",
    r"personuppgift",
    r"registerutdrag",
    r"var har ni fått",
    r"\bradera (?:alla|mina|våra)\b",
    r"personoplysning",
    r"\bindsigt\b",
    r"hvor har I fået",
    r"\bslet (?:alle|mine|vores)\b",
    r"datos personales",
    r"supresión de (?:todos )?(?:mis|nuestros) datos",
    r"derecho de (?:acceso|supresión|oposición|rectificación)",
    r"de dónde (?:han )?(?:obtenido|sacado)",
    r"dati personali",
    r"cancellazione (?:di )?(?:tutti )?(?:i )?(?:miei|nostri) dati",
    r"da dove avete (?:preso|ottenuto)",
    r"diritto di (?:accesso|cancellazione|opposizione|rettifica)",
    r"dados pessoais",
    r"\bapagamento\b",
    r"onde obtiveram",
    r"direito (?:de|ao) (?:acesso|apagamento|oposição|retificação)",
    r"persoonsgegevens",
    r"\binzage\b",
    r"waar heeft u (?:mijn|onze) gegevens",
    r"verwijder (?:al )?(?:mijn|onze) gegevens",
)
DP_TYPES: list[tuple[DpType, re.Pattern[str]]] = [
    (
        "erasure",
        _rx(
            r"\b(?:delete|erase|remove|wipe|destroy)\b.*\b(?:data|details|information|records)\b",
            r"forgotten",
            r"\berasure\b",
            r"supprimer",
            r"löschung",
            r"\barticle 17\b",
            r"\bslett",
            r"\bslet\b",
            r"supresión",
            r"cancellazione",
            r"apagamento",
            r"verwijder",
            r"poist",
            r"\bradera\b",
        ),
    ),
    (
        "access",
        _rx(
            r"what (?:personal )?(?:data|information|details)",
            r"where did you",
            r"how did you",
            r"subject access",
            r"\bd?sar\b",
            r"\barticle 15\b",
            r"auskunft",
            r"droit d'acc",
            r"innsyn",
            r"indsigt",
            r"acceso|accesso|acesso",
            r"inzage",
            r"de dónde|da dove|onde obtiveram|waar heeft u",
            r"hvor har I",
            r"hvor har dere",
            r"tarkastus",
            r"mistä",
            r"registerutdrag",
            r"var har ni",
        ),
    ),
    ("objection", _rx(r"\bobject\b", r"\barticle 21\b")),
    ("rectification", _rx(r"rectif", r"\bcorrect\b", r"\barticle 16\b")),
    ("restriction", _rx(r"restrict", r"\barticle 18\b")),
    ("portability", _rx(r"portab", r"\barticle 20\b")),
]
OOO = _rx(
    r"out of (?:the )?office",
    r"automatic reply",
    r"auto-?reply",
    r"\bannual leave\b",
    r"on holiday",
    r"away (?:until|from)",
    r"\bback on\b",
    r"returning on",
    r"absence du bureau",
    r"absente? du bureau",
    r"réponse automatique",
    r"message d'absence",
    r"\ben congés?\b",
    r"de retour (?:le|lundi|mardi|mercredi|jeudi|vendredi|à partir)",
    r"\babwesend\b",
    r"\bafwezig\b",
    r"fuera de la oficina",
    r"fuori ufficio",
    r"automatisk svar",
    r"\bpå ferie\b",
    r"respuesta automática",
    r"risposta automatica",
    r"resposta automática",
    r"ausente do escritório",
    r"automatisch antwoord",
    r"\bfraværende\b",
    r"automaattinen vastaus",
    r"\blomalla\b",
    r"poissa toimisto",
    r"\bautosvar\b",
    r"\bpå semester\b",
    r"\bfrånvarande\b",
    r"\bfraværende\b",
    r"\bpå ferie\b",
)
REFERRAL = _rx(
    r"not the right person",
    r"wrong person",
    r"\bspeak (?:to|with)\b",
    r"\btalk to\b",
    r"\breach out to\b",
    r"\bcontact (?:my colleague|my team|our)\b",
    r"better person",
    r"\bhandles? (?:this|that)\b",
    r"\bis responsible for\b",
    r"\bforwarded (?:this|your email) to\b",
    r"ikke (?:den )?rette? person",
    r"\bsnakk med\b",
    r"oikea henkilö",
    r"inte (?:den )?rätta? person",
    r"\bprata med\b",
    r"ikke den rette person",
    r"\btal med\b",
    r"no soy la persona (?:adecuada|indicada|correcta)",
    r"\bhable con\b",
    r"non sono la persona giusta",
    r"\bparli con\b",
    r"não sou a pessoa (?:certa|indicada)",
    r"\bfale com\b",
    r"niet de juiste persoon",
    r"\bspreek met\b",
    r"ce n'est pas moi qui",
    r"contactez plutôt",
    r"je transmets à",
)
FLAGS: dict[Flag, re.Pattern[str]] = {
    "anger": _rx(
        r"\bridiculous\b",
        r"\bharass",
        r"\bspam(?:ming)?\b",
        r"how dare",
        r"\bannoying\b",
        r"sick of",
        r"\bf\*+",
        r"\bfuck",
        r"\bpiss",
        r"\bscam\b",
    ),
    "legal": _rx(
        r"\bsolicitor",
        r"\blawyer",
        r"legal action",
        r"\bico\b",
        r"report you",
        r"\bcourt\b",
        r"\bsue\b",
        r"\bcnil\b",
        r"\btribunal\b",
    ),
    "complaint": _rx(r"\bcomplain", r"\bcomplaint"),
    "pricing": _rx(r"\bprice", r"\bpricing\b", r"\bcost", r"how much", r"\brates?\b", r"\bquote\b", r"\bfee"),
    "contract": _rx(r"\bcontract", r"\bterms\b", r"\bsla\b", r"\bagreement\b", r"\bmsa\b", r"notice period"),
}
AI_QUESTION = _rx(
    r"are you (?:a )?(?:bot|robot|real person|human|an? ai|automated)",
    r"is this (?:automated|ai|a bot)",
    r"written by (?:an? )?(?:ai|bot|machine)",
    r"\bchatgpt\b",
)
RESCHEDULE = _rx(
    r"\breschedul",
    r"\bmove (?:the|our) (?:meeting|call)\b",
    r"different time",
    r"can(?:'t|not) make",
    r"push (?:it|the call|the meeting) back",
    r"another (?:day|time)",
)
CONFIRM = _rx(r"see you (?:then|on|there)", r"\bconfirmed?\b", r"\baccepted\b", r"looking forward to (?:it|the call)")
INTERESTED = _rx(
    r"\binterested\b",
    r"sounds good",
    r"happy to",
    r"\bkeen\b",
    r"send (?:it|me|over|that)",
    r"tell me more",
    r"works for me",
    r"would be useful",
    r"please do\b(?!n)",
    r"\blet'?s\b",
    r"\byes\b",
    r"\bsure\b",
    r"\bgo ahead\b",
    r"option\s*[1-5]",
    r"\bgjerne\b",
    r"høres interessant",
    r"kiinnostava",
    r"sopii (?:hyvin|meille)",
    r"\bgärna\b",
    r"passar bra",
    r"\bja tak\b",
    r"passer (?:fint|godt)",
    r"me interesa",
    r"\bhablamos\b",
    r"mi interessa",
    r"\bsentiamoci\b",
    r"tenho interesse",
    r"podemos falar",
    r"\binteressant\b",
    r"zullen we",
    r"m'intéresse",
    r"\bvolontiers\b",
    r"avec plaisir",
    r"\bintéressant\b",
    r"envoyez-moi",
)
NOT_NOW = _rx(
    r"not (?:right )?now",
    r"not at the moment",
    r"maybe later",
    r"next year",
    r"in a few months",
    r"circle back",
    r"reach out (?:again )?in",
    r"after (?:the )?(?:summer|christmas|new year)",
    r"\bq[1-4]\b",
    r"ikke (?:akkurat )?nå\b",
    r"etter (?:sommeren|jul|nyttår)",
    r"ei juuri nyt",
    r"palataan asiaan",
    r"inte just nu",
    r"efter (?:sommaren|jul|årsskiftet)",
    r"ikke lige nu",
    r"efter sommerferien",
    r"ahora (?:mismo )?no\b",
    r"después del verano",
    r"non adesso",
    r"dopo l'estate",
    r"agora não",
    r"depois do verão",
    r"nu even niet",
    r"na de zomer",
    r"pas (?:pour le|en ce) moment",
    r"revenez vers moi",
    r"reparlerons",
    r"(?:premier|deuxième|second|troisième|quatrième) trimestre",
    r"timing n'est pas bon",
)
NEGATIVE = _rx(
    r"not interested",
    r"no,? thanks?",
    r"no thank you",
    r"we(?:'re| are) (?:fine|all set|covered)",
    r"not relevant",
    r"not for us",
    r"\bno need\b",
    r"\bnei,? takk\b",
    r"ikke aktuelt",
    r"\bei kiitos\b",
    r"ei (?:ole )?(?:meille )?ajankohtai",
    r"\bnej,? tack\b",
    r"inte intresserade?",
    r"\bnej,? tak\b",
    r"ikke relevant",
    r"no me interesa",
    r"\bno,? gracias\b",
    r"no es para nosotros",
    r"non (?:mi|ci) interessa",
    r"\bno,? grazie\b",
    r"não (?:me |nos )?interessa",
    r"\bnão,? obrigad[oa]\b",
    r"não é para nós",
    r"\bnee,? bedankt\b",
    r"niets voor ons",
    r"\bnon,? merci\b",
    r"pas pour nous",
    r"pas besoin",
)
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
NAME_AFTER = re.compile(
    r"(?:speak (?:to|with)|talk to|reach out to|contact|colleague)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)"
)
MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ],
        1,
    )
}
MON3 = {k[:3]: v for k, v in MONTHS.items()}
DATE_WORDS = re.compile(
    r"(?:until|back on|returning(?: on)?|return on|from|back)\s+(?:\w+day,?\s+)?(\d{1,2})(?:st|nd|rd|th)?"
    r"\s+(?:of\s+)?([A-Za-z]{3,9})(?:\s+(\d{4}))?",
    re.IGNORECASE,
)
DATE_NUM = re.compile(
    r"(?:until|back on|returning(?: on)?|return on|back)\s+(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?", re.IGNORECASE
)
STOPWORDS = {
    "en": {"the", "and", "you", "is", "to", "we", "for", "please", "thanks", "not", "this", "with", "our", "my"},
    "fr": {
        "le",
        "la",
        "les",
        "et",
        "vous",
        "nous",
        "pour",
        "merci",
        "pas",
        "je",
        "est",
        "une",
        "des",
        "ne",
        "mes",
        "oui",
        "ça",
        "avec",
        "sur",
        "au",
        "du",
        "semaine",
        "c'est",
        "parlons",
    },
    "no": {"jeg", "ikke", "og", "dere", "meg", "oss", "takk", "er", "på", "til", "med", "fra", "vi", "det"},
    "fi": {"ja", "ei", "en", "olen", "minut", "kiitos", "teiltä", "on", "tämä", "meille", "olla", "asti", "nyt"},
    "sv": {"jag", "inte", "och", "ni", "mig", "oss", "tack", "är", "på", "till", "med", "från", "vi", "er"},
    "da": {"jeg", "ikke", "og", "jer", "mig", "os", "tak", "er", "på", "til", "med", "fra", "vi", "jeres"},
    "de": {"der", "die", "das", "und", "sie", "wir", "bitte", "nicht", "ich", "ist", "mit", "danke", "keine", "mich"},
    "nl": {"de", "het", "en", "u", "wij", "niet", "ik", "is", "bedankt", "een", "met", "geen", "mij"},
    "es": {"el", "la", "los", "y", "usted", "nosotros", "para", "gracias", "no", "yo", "es", "una", "por", "me"},
    "it": {"il", "la", "gli", "e", "lei", "noi", "per", "grazie", "non", "io", "una", "di", "mi", "che"},
    "pt": {"o", "a", "os", "e", "não", "obrigado", "obrigada", "nós", "para", "de", "me", "que", "vossa", "sou", "uma"},
}


@dataclass
class RuleResult:
    safety: Literal["unsubscribe", "objection", "data_protection_request"] | None = None
    dp_type: DpType | None = None
    out_of_office: bool = False
    ooo_return_date: date | None = None
    referral_email: str | None = None
    referral_name: str | None = None
    referral_hint: bool = False
    flags: list[Flag] = field(default_factory=list)
    asks_if_ai: bool = False
    wants_reschedule: bool = False
    confirms: bool = False
    interested_hint: bool = False
    not_now_hint: bool = False
    negative_hint: bool = False
    language: str = "unknown"
    matched: list[str] = field(default_factory=list)  # rule names only (no PII), stored on the reply


def detect_language(text: str) -> str:
    words = re.findall(r"[a-zà-ÿß']+", text.lower())
    scores = {lang: sum(1 for w in words if w in sw) for lang, sw in STOPWORDS.items()}
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] >= 2 else "unknown"


def _date(day: int, month: int, year: int | None, received: date) -> date | None:
    try:
        d = date(year or received.year, month, day)
    except ValueError:
        return None
    if year is None and d < received - timedelta(days=7):
        d = date(d.year + 1, month, day)
    return d


def extract_return_date(text: str, received: date) -> date | None:
    for m in DATE_WORDS.finditer(text):
        month = MONTHS.get(m.group(2).lower()) or MON3.get(m.group(2).lower()[:3])
        if month:
            y = int(m.group(3)) if m.group(3) else None
            d = _date(int(m.group(1)), month, y, received)
            if d:
                return d
    for m in DATE_NUM.finditer(text):
        ys: str | None = m.group(3)
        year = (int(ys) + 2000 if len(ys) == 2 else int(ys)) if ys else None
        d = _date(int(m.group(1)), int(m.group(2)), year, received)  # UK/EU: day first
        if d:
            return d
    return None


def apply_rules(text: str, received: date, sender_email: str | None = None) -> RuleResult:
    r = RuleResult(language=detect_language(text))
    if DP_REQUEST.search(text):
        r.safety, r.dp_type = "data_protection_request", next((t for t, rx in DP_TYPES if rx.search(text)), "unclear")
        r.matched.append(f"dp_request:{r.dp_type}")
    elif OBJECTION.search(text):
        r.safety = "objection"
        r.matched.append("objection")
    elif UNSUBSCRIBE.search(text):
        r.safety = "unsubscribe"
        r.matched.append("unsubscribe")
    if OOO.search(text):
        r.out_of_office = True
        r.ooo_return_date = extract_return_date(text, received)
        r.matched.append("out_of_office")
    emails = [e for e in EMAIL.findall(text) if not sender_email or e.lower() != sender_email.lower()]
    if REFERRAL.search(text):
        r.referral_hint = True
        r.referral_email = emails[0] if emails else None
        m = NAME_AFTER.search(text)
        r.referral_name = m.group(1) if m else None
        r.matched.append("referral")
    r.flags = [f for f, rx in FLAGS.items() if rx.search(text)]
    r.matched += [f"flag:{f}" for f in r.flags]
    r.asks_if_ai = bool(AI_QUESTION.search(text))
    r.wants_reschedule = bool(RESCHEDULE.search(text))
    r.confirms = bool(CONFIRM.search(text))
    r.negative_hint = bool(NEGATIVE.search(text))
    r.not_now_hint = bool(NOT_NOW.search(text)) and not r.negative_hint
    r.interested_hint = bool(INTERESTED.search(text)) and not r.negative_hint
    return r


def parse_slot_choice(text: str, labels: list[str]) -> int | None:
    """Returns a 0-based index into the offered slots, or None if the choice is not unambiguous."""
    t = text.lower()
    m = re.search(r"option\s*(\d)", t)
    if m and 1 <= int(m.group(1)) <= len(labels):
        return int(m.group(1)) - 1
    ordinals = [("first", "1st"), ("second", "2nd"), ("third", "3rd"), ("fourth", "4th"), ("fifth", "5th")]
    found = {i for i, words in enumerate(ordinals[: len(labels)]) for w in words if re.search(rf"\b{w}\b", t)}
    if len(found) == 1 and not re.search(r"\bfirst (?:of all|thing)\b", t):
        return found.pop()
    hits = [i for i, lab in enumerate(labels) if lab.split(" at ")[0].lower() in t]
    if len(hits) == 1:
        return hits[0]
    days = [i for i, lab in enumerate(labels) if lab.split()[0].lower() in t]
    return days[0] if len(days) == 1 else None
