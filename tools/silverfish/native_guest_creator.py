"""Validate the portable selection from the stock co-op character editor."""

import re


_RECIPE = re.compile(r"kit[0-4]|preset_[A-Za-z0-9_]{1,64}")
_CHOICE = re.compile(r"[A-Za-z0-9_]{1,80}|-")
_TRAIT = re.compile(r"[A-Za-z0-9_]{1,64}")
_PITCH = re.compile(r"[0-3]\.[0-9]{4}")


def valid_profile(value):
    if not isinstance(value, str) or len(value) > 1000:
        return False
    fields = value.split("|")
    if len(fields) != 7:
        return False
    recipe, voice, pitch, stance, armor, patch, traits = fields
    traits = traits.split(",") if traits else ()
    return bool(_RECIPE.fullmatch(recipe) and _PITCH.fullmatch(pitch) and float(pitch) <= 3
                and all(_CHOICE.fullmatch(part) for part in (voice, stance, armor, patch))
                and len(traits) <= 16 and all(_TRAIT.fullmatch(trait) for trait in traits))
