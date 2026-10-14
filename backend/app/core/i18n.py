"""Fixed texts PipelineOS sends or shows to prospects (slot offers, confirmations, reminders, the public unsubscribe
and reschedule pages), per language. The AI-written emails follow the client's language through their prompts; these
are the deterministic ones. French uses formal "vous". Unknown languages fall back to British English.

PLACEHOLDER wording: like the footers, this is reviewed by a native speaker / counsel before a country is enabled."""

from __future__ import annotations

from datetime import datetime

DEFAULT = "en-GB"

CATALOGUE: dict[str, dict[str, str]] = {
    "en-GB": {
        "greeting": "Hi {name},",
        "slot_intro": "Thanks for coming back to me. Would any of these times work for a short call?",
        "slot_intro_taken": "Sorry, that time has just been taken. Would one of these work instead?",
        "slot_intro_other": "No problem. Would any of these times work instead?",
        "slot_option": "Option {i}: {label}",
        "slot_close": "Just reply with the option that suits you and I will send an invite for {minutes} minutes.",
        "confirm_booked": "Thanks, that is booked",
        "confirm_moved": "Your call has been moved",
        "confirm_body": "{lead}: {when}, {minutes} minutes. A calendar invite is on its way.",
        "agenda": "Agenda:",
        "reschedule_link": "If the time stops working, you can pick another here:",
        "confirm_subject": "Confirmed: {title}, {when}",
        "reminder_tomorrow": "tomorrow",
        "reminder_shortly": "shortly",
        "reminder_body": "A quick reminder of our call {soon}: {when}.",
        "reminder_close": "If the time no longer works, just reply and I will find another.",
        "reminder_subject": "Reminder: {title}, {when}",
        "reply_subject": "Re: {subject}",
        "reply_subject_default": "our conversation",
        "date": "{weekday} {day} {month} at {time} ({zone})",
        # public pages
        "page_unsub_title": "Unsubscribe",
        "page_unsub_intro": "Click below to stop all further emails from us.",
        "page_unsub_button": "Unsubscribe",
        "page_unsub_done_title": "You have been unsubscribed",
        "page_unsub_done": "You will not receive further emails from us. This applies to every campaign we run.",
        "page_link_invalid_title": "Link not valid",
        "page_unsub_invalid": "This unsubscribe link is not valid. Reply to the email with \u201cunsubscribe\u201d and "
        "we will remove you.",
        "page_unsub_unknown": "This unsubscribe link is not valid.",
        "page_wait_title": "Please wait",
        "page_too_many": "Too many requests from your network. Wait a minute and try again.",
        "page_resched_title": "Choose a new time",
        "page_resched_button": "Move my call",
        "page_resched_invalid": "This link is no longer valid. Reply to your confirmation email and we will find "
        "another time.",
        "page_no_times_title": "No free times",
        "page_no_times": "There are no free times right now. Reply to your confirmation email.",
        "page_time_na_title": "Time not available",
        "page_bad_request": "That request was not understood.",
        "page_time_taken": "That time is no longer available. Go back and pick another.",
        "page_time_failed": "That time could not be booked. Reply to your confirmation email.",
        "page_moved_title": "Your call has moved",
        "page_moved": "Your call is now on {when}. A new confirmation is on its way.",
        "page_contact_title": "We will be in touch",
        "page_contact": "A member of the team will contact you to find a time.",
    },
    "fr-FR": {
        "greeting": "Bonjour {name},",
        "slot_intro": "Merci pour votre retour. L'un de ces créneaux vous conviendrait-il pour un court échange ?",
        "slot_intro_taken": "Désolé, ce créneau vient d'être pris. L'un de ceux-ci vous conviendrait-il ?",
        "slot_intro_other": "Aucun problème. L'un de ces créneaux vous conviendrait-il ?",
        "slot_option": "Option {i} : {label}",
        "slot_close": "Il vous suffit de répondre avec l'option qui vous convient et je vous enverrai une invitation "
        "de {minutes} minutes.",
        "confirm_booked": "Merci, c'est réservé",
        "confirm_moved": "Votre rendez-vous a été déplacé",
        "confirm_body": "{lead} : {when}, {minutes} minutes. Une invitation d'agenda va vous parvenir.",
        "agenda": "Ordre du jour :",
        "reschedule_link": "Si l'horaire ne vous convient plus, vous pouvez en choisir un autre ici :",
        "confirm_subject": "Confirmé : {title}, {when}",
        "reminder_tomorrow": "demain",
        "reminder_shortly": "tout à l'heure",
        "reminder_body": "Petit rappel de notre échange {soon} : {when}.",
        "reminder_close": "Si l'horaire ne vous convient plus, répondez simplement et je vous en proposerai un autre.",
        "reminder_subject": "Rappel : {title}, {when}",
        "reply_subject": "Re : {subject}",
        "reply_subject_default": "notre échange",
        "date": "{weekday} {day} {month} à {time} ({zone})",
        "page_unsub_title": "Désinscription",
        "page_unsub_intro": "Cliquez ci-dessous pour ne plus recevoir aucun e-mail de notre part.",
        "page_unsub_button": "Me désinscrire",
        "page_unsub_done_title": "Vous êtes désinscrit(e)",
        "page_unsub_done": "Vous ne recevrez plus d'e-mails de notre part. Cela vaut pour toutes nos campagnes.",
        "page_link_invalid_title": "Lien non valide",
        "page_unsub_invalid": "Ce lien de désinscription n'est pas valide. Répondez à l'e-mail avec « désinscription » "
        "et nous vous retirerons de nos envois.",
        "page_unsub_unknown": "Ce lien de désinscription n'est pas valide.",
        "page_wait_title": "Veuillez patienter",
        "page_too_many": "Trop de demandes depuis votre réseau. Patientez une minute puis réessayez.",
        "page_resched_title": "Choisir un nouvel horaire",
        "page_resched_button": "Déplacer mon rendez-vous",
        "page_resched_invalid": "Ce lien n'est plus valide. Répondez à votre e-mail de confirmation et nous trouverons "
        "un autre horaire.",
        "page_no_times_title": "Aucun créneau libre",
        "page_no_times": "Aucun créneau n'est libre pour le moment. Répondez à votre e-mail de confirmation.",
        "page_time_na_title": "Horaire indisponible",
        "page_bad_request": "Cette demande n'a pas été comprise.",
        "page_time_taken": "Cet horaire n'est plus disponible. Revenez en arrière et choisissez-en un autre.",
        "page_time_failed": "Cet horaire n'a pas pu être réservé. Répondez à votre e-mail de confirmation.",
        "page_moved_title": "Votre rendez-vous a été déplacé",
        "page_moved": "Votre rendez-vous est désormais le {when}. Une nouvelle confirmation va vous parvenir.",
        "page_contact_title": "Nous revenons vers vous",
        "page_contact": "Un membre de l'équipe vous contactera pour trouver un horaire.",
    },
}

WEEKDAYS = {
    "en-GB": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "fr-FR": ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"],
}
MONTHS = {
    "en-GB": [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ],
    "fr-FR": [
        "janvier",
        "février",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "août",
        "septembre",
        "octobre",
        "novembre",
        "décembre",
    ],
}
ZONES = {
    "en-GB": {
        "Europe/London": "UK time",
        "Europe/Dublin": "Irish time",
        "Europe/Paris": "Paris time",
        "Europe/Berlin": "Berlin time",
        "Europe/Amsterdam": "Amsterdam time",
        "Europe/Oslo": "Oslo time",
        "Europe/Helsinki": "Helsinki time",
        "Europe/Stockholm": "Stockholm time",
    },
    "fr-FR": {
        "Europe/London": "heure du Royaume-Uni",
        "Europe/Dublin": "heure de Dublin",
        "Europe/Paris": "heure de Paris",
        "Europe/Berlin": "heure de Berlin",
        "Europe/Amsterdam": "heure d'Amsterdam",
        "Europe/Oslo": "heure d'Oslo",
        "Europe/Helsinki": "heure d'Helsinki",
        "Europe/Stockholm": "heure de Stockholm",
    },
}


def lang(code: str | None) -> str:
    """Normalise a language tag to a supported catalogue: 'fr', 'fr-BE' -> 'fr-FR'; anything else -> en-GB."""
    c = (code or "").strip()
    if c in CATALOGUE:
        return c
    return "fr-FR" if c.lower().startswith("fr") else DEFAULT


def t(code: str | None, key: str, **values: object) -> str:
    return CATALOGUE[lang(code)][key].format(**values)


def date_label(local: datetime, tz: str, code: str | None) -> str:
    lg = lang(code)
    time = f"{local:%H:%M}" if lg == DEFAULT else f"{local:%H}h{local:%M}"
    return t(
        lg,
        "date",
        weekday=WEEKDAYS[lg][local.weekday()],
        day=local.day,
        month=MONTHS[lg][local.month - 1],
        time=time,
        zone=ZONES[lg].get(tz, tz),
    )


def from_accept_language(header: str | None) -> str:
    """Public pages: the visitor's browser language, highest preference first (only en-GB and fr-FR exist)."""
    best, best_q = DEFAULT, -1.0
    for part in (header or "").split(","):
        bits = part.strip().split(";")
        tag = bits[0].strip().lower()
        if not tag:
            continue
        q = 1.0
        for b in bits[1:]:
            if b.strip().startswith("q="):
                try:
                    q = float(b.strip()[2:])
                except ValueError:
                    q = 0.0
        if q > best_q and (tag.startswith("fr") or tag.startswith("en")):
            best, best_q = ("fr-FR" if tag.startswith("fr") else DEFAULT), q
    return best
