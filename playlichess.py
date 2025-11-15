# playlichess.py
"""
Create Lichess play links using the `play-lichess` package.

Features:
- .playlichess [mode] [time] [inc] [days] [variant] [name]
- .playlichess help | -h | --help  -> usage guide
- Flexible parsing: accepts "5+3", numeric seconds, named presets (bullet/blitz/rapid/classical)
- Fuzzy suggestions if mode or variant looks misspelled

How to use:
    from playlichess import setup_playlichess
    setup_playlichess(bot)

Example commands:
    .playlichess                 -> realtime 5+0 (default)
    .playlichess realtime 5+3    -> realtime 5 minutes + 3s increment
    .playlichess correspondence  -> correspondence 1 day
    .playlichess unlimited       -> unlimited match
    .playlichess help            -> shows help
"""

from typing import Optional, Tuple, List
import re
import difflib

# Attempt to import play-lichess; if not available, we surface a clear error to user.
try:
    from play_lichess import (
        RealTimeMatch,
        CorrespondenceMatch,
        UnlimitedMatch,
        Variant,
    )
except Exception as _err:
    RealTimeMatch = None
    CorrespondenceMatch = None
    UnlimitedMatch = None
    Variant = None
    _IMPORT_ERROR = _err
else:
    _IMPORT_ERROR = None


# --- Helpers -----------------------------------------------------------------

def _ensure_package_installed():
    if _IMPORT_ERROR is not None:
        raise RuntimeError(
            "play-lichess is required but not installed. Run: pip install play-lichess"
        )


def _parse_clock(arg: str) -> Optional[Tuple[int, int]]:
    """
    Parse a time control argument.
    Accepts:
      - '5+3'  -> (300, 3)
      - '300'  -> (300, 0)
      - '5'    -> (300, 0) (interpreted as minutes)
      - '300s' -> (300,0)
    Returns (limit_seconds, increment_seconds) or None if cannot parse.
    """
    if not arg:
        return None
    arg = arg.strip().lower()
    # X+Y format
    m = re.match(r"^(\d+)\s*\+\s*(\d+)$", arg)
    if m:
        a = int(m.group(1))
        b = int(m.group(2))
        # assume a is minutes if < 1000 and likely minutes (5 -> 300)
        if a <= 60:
            a = a * 60
        return (a, b)
    # number (minutes or seconds)
    m2 = re.match(r"^(\d+)\s*(s|m|sec|mins|minutes)?$", arg)
    if m2:
        val = int(m2.group(1))
        unit = (m2.group(2) or "").lower()
        if unit in ("s", "sec"):
            return (val, 0)
        # default treat as minutes if value is small (<=60)
        if unit in ("m", "mins", "minutes") or val <= 60:
            return (val * 60, 0)
        # otherwise treat as seconds
        return (val, 0)
    return None


_PRESET_TIMES = {
    "bullet": (60, 0),      # 1+0
    "ultra-bullet": (30, 0),
    "blitz": (300, 0),      # 5+0
    "blitz+1": (300, 1),    # example preset
    "rapid": (900, 0),      # 15+0
    "classical": (1800, 0), # 30+0
}


def _parse_preset_or_clock(arg: Optional[str]) -> Tuple[int, int]:
    """Return (limit_seconds, inc_seconds). Default is 300s (5 minutes) + 0."""
    if not arg:
        return (300, 0)
    arg = arg.lower()
    if arg in _PRESET_TIMES:
        return _PRESET_TIMES[arg]
    parsed = _parse_clock(arg)
    if parsed:
        return parsed
    # fallback: unknown -> default
    return (300, 0)


def _closest_match(word: str, choices: List[str], n=1, cutoff=0.6) -> List[str]:
    """Return a list of close matches (difflib wrapper)."""
    if not word or not choices:
        return []
    return difflib.get_close_matches(word, choices, n=n, cutoff=cutoff)


def _variant_choices() -> List[str]:
    """Return a list of Variant names from the package (if available) or a default list."""
    if Variant is not None:
        return [name for name in Variant.__members__.keys()]
    # common lichess variants (uppercase)
    return [
        "STANDARD", "CHESS960", "ANTICHESS", "ATOMIC",
        "HANOI", "KING_OF_THE_HILL", "RACING_KINGS", "SUICIDE"
    ]


def _mode_choices() -> List[str]:
    return ["realtime", "real", "rt", "correspondence", "corr", "unlimited", "un"]


def _format_help_text(prefix: str = ".") -> str:
    return (
        "**playlichess — create a Lichess challenge link**\n\n"
        "Usage:\n"
        f"  `{prefix}playlichess [mode] [time] [inc/name] [days] [variant] [name]`\n\n"
        "Modes:\n"
        "  `realtime` (default) — real-time games (use `5+3`, `blitz`, or seconds)\n"
        "  `correspondence` — correspondence games (use `days`, e.g. `3` for 3-day)\n"
        "  `unlimited` — unlimited time game\n\n"
        "Time formats (examples):\n"
        "  `5+3` (5 minutes + 3s increment), `300` (300 seconds), `blitz`, `bullet`\n\n"
        "Examples:\n"
        f"  `{prefix}playlichess` -> realtime 5+0 (default)\n"
        f"  `{prefix}playlichess realtime 5+3` -> realtime 5+3\n"
        f"  `{prefix}playlichess correspondence 3` -> correspondence 3 days\n"
        f"  `{prefix}playlichess unlimited` -> unlimited game\n\n"
        "If you aren't sure about a parameter, call: "
        f"`{prefix}playlichess help`"
    )


# --- Core creation -----------------------------------------------------------

async def _create_match(
    mode: str = "realtime",
    clock_limit: int = 300,
    clock_increment: int = 0,
    days: int = 1,
    variant: str = "STANDARD",
    rated: bool = False,
    name: Optional[str] = None,
):
    """
    Create a Lichess match using play-lichess. Returns the match object.
    """
    _ensure_package_installed()

    mode_l = (mode or "realtime").lower()

    # Resolve variant enum if available
    variant_name = (variant or "STANDARD").upper()
    variant_enum = None
    if Variant is not None:
        variant_enum = Variant.__members__.get(variant_name, None)

    if mode_l in ("real", "realtime", "rt"):
        # RealTimeMatch.create expects seconds for clock_limit and int increment
        match = await RealTimeMatch.create(
            rated=rated,
            clock_limit=clock_limit,
            clock_increment=clock_increment,
            variant=variant_enum or variant_name,
            name=name,
        )
    elif mode_l in ("correspondence", "corr", "cor"):
        match = await CorrespondenceMatch.create(
            rated=rated,
            days=days,
            variant=variant_enum or variant_name,
            name=name,
        )
    elif mode_l in ("unlimited", "un"):
        match = await UnlimitedMatch.create(
            variant=variant_enum or variant_name,
            name=name,
        )
    else:
        # fallback to realtime
        match = await RealTimeMatch.create(
            rated=rated,
            clock_limit=clock_limit,
            clock_increment=clock_increment,
            variant=variant_enum or variant_name,
            name=name,
        )

    return match


# --- Bot integration ---------------------------------------------------------

def setup_playlichess(bot):
    """
    Register the .playlichess command on the SelfBot instance.

    Behaviour:
      - Only callable by the self account (same as tldr.py). Remove the check if you want it public.
      - Accepts flexible arguments; see help.
    """

    @bot.command("playlichess")
    async def _cmd_playlichess(ctx, *args):
        # quickly delete the invoking message (same pattern as tldr.py)
        try:
            await ctx.message.delete(delay=1.5)
        except Exception:
            # ignore deletion failure
            await ctx.send("creating...", delete_after=3)
            pass

        # If user asked for help explicitly
        if args and args[0].lower() in ("help", "-h", "--help"):
            prefix = getattr(bot, "prefix", ".")
            await ctx.send(_format_help_text(prefix=prefix))
            return

        # Default values
        mode = "realtime"
        limit_sec = 300
        inc_sec = 0
        days = 1
        variant = "STANDARD"
        name = None
        rated = False

        # Parse args greedily:
        # args[0] -> maybe mode or time, args[1] -> maybe time or increment or days/variant, ...
        args_list = list(args)

        # If first arg looks like a known mode -> consume it
        if args_list:
            candidate_mode = args_list[0].lower()
            if candidate_mode in _mode_choices():
                mode = candidate_mode
                args_list.pop(0)
            else:
                # if looks like '5+3' it may be a time control instead
                if _parse_clock(candidate_mode) is not None or candidate_mode in _PRESET_TIMES:
                    # keep mode default (realtime), parse the clock below
                    pass
                else:
                    # unknown mode: suggest closest modes
                    close = _closest_match(candidate_mode, _mode_choices(), n=3, cutoff=0.5)
                    if close:
                        await ctx.send(
                            f"Unknown mode `{candidate_mode}`. Did you mean: {', '.join(close)} ?\n"
                            "Try `.playlichess help` for usage.",
                            delete_after=12,
                        )
                        return
                    # otherwise assume it's a clock/time or name and continue

        # Next, parse time (clock) if present (for realtime)
        if args_list:
            # check for preset or clock in first arg
            parsed = None
            first = args_list[0]
            if first.lower() in _PRESET_TIMES or _parse_clock(first) is not None:
                limit_sec, inc_sec = _parse_preset_or_clock(first)
                args_list.pop(0)
            # If mode is correspondence and first arg numeric -> days
            elif mode.startswith("corr") and re.match(r"^\d+$", first):
                days = int(first)
                args_list.pop(0)
            # else leave defaults

        # Accept optional explicit increment as second value (e.g., ".playlichess 5 3")
        if args_list and re.match(r"^\d+$", args_list[0]) and limit_sec and inc_sec == 0:
            # if user wrote '5 3' we've already consumed '5' as minutes maybe; be careful
            # Only interpret this if it makes sense (numbers small)
            maybe_inc = int(args_list[0])
            # if limit was set in minutes (<=60 minutes) and this number is small, treat as inc
            if maybe_inc <= 60:
                inc_sec = maybe_inc
                args_list.pop(0)

        # Next argument may be days for correspondence or variant if string
        if args_list:
            if mode.startswith("corr") and re.match(r"^\d+$", args_list[0]):
                days = int(args_list[0])
                args_list.pop(0)

        # Next argument: variant (if provided)
        if args_list:
            candidate_variant = args_list[0].upper()
            # if it looks like a variant name, accept; otherwise attempt fuzzy match
            choices = _variant_choices()
            if candidate_variant in choices:
                variant = candidate_variant
                args_list.pop(0)
            else:
                close_var = _closest_match(candidate_variant, choices, n=2, cutoff=0.6)
                if close_var:
                    await ctx.send(
                        f"Unknown variant `{candidate_variant}`. Did you mean: {', '.join(close_var)} ?\n"
                        "Using default variant `STANDARD`. Use `.playlichess help` for variants.",
                        delete_after=12,
                    )
                    # keep default variant
                    args_list.pop(0)
                else:
                    # if it's not a variant, maybe it's a name — leave it for the name stage
                    pass

        # Remaining args: treat as name / friendly label (join with spaces)
        if args_list:
            name = " ".join(args_list).strip() or None

        # Make the match
        try:
            match = await _create_match(
                mode=mode,
                clock_limit=limit_sec,
                clock_increment=inc_sec,
                days=days,
                variant=variant,
                rated=rated,
                name=name,
            )
        except RuntimeError as rexc:
            # package not installed
            await ctx.send(f"Error: {rexc}", delete_after=10)
            return
        except Exception as e:
            # Provide a helpful error and a hint
            await ctx.send(f"Could not create Lichess match: {e}\nTry `.playlichess help`.", delete_after=10)
            return

        # Build response (friendly)
        lines = ["**Lichess match created!**"]
        # match may contain challenge_url, url_white, url_black depending on library version
        challenge = getattr(match, "challenge_url", None) or getattr(match, "challengeUrl", None)
        url_white = getattr(match, "url_white", None) or getattr(match, "urlWhite", None) or getattr(match, "urlWhitePlayer", None)
        url_black = getattr(match, "url_black", None) or getattr(match, "urlBlack", None)
        if challenge:
            lines.append(f"Challenge: {challenge}")
        if url_white:
            lines.append(f"White join link: {url_white}")
        if url_black:
            lines.append(f"Black join link: {url_black}")

        # friendly metadata
        try:
            speed = getattr(match, "speed", None) or getattr(match, "time_mode", None) or mode
            variant_obj = getattr(match, "variant", None)
            variant_name = variant_obj.name if variant_obj is not None else variant
            rated_flag = getattr(match, "rated", False)
            meta = f"Mode: {speed} | Variant: {variant_name} | Rated: {rated_flag}"
            lines.append(meta)
        except Exception:
            pass

        # If the user probably wanted a different command name (typo detection)
        # We'll check the provided first arg against a small set of known commands and suggest if close.
        known_commands = ["tldr", "playlichess", "chat", "play", "lichess"]
        if args:
            # check the very first raw token (before parsing) for typos
            raw_first = args[0]
            suggestions = _closest_match(raw_first, known_commands, n=2, cutoff=0.6)
            # don't suggest the same command
            suggestions = [s for s in suggestions if s != "playlichess"]
            if suggestions:
                lines.append(f"Tip: did you mean `{suggestions[0]}`?")

        await ctx.send("\n".join(lines))

    return _cmd_playlichess
