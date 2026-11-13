"""
What changed between two timeline versions, in plain words for the version history.
"""

from typing import Optional

from . import typefaces

from .timeline import Timeline


def _mmss(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 60)}:{int(round(t % 60)):02d}" if t >= 10 else f"{t:.1f}s"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _strength(grade: dict) -> float:
    return 1.0 if grade.get("lut_strength") is None else grade["lut_strength"]


def _by_id(items: list[dict]) -> dict[str, dict]:
    return {x["id"]: x for x in items}


def _layer_label(kind: str, item: dict, names: dict[str, str]) -> str:
    if kind == "text":
        return f"{'keyword' if item.get('style') == 'keyword' else 'text'} “{item['text'][:40]}”"
    if kind == "audio" and item.get("sfx"):
        return f"sound effect “{item['sfx']}”"
    name = names.get(item.get("asset_id", ""), "media")
    return f"{'overlay' if kind == 'overlays' else 'audio'} “{name}”"


def describe_changes(old: Optional[dict], new: dict, names: Optional[dict[str, str]] = None) -> list[str]:
    """Human-readable differences from `old` to `new` (timeline JSON). Empty list = identical."""
    names = names or {}
    if old is None:
        t = Timeline.model_validate(new)
        return [f"Started the edit: {_plural(len(t.clips), 'clip')}, {_mmss(t.duration)}"]
    a, b = Timeline.model_validate(old), Timeline.model_validate(new)
    out: list[str] = []

    if (a.output.width, a.output.height) != (b.output.width, b.output.height):
        out.append(f"Format {a.output.width}×{a.output.height} → {b.output.width}×{b.output.height}")
    elif a.output.fit != b.output.fit:
        out.append("Frame now " + ("cropped to fill" if b.output.fit == "cover" else "letterboxed"))

    # clips
    old_clips, new_clips = _by_id([c.model_dump(by_alias=True) for c in a.clips]), \
        _by_id([c.model_dump(by_alias=True) for c in b.clips])
    added = [i for i in new_clips if i not in old_clips]
    removed = [i for i in old_clips if i not in new_clips]
    common = [i for i in new_clips if i in old_clips]
    trimmed = [i for i in common if (old_clips[i]["in"], old_clips[i]["out"]) != (new_clips[i]["in"], new_clips[i]["out"])]
    zoomed = [i for i in common if old_clips[i].get("zoom") != new_clips[i].get("zoom")]
    trans = [i for i in common if old_clips[i].get("transition_in") != new_clips[i].get("transition_in")]
    backs = [i for i in common if old_clips[i].get("background") != new_clips[i].get("background")]
    order_old = [i for i in old_clips if i in new_clips]
    # A split = a kept clip now ends where a new clip from the same source begins, and together
    # they cover the original range.
    splits = [
        i for i in trimmed
        if any(new_clips[n]["asset_id"] == new_clips[i]["asset_id"]
               and abs(new_clips[n]["in"] - new_clips[i]["out"]) < 1e-6
               and abs(new_clips[n]["out"] - old_clips[i]["out"]) < 1e-6
               and abs(new_clips[i]["in"] - old_clips[i]["in"]) < 1e-6 for n in added)
    ]
    if splits:
        out.append(f"Split {_plural(len(splits), 'clip')}")
        trimmed = [i for i in trimmed if i not in splits]
        added = added[len(splits):] if len(added) >= len(splits) else []
    if added and removed and len(added) > len(removed):
        out.append(f"Re-cut into {_plural(len(new_clips), 'clip')} (was {len(old_clips)})")
    else:
        if removed:
            out.append(f"Removed {_plural(len(removed), 'clip')}")
        if added:
            out.append(f"Added {_plural(len(added), 'clip')}")
    if trimmed:
        out.append(f"Trimmed {_plural(len(trimmed), 'clip')}")
    if order_old != common:
        out.append("Reordered clips")
    if zoomed:
        out.append(f"Changed punch-in on {_plural(len(zoomed), 'clip')}")
    if backs:
        kinds = {(new_clips[i].get("background") or {}).get("replace_with") for i in backs}
        what = {"blur": "blurred", "color": "a solid colour", "media": "an image or video"}
        if kinds == {None}:
            out.append(f"Original background restored on {_plural(len(backs), 'clip')}")
        else:
            label = ", ".join(sorted(what.get(k, "original") for k in kinds))
            out.append(f"Background replaced ({label}) on {_plural(len(backs), 'clip')}")
    if trans or (added and any(new_clips[i].get("transition_in") for i in added)):
        kinds = {new_clips[i]["transition_in"]["type"] for i in new_clips if new_clips[i].get("transition_in")}
        out.append("Transitions: " + (", ".join(sorted(kinds)) if kinds else "hard cuts"))
    if abs(a.duration - b.duration) >= 0.05:
        out.append(f"Length {_mmss(a.duration)} → {_mmss(b.duration)}")

    # captions
    ca, cb = a.captions, b.captions
    if ca is None and cb is not None:
        out.append(f"Captions on ({cb.style}, {cb.position})")
    elif ca is not None and cb is None:
        out.append("Captions off")
    elif ca and cb:
        look = [k for k in ("style", "position", "size", "color", "highlight_color", "uppercase", "max_words")
                if getattr(ca, k) != getattr(cb, k)]
        if look:
            out.append(f"Caption look changed ({', '.join(k.replace('_', ' ') for k in look)})")
        old_fix = {(c.asset_id, c.start, c.end, c.text) for c in ca.corrections}
        new_fix = {(c.asset_id, c.start, c.end, c.text) for c in cb.corrections}
        if new_fix != old_fix:
            out.append(f"Edited {_plural(len(new_fix - old_fix) or len(old_fix - new_fix), 'caption line')}")

    if cb is not None:
        if (ca.font if ca else None) != cb.font:
            out.append(f"Caption font: {typefaces.FONTS[cb.font]['label'] if cb.font else 'the style default'}")
        ea, eb = (ca.emphasis if ca else None), cb.emphasis
        if ea != eb:
            if eb is None:
                out.append("Emphasised caption words removed")
            else:
                what = ", ".join(eb.words[:6]) + ("…" if len(eb.words) > 6 else "")
                if eb.numbers:
                    what = (what + ", " if what else "") + "numbers"
                out.append(f"Emphasised words in {typefaces.FONTS[eb.font]['label']}: {what or 'none yet'}")

    # colour
    ga, gb = (a.grade.model_dump(exclude_none=True) if a.grade else None), \
        (b.grade.model_dump(exclude_none=True) if b.grade else None)
    if ga != gb:
        if gb is None:
            out.append("Colour grade removed")
        elif gb.get("lut_preset") and (not ga or ga.get("lut_preset") != gb["lut_preset"]
                                       or _strength(ga) != _strength(gb)):
            from .luts import PRESETS
            pct = round(_strength(gb) * 100)
            out.append(f"Look: {PRESETS[gb['lut_preset']].name}" + (f" at {pct}%" if pct < 100 else ""))
        elif gb.get("lut_asset_id") and (not ga or ga.get("lut_asset_id") != gb["lut_asset_id"]):
            out.append(f"Colour: {names.get(gb['lut_asset_id'], 'LUT').removesuffix('.cube')}")
        elif gb.get("preset") and (not ga or ga.get("preset") != gb.get("preset")):
            out.append(f"Colour preset: {gb['preset']}")
        else:
            out.append("Colour adjusted")

    # layers
    ba, bb = {x.id for x in a.broll}, {x.id for x in b.broll}
    if ba != bb or [x.model_dump() for x in a.broll] != [x.model_dump() for x in b.broll]:
        added_b, removed_b = len(bb - ba), len(ba - bb)
        changed_b = len([i for i in ba & bb if next(x for x in a.broll if x.id == i) != next(x for x in b.broll if x.id == i)])
        parts = [f"added {added_b}" if added_b else "", f"removed {removed_b}" if removed_b else "",
                 f"changed {changed_b}" if changed_b else ""]
        out.append("B-roll: " + ", ".join(p for p in parts if p) + f" ({_plural(len(bb), 'cutaway')} now)")
    sa, sb = {x.id: x for x in a.stickers}, {x.id: x for x in b.stickers}
    if sa != sb:
        name = lambda st: st.sticker.replace("emoji:", "").replace("asset:", "animation ")  # noqa: E731
        added = [name(sb[i]) for i in sb if i not in sa]
        if added:
            out.append(f"Added sticker{'s' if len(added) > 1 else ''}: {', '.join(added)}")
        if [i for i in sa if i not in sb]:
            out.append(f"Removed {_plural(len([i for i in sa if i not in sb]), 'sticker')}")
        if [i for i in sb if i in sa and sa[i] != sb[i]]:
            out.append(f"Changed {_plural(len([i for i in sb if i in sa and sa[i] != sb[i]]), 'sticker')}")
    ea, eb = {x.id: x for x in a.effects}, {x.id: x for x in b.effects}
    if ea != eb:
        label = {"denoise": "video noise removal", "grain": "film grain", "blur": "blur"}
        name = lambda e: label[e.type] + (" (part of the frame)" if e.region else "")  # noqa: E731
        added = [name(eb[i]) for i in eb if i not in ea]
        if added:
            out.append("Added " + ", ".join(added))
        removed = [name(ea[i]) for i in ea if i not in eb]
        if removed:
            out.append("Removed " + ", ".join(removed))
        if [i for i in eb if i in ea and ea[i] != eb[i]]:
            out.append(f"Changed {_plural(len([i for i in eb if i in ea and ea[i] != eb[i]]), 'effect')}")
    za, zb = len(a.zooms), len(b.zooms)
    if [z.model_dump() for z in a.zooms] != [z.model_dump() for z in b.zooms]:
        out.append(f"Emphasis zooms: {za} → {zb}" if za != zb else "Emphasis zooms changed")
    pa = a.audio_polish.model_dump() if a.audio_polish else {}
    pb = b.audio_polish.model_dump() if b.audio_polish else {}
    if pa != pb:
        on = [label for key, label in (("normalize", "loudness for social"), ("voice", "clearer voice"))
              if pb.get(key)]
        if pb.get("denoise", "off") != "off":
            on.append(f"{pb['denoise']} noise reduction")
        out.append("Audio polish: " + (", ".join(on) if on else "off"))
    for kind, items_a, items_b in (("text", a.text, b.text), ("overlays", a.overlays, b.overlays),
                                   ("audio", a.audio, b.audio)):
        la = _by_id([x.model_dump(by_alias=True) for x in items_a])
        lb = _by_id([x.model_dump(by_alias=True) for x in items_b])
        for i in lb:
            if i not in la:
                out.append(f"Added {_layer_label(kind, lb[i], names)}")
            elif la[i] != lb[i]:
                out.append(f"Changed {_layer_label(kind, lb[i], names)}")
        for i in la:
            if i not in lb:
                out.append(f"Removed {_layer_label(kind, la[i], names)}")
    return out
