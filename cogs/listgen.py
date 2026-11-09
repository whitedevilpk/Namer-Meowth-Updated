"""
listgen.py  –  Pokémon List Generator cog (IMPROVED)
=====================================================

Commands
--------
  p!listgen  [reply-to-message]
      Open the list-builder UI.
      If used as a reply the bot extracts Pokémon names from the target
      message immediately and listens for edits for 2 minutes
      (timer resets on every edit).

Features
--------
  ➕ Add Pokémon        – modal with 4 filter-input cells
  ➖ Remove             – same filter syntax, removes matching names
  📋 Format & Display   – dropdown menu with format, case, language options
  🎚️ Advanced Options   – enclosure, replacement, event pokemon control
  📤 Send              – outputs list with backtick wrapping
  🗑️ Clear             – wipe the current list

Filter syntax  (used in Add / Remove modals)
--------------------------------------------
  --type / --t   <type>
  --region / --r <region>
  --sr / --spawnrate <denom>      e.g. --sr 225
  --stage <1|2|3>                 requires data/stagelist.csv
  --name / --n   [all] <name>     "all" adds all forms of that Pokémon
  --catchable                     only Pokémon present in spawnrate data
  --notcatchable                  only Pokémon absent from spawnrate data
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import List, Optional

import discord
from discord.ext import commands

from config import EMBED_COLOR
from listgen_data import (
    ALL_NAMES_ORDERED,
    NAME_TO_ROW,
    NAME_TO_SR,
    SPAWNRATE_DATA,
    POKEMON_DATA,
    EVENT_DATA,
    BEST_NAMES,
    apply_filters,
)

NO_MENTIONS = discord.AllowedMentions.none()

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

MAX_EMBED_CHARS  = 4000     # Discord embed description limit
EDIT_WATCH_SECS  = 120      # 2-minute edit-watch window
PAGINATION_SIZE  = 3800     # chars per page when paginating

# Build a case-insensitive trie-free lookup:  lower-name → display-name
# We sort by length descending so "Mega Charizard X" is matched before "Charizard"
_ALL_LOWER_TO_DISPLAY: dict[str, str] = {
    r["name"].lower(): r["name"] for r in
    sorted(
        [{"name": n} for n in ALL_NAMES_ORDERED],
        key=lambda x: -len(x["name"])
    )
}
# Sorted by length descending for greedy matching in extract_pokemon_names
_SORTED_NAMES = sorted(_ALL_LOWER_TO_DISPLAY.keys(), key=lambda n: -len(n))


# ─────────────────────────────────────────────────────────────────────────────
# Language / name lookup tables  (built once at import time)
# ─────────────────────────────────────────────────────────────────────────────

# Set of all event Pokémon names (lowercase) for fast filtering
_EVENT_NAMES_LOWER: set[str] = {e["name"].lower() for e in EVENT_DATA}

# Language code → flag emoji (used as option value)
# pokemondata.json uses flag emojis as keys; we map them to readable codes
_LANG_FLAG_MAP: dict[str, str] = {
    "en":   "🇬🇧",
    "de":   "🇩🇪",
    "fr":   "🇫🇷",
    "jp":   "🇯🇵",
}

# Build per-language lookup:  english-name (lowercase) → translated name
# If a JP value is a list, prefer the romanised (index 1) then kanji (index 0)
def _build_lang_lookup(flag: str) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for p in POKEMON_DATA:
        val = p.get("other_names", {}).get(flag)
        if val is None:
            continue
        if isinstance(val, list):
            # JP: [kanji, romanised] — prefer romanised when available
            name = val[1] if len(val) > 1 else val[0]
        else:
            name = val
        lookup[p["name"].lower()] = name
    return lookup

_LANG_LOOKUPS: dict[str, dict[str, str]] = {
    code: _build_lang_lookup(flag)
    for code, flag in _LANG_FLAG_MAP.items()
}

# Handle BEST_NAMES - convert to lowercase dict for lookup
_BEST_NAMES_LOWER: dict[str, str] = {}
if isinstance(BEST_NAMES, dict):
    _BEST_NAMES_LOWER = {k.lower(): v for k, v in BEST_NAMES.items()}
elif isinstance(BEST_NAMES, list):
    for item in BEST_NAMES:
        if isinstance(item, dict) and "name" in item and "best" in item:
            _BEST_NAMES_LOWER[item["name"].lower()] = item["best"]
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            _BEST_NAMES_LOWER[item[0].lower()] = item[1]
else:
    print(f"Warning: BEST_NAMES has unexpected type {type(BEST_NAMES)}")
    _BEST_NAMES_LOWER = {}


def _component_texts(components) -> list[str]:
    """Collect the text of every TextDisplay in a Components V2 tree (discord.py 2.6+)."""
    texts: list[str] = []
    for comp in components or []:
        content = getattr(comp, "content", None)
        if isinstance(content, str) and content:
            texts.append(content)
        texts.extend(_component_texts(getattr(comp, "children", None)))
        accessory = getattr(comp, "accessory", None)
        if accessory is not None:
            texts.extend(_component_texts([accessory]))
    return texts


def extract_from_message(msg: "discord.Message") -> list[str]:
    """
    Extract all Pokémon names from a Discord message — both its plain-text
    content, every text field inside any embeds, and the text of Components V2
    messages (containers / sections / text displays).

    Returns a deduplicated list in order of first appearance.
    """
    blobs: list[str] = []
    if msg.content:
        blobs.append(msg.content)

    for embed in msg.embeds:
        if embed.title:
            blobs.append(embed.title)
        if embed.description:
            blobs.append(embed.description)
        for field in embed.fields:
            if field.name:
                blobs.append(field.name)
            if field.value:
                blobs.append(field.value)
        if embed.footer and embed.footer.text:
            blobs.append(embed.footer.text)
        if embed.author and embed.author.name:
            blobs.append(embed.author.name)

    blobs.extend(_component_texts(getattr(msg, "components", None)))

    seen: set[str] = set()
    result: list[str] = []
    for blob in blobs:
        for name in extract_pokemon_names(blob):
            if name.lower() not in seen:
                seen.add(name.lower())
                result.append(name)
    return result


def extract_pokemon_names(text: str) -> list[str]:
    """
    Extract Pokémon names from free text, preserving order of first occurrence.
    Greedy longest-match: "Mega Alakazam" is matched before "Alakazam".
    Returns display-cased names.
    """
    text_lower = text.lower()
    found: list[tuple[int, str]] = []          # (start_pos, display_name)
    covered: set[int] = set()                  # character indices already matched

    for lower_name in _SORTED_NAMES:
        start = 0
        while True:
            idx = text_lower.find(lower_name, start)
            if idx == -1:
                break
            end = idx + len(lower_name)
            # Word-boundary check – must not be surrounded by word chars
            before_ok = idx == 0 or not text_lower[idx - 1].isalpha()
            after_ok  = end >= len(text_lower) or not text_lower[end].isalpha()
            if before_ok and after_ok:
                span = set(range(idx, end))
                if not span & covered:
                    covered |= span
                    display = _ALL_LOWER_TO_DISPLAY[lower_name]
                    found.append((idx, display))
            start = idx + 1

    found.sort(key=lambda x: x[0])
    seen: set[str] = set()
    result: list[str] = []
    for _, name in found:
        if name.lower() not in seen:
            seen.add(name.lower())
            result.append(name)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# State object
# ─────────────────────────────────────────────────────────────────────────────

class ListState:
    """Holds the mutable state of one list-builder session."""

    SORT_OPTIONS = [
        ("A → Z",          "alpha_asc"),
        ("Z → A",          "alpha_desc"),
        ("Longest first",  "len_desc"),
        ("Shortest first", "len_asc"),
        ("SR high → low",  "sr_desc"),
        ("SR low → high",  "sr_asc"),
    ]
    CASE_OPTIONS = [
        ("As-is",  "asis"),
        ("UPPER",  "upper"),
        ("lower",  "lower"),
        ("Title",  "title"),
    ]
    FORMAT_OPTIONS = [
        ("Comma separated",  "comma"),
        ("--n format",       "n_flag"),
        ("--evo format",     "evo_flag"),
        ("One per line",     "newline"),
    ]
    LANG_OPTIONS = [
        ("English 🇬🇧",   "en"),
        ("German 🇩🇪",    "de"),
        ("French 🇫🇷",    "fr"),
        ("Japanese 🇯🇵",  "jp"),
        ("Best Name ⭐",   "best"),
    ]

    def __init__(self):
        # Core list – maintains insertion order (deduplicated)
        self._names: list[str] = []
        self._seen:  set[str]  = set()        # lowercase set for O(1) lookup

        # Display settings
        self.sort_key:   str = "alpha_asc"
        self.case_key:   str = "asis"
        self.format_key: str = "comma"
        self.lang_key:   str = "en"           # "en" | "de" | "fr" | "jp" | "best"

        # Event Pokémon visibility (True = include events, False = exclude)
        self.event_mode: bool = True

        # Optional enclosure
        self.enclose_before: str = ""
        self.enclose_after:  str = ""

        # Bullet / line prefix (only for newline format)
        self.line_prefix: str = ""

        # Replace pair
        self.replace_from: str = ""
        self.replace_to:   str = ""

    # ── list management ──────────────────────────────────────────────────────

    def add(self, names: list[str]):
        for name in names:
            if name.lower() not in self._seen:
                self._seen.add(name.lower())
                self._names.append(name)

    def remove(self, names: list[str]):
        """Remove names, case-insensitive."""
        names_lower = {n.lower() for n in names}
        self._names = [n for n in self._names if n.lower() not in names_lower]
        for n in names_lower:
            self._seen.discard(n)

    def set_names_ordered(self, names: list[str]):
        """Replace the entire list with new names, preserving order."""
        self._names = []
        self._seen  = set()
        self.add(names)

    def clear(self):
        """Clear the list."""
        self._names = []
        self._seen  = set()

    @property
    def names(self) -> list[str]:
        """Return the current list of names, sorted and cased."""
        result = self._names[:]

        # Apply event filter
        if not self.event_mode:
            result = [n for n in result if n.lower() not in _EVENT_NAMES_LOWER]

        # Sort
        if self.sort_key == "alpha_asc":
            result.sort(key=lambda n: n.lower())
        elif self.sort_key == "alpha_desc":
            result.sort(key=lambda n: n.lower(), reverse=True)
        elif self.sort_key == "len_asc":
            result.sort(key=len)
        elif self.sort_key == "len_desc":
            result.sort(key=len, reverse=True)
        elif self.sort_key == "sr_asc":
            result.sort(key=lambda n: NAME_TO_SR.get(n.lower(), 9999))
        elif self.sort_key == "sr_desc":
            result.sort(key=lambda n: NAME_TO_SR.get(n.lower(), 9999), reverse=True)

        # Case
        if self.case_key == "upper":
            result = [n.upper() for n in result]
        elif self.case_key == "lower":
            result = [n.lower() for n in result]
        elif self.case_key == "title":
            result = [n.title() for n in result]
        # else: "asis" – keep as-is

        return result

    @property
    def count(self) -> int:
        """Number of Pokémon in the list."""
        return len(self._names)

    def format_output(self) -> str:
        """Format the list according to current settings."""
        # Start from the sorted (but not yet cased) names
        result = self._names[:]

        # Apply event filter
        if not self.event_mode:
            result = [n for n in result if n.lower() not in _EVENT_NAMES_LOWER]

        # Sort (same logic as names property)
        if self.sort_key == "alpha_asc":
            result.sort(key=lambda n: n.lower())
        elif self.sort_key == "alpha_desc":
            result.sort(key=lambda n: n.lower(), reverse=True)
        elif self.sort_key == "len_asc":
            result.sort(key=len)
        elif self.sort_key == "len_desc":
            result.sort(key=len, reverse=True)
        elif self.sort_key == "sr_asc":
            result.sort(key=lambda n: NAME_TO_SR.get(n.lower(), 9999))
        elif self.sort_key == "sr_desc":
            result.sort(key=lambda n: NAME_TO_SR.get(n.lower(), 9999), reverse=True)

        names = result

        # Apply language transformation (before case so case applies to translated names too)
        if self.lang_key == "best":
            def _pick_best(n: str) -> str:
                val = _BEST_NAMES_LOWER.get(n.lower(), n)
                # If value has multiple options separated by ", " pick the first one
                if isinstance(val, str) and "," in val:
                    return val.split(",")[0].strip()
                return val
            names = [_pick_best(n) for n in names]
        elif self.lang_key != "en":
            lookup = _LANG_LOOKUPS.get(self.lang_key, {})
            names = [lookup.get(n.lower(), n) for n in names]

        # Apply case AFTER language (so lower/upper/title works on translated names)
        if self.case_key == "upper":
            names = [n.upper() for n in names]
        elif self.case_key == "lower":
            names = [n.lower() for n in names]
        elif self.case_key == "title":
            names = [n.title() for n in names]
        # else "asis" — keep translated name as-is

        # Apply enclosure
        if self.enclose_before or self.enclose_after:
            names = [
                f"{self.enclose_before}{n}{self.enclose_after}"
                for n in names
            ]

        # Apply format
        if self.format_key == "comma":
            output = ", ".join(names)
        elif self.format_key == "newline":
            output = "\n".join(f"{self.line_prefix}{n}" for n in names)
        elif self.format_key == "n_flag":
            # Include flag before first pokemon: --n poke1 --n poke2 ...
            output = ("--n " + " --n ".join(names)) if names else ""
        elif self.format_key == "evo_flag":
            output = ("--evo " + " --evo ".join(names)) if names else ""
        else:
            output = ", ".join(names)

        # Apply replacements
        if self.replace_from and self.replace_to:
            output = output.replace(self.replace_from, self.replace_to)

        return output


# ─────────────────────────────────────────────────────────────────────────────
# Modal dialogs
# ─────────────────────────────────────────────────────────────────────────────

class AddModal(discord.ui.Modal, title="Add Pokémon - Enter filters"):
    """Modal for adding Pokémon - 3 independent filter cells."""

    filter1 = discord.ui.TextInput(
        label="Filter 1",
        placeholder="e.g. --type fire",
        style=discord.TextStyle.short,
        required=False,
    )
    filter2 = discord.ui.TextInput(
        label="Filter 2",
        placeholder="e.g. --region kanto",
        style=discord.TextStyle.short,
        required=False,
    )
    filter3 = discord.ui.TextInput(
        label="Filter 3",
        placeholder="e.g. charizard (fuzzy search)",
        style=discord.TextStyle.short,
        required=False,
    )

    def __init__(self, view: "ListBuilderView"):
        super().__init__()
        self._view = view

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self._view.owner_id:
            await interaction.response.send_message("Not yours!", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return

        filter1 = self.filter1.value.strip()
        filter2 = self.filter2.value.strip()
        filter3 = self.filter3.value.strip()

        matched = []

        # Process each filter independently and combine results
        for f in [filter1, filter2, filter3]:
            if not f:
                continue

            if f.startswith("--"):
                # Filter syntax
                try:
                    results = apply_filters(f)
                    matched.extend(results)
                except Exception as e:
                    await interaction.response.send_message(
                        f"❌ Filter error in '{f}': {str(e)}", ephemeral=True, allowed_mentions=NO_MENTIONS
                    )
                    return
            else:
                # Fuzzy name match
                query_lower = f.lower()
                results = [n for n in ALL_NAMES_ORDERED if query_lower in n.lower()]
                matched.extend(results)

        # Remove duplicates while preserving order
        seen = set()
        unique_matched = []
        for name in matched:
            if name.lower() not in seen:
                seen.add(name.lower())
                unique_matched.append(name)

        self._view.state.add(unique_matched)
        await interaction.response.edit_message(
            embed=self._view.build_embed(f"✅ Added {len(unique_matched)} Pokémon."),
            view=self._view,
        )


class RemoveModal(discord.ui.Modal, title="Remove Pokémon - Enter filters"):
    """Modal for removing Pokémon - 3 independent filter cells."""

    filter1 = discord.ui.TextInput(
        label="Filter 1",
        placeholder="e.g. --type fire",
        style=discord.TextStyle.short,
        required=False,
    )
    filter2 = discord.ui.TextInput(
        label="Filter 2",
        placeholder="e.g. --region kanto",
        style=discord.TextStyle.short,
        required=False,
    )
    filter3 = discord.ui.TextInput(
        label="Filter 3",
        placeholder="e.g. charizard (fuzzy search)",
        style=discord.TextStyle.short,
        required=False,
    )

    def __init__(self, view: "ListBuilderView"):
        super().__init__()
        self._view = view

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self._view.owner_id:
            await interaction.response.send_message("Not yours!", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return

        filter1 = self.filter1.value.strip()
        filter2 = self.filter2.value.strip()
        filter3 = self.filter3.value.strip()

        matched = []

        # Process each filter independently and combine results
        for f in [filter1, filter2, filter3]:
            if not f:
                continue

            if f.startswith("--"):
                # Filter syntax
                try:
                    results = apply_filters(f)
                    matched.extend(results)
                except Exception as e:
                    await interaction.response.send_message(
                        f"❌ Filter error in '{f}': {str(e)}", ephemeral=True, allowed_mentions=NO_MENTIONS
                    )
                    return
            else:
                # Fuzzy name match - but only from current list
                query_lower = f.lower()
                results = [n for n in self._view.state._names if query_lower in n.lower()]
                matched.extend(results)

        # Remove duplicates while preserving order
        seen = set()
        unique_matched = []
        for name in matched:
            if name.lower() not in seen:
                seen.add(name.lower())
                unique_matched.append(name)

        self._view.state.remove(unique_matched)
        await interaction.response.edit_message(
            embed=self._view.build_embed(f"✅ Removed {len(unique_matched)} Pokémon."),
            view=self._view,
        )


class FilterModal(discord.ui.Modal, title="Filter List – Keep Matching Pokémon"):
    """Modal for filtering the existing list – keeps only Pokémon that match any filter."""

    filter1 = discord.ui.TextInput(
        label="Keep filter 1",
        placeholder="e.g. --type fire  (keeps fire-type Pokémon)",
        style=discord.TextStyle.short,
        required=False,
    )
    filter2 = discord.ui.TextInput(
        label="Keep filter 2",
        placeholder="e.g. --type grass",
        style=discord.TextStyle.short,
        required=False,
    )
    filter3 = discord.ui.TextInput(
        label="Keep filter 3",
        placeholder="e.g. --region kanto",
        style=discord.TextStyle.short,
        required=False,
    )

    def __init__(self, view: "ListBuilderView"):
        super().__init__()
        self._view = view

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self._view.owner_id:
            await interaction.response.send_message("Not yours!", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return

        filters = [
            self.filter1.value.strip(),
            self.filter2.value.strip(),
            self.filter3.value.strip(),
        ]

        if not any(filters):
            await interaction.response.send_message(
                "❌ Enter at least one filter.", ephemeral=True, allowed_mentions=NO_MENTIONS
            )
            return

        # Build set of names to KEEP (union of all filter results, intersected with current list)
        keep_lower: set[str] = set()
        for f in filters:
            if not f:
                continue
            if f.startswith("--"):
                try:
                    results = apply_filters(f)
                    keep_lower.update(n.lower() for n in results)
                except Exception as e:
                    await interaction.response.send_message(
                        f"❌ Filter error in '{f}': {str(e)}", ephemeral=True, allowed_mentions=NO_MENTIONS
                    )
                    return
            else:
                # Fuzzy name match against ALL names
                query_lower = f.lower()
                for n in ALL_NAMES_ORDERED:
                    if query_lower in n.lower():
                        keep_lower.add(n.lower())

        # Intersect with the current list (preserve order)
        before = self._view.state.count
        kept = [n for n in self._view.state._names if n.lower() in keep_lower]
        self._view.state.set_names_ordered(kept)
        removed = before - self._view.state.count

        await interaction.response.edit_message(
            embed=self._view.build_embed(
                f"🔍 Filtered: kept {self._view.state.count}, removed {removed}."
            ),
            view=self._view,
        )


class EncloseModal(discord.ui.Modal, title="Enclose Names"):
    """Modal for wrapping names with custom strings."""

    before = discord.ui.TextInput(
        label="Before each name",
        placeholder=r"e.g. '[' or ':joy: \s'  — use \s where you want a space",
        required=False,
    )
    after = discord.ui.TextInput(
        label="After each name",
        placeholder=r"e.g. ']' or '\s :joy:'  — use \s where you want a space",
        required=False,
    )

    def __init__(self, view: "ListBuilderView"):
        super().__init__()
        self._view = view

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self._view.owner_id:
            await interaction.response.send_message("Not yours!", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return
        # Discord strips leading/trailing whitespace from modal inputs.
        # Users can type \s where they want a space (e.g. ":joy \s" → ":joy ").
        self._view.state.enclose_before = self.before.value.replace("\\s", " ")
        self._view.state.enclose_after = self.after.value.replace("\\s", " ")
        await interaction.response.send_message("✅ Enclosure applied.", ephemeral=True, allowed_mentions=NO_MENTIONS)
        # Update main builder embed
        if self._view.message:
            await self._view.message.edit(
                embed=self._view.build_embed(),
                view=self._view
            )


class ReplaceModal(discord.ui.Modal, title="Find & Replace"):
    """Modal for find-and-replace in output."""

    find = discord.ui.TextInput(
        label="Find",
        placeholder="Text to find",
        required=True,
    )
    replace = discord.ui.TextInput(
        label="Replace with",
        placeholder="Replacement text",
        required=False,
    )

    def __init__(self, view: "ListBuilderView"):
        super().__init__()
        self._view = view

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self._view.owner_id:
            await interaction.response.send_message("Not yours!", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return
        self._view.state.replace_from = self.find.value
        self._view.state.replace_to = self.replace.value
        await interaction.response.send_message("✅ Find-and-replace set.", ephemeral=True, allowed_mentions=NO_MENTIONS)
        # Update main builder embed
        if self._view.message:
            await self._view.message.edit(
                embed=self._view.build_embed(),
                view=self._view
            )


# ─────────────────────────────────────────────────────────────────────────────
# Display Options View (opened from Format button)
# ─────────────────────────────────────────────────────────────────────────────

class DisplayOptionsView(discord.ui.View):
    """Dropdown menu for format, case, and language options."""

    def __init__(self, builder_view: "ListBuilderView", owner_id: int):
        super().__init__(timeout=300)
        self.builder_view = builder_view
        self.owner_id = owner_id

        # Format dropdown
        format_opts = [
            discord.SelectOption(
                label=label, value=key,
                default=(key == builder_view.state.format_key),
            )
            for label, key in ListState.FORMAT_OPTIONS
        ]
        self.add_item(FormatSelect(builder_view, owner_id, format_opts))

        # Language dropdown
        lang_opts = [
            discord.SelectOption(
                label=label, value=key,
                default=(key == builder_view.state.lang_key),
            )
            for label, key in ListState.LANG_OPTIONS
        ]
        self.add_item(LangSelect(builder_view, owner_id, lang_opts))

        # Sort dropdown
        sort_opts = [
            discord.SelectOption(
                label=label, value=key,
                default=(key == builder_view.state.sort_key),
            )
            for label, key in ListState.SORT_OPTIONS
        ]
        self.add_item(SortSelect(builder_view, owner_id, sort_opts))

    async def on_timeout(self):
        # Ephemeral message — cannot be edited, just release refs
        self.clear_items()
        self.builder_view = None  # type: ignore[assignment]


class FormatSelect(discord.ui.Select):
    def __init__(self, view: "ListBuilderView", owner_id: int, options):
        self.builder_view = view
        self.owner_id = owner_id
        current_label = next(l for l, k in ListState.FORMAT_OPTIONS if k == view.state.format_key)
        super().__init__(placeholder=f"📄 Output format — {current_label}", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Not yours!", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return
        self.builder_view.state.format_key = self.values[0]
        format_label = next(l for l, k in ListState.FORMAT_OPTIONS if k == self.values[0])
        await interaction.response.send_message(
            f"✅ Format set to {format_label}.",
            ephemeral=True, allowed_mentions=NO_MENTIONS
        )
        # Update main builder embed
        if self.builder_view.message:
            await self.builder_view.message.edit(
                embed=self.builder_view.build_embed(),
                view=self.builder_view
            )


class CaseSelect(discord.ui.Select):
    def __init__(self, view: "ListBuilderView", owner_id: int, options):
        self.builder_view = view
        self.owner_id = owner_id
        current_label = next(l for l, k in ListState.CASE_OPTIONS if k == view.state.case_key)
        super().__init__(placeholder=f"🔡 Text case — {current_label}", options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Not yours!", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return
        self.builder_view.state.case_key = self.values[0]
        case_label = next(l for l, k in ListState.CASE_OPTIONS if k == self.values[0])
        await interaction.response.send_message(
            f"✅ Case set to {case_label}.",
            ephemeral=True, allowed_mentions=NO_MENTIONS
        )
        # Update main builder embed
        if self.builder_view.message:
            await self.builder_view.message.edit(
                embed=self.builder_view.build_embed(),
                view=self.builder_view
            )


class LangSelect(discord.ui.Select):
    def __init__(self, view: "ListBuilderView", owner_id: int, options):
        self.builder_view = view
        self.owner_id = owner_id
        current_label = next(l for l, k in ListState.LANG_OPTIONS if k == view.state.lang_key)
        super().__init__(placeholder=f"🌐 Language / Name style — {current_label}", options=options, row=2)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Not yours!", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return
        self.builder_view.state.lang_key = self.values[0]
        lang_label = next(l for l, k in ListState.LANG_OPTIONS if k == self.values[0])
        await interaction.response.send_message(
            f"✅ Language set to {lang_label}.",
            ephemeral=True, allowed_mentions=NO_MENTIONS
        )
        # Update main builder embed
        if self.builder_view.message:
            await self.builder_view.message.edit(
                embed=self.builder_view.build_embed(),
                view=self.builder_view
            )


class SortSelect(discord.ui.Select):
    def __init__(self, view: "ListBuilderView", owner_id: int, options):
        self.builder_view = view
        self.owner_id = owner_id
        current_label = next(l for l, k in ListState.SORT_OPTIONS if k == view.state.sort_key)
        super().__init__(placeholder=f"🔀 Sort order — {current_label}", options=options, row=3)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Not yours!", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return
        self.builder_view.state.sort_key = self.values[0]
        sort_label = next(l for l, k in ListState.SORT_OPTIONS if k == self.values[0])
        await interaction.response.send_message(
            f"✅ Sort order set to {sort_label}.",
            ephemeral=True, allowed_mentions=NO_MENTIONS
        )
        # Update main builder embed
        if self.builder_view.message:
            await self.builder_view.message.edit(
                embed=self.builder_view.build_embed(),
                view=self.builder_view
            )


class AdvLangSelect(discord.ui.Select):
    def __init__(self, view: "ListBuilderView", owner_id: int, options, current_label: str):
        self.builder_view = view
        self.owner_id = owner_id
        super().__init__(placeholder=f"🌐 Language — {current_label}", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Not yours!", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return
        self.builder_view.state.lang_key = self.values[0]
        label = next(l for l, k in ListState.LANG_OPTIONS if k == self.values[0])
        self.placeholder = f"🌐 Language — {label}"
        await interaction.response.send_message(f"✅ Language set to {label}.", ephemeral=True, allowed_mentions=NO_MENTIONS)
        if self.builder_view.message:
            await self.builder_view.message.edit(
                embed=self.builder_view.build_embed(),
                view=self.builder_view,
            )


class AdvSortSelect(discord.ui.Select):
    def __init__(self, view: "ListBuilderView", owner_id: int, options, current_label: str):
        self.builder_view = view
        self.owner_id = owner_id
        super().__init__(placeholder=f"🔀 Sort — {current_label}", options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Not yours!", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return
        self.builder_view.state.sort_key = self.values[0]
        label = next(l for l, k in ListState.SORT_OPTIONS if k == self.values[0])
        self.placeholder = f"🔀 Sort — {label}"
        await interaction.response.send_message(f"✅ Sort set to {label}.", ephemeral=True, allowed_mentions=NO_MENTIONS)
        if self.builder_view.message:
            await self.builder_view.message.edit(
                embed=self.builder_view.build_embed(),
                view=self.builder_view,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Advanced Options View (opened from Advanced button)
# ─────────────────────────────────────────────────────────────────────────────

class AdvancedOptionsView(discord.ui.View):
    """Dropdown menu for advanced options (language, sort, replace, event mode)."""

    def __init__(self, builder_view: "ListBuilderView", owner_id: int):
        super().__init__(timeout=300)
        self.builder_view = builder_view
        self.owner_id = owner_id

        # Language dropdown (row 0)
        lang_opts = [
            discord.SelectOption(
                label=label, value=key,
                default=(key == builder_view.state.lang_key),
            )
            for label, key in ListState.LANG_OPTIONS
        ]
        current_lang = next(l for l, k in ListState.LANG_OPTIONS if k == builder_view.state.lang_key)
        self.add_item(AdvLangSelect(builder_view, owner_id, lang_opts, current_lang))

        # Sort dropdown (row 1)
        sort_opts = [
            discord.SelectOption(
                label=label, value=key,
                default=(key == builder_view.state.sort_key),
            )
            for label, key in ListState.SORT_OPTIONS
        ]
        current_sort = next(l for l, k in ListState.SORT_OPTIONS if k == builder_view.state.sort_key)
        self.add_item(AdvSortSelect(builder_view, owner_id, sort_opts, current_sort))

    @discord.ui.button(label="Replace", emoji="🔄", style=discord.ButtonStyle.secondary, row=2)
    async def replace_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Not yours!", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return
        await interaction.response.send_modal(ReplaceModal(self.builder_view))

    @discord.ui.button(label="Events", emoji="🎉", style=discord.ButtonStyle.secondary, row=2)
    async def events_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Not yours!", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return

        self.builder_view.state.event_mode = not self.builder_view.state.event_mode
        status = "✅ Including event Pokémon" if self.builder_view.state.event_mode else "⛔ Excluding event Pokémon"
        await interaction.response.send_message(status, ephemeral=True, allowed_mentions=NO_MENTIONS)
        # Update main builder embed
        if self.builder_view.message:
            await self.builder_view.message.edit(
                embed=self.builder_view.build_embed(),
                view=self.builder_view
            )

    async def on_timeout(self):
        # Sent as an ephemeral message — cannot be edited, just release refs
        self.clear_items()
        self.builder_view = None  # type: ignore[assignment]


# ─────────────────────────────────────────────────────────────────────────────
# Paginator View  (shown after Send when output spans multiple pages)
# ─────────────────────────────────────────────────────────────────────────────

class PaginatorView(discord.ui.View):
    """Navigation buttons for a multi-page Send output."""

    def __init__(self, pages: list[str], owner_id: int):
        super().__init__(timeout=300)
        self.pages    = pages
        self.owner_id = owner_id
        self.current  = 0
        self.message: discord.Message | None = None
        self._update_buttons()

    def _update_buttons(self):
        self.prev_button.disabled = self.current == 0
        self.next_button.disabled = self.current == len(self.pages) - 1
        self.page_label.label     = f"{self.current + 1} / {len(self.pages)}"

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Not yours!", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return
        self.current -= 1
        self._update_buttons()
        await interaction.response.edit_message(content=self.pages[self.current], view=self, allowed_mentions=NO_MENTIONS)

    @discord.ui.button(label="1 / ?", style=discord.ButtonStyle.secondary, disabled=True)
    async def page_label(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Not yours!", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return
        self.current += 1
        self._update_buttons()
        await interaction.response.edit_message(content=self.pages[self.current], view=self, allowed_mentions=NO_MENTIONS)

    async def on_timeout(self):
        self.clear_items()
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
        self.message = None
        self.pages   = []  # release page data


# ─────────────────────────────────────────────────────────────────────────────
# Case Select — lives on row 2 of the main view (always visible)
# ─────────────────────────────────────────────────────────────────────────────

class MainCaseSelect(discord.ui.Select):
    """Case dropdown shown directly on the main list-builder message (row 2)."""

    def __init__(self, builder_view: "ListBuilderView", owner_id: int, options):
        self.builder_view = builder_view
        self.owner_id = owner_id
        current_label = next(l for l, k in ListState.CASE_OPTIONS if k == builder_view.state.case_key)
        super().__init__(
            placeholder=f"🔡 Text case — {current_label}",
            options=options,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Not yours!", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return
        self.builder_view.state.case_key = self.values[0]
        case_label = next(l for l, k in ListState.CASE_OPTIONS if k == self.values[0])
        # Update placeholder to reflect new selection
        self.placeholder = f"🔡 Text case — {case_label}"
        await interaction.response.edit_message(
            embed=self.builder_view.build_embed(f"🔡 Case: {case_label}"),
            view=self.builder_view,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Main List Builder View
# ─────────────────────────────────────────────────────────────────────────────

class ListBuilderView(discord.ui.View):
    """Main view for the list-builder UI with organized button layout."""

    def __init__(self, owner_id: int, state: ListState):
        super().__init__(timeout=120)  # 2-minute inactivity timeout
        self.owner_id = owner_id
        self.state = state
        self.message: discord.Message | None = None
        self._watcher_task: asyncio.Task | None = None  # edit-watcher, cancelled on timeout

        # Sync format button label to current state
        self.format_cycle_button.label = self._FORMAT_LABELS.get(state.format_key, "📄 Comma")

        # Add Case dropdown on row 2 (always visible)
        case_opts = [
            discord.SelectOption(
                label=label, value=key,
                default=(key == state.case_key),
            )
            for label, key in ListState.CASE_OPTIONS
        ]
        self.add_item(MainCaseSelect(self, owner_id, case_opts))

    def build_embed(self, status: str = "") -> discord.Embed:
        """Build the main builder embed."""
        full = self.state.format_output()
        displayed_count = len(self.state.names)

        if len(full) > MAX_EMBED_CHARS:
            desc = full[:MAX_EMBED_CHARS - 10] + "…\n\n*(Output too long to preview)*"
        else:
            desc = full if full else "(empty list)"

        event_status = "🎉 Include" if self.state.event_mode else "⛔ Exclude"

        embed = discord.Embed(
            title=f"📝 List Builder ({displayed_count} Pokémon)",
            description=desc,
            color=EMBED_COLOR,
        )
        embed.set_footer(text=f"Events: {event_status} | {status}" if status else f"Events: {event_status}")

        return embed

    # ── Row 1: Add/Remove ──────────────────────────────────────────────────

    @discord.ui.button(label="Add", emoji="➕", style=discord.ButtonStyle.primary, row=0)
    async def add_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Not yours!", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return
        self._refresh_timeout()
        await interaction.response.send_modal(AddModal(self))

    @discord.ui.button(label="Filter", emoji="🔍", style=discord.ButtonStyle.primary, row=0)
    async def filter_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Not yours!", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return
        self._refresh_timeout()
        await interaction.response.send_modal(FilterModal(self))

    @discord.ui.button(label="Remove", emoji="➖", style=discord.ButtonStyle.danger, row=0)
    async def remove_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Not yours!", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return
        self._refresh_timeout()
        await interaction.response.send_modal(RemoveModal(self))

    @discord.ui.button(label="Clear", emoji="🗑", style=discord.ButtonStyle.danger, row=0)
    async def clear_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Not yours!", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return
        self._refresh_timeout()
        self.state.clear()
        await interaction.response.edit_message(
            embed=self.build_embed("✅ List cleared."),
            view=self,
        )

    # ── Row 1: Format / Enclose / Advanced / Send ────────────────────────

    _FORMAT_CYCLE = ["comma", "n_flag", "evo_flag", "newline"]
    _FORMAT_LABELS = {"comma": "📄 Comma", "n_flag": "📄 --n", "evo_flag": "📄 --evo", "newline": "📄 Newline"}

    @discord.ui.button(label="📄 Comma", style=discord.ButtonStyle.secondary, row=1)
    async def format_cycle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Not yours!", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return
        self._refresh_timeout()
        cycle = self._FORMAT_CYCLE
        current_idx = cycle.index(self.state.format_key) if self.state.format_key in cycle else 0
        next_idx = (current_idx + 1) % len(cycle)
        self.state.format_key = cycle[next_idx]
        button.label = self._FORMAT_LABELS[self.state.format_key]
        await interaction.response.edit_message(
            embed=self.build_embed(f"📄 Format: {self._FORMAT_LABELS[self.state.format_key]}"),
            view=self,
        )

    @discord.ui.button(label="🔤 Enclose", style=discord.ButtonStyle.secondary, row=1)
    async def enclose_main_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Not yours!", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return
        self._refresh_timeout()
        await interaction.response.send_modal(EncloseModal(self))

    @discord.ui.button(label="⚙️ Advanced", style=discord.ButtonStyle.secondary, row=1)
    async def advanced_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Not yours!", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return
        self._refresh_timeout()
        view = AdvancedOptionsView(self, self.owner_id)
        await interaction.response.send_message(
            "**Advanced options — Language, Sort, Replace, Events:**",
            view=view,
            ephemeral=True, allowed_mentions=NO_MENTIONS
        )

    @discord.ui.button(label="📤 Send", style=discord.ButtonStyle.success, row=1)
    async def send_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Not yours!", ephemeral=True, allowed_mentions=NO_MENTIONS)
            return
        self._refresh_timeout()

        if len(self.state.names) == 0:
            await interaction.response.send_message(
                "❌ List is empty.",
                ephemeral=True,
                allowed_mentions=NO_MENTIONS,
            )
            return

        full = self.state.format_output()

        if self.state.format_key == "comma":
            # Split on ", " boundaries so no Pokémon name is split across pages
            separator = ", "
            items = full.split(separator)
            pages: list[str] = []
            current: list[str] = []
            current_len = 0
            for item in items:
                # Account for backtick wrapping (2 chars) + separator cost
                segment_len = len(item) + (len(separator) if current else 0)
                # 1994 = 2000 - 2 backtick chars - 4 safety margin
                if current_len + segment_len > 1994 and current:
                    pages.append("`" + separator.join(current) + "`")
                    current = [item]
                    current_len = len(item)
                else:
                    current.append(item)
                    current_len += segment_len
            if current:
                pages.append("`" + separator.join(current) + "`")
        elif self.state.format_key == "newline":
            # Split on newline boundaries
            lines = full.split("\n")
            pages = []
            current_lines: list[str] = []
            current_len = 0
            for line in lines:
                segment_len = len(line) + (1 if current_lines else 0)
                if current_len + segment_len > 1994 and current_lines:
                    pages.append("`" + "\n".join(current_lines) + "`")
                    current_lines = [line]
                    current_len = len(line)
                else:
                    current_lines.append(line)
                    current_len += segment_len
            if current_lines:
                pages.append("`" + "\n".join(current_lines) + "`")
        else:
            # For --n / --evo formats, split by the flag separator
            # Output is now "--n poke1 --n poke2 ..." so strip leading prefix first
            prefix = "--n " if self.state.format_key == "n_flag" else "--evo "
            flag = " --n " if self.state.format_key == "n_flag" else " --evo "
            stripped = full[len(prefix):] if full.startswith(prefix) else full
            items = stripped.split(flag)
            pages = []
            current = []
            current_len = 0
            for item in items:
                segment_len = len(item) + (len(flag) if current else 0)
                if current_len + segment_len > (1994 - len(prefix)) and current:
                    pages.append("`" + prefix + flag.join(current) + "`")
                    current = [item]
                    current_len = len(item)
                else:
                    current.append(item)
                    current_len += segment_len
            if current:
                pages.append("`" + prefix + flag.join(current) + "`")

        if len(pages) == 1:
            await interaction.response.send_message(pages[0], allowed_mentions=NO_MENTIONS)
        else:
            # Send with paginator buttons
            view = PaginatorView(pages, interaction.user.id)
            await interaction.response.send_message(
                pages[0],
                view=view,
                allowed_mentions=NO_MENTIONS,
            )
            view.message = await interaction.original_response()

    async def on_timeout(self):
        # Cancel the edit-watcher task if still running
        if self._watcher_task and not self._watcher_task.done():
            self._watcher_task.cancel()
        self._watcher_task = None
        self.clear_items()
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
        self.message = None
        self.state.clear()  # release the stored Pokémon list
        self.state = None   # type: ignore[assignment]


# ─────────────────────────────────────────────────────────────────────────────
# Helper: paginate a long string
# ─────────────────────────────────────────────────────────────────────────────

def _paginate(text: str, max_chars: int) -> list[str]:
    """Split text into pages that each fit within max_chars."""
    if len(text) <= max_chars:
        return [text]
    pages = []
    while text:
        pages.append(text[:max_chars])
        text = text[max_chars:]
    return pages


# ─────────────────────────────────────────────────────────────────────────────
# Cog
# ─────────────────────────────────────────────────────────────────────────────

class ListGen(commands.Cog):
    """Pokémon list-generation tools."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Track active edit-watchers:  source_message_id → asyncio.Task
        self._edit_watchers: dict[int, asyncio.Task] = {}

    # ── main command ─────────────────────────────────────────────────────────

    @commands.command(name="listgen", aliases=["lg", "listbuilder"])
    async def listgen(self, ctx: commands.Context):
        """
        Open the Pokémon list builder.

        Use as a reply to extract names from that message and watch it for
        edits.  Or just run it standalone to start with an empty list.

        Examples:
            p!listgen
            p!lg           (reply to a message containing Pokémon names)
        """
        state = ListState()

        # If used as a reply, extract names from the referenced message
        source_msg: discord.Message | None = None
        if ctx.message.reference and ctx.message.reference.resolved:
            ref = ctx.message.reference.resolved
            if isinstance(ref, discord.Message):
                source_msg = ref
                names = extract_from_message(ref)
                state.set_names_ordered(names)

        view  = ListBuilderView(ctx.author.id, state)
        embed = view.build_embed(
            f"✅ Extracted {len(state.names)} Pokémon from message." if source_msg else ""
        )
        bot_msg = await ctx.reply(embed=embed, view=view, mention_author=False, allowed_mentions=NO_MENTIONS)
        view.message = bot_msg

        # Start edit-watcher if we have a source message
        if source_msg:
            self._start_edit_watcher(source_msg, view, bot_msg, ctx.author.id)
            view._watcher_task = self._edit_watchers.get(source_msg.id)

    # ── edit watcher ─────────────────────────────────────────────────────────

    def _start_edit_watcher(
        self,
        source_msg:  discord.Message,
        view:        ListBuilderView,
        bot_msg:     discord.Message,
        owner_id:    int,
    ):
        # Cancel any previous watcher for this source message
        old = self._edit_watchers.pop(source_msg.id, None)
        if old and not old.done():
            old.cancel()

        task = asyncio.ensure_future(
            self._watch_edits(source_msg, view, bot_msg, owner_id)
        )
        self._edit_watchers[source_msg.id] = task
        # Auto-prune when the task finishes
        task.add_done_callback(lambda t: self._edit_watchers.pop(source_msg.id, None))

    async def _watch_edits(
        self,
        source_msg: discord.Message,
        view:       ListBuilderView,
        bot_msg:    discord.Message,
        owner_id:   int,
    ):
        """
        Listen for edits to source_msg for EDIT_WATCH_SECS seconds.
        Each edit resets the 2-minute timer.
        """
        deadline = asyncio.get_running_loop().time() + EDIT_WATCH_SECS

        def check(before: discord.Message, after: discord.Message) -> bool:
            return after.id == source_msg.id

        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                _, after = await self.bot.wait_for(
                    "message_edit", check=check, timeout=remaining
                )
            except asyncio.TimeoutError:
                break

            # Edit detected – re-extract and reset timer
            deadline = asyncio.get_running_loop().time() + EDIT_WATCH_SECS
            new_names = extract_from_message(after)
            before_count = view.state.count
            view.state.add(new_names)
            added = view.state.count - before_count

            status = (
                f"🔄 Message edited – added {added} new Pokémon."
                if added > 0
                else "🔄 Message edited – no new Pokémon found."
            )
            try:
                await bot_msg.edit(
                    embed=view.build_embed(status),
                    view=view,
                )
            except discord.HTTPException:
                break


async def setup(bot: commands.Bot):
    await bot.add_cog(ListGen(bot))
